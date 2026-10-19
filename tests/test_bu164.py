"""BU164 - speaker transcript storage next to the original transcript."""
import glob
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime

from src.storage import database as dbmod
from src.storage.database import Database

# Sessions and transcripts as a v5 database has them.
_V5_SCHEMA = '''
    CREATE TABLE sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        start_time INTEGER NOT NULL,
        end_time INTEGER,
        status TEXT NOT NULL,
        transcription_status TEXT DEFAULT 'none',
        summary_status TEXT DEFAULT 'none',
        needs_finalize INTEGER NOT NULL DEFAULT 0,
        origin TEXT NOT NULL DEFAULT 'recorded'
    );
    CREATE TABLE transcripts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id INTEGER NOT NULL,
        timestamp INTEGER NOT NULL,
        text TEXT NOT NULL,
        source TEXT NOT NULL,
        end_timestamp INTEGER,
        audio_file TEXT
    );
    INSERT INTO sessions (name, start_time, status) VALUES ('Old', 1000, 'stopped');
    INSERT INTO transcripts (session_id, timestamp, text, source, audio_file)
        VALUES (1, 1001, 'hello', 'microphone', 'mic/a.wav'),
               (1, 1005, 'world', 'system', 'system/b.wav');
    PRAGMA user_version = 5;
'''


class MigrationTests(unittest.TestCase):
    def test_v5_migrates(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'chronicle.db')
            conn = sqlite3.connect(path)
            conn.executescript(_V5_SCHEMA)
            conn.close()

            db = Database(path)
            db.connect()
            try:
                self.assertEqual(db.connection.execute('PRAGMA user_version').fetchone()[0], 6)
                [session] = db.list_sessions_with_flags()
                self.assertEqual(session['diarization_status'], 'none')
                self.assertIs(session['diarization_enabled'], False)
                self.assertEqual(session['speaker_count'], 0)
                self.assertEqual([t['text'] for t in db.get_transcripts(1)], ['hello', 'world'])
                indexes = {r[0] for r in db.connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'index'")}
                self.assertIn('idx_diarized_transcripts_session', indexes)
            finally:
                db.disconnect()
            self.assertEqual(len(glob.glob(path + '.bak-v5-*')), 1)


class _Db(unittest.TestCase):
    def setUp(self):
        self.db = Database(':memory:')
        self.db.connect()
        self.sid = self.db.create_session('S', datetime(2026, 10, 1, 9, 0))
        self.db.add_transcript(self.sid, datetime(2026, 10, 1, 9, 0, 1), 'original one', 'microphone',
                               audio_file='mic/a.wav')
        self.db.add_transcript(self.sid, datetime(2026, 10, 1, 9, 0, 6), 'original two', 'system')
        t0 = datetime(2026, 10, 1, 9, 0).timestamp()
        self.diarized = [
            {'timestamp': t0 + 1.4, 'end_timestamp': t0 + 4.9, 'text': 'A says', 'source': 'microphone',
             'speaker': 1, 'audio_file': 'mic/a.wav'},
            {'timestamp': datetime(2026, 10, 1, 9, 0, 6), 'text': 'B says', 'source': 'system', 'speaker': 2},
        ]

    def tearDown(self):
        self.db.disconnect()

    def texts(self, rows):
        return [r['text'] for r in rows]


class SwitchTests(_Db):
    def test_switches_only_when_enabled_and_ready(self):
        self.db.replace_diarized_transcripts(self.sid, self.diarized)
        for enabled, status in ((False, 'ready'), (True, 'running'), (True, 'failed'), (True, 'none')):
            self.db.set_diarization(self.sid, enabled=enabled, status=status)
            rows = self.db.get_transcripts(self.sid)
            self.assertEqual(self.texts(rows), ['original one', 'original two'], (enabled, status))
            self.assertNotIn('speaker', rows[0])

        self.db.set_diarization(self.sid, enabled=True, status='ready')
        rows = self.db.get_transcripts(self.sid)
        self.assertEqual(self.texts(rows), ['A says', 'B says'])
        self.assertEqual([r['speaker'] for r in rows], [1, 2])
        self.assertEqual(rows[0]['timestamp'], int(self.diarized[0]['timestamp']))
        self.assertEqual(rows[0]['audio_file'], 'mic/a.wav')
        self.assertEqual(self.db.list_sessions_with_flags()[0]['speaker_count'], 2)

        self.db.set_diarization(self.sid, enabled=False)
        self.assertEqual(self.texts(self.db.get_transcripts(self.sid)), ['original one', 'original two'])

    def test_original_never_switches_or_changes(self):
        before = self.db.get_original_transcripts(self.sid)
        self.db.replace_diarized_transcripts(self.sid, self.diarized)
        self.db.set_diarization(self.sid, enabled=True, status='ready')
        self.assertEqual(self.db.get_original_transcripts(self.sid), before)
        self.db.replace_diarized_transcripts(self.sid, self.diarized[:1])
        self.assertEqual(self.texts(self.db.get_transcripts(self.sid)), ['A says'])
        self.assertEqual(self.db.get_original_transcripts(self.sid), before)

    def test_replace_is_atomic(self):
        self.db.replace_diarized_transcripts(self.sid, self.diarized)
        bad = [dict(self.diarized[0]), dict(self.diarized[1], text=None)]  # NOT NULL fails
        with self.assertRaises(dbmod.DatabaseError):
            self.db.replace_diarized_transcripts(self.sid, bad)
        self.db.set_diarization(self.sid, enabled=True, status='ready')
        self.assertEqual(self.texts(self.db.get_transcripts(self.sid)), ['A says', 'B says'])

    def test_unknown_status_rejected(self):
        with self.assertRaises(ValueError):
            self.db.set_diarization(self.sid, status='done')


class DeleteTests(_Db):
    def count(self):
        return self.db.connection.execute(
            'SELECT COUNT(*) FROM diarized_transcripts WHERE session_id = ?', (self.sid,)).fetchone()[0]

    def test_delete_session_removes_diarized_rows(self):
        self.db.replace_diarized_transcripts(self.sid, self.diarized)
        self.db.delete_session(self.sid)
        self.assertEqual(self.count(), 0)

    def test_purge_session_removes_diarized_rows(self):
        self.db.replace_diarized_transcripts(self.sid, self.diarized)
        self.db.purge_session(self.sid)
        self.assertEqual(self.count(), 0)

    def test_retranscription_clears_diarization(self):
        self.db.replace_diarized_transcripts(self.sid, self.diarized)
        self.db.set_diarization(self.sid, enabled=True, status='ready')
        self.db.delete_transcripts(self.sid)
        self.assertEqual(self.count(), 0)
        session = self.db.get_session(self.sid)
        self.assertEqual(session['diarization_status'], 'none')
        self.assertEqual(session['diarization_enabled'], 1)
        self.assertEqual(self.db.get_transcripts(self.sid), [])


if __name__ == '__main__':
    unittest.main()
