"""Background worker that owns live transcription.

Audio capture used to run speech-to-text inline: inside the microphone's
PortAudio callback and inside the system-audio loop. A ~0.5-1 s inference (or
the ~5-7 s model load on the first chunk) blocked the callback, overflowed the
input buffer and dropped microphone audio, and made stopping a session wait
for transcriptions. Now recorders only call :meth:`TranscriptionWorker.submit`,
which queues the chunk and returns at once; this single thread transcribes
chunks in order with the shared model.
"""
import logging
import queue
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

from ..audio_capture.chunk import AudioChunk
from .live import LiveTranscriber

logger = logging.getLogger(__name__)

ResultCallback = Callable[[int, str, AudioChunk, Dict[str, Any]], None]

# Warn once the backlog is this deep (~4 s of audio per chunk).
BACKLOG_WARNING = 12


@dataclass
class _Job:
    session_id: int
    source: str
    chunk: AudioChunk


_LOAD = object()   # queue marker: load the model now
_STOP = object()   # queue marker: exit the thread


class TranscriptionWorker:
    """A single thread transcribing queued chunks one at a time.

    Args:
        transcriber: Does the actual transcription (and per-stream dedup).
        on_result: Called on the worker thread as
            ``(session_id, source, chunk, result)`` for every chunk with text.
        on_status: Optional ``(message, is_error)`` callback.
        on_idle: Optional callback run on the worker thread every
            ``idle_poll_seconds`` while the queue is empty (used to unload
            models that haven't been needed for a while).
    """

    def __init__(
        self,
        transcriber: LiveTranscriber,
        on_result: ResultCallback,
        on_status: Optional[Callable[[str, bool], None]] = None,
        on_idle: Optional[Callable[[], None]] = None,
        idle_poll_seconds: float = 15.0,
    ):
        self._transcriber = transcriber
        self._on_result = on_result
        self._on_status = on_status
        self._on_idle = on_idle
        self._idle_poll_seconds = idle_poll_seconds
        self._queue: "queue.Queue[Any]" = queue.Queue()
        self._pending: Dict[int, int] = {}
        self._pending_cond = threading.Condition()
        self._thread: Optional[threading.Thread] = None
        self._warned_backlog = False

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._run, name="live-transcription", daemon=True
        )
        self._thread.start()

    def stop(self, drain_timeout: Optional[float] = 10.0) -> bool:
        """Finish the queued chunks (up to ``drain_timeout``) and exit.

        Returns True if everything queued was transcribed. Chunks left over
        still have their audio on disk; the next finalization pass picks
        them up.
        """
        drained = self.wait_idle(timeout=drain_timeout)
        self._queue.put(_STOP)
        if self._thread is not None:
            self._thread.join(timeout=5.0)
        return drained

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # -- producer side (any thread) ------------------------------------------

    def preload(self) -> None:
        """Load the model on the worker thread ahead of the first chunk."""
        self.start()
        self._queue.put(_LOAD)

    def submit(self, session_id: int, source: str, chunk: AudioChunk) -> None:
        """Queue a chunk for transcription. Never blocks on inference."""
        self.start()
        with self._pending_cond:
            self._pending[session_id] = self._pending.get(session_id, 0) + 1
            backlog = sum(self._pending.values())
        self._queue.put(_Job(session_id, source, chunk))
        if backlog >= BACKLOG_WARNING and not self._warned_backlog:
            self._warned_backlog = True
            self._status(
                f"Live transcription is {backlog} chunks behind - it will catch up", False
            )

    def pending(self, session_id: Optional[int] = None) -> int:
        with self._pending_cond:
            if session_id is None:
                return sum(self._pending.values())
            return self._pending.get(session_id, 0)

    def wait_idle(self, session_id: Optional[int] = None,
                  timeout: Optional[float] = None) -> bool:
        """Block until no chunk (of ``session_id``, or at all) is queued or
        being transcribed. Returns False on timeout."""
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._pending_cond:
            while self.pending(session_id) > 0:
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    return False
                self._pending_cond.wait(remaining if remaining is not None else 1.0)
        return True

    def forget_session(self, session_id: int) -> None:
        """Drop per-stream dedup state of a finished session."""
        self._transcriber.forget_session(session_id)

    # -- worker thread -----------------------------------------------------------

    def _status(self, message: str, is_error: bool) -> None:
        (logger.error if is_error else logger.info)(message)
        if self._on_status:
            try:
                self._on_status(message, is_error)
            except Exception as exc:  # noqa: BLE001 - never kill the worker
                logger.warning(f"status callback raised: {exc}")

    def _load_model(self) -> None:
        if self._transcriber.engine.is_loaded():
            return
        self._status("Loading speech model…", False)
        started = time.perf_counter()
        try:
            self._transcriber.load_model()
        except Exception as exc:  # noqa: BLE001
            self._status(f"Speech model failed to load: {exc}", True)
            return
        self._status(f"Speech model ready ({time.perf_counter() - started:.0f}s)", False)

    def _run(self) -> None:
        while True:
            try:
                item = self._queue.get(timeout=self._idle_poll_seconds)
            except queue.Empty:
                if self._on_idle:
                    try:
                        self._on_idle()
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(f"idle callback raised: {exc}")
                continue

            if item is _STOP:
                break
            if item is _LOAD:
                self._load_model()
                continue

            job: _Job = item
            try:
                if not self._transcriber.engine.is_loaded():
                    self._load_model()
                result = self._transcriber.transcribe_chunk(
                    job.chunk, stream=(job.session_id, job.source)
                )
                if result:
                    self._on_result(job.session_id, job.source, job.chunk, result)
            except Exception as exc:  # noqa: BLE001 - one bad chunk must not stop the stream
                logger.error(f"Live transcription failed for {job.chunk.chunk_id}: {exc}")
            finally:
                with self._pending_cond:
                    left = self._pending.get(job.session_id, 1) - 1
                    if left > 0:
                        self._pending[job.session_id] = left
                    else:
                        self._pending.pop(job.session_id, None)
                    if not self._pending:
                        self._warned_backlog = False
                    self._pending_cond.notify_all()
        logger.info("Live transcription worker exited")
