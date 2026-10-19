"""A session's diarization from start to finish, in the background (BU165).

:func:`run_diarization` downloads the models if they are missing, finds the
speakers (:func:`~.engine.diarize_session`), transcribes each speaker turn
with Parakeet (pieces of at most :data:`MAX_PIECE_SECONDS`), stores the rows
next to the original transcript, switches the session over and re-indexes it.

:class:`DiarizationRunner` runs it on its own daemon thread, one session at a
time, so a 30-minute run never holds up the shared job thread (transcribe,
summarize, imports). Nothing is written until the very end: a cancel or a
failure leaves the original transcript in place.
"""
from __future__ import annotations

import logging
import queue
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np

from .. import model_manager
from ..rag.indexer import index_session_content
from .engine import (SAMPLE_RATE, DiarizationCancelled, DiarizationError, Turn, _chunk_start,
                     _resample, diarize_session)

logger = logging.getLogger(__name__)

MAX_PIECE_SECONDS = 30.0
QUIET_SEARCH_SECONDS = 5.0  # a piece is cut at the quietest point of its last seconds
QUIET_FRAME_SECONDS = 0.05

# Overall progress per stage, in percent: (start, end).
DOWNLOAD_RANGE = (0, 5)
SPEAKERS_RANGE = (5, 75)
TRANSCRIBE_RANGE = (75, 97)
SAVE_RANGE = (97, 100)

Progress = Callable[[int, str], None]  # (percent, stage text)


def _scale(span: Tuple[int, int], done: float, total: float) -> int:
    lo, hi = span
    return int(lo + (hi - lo) * min(max(done / total, 0.0), 1.0)) if total else lo


# -- audio of a turn ---------------------------------------------------------------

def _read_span(audio_dir: Path, audio_file: str, start: float, end: float) -> np.ndarray:
    import soundfile as sf

    path = audio_dir / audio_file
    rate = sf.info(str(path)).samplerate
    data, rate = sf.read(str(path), dtype="float32", always_2d=True,
                         start=int(start * rate), stop=int(end * rate))
    samples = data.mean(axis=1)
    return _resample(samples, rate) if rate != SAMPLE_RATE else samples


def turn_pieces(audio_dir: Path, turn: Turn) -> List[Tuple[np.ndarray, float, float, str]]:
    """The turn's audio as (samples, start_abs, end_abs, audio_file) pieces of
    at most :data:`MAX_PIECE_SECONDS`, each cut at a quiet moment."""
    parts, marks, offset = [], [], 0  # marks: (sample offset, absolute time, audio_file)
    for span in turn.spans:
        samples = _read_span(audio_dir, span.audio_file, span.start, span.end)
        if not len(samples):
            continue
        marks.append((offset, _chunk_start(Path(span.audio_file)).timestamp() + span.start, span.audio_file))
        parts.append(samples)
        offset += len(samples)
    if not parts:
        return []
    audio = np.concatenate(parts)
    starts = [m[0] for m in marks]

    def where(i: int) -> Tuple[float, str]:
        k = max(np.searchsorted(starts, i, side="right") - 1, 0)
        return marks[k][1] + (i - marks[k][0]) / SAMPLE_RATE, marks[k][2]

    pieces, lo = [], 0
    longest = int(MAX_PIECE_SECONDS * SAMPLE_RATE)
    frame = int(QUIET_FRAME_SECONDS * SAMPLE_RATE)
    while lo < len(audio):
        hi = min(lo + longest, len(audio))
        if hi < len(audio):
            window = audio[hi - int(QUIET_SEARCH_SECONDS * SAMPLE_RATE):hi]
            energy = np.square(window[:len(window) // frame * frame]).reshape(-1, frame).mean(axis=1)
            hi = hi - len(window) + (int(np.argmin(energy)) + 1) * frame
        start_abs, audio_file = where(lo)
        end_abs = where(hi - 1)[0] + 1 / SAMPLE_RATE
        pieces.append((audio[lo:hi], start_abs, end_abs, audio_file))
        lo = hi
    return pieces


def transcribe_turns(audio_dir: Path, turns: List[Turn], engine, progress: Progress,
                     cancel_event: threading.Event) -> List[Dict[str, Any]]:
    """Speaker-labelled transcript rows, one per piece with text."""
    rows = []
    for n, turn in enumerate(turns):
        if cancel_event.is_set():
            raise DiarizationCancelled("Diarization cancelled.")
        progress(_scale(TRANSCRIBE_RANGE, n, len(turns)), f"Transcribing speakers {n + 1}/{len(turns)}…")
        for samples, start_abs, end_abs, audio_file in turn_pieces(audio_dir, turn):
            if cancel_event.is_set():
                raise DiarizationCancelled("Diarization cancelled.")
            text = (engine.transcribe_array(samples, SAMPLE_RATE, label=audio_file) or "").strip()
            if text:
                rows.append({"timestamp": start_abs, "end_timestamp": end_abs, "text": text,
                             "source": turn.source, "speaker": turn.speaker, "audio_file": audio_file})
    return rows


# -- the whole run -----------------------------------------------------------------

def run_diarization(db, session_id: int, session_path, progress: Progress,
                    cancel_event: threading.Event, engine=None) -> int:
    """Diarize one session, store the rows and switch it over. Blocking.

    Returns the number of rows stored.

    Raises:
        DiarizationCancelled / model_manager.DownloadCancelled: cancelled;
            nothing was written.
        DiarizationError, model_manager.ModelManagerError, ...: failed;
            nothing was written.
    """
    if not model_manager.diarization_installed():
        totals = {m.name: m.total_bytes for m in model_manager.DIARIZATION_MODELS}
        done: Dict[str, int] = {}

        def on_download(name: str, got: int, _total: int) -> None:
            done[name] = got
            progress(_scale(DOWNLOAD_RANGE, sum(done.values()), sum(totals.values())),
                     "Downloading models…")

        progress(DOWNLOAD_RANGE[0], "Downloading models…")
        model_manager.download_diarization(on_download, cancel_event)

    def on_speakers(stage: str, got: int, total: int) -> None:
        if stage == "clustering":
            got, total = 1, 1
        progress(_scale(SPEAKERS_RANGE, got, total), "Finding speakers…")

    progress(SPEAKERS_RANGE[0], "Finding speakers…")
    turns = diarize_session(session_path, on_speakers, cancel_event)
    if not turns:
        raise DiarizationError("No speech was found in this session's audio.")

    if engine is None:
        from ..transcription.parakeet import get_shared_engine
        engine = get_shared_engine()
    rows = transcribe_turns(Path(session_path) / "audio", turns, engine, progress, cancel_event)
    if not rows:
        raise DiarizationError("No speech could be transcribed in this session's audio.")

    if cancel_event.is_set():
        raise DiarizationCancelled("Diarization cancelled.")
    if not db.get_session(session_id):
        raise DiarizationError("The session was deleted.")
    progress(SAVE_RANGE[0], "Saving…")
    db.replace_diarized_transcripts(session_id, rows)
    db.set_diarization(session_id, enabled=True, status="ready")
    try:
        index_session_content(db, session_id)
    except Exception:  # noqa: BLE001 - the transcript is stored; search catches up later
        logger.exception("Re-indexing diarized session %s failed", session_id)
    progress(SAVE_RANGE[1], "Done")
    return len(rows)


_CANCELLED = (DiarizationCancelled, model_manager.DownloadCancelled)


class DiarizationRunner:
    """One daemon thread diarizing one session at a time; later requests queue.

    A session is marked 'running' as soon as it is queued (and stays so if
    the app dies, for the startup repair to find). ``on_progress(session_id,
    percent, text)`` and ``on_finished(session_id, ok, error)`` are called on
    the runner's thread; ``error`` is None when the run was cancelled.
    """

    def __init__(self, db, session_path: Callable[[int], Path],
                 on_progress: Optional[Callable[[int, int, str], None]] = None,
                 on_finished: Optional[Callable[[int, bool, Optional[str]], None]] = None,
                 run: Callable[..., Any] = run_diarization):
        self._db = db
        self._session_path = session_path
        self._on_progress = on_progress
        self._on_finished = on_finished
        self._run_fn = run
        self._queue: "queue.Queue[int]" = queue.Queue()
        self._lock = threading.Lock()
        self._jobs: Dict[int, Tuple[threading.Event, str]] = {}  # session -> (cancel, prior status)
        self._errors: Dict[int, str] = {}
        self._thread: Optional[threading.Thread] = None
        self._idle = threading.Condition(self._lock)

    def submit(self, session_id: int) -> bool:
        """Queue a run; False when the session is already queued or running."""
        with self._lock:
            if session_id in self._jobs:
                return False
            session = self._db.get_session(session_id) or {}
            self._jobs[session_id] = (threading.Event(), session.get("diarization_status") or "none")
            self._errors.pop(session_id, None)
            self._db.set_diarization(session_id, status="running")
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._loop, name="diarization", daemon=True)
                self._thread.start()
        self._queue.put(session_id)
        return True

    def cancel(self, session_id: int) -> bool:
        with self._lock:
            job = self._jobs.get(session_id)
        if job:
            job[0].set()
        return job is not None

    def cancel_all(self) -> None:
        with self._lock:
            for event, _prior in self._jobs.values():
                event.set()

    def is_active(self, session_id: int) -> bool:
        with self._lock:
            return session_id in self._jobs

    def last_error(self, session_id: int) -> Optional[str]:
        return self._errors.get(session_id)

    def wait(self, timeout: Optional[float] = None) -> bool:
        """Block until nothing is queued or running. False on timeout."""
        with self._idle:
            return self._idle.wait_for(lambda: not self._jobs, timeout=timeout)

    def _loop(self) -> None:
        while True:
            session_id = self._queue.get()
            with self._lock:
                cancel_event, prior = self._jobs[session_id]
            ok, error, last = False, None, [-1, ""]

            def progress(percent: int, text: str) -> None:
                if [percent, text] == last or self._on_progress is None:
                    return
                last[:] = [percent, text]
                try:
                    self._on_progress(session_id, percent, text)
                except Exception:  # noqa: BLE001
                    logger.exception("Diarization progress callback failed")

            try:
                if cancel_event.is_set():
                    raise DiarizationCancelled("Diarization cancelled.")
                self._run_fn(self._db, session_id, self._session_path(session_id), progress, cancel_event)
                ok = True
            except _CANCELLED:
                self._restore(session_id, prior)
            except Exception as exc:  # noqa: BLE001 - reported to the UI
                logger.exception("Diarization of session %s failed", session_id)
                error = str(exc) or type(exc).__name__
                self._errors[session_id] = error
                self._restore(session_id, "failed")
            finally:
                with self._lock:
                    self._jobs.pop(session_id, None)
                    self._idle.notify_all()
            if self._on_finished is not None:
                try:
                    self._on_finished(session_id, ok, error)
                except Exception:  # noqa: BLE001
                    logger.exception("Diarization finished callback failed")
            self._db.release_thread_connection()

    def _restore(self, session_id: int, status: str) -> None:
        try:
            if self._db.get_session(session_id):
                self._db.set_diarization(session_id, status=status)
        except Exception:  # noqa: BLE001
            logger.exception("Could not reset the diarization status of session %s", session_id)
