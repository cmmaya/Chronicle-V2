"""BU151: the detached window on session documents (replaces the BU117 attachment)."""
import json
import os
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.detached_harness import DetachedHarness, app

from src.app import window as window_module
from src.app.window import DetachedTranscriptsDialog, document_drop_error
from src.assistant.live_qa import answer_instruction
from src.config import LIVE_QA
from src.storage.database import Database

BASELINE = Path(__file__).parent / 'fixtures' / 'prompt_baseline' / 'answer_instructions.json'
SYLLABUS = 'Week 1 covers sampling methods. Week 2 covers the survey critique.'

_app = app()


class _Signal:
    def connect(self, *_args, **_kwargs):
        pass

    def disconnect(self, *_args, **_kwargs):
        pass


class _Combo:
    @staticmethod
    def currentData():
        return 'research_helper'


class _Harness(DetachedHarness):
    def __init__(self, db):
        super().__init__()
        self.session_manager = SimpleNamespace(db=db)
        self.assistant_service = object()
        self.agent_combo = _Combo()
        self.opened = []

    def _run_document_job(self, fn, on_done):
        try:
            result, error = fn(), None
        except Exception as e:  # noqa: BLE001
            result, error = None, e
        on_done(result, error)

    def _open_session_documents(self, session_id, session_name, parent=None):
        self.opened.append((session_id, session_name, parent))

    def _session_name_for_id(self, session_id):
        return 'Class'


@pytest.fixture
def db(tmp_path):
    database = Database(str(tmp_path / 'chronicle.db'))
    database.connect()
    yield database
    database.disconnect()


@pytest.fixture
def session_id(db):
    return db.create_session('Class', datetime(2026, 10, 5, 21, 0))


@pytest.fixture
def harness(db, session_id):
    h = _Harness(db)
    h._selected_session_id = session_id
    h.build_answers_column()
    h.add('an edge case', 'system', 0)
    return h


def _file(tmp_path, name, text=SYLLABUS):
    path = tmp_path / name
    path.write_text(text, encoding='utf-8')
    return str(path)


# --- drops -------------------------------------------------------------------

def test_drop_adds_to_the_displayed_session(harness, db, session_id, tmp_path):
    seen = []
    harness.documents_changed.connect(seen.append)
    harness._add_documents_to_displayed_session(
        [_file(tmp_path, 'syllabus.md'), _file(tmp_path, 'notes.txt')])
    assert [d['name'] for d in db.get_session_documents(session_id)] == ['syllabus.md', 'notes.txt']
    assert seen == [session_id, session_id]


def test_refused_types_add_nothing_and_say_so(harness, db, session_id, tmp_path):
    bad = tmp_path / 'slides.pptx'
    bad.write_bytes(b'x' * 100)
    harness._add_documents_to_displayed_session([str(bad), _file(tmp_path, 'good.txt')])
    assert [d['name'] for d in db.get_session_documents(session_id)] == ['good.txt']
    assert any(is_error and 'not supported' in message for message, is_error in harness.status_messages)


def test_limit_and_duplicates_come_back_through_the_status_bar(harness, db, session_id, tmp_path):
    path = _file(tmp_path, 'a.txt')
    harness._add_documents_to_displayed_session([path, path])
    assert db.count_session_documents(session_id) == 1
    assert any(is_error and 'already has a document named' in message
               for message, is_error in harness.status_messages)


def test_drop_with_no_session_displayed_is_refused(harness, db, tmp_path):
    harness._selected_session_id = None
    assert 'No session is displayed' in harness._detached_drop_gate()
    harness._add_documents_to_displayed_session([_file(tmp_path, 'a.txt')])
    assert harness.status_messages[-1][1] is True
    assert 'No session is displayed' in harness.status_messages[-1][0]


def test_drop_rules(tmp_path):
    assert document_drop_error([_file(tmp_path, 'a.txt')]) is None
    assert document_drop_error([_file(tmp_path, 'b.md'), _file(tmp_path, 'c.docx')]) is None
    assert document_drop_error([str(tmp_path / 'a.pptx')]) is not None
    assert 'Folders' in document_drop_error([str(tmp_path)])
    assert document_drop_error([]) is not None


def test_the_window_refuses_when_the_gate_says_so():
    dialog = DetachedTranscriptsDialog()
    assert dialog._drop_error([__file__]) is not None  # .py is not a document
    dialog.drop_gate = lambda: 'No session is displayed'
    assert dialog._drop_error([__file__]) == 'No session is displayed'
    dialog.drop_gate = None


# --- header chip -------------------------------------------------------------

def test_chip_hidden_without_documents_and_counts_otherwise(harness, db, session_id, tmp_path):
    chip = harness._detached_documents_chip
    harness._update_detached_documents_chip()
    assert chip.isHidden() and chip.text() == ''

    harness._add_documents_to_displayed_session([_file(tmp_path, 'a.txt')])
    harness._update_detached_documents_chip()
    assert not chip.isHidden() and chip.text().endswith('1 document')

    harness._add_documents_to_displayed_session([_file(tmp_path, 'b.txt')])
    harness._update_detached_documents_chip()
    assert chip.text().endswith('2 documents')


def test_chip_opens_the_pop_up_for_the_displayed_session(harness, session_id):
    harness._open_detached_documents()
    assert harness.opened[0][:2] == (session_id, 'Class')


# --- the instruction ----------------------------------------------------------

def test_instruction_without_documents_is_the_baseline_for_every_mode():
    golden = json.loads(BASELINE.read_text(encoding='utf-8'))
    assert set(golden) == set(LIVE_QA['answer_instructions'])
    for mode, text in golden.items():
        assert answer_instruction(mode, False) == text
        assert answer_instruction(mode) == text


def test_transcripts_mode_with_documents_uses_the_documents_tier():
    text = answer_instruction('transcripts', True)
    assert text != answer_instruction('transcripts', False)
    assert text.index('From transcripts:') < text.index('From documents:') < text.index('General knowledge:')
    assert 'Documents used:' in text


def test_general_mode_ignores_documents():
    assert answer_instruction('general', True) == answer_instruction('general', False)


# --- the question and the card -------------------------------------------------

@pytest.fixture
def capture(monkeypatch):
    started = []

    class _CapturingThread:
        def __init__(self, **kwargs):
            started.append(kwargs)
            self.finished_signal = _Signal()
            self.error_signal = _Signal()
            self.delta_signal = _Signal()
            self.finished = _Signal()

        def start(self):
            pass

    monkeypatch.setattr(window_module, 'AssistantQueryThread', _CapturingThread)
    return started


def _ask(harness):
    harness._detached_selected_chunks = [0]
    harness._on_answer_selected_chunks()


def test_the_question_carries_no_reference_block(harness, capture, tmp_path):
    harness._add_documents_to_displayed_session([_file(tmp_path, 'a.txt')])
    _ask(harness)
    question = capture[0]['question']
    assert 'Reference document' not in question and SYLLABUS not in question
    assert capture[0]['explicit_scope'] == 'current_session'


def test_the_instruction_follows_the_displayed_sessions_documents(harness, capture, tmp_path):
    _ask(harness)
    assert capture[0]['system_instruction'] == answer_instruction('transcripts')
    harness._add_documents_to_displayed_session([_file(tmp_path, 'a.txt')])
    _ask(harness)
    assert capture[1]['system_instruction'] == answer_instruction('transcripts', True)


def test_general_mode_sends_the_general_instruction_and_no_context(harness, capture, tmp_path):
    harness._add_documents_to_displayed_session([_file(tmp_path, 'a.txt')])
    harness._set_live_qa_answer_mode('general')
    _ask(harness)
    assert capture[0]['system_instruction'] == answer_instruction('general')
    assert capture[0]['use_context'] is False


def _card(harness, capture):
    _ask(harness)
    return next(iter(harness._detached_answer_cards.items()))


def test_response_with_document_refs_badges_the_card(harness, capture):
    card_id, card = _card(harness, capture)
    response = SimpleNamespace(
        success=True, answer='From documents: Friday.',
        document_refs=[{'id': 1, 'name': 'syllabus.pdf'}])
    harness._on_detached_answer_finished(card_id, response)
    assert not card.document_badge_row.isHidden()
    assert card._document_badge.names == ['syllabus.pdf']


def test_response_without_refs_leaves_the_card_unbadged(harness, capture):
    card_id, card = _card(harness, capture)
    harness._on_detached_answer_finished(
        card_id, SimpleNamespace(success=True, answer='From transcripts: Friday.', document_refs=[]))
    assert card.document_badge_row.isHidden()


def test_streaming_text_hides_the_documents_line(harness, capture):
    card_id, card = _card(harness, capture)
    harness._on_detached_answer_delta(
        card_id, 'From documents: Friday.\nDocuments used: D3')
    assert 'Documents used' not in card.body_label.text()
    assert 'Friday' in card.body_label.text()


def test_documents_label_is_rendered_as_a_label():
    from src.app.pixel_widgets import format_answer_html
    html = format_answer_html('From documents: Friday.')
    assert '<b>' in html and 'From documents:' in html
