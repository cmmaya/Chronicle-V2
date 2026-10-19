"""BU108: screenshot-aware Specific Session answers."""
import json
import unittest
from unittest.mock import patch

from src.assistant.context_models import ScreenshotReference, render_screenshot_section
from src.assistant.response_contract import RESPONSE_CONTRACT
from src.assistant.screenshot_contract import (
    CITATION_PREFIX,
    SCREENSHOT_CONTRACT,
    parse_screenshot_refs,
)
import src.assistant.service as service_module
from src.assistant.service import AssistantAnswerService
from src.assistant.session_resolver import ResolutionResult, ScopeResolution

# Patch the module object this file imported: test_openrouter_client.py purges
# src.* from sys.modules, so patching by dotted name could hit a fresh copy.

T0 = 1_700_000_000


class FakeDb:
    def __init__(self, screenshots=None):
        self.screenshots = screenshots or []
        self.messages = []

    def get_session(self, session_id):
        return {"id": session_id, "name": "Design review", "start_time": T0}

    def get_summaries(self, session_id):
        return []

    def get_transcripts(self, session_id):
        return []

    def get_screenshots(self, session_id):
        return [dict(r) for r in self.screenshots]

    def search_rag_fts(self, query, limit=20, session_id=None):
        return []

    def search_everything(self, *args, **kwargs):
        return []

    def create_conversation(self, session_id=None, title=None):
        return 1

    def add_message(self, conversation_id, role, content):
        self.messages.append({"conversation_id": conversation_id, "role": role, "content": content})

    def get_messages(self, conversation_id):
        return [m for m in self.messages if m["conversation_id"] == conversation_id]


class FakeClient:
    def __init__(self, reply):
        self.reply = reply
        self.calls = []

    def chat(self, messages, model=None, temperature=None):
        self.calls.append(messages)
        return self.reply


SCREENSHOTS = [
    {"id": 41, "timestamp": T0 + 60, "filepath": "C:/s/41.png",
     "preview_description": "Figma mockup of the checkout page",
     "ai_summary": "Checkout mockup with the new coupon field",
     "visible_text": json.dumps(["Apply coupon", "Total $49"]),
     "keywords": json.dumps(["checkout", "coupon"])},
    {"id": 42, "timestamp": T0 + 600, "filepath": "C:/s/42.png",
     "preview_description": "Sprint burndown chart"},
]


def _ask(reply, screenshots=SCREENSHOTS, scope=ScopeResolution.SELECTED_SESSION,
         question="what did the coupon field look like?"):
    db = FakeDb(screenshots)
    client = FakeClient(reply)
    service = AssistantAnswerService(db=db)
    with patch.object(service_module, "get_selected_model", return_value="m"), \
         patch.object(service_module, "OpenRouterClient", return_value=client), \
         patch.object(service._resolver, "resolve", return_value=ResolutionResult(
             scope=scope, session_ids=[1] if scope != ScopeResolution.ALL_SESSIONS else [])), \
         patch.object(service_module, "route_sessions", return_value=[]):
        response = service.ask(question)
    # System message (instructions, background, contracts) plus the final
    # user message that carries the per-question context.
    sent = client.calls[-1]
    return response, sent[0]["content"] + "\n" + sent[-1]["content"], db


class RenderingTest(unittest.TestCase):
    def test_preview_and_full_tiers(self):
        full = ScreenshotReference(1, "S", T0, "C:/x.png", screenshot_id=3, preview="Login form",
                                   tier="full", ai_summary="Login failing",
                                   visible_text=["Error 500"], keywords=["login"])
        preview = ScreenshotReference(1, "S", T0, "C:/y.png", screenshot_id=4, preview="Chart",
                                      description="burn chart note")
        lines = "\n".join(render_screenshot_section([preview, full]))
        self.assertLess(lines.index("#3"), lines.index("#4"))  # full cards first
        self.assertIn("Summary: Login failing", lines)
        self.assertIn("Visible text: Error 500", lines)
        self.assertIn("User note: burn chart note", lines)
        self.assertNotIn("C:/", lines)

    def test_empty_section(self):
        self.assertEqual(render_screenshot_section([]), [])


class ParseRefsTest(unittest.TestCase):
    def test_valid_single(self):
        answer, ids = parse_screenshot_refs(f"It had a coupon field.\n{CITATION_PREFIX} #41", {41})
        self.assertEqual(ids, [41])
        self.assertTrue(answer.endswith("#41"))

    def test_multiple_and_dedupe(self):
        _, ids = parse_screenshot_refs(f"x\n{CITATION_PREFIX} #41, #42, #41", {41, 42})
        self.assertEqual(ids, [41, 42])

    def test_hallucinated_id_is_stripped(self):
        answer, ids = parse_screenshot_refs(f"Answer.\n{CITATION_PREFIX} #99", {41})
        self.assertEqual(ids, [])
        self.assertEqual(answer, "Answer.")

    def test_partially_valid_line_is_rewritten(self):
        answer, ids = parse_screenshot_refs(f"A.\n{CITATION_PREFIX} #99, #41", {41})
        self.assertEqual(ids, [41])
        self.assertEqual(answer.splitlines()[-1], f"{CITATION_PREFIX} #41")

    def test_spanish_line_still_parses(self):
        answer, ids = parse_screenshot_refs(
            "El cupón estaba abajo.\nLa información podría estar en la captura: #41\n", {41})
        self.assertEqual(ids, [41])
        self.assertIn("captura: #41", answer)

    def test_no_line(self):
        self.assertEqual(parse_screenshot_refs("Plain #3 answer", {3}), ("Plain #3 answer", []))


class ServiceTest(unittest.TestCase):
    def test_specific_session_prompt_and_citation(self):
        reply = f"It showed an 'Apply coupon' field.\n{CITATION_PREFIX} #41"
        response, system_prompt, db = _ask(reply)
        self.assertIn(SCREENSHOT_CONTRACT, system_prompt)
        self.assertIn("[Screenshot #41", system_prompt)
        self.assertIn("Visible text: Apply coupon | Total $49", system_prompt)
        self.assertIn("[Screenshot #42", system_prompt)
        self.assertNotIn("C:/s/", system_prompt)
        self.assertEqual(response.screenshot_refs, [41])
        self.assertEqual(db.messages[-1]["content"], reply)

    def test_hallucinated_citation_not_persisted(self):
        response, _, db = _ask(f"Not sure.\n{CITATION_PREFIX} #7")
        self.assertEqual(response.screenshot_refs, [])
        self.assertEqual(response.answer, "Not sure.")
        self.assertEqual(db.messages[-1]["content"], "Not sure.")

    def test_no_contract_without_screenshots(self):
        response, system_prompt, _ = _ask("Answer.", screenshots=[])
        self.assertNotIn(SCREENSHOT_CONTRACT, system_prompt)
        self.assertEqual(response.screenshot_refs, [])

    def test_repeated_text_first_question_context_last(self):
        """The system message is the same for every question of a session (so
        the provider can cache it); per-question context rides with the question."""
        db = FakeDb(SCREENSHOTS)
        db.get_summaries = lambda session_id: [
            {"summary_type": "full", "content": "1. Overview\nCheckout redesign review."}]
        db.messages = [{"conversation_id": 1, "role": "user", "content": "earlier q"},
                       {"conversation_id": 1, "role": "assistant", "content": "earlier a"}]
        client = FakeClient("Answer.")
        service = AssistantAnswerService(db=db)
        with patch.object(service_module, "get_selected_model", return_value="m"), \
             patch.object(service_module, "OpenRouterClient", return_value=client), \
             patch.object(service._resolver, "resolve", return_value=ResolutionResult(
                 scope=ScopeResolution.SELECTED_SESSION, session_ids=[1])):
            service.ask("what did the coupon field look like?", conversation_id=1)
            service.ask("show the burndown chart", conversation_id=1)

        first, second = client.calls
        self.assertEqual(first[0], second[0])
        self.assertIn("Checkout redesign review.", first[0]["content"])
        self.assertIn(SCREENSHOT_CONTRACT, first[0]["content"])
        self.assertNotIn("[Screenshot #41", first[0]["content"])
        self.assertEqual([m["content"] for m in first[1:3]], ["earlier q", "earlier a"])
        self.assertIn("[Screenshot #41", first[-1]["content"])
        self.assertTrue(first[-1]["content"].endswith(
            "Question: what did the coupon field look like?"))
        # Only the bare question is stored, never the context sent with it.
        self.assertIn("show the burndown chart", [m["content"] for m in db.messages])
        self.assertFalse(any("Context for this question" in m["content"] for m in db.messages))

    def test_any_session_untouched(self):
        response, system_prompt, _ = _ask(
            f"Answer.\n{CITATION_PREFIX} #41", scope=ScopeResolution.ALL_SESSIONS)
        self.assertNotIn(SCREENSHOT_CONTRACT, system_prompt)
        self.assertIn(RESPONSE_CONTRACT, system_prompt)
        self.assertEqual(response.screenshot_refs, [])


if __name__ == "__main__":
    unittest.main()
