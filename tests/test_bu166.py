"""BU166 - enable / disable diarization from a session card's menu (offscreen Qt)."""
import os
from datetime import datetime
from unittest.mock import MagicMock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox, QWidget

from src.app import window as window_module
from src.app.session_manager import SessionManager
from src.app.window import MainWindow

T0 = datetime(2026, 10, 1, 10, 0)


@pytest.fixture(scope='module')
def qapp():
    return QApplication.instance() or QApplication([])


def _bind(host, *names):
    for name in names:
        setattr(host, name, getattr(MainWindow, name).__get__(host))


def _host(diarizing=False, error=None):
    host = MagicMock()
    host._ALL_SESSIONS_MENU_QSS = ''
    host._live_session_state.return_value = None
    host.session_manager.db.count_session_documents.return_value = 0
    host.session_manager.is_diarizing.return_value = diarizing
    host.session_manager.diarization_error.return_value = error
    _bind(host, '_add_diarization_action')
    return host


def _session(**extra):
    session = {'id': 1, 'name': 'Lecture', 'status': 'stopped', 'origin': 'recorded',
               'transcription_status': 'transcribed', 'summary_status': 'none',
               'diarization_status': 'none', 'diarization_enabled': False}
    session.update(extra)
    return session


def _actions(host, session, live_state=None):
    with patch.object(window_module, 'QMenu') as menu_cls:
        MainWindow._build_session_actions_menu(host, session, MagicMock(), MagicMock(), live_state)
    menu = menu_cls.return_value
    labels = [c.args[0] for c in menu.addAction.call_args_list]
    return labels, menu


def _diarization_label(labels):
    return next((l for l in labels if 'diarization' in l), None)


@pytest.mark.parametrize('extra,diarizing,label', [
    ({}, False, 'Enable diarization'),
    ({'diarization_status': 'running'}, True, 'Cancel diarization'),
    ({'diarization_status': 'ready'}, False, 'Enable diarization'),
    ({'diarization_status': 'ready', 'diarization_enabled': True}, False, 'Disable diarization'),
    ({'diarization_status': 'failed'}, False, 'Enable diarization (retry)'),
])
def test_label_per_state(extra, diarizing, label):
    labels, _menu = _actions(_host(diarizing), _session(**extra))
    assert _diarization_label(labels) == label
    # Right after the Transcribe / Summarize item.
    assert labels.index(label) == labels.index('Summarize') + 1


def test_failed_run_explains_in_tooltip():
    labels, menu = _actions(_host(error='No speech was found'), _session(diarization_status='failed'))
    action = menu.addAction.return_value
    action.setToolTip.assert_called_with('No speech was found')
    menu.setToolTipsVisible.assert_called_with(True)


def test_disabled_while_live_and_before_transcription():
    host = _host()
    labels, menu = _actions(host, _session(status='active'), live_state='recording')
    assert _diarization_label(labels) == 'Enable diarization (after recording stops)'
    labels, menu = _actions(host, _session(transcription_status='none'))
    assert _diarization_label(labels) == 'Enable diarization (transcribe first)'
    # The only disabled item: Transcribe and Delete stay enabled.
    assert [c.args for c in menu.addAction.return_value.setEnabled.call_args_list].count((False,)) == 1


def test_absent_for_inserted_transcripts():
    labels, _menu = _actions(_host(), _session(origin='text'))
    assert _diarization_label(labels) is None


@pytest.fixture
def manager(tmp_path):
    mgr = SessionManager(base_path=str(tmp_path / 'sessions'), db_path=str(tmp_path / 'chronicle.db'))
    sid = mgr.db.create_session('Lecture', T0, status='stopped', transcription_status='transcribed')
    mgr.db.add_transcript(sid, T0, 'original', 'microphone')
    mgr.db.replace_diarized_transcripts(sid, [
        {'timestamp': T0, 'text': 'diarized', 'source': 'microphone', 'speaker': 1}])
    mgr.db.set_diarization(sid, status='ready')
    with patch('src.app.session_manager.index_session_content'):
        yield mgr, sid
        mgr.close()


def _window_host(mgr):
    host = MagicMock()
    host.session_manager = mgr
    host._current_display_session_id.return_value = None
    host._DIARIZATION_POPUP_QSS = MainWindow._DIARIZATION_POPUP_QSS
    _bind(host, '_diarization_popup', '_ask_diarization', '_disable_diarization', '_enable_diarization',
          '_after_diarization_switch')
    return host


def test_disable_popup_primary_switches_to_original(qapp, manager):
    mgr, sid = manager
    mgr.db.set_diarization(sid, enabled=True)
    assert 'speaker' in mgr.db.get_transcripts(sid)[0]
    seen = {}

    def click_primary(box):
        seen['text'] = box.text()
        seen['primary'] = box.defaultButton().text()
        box.defaultButton().click()

    parent = QWidget()
    with patch.object(QMessageBox, 'exec', click_primary):
        MainWindow._disable_diarization(_window_host(mgr), sid, parent)
    assert seen == {'text': 'Use the original transcript, without speakers?',
                    'primary': 'Use original transcript'}
    assert [r['text'] for r in mgr.db.get_transcripts(sid)] == ['original']


def test_disable_popup_cancel_keeps_speakers(qapp, manager):
    mgr, sid = manager
    mgr.db.set_diarization(sid, enabled=True)
    with patch.object(QMessageBox, 'exec', lambda box: box.button(QMessageBox.Cancel).click()):
        MainWindow._disable_diarization(_window_host(mgr), sid, QWidget())
    assert 'speaker' in mgr.db.get_transcripts(sid)[0]


def test_enabling_ready_session_starts_no_job(qapp, manager):
    mgr, sid = manager
    exec_mock = MagicMock()
    with patch.object(QMessageBox, 'exec', exec_mock):
        MainWindow._enable_diarization(_window_host(mgr), dict(_session(id=sid), diarization_status='ready'),
                                       QWidget())
    exec_mock.assert_not_called()  # no pop-up
    assert not mgr.is_diarizing(sid)
    assert [r['text'] for r in mgr.db.get_transcripts(sid)] == ['diarized']


def test_first_run_asks_then_queues(qapp, manager):
    mgr, sid = manager
    mgr.db.set_diarization(sid, status='none')
    host = _window_host(mgr)
    host._diarization_progress = {}
    texts = []

    def click_primary(box):
        texts.append(box.text())
        box.defaultButton().click()

    with patch.object(QMessageBox, 'exec', click_primary), \
            patch.object(mgr, 'start_diarization') as start, \
            patch.object(window_module.model_manager, 'diarization_installed', return_value=False):
        MainWindow._enable_diarization(host, _session(id=sid), QWidget())
    start.assert_called_once_with(sid)
    assert '10-15 min per hour' in texts[0] and '46 MB' in texts[0]
    assert host._diarization_progress[sid] == (0, 'Queued…')
