"""BU134: sessions.origin flag for inserted transcripts."""
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime

from src.storage.database import (
    SCHEMA_VERSION,
    SESSION_ORIGIN_RECORDED,
    SESSION_ORIGIN_TEXT,
    Database,
)

T0 = 1_700_000_000


class FreshDatabaseTest(unittest.TestCase):
    def setUp(self):
        self.db = Database(':memory:')
        self.db.connect()

    def tearDown(self):
        self.db.disconnect()

    def test_origin_column_defaults_to_recorded(self):
        columns = {row[1]: row for row in self.db.connection.execute('PRAGMA table_info(sessions)')}
        self.assertIn('origin', columns)
        session_id = self.db.create_session('S', datetime.fromtimestamp(T0))
        self.assertEqual(self.db.get_session(session_id)['origin'], SESSION_ORIGIN_RECORDED)
        self.assertEqual(self.db.get_session_origin(session_id), SESSION_ORIGIN_RECORDED)
        self.assertFalse(self.db.is_inserted_transcript(session_id))

    def test_text_origin_round_trips(self):
        session_id = self.db.create_session('T', datetime.fromtimestamp(T0),
                                            origin=SESSION_ORIGIN_TEXT)
        self.assertEqual(self.db.get_session(session_id)['origin'], SESSION_ORIGIN_TEXT)
        self.assertEqual(self.db.get_session_origin(session_id), SESSION_ORIGIN_TEXT)
        self.assertTrue(self.db.is_inserted_transcript(session_id))
        listed = {s['id']: s for s in self.db.list_sessions_with_flags()}
        self.assertEqual(listed[session_id]['origin'], SESSION_ORIGIN_TEXT)

    def test_missing_session_reads_as_recorded(self):
        self.assertEqual(self.db.get_session_origin(9999), SESSION_ORIGIN_RECORDED)
        self.assertFalse(self.db.is_inserted_transcript(9999))


class MigrationTest(unittest.TestCase):
    def test_v3_database_gains_origin_without_data_loss(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'chronicle.db')
            db = Database(path)
            db.connect()
            db.disconnect()

            # Reshape the sessions table to its v3 form (no origin column).
            con = sqlite3.connect(path)
            con.executescript('''
                DROP TABLE sessions;
                CREATE TABLE sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL, start_time INTEGER NOT NULL,
                    end_time INTEGER, status TEXT NOT NULL,
                    transcription_status TEXT DEFAULT 'none',
                    summary_status TEXT DEFAULT 'none',
                    needs_finalize INTEGER NOT NULL DEFAULT 0
                );
                PRAGMA user_version = 3;
            ''')
            con.execute("INSERT INTO sessions (name, start_time, status) VALUES ('old', ?, 'stopped')",
                        (T0,))
            con.commit()
            con.close()

            db = Database(path)
            db.connect()
            try:
                self.assertEqual(
                    db.connection.execute('PRAGMA user_version').fetchone()[0], SCHEMA_VERSION)
                sessions = db.list_sessions_with_flags()
                self.assertEqual(len(sessions), 1)
                self.assertEqual(sessions[0]['name'], 'old')
                self.assertEqual(sessions[0]['origin'], SESSION_ORIGIN_RECORDED)
                self.assertEqual(db.get_session_origin(sessions[0]['id']), SESSION_ORIGIN_RECORDED)
            finally:
                db.disconnect()
            self.assertTrue(any(name.startswith('chronicle.db.bak-v3-') for name in os.listdir(tmp)))


if __name__ == '__main__':
    unittest.main()
