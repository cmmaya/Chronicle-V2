"""BU113 - manual chunk selection and answer from the context menu.

Splits into three parts: the Qt-free question assembly in
``src/assistant/live_qa.py``, the pure selection math in
``resolve_chunk_selection``, and the widget-level wiring driven through the
shared harness (see tests/detached_harness.py).
"""
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace

from PySide6.QtCore import QPoint, Qt

from src.assistant.live_qa import (
    MAX_OVERLAP_WORDS,
    QUESTION_PREAMBLE,
    build_question_from_records,
    collapse_overlap,
    timestamp_range,
)
from src.app.pixel_widgets import PixelAnswerCard
from src.app.window import resolve_chunk_selection
from tests.detached_harness import T0, DetachedHarness, app

_app = app()


def rec(text, source="mic", offset=0, duration=5):
    start = T0 + timedelta(seconds=offset)
    return SimpleNamespace(
        text=text,
        source=source,
        start_dt=start,
        end_dt=start + timedelta(seconds=duration),
        transcript_id=None,
        display_text=f"[{start.strftime('%H:%M:%S')}] {source}: {text}",
    )


class CollapseOverlapTest(unittest.TestCase):
    def test_no_overlap_is_left_alone(self):
        self.assertEqual(collapse_overlap("alpha beta", "gamma delta"),
                         "gamma delta")

    def test_trailing_words_are_dropped_from_the_next_chunk(self):
        self.assertEqual(
            collapse_overlap("we need the boundary condition",
                             "boundary condition at the left edge"),
            "at the left edge",
        )

    def test_longest_overlap_wins(self):
        self.assertEqual(
            collapse_overlap("the value at the", "the value at the left edge"),
            "left edge",
        )

    def test_matching_ignores_case_and_trailing_punctuation(self):
        self.assertEqual(
            collapse_overlap("at the left edge.", "Left edge, we said"),
            "we said",
        )

    def test_surviving_text_keeps_its_original_casing(self):
        self.assertEqual(collapse_overlap("hello there", "There Was A Plan"),
                         "Was A Plan")

    def test_fully_contained_chunk_collapses_to_nothing(self):
        self.assertEqual(collapse_overlap("alpha beta gamma", "beta gamma"), "")

    def test_empty_inputs_pass_through(self):
        self.assertEqual(collapse_overlap("", "something"), "something")
        self.assertEqual(collapse_overlap("something", ""), "")

    def test_a_long_repeat_is_not_treated_as_overlap(self):
        words = " ".join(f"w{i}" for i in range(MAX_OVERLAP_WORDS + 4))
        # Beyond the cap the speaker really is repeating themselves, so the
        # text is kept rather than silently deleted.
        self.assertNotEqual(collapse_overlap(words, words), "")


class BuildQuestionTest(unittest.TestCase):
    def test_empty_selection_produces_no_question(self):
        self.assertEqual(build_question_from_records([]), "")
        self.assertEqual(build_question_from_records(None), "")

    def test_single_record_carries_source_and_time(self):
        question = build_question_from_records([rec("what is x?", "mic", 0)])
        self.assertIn(QUESTION_PREAMBLE, question)
        self.assertIn("[10:00:00] Mic: what is x?", question)

    def test_system_source_is_labelled_system(self):
        question = build_question_from_records([rec("hello", "system", 0)])
        self.assertIn("System: hello", question)

    def test_records_are_ordered_by_time_not_by_argument_order(self):
        records = [rec("second", "mic", 30), rec("first", "mic", 0)]
        lines = build_question_from_records(records).splitlines()
        self.assertLess(lines.index("[10:00:00] Mic: first"),
                        lines.index("[10:00:30] Mic: second"))

    def test_overlap_between_consecutive_chunks_is_collapsed(self):
        records = [
            rec("we need the boundary condition", "mic", 0),
            rec("boundary condition at the left edge", "mic", 5),
        ]
        question = build_question_from_records(records)
        self.assertIn("[10:00:05] Mic: at the left edge", question)
        self.assertEqual(question.count("boundary condition"), 1)

    def test_overlap_is_only_collapsed_within_one_source(self):
        # Both streams saying the same words is two people agreeing, not the
        # recorder repeating itself.
        records = [
            rec("the left edge", "mic", 0),
            rec("the left edge", "system", 5),
        ]
        question = build_question_from_records(records)
        self.assertIn("Mic: the left edge", question)
        self.assertIn("System: the left edge", question)

    def test_blank_chunks_are_skipped(self):
        records = [rec("   ", "mic", 0), rec("real text", "mic", 5)]
        question = build_question_from_records(records)
        self.assertEqual(len(question.splitlines()[2:]), 1)

    def test_records_without_a_timestamp_still_render(self):
        r = rec("untimed", "mic", 0)
        r.start_dt = None
        self.assertIn("[--:--:--] Mic: untimed", build_question_from_records([r]))


class TimestampRangeTest(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(timestamp_range([]), "")

    def test_single_record_shows_one_time(self):
        self.assertEqual(timestamp_range([rec("a", "mic", 0, duration=0)]),
                         "10:00:00")

    def test_several_records_show_start_and_end(self):
        records = [rec("a", "mic", 0), rec("b", "mic", 30)]
        self.assertEqual(timestamp_range(records), "10:00:00 - 10:00:35")


class ChunkSelectionMathTest(unittest.TestCase):
    """The gesture model, over record indices rather than bubble rows.

    A grouped bubble holds several chunks, so the unit here is the chunk: these
    indices are positions in the record list, not rows on screen.
    """

    ORDER = [0, 1, 2, 3, 4]
    NONE = Qt.NoModifier
    CTRL = Qt.ControlModifier
    SHIFT = Qt.ShiftModifier

    def test_plain_click_replaces_the_selection(self):
        sel, anchor = resolve_chunk_selection(self.ORDER, [0, 1], 0, 3, self.NONE)
        self.assertEqual(sel, [3])
        self.assertEqual(anchor, 3)

    def test_ctrl_click_adds_and_keeps_display_order(self):
        sel, anchor = resolve_chunk_selection(self.ORDER, [3], 3, 1, self.CTRL)
        self.assertEqual(sel, [1, 3])
        self.assertEqual(anchor, 1)

    def test_ctrl_click_toggles_an_already_selected_chunk_off(self):
        sel, _ = resolve_chunk_selection(self.ORDER, [1, 3], 1, 3, self.CTRL)
        self.assertEqual(sel, [1])

    def test_a_drag_selects_the_run_it_touches(self):
        sel, anchor = resolve_chunk_selection(self.ORDER, [1], 1, 3, self.NONE,
                                              extending=True)
        self.assertEqual(sel, [1, 2, 3])
        self.assertEqual(anchor, 1)

    def test_a_drag_backwards_selects_the_same_run(self):
        sel, _ = resolve_chunk_selection(self.ORDER, [3], 3, 1, self.NONE,
                                         extending=True)
        self.assertEqual(sel, [1, 2, 3])

    def test_a_drag_shrinks_again_when_it_comes_back(self):
        # The run is recomputed from the anchor each move, so reversing a drag
        # gives the chunks back instead of leaving them stuck selected.
        sel, _ = resolve_chunk_selection(self.ORDER, [1], 1, 4, self.NONE,
                                         extending=True)
        self.assertEqual(sel, [1, 2, 3, 4])
        sel, _ = resolve_chunk_selection(self.ORDER, [1], 1, 2, self.NONE,
                                         extending=True)
        self.assertEqual(sel, [1, 2])

    def test_ctrl_drag_adds_its_run_to_what_was_held(self):
        sel, _ = resolve_chunk_selection(self.ORDER, [0], 3, 4, self.CTRL,
                                         extending=True)
        self.assertEqual(sel, [0, 3, 4])

    def test_shift_click_selects_the_same_run_a_drag_would(self):
        sel, anchor = resolve_chunk_selection(self.ORDER, [1], 1, 3, self.SHIFT)
        self.assertEqual(sel, [1, 2, 3])
        self.assertEqual(anchor, 1)

    def test_a_drag_without_an_anchor_behaves_like_a_plain_click(self):
        sel, anchor = resolve_chunk_selection(self.ORDER, [], None, 2, self.NONE,
                                              extending=True)
        self.assertEqual(sel, [2])
        self.assertEqual(anchor, 2)

    def test_hidden_chunks_drop_out_of_the_selection(self):
        visible = [0, 2, 4]  # 1 and 3 filtered away
        sel, _ = resolve_chunk_selection(visible, [0, 1, 2], 0, 4, self.CTRL)
        self.assertEqual(sel, [0, 2, 4])

    def test_a_drag_cannot_pick_up_a_hidden_chunk(self):
        visible = [0, 2, 4]
        sel, _ = resolve_chunk_selection(visible, [0], 0, 4, self.NONE,
                                         extending=True)
        self.assertEqual(sel, [0, 2, 4])

    def test_clicking_a_chunk_that_is_gone_prunes_only(self):
        visible = [0, 2]
        sel, anchor = resolve_chunk_selection(visible, [0, 1], 1, 1, self.NONE)
        self.assertEqual(sel, [0])
        self.assertIsNone(anchor)


class _StubEvent:
    """Enough of a QMouseEvent for the widget-resolution path."""

    def globalPosition(self):
        class _P:
            def toPoint(self_inner):
                return QPoint(0, 0)
        return _P()


class _FakeResponse:
    def __init__(self, success=True, answer="", error=None):
        self.success = success
        self.answer = answer
        self.error = error


class SelectionWiringTest(unittest.TestCase):
    def setUp(self):
        self.h = DetachedHarness()
        self.h.build_transcript_column()
        self.h.build_answers_column()

    def test_a_grouped_bubble_keeps_one_segment_per_chunk(self):
        self.h.add("one", "mic", 0)
        self.h.add("two", "mic", 5)
        self.h.add("three", "mic", 10)

        self.assertEqual(len(self.h.bubbles()), 1)
        bubble = self.h._safe_bubble(self.h.bubbles()[0])
        self.assertEqual(bubble.segment_count, 3)

    def test_selecting_one_chunk_leaves_its_neighbours_alone(self):
        self.h.add("one", "mic", 0)
        self.h.add("two", "mic", 5)
        self.h.add("three", "mic", 10)

        self.h._set_detached_selection([1], anchor=1)
        self.assertEqual(self.h._detached_selected_chunks, [1])
        # The whole bubble is not marked - only the chunk inside it.
        self.assertFalse(self.h._safe_bubble(self.h.bubbles()[0]).is_selected())

    def test_a_click_selects_exactly_one_chunk_of_a_grouped_bubble(self):
        for i in range(4):
            self.h.add(f"chunk {i}", "mic", i * 5)
        self.assertEqual(self.h.click_chunk(2), [2])

    def test_a_drag_through_a_bubble_takes_the_chunks_it_crosses(self):
        for i in range(5):
            self.h.add(f"chunk {i}", "mic", i * 5)
        self.h.click_chunk(1)
        self.assertEqual(self.h.drag_to_chunk(3), [1, 2, 3])

    def test_a_drag_crosses_bubble_boundaries(self):
        # Two bubbles: a mic group and a system group.
        self.h.add("m0", "mic", 0)
        self.h.add("m1", "mic", 5)
        self.h.add("s0", "system", 2)
        self.assertEqual(len(self.h.bubbles()), 2)

        self.h.click_chunk(0)
        self.assertEqual(self.h.drag_to_chunk(2), [0, 1, 2])

    def test_ctrl_click_builds_a_non_contiguous_selection(self):
        for i in range(5):
            self.h.add(f"chunk {i}", "mic", i * 5)
        self.h.click_chunk(0)
        self.h.click_chunk(2, modifiers=Qt.ControlModifier)
        self.assertEqual(self.h.click_chunk(4, modifiers=Qt.ControlModifier),
                         [0, 2, 4])

    def test_clearing_drops_every_chunk(self):
        self.h.add("one", "mic", 0)
        self.h.add("two", "mic", 5)
        self.h._set_detached_selection([0, 1], anchor=0)
        self.h._clear_detached_selection()
        self.assertEqual(self.h._detached_selected_chunks, [])

    def test_the_visible_order_excludes_separators(self):
        self.h.add("before", "mic", 0)
        self.h.add("after", "mic", 5000)
        self.assertEqual(len(self.h.separators()), 1)
        self.assertEqual(self.h._visible_chunk_order(), [0, 1])

    def test_a_filter_change_prunes_hidden_chunks_from_the_selection(self):
        self.h.add("mic line", "mic", 0)
        self.h.add("system line", "system", 30)
        self.h._set_detached_selection([0, 1], anchor=0)

        self.h._detached_filter_combo.setCurrentIndex(1)  # Mic

        self.assertEqual(self.h._detached_selected_chunks, [0])

    def test_selected_records_follow_the_selected_chunks(self):
        self.h.add("one", "mic", 0)
        self.h.add("two", "mic", 5)
        self.h.add("three", "mic", 10)

        self.h._set_detached_selection([0, 2], anchor=0)
        texts = [r.text for r in self.h._selected_detached_records()]
        self.assertEqual(texts, ["one", "three"])

    def test_a_question_is_built_from_only_the_selected_chunks(self):
        self.h.add("the boundary condition is fixed", "mic", 0)
        self.h.add("what about the right edge", "mic", 5)
        self.h.add("we will get to that later", "mic", 10)

        self.h._set_detached_selection([1], anchor=1)
        question = build_question_from_records(self.h._selected_detached_records())
        self.assertIn("what about the right edge", question)
        self.assertNotIn("boundary condition", question)
        self.assertNotIn("get to that later", question)

    def test_every_chunk_label_carries_the_row_event_filter(self):
        """A chunk with no filter on it cannot report the drag it is holding.

        The labels for chunks 2..n are created by ``append_text`` well after
        the row was built, so installing the filter only at row-creation time
        left them inert: a press still reached the window by propagating up to
        the bubble, but the moves that make a drag never did.
        """
        for i in range(4):
            self.h.add(f"chunk {i}", "mic", i * 5)

        row = self.h.bubbles()[0]
        click_filter = row.property("_click_filter")
        self.assertIsNotNone(click_filter)

        bubble = self.h._safe_bubble(row)
        self.assertEqual(bubble.segment_count, 4)
        for index, label in enumerate(bubble._segments):
            with self.subTest(segment=index):
                # An installed filter is the label's parent-chain-independent
                # child of the row; assert by behaviour instead of internals.
                self.assertEqual(bubble.segment_index_of(label), index)
                self.assertIn(label, row.findChildren(type(label)))

    def test_a_press_on_any_chunk_label_resolves_to_that_chunk(self):
        for i in range(4):
            self.h.add(f"chunk {i}", "mic", i * 5)

        row = self.h.bubbles()[0]
        bubble = self.h._safe_bubble(row)
        for index, label in enumerate(bubble._segments):
            with self.subTest(segment=index):
                self.assertEqual(
                    self.h._chunk_at_event(row, label, _StubEvent()), index)

    def test_duplicate_indices_cannot_appear_twice(self):
        self.h.add("one", "mic", 0)
        self.h._set_detached_selection([0, 0], anchor=0)
        self.assertEqual(len(self.h._selected_detached_records()), 1)


class AnswerCardTest(unittest.TestCase):
    def setUp(self):
        self.h = DetachedHarness()
        self.h.build_transcript_column()
        self.h.build_answers_column()

    def _post(self, card_id, indices=(0,)):
        return self.h._post_detached_answer_card(
            card_id, question_preview=f"q{card_id}",
            time_range="10:00:00", row_indices=list(indices),
        )

    def test_cards_are_newest_first(self):
        first = self._post(1)
        second = self._post(2)
        layout = self.h._detached_answers_layout
        self.assertIs(layout.itemAt(0).widget(), second)
        self.assertIs(layout.itemAt(1).widget(), first)

    def test_posting_a_card_hides_the_empty_state(self):
        self.assertTrue(self.h._detached_answers_empty.isVisibleTo(
            self.h._answers_column))
        self._post(1)
        self.assertFalse(self.h._detached_answers_empty.isVisibleTo(
            self.h._answers_column))

    def test_dismissing_the_last_card_restores_the_empty_state(self):
        self._post(1)
        self.h._dismiss_detached_card(1)
        self.assertNotIn(1, self.h._detached_answer_cards)
        self.assertTrue(self.h._detached_answers_empty.isVisibleTo(
            self.h._answers_column))

    def test_an_answer_lands_only_in_its_own_card(self):
        one, two = self._post(1), self._post(2)
        self.h._on_detached_answer_finished(1, _FakeResponse(answer="answer one"))
        self.assertIn("answer one", one.body_label.text())
        self.assertEqual(two.body_label.text(), "Thinking...")

    def test_two_answers_resolve_independently(self):
        one, two = self._post(1), self._post(2)
        self.h._on_detached_answer_finished(2, _FakeResponse(answer="second"))
        self.h._on_detached_answer_finished(1, _FakeResponse(answer="first"))
        self.assertIn("first", one.body_label.text())
        self.assertIn("second", two.body_label.text())

    def test_a_response_for_a_dismissed_card_is_dropped(self):
        self._post(1)
        self.h._dismiss_detached_card(1)
        self.h._on_detached_answer_finished(1, _FakeResponse(answer="late"))  # no raise

    def test_a_failed_answer_offers_retry(self):
        card = self._post(1)
        self.assertFalse(card.retry_button.isVisibleTo(card))
        self.h._on_detached_answer_finished(1, _FakeResponse(success=False,
                                                            error="rate limited"))
        self.assertIn("rate limited", card.body_label.text())
        self.assertTrue(card.retry_button.isVisibleTo(card))

    def test_a_thread_level_error_also_offers_retry(self):
        card = self._post(1)
        self.h._on_detached_answer_error(1, "connection reset")
        self.assertIn("connection reset", card.body_label.text())
        self.assertTrue(card.retry_button.isVisibleTo(card))

    def test_answer_markdown_is_rendered(self):
        card = self._post(1)
        self.h._on_detached_answer_finished(1, _FakeResponse(answer="**bold**"))
        self.assertIn("<b>bold</b>", card.body_label.text())

    def test_the_timestamp_scrolls_to_the_originating_bubble(self):
        self.h.add("one", "mic", 0)
        self.h.add("two", "system", 30)
        card = self._post(1, indices=(1,))

        self.assertTrue(self.h._scroll_detached_to_records([1]))
        target = self.h.bubbles()[1]
        self.assertTrue(self.h._safe_bubble(target).is_highlighted())
        card.timestamp_clicked.emit()  # must not raise

    def test_scrolling_to_an_unknown_record_reports_failure(self):
        self.h.add("one", "mic", 0)
        self.assertFalse(self.h._scroll_detached_to_records([99]))
        self.assertFalse(self.h._scroll_detached_to_records([]))


class AnswerCardWidgetTest(unittest.TestCase):
    def test_error_text_is_escaped_not_interpreted(self):
        card = PixelAnswerCard("q", "10:00:00")
        card.set_error("<script>boom</script>")
        self.assertIn("&lt;script&gt;", card.body_label.text())

    def test_set_answer_hides_the_retry_button_again(self):
        card = PixelAnswerCard("q", "10:00:00")
        card.set_error("nope")
        card.set_answer("fine")
        self.assertFalse(card.retry_button.isVisibleTo(card))


if __name__ == "__main__":
    unittest.main()
