"""BU114 - question detection service.

No Qt and no network: the detector takes its client by injection, and its
clock too, so the interval cap is tested without sleeping through it.
"""
import json
import threading
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace

from src.config import ALLOWED_MODELS, LIVE_QA
from src.assistant.live_qa import (
    DETECTOR_SYSTEM_PROMPT,
    DetectedQuestion,
    QuestionDetector,
    build_window_text,
    dedupe,
    is_discourse,
    local_gate,
    normalize_question,
    parse_detection,
    window_is_all_discourse,
)

T0 = datetime(2026, 9, 16, 10, 0, 0)


def rec(text, source="mic", offset=0):
    start = T0 + timedelta(seconds=offset)
    return SimpleNamespace(text=text, source=source, start_dt=start,
                           end_dt=start + timedelta(seconds=5))


def detection(text="what is x?", kind="genuine", confidence=0.95, **kw):
    payload = {
        "text": text,
        "asker": kw.get("asker", "system"),
        "kind": kind,
        "directed_at_user": kw.get("directed_at_user", True),
        "answerable": kw.get("answerable", True),
        "confidence": confidence,
    }
    return json.dumps([payload])


class _FakeClient:
    """Records the calls made to it and replays queued replies."""

    def __init__(self, replies=None, raises=None):
        self.replies = list(replies or [])
        self.raises = raises
        self.calls = []

    def chat(self, messages, model=None, temperature=None):
        self.calls.append(
            {"messages": messages, "model": model, "temperature": temperature}
        )
        if self.raises is not None:
            raise self.raises
        return self.replies.pop(0) if self.replies else "[]"


class _Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class ConfigTest(unittest.TestCase):
    def test_three_detector_models_are_offered(self):
        ids = [m["id"] for m in LIVE_QA["detector_models"]]
        self.assertEqual(len(ids), 3)
        self.assertIn("google/gemini-3.8-flash", ids)
        self.assertIn("qwen/qwen3-14b", ids)
        self.assertIn("google/gemini-2.5-flash", ids)

    def test_every_offered_model_is_an_allowed_model(self):
        for model in LIVE_QA["detector_models"]:
            with self.subTest(model=model["id"]):
                self.assertIn(model["id"], ALLOWED_MODELS)

    def test_each_offered_model_carries_a_note_for_the_picker(self):
        for model in LIVE_QA["detector_models"]:
            with self.subTest(model=model["id"]):
                self.assertTrue(model["label"])
                self.assertTrue(model["note"])

    def test_the_default_is_the_measured_best(self):
        self.assertEqual(LIVE_QA["detector_model"], "google/gemini-3.8-flash")

    def test_window_default_sits_inside_its_range(self):
        low, high = LIVE_QA["window_chunks_range"]
        self.assertEqual((low, high), (1, 5))
        self.assertTrue(low <= LIVE_QA["window_chunks"] <= high)


class LocalGateTest(unittest.TestCase):
    def test_a_question_mark_always_passes(self):
        self.assertTrue(local_gate("[10:00:00] Mic: so yeah that thing?"))

    def test_an_interrogative_opening_passes_without_a_question_mark(self):
        self.assertTrue(local_gate("[10:00:00] Mic: what is the boundary condition"))

    def test_an_interrogative_mid_sentence_passes(self):
        self.assertTrue(
            local_gate("[10:00:00] Mic: right, how do we solve it")
        )

    def test_a_plain_statement_does_not_pass(self):
        self.assertFalse(
            local_gate("[10:00:00] Mic: the value at the left edge is zero")
        )

    def test_a_wh_word_mid_clause_passes(self):
        # A 4 s chunk cuts sentences anywhere, so the question word is rarely
        # first: this exact chunk was missed in a real session.
        self.assertTrue(local_gate("[10:00:00] Mic: question um what is the law"))

    def test_an_auxiliary_mid_clause_does_not_pass(self):
        # "is" and "do" are too common mid-sentence to count anywhere.
        self.assertFalse(local_gate("[10:00:00] Mic: the answer is that we do it"))

    def test_empty_input_does_not_pass(self):
        self.assertFalse(local_gate(""))
        self.assertFalse(local_gate(None))

    def test_the_timestamp_prefix_is_not_mistaken_for_content(self):
        # "Is" only appears inside the source label here, not in the speech.
        self.assertFalse(local_gate("[10:00:00] System: the diagram was drawn"))


class IsDiscourseTest(unittest.TestCase):
    def test_blocklisted_phrases_are_discourse(self):
        self.assertTrue(is_discourse("Any questions?"))
        self.assertTrue(is_discourse("any questions so far"))
        self.assertTrue(is_discourse("Does that make sense?"))

    def test_matching_ignores_case_and_punctuation(self):
        self.assertTrue(is_discourse("ANY   QUESTIONS!!"))

    def test_a_real_question_is_not_discourse(self):
        self.assertFalse(is_discourse("What is the boundary condition?"))

    def test_a_blocklisted_phrase_inside_a_longer_question_is_not_discourse(self):
        # Substring matching would suppress a genuine question that happens to
        # contain a filler phrase, so the match is on the whole utterance.
        self.assertFalse(is_discourse("any questions about the left edge?"))

    def test_empty_is_not_discourse(self):
        self.assertFalse(is_discourse(""))
        self.assertFalse(is_discourse(None))


class ParseDetectionTest(unittest.TestCase):
    def test_valid_json_array(self):
        results = parse_detection(detection("what is x?"))
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].text, "what is x?")
        self.assertEqual(results[0].kind, "genuine")
        self.assertTrue(results[0].answerable)

    def test_a_markdown_fence_is_stripped(self):
        fenced = "```json\n" + detection("what is x?") + "\n```"
        self.assertEqual(len(parse_detection(fenced)), 1)

    def test_a_bare_fence_is_stripped(self):
        fenced = "```\n" + detection() + "\n```"
        self.assertEqual(len(parse_detection(fenced)), 1)

    def test_a_single_object_is_accepted(self):
        self.assertEqual(len(parse_detection(json.loads(detection()) and
                                             json.dumps(json.loads(detection())[0]))), 1)

    def test_malformed_json_returns_empty(self):
        for bad in ("not json", "{", "[{", "", None, "   "):
            with self.subTest(bad=bad):
                self.assertEqual(parse_detection(bad), [])

    def test_an_empty_array_returns_empty(self):
        self.assertEqual(parse_detection("[]"), [])

    def test_entries_without_text_are_dropped(self):
        self.assertEqual(parse_detection('[{"kind": "genuine"}]'), [])

    def test_a_non_numeric_confidence_becomes_zero(self):
        results = parse_detection('[{"text": "x?", "confidence": "high"}]')
        self.assertEqual(results[0].confidence, 0.0)

    def test_confidence_is_clamped(self):
        self.assertEqual(parse_detection('[{"text":"x?","confidence":5}]')[0].confidence, 1.0)
        self.assertEqual(parse_detection('[{"text":"x?","confidence":-2}]')[0].confidence, 0.0)

    def test_an_unknown_asker_falls_back_to_system(self):
        self.assertEqual(parse_detection('[{"text":"x?","asker":"bob"}]')[0].asker,
                         "system")


class DedupeTest(unittest.TestCase):
    def _q(self, text):
        return DetectedQuestion(text, "mic", "genuine", True, True, 1.0)

    def test_a_new_question_passes_and_is_remembered(self):
        seen = set()
        self.assertTrue(dedupe(self._q("What is x?"), seen))
        self.assertFalse(dedupe(self._q("What is x?"), seen))

    def test_normalization_catches_near_repeats(self):
        seen = set()
        dedupe(self._q("What is X?"), seen)
        self.assertFalse(dedupe(self._q("what is x"), seen))

    def test_the_set_never_expires_so_a_stale_refire_is_caught(self):
        seen = set()
        dedupe(self._q("What is x?"), seen)
        for _ in range(100):
            dedupe(self._q("something else " + str(_)), seen)
        self.assertFalse(dedupe(self._q("What is x?"), seen))

    def test_a_different_question_passes(self):
        seen = set()
        dedupe(self._q("What is x?"), seen)
        self.assertTrue(dedupe(self._q("What is y?"), seen))

    def test_an_empty_question_never_passes(self):
        self.assertFalse(dedupe(self._q("   "), set()))


class WindowIsAllDiscourseTest(unittest.TestCase):
    def test_a_window_of_only_filler_is_discourse(self):
        self.assertTrue(window_is_all_discourse(
            [rec("any questions", "mic", 0), rec("right", "system", 5)]
        ))

    def test_one_real_question_keeps_the_window_worth_a_call(self):
        self.assertFalse(window_is_all_discourse(
            [rec("any questions", "mic", 0),
             rec("what is the boundary condition?", "system", 5)]
        ))

    def test_an_empty_window_is_not_discourse(self):
        self.assertFalse(window_is_all_discourse([]))
        self.assertFalse(window_is_all_discourse([rec("   ", "mic", 0)]))


class WindowAssemblyTest(unittest.TestCase):
    def test_window_text_carries_source_and_time(self):
        text = build_window_text([rec("hello", "mic", 0), rec("hi", "system", 5)])
        self.assertEqual(text, "[10:00:00] Mic: hello\n[10:00:05] System: hi")

    def test_blank_records_are_skipped(self):
        self.assertEqual(build_window_text([rec("  ", "mic", 0)]), "")


class DetectorWindowTest(unittest.TestCase):
    """New chunks and their lead-in, driven synchronously through _consume."""

    def _detector(self, window_chunks, client=None, clock=None):
        return QuestionDetector(
            client or _FakeClient(),
            lambda candidates: None,
            config={"window_chunks": window_chunks},
            clock=clock or _Clock(),
        )

    def _sent(self, client, call=-1):
        return client.calls[call]["messages"][1]["content"]

    def _part(self, excerpt, name):
        """The lines under ``name:`` in a detector excerpt."""
        parts = dict(p.split(":\n", 1) for p in excerpt.split("\n\n"))
        return parts.get(name, "").splitlines()

    def test_the_first_call_has_no_earlier_part(self):
        client = _FakeClient()
        self._detector(1, client)._consume((0, rec("what is 0?", "mic", 0)))
        self.assertEqual(self._sent(client), "New:\n[10:00:00] Mic: what is 0?")

    def test_chunks_inside_the_interval_are_sent_with_the_next_call(self):
        client = _FakeClient()
        clock = _Clock()
        d = self._detector(1, client, clock)
        for i in range(3):
            d._consume((i, rec(f"what is {i}?", "mic", i * 5)))
            clock.advance(5)
        clock.advance(15)
        d._check_pending()
        self.assertEqual(len(client.calls), 2)
        self.assertEqual(self._part(self._sent(client), "New"),
                         ["[10:00:05] Mic: what is 1?",
                          "[10:00:10] Mic: what is 2?"])

    def test_the_earlier_part_is_the_last_window_chunks_checked(self):
        client = _FakeClient()
        clock = _Clock()
        d = self._detector(2, client, clock)
        for i in range(4):
            clock.advance(60)
            d._consume((i, rec(f"what is {i}?", "mic", i * 5)))
        excerpt = self._sent(client)
        self.assertEqual(self._part(excerpt, "Earlier"),
                         ["[10:00:05] Mic: what is 1?",
                          "[10:00:10] Mic: what is 2?"])
        self.assertEqual(self._part(excerpt, "New"),
                         ["[10:00:15] Mic: what is 3?"])

    def test_out_of_range_window_sizes_are_clamped(self):
        self.assertEqual(self._detector(99).window_chunks, 5)
        self.assertEqual(self._detector(0).window_chunks, 1)
        self.assertEqual(self._detector("nope").window_chunks, 1)

    def test_narrowing_the_window_takes_effect_immediately(self):
        client = _FakeClient()
        clock = _Clock()
        d = self._detector(5, client, clock)
        for i in range(5):
            clock.advance(60)
            d._consume((i, rec(f"what is {i}?", "mic", i * 5)))
        d.set_window_chunks(2)
        clock.advance(60)
        d._consume((5, rec("what is 5?", "mic", 25)))
        self.assertEqual(len(self._part(self._sent(client), "Earlier")), 2)

    def test_a_question_split_across_calls_is_still_sent(self):
        # The detector skips "What is" as unfinished; the chunk that finishes
        # it carries no question signal of its own and must not be gated out.
        client = _FakeClient()
        clock = _Clock()
        d = self._detector(1, client, clock)
        d._consume((0, rec("for you. What is", "mic", 0)))
        clock.advance(15)
        d._consume((1, rec("one of the biggest bands ever.", "mic", 5)))
        self.assertEqual(len(client.calls), 2)
        self.assertEqual(self._part(self._sent(client), "Earlier"),
                         ["[10:00:00] Mic: for you. What is"])

    def test_the_worker_sleeps_until_pending_chunks_are_due(self):
        clock = _Clock()
        d = self._detector(1, clock=clock)
        self.assertIsNone(d._seconds_until_due())  # nothing pending
        d._consume((0, rec("what is 0?", "mic", 0)))
        clock.advance(4)
        d._consume((1, rec("what is 1?", "mic", 5)))
        self.assertEqual(d._seconds_until_due(), 11)


class DetectorSpendTest(unittest.TestCase):
    def setUp(self):
        self.clock = _Clock()
        self.client = _FakeClient()
        self.emitted = []
        self.d = QuestionDetector(
            self.client, self.emitted.extend,
            config={"window_chunks": 1, "min_detect_interval_seconds": 15},
            clock=self.clock,
        )

    def _feed(self, text="what is x?"):
        self.d._consume((0, rec(text, "mic", 0)))

    def test_at_most_one_call_per_interval(self):
        for _ in range(5):
            self.clock.advance(1)
            self._feed()
        self.assertEqual(len(self.client.calls), 1)

    def test_a_call_is_made_again_once_the_interval_elapses(self):
        self._feed()
        self.clock.advance(15)
        self._feed()
        self.assertEqual(len(self.client.calls), 2)

    def test_the_gate_suppresses_a_call_for_a_plain_statement(self):
        self._feed("the value at the left edge is zero")
        self.assertEqual(self.client.calls, [])

    def test_a_gated_out_chunk_does_not_start_the_interval(self):
        # It used to: a statement spent the slot and the question right after
        # it was dropped.
        self._feed("the value at the left edge is zero")
        self.clock.advance(1)
        self._feed()
        self.assertEqual(len(self.client.calls), 1)

    def test_a_question_followed_by_silence_is_checked_when_due(self):
        self._feed()
        self.clock.advance(4)
        self._feed("what is y?")  # inside the interval: waits
        self.assertEqual(len(self.client.calls), 1)
        self.clock.advance(11)
        self.d._check_pending()  # what the worker's wake-up does
        self.assertEqual(len(self.client.calls), 2)

    def test_discourse_is_suppressed_without_a_call(self):
        self._feed("any questions")
        self.assertEqual(self.client.calls, [])

    def test_the_call_uses_the_detector_model_at_temperature_zero(self):
        self._feed()
        call = self.client.calls[0]
        self.assertEqual(call["model"], LIVE_QA["detector_model"])
        self.assertEqual(call["temperature"], 0)
        self.assertEqual(call["messages"][0]["content"], DETECTOR_SYSTEM_PROMPT)

    def test_switching_the_detector_model_applies_to_the_next_call(self):
        self._feed()
        self.d.set_detector_model("qwen/qwen3-14b")
        self.clock.advance(15)
        self._feed("what is y?")
        self.assertEqual(self.client.calls[-1]["model"], "qwen/qwen3-14b")

    def test_calls_and_cost_are_counted(self):
        self._feed()
        summary = self.d.spend_summary()
        self.assertEqual(summary["calls"], 1)
        self.assertGreater(summary["estimated_cost"], 0)

    def test_a_failed_call_costs_nothing_and_is_swallowed(self):
        d = QuestionDetector(
            _FakeClient(raises=RuntimeError("connection reset")),
            self.emitted.extend,
            config={"window_chunks": 1}, clock=self.clock,
        )
        d._consume((0, rec("what is x?", "mic", 0)))  # must not raise
        self.assertEqual(d.spend_summary()["calls"], 0)
        self.assertEqual(d.spend_summary()["estimated_cost"], 0.0)
        self.assertEqual(self.emitted, [])

    def test_malformed_output_emits_nothing_and_does_not_raise(self):
        d = QuestionDetector(
            _FakeClient(replies=["not json at all"]), self.emitted.extend,
            config={"window_chunks": 1}, clock=self.clock,
        )
        d._consume((0, rec("what is x?", "mic", 0)))
        self.assertEqual(self.emitted, [])
        self.assertEqual(d.spend_summary()["calls"], 1)

    def test_a_raising_callback_does_not_break_the_detector(self):
        def boom(_candidates):
            raise ValueError("ui exploded")

        d = QuestionDetector(
            _FakeClient(replies=[detection()]), boom,
            config={"window_chunks": 1}, clock=self.clock,
        )
        d._consume((0, rec("what is x?", "mic", 0)))  # must not raise


class DetectorFilteringTest(unittest.TestCase):
    def _run(self, reply, **config):
        emitted = []
        cfg = {"window_chunks": 1}
        cfg.update(config)
        d = QuestionDetector(_FakeClient(replies=[reply]), emitted.extend,
                             config=cfg, clock=_Clock())
        d._consume((7, rec("what is x?", "mic", 0)))
        return d, emitted

    def test_a_confident_genuine_question_is_emitted(self):
        _, emitted = self._run(detection(confidence=0.95))
        self.assertEqual(len(emitted), 1)
        self.assertEqual(emitted[0].kind, "genuine")

    def test_a_low_confidence_candidate_is_dropped(self):
        _, emitted = self._run(detection(confidence=0.3), min_confidence=0.8)
        self.assertEqual(emitted, [])

    def test_discourse_labelled_by_the_model_is_dropped(self):
        _, emitted = self._run(detection(text="right?", kind="discourse"))
        self.assertEqual(emitted, [])

    def test_a_blocklisted_phrase_is_dropped_even_if_labelled_genuine(self):
        _, emitted = self._run(detection(text="Any questions?", kind="genuine"))
        self.assertEqual(emitted, [])

    def test_rhetorical_is_passed_through_for_the_caller_to_filter(self):
        # BU114 emits neutral labels; the mode policy is BU115's job.
        _, emitted = self._run(detection(kind="rhetorical"))
        self.assertEqual(len(emitted), 1)
        self.assertEqual(emitted[0].kind, "rhetorical")

    def test_the_originating_record_indices_are_carried(self):
        _, emitted = self._run(detection())
        self.assertEqual(emitted[0].record_indices, [7])

    def test_a_detection_is_timestamped(self):
        _, emitted = self._run(detection())
        self.assertIsInstance(emitted[0].detected_at, datetime)

    def test_a_repeat_across_windows_is_deduplicated(self):
        emitted = []
        clock = _Clock()
        d = QuestionDetector(
            _FakeClient(replies=[detection("What is x?"), detection("what is x")]),
            emitted.extend, config={"window_chunks": 1}, clock=clock,
        )
        d._consume((0, rec("what is x?", "mic", 0)))
        clock.advance(60)
        d._consume((1, rec("what is x?", "mic", 5)))
        self.assertEqual(len(emitted), 1)


class DetectorCapTest(unittest.TestCase):
    def test_detection_stops_at_the_session_cap(self):
        clock = _Clock()
        emitted = []
        replies = [detection(f"question number {i}?") for i in range(5)]
        client = _FakeClient(replies=replies)
        d = QuestionDetector(client, emitted.extend,
                             config={"window_chunks": 1,
                                     "max_detections_per_session": 2},
                             clock=clock)
        for i in range(5):
            clock.advance(60)
            d._consume((i, rec(f"question number {i}?", "mic", i * 5)))

        self.assertTrue(d.spend_summary()["cap_reached"])
        self.assertEqual(len(emitted), 2)
        self.assertEqual(len(client.calls), 2)

    def test_feed_stops_queueing_once_the_cap_is_reached(self):
        d = QuestionDetector(_FakeClient(), lambda c: None,
                             config={"window_chunks": 1}, clock=_Clock())
        d.cap_reached = True
        d.feed(rec("what is x?", "mic", 0), index=0)
        self.assertTrue(d._queue.empty())


class DetectorThreadTest(unittest.TestCase):
    def test_start_and_stop_release_the_worker_thread(self):
        d = QuestionDetector(_FakeClient(), lambda c: None, clock=_Clock())
        self.assertFalse(d.is_running)
        d.start()
        self.assertTrue(d.is_running)
        d.stop()
        self.assertFalse(d.is_running)

    def test_stop_is_safe_without_a_start(self):
        QuestionDetector(_FakeClient(), lambda c: None).stop()  # must not raise

    def test_start_twice_does_not_spawn_a_second_thread(self):
        d = QuestionDetector(_FakeClient(), lambda c: None, clock=_Clock())
        d.start()
        first = d._thread
        d.start()
        self.assertIs(d._thread, first)
        d.stop()

    def test_a_fed_record_is_processed_off_the_calling_thread(self):
        done = threading.Event()
        seen_thread = []

        def on_candidates(candidates):
            seen_thread.append(threading.current_thread().name)
            done.set()

        d = QuestionDetector(_FakeClient(replies=[detection()]), on_candidates,
                             config={"window_chunks": 1}, clock=_Clock())
        d.start()
        try:
            d.feed(rec("what is x?", "mic", 0), index=0)
            self.assertTrue(done.wait(timeout=5), "detector never emitted")
            self.assertEqual(seen_thread, ["QuestionDetector"])
        finally:
            d.stop()

    def test_feed_never_raises_even_when_the_detector_is_broken(self):
        d = QuestionDetector(_FakeClient(), lambda c: None, clock=_Clock())
        d._queue = None  # simulate a torn-down detector
        d.feed(rec("what is x?", "mic", 0), index=0)  # must not raise


class NormalizeTest(unittest.TestCase):
    def test_punctuation_and_case_are_removed(self):
        self.assertEqual(normalize_question("  What  is X?! "), "what is x")

    def test_empty_stays_empty(self):
        self.assertEqual(normalize_question(""), "")
        self.assertEqual(normalize_question(None), "")


if __name__ == "__main__":
    unittest.main()
