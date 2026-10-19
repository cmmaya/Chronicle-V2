"""BU150: the document badge on chat answers and the documents indicator."""
import os
from types import SimpleNamespace
from unittest.mock import MagicMock

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget

from src.app.pixel_widgets import (
    PixelDocumentBadge, PixelDocumentsChip, REMOVED_DOCUMENT_LABEL, document_badge_text,
)
from src.app.window import MainWindow
from src.assistant.document_contract import citation_line


@pytest.fixture(scope='module')
def qapp():
    return QApplication.instance() or QApplication([])


def _host(documents=(), scope='current', selected=1):
    host = MagicMock()
    host._detached_answer_layout = None
    host._answer_container = QWidget()
    host._answer_layout = QVBoxLayout(host._answer_container)
    host._answer_layout.addStretch()
    host._documents_chip = PixelDocumentsChip()
    host.scope_combo.currentData.return_value = scope
    host._selected_session_id = selected
    db = host.session_manager.db
    db.count_session_documents.side_effect = lambda sid: len(documents) if sid == selected else 0
    db.list_session_documents.return_value = list(documents)
    db.get_conversation.return_value = {'id': 5, 'session_id': selected}
    for name in ('_scoped_session_id', '_update_documents_chip', '_stored_document_names',
                 '_add_document_badge_to_conversation'):
        setattr(host, name, getattr(MainWindow, name).__get__(host))
    return host


def _badges(host):
    layout = host._answer_layout
    found = []
    for i in range(layout.count()):
        widget = layout.itemAt(i).widget()
        if widget is not None:
            found.extend(widget.findChildren(PixelDocumentBadge))
    return found


def test_badge_names_the_documents_and_elides_long_lists(qapp):
    assert document_badge_text(['syllabus.pdf']) == 'From: syllabus.pdf'
    names = ['a-very-long-document-name.pdf', 'another-long-document-name.docx']
    text = document_badge_text(names)
    assert len(text) <= 40 and text.endswith('…')
    badge = PixelDocumentBadge(names)
    for name in names:
        assert name in badge.toolTip()


def test_response_with_refs_adds_a_badge_after_the_bubble(qapp):
    host = _host()
    host._add_document_badge_to_conversation(['syllabus.pdf', 'handout.md'])
    [badge] = _badges(host)
    assert badge.names == ['syllabus.pdf', 'handout.md']
    # Inserted before the layout's trailing stretch, i.e. at the end of the chat.
    assert host._answer_layout.count() == 2


def test_response_without_refs_adds_nothing(qapp):
    host = _host()
    host._add_document_badge_to_conversation([])
    assert _badges(host) == [] and host._answer_layout.count() == 1


def _reopen(host, messages):
    host.session_manager.db.get_messages.return_value = messages
    shown = []
    host._add_message_to_conversation = lambda role, text: shown.append((role, text))
    host._add_document_badge_to_conversation = lambda names: shown.append(('badge', list(names)))
    MainWindow._load_conversation(host, 5)
    return shown


def test_reopened_conversation_rebuilds_the_badge_and_hides_the_line(qapp):
    host = _host([{'id': 11, 'name': 'syllabus.pdf'}])
    stored = f'Due Friday.\n\n{citation_line([11])}'
    shown = _reopen(host, [{'role': 'user', 'content': 'when?'},
                           {'role': 'assistant', 'content': stored}])
    assert ('assistant', 'Due Friday.') in shown
    assert ('badge', ['syllabus.pdf']) in shown
    assert not any('Documents used' in text for kind, text in shown if kind != 'badge')


def test_removed_document_renders_as_removed(qapp):
    host = _host([{'id': 11, 'name': 'syllabus.pdf'}])
    stored = f'Answer.\n\n{citation_line([11, 12])}'
    shown = _reopen(host, [{'role': 'assistant', 'content': stored}])
    assert ('badge', ['syllabus.pdf', REMOVED_DOCUMENT_LABEL]) in shown


def test_reopened_message_without_the_line_gets_no_badge(qapp):
    host = _host([{'id': 11, 'name': 'syllabus.pdf'}])
    shown = _reopen(host, [{'role': 'assistant', 'content': 'Plain answer.'}])
    badges = [entry for entry in shown if entry[0] == 'badge']
    assert all(names == [] for _, names in badges)


def test_indicator_shows_for_a_specific_session_with_documents(qapp):
    host = _host([{'id': 1, 'name': 'a.pdf'}])
    host._update_documents_chip()
    assert not host._documents_chip.isHidden()
    assert host._documents_chip.text().endswith('1 document')


def test_indicator_pluralises(qapp):
    host = _host([{'id': 1, 'name': 'a.pdf'}, {'id': 2, 'name': 'b.pdf'}])
    host._update_documents_chip()
    assert host._documents_chip.text().endswith('2 documents')


def test_indicator_hidden_without_documents_or_under_any_session(qapp):
    host = _host([])
    host._update_documents_chip()
    assert host._documents_chip.isHidden() and host._documents_chip.text() == ''

    host = _host([{'id': 1, 'name': 'a.pdf'}], scope='any')
    host._update_documents_chip()
    assert host._documents_chip.isHidden()


def test_indicator_follows_documents_changed(qapp):
    documents = [{'id': 1, 'name': 'a.pdf'}]
    host = _host(documents)
    host._update_documents_chip()
    documents.clear()
    host._update_documents_chip(1)
    assert host._documents_chip.isHidden()
    documents.append({'id': 2, 'name': 'b.pdf'})
    host._update_documents_chip(1)
    assert not host._documents_chip.isHidden()


def test_indicator_opens_the_pop_up_for_the_scoped_session(qapp):
    host = _host([{'id': 1, 'name': 'a.pdf'}])
    host._session_name_for_id.return_value = 'Class'
    host._scoped_session_id = MainWindow._scoped_session_id.__get__(host)
    MainWindow._open_scoped_session_documents(host)
    host._open_session_documents.assert_called_once_with(1, 'Class', host)


def test_scoped_session_falls_back_to_the_active_session(qapp):
    host = _host([], selected=None)
    host.session_manager.get_active_session.return_value = SimpleNamespace(id=9)
    assert host._scoped_session_id() == 9
