"""BU170 - diarization smoke: menu -> job -> rows -> view (engine and ASR stubbed)."""
import os
import threading
from datetime import datetime
from unittest import mock

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import numpy as np
import pytest
import soundfile as sf
from PySide6.QtWidgets import QApplication, QMessageBox, QWidget

from src.app.session_manager import SessionManager
from src.app.window import MainWindow, speaker_layout
from src.diarization import job
from src.diarization.engine import ChunkSpan, Turn
from tests.test_bu168 import _bubbles, _host as _view_host

T0 = datetime(2026, 10, 1, 10, 0)


class _Engine:
    def transcribe_array(self, samples, rate, label=''):
        return f'{len(samples) / rate:.0f} seconds of talk'


@pytest.fixture(scope='module')
def qapp():
    return QApplication.instance() or QApplication([])


def test_menu_to_view(qapp, tmp_path):
    finished = threading.Event()
    mgr = SessionManager(base_path=str(tmp_path / 'sessions'), db_path=str(tmp_path / 'chronicle.db'),
                         diarization_finished_callback=lambda *a: finished.set())
    sid = mgr.db.create_session('Lecture', T0, status='stopped', transcription_status='transcribed')
    mgr.db.add_transcript(sid, T0, 'original line', 'microphone')
    mic = mgr._get_session_path(sid) / 'audio' / 'mic'
    mic.mkdir(parents=True)
    name = f'{T0:%Y%m%d_%H%M%S}_{T0:%Y%m%d_%H%M%S}.wav'
    sf.write(str(mic / name), np.full(20 * 16000, 0.1, np.float32), 16000, subtype='PCM_16')
    t = T0.timestamp()
    key = f'mic/{name}'
    turns = [Turn('microphone', 1, t, t + 4, (ChunkSpan(key, 0, 4),)),
             Turn('microphone', 2, t + 4, t + 20, (ChunkSpan(key, 4, 20),))]
    mgr._diarization._run_fn = lambda db, s, path, progress, cancel: job.run_diarization(
        db, s, path, progress, cancel, engine=_Engine())

    host = mock.MagicMock()
    host.session_manager = mgr
    host._ALL_SESSIONS_MENU_QSS = MainWindow._ALL_SESSIONS_MENU_QSS
    host._DIARIZATION_POPUP_QSS = MainWindow._DIARIZATION_POPUP_QSS
    host._live_session_state.return_value = None
    host._diarization_progress = {}
    for name_ in ('_add_diarization_action', '_enable_diarization', '_diarization_popup',
                  '_ask_diarization'):
        setattr(host, name_, getattr(MainWindow, name_).__get__(host))

    session = next(s for s in mgr.db.list_sessions_with_flags() if s['id'] == sid)
    session['summary_status'] = 'none'
    card = QWidget()
    try:
        with mock.patch.object(job.model_manager, 'diarization_installed', return_value=True), \
                mock.patch.object(job, 'diarize_session', return_value=turns), \
                mock.patch.object(job, 'index_session_content'), \
                mock.patch.object(QMessageBox, 'exec', lambda box: box.defaultButton().click()):
            menu = MainWindow._build_session_actions_menu(host, session, card, QWidget(), None)
            [action] = [a for a in menu.actions() if a.text() == 'Enable diarization']
            action.trigger()
            assert finished.wait(5)

        rows = mgr.db.get_transcripts(sid)
        assert [(r['speaker'], r['text']) for r in rows] == [
            (1, '4 seconds of talk'), (2, '16 seconds of talk')]

        view = _view_host(speaker_layout(rows))
        for row in rows:
            MainWindow.add_transcription_to_view(
                view, row['text'], 'mic', '', start_dt=datetime.fromtimestamp(row['timestamp']),
                end_dt=datetime.fromtimestamp(row['end_timestamp']), animate=False, speaker=row['speaker'])
        bubbles = _bubbles(view)
        assert [b.variant for _r, b in bubbles] == ['cream', 'blue']  # Speaker 2 talked most
        assert 'Speaker 2:' in bubbles[1][1].label.text()
    finally:
        mgr.close()
