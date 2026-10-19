"""BU097 - Keyword search for transcripts and conversations.

DB-level only (no Qt harness, same constraint as BU093/BU095/BU096): exercises
``db.search_transcripts`` multi-keyword AND / wildcard escaping and the new
``db.search_conversations`` title+message union search.
"""
import os
import tempfile
from datetime import datetime

from src.storage.database import Database


def _db(tmpdir):
    db = Database(os.path.join(tmpdir, 'test.db'))
    db.connect()
    return db


def _session(db, name='S'):
    return db.create_session(name, datetime.now())


def _transcript(db, session_id, text, ts):
    """Insert a transcript row with an explicit unix timestamp."""
    cur = db.connection.cursor()
    cur.execute(
        'INSERT INTO transcripts (session_id, timestamp, text, source) VALUES (?, ?, ?, ?)',
        (session_id, ts, text, 'microphone'),
    )
    db.connection.commit()


def _conversation(db, title, updated_at):
    conv_id = db.create_conversation(title=title)
    _touch(db, conv_id, updated_at)
    return conv_id


def _touch(db, conv_id, updated_at):
    """Force a conversation's updated_at (add_message resets it to now)."""
    cur = db.connection.cursor()
    cur.execute(
        'UPDATE assistant_conversations SET updated_at = ? WHERE id = ?',
        (updated_at, conv_id),
    )
    db.connection.commit()


# --------------------------------------------------------------------------
# search_transcripts
# --------------------------------------------------------------------------

def test_search_transcripts_single_and_multi_term_and():
    with tempfile.TemporaryDirectory() as tmp:
        db = _db(tmp)
        sid = _session(db)
        _transcript(db, sid, 'the quick brown fox', 100)
        _transcript(db, sid, 'quick notes only', 101)
        _transcript(db, sid, 'brown bread', 102)

        assert {r['text'] for r in db.search_transcripts('quick')} == {
            'the quick brown fox', 'quick notes only',
        }
        # two terms -> only the row containing BOTH, order irrelevant
        assert [r['text'] for r in db.search_transcripts('brown quick')] == ['the quick brown fox']
        assert [r['text'] for r in db.search_transcripts('quick brown')] == ['the quick brown fox']
        db.disconnect()


def test_search_transcripts_empty_query_is_noop():
    with tempfile.TemporaryDirectory() as tmp:
        db = _db(tmp)
        sid = _session(db)
        _transcript(db, sid, 'hello world', 100)
        assert db.search_transcripts('') == []
        assert db.search_transcripts('   ') == []
        assert db.search_transcripts('\t\n') == []
        db.disconnect()


def test_search_transcripts_session_scope_order_and_limit():
    with tempfile.TemporaryDirectory() as tmp:
        db = _db(tmp)
        a = _session(db, 'A')
        b = _session(db, 'B')
        _transcript(db, a, 'alpha one', 100)
        _transcript(db, a, 'alpha two', 300)
        _transcript(db, a, 'alpha three', 200)
        _transcript(db, b, 'alpha other', 999)

        scoped = db.search_transcripts('alpha', session_id=a)
        assert {r['session_id'] for r in scoped} == {a}
        # newest first
        assert [r['text'] for r in scoped] == ['alpha two', 'alpha three', 'alpha one']
        assert len(db.search_transcripts('alpha', limit=2)) == 2
        db.disconnect()


def test_search_transcripts_wildcards_and_quote_are_literal():
    with tempfile.TemporaryDirectory() as tmp:
        db = _db(tmp)
        sid = _session(db)
        _transcript(db, sid, 'discount is 50% today', 100)
        _transcript(db, sid, 'plain fifty cents', 101)
        _transcript(db, sid, "o'brien said hi", 102)
        _transcript(db, sid, 'snake_case name', 103)
        _transcript(db, sid, 'snakeXcase name', 104)

        assert [r['text'] for r in db.search_transcripts('50%')] == ['discount is 50% today']
        assert [r['text'] for r in db.search_transcripts("o'brien")] == ["o'brien said hi"]
        assert [r['text'] for r in db.search_transcripts('snake_case')] == ['snake_case name']
        # injection attempt is treated as literal text -> no rows
        assert db.search_transcripts("%' OR '1'='1") == []
        db.disconnect()


# --------------------------------------------------------------------------
# search_conversations
# --------------------------------------------------------------------------

def test_search_conversations_matches_message_content():
    with tempfile.TemporaryDirectory() as tmp:
        db = _db(tmp)
        conv = _conversation(db, 'Untitled', 100)
        db.add_message(conv, 'user', 'tell me about kubernetes networking')
        other = _conversation(db, 'Other', 90)
        db.add_message(other, 'user', 'unrelated chatter')

        res = db.search_conversations('kubernetes')
        assert [r['id'] for r in res] == [conv]
        assert res[0]['match_count'] == 1
        assert 'kubernetes' in res[0]['snippet']
        db.disconnect()


def test_search_conversations_matches_title_only():
    with tempfile.TemporaryDirectory() as tmp:
        db = _db(tmp)
        conv = _conversation(db, 'Budget planning', 100)
        db.add_message(conv, 'user', 'no keyword here')

        res = db.search_conversations('budget')
        assert [r['id'] for r in res] == [conv]
        assert res[0]['match_count'] == 0
        assert res[0]['snippet'] == 'Budget planning'
        db.disconnect()


def test_search_conversations_dedupes_and_counts_matches():
    with tempfile.TemporaryDirectory() as tmp:
        db = _db(tmp)
        conv = _conversation(db, 'Chat', 100)
        db.add_message(conv, 'user', 'deadline monday')
        db.add_message(conv, 'assistant', 'the deadline is firm')
        db.add_message(conv, 'user', 'push the deadline?')
        db.add_message(conv, 'assistant', 'nothing relevant')

        res = db.search_conversations('deadline')
        assert len(res) == 1
        assert res[0]['id'] == conv
        assert res[0]['match_count'] == 3
        db.disconnect()


def test_search_conversations_two_term_and_across_title_and_messages():
    with tempfile.TemporaryDirectory() as tmp:
        db = _db(tmp)
        conv = _conversation(db, 'Roadmap review', 100)
        db.add_message(conv, 'user', 'what about the migration timeline')
        miss = _conversation(db, 'Roadmap review', 90)
        db.add_message(miss, 'user', 'no second term present')

        assert [r['id'] for r in db.search_conversations('roadmap migration')] == [conv]
        assert [r['id'] for r in db.search_conversations('migration roadmap')] == [conv]
        db.disconnect()


def test_search_conversations_empty_and_unknown_query():
    with tempfile.TemporaryDirectory() as tmp:
        db = _db(tmp)
        conv = _conversation(db, 'Something', 100)
        db.add_message(conv, 'user', 'content here')
        assert db.search_conversations('') == []
        assert db.search_conversations('   ') == []
        assert db.search_conversations('nonexistentkeyword') == []
        db.disconnect()


def test_search_conversations_order_and_limit():
    with tempfile.TemporaryDirectory() as tmp:
        db = _db(tmp)
        for i, updated in enumerate([100, 300, 200]):
            conv = _conversation(db, f'topic {i}', updated)
            db.add_message(conv, 'user', 'shared keyword apple')
            _touch(db, conv, updated)

        res = db.search_conversations('apple')
        assert [r['updated_at'] for r in res] == [300, 200, 100]
        assert len(db.search_conversations('apple', limit=2)) == 2
        db.disconnect()


def test_search_conversations_injection_is_literal():
    with tempfile.TemporaryDirectory() as tmp:
        db = _db(tmp)
        conv = _conversation(db, 'safe', 100)
        db.add_message(conv, 'user', 'ordinary text')
        assert db.search_conversations("%' OR '1'='1") == []
        db.disconnect()
