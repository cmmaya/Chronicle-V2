"""BU149: session documents in Specific Session prompts."""
import asyncio
import json
from unittest.mock import MagicMock

import pytest

# The BU145 scenarios and fixtures are imported, not copied.
from test_bu145 import (  # noqa: F401
    AGENT, BASELINE_DIR, DETACHED_RECORDS, HISTORY, LONG, QUESTION, RECORDED,
    T0, _card_service, _db, _messages, _service, frozen_clock,
)
from src.assistant.document_contract import (
    DOCUMENT_CONTRACT, citation_line, parse_document_refs,
)
from src.assistant.live_qa import build_question_from_records
from src.assistant.screenshot_contract import SCREENSHOT_CONTRACT, citation_line as shot_line
from src.assistant.session_resolver import ScopeResolution
from src.config import SESSION, SESSION_DOCUMENTS

SYLLABUS = ('Week 1 covers sampling methods.\n\n'
            'Week 2 covers the survey critique, due on Friday the 14th.')
HANDOUT = 'Exercise 4 asks for a stratified sample of 200 households.'
DOCS = [
    {'id': 11, 'session_id': 1, 'name': 'syllabus.pdf', 'file_type': 'pdf', 'text': SYLLABUS},
    {'id': 12, 'session_id': 1, 'name': 'handout.md', 'file_type': 'md', 'text': HANDOUT},
]


def _db_with(docs, rows=RECORDED, **kwargs):
    db = _db(rows, **kwargs)
    db.get_session_documents.return_value = [dict(d) for d in docs]
    return db


# --- no documents: byte-identical to BU145 ------------------------------------

def _golden(name):
    return (BASELINE_DIR / f'{name}.json').read_bytes()


def _dump(messages):
    return (json.dumps(messages, indent=2, ensure_ascii=False) + '\n').encode('utf-8')


@pytest.mark.parametrize('golden,rows,kwargs', [
    ('chat_full_screenshots', RECORDED, {'screenshots': True}),
    ('chat_full_no_screenshots', RECORDED, {}),
])
def test_no_documents_matches_the_baseline(monkeypatch, golden, rows, kwargs):
    service = _service(_db(rows), monkeypatch, **kwargs)
    assert _dump(_messages(service, QUESTION)) == _golden(golden)
    assert service._last_documents == {}


def test_no_documents_search_layout_and_follow_up_match_the_baseline(monkeypatch):
    monkeypatch.setitem(SESSION, 'full_transcript_max_chars', 200)
    service = _service(_db(LONG), monkeypatch)
    assert _dump(_messages(service, 'when is the survey?')) == _golden('chat_search')
    monkeypatch.setitem(SESSION, 'full_transcript_max_chars', 300000)
    db = _db(RECORDED)
    db.get_messages.return_value = list(HISTORY)
    service = _service(db, monkeypatch, screenshots=True)
    assert _dump(_messages(service, QUESTION, conversation_id=5)) == _golden('chat_follow_up')


def test_unreadable_documents_cost_the_documents_not_the_answer(monkeypatch):
    db = _db(RECORDED)
    db.get_session_documents.side_effect = RuntimeError('db locked')
    service = _service(db, monkeypatch)
    assert _dump(_messages(service, QUESTION)) == _golden('chat_full_no_screenshots')


# --- whole mode ----------------------------------------------------------------

def test_whole_documents_sit_in_the_system_message_before_the_transcript(monkeypatch):
    service = _service(_db_with(DOCS), monkeypatch, screenshots=True)
    messages = _messages(service, QUESTION)
    system, user = messages[0]['content'], messages[-1]['content']

    assert system.index(SCREENSHOT_CONTRACT) < system.index(DOCUMENT_CONTRACT)
    assert system.index(DOCUMENT_CONTRACT) < system.index('[D11] syllabus.pdf (pdf)')
    assert system.index('## Session documents') < system.index('Context:\n## Relevant Sessions')
    assert system.index('Context:\n## Relevant Sessions') < system.index('## Full transcript')
    assert '[D12] handout.md (md)' in system and HANDOUT in system
    assert 'Exercise 4' not in user and 'Week 2 covers' not in user


def test_whole_mode_keeps_the_system_message_a_prefix_extension(monkeypatch):
    first = _messages(_service(_db_with(DOCS, status='active'), monkeypatch), QUESTION)[0]['content']
    rows = RECORDED + [{'text': 'one more thing', 'timestamp': T0 + 999, 'source': 'system'}]
    second = _messages(_service(_db_with(DOCS, rows, status='active'), monkeypatch), QUESTION)[0]['content']
    assert second.startswith(first) and second != first


def test_whole_mode_in_the_search_layout_goes_before_the_background(monkeypatch):
    monkeypatch.setitem(SESSION, 'full_transcript_max_chars', 200)
    messages = _messages(_service(_db_with(DOCS, LONG), monkeypatch), 'when is the survey?')
    system = messages[0]['content']
    assert system.index(DOCUMENT_CONTRACT) < system.index('## Session documents') < system.index('Context:')
    assert '## Session documents' not in messages[-1]['content']


# --- excerpt mode --------------------------------------------------------------

def test_over_the_total_budget_sends_excerpts_in_the_user_message(monkeypatch):
    monkeypatch.setitem(SESSION_DOCUMENTS, 'whole_total_max_chars', 10)
    monkeypatch.setitem(SESSION_DOCUMENTS, 'excerpt_chars_per_document', 80)
    service = _service(_db_with(DOCS), monkeypatch)
    messages = _messages(service, 'what does exercise 4 ask for?')
    system, user = messages[0]['content'], messages[-1]['content']

    assert DOCUMENT_CONTRACT in system
    assert '## Session documents' not in system and HANDOUT not in system
    assert '## Session document excerpts' in user
    assert '[D12] handout.md (md)\n' + HANDOUT in user
    assert '[D11] syllabus.pdf (pdf)' in user
    assert user.rstrip().endswith('Question: what does exercise 4 ask for?')
    assert service._last_documents['mode'] == 'excerpt'


def test_excerpts_ignore_the_transcript_evidence_of_a_live_question(monkeypatch):
    monkeypatch.setitem(SESSION_DOCUMENTS, 'whole_total_max_chars', 10)
    monkeypatch.setitem(SESSION_DOCUMENTS, 'excerpt_chars_per_document', 80)
    question = (build_question_from_records(DETACHED_RECORDS[1:2], earlier=DETACHED_RECORDS[:1]))
    messages = _messages(_service(_db_with(DOCS), monkeypatch), question)
    assert 'Exercise 4' in messages[-1]['content']


# --- scopes that never read documents -------------------------------------------

def test_any_session_never_reads_or_renders_documents(monkeypatch):
    db = _db_with(DOCS)
    service = _service(db, monkeypatch)
    service._resolver.resolve.return_value.scope = ScopeResolution.ALL_SESSIONS
    service._get_all_sessions_context = lambda question: 'ANY CONTEXT'
    sent = {}

    async def fake_call(messages, **kwargs):
        sent['messages'] = messages
        return 'ok'

    service._call_openrouter_async = fake_call
    asyncio.run(service.ask_async(QUESTION, agent_id='chronicle_assistant', persist=False))
    db.get_session_documents.assert_not_called()
    assert 'Session documents' not in sent['messages'][0]['content']

    service._last_documents = {'mode': 'whole', 'block': 'LEAK', 'names': {1: 'x'}}
    built = service._build_messages(AGENT, 'ctx', 'q?', None, is_any_session=True)
    assert 'LEAK' not in built[0]['content'] + built[-1]['content']


def test_general_knowledge_card_never_reads_documents(monkeypatch):
    db = _db_with(DOCS)
    service = _card_service(db, monkeypatch, 'general')
    question = build_question_from_records(DETACHED_RECORDS[1:2])
    messages = _messages(service, question, use_context=False)
    db.get_session_documents.assert_not_called()
    assert (json.dumps(messages, indent=2, ensure_ascii=False) + '\n').encode() == _golden('card_general')


def test_no_context_path_never_reads_documents(monkeypatch):
    db = _db_with(DOCS)
    service = _service(db, monkeypatch)
    resolution = MagicMock(session_ids=[1])
    resolution.scope = ScopeResolution.CURRENT_SESSION
    assert service._retrieve_context(False, resolution, QUESTION, None) == ('', '', '')
    db.get_session_documents.assert_not_called()


# --- parsing and persistence -----------------------------------------------------

@pytest.mark.parametrize('answer,allowed,expected_answer,expected_ids', [
    ('From the syllabus.\nDocuments used: D11, D12', {11, 12}, 'From the syllabus.', [11, 12]),
    ('Answer\nDocuments used: D11, D99', {11}, 'Answer', [11]),
    ('Answer\nDocuments used: D99', {11}, 'Answer', []),
    ('Plain answer, no line.', {11}, 'Plain answer, no line.', []),
    ('Answer\nDocuments used: the syllabus', {11}, 'Answer\nDocuments used: the syllabus', []),
    ('Answer\nDocuments used: D11\n', {11}, 'Answer', [11]),
    ('Answer\ndocuments used: d11, D11', {11}, 'Answer', [11]),
    ('We discussed documents used in class.\nMore.', {11}, 'We discussed documents used in class.\nMore.', []),
])
def test_parse_document_refs(answer, allowed, expected_answer, expected_ids):
    assert parse_document_refs(answer, allowed) == (expected_answer, expected_ids)


def test_parse_document_refs_with_a_screenshot_line_before_it():
    answer = f'Answer\n{shot_line([7])}\nDocuments used: D11'
    stripped, ids = parse_document_refs(answer, {11})
    assert ids == [11] and stripped == f'Answer\n{shot_line([7])}'


def test_parse_document_refs_never_raises():
    assert parse_document_refs(None, {1}) == (None, [])
    assert parse_document_refs('', None) == ('', [])


def _ask_with_answer(service, raw_answer, persist=True):
    async def fake_call(messages, **kwargs):
        return raw_answer

    service._call_openrouter_async = fake_call
    return asyncio.run(service.ask_async(
        QUESTION, agent_id='chronicle_assistant', explicit_scope='current_session',
        active_session_id=1, persist=persist))


def test_response_carries_validated_refs_and_persistence_keeps_the_line(monkeypatch):
    db = _db_with(DOCS)
    db.create_conversation.return_value = 5
    service = _service(db, monkeypatch, screenshots=True)
    raw = f'The critique is due Friday.\n{shot_line([7, 99])}\nDocuments used: D12, D11, D404'
    response = _ask_with_answer(service, raw)

    assert response.success
    assert response.document_refs == [{'id': 12, 'name': 'handout.md'},
                                      {'id': 11, 'name': 'syllabus.pdf'}]
    assert 'Documents used' not in response.answer
    assert response.screenshot_refs == [7]
    assert response.answer.endswith(shot_line([7]))
    stored = db.add_message.call_args_list[-1].args
    assert stored[1] == 'assistant'
    assert stored[2] == f'{response.answer}\n\n{citation_line([12, 11])}'


def test_answer_without_a_documents_line_has_no_refs_and_stores_it_unchanged(monkeypatch):
    db = _db_with(DOCS)
    db.create_conversation.return_value = 5
    response = _ask_with_answer(_service(db, monkeypatch), 'Said in the session.')
    assert response.document_refs == []
    assert db.add_message.call_args_list[-1].args[2] == 'Said in the session.'


def test_session_without_documents_keeps_a_documents_line_it_was_never_offered(monkeypatch):
    db = _db(RECORDED)
    db.create_conversation.return_value = 5
    response = _ask_with_answer(_service(db, monkeypatch), 'Answer\nDocuments used: D11')
    assert response.document_refs == []
    assert response.answer == 'Answer\nDocuments used: D11'
