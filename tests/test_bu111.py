"""BU111 - structured transcript records and detached bubble grouping.

Runs Qt offscreen so the detached-stream tests exercise the real widgets.
See tests/detached_harness.py for how a MainWindow is stood up without its
database and session manager.
"""
import os
import unittest
from datetime import datetime, timedelta

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from src.app.window import (
    TRANSCRIPT_MAX_GROUP_SECONDS,
    TRANSCRIPT_PAUSE_GAP_SECONDS,
    should_extend_group,
)
from tests.detached_harness import T0, DetachedHarness, app

_app = app()


def _harness():
    h = DetachedHarness()
    h.build_transcript_column()
    return h


def _group(start_dt, last_end):
    return {"row": None, "bubble": None, "start_dt": start_dt, "last_end": last_end}


class ShouldExtendGroupTest(unittest.TestCase):
    def test_no_group_never_extends(self):
        self.assertFalse(should_extend_group(None, T0, T0 + timedelta(seconds=5)))

    def test_gap_under_threshold_extends(self):
        group = _group(T0, T0 + timedelta(seconds=5))
        start = T0 + timedelta(seconds=5 + TRANSCRIPT_PAUSE_GAP_SECONDS - 0.5)
        self.assertTrue(should_extend_group(group, start, start + timedelta(seconds=5)))

    def test_gap_exactly_at_threshold_extends(self):
        group = _group(T0, T0 + timedelta(seconds=5))
        start = T0 + timedelta(seconds=5 + TRANSCRIPT_PAUSE_GAP_SECONDS)
        self.assertTrue(should_extend_group(group, start, start + timedelta(seconds=1)))

    def test_gap_over_threshold_breaks(self):
        group = _group(T0, T0 + timedelta(seconds=5))
        start = T0 + timedelta(seconds=5 + TRANSCRIPT_PAUSE_GAP_SECONDS + 0.1)
        self.assertFalse(should_extend_group(group, start, start + timedelta(seconds=1)))

    def test_duration_exactly_at_cap_extends(self):
        group = _group(T0, T0 + timedelta(seconds=TRANSCRIPT_MAX_GROUP_SECONDS - 5))
        start = T0 + timedelta(seconds=TRANSCRIPT_MAX_GROUP_SECONDS - 5)
        end = T0 + timedelta(seconds=TRANSCRIPT_MAX_GROUP_SECONDS)
        self.assertTrue(should_extend_group(group, start, end))

    def test_duration_over_cap_breaks(self):
        group = _group(T0, T0 + timedelta(seconds=TRANSCRIPT_MAX_GROUP_SECONDS - 5))
        start = T0 + timedelta(seconds=TRANSCRIPT_MAX_GROUP_SECONDS - 5)
        end = T0 + timedelta(seconds=TRANSCRIPT_MAX_GROUP_SECONDS + 0.1)
        self.assertFalse(should_extend_group(group, start, end))

    def test_missing_datetimes_break(self):
        group = _group(T0, T0 + timedelta(seconds=5))
        self.assertFalse(should_extend_group(group, None, T0 + timedelta(seconds=10)))
        self.assertFalse(should_extend_group(group, T0 + timedelta(seconds=6), None))

    def test_group_without_timing_breaks(self):
        self.assertFalse(
            should_extend_group(_group(None, None), T0, T0 + timedelta(seconds=5))
        )


class DetachedGroupingTest(unittest.TestCase):
    def setUp(self):
        self.h = _harness()

    def test_consecutive_same_source_chunks_share_one_bubble(self):
        self.h.add("one", "mic", 0)
        self.h.add("two", "mic", 5)
        self.h.add("three", "mic", 10)

        rows = self.h.rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].property("record_indices"), [0, 1, 2])

    def test_long_pause_starts_a_new_bubble(self):
        self.h.add("one", "mic", 0)
        self.h.add("two", "mic", 5 + TRANSCRIPT_PAUSE_GAP_SECONDS + 1)

        rows = self.h.rows()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0].property("record_indices"), [0])
        self.assertEqual(rows[1].property("record_indices"), [1])

    def test_sixty_second_cap_starts_a_new_bubble(self):
        offset = 0
        while offset + 5 <= TRANSCRIPT_MAX_GROUP_SECONDS:
            self.h.add("chunk", "mic", offset)
            offset += 5
        self.assertEqual(len(self.h.rows()), 1)

        self.h.add("overflow", "mic", offset)
        self.assertEqual(len(self.h.rows()), 2)

    def test_sources_keep_separate_open_groups(self):
        self.h.add("m1", "mic", 0)
        self.h.add("s1", "system", 1)
        self.h.add("m2", "mic", 5)

        rows = self.h.rows()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0].property("record_indices"), [0, 2])
        self.assertEqual(rows[0].property("source"), "mic")
        self.assertEqual(rows[1].property("record_indices"), [1])
        self.assertEqual(rows[1].property("source"), "system")

    def test_extended_bubble_accumulates_find_text(self):
        self.h.add("hello there", "mic", 0)
        self.h.add("and again", "mic", 5)

        row = self.h.rows()[0]
        self.assertIn("hello there", row.property("find_text"))
        self.assertIn("and again", row.property("find_text"))

    def test_filter_hides_the_other_source(self):
        self.h.add("m1", "mic", 0)
        self.h.add("s1", "system", 1)

        self.h._detached_filter_combo.setCurrentIndex(1)  # Mic
        self.h._update_detached_filter()

        rows = self.h.rows()
        self.assertTrue(rows[0].isVisibleTo(self.h._detached_container))
        self.assertFalse(rows[1].isVisibleTo(self.h._detached_container))

    def test_new_bubble_respects_the_active_filter(self):
        self.h._detached_filter_combo.setCurrentIndex(1)  # Mic
        self.h.add("s1", "system", 0)

        row = self.h.rows()[0]
        self.assertFalse(row.isVisibleTo(self.h._detached_container))


class HistoryShimTest(unittest.TestCase):
    def test_history_mirrors_record_display_text(self):
        h = _harness()
        h.add("one", "mic", 0)
        h.add("two", "system", 30)

        self.assertEqual(
            h._transcription_history,
            ["[10:00:00] Mic: one", "[10:00:30] System: two"],
        )

    def test_history_is_empty_without_records(self):
        self.assertEqual(_harness()._transcription_history, [])


class RecordConstructionTest(unittest.TestCase):
    def test_replay_record_carries_the_row_id(self):
        h = _harness()
        record = h.add("from db", "system", 0, transcript_id=42)
        self.assertEqual(record.transcript_id, 42)

    def test_live_record_has_no_row_id(self):
        h = _harness()
        self.assertIsNone(h.add("live", "mic", 0).transcript_id)


if __name__ == "__main__":
    unittest.main()
