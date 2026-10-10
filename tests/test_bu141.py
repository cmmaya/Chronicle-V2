"""BU141: live / unindexed sessions are searched in memory from raw rows."""
import json
from unittest.mock import MagicMock

from src.config import SESSION
from src.assistant.context import AssistantContextRetriever, RECENT_TAIL_CHARS

T0 = 1_791_414_441


def _rows(texts, step=120):
    return [{'text': t, 'timestamp': T0 + i * step, 'source': 'system'}
            for i, t in enumerate(texts)]


def _db(rows, status='stopped', rag_doc=None, fts=()):
    db = MagicMock()
    db.get_session.return_value = {'name': 'S', 'start_time': T0, 'status': status}
    db.get_transcripts.return_value = rows
    db.get_summaries.return_value = []
    db.get_screenshots.return_value = []
    db.get_rag_document.return_value = rag_doc
    db.search_rag_fts.return_value = list(fts)
    return db


FILLER = ['unrelated talk number %d about design' % i for i in range(40)]
ROWS = _rows(FILLER[:5] + ['remember the final report is due in two weeks'] + FILLER[5:])


def test_live_session_skips_index_and_finds_match():
    db = _db(ROWS, status='active')
    ctx = AssistantContextRetriever(db).build_session_context(1, 'final report due')

    db.search_rag_fts.assert_not_called()
    assert ctx.transcript_live and not ctx.transcript_indexed
    assert any('final report' in t.text for t in ctx.transcripts)
    assert ctx.transcript_until == ROWS[-1]['timestamp']


def test_live_session_always_includes_newest_windows():
    db = _db(ROWS, status='active')
    ctx = AssistantContextRetriever(db).build_session_context(1, 'zzz nothing matches')

    texts = [t.text for t in ctx.transcripts]
    assert texts[-1] == FILLER[-1]
    assert sum(len(t) for t in texts) <= RECENT_TAIL_CHARS


def test_excerpts_come_back_in_time_order():
    db = _db(ROWS, status='active')
    ctx = AssistantContextRetriever(db).build_session_context(1, 'final report')
    stamps = [t.timestamp for t in ctx.transcripts]
    assert stamps == sorted(stamps)


def test_missing_index_after_stop_uses_raw_rows():
    db = _db(ROWS, rag_doc=None)
    ctx = AssistantContextRetriever(db).build_session_context(1, 'final report')

    db.search_rag_fts.assert_not_called()
    assert not ctx.transcript_live and not ctx.transcript_indexed
    assert any('final report' in t.text for t in ctx.transcripts)


def test_short_index_counts_as_unindexed():
    doc = {'metadata_json': json.dumps({'transcript_count': len(ROWS) - 3})}
    ctx = AssistantContextRetriever(_db(ROWS, rag_doc=doc)).build_session_context(1, 'report')
    assert not ctx.transcript_indexed


def test_covering_index_uses_rag(monkeypatch):
    monkeypatch.setitem(SESSION, 'full_transcript_max_chars', 0)  # BU144: search path
    doc = {'metadata_json': json.dumps({'transcript_count': len(ROWS)})}
    hit = {'chunk_id': 1, 'document_id': 1, 'source_type': 'transcript',
           'source_id': 1, 'session_id': 1, 'timestamp': T0, 'title': 't',
           'content': 'indexed chunk text', 'rank': -1.0}
    db = _db(ROWS, rag_doc=doc, fts=[hit])
    ctx = AssistantContextRetriever(db).build_session_context(1, 'report')

    assert ctx.transcript_indexed
    assert [t.text for t in ctx.transcripts] == ['indexed chunk text']
