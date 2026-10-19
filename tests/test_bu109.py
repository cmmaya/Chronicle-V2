"""BU109: Qt-free logic behind the screenshot viewer."""
import json
import os
import unittest

from src.screenshots.viewer_logic import (
    MAX_ZOOM,
    MIN_ZOOM,
    can_generate_context,
    capture_meta,
    clamp_index,
    context_state_label,
    description_file_for,
    detail_sections,
    help_sections,
    index_after_delete,
    next_zoom,
    step_index,
)

T0 = 1_700_000_000


class NavigationTest(unittest.TestCase):
    def test_step_clamps_without_wrapping(self):
        self.assertEqual(step_index(0, -1, 5), 0)
        self.assertEqual(step_index(4, 1, 5), 4)
        self.assertEqual(step_index(2, 1, 5), 3)
        self.assertEqual(step_index(0, 1, 0), -1)

    def test_clamp_index(self):
        self.assertEqual(clamp_index(9, 3), 2)
        self.assertEqual(clamp_index(-4, 3), 0)

    def test_selection_after_delete(self):
        self.assertEqual(index_after_delete(1, 2), 1)   # the next item slid in
        self.assertEqual(index_after_delete(2, 2), 1)   # deleted the last one
        self.assertEqual(index_after_delete(0, 0), -1)  # nothing left


class ZoomTest(unittest.TestCase):
    def test_steps_and_clamps(self):
        self.assertAlmostEqual(next_zoom(1.0, 1), 1.25)
        self.assertAlmostEqual(next_zoom(1.25, -1), 1.0)
        self.assertEqual(next_zoom(MAX_ZOOM, 1), MAX_ZOOM)
        self.assertEqual(next_zoom(MIN_ZOOM, -1), MIN_ZOOM)


class DetailsTest(unittest.TestCase):
    def test_sections_with_ai_context(self):
        row = {
            'description': 'Release board',
            'preview_description': 'Jira board, blocked tickets',
            'ai_summary': 'Board with AUTH-142 blocked',
            'visible_text': json.dumps(['AUTH-142', 'Sprint 14']),
            'keywords': json.dumps(['jira', 'auth']),
        }
        sections = dict(detail_sections(row))
        self.assertEqual([t for t, _ in detail_sections(row)],
                         ['Description', 'Preview', 'AI Summary', 'Visible Text', 'Keywords'])
        self.assertEqual(sections['Visible Text'], '- AUTH-142\n- Sprint 14')
        self.assertEqual(sections['Keywords'], 'jira, auth')
        self.assertEqual(context_state_label(row), 'AI context')

    def test_empty_sections_carry_hints(self):
        sections = dict(detail_sections({'visible_text': 'not json'}))
        self.assertIn('Edit', sections['Description'])
        self.assertIn('Generate context', sections['AI Summary'])
        self.assertEqual(sections['Visible Text'], '- not json')
        self.assertEqual(context_state_label({}), 'Preview only')

    def test_capture_meta(self):
        self.assertTrue(capture_meta({'timestamp': T0 + 75}, T0).endswith('(+00:01:15)'))
        self.assertEqual(capture_meta({}), 'unknown time')


class HelpTest(unittest.TestCase):
    def test_help_covers_every_control_and_uses_configured_hotkey(self):
        sections = help_sections('Ctrl+Alt+K')
        text = '\n'.join(body for _, body in sections)
        for control in ('Double-click the image', 'Esc', 'Generate context',
                        'Generate missing', 'Open folder', 'Delete', 'Edit',
                        'Ctrl+F', '#42', 'Preview only', 'View screenshot #N'):
            self.assertIn(control, text)
        self.assertIn('**Ctrl+Alt+K**', text)
        # Every line is a bullet, so the collapsible sections render them as a list.
        for _, body in sections:
            self.assertTrue(all(line.startswith('- ') for line in body.split('\n')))


class MiscTest(unittest.TestCase):
    def test_generate_needs_summary_or_transcript(self):
        self.assertTrue(can_generate_context(False, True))
        self.assertTrue(can_generate_context(True, False))
        self.assertFalse(can_generate_context(False, False))

    def test_description_sidecar_path(self):
        path = os.path.join('s', 'screenshots', 'Standup_20260913_101500.png')
        self.assertEqual(
            description_file_for(path),
            os.path.join('s', 'screenshots', 'description_Standup_20260913_101500.txt'),
        )


if __name__ == '__main__':
    unittest.main()
