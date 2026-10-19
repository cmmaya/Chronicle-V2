"""BU112 - detached window two-column layout and transcript polish.

Qt runs offscreen so the column builders and the stream exercise the real
widgets. See tests/detached_harness.py for how a MainWindow is stood up
without its database and session manager.
"""
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QComboBox, QLabel

from src.app.pixel_widgets import PixelBubble, PixelSectionTitle, PixelToolButton
from src.app.window import (
    DETACHED_SCROLL_PIXELS_PER_STEP,
    TRANSCRIPT_GAP_SEPARATOR_SECONDS,
    gap_separator_label,
)
from tests.detached_harness import T0, DetachedHarness, app

_app = app()


class GapSeparatorLabelTest(unittest.TestCase):
    def test_short_gap_gets_no_separator(self):
        self.assertIsNone(gap_separator_label(T0, T0 + timedelta(seconds=30)))

    def test_gap_exactly_at_threshold_gets_no_separator(self):
        later = T0 + timedelta(seconds=TRANSCRIPT_GAP_SEPARATOR_SECONDS)
        self.assertIsNone(gap_separator_label(T0, later))

    def test_gap_just_over_threshold_gets_a_separator(self):
        later = T0 + timedelta(seconds=TRANSCRIPT_GAP_SEPARATOR_SECONDS + 1)
        self.assertEqual(gap_separator_label(T0, later), later.strftime("%H:%M"))

    def test_missing_timestamps_get_no_separator(self):
        self.assertIsNone(gap_separator_label(None, T0))
        self.assertIsNone(gap_separator_label(T0, None))

    def test_crossing_midnight_shows_the_date(self):
        later = T0 + timedelta(days=1)
        self.assertEqual(gap_separator_label(T0, later), later.strftime("%b %d - %H:%M"))


class PreferenceRoundTripTest(unittest.TestCase):
    """_read_preferences / _update_preferences keep unrelated keys intact."""

    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        os.unlink(self.path)
        self.h = DetachedHarness(prefs_path=self.path)

    def tearDown(self):
        if os.path.exists(self.path):
            os.unlink(self.path)

    def test_missing_file_reads_as_empty(self):
        self.assertEqual(self.h._read_preferences(), {})

    def test_unreadable_file_reads_as_empty(self):
        with open(self.path, "w") as fh:
            fh.write("{not json")
        self.assertEqual(self.h._read_preferences(), {})

    def test_layout_round_trips_without_clobbering_other_keys(self):
        with open(self.path, "w") as fh:
            json.dump({"enable_live_transcription": False}, fh)

        self.h._update_preferences({
            "detached_splitter_sizes": [400, 640],
            "detached_window_geometry": [10, 20, 1040, 620],
        })

        prefs = self.h._read_preferences()
        self.assertEqual(prefs["detached_splitter_sizes"], [400, 640])
        self.assertEqual(prefs["detached_window_geometry"], [10, 20, 1040, 620])
        self.assertIs(prefs["enable_live_transcription"], False)


class TranscriptColumnTest(unittest.TestCase):
    def setUp(self):
        self.h = DetachedHarness()
        self.column = self.h.build_transcript_column()

    def test_filter_control_is_parented_to_the_column_header(self):
        self.assertIsNotNone(self.h._detached_filter_button)
        self.assertIn(self.h._detached_filter_button, self.column.findChildren(PixelToolButton))
        self.assertIn(self.h._detached_filter_combo, self.column.findChildren(QComboBox))

    def test_wheel_step_is_a_fixed_pixel_distance(self):
        area = self.h._detached_scroll_area
        self.assertEqual(area.verticalScrollBar().singleStep(),
                         DETACHED_SCROLL_PIXELS_PER_STEP)

    def test_answers_column_uses_the_same_wheel_step(self):
        self.h.build_answers_column()
        self.assertEqual(
            self.h._detached_answers_scroll_area.verticalScrollBar().singleStep(),
            DETACHED_SCROLL_PIXELS_PER_STEP,
        )

    def test_no_horizontal_scrollbar(self):
        from PySide6.QtCore import Qt
        self.assertEqual(self.h._detached_scroll_area.horizontalScrollBarPolicy(),
                         Qt.ScrollBarAlwaysOff)

    def test_bubble_text_is_selectable(self):
        from PySide6.QtCore import Qt
        self.h.add("hello", "mic", 0)
        bubble = self.h.rows()[0].findChild(PixelBubble)
        self.assertTrue(bubble.label.textInteractionFlags() & Qt.TextSelectableByMouse)


class GapSeparatorInsertionTest(unittest.TestCase):
    def setUp(self):
        self.h = DetachedHarness()
        self.h.build_transcript_column()

    def test_no_separator_before_the_first_bubble(self):
        self.h.add("first", "mic", 0)
        self.assertEqual(self.h.separators(), [])

    def test_no_separator_for_a_short_break(self):
        self.h.add("first", "mic", 0)
        self.h.add("second", "mic", 60)
        self.assertEqual(self.h.separators(), [])

    def test_separator_for_a_long_silence(self):
        self.h.add("before", "mic", 0)
        self.h.add("after", "mic", 5 + TRANSCRIPT_GAP_SEPARATOR_SECONDS + 1)

        rows = self.h.rows()
        self.assertEqual(len(rows), 3)
        self.assertIsNone(rows[1].property("record_indices"))
        self.assertEqual(rows[0].property("record_indices"), [0])
        self.assertEqual(rows[2].property("record_indices"), [1])

    def test_separator_boundary_is_exclusive(self):
        self.h.add("before", "mic", 0)
        self.h.add("after", "mic", 5 + TRANSCRIPT_GAP_SEPARATOR_SECONDS)
        self.assertEqual(self.h.separators(), [])

    def test_gap_is_measured_across_sources_not_within_one(self):
        # Mic talks, then system talks much later: the break belongs to the
        # stream, so the system bubble sits below a separator.
        self.h.add("mic line", "mic", 0)
        self.h.add("system line", "system", 5 + TRANSCRIPT_GAP_SEPARATOR_SECONDS + 1)
        self.assertEqual(len(self.h.separators()), 1)

    def test_separator_closes_open_groups(self):
        self.h.add("before", "mic", 0)
        after = 5 + TRANSCRIPT_GAP_SEPARATOR_SECONDS + 1
        self.h.add("after", "mic", after)
        self.h.add("more", "mic", after + 5)

        rows = self.h.rows()
        # before | separator | after+more grouped
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[2].property("record_indices"), [1, 2])

    def test_separators_hide_under_a_source_filter(self):
        self.h.add("before", "mic", 0)
        self.h.add("after", "mic", 5 + TRANSCRIPT_GAP_SEPARATOR_SECONDS + 1)

        self.h._detached_filter_combo.setCurrentIndex(1)  # Mic
        self.h._update_detached_filter()

        separator = self.h.separators()[0]
        self.assertFalse(separator.isVisibleTo(self.h._detached_container))

    def test_separators_return_under_the_all_filter(self):
        self.h.add("before", "mic", 0)
        self.h.add("after", "mic", 5 + TRANSCRIPT_GAP_SEPARATOR_SECONDS + 1)

        self.h._detached_filter_combo.setCurrentIndex(1)
        self.h._update_detached_filter()
        self.h._detached_filter_combo.setCurrentIndex(0)
        self.h._update_detached_filter()

        separator = self.h.separators()[0]
        self.assertTrue(separator.isVisibleTo(self.h._detached_container))


class AnswersColumnTest(unittest.TestCase):
    def setUp(self):
        self.h = DetachedHarness()
        self.column = self.h.build_answers_column()

    def test_column_is_titled(self):
        titles = [w.text() for w in self.column.findChildren(PixelSectionTitle)]
        self.assertIn("ANSWERS", titles)

    def test_the_column_cannot_be_narrower_than_its_controls(self):
        # A fixed minimum used to let the splitter clip the mode chips.
        self.assertEqual(self.column.minimumWidth(), 0)
        chips = list(self.h._live_qa_mode_chips.values())
        needed = sum(c.minimumSizeHint().width() for c in chips)
        self.assertGreater(self.column.minimumSizeHint().width(), needed)

    def test_empty_state_hint_is_present(self):
        self.assertIsInstance(self.h._detached_answers_empty, QLabel)
        self.assertIn("right-click", self.h._detached_answers_empty.text())


if __name__ == "__main__":
    unittest.main()


class DetachedFindTest(unittest.TestCase):
    """The detached Ctrl+F corpus must stay index-aligned with the rows."""

    def setUp(self):
        self.h = DetachedHarness()
        self.h.build_transcript_column()

    def test_texts_line_up_with_rows(self):
        self.h.add("alpha", "mic", 0)
        self.h.add("beta", "system", 30)
        self.assertEqual(len(self.h._detached_find_texts()),
                         len(self.h._detached_find_rows()))

    def test_separator_contributes_an_empty_string(self):
        self.h.add("before", "mic", 0)
        self.h.add("after", "mic", 5 + TRANSCRIPT_GAP_SEPARATOR_SECONDS + 1)

        texts = self.h._detached_find_texts()
        rows = self.h._detached_find_rows()
        separator_index = rows.index(self.h.separators()[0])
        self.assertEqual(texts[separator_index], "")
        self.assertIn("before", texts[separator_index - 1])

    def test_filtered_out_rows_are_not_searchable_but_keep_their_slot(self):
        self.h.add("alpha", "mic", 0)
        self.h.add("beta", "system", 30)

        self.h._detached_filter_combo.setCurrentIndex(1)  # Mic
        self.h._update_detached_filter()

        texts = self.h._detached_find_texts()
        self.assertEqual(len(texts), len(self.h._detached_find_rows()))
        self.assertIn("alpha", texts[0])
        self.assertEqual(texts[1], "")

    def test_grouped_bubble_is_searchable_by_every_chunk(self):
        self.h.add("first half", "mic", 0)
        self.h.add("second half", "mic", 5)

        text = self.h._detached_find_texts()[0]
        self.assertIn("first half", text)
        self.assertIn("second half", text)


class DetachedPlacementTest(unittest.TestCase):
    """The window opens at its narrowest complete layout, bottom-right."""

    def setUp(self):
        from PySide6.QtWidgets import QWidget
        self.h = DetachedHarness()
        self.h.right_shell = QWidget()  # the main-UI panel the detach hides
        self.h._on_detach_transcription()
        _app.processEvents()
        self.window = self.h._detached_window

    def tearDown(self):
        self.window.hide()
        self.window.deleteLater()

    def test_it_opens_at_the_width_its_columns_need(self):
        self.assertEqual(self.window.width(),
                         self.window.minimumSizeHint().width())

    def test_the_answers_column_is_not_clipped(self):
        answers = self.h._detached_splitter.widget(0)
        self.assertGreaterEqual(self.h._detached_splitter.sizes()[0],
                                answers.minimumSizeHint().width())

    def test_it_opens_in_the_bottom_right_corner(self):
        area = _app.primaryScreen().availableGeometry()
        frame = self.window.frameGeometry()
        self.assertEqual(frame.bottom(), area.bottom())
        if frame.width() <= area.width():
            self.assertEqual(frame.right(), area.right())
        self.assertGreaterEqual(frame.left(), area.left())
