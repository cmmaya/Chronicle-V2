"""BU110: visible-text search, capture hotkey parsing, clipboard dedupe."""
import json
import unittest
from datetime import datetime

from src.screenshots.hotkeys import (
    MOD_ALT,
    MOD_CONTROL,
    MOD_SHIFT,
    MOD_WIN,
    ClipboardDeduper,
    is_reserved_by_windows,
    parse_hotkey,
)
from src.screenshots.viewer_logic import (
    MATCH_END,
    MATCH_START,
    detail_sections,
    mark_terms,
    filter_screenshots,
    match_screenshot,
    missing_visible_text,
    search_terms,
    wrap_index,
)
from src.storage.database import Database

T0 = 1_700_000_000


class HotkeyParseTest(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(parse_hotkey('Ctrl+Alt+S'), (MOD_CONTROL | MOD_ALT, ord('S')))
        self.assertEqual(parse_hotkey(' shift + win + f9 '), (MOD_SHIFT | MOD_WIN, 0x78))
        self.assertEqual(parse_hotkey('Control+PrintScreen'), (MOD_CONTROL, 0x2C))
        self.assertEqual(parse_hotkey('ctrl+alt+7'), (MOD_CONTROL | MOD_ALT, ord('7')))

    def test_invalid(self):
        for spec in ('', 'S', 'Ctrl+Alt', 'Ctrl+S+D', 'Ctrl+Banana', 'Ctrl+F25'):
            with self.assertRaises(ValueError, msg=spec):
                parse_hotkey(spec)

    def test_win_shift_s_is_reserved(self):
        self.assertTrue(is_reserved_by_windows(*parse_hotkey('Win+Shift+S')))
        self.assertFalse(is_reserved_by_windows(*parse_hotkey('Ctrl+Alt+S')))


class ClipboardDeduperTest(unittest.TestCase):
    def test_repeat_within_window_is_duplicate(self):
        dedupe = ClipboardDeduper(window_seconds=5)
        self.assertFalse(dedupe.is_duplicate('a', now=100))
        self.assertTrue(dedupe.is_duplicate('a', now=103))
        self.assertFalse(dedupe.is_duplicate('b', now=103))
        self.assertFalse(dedupe.is_duplicate('a', now=106))  # window passed


def _row(visible=None, **fields):
    row = {'visible_text': json.dumps(visible) if visible is not None else None}
    row.update(fields)
    return row


class MatchTest(unittest.TestCase):
    def test_accent_and_case_insensitive_all_terms(self):
        row = _row(['Reunión de Presupuesto', 'Total: 4M'])
        self.assertTrue(match_screenshot(row, search_terms('reunion presupuesto')))
        self.assertTrue(match_screenshot(row, search_terms('TOTAL')))
        self.assertFalse(match_screenshot(row, search_terms('reunion budget')))
        self.assertFalse(match_screenshot(row, []))

    def test_only_visible_text_is_searched(self):
        row = _row(['Login'], ai_summary='The SSO migration blocks auth',
                   description='sso note', preview_description='sso talk')
        self.assertFalse(match_screenshot(row, search_terms('sso')))
        self.assertTrue(match_screenshot(row, search_terms('login')))

    def test_malformed_json_still_searchable(self):
        self.assertTrue(match_screenshot({'visible_text': 'plain text here'}, ['plain']))

    def test_filter_and_missing(self):
        rows = [_row(['alpha']), _row(['beta']), _row(None), _row([])]
        self.assertEqual(filter_screenshots(rows, 'ALPHA'), [rows[0]])
        self.assertEqual(filter_screenshots(rows * 3, 'alpha', limit=2), [rows[0], rows[0]])
        self.assertEqual(missing_visible_text(rows), 2)

    def test_wrap_index(self):
        self.assertEqual(wrap_index(2, 1, 3), 0)
        self.assertEqual(wrap_index(0, -1, 3), 2)
        self.assertEqual(wrap_index(0, 1, 0), -1)


def _m(text):
    return f'{MATCH_START}{text}{MATCH_END}'


class MarkTermsTest(unittest.TestCase):
    def test_marks_accented_original_text(self):
        self.assertEqual(mark_terms('Reunión anual', ['reunion']), f"{_m('Reunión')} anual")

    def test_every_occurrence_and_overlaps_merge(self):
        self.assertEqual(mark_terms('abcabc', ['abc', 'bc']), _m('abcabc'))
        self.assertEqual(mark_terms('Total total', ['total']), f"{_m('Total')} {_m('total')}")
        self.assertEqual(mark_terms('no hit', ['zzz']), 'no hit')

    def test_details_highlight_only_visible_text(self):
        sections = dict(detail_sections(
            _row(['Total 4M', 'Q3'], ai_summary='total budget'), ['total']))
        self.assertEqual(sections['Visible Text'], f"- {_m('Total')} 4M\n- Q3")
        self.assertEqual(sections['AI Summary'], 'total budget')


class SearchableScreenshotsDbTest(unittest.TestCase):
    def setUp(self):
        self.db = Database(':memory:')
        self.db.connect()
        self.s1 = self.db.create_session('One', datetime.fromtimestamp(T0))
        self.s2 = self.db.create_session('Two', datetime.fromtimestamp(T0))
        rows = [
            (self.s1, T0 + 1, json.dumps(['x']), None),
            (self.s1, T0 + 2, None, 'has a description'),
            (self.s2, T0 + 3, json.dumps(['y']), None),
            (self.s2, T0 + 4, '[]', None),
        ]
        for session_id, ts, visible, desc in rows:
            self.db.connection.execute(
                'INSERT INTO screenshots (session_id, timestamp, filepath, visible_text, description)'
                ' VALUES (?, ?, ?, ?, ?)', (session_id, ts, f'{ts}.png', visible, desc))
        self.db.connection.commit()

    def tearDown(self):
        self.db.disconnect()

    def test_only_rows_with_text_newest_first_with_session_name(self):
        rows = self.db.get_searchable_screenshots()
        self.assertEqual([r['timestamp'] for r in rows], [T0 + 3, T0 + 1])
        self.assertEqual(rows[0]['session_name'], 'Two')

    def test_session_filter_ignores_rows_without_visible_text(self):
        rows = self.db.get_searchable_screenshots(self.s1)
        self.assertEqual([r['timestamp'] for r in rows], [T0 + 1])


if __name__ == '__main__':
    unittest.main()
