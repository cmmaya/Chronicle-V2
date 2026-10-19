"""BU148: the Documents pop-up of a session (offscreen Qt, jobs run inline)."""
import os
from datetime import datetime
from unittest.mock import MagicMock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox, QWidget

from src.app import documents_dialog as dialog_module
from src.app import window as window_module
from src.app.documents_dialog import SessionDocumentsDialog
from src.app.window import MainWindow
from src.config import SESSION_DOCUMENTS
from src.storage.database import Database

SYLLABUS = 'Week 1 covers sampling methods. Week 2 covers the survey critique.'


@pytest.fixture(scope='module')
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def db(tmp_path):
    database = Database(str(tmp_path / 'chronicle.db'))
    database.connect()
    yield database
    database.disconnect()


@pytest.fixture
def session_id(db):
    return db.create_session('Class', datetime(2026, 10, 5, 21, 0))


def _inline(fn, on_done):
    try:
        result, error = fn(), None
    except Exception as e:  # noqa: BLE001
        result, error = None, e
    on_done(result, error)


@pytest.fixture
def dialog(qapp, db, session_id):
    dlg = SessionDocumentsDialog(db, session_id, 'Class', _inline)
    yield dlg
    dlg.deleteLater()


def _file(tmp_path, name, text=SYLLABUS):
    path = tmp_path / name
    path.write_text(text, encoding='utf-8')
    return str(path)


def _menu_host(count):
    host = MagicMock()
    host._ALL_SESSIONS_MENU_QSS = ''
    host._live_session_state.return_value = None
    host.session_manager.db.count_session_documents.return_value = count
    session = {'id': 1, 'name': 'x', 'status': 'stopped', 'origin': 'recorded',
               'transcription_status': 'transcribed', 'summary_status': 'summarized'}
    return host, session


def _menu_labels(host, session, live_state=None):
    with patch.object(window_module, 'QMenu') as menu_cls:
        MainWindow._build_session_actions_menu(host, session, MagicMock(), MagicMock(), live_state)
    return [c.args[0] for c in menu_cls.return_value.addAction.call_args_list]


@pytest.mark.parametrize('status,origin,live', [
    ('stopped', 'recorded', None), ('active', 'recorded', (1, {})),
    ('stopped', 'text', None),
])
def test_menu_has_documents_for_live_finished_and_inserted(status, origin, live):
    host, session = _menu_host(0)
    session.update(status=status, origin=origin)
    host._live_session_state.return_value = live
    labels = _menu_labels(host, session, live and live[1])
    assert 'Documents…' in labels
    assert labels.index('Documents…') > labels.index('View screenshots')


def test_menu_label_shows_the_count():
    host, session = _menu_host(3)
    assert 'Documents (3)…' in _menu_labels(host, session)


def test_empty_state(dialog):
    assert not dialog.empty_label.isHidden()
    assert 'No documents yet' in dialog.empty_label.text()
    assert dialog.import_button.isEnabled()


def test_import_adds_a_row_and_emits(dialog, db, session_id, tmp_path):
    seen = []
    dialog.documents_changed.connect(seen.append)
    dialog.import_paths([_file(tmp_path, 'syllabus.md')])
    [doc] = db.get_session_documents(session_id)
    assert doc['name'] == 'syllabus.md' and doc['text'] == SYLLABUS
    assert seen == [session_id]
    assert [r.name_label.text() for r in dialog._rows] == ['syllabus.md']
    assert dialog.empty_label.isHidden()


def test_row_shows_reading_until_the_job_finishes(qapp, db, session_id, tmp_path):
    jobs = []
    dlg = SessionDocumentsDialog(db, session_id, 'Class', lambda fn, done: jobs.append((fn, done)))
    dlg.import_paths([_file(tmp_path, 'a.txt')])
    assert [r.meta_label.text().startswith('reading') for r in dlg._rows] == [True]
    fn, done = jobs[0]
    done(fn(), None)
    assert 'reading' not in dlg._rows[0].meta_label.text()


def test_refused_file_shows_its_message_and_later_files_still_import(dialog, db, session_id, tmp_path):
    bad = tmp_path / 'slides.pptx'
    bad.write_bytes(b'x' * 100)
    dialog.import_paths([_file(tmp_path, 'good.txt'), str(bad)])
    assert [d['name'] for d in db.get_session_documents(session_id)] == ['good.txt']
    assert 'not supported' in dialog.status_label.text()
    assert not dialog.status_label.isHidden()
    # A refused file in front does not stop the one behind it.
    dialog.import_paths([str(bad), _file(tmp_path, 'second.txt')])
    assert [d['name'] for d in db.get_session_documents(session_id)] == ['good.txt', 'second.txt']


def test_duplicate_name_is_reported(dialog, db, session_id, tmp_path):
    path = _file(tmp_path, 'a.txt')
    dialog.import_paths([path])
    dialog.import_paths([path])
    assert db.count_session_documents(session_id) == 1
    assert 'already has a document named' in dialog.status_label.text()


def test_limit_disables_import_and_drops(dialog, db, session_id, tmp_path):
    limit = SESSION_DOCUMENTS['max_per_session']
    dialog.import_paths([_file(tmp_path, f'd{i}.txt') for i in range(limit)])
    assert db.count_session_documents(session_id) == limit
    assert not dialog.import_button.isEnabled()
    assert 'already has' in dialog.status_label.text()
    assert dialog.drop_error([_file(tmp_path, 'extra.txt')]) == 'Document limit reached'
    dialog.import_paths([_file(tmp_path, 'extra.txt')])
    assert db.count_session_documents(session_id) == limit


def test_drop_rules(dialog, tmp_path):
    assert dialog.drop_error([_file(tmp_path, 'a.txt')]) is None
    assert dialog.drop_error([str(tmp_path)]) == 'Folders cannot be added'
    assert 'Use' in dialog.drop_error([str(tmp_path / 'slides.pptx')])
    assert dialog.drop_error([]) is not None


def test_remove_asks_then_deletes_and_emits(dialog, db, session_id, tmp_path):
    dialog.import_paths([_file(tmp_path, 'a.txt'), _file(tmp_path, 'b.txt')])
    first = db.get_session_documents(session_id)[0]['id']
    seen = []
    dialog.documents_changed.connect(seen.append)

    row = dialog._rows[0]
    row.remove_button.click()  # asks inline first; nothing is deleted yet
    assert not row.confirm_yes.isHidden() and row.remove_button.isHidden()
    row.confirm_no.click()
    assert db.count_session_documents(session_id) == 2 and seen == []
    assert not row.remove_button.isHidden()

    row.remove_button.click()
    row.confirm_yes.click()
    assert [d['name'] for d in db.get_session_documents(session_id)] == ['b.txt']
    assert seen == [session_id]
    assert [r.name_label.text() for r in dialog._rows] == ['b.txt']


def test_preview_shows_the_stored_text_of_the_selected_row(dialog, db, session_id, tmp_path):
    dialog.import_paths([_file(tmp_path, 'a.txt', 'First document text. ' * 4),
                         _file(tmp_path, 'b.txt', SYLLABUS)])
    assert dialog.preview.toPlainText() == SYLLABUS  # the newest import is selected
    first = db.get_session_documents(session_id)[0]['id']
    dialog._select(first)
    assert dialog.preview.toPlainText().startswith('First document text.')


def test_documents_persist_across_dialogs(qapp, db, session_id, tmp_path):
    first = SessionDocumentsDialog(db, session_id, 'Class', _inline)
    first.import_paths([_file(tmp_path, 'a.txt')])
    second = SessionDocumentsDialog(db, session_id, 'Class', _inline)
    assert [r.name_label.text() for r in second._rows] == ['a.txt']


def test_window_opens_the_dialog_and_relays_the_signal(qapp, db, session_id):
    host = MagicMock()
    host.session_manager.db = db
    host._all_sessions_dialog = None
    relayed = []
    host.documents_changed = relayed.append
    parent = QWidget()
    dialog = MainWindow._open_session_documents(host, session_id, 'Class', parent)
    try:
        dialog.documents_changed.emit(session_id)
        assert relayed == [session_id]
    finally:
        dialog.reject()


def test_dropped_image_is_described_and_stored(dialog, db, session_id, tmp_path, monkeypatch):
    from src.screenshots.context_generator import ScreenshotContextGenerator
    monkeypatch.setattr(
        ScreenshotContextGenerator, 'describe_image',
        lambda self, path: {'summary': 'A whiteboard of the onboarding flow.',
                            'visible_text': ['Sign up', 'Verify email']})
    image = tmp_path / 'whiteboard.png'
    image.write_bytes(b'\x89PNG fake')
    assert dialog.drop_error([str(image)]) is None
    dialog.import_paths([str(image)])
    [doc] = db.get_session_documents(session_id)
    assert doc['file_type'] == 'png'
    assert 'onboarding flow' in doc['text'] and 'Verify email' in doc['text']
    assert dialog._rows[0].is_image and 'described' in dialog._rows[0].meta_label.text()
