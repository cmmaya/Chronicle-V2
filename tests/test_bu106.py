"""BU106: preliminary screenshot descriptions and vision-metadata parsing."""
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime

from src.screenshots.context_generator import ScreenshotContextGenerator
from src.screenshots.metadata import (
    PREVIEW_MAX_CHARS,
    build_preliminary_description,
    capture_time_label,
    ensure_previews,
    transcript_window,
)
from src.storage.database import Database

T0 = 1_700_000_000


def _t(offset, text, end=None, id_=None):
    row = {'timestamp': T0 + offset, 'text': text}
    if end is not None:
        row['end_timestamp'] = T0 + end
    if id_ is not None:
        row['id'] = id_
    return row


class TranscriptWindowTest(unittest.TestCase):
    def test_keeps_only_rows_inside_window_in_order(self):
        rows = [
            _t(-60, 'way before'),
            _t(-15, 'first inside'),
            _t(5, 'second inside'),
            _t(19, 'third inside'),
            _t(45, 'way after'),
        ]
        self.assertEqual(
            transcript_window(rows, T0),
            'first inside second inside third inside',
        )

    def test_row_spanning_the_window_edge_counts(self):
        rows = [_t(-40, 'long utterance', end=-18)]
        self.assertEqual(transcript_window(rows, T0), 'long utterance')

    def test_falls_back_to_two_nearest_rows(self):
        rows = [_t(-300, 'far'), _t(-90, 'near before'), _t(100, 'near after')]
        self.assertEqual(transcript_window(rows, T0), 'near before near after')

    def test_max_chars_prefers_rows_closest_to_capture(self):
        rows = [_t(-18, 'a' * 50), _t(0, 'b' * 50), _t(18, 'c' * 50)]
        text = transcript_window(rows, T0, max_chars=60)
        self.assertIn('b' * 50, text)
        self.assertLessEqual(len(text), 60)

    def test_empty_and_blank_rows(self):
        self.assertEqual(transcript_window([], T0), '')
        self.assertEqual(transcript_window([_t(0, '   ')], T0), '')


class PreliminaryDescriptionTest(unittest.TestCase):
    def test_description_then_speech_then_time(self):
        row = {'timestamp': T0 + 90, 'description': 'Release board'}
        text = build_preliminary_description(
            row, [_t(85, 'we are blocked on auth')], session_start=T0
        )
        self.assertTrue(text.startswith('Release board · Discussed: we are blocked on auth'))
        self.assertTrue(text.endswith('(+00:01:30)'))

    def test_never_exceeds_budget(self):
        row = {'timestamp': T0, 'description': 'x ' * 300}
        text = build_preliminary_description(row, [_t(0, 'y ' * 800)], session_start=T0)
        self.assertLessEqual(len(text), PREVIEW_MAX_CHARS)

    def test_placeholder_when_nothing_is_known(self):
        text = build_preliminary_description({'timestamp': T0}, [])
        self.assertIn('no description or nearby speech', text)

    def test_time_label_without_session_start(self):
        label = capture_time_label(T0)
        self.assertRegex(label, r'^\d\d:\d\d:\d\d$')
        self.assertEqual(capture_time_label(None), '')


class ParseResponseTest(unittest.TestCase):
    def setUp(self):
        self.gen = ScreenshotContextGenerator(database=object())

    def test_full_json(self):
        parsed = self.gen._parse_response(
            '{"short_description": "Jira board", "summary": "s",'
            ' "visible_text": ["A, B", "C"], "keywords": ["jira"]}'
        )
        self.assertEqual(parsed['short_description'], 'Jira board')
        self.assertEqual(parsed['visible_text'], ['A, B', 'C'])
        self.assertEqual(parsed['keywords'], ['jira'])

    def test_missing_fields_and_wrong_types_are_normalized(self):
        parsed = self.gen._parse_response(
            'Here you go: {"summary": null, "visible_text": "line one\\nline two",'
            ' "keywords": "a, b ,, c"}'
        )
        self.assertEqual(parsed['short_description'], '')
        self.assertEqual(parsed['summary'], '')
        self.assertEqual(parsed['visible_text'], ['line one', 'line two'])
        self.assertEqual(parsed['keywords'], ['a', 'b', 'c'])

    def test_unparseable_reply_kept_as_summary(self):
        parsed = self.gen._parse_response('no json at all')
        self.assertEqual(parsed['summary'], 'no json at all')
        self.assertEqual(parsed['visible_text'], [])
        self.assertEqual(set(parsed), {'short_description', 'summary', 'visible_text', 'keywords'})


class _CountingDb(Database):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.preview_writes = 0

    def update_screenshot_preview(self, screenshot_id, text, source):
        self.preview_writes += 1
        super().update_screenshot_preview(screenshot_id, text, source)


class EnsurePreviewsTest(unittest.TestCase):
    def setUp(self):
        self.db = _CountingDb(':memory:')
        self.db.connect()
        self.session_id = self.db.create_session('S', datetime.fromtimestamp(T0))
        cur = self.db.connection.cursor()
        for ts, desc in ((T0 + 10, 'Login page'), (T0 + 100, None)):
            cur.execute(
                'INSERT INTO screenshots (session_id, timestamp, filepath, description)'
                ' VALUES (?, ?, ?, ?)',
                (self.session_id, ts, f'/s/{ts}.png', desc),
            )
        self.db.connection.commit()
        self.db.add_transcript(self.session_id, datetime.fromtimestamp(T0 + 8),
                               'the login fails with a 500', 'microphone')

    def tearDown(self):
        self.db.disconnect()

    def _previews(self):
        return {r['id']: r for r in self.db.get_screenshot_previews(self.session_id)}

    def test_every_row_gets_an_auto_preview(self):
        rows = ensure_previews(self.db, self.session_id)
        self.assertEqual(len(rows), 2)
        for row in self._previews().values():
            self.assertTrue(row['preview_description'])
            self.assertEqual(row['preview_source'], 'auto')
        first = rows[0]['preview_description']
        self.assertIn('Login page', first)
        self.assertIn('login fails', first)

    def test_unchanged_rows_are_not_rewritten(self):
        ensure_previews(self.db, self.session_id)
        writes = self.db.preview_writes
        ensure_previews(self.db, self.session_id)
        self.assertEqual(self.db.preview_writes, writes)

    def test_auto_preview_refreshes_when_speech_arrives(self):
        ensure_previews(self.db, self.session_id)
        second_id = max(self._previews())
        before = self._previews()[second_id]['preview_description']
        self.db.add_transcript(self.session_id, datetime.fromtimestamp(T0 + 105),
                               'now the dashboard loads', 'system')
        ensure_previews(self.db, self.session_id)
        after = self._previews()[second_id]['preview_description']
        self.assertNotEqual(before, after)
        self.assertIn('dashboard loads', after)

    def test_ai_preview_is_never_overwritten(self):
        first_id = min(self._previews())
        self.db.update_screenshot_preview(first_id, 'Model description', 'ai')
        ensure_previews(self.db, self.session_id)
        row = self._previews()[first_id]
        self.assertEqual(row['preview_description'], 'Model description')
        self.assertEqual(row['preview_source'], 'ai')


class MigrationTest(unittest.TestCase):
    def test_v2_database_gains_preview_columns_without_data_loss(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'chronicle.db')
            db = Database(path)
            db.connect()
            session_id = db.create_session('S', datetime.fromtimestamp(T0))
            db.disconnect()

            # Reshape the screenshots table to its v2 form (no preview columns).
            con = sqlite3.connect(path)
            con.executescript('''
                DROP TABLE screenshots;
                CREATE TABLE screenshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id INTEGER NOT NULL, timestamp INTEGER NOT NULL,
                    filepath TEXT NOT NULL, description TEXT, ai_summary TEXT,
                    visible_text TEXT, keywords TEXT
                );
                PRAGMA user_version = 2;
            ''')
            con.execute(
                "INSERT INTO screenshots (session_id, timestamp, filepath, ai_summary)"
                " VALUES (?, ?, 'a.png', 'kept')", (session_id, T0),
            )
            con.commit()
            con.close()

            db = Database(path)
            db.connect()
            rows = db.get_screenshots(session_id)
            db.disconnect()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]['ai_summary'], 'kept')
            self.assertIn('preview_description', rows[0])
            self.assertIn('preview_source', rows[0])


if __name__ == '__main__':
    unittest.main()
