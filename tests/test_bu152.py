"""BU152: Any Session context for unsummarized (uploaded audio) sessions."""
import os
from datetime import datetime, timedelta
from unittest.mock import MagicMock

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest

from src.app.window import MainWindow
from src.assistant.context import AssistantContextRetriever, search_query
from src.assistant.context_models import ConversationTurn
from src.assistant.rag_context_builder import MAX_CHUNKS_PER_SESSION, build_routed_session_context
from src.assistant.service import AssistantAnswerService
from src.config import SESSION
from src.rag.indexer import PROFILE_TRANSCRIPT_CHARS, build_session_profile, index_session_content
from src.rag.router import RoutedSession
from src.storage.database import Database

TRANSCRIPT = [
    'we should go for coffee after the monte carlo lecture',
    'dynamic programming comes next and then the midterm review',
]


CONTEXT = '## [S1] x\n[transcript]: y'


@pytest.fixture
def db(tmp_path):
    database = Database(str(tmp_path / 'c.db'))
    database.connect()
    yield database
    database.disconnect()


def _session(db, name='WhatsApp Audio', summary=None, rows=TRANSCRIPT):
    sid = db.create_session(
        name=name, start_time=datetime(2026, 10, 9, 14, 11), status='completed',
        transcription_status='transcribed', summary_status='completed' if summary else 'none')
    for i, text in enumerate(rows):
        db.add_transcript(sid, datetime(2026, 10, 9, 14, 11) + timedelta(seconds=10 * i), text, 'microphone')
    if summary:
        db.add_summary(sid, 'full', summary, 'test-model')
    return sid


def _routed(sid, name='WhatsApp Audio'):
    return RoutedSession(session_id=sid, name=name, start_time=1760000000, score=1.0, reason='')


# --- Upload Audio chains the summary -----------------------------------------

def _upload_host(outcome, error=None):
    host = MagicMock()
    host._post_to_ui = lambda fn: fn()
    host._run_transcription = lambda *a, **k: MainWindow._run_transcription(host, *a, **k)
    host._refresh_all_sessions_window = MagicMock()
    host._refresh_session_completer = MagicMock()
    jobs = []

    def submit_job(label, fn, on_done=None):
        jobs.append(label)
        if label.startswith('transcribe'):
            on_done(outcome, error)

    host.session_manager.submit_job.side_effect = submit_job
    host.jobs = jobs
    return host


def _transcribe(host, auto_summary, monkeypatch):
    monkeypatch.setitem(SESSION, 'auto_summary_after_stop', auto_summary)
    MainWindow._transcribe_uploaded_session(host, MagicMock(id=7), MagicMock())


def test_upload_audio_summarizes_after_transcription(monkeypatch):
    host = _upload_host({'has_transcripts': True, 'transcribed': 3})
    _transcribe(host, True, monkeypatch)
    host._summarize_uploaded_session.assert_called_once()


def test_upload_audio_skips_summary_when_setting_off(monkeypatch):
    host = _upload_host({'has_transcripts': True, 'transcribed': 3})
    _transcribe(host, False, monkeypatch)
    host._summarize_uploaded_session.assert_not_called()


def test_upload_audio_skips_summary_without_transcripts(monkeypatch):
    host = _upload_host({'has_transcripts': False, 'transcribed': 0})
    _transcribe(host, True, monkeypatch)
    host._summarize_uploaded_session.assert_not_called()


def test_upload_audio_skips_summary_when_transcription_fails(monkeypatch):
    monkeypatch.setattr('src.app.window.QMessageBox', MagicMock())
    host = _upload_host(None, error=RuntimeError('boom'))
    _transcribe(host, True, monkeypatch)
    host._summarize_uploaded_session.assert_not_called()


# --- router profile -----------------------------------------------------------

def test_unsummarized_profile_uses_the_transcript(db):
    sid = _session(db, rows=TRANSCRIPT + ['filler ' * 600, 'zebra crossing at the very end'])
    profile = build_session_profile(db, sid)
    assert 'monte carlo lecture' in profile['profile_text']
    assert 'zebra' not in profile['profile_text'].split('Keywords:')[0]  # past the 2000 chars
    assert 'zebra' in profile['keywords'] or 'filler' in profile['keywords']
    assert 'coffee' in profile['keywords']
    assert len(profile['profile_text']) < PROFILE_TRANSCRIPT_CHARS + 300


def test_summarized_profile_ignores_the_transcript(db):
    sid = _session(db, summary='Quarterly budget planning discussion.')
    profile = build_session_profile(db, sid)
    assert 'budget' in profile['profile_text']
    assert 'coffee' not in profile['profile_text']
    assert 'coffee' not in profile['keywords']


def test_profile_refreshes_when_summary_arrives(db):
    sid = _session(db)
    index_session_content(db, sid)
    assert 'coffee' in db.get_session_profile(sid)['profile_text']
    db.add_summary(sid, 'full', 'Quarterly budget planning discussion.', 'test-model')
    index_session_content(db, sid)
    assert 'coffee' not in db.get_session_profile(sid)['profile_text']


# --- follow-up query ----------------------------------------------------------

def test_search_query_is_shared_with_the_specific_session_retriever():
    assert AssistantContextRetriever._search_query('is it a report?', []) == search_query('is it a report?', [])


def _service(messages):
    service = AssistantAnswerService.__new__(AssistantAnswerService)
    service._db = MagicMock()
    service._db.get_messages.return_value = messages
    return service


def test_service_routes_a_short_follow_up_with_the_previous_question(monkeypatch):
    service = _service([
        {'role': 'user', 'content': 'did they mention going for coffee?'},
        {'role': 'assistant', 'content': 'No.'},
    ])
    seen = {}

    def fake_route(_db, query, k=5):
        seen['route'] = query
        return [_routed(1)]

    def fake_build(_db, query, routed):
        seen['build'] = query
        return CONTEXT

    monkeypatch.setattr('src.assistant.service.route_sessions', fake_route)
    monkeypatch.setattr('src.assistant.service.build_routed_session_context', fake_build)
    service._get_all_sessions_context('what about the whatsapp audio session?', 5)
    assert 'coffee' in seen['route'] and 'whatsapp' in seen['route']
    assert seen['build'] == seen['route']


def test_service_searches_a_full_question_alone(monkeypatch):
    service = _service([{'role': 'user', 'content': 'earlier topic entirely'}])
    seen = {}
    monkeypatch.setattr('src.assistant.service.route_sessions',
                        lambda _db, query, k=5: seen.setdefault('route', query) and [_routed(1)])
    monkeypatch.setattr('src.assistant.service.build_routed_session_context',
                        lambda _db, query, routed: CONTEXT)
    service._get_all_sessions_context('what did the professor say about grading rubric deadlines', 5)
    assert 'earlier' not in seen['route']


# --- Tier-2 opening-chunks fallback -------------------------------------------

def test_unsummarized_session_without_matches_gets_its_opening_chunks(db):
    sid = _session(db, rows=[f'row {i} ' + 'word ' * 100 for i in range(30)])
    index_session_content(db, sid)
    context = build_routed_session_context(db, 'zzzunmatched', [_routed(sid)])
    assert context.count('[transcript @') == MAX_CHUNKS_PER_SESSION
    assert context.index('row 0') < context.index('[transcript @', context.index('row 0') + 1)


def test_matching_chunks_replace_the_opening_chunks(db):
    sid = _session(db, rows=['intro words here'] + ['filler ' * 110] * 30 + ['the coffee order ' + 'pad ' * 20])
    index_session_content(db, sid)
    context = build_routed_session_context(db, 'coffee', [_routed(sid)])
    assert 'coffee order' in context
    assert 'intro words' not in context


def test_summarized_session_without_matches_gets_no_opening_chunks(db):
    sid = _session(db, summary='Quarterly budget planning discussion.')
    index_session_content(db, sid)
    context = build_routed_session_context(db, 'zzzunmatched', [_routed(sid)])
    assert '[summary]' in context
    assert 'monte carlo' not in context
