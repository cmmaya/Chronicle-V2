"""Tests for the perf-rework changes to src/storage/database.py:
per-thread connections + WAL, versioned migration with an automatic backup,
startup session repair, and orphan cleanup.
"""
import glob
import os
import sqlite3
import tempfile
import threading
import unittest
from datetime import datetime

from src.storage.database import Database, SCHEMA_VERSION


def _make_v0_database(path: str) -> None:
    """Build a database shaped like the pre-migration schema (no
    schema_version tracking, missing columns migrations later add, an
    orphaned RAG row, and a session left 'active' by a crash)."""
    con = sqlite3.connect(path)
    cur = con.cursor()
    cur.execute('''CREATE TABLE sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
        start_time INTEGER NOT NULL, end_time INTEGER, status TEXT NOT NULL,
        transcription_status TEXT DEFAULT 'none', summary_status TEXT DEFAULT 'none'
    )''')
    cur.execute('''CREATE TABLE transcripts (
        id INTEGER PRIMARY KEY AUTOINCREMENT, session_id INTEGER NOT NULL,
        timestamp INTEGER NOT NULL, text TEXT NOT NULL, source TEXT NOT NULL
    )''')
    cur.execute('''CREATE TABLE screenshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT, session_id INTEGER NOT NULL,
        timestamp INTEGER NOT NULL, filepath TEXT NOT NULL, description TEXT
    )''')
    cur.execute('''CREATE TABLE summaries (
        id INTEGER PRIMARY KEY AUTOINCREMENT, session_id INTEGER NOT NULL,
        summary_type TEXT NOT NULL, content TEXT NOT NULL, model_used TEXT NOT NULL,
        created_at INTEGER NOT NULL
    )''')
    cur.execute('''CREATE TABLE assistant_conversations (
        id INTEGER PRIMARY KEY AUTOINCREMENT, session_id INTEGER, title TEXT,
        created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
    )''')
    cur.execute('''CREATE TABLE assistant_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id INTEGER NOT NULL,
        role TEXT NOT NULL, content TEXT NOT NULL, timestamp INTEGER NOT NULL
    )''')
    cur.execute('''CREATE TABLE rag_documents (
        id INTEGER PRIMARY KEY AUTOINCREMENT, source_type TEXT NOT NULL,
        source_id INTEGER NOT NULL, session_id INTEGER, timestamp INTEGER, title TEXT,
        content_hash TEXT, metadata_json TEXT, created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL
    )''')
    cur.execute('''CREATE TABLE rag_chunks (
        id INTEGER PRIMARY KEY AUTOINCREMENT, document_id INTEGER NOT NULL,
        session_id INTEGER, chunk_index INTEGER NOT NULL, content TEXT NOT NULL,
        token_count INTEGER, start_timestamp INTEGER, end_timestamp INTEGER,
        metadata_json TEXT, embedding_model TEXT, embedding BLOB, created_at INTEGER NOT NULL
    )''')
    cur.execute('''CREATE VIRTUAL TABLE rag_fts USING fts5(
        content, source_type UNINDEXED, session_id UNINDEXED,
        document_id UNINDEXED, chunk_id UNINDEXED
    )''')
    cur.execute('''CREATE TABLE session_profiles (
        session_id INTEGER PRIMARY KEY, profile_text TEXT NOT NULL, keywords TEXT,
        embedding BLOB, embedding_model TEXT, content_hash TEXT, updated_at INTEGER NOT NULL
    )''')
    cur.execute('''CREATE VIRTUAL TABLE session_profiles_fts USING fts5(
        profile_text, session_id UNINDEXED
    )''')

    now = int(datetime.now().timestamp())
    # A session interrupted mid-recording (app closed/crashed while active).
    cur.execute("INSERT INTO sessions (id, name, start_time, status) VALUES (1, 'Live', ?, 'active')", (now,))
    # A normal, finished session.
    cur.execute("INSERT INTO sessions (id, name, start_time, status) VALUES (2, 'Done', ?, 'stopped')", (now,))
    cur.execute("INSERT INTO transcripts (session_id, timestamp, text, source) VALUES (2, ?, 'hello', 'microphone')", (now,))
    # A RAG document + chunk + FTS row left over from a session deleted before
    # purge_session existed (session_id 999 does not exist).
    cur.execute("""INSERT INTO rag_documents (id, source_type, source_id, session_id, timestamp,
                   title, content_hash, metadata_json, created_at, updated_at)
                   VALUES (1, 'transcript', 999, 999, ?, 'x', 'h', '{}', ?, ?)""", (now, now, now))
    cur.execute("""INSERT INTO rag_chunks (id, document_id, session_id, chunk_index, content,
                   created_at) VALUES (1, 1, 999, 0, 'orphaned content', ?)""", (now,))
    cur.execute("INSERT INTO rag_fts (rowid, content, source_type, session_id, document_id, chunk_id) "
                "VALUES (1, 'orphaned content', 'transcript', 999, 1, 1)")
    con.commit()
    con.close()


class TestMigrationFromV0(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.db')
        self.tmp.close()
        _make_v0_database(self.tmp.name)

    def tearDown(self):
        for path in glob.glob(self.tmp.name + '*'):
            try:
                os.unlink(path)
            except OSError:
                pass

    def test_migration_adds_missing_columns_and_bumps_version(self):
        db = Database(self.tmp.name)
        db.connect()
        try:
            self.assertEqual(
                db.connection.execute('PRAGMA user_version').fetchone()[0], SCHEMA_VERSION
            )
            transcript_cols = {r[1] for r in db.connection.execute('PRAGMA table_info(transcripts)')}
            self.assertIn('end_timestamp', transcript_cols)
            self.assertIn('audio_file', transcript_cols)
            session_cols = {r[1] for r in db.connection.execute('PRAGMA table_info(sessions)')}
            self.assertIn('needs_finalize', session_cols)
            chunk_cols = {r[1] for r in db.connection.execute('PRAGMA table_info(rag_chunks)')}
            self.assertIn('source', chunk_cols)
        finally:
            db.disconnect()

    def test_migration_switches_to_wal_and_backs_up_first(self):
        db = Database(self.tmp.name)
        db.connect()
        try:
            self.assertEqual(db.connection.execute('PRAGMA journal_mode').fetchone()[0], 'wal')
        finally:
            db.disconnect()
        backups = glob.glob(self.tmp.name + '.bak-v0-*')
        self.assertEqual(len(backups), 1)
        # The backup is a readable, complete copy of the pre-migration data.
        backup_con = sqlite3.connect(backups[0])
        self.assertEqual(
            backup_con.execute("SELECT name FROM sessions WHERE id = 2").fetchone()[0], 'Done'
        )
        backup_con.close()

    def test_migration_preserves_existing_rows(self):
        db = Database(self.tmp.name)
        db.connect()
        try:
            session = db.get_session(2)
            self.assertEqual(session['name'], 'Done')
            transcripts = db.get_transcripts(2)
            self.assertEqual(len(transcripts), 1)
            self.assertEqual(transcripts[0]['text'], 'hello')
        finally:
            db.disconnect()

    def test_migration_repairs_a_session_left_active(self):
        db = Database(self.tmp.name)
        db.connect()
        try:
            repaired = db.repair_interrupted_sessions()
            self.assertEqual(repaired, [1])
            session = db.get_session(1)
            self.assertEqual(session['status'], 'stopped')
            self.assertEqual(session['needs_finalize'], 1)
            # The already-finished session must be untouched.
            self.assertEqual(db.get_session(2)['status'], 'stopped')
            self.assertEqual(db.get_session(2)['needs_finalize'], 0)
        finally:
            db.disconnect()

    def test_migration_purges_rows_of_a_session_that_no_longer_exists(self):
        """The orphaned rag_documents/rag_chunks/rag_fts rows (session_id 999,
        which was never a real session) must be gone; session 2's real chunk
        would survive the same sweep (none exists here, so this only checks
        removal, covered together with the "keeps valid rows" test below)."""
        db = Database(self.tmp.name)
        db.connect()
        try:
            self.assertEqual(
                db.connection.execute('SELECT COUNT(*) FROM rag_documents').fetchone()[0], 0
            )
            self.assertEqual(
                db.connection.execute('SELECT COUNT(*) FROM rag_chunks').fetchone()[0], 0
            )
            self.assertEqual(
                db.connection.execute('SELECT COUNT(*) FROM rag_fts').fetchone()[0], 0
            )
        finally:
            db.disconnect()

    def test_reconnecting_an_already_migrated_database_is_a_no_op(self):
        db = Database(self.tmp.name)
        db.connect()
        db.disconnect()
        before = set(glob.glob(self.tmp.name + '.bak-*'))

        db2 = Database(self.tmp.name)
        db2.connect()
        db2.disconnect()

        after = set(glob.glob(self.tmp.name + '.bak-*'))
        self.assertEqual(before, after, 'a second connect() must not re-migrate or re-backup')


class TestPurgeOrphansIsSelective(unittest.TestCase):
    """purge_orphans() must remove only rows of sessions that don't exist,
    never rows belonging to a real session."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.db')
        self.tmp.close()
        self.db = Database(self.tmp.name)
        self.db.connect()

    def tearDown(self):
        self.db.disconnect()
        for path in glob.glob(self.tmp.name + '*'):
            try:
                os.unlink(path)
            except OSError:
                pass

    def test_keeps_rows_of_existing_sessions_and_drops_the_rest(self):
        real_session = self.db.create_session('Real', datetime.now(), status='stopped')
        self.db.add_transcript(real_session, datetime.now(), 'kept text', 'microphone')
        doc_id = self.db.upsert_rag_document(
            source_type='transcript', source_id=real_session, session_id=real_session,
            timestamp=0, title='t', content_hash='h', metadata_json='{}',
        )
        self.db.replace_rag_chunks(doc_id, [{'content': 'kept chunk', 'chunk_index': 0}])

        # A conversation and a screenshot tied to a session that was deleted
        # without purge_session (session_id 999 was never created here).
        self.db.create_conversation(session_id=999, title='orphan')
        cur = self.db.connection.cursor()
        cur.execute(
            "INSERT INTO screenshots (session_id, timestamp, filepath) VALUES (999, 0, 'x.png')"
        )
        self.db.connection.commit()

        removed = self.db.purge_orphans()

        self.assertEqual(removed['assistant_conversations'], 1)
        self.assertEqual(removed['screenshots'], 1)
        # The real session's own data survives untouched.
        self.assertEqual(len(self.db.get_transcripts(real_session)), 1)
        hits = self.db.search_rag_fts('kept', limit=5)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]['session_id'], real_session)

    def test_is_idempotent(self):
        self.db.create_conversation(session_id=999, title='orphan')
        first = self.db.purge_orphans()
        second = self.db.purge_orphans()
        self.assertGreater(first['assistant_conversations'], 0)
        self.assertEqual(sum(second.values()), 0)


class TestPerThreadConnections(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.db')
        self.tmp.close()
        self.db = Database(self.tmp.name)
        self.db.connect()

    def tearDown(self):
        self.db.disconnect()
        for path in glob.glob(self.tmp.name + '*'):
            try:
                os.unlink(path)
            except OSError:
                pass

    def test_same_thread_reuses_one_connection(self):
        self.assertIs(self.db.connection, self.db.connection)

    def test_a_write_from_the_main_thread_is_visible_from_another_thread(self):
        session_id = self.db.create_session('S', datetime.now(), status='stopped')

        seen = {}

        def read_from_thread():
            row = self.db.get_session(session_id)
            seen['name'] = row['name'] if row else None
            seen['is_different_connection'] = self.db.connection is not self.db.connection

        t = threading.Thread(target=read_from_thread)
        t.start()
        t.join(timeout=5)

        self.assertEqual(seen.get('name'), 'S')

    def test_release_thread_connection_lets_a_dead_thread_be_reclaimed(self):
        result = {}

        def worker():
            self.db.connection.execute('SELECT 1')
            result['before'] = len(self.db._connections)
            self.db.release_thread_connection()
            result['after'] = len(self.db._connections)

        t = threading.Thread(target=worker)
        t.start()
        t.join(timeout=5)

        self.assertEqual(result['after'], result['before'] - 1)

    def test_disconnect_closes_connections_opened_by_other_threads(self):
        opened = threading.Event()
        keep_alive = threading.Event()

        def worker():
            self.db.connection.execute('SELECT 1')
            opened.set()
            keep_alive.wait(timeout=5)

        t = threading.Thread(target=worker)
        t.start()
        try:
            self.assertTrue(opened.wait(timeout=5))
            self.assertGreaterEqual(len(self.db._connections), 2)
            self.db.disconnect()
            self.assertEqual(self.db._connections, [])
        finally:
            keep_alive.set()
            t.join(timeout=5)
        # Re-open for tearDown's disconnect() to have something to close.
        self.db.connect()


class TestListSessionsWithFlags(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.db')
        self.tmp.close()
        self.db = Database(self.tmp.name)
        self.db.connect()

    def tearDown(self):
        self.db.disconnect()
        for path in glob.glob(self.tmp.name + '*'):
            try:
                os.unlink(path)
            except OSError:
                pass

    def test_flags_reflect_actual_transcripts_and_summaries(self):
        empty = self.db.create_session('Empty', datetime.now(), status='stopped')
        full = self.db.create_session('Full', datetime.now(), status='stopped')
        self.db.add_transcript(full, datetime.now(), 'hi', 'microphone')
        self.db.add_summary(full, 'full', 'a summary', 'test-model')

        rows = {r['id']: r for r in self.db.list_sessions_with_flags()}

        self.assertFalse(rows[empty]['has_transcripts'])
        self.assertFalse(rows[empty]['has_summary'])
        self.assertTrue(rows[full]['has_transcripts'])
        self.assertTrue(rows[full]['has_summary'])


if __name__ == '__main__':
    unittest.main()
