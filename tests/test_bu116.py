"""BU116 - the detected question is sent, and the two answer modes."""
import json
import os
import tempfile
import unittest
from datetime import datetime

from src.assistant.live_qa import (
    CANDIDATE_CONTEXT_PREAMBLE, CANDIDATE_QUESTION_PREAMBLE, DetectedQuestion,
    EARLIER_TRANSCRIPT_PREAMBLE, QUESTION_PREAMBLE, answer_instruction,
    build_question_for_candidate, build_question_from_records,
    strip_transcript_evidence,
)
from src.app.pixel_widgets import PixelAnswerCard, format_answer_html
from src.app.window import (AssistantQueryThread, LIVE_QA_ANSWER_MODES,
                            normalize_live_qa_answer_mode)
from src.config import LIVE_QA
from tests.detached_harness import DetachedHarness, app

_app = app()

DETECTED_AT = datetime(2026, 9, 16, 10, 0, 30)


def candidate(text="Do we need the right edge too?", indices=(0,)):
    return DetectedQuestion(
        text=text, asker="system", kind="genuine", directed_at_user=True,
        answerable=True, confidence=0.95, record_indices=list(indices),
        detected_at=DETECTED_AT,
    )


class _Record:
    def __init__(self, text, source="system", offset=0):
        self.text = text
        self.source = source
        self.start_dt = datetime(2026, 9, 16, 10, 0, offset)
        self.end_dt = None


# =========================
# The auto-answer question
# =========================

class CandidateQuestionTest(unittest.TestCase):
    def setUp(self):
        self.records = [
            _Record("the integral runs over the left edge", offset=0),
            _Record("do we need the right edge too", offset=5),
        ]

    def test_the_detected_question_is_in_the_message(self):
        question = build_question_for_candidate(candidate(), self.records)
        self.assertIn("Do we need the right edge too?", question)

    def test_the_question_comes_before_its_context(self):
        question = build_question_for_candidate(candidate(), self.records)
        self.assertLess(question.index("Do we need the right edge too?"),
                        question.index(CANDIDATE_CONTEXT_PREAMBLE))

    def test_the_context_is_labelled_as_context(self):
        question = build_question_for_candidate(candidate(), self.records)
        self.assertIn(CANDIDATE_QUESTION_PREAMBLE, question)
        self.assertIn(CANDIDATE_CONTEXT_PREAMBLE, question)
        self.assertIn("[10:00:00] System: the integral runs over the left edge",
                      question)

    def test_it_does_not_use_the_manual_path_preamble(self):
        # That one says "answer the question in the following excerpt", which
        # is the work the detector already did.
        question = build_question_for_candidate(candidate(), self.records)
        self.assertNotIn(QUESTION_PREAMBLE, question)

    def test_unresolvable_indices_still_ask_the_question(self):
        question = build_question_for_candidate(candidate(), [])
        self.assertEqual(question, "Do we need the right edge too?")

    def test_empty_context_records_still_ask_the_question(self):
        question = build_question_for_candidate(candidate(), [_Record("  ")])
        self.assertEqual(question, "Do we need the right edge too?")

    def test_a_candidate_with_no_text_falls_back_to_the_excerpt(self):
        question = build_question_for_candidate(candidate(text="  "), self.records)
        self.assertTrue(question.startswith(QUESTION_PREAMBLE))

    def test_nothing_at_all_is_an_empty_question(self):
        self.assertEqual(build_question_for_candidate(candidate(text=""), []), "")

    def test_the_manual_builder_is_unchanged(self):
        question = build_question_from_records(self.records)
        self.assertTrue(question.startswith(QUESTION_PREAMBLE))
        self.assertIn("[10:00:05] System: do we need the right edge too",
                      question)


class TranscriptEvidenceTest(unittest.TestCase):
    """The transcript before a live question is sent as evidence.

    During a live session the search index does not exist yet, so this is the
    only way the answer - usually said before the question - reaches the model.
    """

    def setUp(self):
        self.history = [
            _Record("I have a ukulele", "mic", offset=0),
            _Record("what's the instrument that I own?", "mic", offset=30),
        ]

    def test_a_candidate_answer_sees_what_was_said_before(self):
        question = build_question_for_candidate(
            candidate("What's the instrument that I own?"), self.history)
        self.assertIn("I have a ukulele", question)

    def test_the_manual_path_sends_the_transcript_before_the_selection(self):
        question = build_question_from_records(self.history[1:], self.history[:1])
        self.assertIn(EARLIER_TRANSCRIPT_PREAMBLE, question)
        self.assertLess(question.index("the instrument that I own"),
                        question.index("I have a ukulele"))

    def test_the_evidence_keeps_the_newest_lines_within_budget(self):
        records = [_Record(f"line {i:02d} " + "x" * 80, offset=i) for i in range(40)]
        old = LIVE_QA.get("answer_context_chars")
        LIVE_QA["answer_context_chars"] = 500
        try:
            question = build_question_for_candidate(candidate(), records)
        finally:
            LIVE_QA["answer_context_chars"] = old
        evidence = question.split(CANDIDATE_CONTEXT_PREAMBLE, 1)[1]
        self.assertIn("line 39", evidence)
        self.assertNotIn("line 00", evidence)
        self.assertLessEqual(len(evidence.strip()), 500)

    def test_the_reference_match_sees_only_the_question(self):
        question = build_question_for_candidate(candidate(), self.history)
        stripped = strip_transcript_evidence(question)
        self.assertIn("Do we need the right edge too?", stripped)
        self.assertNotIn("ukulele", stripped)


# =========================
# Answer mode -> instruction
# =========================

class AnswerInstructionTest(unittest.TestCase):
    def test_every_mode_has_an_instruction(self):
        for mode in LIVE_QA_ANSWER_MODES:
            with self.subTest(mode=mode):
                self.assertTrue(answer_instruction(mode).strip())

    def test_the_two_modes_differ(self):
        self.assertNotEqual(answer_instruction("transcripts"),
                            answer_instruction("general"))

    def test_transcripts_asks_for_the_labelled_lines(self):
        text = answer_instruction("transcripts")
        self.assertIn("From transcripts:", text)
        self.assertIn("General knowledge:", text)

    def test_general_asks_for_no_prefix(self):
        self.assertIn("No label or prefix", answer_instruction("general"))

    def test_both_modes_carry_the_length_rule(self):
        for mode in LIVE_QA_ANSWER_MODES:
            with self.subTest(mode=mode):
                self.assertIn("two sentences", answer_instruction(mode))

    def test_an_unknown_mode_falls_back_to_transcripts(self):
        self.assertEqual(answer_instruction("nonsense"),
                         answer_instruction("transcripts"))


class NormalizeAnswerModeTest(unittest.TestCase):
    def test_known_modes_pass_through(self):
        for mode in LIVE_QA_ANSWER_MODES:
            with self.subTest(mode=mode):
                self.assertEqual(normalize_live_qa_answer_mode(mode), mode)

    def test_anything_else_is_the_default(self):
        for bad in (None, "", 7, "TRANSCRIPTS", [], {"mode": "general"}):
            with self.subTest(bad=bad):
                self.assertEqual(normalize_live_qa_answer_mode(bad),
                                 LIVE_QA["answer_mode"])


# =========================
# The window
# =========================

class _Harness(DetachedHarness):
    """Records what would have been asked instead of asking it."""

    def __init__(self, prefs_path=None):
        super().__init__(prefs_path=prefs_path)
        self.assistant_service = object()
        self.answers_started = []

    def _start_detached_answer(self, card_id, question):
        self.answers_started.append((card_id, question))


class AnswerModeChipTest(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        self.h = _Harness(prefs_path=self.path)
        self.h.build_answers_column()

    def tearDown(self):
        if os.path.exists(self.path):
            os.unlink(self.path)

    def test_transcripts_is_the_default(self):
        self.assertEqual(self.h._live_qa_answer_mode, "transcripts")
        self.assertTrue(self.h._live_qa_answer_mode_chips["transcripts"].isChecked())

    def test_both_modes_are_offered(self):
        self.assertEqual(set(self.h._live_qa_answer_mode_chips),
                         set(LIVE_QA_ANSWER_MODES))

    def test_exactly_one_chip_is_checked(self):
        for mode in LIVE_QA_ANSWER_MODES:
            with self.subTest(mode=mode):
                self.h._set_live_qa_answer_mode(mode)
                checked = [m for m, c in self.h._live_qa_answer_mode_chips.items()
                           if c.isChecked()]
                self.assertEqual(checked, [mode])

    def test_the_answer_chips_are_a_separate_group_from_the_detection_chips(self):
        self.assertFalse(
            set(self.h._live_qa_answer_mode_chips)
            & set(self.h._live_qa_mode_chips)
        )

    def test_choosing_an_answer_mode_does_not_touch_detection(self):
        self.h._set_live_qa_answer_mode("general")
        self.assertEqual(self.h._live_qa_mode, "manual")
        self.assertIsNone(self.h._question_detector)

    def test_an_unknown_mode_falls_back(self):
        self.h._set_live_qa_answer_mode("nonsense")
        self.assertEqual(self.h._live_qa_answer_mode, LIVE_QA["answer_mode"])


class AnswerModePreferenceTest(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        os.unlink(self.path)
        self.h = _Harness(prefs_path=self.path)
        self.h.build_answers_column()

    def tearDown(self):
        if os.path.exists(self.path):
            os.unlink(self.path)

    def test_the_answer_mode_round_trips(self):
        self.h._set_live_qa_answer_mode("general")
        fresh = _Harness(prefs_path=self.path)
        fresh._load_live_qa_preferences()
        self.assertEqual(fresh._live_qa_answer_mode, "general")

    def test_it_is_written_under_its_own_key(self):
        self.h._set_live_qa_answer_mode("general")
        with open(self.path) as f:
            self.assertEqual(json.load(f)["live_qa_answer_mode"], "general")

    def test_a_stored_rubbish_value_loads_as_the_default(self):
        with open(self.path, "w") as f:
            json.dump({"live_qa_answer_mode": "whatever"}, f)
        fresh = _Harness(prefs_path=self.path)
        fresh._load_live_qa_preferences()
        self.assertEqual(fresh._live_qa_answer_mode, LIVE_QA["answer_mode"])

    def test_no_stored_value_loads_as_the_default(self):
        with open(self.path, "w") as f:
            json.dump({}, f)
        fresh = _Harness(prefs_path=self.path)
        fresh._load_live_qa_preferences()
        self.assertEqual(fresh._live_qa_answer_mode, LIVE_QA["answer_mode"])

    def test_the_detection_mode_is_untouched_by_an_answer_mode_change(self):
        self.h._set_live_qa_answer_mode("general")
        fresh = _Harness(prefs_path=self.path)
        fresh._load_live_qa_preferences()
        self.assertEqual(fresh._live_qa_mode, "manual")


class CandidateAnswerQuestionTest(unittest.TestCase):
    """The auto path sends the detected question, through the real window."""

    def setUp(self):
        self.h = _Harness()
        self.h.build_answers_column()
        self.h.build_transcript_column()
        self.h.add("the integral runs over the left edge", "system", 0)
        self.h.add("do we need the right edge too", "system", 5)

    def _post(self, cand):
        self.h._post_candidate_card(cand)
        return self.h._detached_answer_seq

    def test_the_sent_question_contains_the_detected_question(self):
        card_id = self._post(candidate(indices=(0, 1)))
        self.h._answer_candidate(card_id)
        _, question = self.h.answers_started[0]
        self.assertIn("Do we need the right edge too?", question)

    def test_the_sent_question_still_carries_the_window(self):
        card_id = self._post(candidate(indices=(0, 1)))
        self.h._answer_candidate(card_id)
        _, question = self.h.answers_started[0]
        self.assertIn("the integral runs over the left edge", question)


# =========================
# Instruction routing
# =========================

class _FakeService:
    """Just enough of AssistantAnswerService to see which agent was asked."""

    def __init__(self):
        self._agents = {
            "research_helper": {
                "label": "Research Helper",
                "model": "google/gemini-3.8-flash",
                "system_instruction": "chat pane instruction",
            },
        }

    async def ask_async(self, **kwargs):  # pragma: no cover - not run here
        raise AssertionError("not called in these tests")


class InstructionRoutingTest(unittest.TestCase):
    def setUp(self):
        self.service = _FakeService()

    def _thread(self, system_instruction=None):
        return AssistantQueryThread(
            assistant_service=self.service,
            question="q",
            agent_id="research_helper",
            explicit_scope="current_session",
            active_session_id=1,
            selected_session_id=1,
            conversation_id=None,
            system_instruction=system_instruction,
        )

    def test_the_chat_ask_flow_is_unchanged(self):
        thread = self._thread()
        self.assertEqual(thread._effective_agent_id(), "research_helper")
        self.assertEqual(set(self.service._agents), {"research_helper"})

    def test_a_live_answer_asks_under_a_derived_agent(self):
        thread = self._thread(answer_instruction("transcripts"))
        agent_id = thread._effective_agent_id()
        self.assertNotEqual(agent_id, "research_helper")
        self.assertEqual(
            self.service._agents[agent_id]["system_instruction"],
            answer_instruction("transcripts"),
        )

    def test_the_derived_agent_keeps_the_chat_agents_model(self):
        thread = self._thread(answer_instruction("general"))
        agent_id = thread._effective_agent_id()
        self.assertEqual(self.service._agents[agent_id]["model"],
                         self.service._agents["research_helper"]["model"])

    def test_the_chat_agents_own_instruction_is_not_overwritten(self):
        self._thread(answer_instruction("general"))._effective_agent_id()
        self.assertEqual(
            self.service._agents["research_helper"]["system_instruction"],
            "chat pane instruction",
        )

    def test_the_two_modes_get_two_agents(self):
        first = self._thread(answer_instruction("transcripts"))._effective_agent_id()
        second = self._thread(answer_instruction("general"))._effective_agent_id()
        self.assertNotEqual(first, second)

    def test_the_same_instruction_reuses_one_agent(self):
        first = self._thread(answer_instruction("general"))._effective_agent_id()
        second = self._thread(answer_instruction("general"))._effective_agent_id()
        self.assertEqual(first, second)
        self.assertEqual(len(self.service._agents), 2)


class LiveAnswerUsesTheModeInstructionTest(unittest.TestCase):
    """The window hands the mode's instruction to the thread it starts."""

    class _AgentCombo:
        """Stands in for the chat panel's agent picker."""

        @staticmethod
        def currentData():
            return "research_helper"

    class _Harness(DetachedHarness):
        def __init__(self, prefs_path=None):
            super().__init__(prefs_path=prefs_path)
            self.assistant_service = _FakeService()
            self.agent_combo = LiveAnswerUsesTheModeInstructionTest._AgentCombo()

    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        self.h = self._Harness(prefs_path=self.path)
        self.h.build_answers_column()
        self.h.add("an edge case", "system", 0)
        self.started = []

        # Capture the thread's constructor arguments without starting a thread.
        import src.app.window as window_module
        self._real_thread = window_module.AssistantQueryThread
        captured = self.started

        class _CapturingThread:
            def __init__(self, **kwargs):
                captured.append(kwargs)
                self.finished_signal = _Signal()
                self.error_signal = _Signal()
                self.finished = _Signal()

            def start(self):
                pass

        window_module.AssistantQueryThread = _CapturingThread

    def tearDown(self):
        import src.app.window as window_module
        window_module.AssistantQueryThread = self._real_thread
        if os.path.exists(self.path):
            os.unlink(self.path)

    def _ask(self):
        self.h._detached_selected_chunks = [0]
        self.h._on_answer_selected_chunks()

    def test_transcripts_mode_sends_the_transcripts_instruction(self):
        self.h._set_live_qa_answer_mode("transcripts")
        self._ask()
        self.assertEqual(self.started[0]["system_instruction"],
                         answer_instruction("transcripts"))

    def test_general_mode_sends_the_general_instruction(self):
        self.h._set_live_qa_answer_mode("general")
        self._ask()
        self.assertEqual(self.started[0]["system_instruction"],
                         answer_instruction("general"))

    def test_the_answering_agent_is_the_chat_panels(self):
        self._ask()
        self.assertEqual(self.started[0]["agent_id"], "research_helper")

    def test_the_detector_model_is_never_the_answering_model(self):
        # The thread is never handed a model at all: the service resolves it
        # through the chat agent and get_selected_model().
        self._ask()
        self.assertNotIn("model", self.started[0])


class _Signal:
    """A Signal-shaped stand-in for the capturing thread above."""

    def connect(self, *_args, **_kwargs):
        pass

    def disconnect(self, *_args, **_kwargs):
        pass


# =========================
# Rendering
# =========================

class AnswerLabelRenderingTest(unittest.TestCase):
    def test_the_transcripts_label_survives_as_its_own_line(self):
        html = format_answer_html("From transcripts: the left edge only.")
        self.assertIn("From transcripts:", html)
        self.assertIn("<b>", html)

    def test_both_labels_are_rendered_on_separate_lines(self):
        html = format_answer_html(
            "From transcripts: No answer found\n"
            "General knowledge: Gauss's theorem relates them."
        )
        self.assertIn("<br>", html)
        self.assertIn("From transcripts:", html)
        self.assertIn("General knowledge:", html)

    def test_a_reference_document_label_is_recognised(self):
        html = format_answer_html("From syllabus.txt: exercise 4 is on page 7.")
        self.assertIn("From syllabus.txt:", html)
        self.assertIn("<b>", html)

    def test_an_unlabelled_answer_renders_as_before(self):
        html = format_answer_html("Gauss's theorem relates them.")
        self.assertEqual(html, "Gauss&#x27;s theorem relates them.")

    def test_a_sentence_merely_starting_with_from_is_not_a_label(self):
        html = format_answer_html("From the slides the answer is: page 7.")
        self.assertNotIn("<b>", html)

    def test_markdown_inside_a_labelled_line_still_renders(self):
        html = format_answer_html("From transcripts: the **left** edge.")
        self.assertIn("<b>left</b>", html)

    def test_empty_text_renders_to_nothing(self):
        self.assertEqual(format_answer_html(""), "")

    def test_the_card_accepts_the_rendered_html(self):
        card = PixelAnswerCard("q", "10:00:00")
        card.set_answer(format_answer_html("From transcripts: yes."))
        self.assertIn("From transcripts:", card.body_label.text())


if __name__ == "__main__":
    unittest.main()
