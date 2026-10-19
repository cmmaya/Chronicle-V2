"""BU145: golden baseline of every prompt a session with no documents sends.

Session Documents (BU146-BU151) must leave a session without documents
answered exactly as before. Each case below builds the ``messages`` list the
Specific Session chat, the detached answer cards or Any Session send, with
OpenRouter mocked, and compares it byte-for-byte with a file under
``tests/fixtures/prompt_baseline/``.

When a prompt is changed on purpose, regenerate the files with

    CHRONICLE_UPDATE_BASELINE=1 python -m pytest tests/test_bu145.py

and review the diff. BU146-BU151 must NOT do this: they add documents, and a
session without any must not change.

Clock-dependent output is frozen here (local-time rendering is pinned to UTC)
rather than masked in the comparison.
"""
import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.assistant import context as context_module
from src.assistant import context_models, service as service_module
from src.assistant.context_models import ScreenshotReference  # noqa: F401
from src.assistant.live_qa import (
    answer_instruction, build_question_for_candidate,
    build_question_from_records,
)
from src.assistant.response_contract import RESPONSE_CONTRACT
from src.assistant.service import AssistantAnswerService
from src.assistant.session_resolver import ScopeResolution
from src.config import ASSISTANT_AGENTS, LIVE_QA, SESSION
from src.screenshots.search import ScreenshotHit

BASELINE_DIR = Path(__file__).parent / 'fixtures' / 'prompt_baseline'
UPDATE = os.environ.get('CHRONICLE_UPDATE_BASELINE') == '1'

T0 = 1_791_414_441
AGENT = ASSISTANT_AGENTS['agents']['chronicle_assistant']


class _UtcDatetime(datetime):
    """``datetime`` whose ``fromtimestamp`` ignores the machine's timezone."""

    @classmethod
    def fromtimestamp(cls, ts, tz=None):
        return datetime.fromtimestamp(ts, tz or timezone.utc).replace(tzinfo=None)


@pytest.fixture(autouse=True)
def frozen_clock(monkeypatch):
    monkeypatch.setattr(context_models, 'datetime', _UtcDatetime)
    monkeypatch.setattr(service_module, 'datetime', _UtcDatetime)


def _rows(texts, source='system', step=60):
    return [{'text': t, 'timestamp': T0 + i * step, 'source': source}
            for i, t in enumerate(texts)]


RECORDED = _rows(['welcome to the class', 'today we cover sampling methods',
                  'the critique is due next week', 'any final advice on the project'])
LONG = _rows([f'line {i} about the sampling method and the survey'
              for i in range(12)])
SUMMARIES = [{'summary_type': 'full', 'content': 'SUMMARY: a class on sampling.'}]
HITS = [
    ScreenshotHit(screenshot_id=7, timestamp=T0 + 30, filepath='a.png',
                  preview='slide: sampling methods', tier='preview'),
    ScreenshotHit(screenshot_id=8, timestamp=T0 + 130, filepath='b.png',
                  preview='slide: critique schedule', tier='full',
                  details={'ai_summary': 'Critique due dates.',
                           'visible_text': ['Critique 1', 'Friday'],
                           'keywords': ['critique']}),
]
HISTORY = [
    {'role': 'user', 'content': 'what was covered?'},
    {'role': 'assistant', 'content': 'Sampling methods.'},
]


def _db(rows, origin='recorded', status='stopped', summaries=SUMMARIES):
    db = MagicMock()
    db.get_session.return_value = {
        'name': 'Class', 'start_time': T0, 'status': status, 'origin': origin}
    db.get_transcripts.return_value = list(rows)
    db.get_summaries.return_value = list(summaries)
    db.get_screenshots.return_value = []
    db.get_rag_document.return_value = None
    db.get_messages.return_value = []
    # BU146+: a session without documents.
    db.get_session_documents.return_value = []
    db.count_session_documents.return_value = 0
    return db


def _service(db, monkeypatch, screenshots=False, agent=AGENT):
    monkeypatch.setattr(
        context_module, 'search_session_screenshots',
        lambda *a, **k: list(HITS) if screenshots else [])
    service = AssistantAnswerService(db)
    service._agents = {'chronicle_assistant': agent}
    resolution = MagicMock(session_ids=[1], candidates=[])
    resolution.scope = ScopeResolution.CURRENT_SESSION
    service._resolver = MagicMock()
    service._resolver.resolve.return_value = resolution
    return service


def _messages(service, question, conversation_id=None, use_context=True):
    """The messages ``ask_async`` sends, with OpenRouter mocked."""
    sent = {}

    async def fake_call(messages, **kwargs):
        sent['messages'] = messages
        return 'ok'

    service._call_openrouter_async = fake_call
    asyncio.run(service.ask_async(
        question, agent_id='chronicle_assistant',
        explicit_scope='current_session', active_session_id=1,
        conversation_id=conversation_id, persist=False,
        use_context=use_context))
    return sent['messages']


def _check(name, messages):
    path = BASELINE_DIR / f'{name}.json'
    text = json.dumps(messages, indent=2, ensure_ascii=False) + '\n'
    if UPDATE:
        BASELINE_DIR.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode('utf-8'))
        return
    assert path.exists(), f'missing golden file {path.name}; see the module docstring'
    assert path.read_bytes() == text.encode('utf-8'), (
        f'{name}: the prompt changed for a session without documents')


QUESTION = 'what is due next week?'


def test_chat_full_transcript_with_screenshots(monkeypatch):
    service = _service(_db(RECORDED), monkeypatch, screenshots=True)
    _check('chat_full_screenshots', _messages(service, QUESTION))


def test_chat_full_transcript_without_screenshots(monkeypatch):
    service = _service(_db(RECORDED), monkeypatch)
    _check('chat_full_no_screenshots', _messages(service, QUESTION))


def test_chat_full_transcript_inserted_text_session(monkeypatch):
    service = _service(_db(RECORDED, origin='text'), monkeypatch)
    _check('chat_full_text_session', _messages(service, QUESTION))


def test_chat_full_transcript_live_session(monkeypatch):
    service = _service(_db(RECORDED, status='active', summaries=[]), monkeypatch)
    _check('chat_full_live', _messages(service, QUESTION))


def test_chat_search_layout_over_the_limit(monkeypatch):
    monkeypatch.setitem(SESSION, 'full_transcript_max_chars', 200)
    service = _service(_db(LONG), monkeypatch)
    _check('chat_search', _messages(service, 'when is the survey?'))


def test_chat_search_layout_with_screenshots(monkeypatch):
    monkeypatch.setitem(SESSION, 'full_transcript_max_chars', 200)
    service = _service(_db(LONG), monkeypatch, screenshots=True)
    _check('chat_search_screenshots', _messages(service, 'when is the survey?'))


def test_chat_follow_up_replays_history(monkeypatch):
    db = _db(RECORDED)
    db.get_messages.return_value = list(HISTORY)
    service = _service(db, monkeypatch, screenshots=True)
    _check('chat_follow_up', _messages(service, QUESTION, conversation_id=5))


def _record(minute, text, source='system'):
    start = datetime(2026, 10, 5, 21, minute, 0)
    return SimpleNamespace(start_dt=start, end_dt=start, source=source, text=text)


DETACHED_RECORDS = [_record(0, 'welcome to the class'),
                    _record(1, 'what is sampling?', 'mic'),
                    _record(2, 'sampling is choosing a subset')]


def _card_service(db, monkeypatch, mode):
    agent = {'system_instruction': answer_instruction(mode, '')}
    return _service(db, monkeypatch, screenshots=True, agent=agent)


def test_detached_card_transcripts_manual_selection(monkeypatch):
    question = build_question_from_records(
        DETACHED_RECORDS[1:2], earlier=DETACHED_RECORDS[:1])
    service = _card_service(_db(RECORDED), monkeypatch, 'transcripts')
    _check('card_transcripts_manual', _messages(service, question))


def test_detached_card_transcripts_manual_selection_search_layout(monkeypatch):
    monkeypatch.setitem(SESSION, 'full_transcript_max_chars', 200)
    question = build_question_from_records(
        DETACHED_RECORDS[1:2], earlier=DETACHED_RECORDS[:1])
    service = _card_service(_db(LONG), monkeypatch, 'transcripts')
    _check('card_transcripts_manual_search', _messages(service, question))


def test_detached_card_transcripts_detected_candidate(monkeypatch):
    candidate = SimpleNamespace(text='what is sampling?')
    question = build_question_for_candidate(candidate, DETACHED_RECORDS)
    service = _card_service(_db(RECORDED), monkeypatch, 'transcripts')
    _check('card_transcripts_candidate', _messages(service, question))


def test_detached_card_general_knowledge(monkeypatch):
    question = build_question_from_records(DETACHED_RECORDS[1:2])
    service = _card_service(_db(RECORDED), monkeypatch, 'general')
    messages = _messages(service, question, use_context=False)
    _check('card_general', messages)


def test_chat_any_session(monkeypatch):
    service = _service(_db(RECORDED), monkeypatch)
    messages = service._build_messages(
        AGENT, '## Relevant Sessions\n[Session 1] Class\n', QUESTION, None,
        is_any_session=True)
    assert RESPONSE_CONTRACT in messages[0]['content']
    _check('chat_any_session', messages)


def test_answer_instructions_are_pinned_for_every_mode():
    modes = sorted(LIVE_QA['answer_instructions'])
    assert modes
    _check('answer_instructions',
           {mode: answer_instruction(mode, '') for mode in modes})
