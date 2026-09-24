"""BU115 - auto mode controls and detected-question cards."""
import json
import os
import tempfile
import unittest
from datetime import datetime

from PySide6.QtWidgets import QToolButton

from src.assistant.live_qa import DetectedQuestion, passes_mode_policy
from src.app.pixel_widgets import PixelAnswerCard, PixelCandidateCard
from src.config import LIVE_QA
from src.app.window import normalize_live_qa_mode
from tests.detached_harness import DetachedHarness, app

_app = app()

DETECTED_AT = datetime(2026, 9, 16, 10, 0, 30)


def candidate(kind="genuine", answerable=True, directed=True, confidence=0.95,
              text="Do we need the right edge too?", indices=(1,)):
    return DetectedQuestion(
        text=text, asker="system", kind=kind, directed_at_user=directed,
        answerable=answerable, confidence=confidence,
        record_indices=list(indices), detected_at=DETECTED_AT,
    )


class ModePolicyTest(unittest.TestCase):
    def test_a_genuine_answerable_question_is_surfaced(self):
        self.assertTrue(passes_mode_policy(candidate()))

    def test_a_request_is_surfaced(self):
        self.assertTrue(passes_mode_policy(candidate(kind="request")))

    def test_rhetorical_is_never_surfaced(self):
        self.assertFalse(passes_mode_policy(candidate(kind="rhetorical")))

    def test_discourse_is_never_surfaced(self):
        self.assertFalse(passes_mode_policy(candidate(kind="discourse")))

    def test_a_hidden_candidate_says_why(self):
        from src.assistant.live_qa import mode_policy_reason
        self.assertIsNone(mode_policy_reason(candidate()))
        self.assertEqual(mode_policy_reason(candidate(kind="rhetorical")),
                         "judged rhetorical")
        self.assertEqual(mode_policy_reason(candidate(answerable=False)),
                         "judged not answerable")
        self.assertIn("aimed at you",
                      mode_policy_reason(candidate(directed=False), True))

    def test_an_unanswerable_question_is_not_surfaced(self):
        self.assertFalse(passes_mode_policy(candidate(answerable=False)))

    def test_rhetorical_stays_hidden_even_when_aimed_at_the_user(self):
        self.assertFalse(passes_mode_policy(candidate(kind="rhetorical",
                                                      directed=True)))

    def test_directed_filter_off_surfaces_a_question_aimed_elsewhere(self):
        self.assertTrue(passes_mode_policy(candidate(directed=False),
                                           directed_at_user_only=False))

    def test_directed_filter_on_hides_a_question_aimed_elsewhere(self):
        self.assertFalse(passes_mode_policy(candidate(directed=False),
                                            directed_at_user_only=True))

    def test_directed_filter_on_still_surfaces_one_aimed_at_the_user(self):
        self.assertTrue(passes_mode_policy(candidate(directed=True),
                                           directed_at_user_only=True))

    def test_none_is_not_surfaced(self):
        self.assertFalse(passes_mode_policy(None))


class NormalizeModeTest(unittest.TestCase):
    def test_known_modes_pass_through(self):
        for mode in ("manual", "suggest", "auto"):
            with self.subTest(mode=mode):
                self.assertEqual(normalize_live_qa_mode(mode), mode)

    def test_the_old_auto_splits_on_the_old_flag(self):
        self.assertEqual(normalize_live_qa_mode("auto", auto_answer=False), "suggest")
        self.assertEqual(normalize_live_qa_mode("auto", auto_answer=True), "auto")

    def test_auto_without_a_flag_is_auto(self):
        # This is the mode-chip path: the user clicked Auto, and there is no
        # stored flag to reconcile against.
        self.assertEqual(normalize_live_qa_mode("auto"), "auto")

    def test_anything_else_is_manual(self):
        for bad in (None, "", 12, "AUTO", [], {"mode": "auto"}):
            with self.subTest(bad=bad):
                self.assertEqual(normalize_live_qa_mode(bad), "manual")


class _FakeDetector:
    """Stands in for QuestionDetector so no thread and no network are used."""

    def __init__(self, cap_reached=False):
        self.stopped = False
        self.fed = []
        self.window_chunks = LIVE_QA["window_chunks"]
        self.detector_model = LIVE_QA["detector_model"]
        self.cap_reached = cap_reached

    def stop(self):
        self.stopped = True

    def feed(self, record, index=None):
        self.fed.append((index, record))

    def set_window_chunks(self, value):
        self.window_chunks = value

    def set_detector_model(self, model_id):
        self.detector_model = model_id

    def spend_summary(self):
        return {"calls": 3, "detections": 2, "estimated_cost": 0.0072,
                "cap_reached": self.cap_reached}


class _Harness(DetachedHarness):
    """Harness whose detector is the fake, so no call ever leaves the process."""

    def __init__(self, prefs_path=None):
        super().__init__(prefs_path=prefs_path)
        self.detector_builds = 0

    def _ensure_question_detector(self):
        if self._question_detector is None:
            self.detector_builds += 1
            self._question_detector = _FakeDetector()
        return self._question_detector


class ModeToggleTest(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        self.h = _Harness(prefs_path=self.path)
        self.h.build_answers_column()

    def tearDown(self):
        if os.path.exists(self.path):
            os.unlink(self.path)

    def test_manual_is_the_default(self):
        self.assertEqual(self.h._live_qa_mode, "manual")
        self.assertTrue(self.h._live_qa_mode_chips["manual"].isChecked())
        self.assertFalse(self.h._live_qa_mode_chips["suggest"].isChecked())
        self.assertFalse(self.h._live_qa_mode_chips["auto"].isChecked())

    def test_all_three_modes_are_offered_as_chips(self):
        self.assertEqual(set(self.h._live_qa_mode_chips),
                         {"manual", "suggest", "auto"})

    def test_exactly_one_chip_is_checked_at_a_time(self):
        for mode in ("manual", "suggest", "auto"):
            with self.subTest(mode=mode):
                self.h._set_live_qa_mode(mode)
                checked = [m for m, c in self.h._live_qa_mode_chips.items()
                           if c.isChecked()]
                self.assertEqual(checked, [mode])

    def test_manual_builds_no_detector(self):
        self.h.add("what is x?", "mic", 0)
        self.h._feed_question_detector(self.h._transcript_records[0], 0)
        self.assertIsNone(self.h._question_detector)
        self.assertEqual(self.h.detector_builds, 0)

    def test_suggest_stands_the_detector_up_without_auto_answering(self):
        self.h._set_live_qa_mode("suggest")
        self.assertIsNotNone(self.h._question_detector)
        self.assertTrue(self.h._detection_enabled)
        self.assertFalse(self.h._live_qa_auto_answer)

    def test_auto_stands_the_detector_up_and_answers(self):
        self.h._set_live_qa_mode("auto")
        self.assertIsNotNone(self.h._question_detector)
        self.assertTrue(self.h._detection_enabled)
        self.assertTrue(self.h._live_qa_auto_answer)

    def test_manual_enables_no_detection(self):
        self.assertFalse(self.h._detection_enabled)
        self.assertFalse(self.h._live_qa_auto_answer)

    def test_an_unknown_mode_falls_back_to_manual(self):
        self.h._set_live_qa_mode("nonsense")
        self.assertEqual(self.h._live_qa_mode, "manual")

    def test_switching_back_to_manual_tears_it_down(self):
        self.h._set_live_qa_mode("auto")
        detector = self.h._question_detector
        self.h._set_live_qa_mode("manual")
        self.assertTrue(detector.stopped)
        self.assertIsNone(self.h._question_detector)

    def test_auto_mode_feeds_live_chunks_to_the_detector(self):
        self.h._detached_window = object()
        self.h._set_live_qa_mode("auto")
        self.h.add("what is x?", "mic", 0)
        self.h._feed_question_detector(self.h._transcript_records[0], 0)
        self.assertEqual(len(self.h._question_detector.fed), 1)
        self.assertEqual(self.h._question_detector.fed[0][0], 0)

    def test_nothing_is_fed_while_the_window_is_closed(self):
        self.h._set_live_qa_mode("auto")
        self.h._detached_window = None
        self.h.add("what is x?", "mic", 0)
        self.h._feed_question_detector(self.h._transcript_records[0], 0)
        self.assertEqual(self.h._question_detector.fed, [])


class SettingsTest(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        self.h = _Harness(prefs_path=self.path)
        self.h.build_answers_column()
        self.h._set_live_qa_mode("suggest")

    def tearDown(self):
        LIVE_QA["window_chunks"] = 3
        if os.path.exists(self.path):
            os.unlink(self.path)

    def test_window_size_reaches_a_running_detector(self):
        self.h._set_live_qa_window_chunks(5)
        self.assertEqual(self.h._question_detector.window_chunks, 5)
        self.assertEqual(self.h._live_qa_window_chunks, 5)

    def test_detector_model_reaches_a_running_detector(self):
        self.h._set_live_qa_detector_model("qwen/qwen3-14b")
        self.assertEqual(self.h._question_detector.detector_model, "qwen/qwen3-14b")

    def test_changing_settings_does_not_restart_the_detector(self):
        first = self.h._question_detector
        self.h._set_live_qa_window_chunks(5)
        self.h._set_live_qa_detector_model("qwen/qwen3-14b")
        self.assertIs(self.h._question_detector, first)
        self.assertFalse(first.stopped)

    def test_directed_only_is_off_by_default(self):
        self.assertFalse(self.h._live_qa_directed_only)


class PreferenceRoundTripTest(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        os.unlink(self.path)
        self.h = _Harness(prefs_path=self.path)
        self.h.build_answers_column()

    def tearDown(self):
        LIVE_QA["window_chunks"] = 3
        if os.path.exists(self.path):
            os.unlink(self.path)

    def test_all_settings_round_trip(self):
        self.h._set_live_qa_mode("auto")
        self.h._set_live_qa_window_chunks(5)
        self.h._set_live_qa_detector_model("qwen/qwen3-14b")
        self.h._set_live_qa_directed_only(True)

        fresh = _Harness(prefs_path=self.path)
        fresh._load_live_qa_preferences()

        self.assertEqual(fresh._live_qa_mode, "auto")
        self.assertEqual(fresh._live_qa_window_chunks, 5)
        self.assertEqual(fresh._live_qa_detector_model, "qwen/qwen3-14b")
        self.assertTrue(fresh._live_qa_directed_only)

    def test_suggest_round_trips_as_itself(self):
        self.h._set_live_qa_mode("suggest")
        fresh = _Harness(prefs_path=self.path)
        fresh._load_live_qa_preferences()
        self.assertEqual(fresh._live_qa_mode, "suggest")
        self.assertFalse(fresh._live_qa_auto_answer)

    def test_defaults_apply_when_nothing_is_saved(self):
        self.h._load_live_qa_preferences()
        self.assertEqual(self.h._live_qa_mode, "manual")
        self.assertEqual(self.h._live_qa_detector_model, LIVE_QA["detector_model"])
        self.assertFalse(self.h._live_qa_auto_answer)

    def test_an_out_of_range_window_size_is_clamped_on_load(self):
        with open(self.path, "w") as fh:
            json.dump({"live_qa_window_chunks": 99}, fh)
        self.h._load_live_qa_preferences()
        self.assertEqual(self.h._live_qa_window_chunks, 5)

    def test_a_model_that_is_no_longer_offered_falls_back_to_the_default(self):
        with open(self.path, "w") as fh:
            json.dump({"live_qa_detector_model": "google/gemma-3-12b-it"}, fh)
        self.h._load_live_qa_preferences()
        self.assertEqual(self.h._live_qa_detector_model, LIVE_QA["detector_model"])

    def test_a_preference_from_the_two_setting_shape_is_migrated(self):
        # Before the modes were merged, "auto" meant auto-detect and a separate
        # flag decided whether it answered. Both old shapes have to land on the
        # mode that behaves the same way.
        for auto_answer, expected in ((False, "suggest"), (True, "auto")):
            with self.subTest(auto_answer=auto_answer):
                with open(self.path, "w") as fh:
                    json.dump({"live_qa_mode": "auto",
                               "live_qa_auto_answer": auto_answer}, fh)
                self.h._load_live_qa_preferences()
                self.assertEqual(self.h._live_qa_mode, expected)

    def test_garbage_values_do_not_raise(self):
        with open(self.path, "w") as fh:
            json.dump({"live_qa_window_chunks": "five",
                       "live_qa_mode": 12,
                       "live_qa_auto_answer": "yes"}, fh)
        self.h._load_live_qa_preferences()
        self.assertEqual(self.h._live_qa_mode, "manual")
        self.assertEqual(self.h._live_qa_window_chunks, LIVE_QA["window_chunks"])

    def test_live_qa_settings_do_not_clobber_the_layout_settings(self):
        self.h._update_preferences({"detached_splitter_sizes": [400, 640]})
        self.h._set_live_qa_mode("auto")
        self.assertEqual(self.h._read_preferences()["detached_splitter_sizes"],
                         [400, 640])


class CandidateCardTest(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        self.h = _Harness(prefs_path=self.path)
        self.h.build_transcript_column()
        self.h.build_answers_column()
        self.h.add("The left edge, at x equals zero.", "mic", 0)
        self.h.add("Do we need the right edge too?", "system", 30)

    def tearDown(self):
        if os.path.exists(self.path):
            os.unlink(self.path)

    def _cards(self):
        layout = self.h._detached_answers_layout
        return [layout.itemAt(i).widget() for i in range(layout.count())
                if layout.itemAt(i).widget() is not None]

    def test_a_passing_candidate_becomes_a_card(self):
        self.h._on_candidates_detected([candidate()])
        cards = [c for c in self._cards() if isinstance(c, PixelCandidateCard)]
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0].question_text, "Do we need the right edge too?")

    def test_a_rhetorical_candidate_never_becomes_a_card(self):
        self.h._on_candidates_detected([candidate(kind="rhetorical")])
        self.assertEqual(self.h._detached_candidate_cards, {})

    def test_a_hidden_candidate_is_counted_and_explained_on_the_chip(self):
        # "2 found" with nothing on screen read as a bug; say what was hidden.
        self.h._live_qa_mode = "suggest"
        self.h._on_candidates_detected([candidate(kind="rhetorical")])
        chip = self.h._live_qa_spend_chip
        self.assertIn("1 hidden", chip.text())
        self.assertIn("Do we need the right edge too? (judged rhetorical)",
                      chip.toolTip())

    def test_a_discourse_candidate_never_becomes_a_card(self):
        self.h._on_candidates_detected([candidate(kind="discourse")])
        self.assertEqual(self.h._detached_candidate_cards, {})

    def test_the_directed_filter_is_applied_at_card_time(self):
        self.h._live_qa_directed_only = True
        self.h._on_candidates_detected([candidate(directed=False)])
        self.assertEqual(self.h._detached_candidate_cards, {})

    def test_a_card_shows_asker_kind_and_time(self):
        self.h._on_candidates_detected([candidate()])
        card = next(iter(self.h._detached_candidate_cards.values()))[0]
        self.assertIn("System", card.meta_label.text())
        self.assertIn("genuine", card.meta_label.text())
        self.assertEqual(card.time_button.text(), "10:00:30")

    def test_posting_a_candidate_hides_the_empty_state(self):
        self.h._on_candidates_detected([candidate()])
        self.assertFalse(
            self.h._detached_answers_empty.isVisibleTo(self.h._answers_column))

    def test_dismissing_the_last_candidate_restores_the_empty_state(self):
        self.h._on_candidates_detected([candidate()])
        card_id = next(iter(self.h._detached_candidate_cards))
        self.h._dismiss_candidate_card(card_id)
        self.assertEqual(self.h._detached_candidate_cards, {})
        self.assertTrue(
            self.h._detached_answers_empty.isVisibleTo(self.h._answers_column))

    def test_candidates_do_not_auto_answer_by_default(self):
        self.h._on_candidates_detected([candidate()])
        self.assertEqual(self.h._detached_answer_cards, {})
        self.assertEqual(len(self.h._detached_candidate_cards), 1)


class AnswerFromCandidateTest(unittest.TestCase):
    """Answering a candidate must run the chat agent, not the detector model."""

    class _H(_Harness):
        def __init__(self, prefs_path=None):
            super().__init__(prefs_path=prefs_path)
            self.answers_started = []

        def _start_detached_answer(self, card_id, question):
            self.answers_started.append((card_id, question))

    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        self.h = self._H(prefs_path=self.path)
        self.h.assistant_service = object()  # only its presence is checked
        self.h.build_transcript_column()
        self.h.build_answers_column()
        self.h.add("The left edge, at x equals zero.", "mic", 0)
        self.h.add("Do we need the right edge too?", "system", 30)

    def tearDown(self):
        if os.path.exists(self.path):
            os.unlink(self.path)

    def _post(self, cand=None):
        self.h._on_candidates_detected([cand or candidate()])
        return next(iter(self.h._detached_candidate_cards))

    def test_answering_replaces_the_candidate_with_an_answer_card(self):
        card_id = self._post()
        self.h._answer_candidate(card_id)
        self.assertNotIn(card_id, self.h._detached_candidate_cards)
        self.assertIsInstance(self.h._detached_answer_cards[card_id],
                              PixelAnswerCard)

    def test_the_question_is_built_from_the_originating_chunks(self):
        card_id = self._post()
        self.h._answer_candidate(card_id)
        _, question = self.h.answers_started[0]
        self.assertIn("Do we need the right edge too?", question)

    def test_the_answer_card_keeps_the_candidate_question_as_its_title(self):
        card_id = self._post()
        self.h._answer_candidate(card_id)
        card = self.h._detached_answer_cards[card_id]
        self.assertEqual(card.question_label.text(),
                         "Do we need the right edge too?")

    def test_auto_answer_answers_a_confident_candidate_without_a_click(self):
        self.h._set_live_qa_mode("auto")
        self.h._on_candidates_detected([candidate(confidence=0.95)])
        self.assertEqual(len(self.h.answers_started), 1)
        self.assertEqual(self.h._detached_candidate_cards, {})

    def test_auto_answer_leaves_a_low_confidence_candidate_waiting(self):
        self.h._set_live_qa_mode("auto")
        self.h._on_candidates_detected([candidate(confidence=0.5)])
        self.assertEqual(self.h.answers_started, [])
        self.assertEqual(len(self.h._detached_candidate_cards), 1)

    def test_answering_twice_is_a_no_op_the_second_time(self):
        card_id = self._post()
        self.h._answer_candidate(card_id)
        self.h._answer_candidate(card_id)
        self.assertEqual(len(self.h.answers_started), 1)

    def test_a_candidate_with_no_usable_indices_falls_back_to_its_own_text(self):
        card_id = self._post(candidate(indices=(999,)))
        self.h._answer_candidate(card_id)
        _, question = self.h.answers_started[0]
        self.assertEqual(question, "Do we need the right edge too?")


class SpendChipTest(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        self.h = _Harness(prefs_path=self.path)
        self.h.build_answers_column()

    def tearDown(self):
        if os.path.exists(self.path):
            os.unlink(self.path)

    def test_manual_mode_says_nothing_is_being_spent(self):
        self.assertIn("no detector calls", self.h._live_qa_spend_chip.text())

    def test_auto_mode_reports_calls_and_cost(self):
        self.h._set_live_qa_mode("auto")
        self.h._refresh_live_qa_spend_chip()
        text = self.h._live_qa_spend_chip.text()
        self.assertIn("3 calls", text)
        self.assertIn("0.007", text)

    def test_switching_back_to_manual_stops_reporting_spend(self):
        self.h._set_live_qa_mode("auto")
        self.h._set_live_qa_mode("manual")
        self.assertIn("no detector calls", self.h._live_qa_spend_chip.text())


class CapNoticeTest(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        self.h = _Harness(prefs_path=self.path)
        self.h.build_answers_column()

    def tearDown(self):
        if os.path.exists(self.path):
            os.unlink(self.path)

    def test_no_notice_before_the_cap(self):
        self.h._set_live_qa_mode("auto")
        self.h._on_candidates_detected([candidate()])
        self.assertIsNone(self.h._detached_cap_notice)

    def test_the_cap_shows_a_notice_rather_than_going_quiet(self):
        self.h._set_live_qa_mode("auto")
        self.h._question_detector.cap_reached = True
        self.h._on_candidates_detected([candidate()])
        self.assertIsNotNone(self.h._detached_cap_notice)
        self.assertIn("Detection limit reached",
                      self.h._detached_cap_notice.text())

    def test_the_notice_is_only_posted_once(self):
        self.h._set_live_qa_mode("auto")
        self.h._question_detector.cap_reached = True
        self.h._on_candidates_detected([candidate(text="one?")])
        first = self.h._detached_cap_notice
        self.h._on_candidates_detected([candidate(text="two?")])
        self.assertIs(self.h._detached_cap_notice, first)


if __name__ == "__main__":
    unittest.main()
