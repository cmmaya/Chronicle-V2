"""End-to-end test of the perf-reworked session lifecycle, against a fake
audio backend and a fake speech engine (no real devices, no real model, no
network) - a temp DB and temp session folder throughout.

Covers the core promise of the rework: Stop returns immediately and the
session is fully finalized (transcribed, indexed, status updated) shortly
after in the background; closing mid-session is even faster and defers all
of that to the next start.
"""
import time
from datetime import datetime
from pathlib import Path

import pytest

from src.app.session_manager import SessionManager
from src.app.session import Session
from src.audio_capture.chunk import AudioChunk
from src.transcription.parakeet import set_shared_engine

# Genuinely unrelated sentences (no shared words with their neighbours) for
# _FakeEngine.transcribe() to cycle through. LiveTranscriber's real
# cross-chunk dedup runs character-similarity on whatever this returns; an
# earlier version returned "fake transcript for <filename>" for every call,
# which is ~93% character-identical between any two chunks (only a few
# timestamp digits differ) and the dedup correctly treated that as a
# duplicate and discarded it - exactly as it should for real near-identical
# text, but not what these tests want to exercise.
_FAKE_TRANSCRIPTS = [
    "the weather was pleasant this morning",
    "quarterly revenue increased significantly",
    "please review the attached document",
    "server migration completed without issues",
    "client feedback was overwhelmingly positive",
    "the new office opens next month",
]


class _FakeEngine:
    """Stands in for ParakeetEngine: no model, no download, near-instant."""

    def __init__(self, transcribe_delay: float = 0.0):
        self._loaded = False
        self._transcribe_delay = transcribe_delay
        self.transcribe_calls = []

    def is_loaded(self):
        return self._loaded

    def load(self):
        self._loaded = True

    def unload(self):
        self._loaded = False

    def idle_seconds(self):
        return 0.0

    def transcribe(self, audio_path, initial_prompt=None):
        if self._transcribe_delay:
            time.sleep(self._transcribe_delay)
        text = _FAKE_TRANSCRIPTS[len(self.transcribe_calls) % len(_FAKE_TRANSCRIPTS)]
        self.transcribe_calls.append(str(audio_path))
        return text


class _NoOpDualRecorder:
    """Stands in for DualSourceChunkedRecorder: no real audio devices."""

    def __init__(self, session_path, **kwargs):
        self.session_path = session_path
        self.live_transcription_callback = kwargs.get('live_transcription_callback')
        self.is_running = False

    def start(self):
        self.is_running = True

    def stop(self):
        self.is_running = False
        return ([], [])


class _NoOpScreenshotCapture:
    def __init__(self, session_path, db=None):
        self.session_path = session_path
        self.db = db


@pytest.fixture(autouse=True)
def _no_real_network_calls(monkeypatch):
    """Session.STOP_AND_FINALIZE has an auto-summary step that calls the real
    OpenRouter API with the user's key when config.SESSION[
    'auto_summary_after_stop'] is on (the default) - a test session with real
    transcript text and no summary yet triggers it exactly like a real one
    would. None of these tests are about summarization; turn it off so
    finalize_session() never leaves this process during a test run."""
    from src import config
    monkeypatch.setitem(config.SESSION, 'auto_summary_after_stop', False)


@pytest.fixture
def fake_engine():
    engine = _FakeEngine()
    set_shared_engine(engine)
    yield engine
    set_shared_engine(None)


def _make_manager(tmp_path, name='m', **callbacks) -> SessionManager:
    mgr = SessionManager(
        base_path=str(tmp_path / 'sessions'),
        db_path=str(tmp_path / 'chronicle.db'),
        **callbacks,
    )
    mgr.dual_recorder_factory = _NoOpDualRecorder
    mgr.screenshot_capture_factory = _NoOpScreenshotCapture
    return mgr


def _wait_for(predicate, timeout=5.0, interval=0.02) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if predicate():
            return True
        time.sleep(interval)
    return False


def _drop_a_live_chunk(manager: SessionManager, session: Session, chunk_id: str,
                       start: str = '20240101_090000', end: str = '20240101_090005') -> AudioChunk:
    """Write a tiny fake WAV chunk into the session's mic folder and hand it
    to the manager the way DualSourceChunkedRecorder's callback would."""
    audio_dir = Path(session.session_path) / 'audio' / 'mic'
    audio_dir.mkdir(parents=True, exist_ok=True)
    chunk_path = audio_dir / f'{start}_{end}.wav'
    chunk_path.write_bytes(b'')
    chunk = AudioChunk(
        source='mic', chunk_id=chunk_id,
        timestamp_start=datetime.strptime(start, '%Y%m%d_%H%M%S').isoformat(),
        timestamp_end=datetime.strptime(end, '%Y%m%d_%H%M%S').isoformat(),
        file_path=str(chunk_path),
    )
    manager.handle_live_transcription('mic', chunk)
    return chunk


class TestFullLifecycle:
    def test_start_live_chunk_stop_and_background_finalize(self, tmp_path, fake_engine):
        statuses = []
        finalized = []
        live_results = []
        manager = _make_manager(
            tmp_path,
            status_callback=lambda msg, is_error: statuses.append((msg, is_error)),
            live_transcription_ui_callback=live_results.append,
            session_finalized_callback=lambda sid, outcome: finalized.append((sid, outcome)),
        )
        try:
            t0 = time.perf_counter()
            session = manager.start_session('Test Session', auto_record=True,
                                            enable_live_transcription=True)
            assert time.perf_counter() - t0 < 1.0, 'start_session must not block on model load'

            _drop_a_live_chunk(manager, session, 'c1')
            assert _wait_for(lambda: len(live_results) >= 1), 'live result never arrived'
            assert live_results[0]['text'] == _FAKE_TRANSCRIPTS[0]
            assert manager.db.has_transcripts(session.id)

            t0 = time.perf_counter()
            stopped = manager.stop_session(auto_transcribe=True, background=True)
            elapsed = time.perf_counter() - t0
            assert stopped is not None
            assert elapsed < 1.0, 'stop_session(background=True) must return immediately'
            assert stopped.status == Session.STATUS_STOPPED

            # It's flagged for finalization until the background job completes.
            row = manager.db.get_session(session.id)
            assert row['needs_finalize'] == 1

            assert _wait_for(lambda: len(finalized) >= 1, timeout=5.0), \
                'session_finalized_callback never fired'
            finalized_id, outcome = finalized[0]
            assert finalized_id == session.id
            assert outcome.get('error') is None
            assert outcome.get('indexed') is True
            # The chunk was already transcribed live; finalize's batch pass
            # must not have re-transcribed (and re-inserted) it - so the
            # engine's only call stays the one live transcription itself made.
            assert outcome.get('transcribed', 0) == 0
            assert len(fake_engine.transcribe_calls) == 1

            final_row = manager.db.get_session(session.id)
            assert final_row['status'] == 'stopped'
            assert final_row['needs_finalize'] == 0
            assert final_row['transcription_status'] == 'transcribed'
            assert len(manager.db.get_transcripts(session.id)) == 1, \
                'the live transcript must not be duplicated by finalize'
        finally:
            manager.close()

    def test_finalize_transcribes_chunks_live_transcription_missed(self, tmp_path, fake_engine):
        """A chunk that never made it into the live queue (e.g. the worker
        was still starting up) must still get picked up by finalize's batch
        pass, using the shared (fake) engine."""
        manager = _make_manager(tmp_path)
        try:
            session = manager.start_session('S', auto_record=True, enable_live_transcription=True)
            # No live chunk submitted - just drop the audio file on disk,
            # as if live transcription never got to it.
            audio_dir = Path(session.session_path) / 'audio' / 'mic'
            audio_dir.mkdir(parents=True, exist_ok=True)
            (audio_dir / '20240101_090000_20240101_090005.wav').write_bytes(b'')

            manager.stop_session(auto_transcribe=True, background=False)  # blocking, for this test

            assert len(fake_engine.transcribe_calls) == 1
            assert manager.db.has_transcripts(session.id)
            assert manager.db.get_session(session.id)['transcription_status'] == 'transcribed'
        finally:
            manager.close()

    def test_close_mid_session_does_not_finalize_and_returns_promptly(self, tmp_path, fake_engine):
        """Closing while a session is active/paused must stop capture and
        flag the session, without running transcribe/index/summarize -
        that used to load a second copy of the model and could block
        quitting for minutes on a real session."""
        manager = _make_manager(tmp_path)
        session = manager.start_session('S', auto_record=True, enable_live_transcription=True)
        audio_dir = Path(session.session_path) / 'audio' / 'mic'
        audio_dir.mkdir(parents=True, exist_ok=True)
        (audio_dir / '20240101_090000_20240101_090005.wav').write_bytes(b'')

        t0 = time.perf_counter()
        manager.close()
        elapsed = time.perf_counter() - t0

        assert elapsed < 2.0, 'close() must not run finalize synchronously'
        assert len(fake_engine.transcribe_calls) == 0

        # Re-open the same DB: repair must have flagged the session, and
        # finalize_pending_sessions() picks it up in the background.
        manager2 = _make_manager(tmp_path)
        try:
            row = manager2.db.get_session(session.id)
            assert row['status'] == 'stopped'
            assert row['needs_finalize'] == 1

            pending = manager2.finalize_pending_sessions()
            assert pending == [session.id]
            assert _wait_for(
                lambda: manager2.db.get_session(session.id)['needs_finalize'] == 0, timeout=5.0
            )
            assert len(fake_engine.transcribe_calls) == 1
            assert manager2.db.has_transcripts(session.id)
        finally:
            manager2.close()

    def test_pause_resume_preserves_session_id_and_transcripts(self, tmp_path, fake_engine):
        manager = _make_manager(tmp_path)
        try:
            session = manager.start_session('S', auto_record=True, enable_live_transcription=True)
            _drop_a_live_chunk(manager, session, 'c1')
            assert _wait_for(lambda: manager.db.has_transcripts(session.id))

            assert manager.pause_session()
            assert manager.current_session.status == Session.STATUS_PAUSED
            assert manager.resume_session()
            assert manager.current_session.status == Session.STATUS_ACTIVE
            assert manager.current_session.id == session.id

            _drop_a_live_chunk(manager, session, 'c2', '20240101_090010', '20240101_090015')
            assert _wait_for(lambda: len(manager.db.get_transcripts(session.id)) >= 2)
        finally:
            manager.close()

    def test_resume_stopped_session_continues_the_same_session_id(self, tmp_path, fake_engine):
        manager = _make_manager(tmp_path)
        try:
            session = manager.start_session('S', auto_record=True, enable_live_transcription=True)
            manager.stop_session(auto_transcribe=False, background=True)
            assert _wait_for(lambda: manager.current_session is None)

            resumed = manager.resume_stopped_session(session.id)
            assert resumed is not None
            assert resumed.id == session.id
            assert resumed.status == Session.STATUS_ACTIVE
            assert manager.db.get_session(session.id)['status'] == 'active'
        finally:
            manager.close()

    def test_summarize_and_transcribe_session_run_synchronously_when_called_directly(
        self, tmp_path, fake_engine
    ):
        """SessionManager.transcribe_session()/summarize_session() are the
        blocking primitives window.py now wraps in submit_job()."""
        manager = _make_manager(tmp_path)
        try:
            session = manager.start_session('S', auto_record=False, enable_live_transcription=False)
            audio_dir = Path(session.session_path) / 'audio' / 'mic'
            audio_dir.mkdir(parents=True, exist_ok=True)
            (audio_dir / '20240101_090000_20240101_090005.wav').write_bytes(b'')

            result = manager.transcribe_session(session.id)
            assert result['has_transcripts'] is True
            assert result['transcribed'] == 1

            with pytest.raises(ValueError):
                # SummaryGenerator would need a real/mocked HTTP call past
                # this point; the ValueError path (no transcript text) is
                # what's actually exercised without going near the network.
                manager._summarize(999999999)  # nonexistent session -> no transcripts
        finally:
            manager.close()
