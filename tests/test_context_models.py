"""Unit tests for context models."""

import unittest
from src.assistant.context_models import (
    SessionCandidate,
    TranscriptExcerpt,
    SummaryExcerpt,
    ScreenshotReference,
    AssistantContext,
)


class TestSessionCandidate(unittest.TestCase):
    def test_construct(self):
        candidate = SessionCandidate(
            session_id=1,
            session_name="Team Standup",
            start_timestamp=1700000000,
            relevance_score=0.95,
        )
        self.assertEqual(candidate.session_id, 1)
        self.assertEqual(candidate.session_name, "Team Standup")
        self.assertEqual(candidate.relevance_score, 0.95)

    def test_default_relevance_score(self):
        candidate = SessionCandidate(
            session_id=2,
            session_name="Design Review",
            start_timestamp=1700000000,
        )
        self.assertEqual(candidate.relevance_score, 1.0)

    def test_to_prompt_text_format(self):
        candidate = SessionCandidate(
            session_id=1,
            session_name="Team Standup",
            start_timestamp=1700000000,
        )
        text = candidate.to_prompt_text()
        self.assertIn("Session 1", text)
        self.assertIn("Team Standup", text)


class TestTranscriptExcerpt(unittest.TestCase):
    def test_construct_microphone(self):
        excerpt = TranscriptExcerpt(
            session_id=1,
            session_name="Team Standup",
            timestamp=1700000100,
            source="microphone",
            text="Let's discuss the roadmap.",
        )
        self.assertEqual(excerpt.source, "microphone")
        self.assertEqual(excerpt.text, "Let's discuss the roadmap.")

    def test_construct_system(self):
        excerpt = TranscriptExcerpt(
            session_id=1,
            session_name="Team Standup",
            timestamp=1700000100,
            source="system",
            text="Projector connected.",
        )
        self.assertEqual(excerpt.source, "system")

    def test_to_prompt_text_mic(self):
        excerpt = TranscriptExcerpt(
            session_id=1,
            session_name="Team Standup",
            timestamp=1700000100,
            source="microphone",
            text="Hello everyone.",
        )
        text = excerpt.to_prompt_text()
        self.assertIn("(Mic):", text)
        self.assertIn("Hello everyone.", text)


class TestSummaryExcerpt(unittest.TestCase):
    def test_construct(self):
        excerpt = SummaryExcerpt(
            session_id=1,
            session_name="Team Standup",
            summary_type="action_items",
            content="- Review PR #123\n- Update documentation",
        )
        self.assertEqual(excerpt.summary_type, "action_items")

    def test_to_prompt_text_format(self):
        excerpt = SummaryExcerpt(
            session_id=1,
            session_name="Team Standup",
            summary_type="key_points",
            content="Discussed project timeline.",
        )
        text = excerpt.to_prompt_text()
        self.assertIn("[Summary - key_points]:", text)
        self.assertIn("Discussed project timeline.", text)


class TestScreenshotReference(unittest.TestCase):
    def test_construct(self):
        ref = ScreenshotReference(
            session_id=1,
            session_name="Team Standup",
            timestamp=1700000050,
            filepath="/sessions/1/screenshots/screen_001.png",
        )
        self.assertIsNotNone(ref.filepath)

    def test_construct_with_description(self):
        ref = ScreenshotReference(
            session_id=1,
            session_name="Team Standup",
            timestamp=1700000050,
            filepath="/sessions/1/screenshots/screen_001.png",
            description="Whiteboard diagram",
        )
        self.assertEqual(ref.description, "Whiteboard diagram")

    def test_to_prompt_text_without_description(self):
        ref = ScreenshotReference(
            session_id=1,
            session_name="Team Standup",
            timestamp=1700000050,
            filepath="/sessions/1/screenshots/screen_001.png",
        )
        text = ref.to_prompt_text()
        self.assertIn("Screenshot at", text)
        self.assertIn("/sessions/1/screenshots/screen_001.png", text)

    def test_to_prompt_text_with_description(self):
        ref = ScreenshotReference(
            session_id=1,
            session_name="Team Standup",
            timestamp=1700000050,
            filepath="/sessions/1/screenshots/screen_001.png",
            description="Whiteboard",
        )
        text = ref.to_prompt_text()
        self.assertIn("- Whiteboard", text)


class TestAssistantContext(unittest.TestCase):
    def test_construct_empty(self):
        ctx = AssistantContext(query="What was discussed?")
        self.assertEqual(ctx.query, "What was discussed?")
        self.assertTrue(ctx.is_empty())

    def test_add_sessions(self):
        ctx = AssistantContext(query="What was discussed?")
        ctx.sessions.append(SessionCandidate(
            session_id=1,
            session_name="Standup",
            start_timestamp=1700000000,
        ))
        self.assertFalse(ctx.is_empty())

    def test_to_prompt_text_empty(self):
        ctx = AssistantContext(query="What was discussed?")
        text = ctx.to_prompt_text()
        self.assertEqual(text, "(No context found)")

    def test_to_prompt_text_with_sessions_only(self):
        ctx = AssistantContext(query="What was discussed?")
        ctx.sessions.append(SessionCandidate(
            session_id=1,
            session_name="Standup",
            start_timestamp=1700000000,
        ))
        text = ctx.to_prompt_text()
        self.assertIn("## Relevant Sessions", text)
        self.assertIn("Session 1", text)

    def test_to_prompt_text_with_transcripts(self):
        ctx = AssistantContext(query="What was discussed?")
        ctx.transcripts.append(TranscriptExcerpt(
            session_id=1,
            session_name="Standup",
            timestamp=1700000100,
            source="microphone",
            text="Let's review the metrics.",
        ))
        text = ctx.to_prompt_text()
        self.assertIn("## Transcripts", text)
        self.assertIn("Let's review the metrics.", text)

    def test_to_prompt_text_with_summaries(self):
        ctx = AssistantContext(query="What was discussed?")
        ctx.summaries.append(SummaryExcerpt(
            session_id=1,
            session_name="Standup",
            summary_type="action_items",
            content="- Send weekly report",
        ))
        text = ctx.to_prompt_text()
        self.assertIn("## Summaries", text)
        self.assertIn("- Send weekly report", text)

    def test_to_prompt_text_with_screenshots(self):
        ctx = AssistantContext(query="Show me the whiteboard.")
        ctx.screenshots.append(ScreenshotReference(
            session_id=1,
            session_name="Standup",
            timestamp=1700000050,
            filepath="/sessions/1/screenshots/screen_001.png",
            description="Whiteboard",
        ))
        text = ctx.to_prompt_text()
        self.assertIn("## Screenshots", text)
        self.assertIn("Whiteboard", text)

    def test_to_prompt_text_full_context(self):
        ctx = AssistantContext(query="Tell me about the meeting.")
        ctx.sessions.append(SessionCandidate(
            session_id=1,
            session_name="Standup",
            start_timestamp=1700000000,
        ))
        ctx.transcripts.append(TranscriptExcerpt(
            session_id=1,
            session_name="Standup",
            timestamp=1700000100,
            source="microphone",
            text="Hello team.",
        ))
        ctx.summaries.append(SummaryExcerpt(
            session_id=1,
            session_name="Standup",
            summary_type="key_points",
            content="Product roadmap discussed.",
        ))
        ctx.screenshots.append(ScreenshotReference(
            session_id=1,
            session_name="Standup",
            timestamp=1700000050,
            filepath="/sessions/1/screenshots/screen_001.png",
        ))
        text = ctx.to_prompt_text()
        self.assertIn("## Relevant Sessions", text)
        self.assertIn("## Transcripts", text)
        self.assertIn("## Summaries", text)
        self.assertIn("## Screenshots", text)


if __name__ == "__main__":
    unittest.main()