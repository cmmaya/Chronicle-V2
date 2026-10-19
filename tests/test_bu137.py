"""BU137: Upload Transcript button in the All Sessions window."""
import os
from unittest.mock import MagicMock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from PySide6.QtWidgets import QApplication, QDialog, QPushButton

from src.app import window as window_module
from src.app.window import MainWindow


@pytest.fixture(scope='module')
def qapp():
    return QApplication.instance() or QApplication([])


def _host():
    host = MagicMock()
    host._post_to_ui = lambda fn: fn()
    host._upload_transcript_dir = None
    host._summarize_uploaded_session = (
        lambda session, dialog: MainWindow._summarize_uploaded_session(host, session, dialog))
    host._refresh_all_sessions_window = (
        lambda dialog: MainWindow._refresh_all_sessions_window(host, dialog))
    return host


def _run_upload(host, dialog, import_result):
    """Click through the upload; run the import job's callback with
    ``import_result`` = (session, error). Returns the submitted job labels."""
    jobs = []

    def submit_job(label, fn, on_done=None):
        jobs.append(label)
        if label.startswith('import'):
            on_done(*import_result)

    host.session_manager.submit_job.side_effect = submit_job
    with patch.object(window_module.QFileDialog, 'getOpenFileName',
                      return_value=('C:/tmp/meeting.txt', '')), \
            patch.object(window_module, 'QMessageBox') as box:
        MainWindow._upload_transcript_from_all_sessions(host, dialog)
    return jobs, box


def test_button_sits_between_upload_audio_and_close(qapp):
    host = MagicMock()
    host._ALL_SESSIONS_DIALOG_QSS = ''
    host._all_sessions_window_size = None
    host._ALL_SESSIONS_FILTERS = MainWindow._ALL_SESSIONS_FILTERS
    captured = []

    class FakeDialog(QDialog):
        def __init__(self, parent=None):
            super().__init__()

        def exec(self):
            captured.append(self)
            return 0

    with patch.object(window_module, 'QDialog', FakeDialog):
        MainWindow._show_all_sessions_window(host)

    names = [b.objectName() for b in captured[0].findChildren(QPushButton)]
    audio = names.index('AllSessionsUpload')
    assert names[audio + 1] == 'AllSessionsUploadTranscript'
    assert names[audio + 2] == 'AllSessionsClose'


def test_successful_import_chains_one_summarize_job():
    host = _host()
    dialog = MagicMock()
    session = MagicMock(id=7)
    session.name = 'meeting'

    jobs, box = _run_upload(host, dialog, (session, None))

    assert jobs == ['import meeting.txt', 'summarize uploaded session 7']
    dialog._focus_session.assert_called_once_with(7)
    box.warning.assert_not_called()


def test_import_error_warns_and_chains_nothing():
    host = _host()
    dialog = MagicMock()

    jobs, box = _run_upload(host, dialog, (None, ValueError('The file is empty')))

    assert jobs == ['import meeting.txt']
    box.warning.assert_called_once()
    assert 'The file is empty' in box.warning.call_args.args[2]


def test_menu_hides_resume_for_inserted_session(qapp):
    host = MagicMock()
    host._ALL_SESSIONS_MENU_QSS = ''
    host._live_session_state.return_value = None
    card = MagicMock()

    def actions(origin):
        session = {'id': 1, 'name': 'x', 'status': 'stopped', 'origin': origin,
                   'transcription_status': 'transcribed', 'summary_status': 'none'}
        with patch.object(window_module, 'QMenu') as menu_cls:
            MainWindow._build_session_actions_menu(host, session, card, MagicMock(), None)
        return [c.args[0] for c in menu_cls.return_value.addAction.call_args_list]

    assert 'Resume' in actions('recorded')
    assert 'Resume' not in actions('text')
    assert 'Transcribe' not in actions('text')


def test_card_meta_for_inserted_transcript():
    host = MagicMock()
    meta = MainWindow._session_card_meta(
        host, {'start_time': 1700000000, 'end_time': 1700000000, 'origin': 'text'}, None)
    assert meta.endswith('Inserted transcript')
    assert 'min' not in meta


def _open_dialog(sessions, live=None):
    """Build the real All Sessions dialog without running its event loop."""
    host = MagicMock()
    host._ALL_SESSIONS_DIALOG_QSS = ''
    host._ALL_SESSIONS_MENU_QSS = ''
    host._all_sessions_window_size = None
    host._ALL_SESSIONS_FILTERS = MainWindow._ALL_SESSIONS_FILTERS
    host._live_session_state.return_value = live
    host._load_sessions_for_browser.return_value = sessions
    host._session_card_meta.return_value = ''
    host._session_day_label.return_value = 'Today'
    host._build_session_actions_menu.return_value = None
    captured = []

    class FakeDialog(QDialog):
        def __init__(self, parent=None):
            super().__init__()

        def exec(self):
            captured.append(self)
            return 0

    with patch.object(window_module, 'QDialog', FakeDialog):
        MainWindow._show_all_sessions_window(host)
    return captured[0]


def _session(sid, summary_status='none'):
    return {'id': sid, 'name': f's{sid}', 'status': 'stopped', 'origin': 'text',
            'transcription_status': 'transcribed', 'summary_status': summary_status,
            'start_time': 1700000000, 'end_time': 1700000000}


def test_progress_bar_runs_for_the_whole_upload(qapp):
    dialog = _open_dialog([])
    bar = dialog.findChild(window_module.QProgressBar, 'AllSessionsProgress')
    assert bar.isHidden()

    dialog._busy_begin('Importing meeting.txt…', 40)
    assert not bar.isHidden()
    dialog._busy_update("Summarizing 'meeting'…", 92)
    dialog._busy_end()

    assert bar.value() == 100
    # Hides itself shortly after completing.
    from PySide6.QtCore import QEventLoop, QTimer
    loop = QEventLoop()
    QTimer.singleShot(600, loop.quit)
    loop.exec()
    assert bar.isHidden()


def test_busy_chip_survives_a_list_reload_and_clears_when_done(qapp):
    sessions = [_session(7)]
    dialog = _open_dialog(sessions)

    dialog._mark_busy(7, 'summary_chip', 'Summarizing')
    dialog._reload_sessions()
    card = dialog._focus_session(7)
    assert 'Summarizing' in card.summary_chip.text()

    # The job finishes: the stored state is "summarized" and the reload shows it.
    dialog._clear_busy(7)
    sessions[0]['summary_status'] = 'summarized'
    dialog._reload_sessions()
    card = dialog._focus_session(7)
    assert 'Summarizing' not in card.summary_chip.text()
    assert card.summary_chip.toolTip() == 'Summary ready'


def test_summary_completion_ends_progress_and_reloads_the_list():
    host = _host()
    dialog = MagicMock()
    session = MagicMock(id=7)
    session.name = 'meeting'

    submitted = []

    def submit_job(label, fn, on_done=None):
        submitted.append(label)
        on_done(session if label.startswith('import') else None, None)

    host.session_manager.submit_job.side_effect = submit_job
    with patch.object(window_module.QFileDialog, 'getOpenFileName',
                      return_value=('C:/tmp/meeting.txt', '')),             patch.object(window_module, 'QMessageBox'):
        MainWindow._upload_transcript_from_all_sessions(host, dialog)

    assert submitted == ['import meeting.txt', 'summarize uploaded session 7']
    dialog._busy_begin.assert_called_once()
    dialog._mark_busy.assert_called_once_with(7, 'summary_chip', 'Summarizing')
    dialog._busy_end.assert_called_once()
    dialog._clear_busy.assert_called_once_with(7)
    dialog._reload_sessions.assert_called()


def test_import_error_ends_progress():
    host = _host()
    dialog = MagicMock()

    _run_upload(host, dialog, (None, ValueError('The file is empty')))

    dialog._busy_begin.assert_called_once()
    dialog._busy_end.assert_called_once()
