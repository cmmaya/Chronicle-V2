"""BU144: a Specific Session question is answered from the whole transcript."""
from unittest.mock import MagicMock

import pytest

from src.assistant.context import AssistantContextRetriever
from src.assistant.live_qa import (
    CANDIDATE_CONTEXT_PREAMBLE, CANDIDATE_QUESTION_PREAMBLE,
    EARLIER_TRANSCRIPT_PREAMBLE, QUESTION_PREAMBLE,
)
from src.assistant.screenshot_contract import SCREENSHOT_CONTRACT
from src.assistant.service import AssistantAnswerService
from src.config import SESSION

T0 = 1_791_414_441
AGENT = {'system_instruction': 'INSTRUCTION'}


def _rows(texts, source='system', step=60):
    return [{'text': t, 'timestamp': T0 + i * step, 'source': source}
            for i, t in enumerate(texts)]


def _db(rows, status='stopped', summaries=()):
    db = MagicMock()
    db.get_session.return_value = {'name': 'Class', 'start_time': T0, 'status': status}
    db.get_transcripts.return_value = list(rows)
    db.get_summaries.return_value = list(summaries)
    db.get_screenshots.return_value = []
    db.get_rag_document.return_value = None
    db.get_messages.return_value = []
    return db


ROWS = _rows(['welcome to the class', 'today we cover sampling',
              'the critique is due next week', 'any final advice'])


def _ctx(rows=ROWS, status='stopped', question='what was covered?'):
    db = _db(rows, status)
    return db, AssistantContextRetriever(db).build_session_context(1, question)


def test_under_limit_returns_every_row_in_order_without_fts():
    shuffled = [ROWS[2], ROWS[0], ROWS[3], ROWS[1]]
    db, ctx = _ctx(shuffled)
    assert ctx.transcript_full
    assert [t.text for t in ctx.transcripts] == [r['text'] for r in ROWS]
    db.search_rag_fts.assert_not_called()


def test_overlap_is_collapsed_between_rows_of_one_source():
    rows = _rows(['we looked at the sampling method',
                  'at the sampling method and then the survey'])
    _, ctx = _ctx(rows)
    assert [t.text for t in ctx.transcripts][1] == 'and then the survey'


def test_over_limit_falls_back_to_search(monkeypatch):
    monkeypatch.setitem(SESSION, 'full_transcript_max_chars', 20)
    _, ctx = _ctx()
    assert not ctx.transcript_full


def test_limit_zero_disables_full_mode(monkeypatch):
    monkeypatch.setitem(SESSION, 'full_transcript_max_chars', 0)
    _, ctx = _ctx()
    assert not ctx.transcript_full


def test_live_session_includes_newest_row():
    _, ctx = _ctx(status='active')
    assert ctx.transcript_full and ctx.transcript_live
    assert ctx.transcripts[-1].text == 'any final advice'
    assert ctx.transcript_until == ROWS[-1]['timestamp']


def _service(db):
    return AssistantAnswerService(db)


def _prompt(service, question='what was covered?', screenshots=()):
    background, evidence, full = service._get_single_session_context(1, question)
    service._last_screenshot_ids = set(screenshots)
    return background, evidence, full, service._build_messages(
        AGENT, evidence, question, None, background=background,
        full_transcript=full)


def test_message_layout_for_full_transcript():
    db = _db(ROWS, summaries=[{'summary_type': 'full', 'content': 'SUMMARY TEXT'}])
    service = _service(db)
    background, evidence, full, messages = _prompt(service, screenshots={7})
    system, user = messages[0]['content'], messages[-1]['content']

    assert background == ''
    order = [system.index(x) for x in (
        'INSTRUCTION', SCREENSHOT_CONTRACT, '## Relevant Sessions',
        '## Full transcript (in time order)', 'the critique is due')]
    assert order == sorted(order)
    assert 'SUMMARY TEXT' not in system
    assert 'SUMMARY TEXT' in user and 'Question: what was covered?' in user
    assert '## Transcripts' not in user and 'the critique is due' not in user


def test_live_row_appended_extends_system_prefix():
    service = _service(_db(ROWS, status='active'))
    first = _prompt(service)[3][0]['content']
    service = _service(_db(ROWS + [{'text': 'one more thing', 'timestamp': T0 + 999, 'source': 'system'}], status='active'))
    second = _prompt(service)[3][0]['content']
    assert second.startswith(first) and second != first


def test_search_mode_keeps_old_layout(monkeypatch):
    monkeypatch.setitem(SESSION, 'full_transcript_max_chars', 0)
    service = _service(_db(ROWS))
    background, evidence, full, messages = _prompt(service)
    assert full == '' and '## Relevant Sessions' in background
    assert '## Transcripts' in messages[-1]['content']


def test_any_session_prompt_unchanged():
    service = _service(_db(ROWS))
    messages = service._build_messages(AGENT, 'ctx', 'q?', None, is_any_session=True)
    assert messages[0]['content'].startswith('INSTRUCTION')
    assert 'Full transcript' not in messages[0]['content']
    assert messages[1]['content'] == 'Context for this question:\nctx\n\nQuestion: q?'


def _ask(service, question, monkeypatch=None):
    """Run ask() against a canned OpenRouter call; return the messages sent."""
    sent = {}
    service._call_openrouter = lambda messages, **kw: sent.setdefault('m', messages) and 'ok'
    resolution = MagicMock(scope='current_session', session_ids=[1], candidates=[])
    service._resolver = MagicMock()
    service._resolver.resolve.return_value = resolution
    service._agents = {'chronicle_assistant': AGENT}
    from src.assistant.session_resolver import ScopeResolution
    resolution.scope = ScopeResolution.CURRENT_SESSION
    service.ask(question, agent_id='chronicle_assistant', explicit_scope='current_session',
                active_session_id=1)
    return sent['m']


EVIDENCE = 'EARLIER SPEECH LINE'
SELECTION = f'{QUESTION_PREAMBLE}\n\n[21:00:00] Mic: selected excerpt'
CANDIDATE = (f'{CANDIDATE_QUESTION_PREAMBLE}\n\nwhat is sampling?\n\n'
             f'{CANDIDATE_CONTEXT_PREAMBLE}\n\n{EVIDENCE}')


@pytest.mark.parametrize('question,kept', [
    (f'{SELECTION}\n\n{EARLIER_TRANSCRIPT_PREAMBLE}\n\n{EVIDENCE}', 'selected excerpt'),
    (CANDIDATE, 'what is sampling?'),
])
def test_detached_question_strips_evidence_in_full_mode(question, kept):
    messages = _ask(_service(_db(ROWS)), question)
    user = messages[-1]['content']
    assert kept in user and EVIDENCE not in user
    assert 'the critique is due' in messages[0]['content']


def test_detached_question_keeps_evidence_over_limit(monkeypatch):
    monkeypatch.setitem(SESSION, 'full_transcript_max_chars', 20)
    messages = _ask(_service(_db(ROWS)), CANDIDATE)
    assert EVIDENCE in messages[-1]['content']
