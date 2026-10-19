"""BU161: Any Session archive-wide rare-word search and centred excerpts."""
from datetime import datetime, timedelta

import pytest

from src.assistant import service as service_module
from src.assistant.rag_context_builder import (
    MAX_EXCERPT_CHARS,
    _excerpt,
    build_routed_session_context,
    find_archive_chunks,
    rank_query_terms,
)
from src.assistant.service import AssistantAnswerService
from src.rag.indexer import index_session_content
from src.rag.router import RoutedSession
from src.storage.database import Database

QUESTION = 'Where was the location they mentioned I should go near Toronto'
TORONTO = 'if you have a free weekend you should go to Niagara on the Lake, it is near Toronto'


@pytest.fixture
def db(tmp_path):
    database = Database(str(tmp_path / 'c.db'))
    database.connect()
    yield database
    database.disconnect()


def _session(db, name, rows, summary):
    start = datetime(2026, 10, 9, 14, 0)
    sid = db.create_session(name=name, start_time=start, status='completed',
                            transcription_status='transcribed', summary_status='completed')
    # 70 s apart: every row is its own chunk (windows close at 60 s).
    for i, text in enumerate(rows):
        db.add_transcript(sid, start + timedelta(seconds=70 * i), text, 'microphone')
    db.add_summary(sid, 'full', summary, 'test-model')
    index_session_content(db, sid)
    return sid


@pytest.fixture
def archive(db):
    """Toronto is a passing remark in a budget meeting; "should" is everywhere."""
    budget = _session(db, 'Budget Review', [
        'we should cut the travel line by ten percent',
        TORONTO,
        'we should close the quarter before the audit',
    ], 'Quarterly budget review and travel cuts.')
    other = _session(db, 'Sprint Planning', [
        f'we should move ticket {n} to the next sprint' for n in range(8)
    ], 'Sprint planning for the mobile release.')
    return {'budget': budget, 'other': other}


def _routed(sid, name):
    return RoutedSession(session_id=sid, name=name, start_time=1760000000, score=1.0, reason='')


def test_rare_terms_keep_the_specific_word_only(db, archive):
    terms = rank_query_terms(db, QUESTION)
    assert terms['rare'] == ['toronto']  # "should" is in 11 of 14 chunks, "go" too short
    assert terms['ranked'][-1] == 'location'  # no chunk has it


def test_archive_search_finds_a_remark_the_summary_left_out(db, archive):
    chunks = find_archive_chunks(db, rank_query_terms(db, QUESTION)['rare'])
    assert any('Niagara' in c['content'] for c in chunks)
    assert all(c['source_type'] != 'assistant_message' for c in chunks)


def test_no_rare_word_adds_nothing(db, archive):
    assert find_archive_chunks(db, rank_query_terms(db, 'what should we do')['rare']) == []


def test_archive_session_is_added_when_routing_missed_it(db, archive):
    terms = rank_query_terms(db, QUESTION)
    context = build_routed_session_context(
        db, QUESTION, [_routed(archive['other'], 'Sprint Planning')],
        archive=find_archive_chunks(db, terms['rare']), terms=terms['ranked'])
    assert f"[S{archive['other']}] Sprint Planning" in context
    assert f"[S{archive['budget']}] Budget Review" in context
    assert 'Niagara' in context
    assert context.index('Sprint Planning') < context.index('Budget Review')


def test_archive_chunk_in_a_routed_session_is_not_repeated(db, archive):
    terms = rank_query_terms(db, QUESTION)
    context = build_routed_session_context(
        db, QUESTION, [_routed(archive['budget'], 'Budget Review')],
        archive=find_archive_chunks(db, terms['rare']), terms=terms['ranked'])
    assert context.count('Niagara') == 1
    assert context.count('## [S') == 1


def test_context_without_archive_is_unchanged(db, archive):
    routed = [_routed(archive['other'], 'Sprint Planning')]
    assert build_routed_session_context(db, QUESTION, routed) == \
        build_routed_session_context(db, QUESTION, routed, archive=[])


def test_service_offers_the_archive_session(db, archive, monkeypatch):
    monkeypatch.setattr(service_module, 'route_sessions', lambda *a, **k: [
        _routed(archive['other'], 'Sprint Planning')])
    service = AssistantAnswerService.__new__(AssistantAnswerService)
    service._db = db
    service._last_routed_sessions = []
    context = service._get_all_sessions_context(QUESTION)
    assert 'Niagara' in context
    ids = [s['session_id'] for s in service._last_routed_sessions]
    assert ids == [archive['other'], archive['budget']]
    assert service._last_routed_sessions[1]['session_name'] == 'Budget Review'


def test_routing_nothing_still_answers_from_the_archive(db, archive, monkeypatch):
    monkeypatch.setattr(service_module, 'route_sessions', lambda *a, **k: [])
    service = AssistantAnswerService.__new__(AssistantAnswerService)
    service._db = db
    service._last_routed_sessions = []
    assert 'Niagara' in service._get_all_sessions_context(QUESTION)


# --- excerpts -----------------------------------------------------------------

def test_excerpt_is_centred_on_a_late_match():
    text = 'filler words here ' * 40 + 'it is near Toronto and close to the lake ' + 'more ' * 80
    excerpt = _excerpt(text, ['toronto'])
    assert 'Toronto' in excerpt
    assert excerpt.startswith('...') and excerpt.endswith('...')
    assert len(excerpt) <= MAX_EXCERPT_CHARS + 6


def test_excerpt_prefers_the_first_term_in_priority_order():
    text = 'should ' * 60 + 'Toronto ' + 'x ' * 200
    assert 'Toronto' in _excerpt(text, ['toronto', 'should'])


def test_excerpt_without_a_match_keeps_the_opening():
    text = 'a' * 900
    assert _excerpt(text, ['toronto']) == 'a' * MAX_EXCERPT_CHARS + '...'
    assert _excerpt('short', ['toronto']) == 'short'
