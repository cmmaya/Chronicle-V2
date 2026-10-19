"""BU169 - speaker labels reach the assistant's evidence and the search index."""
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from src.assistant import rag_context_builder as rcb
from src.assistant.context import AssistantContextRetriever
from src.rag.indexer import SPEAKER_NOTE, _chunk_transcripts, index_session_content
from src.storage.database import Database

T0 = 1_791_414_441


def _rows(speakers=True):
    rows = [
        {'text': 'what is a confound', 'timestamp': T0, 'source': 'microphone', 'speaker': 2},
        {'text': 'a variable that distorts the effect', 'timestamp': T0 + 5, 'source': 'microphone',
         'speaker': 1},
        {'text': 'it changes with both', 'timestamp': T0 + 9, 'source': 'microphone', 'speaker': 1},
    ]
    if not speakers:
        for row in rows:
            row.pop('speaker')
    return rows


def test_chunks_open_with_speaker_and_split_per_speaker():
    chunks = _chunk_transcripts(_rows())
    assert [c['content'] for c in chunks] == [
        'Speaker 2: what is a confound',
        'Speaker 1: a variable that distorts the effect it changes with both',
    ]


def test_chunks_unchanged_without_speakers():
    [chunk] = _chunk_transcripts(_rows(speakers=False))
    assert chunk['content'] == 'what is a confound a variable that distorts the effect it changes with both'
    assert set(chunk) == {'content', 'chunk_index', 'start_timestamp', 'end_timestamp', 'source'}


@pytest.fixture
def db(tmp_path):
    database = Database(str(tmp_path / 'chronicle.db'))
    database.connect()
    yield database
    database.disconnect()


def test_index_search_finds_a_speaker(db):
    sid = db.create_session('Methods', datetime.fromtimestamp(T0), status='stopped')
    for row in _rows(speakers=False):
        db.add_transcript(sid, datetime.fromtimestamp(row['timestamp']), row['text'], row['source'])
    db.replace_diarized_transcripts(sid, _rows())
    db.set_diarization(sid, enabled=True, status='ready')
    with patch('src.rag.indexer._embed_chunks', return_value=None), \
            patch('src.rag.indexer.index_session_profile'):
        index_session_content(db, sid)
    hits = db.search_rag_fts('Speaker 2', session_id=sid)
    assert hits and hits[0]['content'].startswith('Speaker 2:')


def _db(rows, diarized):
    db = MagicMock()
    session = {'name': 'Class', 'start_time': T0, 'status': 'stopped'}
    if diarized:
        session.update(diarization_enabled=True, diarization_status='ready')
    db.get_session.return_value = session
    db.get_transcripts.return_value = rows
    db.get_summaries.return_value = []
    db.get_screenshots.return_value = []
    db.get_rag_document.return_value = None
    db.get_messages.return_value = []
    return db


def test_evidence_lines_carry_speaker_and_note():
    ctx = AssistantContextRetriever(_db(_rows(), True)).build_session_context(1, 'what was asked?')
    lines = [t.to_prompt_text() for t in ctx.transcripts]
    assert any('Speaker 2: what is a confound' in line for line in lines)
    assert ctx.summaries[0].summary_type == 'speakers' and ctx.summaries[0].content == SPEAKER_NOTE


def test_not_diarized_context_unchanged():
    # Diarized once, then switched off: readers get the original rows.
    plain_db = _db(_rows(False), False)
    plain_db.get_session.return_value.update(diarization_enabled=False, diarization_status='ready')
    plain = AssistantContextRetriever(plain_db).build_session_context(1, 'q')
    # The same rows through a database double that knows nothing of diarization.
    legacy_db = _db(_rows(False), False)
    legacy_db.get_session.return_value = {'name': 'Class', 'start_time': T0, 'status': 'stopped'}
    legacy = AssistantContextRetriever(legacy_db).build_session_context(1, 'q')
    assert plain == legacy
    assert not plain.summaries
    assert all('Speaker' not in t.text for t in plain.transcripts)


class _Routed:
    session_id = 1
    name = 'Class'
    start_time = T0
    score = 1.0
    reason = ''


def _rag_db(diarized):
    db = _db(_rows(diarized), diarized)
    db.search_rag_fts.return_value = [
        {'source_type': 'transcript', 'timestamp': T0, 'content': 'Speaker 2: what is a confound'
         if diarized else 'what is a confound', 'session_id': 1, 'chunk_id': 1}]
    return db


def test_any_session_context_notes_speakers():
    text = rcb.build_routed_session_context(_rag_db(True), 'confound', [_Routed()])
    assert f'[speakers]: {SPEAKER_NOTE}' in text and 'Speaker 2: what is a confound' in text


def test_any_session_context_unchanged_without_diarization():
    db = _rag_db(False)
    with_flags = rcb.build_routed_session_context(db, 'confound', [_Routed()])
    db.get_session.side_effect = AttributeError  # a database double without sessions
    without = rcb.build_routed_session_context(db, 'confound', [_Routed()])
    assert with_flags == without
    assert 'speakers' not in with_flags
