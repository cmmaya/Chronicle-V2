"""BU165 - diarization job: background run, cancel, failure, enable / disable."""
import threading
from datetime import datetime
from pathlib import Path
from unittest import mock

import numpy as np
import pytest
import soundfile as sf

from src.app.session_manager import SessionManager
from src.diarization import job
from src.diarization.engine import ChunkSpan, DiarizationCancelled, Turn
from src.storage.database import SESSION_ORIGIN_TEXT

SR = 16000
T0 = datetime(2026, 10, 1, 10, 0, 0)


class _Engine:
    def __init__(self):
        self.calls = []

    def transcribe_array(self, samples, rate, label=''):
        self.calls.append((len(samples) / rate, label))
        return f'piece {len(self.calls)}'


def _write_chunk(folder: Path, seconds: float) -> str:
    folder.mkdir(parents=True, exist_ok=True)
    name = f'{T0:%Y%m%d_%H%M%S}_{T0:%Y%m%d_%H%M%S}.wav'
    sf.write(str(folder / name), np.full(int(seconds * SR), 0.1, np.float32), SR, subtype='PCM_16')
    return f'{folder.name}/{name}'


def _turns(key):
    t = T0.timestamp()
    return [Turn('microphone', 1, t, t + 40, (ChunkSpan(key, 0.0, 40.0),)),
            Turn('microphone', 2, t + 40, t + 50, (ChunkSpan(key, 40.0, 50.0),))]


@pytest.fixture
def env(tmp_path):
    events = {'progress': [], 'finished': threading.Event(), 'result': None}

    def on_finished(sid, ok, error):
        events['result'] = (sid, ok, error)
        events['finished'].set()

    mgr = SessionManager(base_path=str(tmp_path / 'sessions'), db_path=str(tmp_path / 'chronicle.db'),
                         diarization_progress_callback=lambda *a: events['progress'].append(a),
                         diarization_finished_callback=on_finished)
    sid = mgr.db.create_session('Lecture', T0, status='stopped', transcription_status='transcribed')
    mgr.db.add_transcript(sid, T0, 'original line', 'microphone')
    key = _write_chunk(mgr._get_session_path(sid) / 'audio' / 'mic', 50.0)
    engine = _Engine()

    def run(db, session_id, path, progress, cancel_event):
        return job.run_diarization(db, session_id, path, progress, cancel_event, engine=engine)

    mgr._diarization._run_fn = run
    with mock.patch.object(job.model_manager, 'diarization_installed', return_value=True), \
            mock.patch.object(job, 'diarize_session', return_value=_turns(key)) as diarize, \
            mock.patch.object(job, 'index_session_content') as job_index, \
            mock.patch('src.app.session_manager.index_session_content') as mgr_index:
        yield mgr, sid, events, engine, diarize, job_index, mgr_index
    mgr.close()


def _wait(events):
    assert events['finished'].wait(5)
    events['finished'].clear()
    return events['result']


def test_success_stores_enables_reindexes(env):
    mgr, sid, events, engine, _diarize, job_index, _ = env
    assert mgr.start_diarization(sid)
    assert _wait(events) == (sid, True, None)

    rows = mgr.db.get_transcripts(sid)
    # A 40 s turn is split into two pieces (<= 30 s each), plus the 10 s turn.
    assert [r['speaker'] for r in rows] == [1, 1, 2]
    assert all(d <= job.MAX_PIECE_SECONDS for d, _ in engine.calls)
    assert rows[0]['audio_file'].startswith('mic/')
    session = mgr.db.get_session(sid)
    assert (session['diarization_status'], session['diarization_enabled']) == ('ready', 1)
    job_index.assert_called_once()

    percents = [p for _, p, _ in events['progress']]
    assert percents == sorted(percents) and percents[-1] == 100
    assert any('Transcribing speakers 2/2' in t for _, _, t in events['progress'])


def test_failure_keeps_original(env):
    mgr, sid, events, _engine, diarize, _, _ = env
    diarize.side_effect = RuntimeError('boom')
    mgr.start_diarization(sid)
    assert _wait(events) == (sid, False, 'boom')
    assert mgr.db.get_session(sid)['diarization_status'] == 'failed'
    assert mgr.diarization_error(sid) == 'boom'
    assert [r['text'] for r in mgr.db.get_transcripts(sid)] == ['original line']
    assert 'speaker' not in mgr.db.get_transcripts(sid)[0]


def test_cancel_restores_prior_status(env):
    mgr, sid, events, _engine, diarize, _, _ = env
    mgr.db.set_diarization(sid, status='failed')
    started = threading.Event()

    def slow(path, progress, cancel_event):
        started.set()
        assert cancel_event.wait(5)
        raise DiarizationCancelled('cancelled')

    diarize.side_effect = slow
    mgr.start_diarization(sid)
    assert started.wait(5)
    assert mgr.db.get_session(sid)['diarization_status'] == 'running'
    assert mgr.is_diarizing(sid)
    assert mgr.cancel_diarization(sid)
    assert _wait(events) == (sid, False, None)
    assert mgr.db.get_session(sid)['diarization_status'] == 'failed'
    assert mgr.db.connection.execute('SELECT COUNT(*) FROM diarized_transcripts').fetchone()[0] == 0
    assert [r['text'] for r in mgr.db.get_transcripts(sid)] == ['original line']


def test_disable_then_enable_is_instant(env):
    mgr, sid, events, _engine, diarize, _, mgr_index = env
    mgr.start_diarization(sid)
    _wait(events)
    mgr.disable_diarization(sid)
    assert 'speaker' not in mgr.db.get_transcripts(sid)[0]
    assert mgr.enable_diarization(sid) is True
    assert 'speaker' in mgr.db.get_transcripts(sid)[0]
    assert diarize.call_count == 1
    assert mgr._jobs.wait(5)
    assert mgr_index.call_count == 2


def test_refusals(env):
    mgr, sid, _events, _engine, _diarize, _, _ = env
    live = mgr.db.create_session('Live', T0, status='active')
    with pytest.raises(ValueError, match='Stop recording'):
        mgr.start_diarization(live)

    text = mgr.db.create_session('Text', T0, status='stopped', transcription_status='transcribed',
                                 origin=SESSION_ORIGIN_TEXT)
    with pytest.raises(ValueError, match='inserted'):
        mgr.start_diarization(text)

    raw = mgr.db.create_session('Raw', T0, status='stopped')
    _write_chunk(mgr._get_session_path(raw) / 'audio' / 'mic', 5.0)
    with pytest.raises(ValueError, match='Transcribe'):
        mgr.start_diarization(raw)


def test_transcription_job_not_blocked(env):
    mgr, sid, events, _engine, diarize, _, _ = env
    release = threading.Event()
    diarize.side_effect = lambda *a: (release.wait(5), [])[1]
    mgr.start_diarization(sid)
    ran = threading.Event()
    mgr.submit_job('other', ran.set)
    try:
        assert ran.wait(2)
    finally:
        release.set()
    _wait(events)


def test_running_at_startup_becomes_failed(tmp_path):
    mgr = SessionManager(base_path=str(tmp_path / 's'), db_path=str(tmp_path / 'c.db'))
    sid = mgr.db.create_session('S', T0, status='stopped')
    mgr.db.set_diarization(sid, status='running')
    mgr.close()
    mgr = SessionManager(base_path=str(tmp_path / 's'), db_path=str(tmp_path / 'c.db'))
    try:
        assert mgr.db.get_session(sid)['diarization_status'] == 'failed'
    finally:
        mgr.close()


def test_resume_drops_the_speaker_copy(env):
    mgr, sid, events, *_ = env
    mgr.start_diarization(sid)
    _wait(events)
    mgr.dual_recorder_factory = mock.MagicMock()
    mgr.dual_recorder_factory.return_value.stop.return_value = ([], [])
    mgr.screenshot_capture_factory = mock.MagicMock()
    mgr._worker.preload = mock.MagicMock()  # no real speech model
    assert mgr.resume_stopped_session(sid) is not None
    assert mgr.db.get_session(sid)['diarization_status'] == 'none'
    assert 'speaker' not in mgr.db.get_transcripts(sid)[0]
    mgr.stop_session(finalize=False)
