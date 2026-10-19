"""Tests for TranscriptionWorker, the single-thread queue that live
transcription now goes through instead of running inline in the audio
capture callback (see src/transcription/worker.py and BU perf rework)."""
import threading
import time

import pytest

from src.audio_capture.chunk import AudioChunk
from src.transcription.worker import BACKLOG_WARNING, TranscriptionWorker


class _FakeEngine:
    """Just enough of ParakeetEngine's interface for the worker's load hooks."""

    def __init__(self):
        self._loaded = False

    def is_loaded(self):
        return self._loaded

    def load(self):
        self._loaded = True


class _FakeTranscriber:
    """Records call order (as (stream, chunk_id) pairs) with a small delay
    per call, so ordering, pending()/wait_idle() and backlog behaviour are
    all observable without real audio or a real model."""

    def __init__(self, delay: float = 0.02, engine=None):
        self.engine = engine or _FakeEngine()
        self.calls = []
        self.forgotten = []
        self._delay = delay
        self._lock = threading.Lock()

    def load_model(self):
        self.engine.load()

    def transcribe_chunk(self, chunk: AudioChunk, stream=None):
        time.sleep(self._delay)
        with self._lock:
            self.calls.append((stream, chunk.chunk_id))
        return {
            'text': f'text-{chunk.chunk_id}',
            'source': chunk.source,
            'chunk_id': chunk.chunk_id,
            'timestamp_start': chunk.timestamp_start,
            'timestamp_end': chunk.timestamp_end,
        }

    def forget_session(self, session_id):
        self.forgotten.append(session_id)


def _chunk(chunk_id: str, source: str = 'mic') -> AudioChunk:
    return AudioChunk(
        source=source, chunk_id=chunk_id,
        timestamp_start='2024-01-01T09:00:00', timestamp_end='2024-01-01T09:00:05',
        file_path='/dev/null',
    )


def test_submit_returns_immediately_and_preserves_per_stream_order():
    """submit() must never block on inference, and chunks of the same
    (session, source) stream must come out in submission order."""
    transcriber = _FakeTranscriber(delay=0.03)
    results = []
    worker = TranscriptionWorker(
        transcriber, on_result=lambda sid, src, chunk, res: results.append(chunk.chunk_id)
    )
    try:
        t0 = time.perf_counter()
        worker.submit(1, 'mic', _chunk('a'))
        worker.submit(1, 'mic', _chunk('b'))
        worker.submit(1, 'system', _chunk('c', source='system'))
        elapsed = time.perf_counter() - t0

        assert elapsed < 0.05, 'submit() blocked on inference'
        assert worker.wait_idle(timeout=3.0)
        assert [c[1] for c in transcriber.calls] == ['a', 'b', 'c']
        assert results == ['a', 'b', 'c']
        # mic chunks share one stream key; the system chunk gets its own.
        assert transcriber.calls[0][0] == (1, 'mic')
        assert transcriber.calls[1][0] == (1, 'mic')
        assert transcriber.calls[2][0] == (1, 'system')
    finally:
        worker.stop()


def test_pending_and_wait_idle_track_the_queue():
    transcriber = _FakeTranscriber(delay=0.05)
    worker = TranscriptionWorker(transcriber, on_result=lambda *a: None)
    try:
        worker.submit(1, 'mic', _chunk('a'))
        worker.submit(1, 'mic', _chunk('b'))
        assert worker.pending(1) >= 1
        assert worker.pending() >= 1
        assert worker.wait_idle(1, timeout=3.0)
        assert worker.pending(1) == 0
        assert worker.pending() == 0
    finally:
        worker.stop()


def test_wait_idle_times_out_without_hanging_forever():
    transcriber = _FakeTranscriber(delay=1.0)
    worker = TranscriptionWorker(transcriber, on_result=lambda *a: None)
    try:
        worker.submit(1, 'mic', _chunk('a'))
        assert worker.wait_idle(1, timeout=0.05) is False
    finally:
        worker.stop(drain_timeout=0.1)


def test_stop_drains_the_queue_before_exiting():
    transcriber = _FakeTranscriber(delay=0.02)
    results = []
    worker = TranscriptionWorker(
        transcriber, on_result=lambda sid, src, chunk, res: results.append(chunk.chunk_id)
    )
    worker.submit(1, 'mic', _chunk('a'))
    worker.submit(1, 'mic', _chunk('b'))

    drained = worker.stop(drain_timeout=3.0)

    assert drained is True
    assert results == ['a', 'b']
    assert not worker.is_running


def test_forget_session_delegates_to_transcriber():
    transcriber = _FakeTranscriber()
    worker = TranscriptionWorker(transcriber, on_result=lambda *a: None)
    try:
        worker.forget_session(7)
        assert transcriber.forgotten == [7]
    finally:
        worker.stop()


def test_a_failing_chunk_does_not_stop_the_stream():
    """One bad chunk (transcribe_chunk raises) must not kill the worker or
    block the chunks queued after it."""
    class _FlakyTranscriber(_FakeTranscriber):
        def transcribe_chunk(self, chunk, stream=None):
            if chunk.chunk_id == 'bad':
                raise RuntimeError('boom')
            return super().transcribe_chunk(chunk, stream=stream)

    transcriber = _FlakyTranscriber(delay=0.01)
    results = []
    worker = TranscriptionWorker(
        transcriber, on_result=lambda sid, src, chunk, res: results.append(chunk.chunk_id)
    )
    try:
        worker.submit(1, 'mic', _chunk('a'))
        worker.submit(1, 'mic', _chunk('bad'))
        worker.submit(1, 'mic', _chunk('b'))
        assert worker.wait_idle(timeout=3.0)
        assert results == ['a', 'b']
        assert worker.is_running
    finally:
        worker.stop()


def test_backlog_warning_fires_once_the_queue_gets_deep():
    transcriber = _FakeTranscriber(delay=0.03)
    statuses = []
    worker = TranscriptionWorker(
        transcriber, on_result=lambda *a: None,
        on_status=lambda msg, is_error: statuses.append((msg, is_error)),
    )
    try:
        for i in range(BACKLOG_WARNING + 2):
            worker.submit(1, 'mic', _chunk(f'c{i}'))
        assert any('behind' in msg for msg, _ in statuses)
        assert worker.wait_idle(timeout=5.0)
    finally:
        worker.stop()


def _wait_for(predicate, timeout=3.0, interval=0.01) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if predicate():
            return True
        time.sleep(interval)
    return False


def test_preload_loads_the_model_without_a_chunk():
    """preload() only queues a _LOAD marker - it isn't reflected in
    pending()/wait_idle(), which track chunks - so completion is polled here
    the same way a real caller would (or just fire-and-forget, as
    SessionManager.start_session does)."""
    transcriber = _FakeTranscriber()
    worker = TranscriptionWorker(transcriber, on_result=lambda *a: None)
    try:
        assert not transcriber.engine.is_loaded()
        worker.preload()
        assert _wait_for(lambda: transcriber.engine.is_loaded())
    finally:
        worker.stop()


def test_on_idle_callback_runs_while_queue_is_empty():
    transcriber = _FakeTranscriber()
    ticks = threading.Event()
    worker = TranscriptionWorker(
        transcriber, on_result=lambda *a: None, on_idle=ticks.set,
        idle_poll_seconds=0.05,
    )
    try:
        worker.start()  # nothing else here calls submit()/preload() to start it
        assert ticks.wait(timeout=2.0), 'on_idle was never called'
    finally:
        worker.stop()
