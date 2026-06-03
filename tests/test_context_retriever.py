"""Unit tests for AssistantContextRetriever."""

import unittest
from src.assistant.context import (
    AssistantContextRetriever,
    DEFAULT_KEYWORD_MATCHES,
    MAX_TRANSCRIPT_LENGTH,
)


class MockDatabase:
    """Mock database for testing AssistantContextRetriever."""

    def __init__(self, sessions=None, summaries=None, transcripts=None, screenshots=None):
        self._sessions = sessions or {}
        self._summaries = summaries or {}
        self._transcripts = transcripts or {}
        self._screenshots = screenshots or {}

    def get_session(self, session_id: int):
        return self._sessions.get(session_id, {})

    def get_summaries(self, session_id: int):
        return self._summaries.get(session_id, [])

    def get_transcripts(self, session_id: int):
        return self._transcripts.get(session_id, [])

    def get_screenshots(self, session_id: int):
        return self._screenshots.get(session_id, [])


class TestAssistantContextRetriever(unittest.TestCase):
    """Tests for AssistantContextRetriever class."""

    def test_build_session_context_returns_context(self):
        """Test that build_session_context returns an AssistantContext."""
        db = MockDatabase()
        retriever = AssistantContextRetriever(db)

        context = retriever.build_session_context(1, "test question")

        self.assertEqual(context.query, "test question")
        self.assertEqual(len(context.sessions), 1)
        self.assertEqual(context.sessions[0].session_id, 1)

    def test_empty_database_returns_empty_context(self):
        """Test empty database returns empty but valid context."""
        db = MockDatabase()
        retriever = AssistantContextRetriever(db)

        context = retriever.build_session_context(999, "question")

        self.assertFalse(context.is_empty())  # Has session info
        self.assertEqual(len(context.summaries), 0)
        self.assertEqual(len(context.transcripts), 0)
        self.assertEqual(len(context.screenshots), 0)

    def test_includes_summary_when_available(self):
        """Test that summary is included when available."""
        db = MockDatabase(
            summaries={1: [{"summary_type": "key_points", "content": "Discussed roadmap"}]}
        )
        retriever = AssistantContextRetriever(db)

        context = retriever.build_session_context(1, "what was discussed")

        self.assertEqual(len(context.summaries), 1)
        self.assertEqual(context.summaries[0].content, "Discussed roadmap")

    def test_transcript_filtering_with_keywords(self):
        """Test transcript filtering limits prompt size."""
        db = MockDatabase(
            transcripts={
                1: [
                    {"timestamp": 1000, "source": "microphone", "text": "This is about project alpha"},
                    {"timestamp": 2000, "source": "microphone", "text": "Random conversation about weather"},
                    {"timestamp": 3000, "source": "microphone", "text": "Alpha and beta discussed in meeting"},
                    {"timestamp": 4000, "source": "system", "text": "alpha mentioned in presentation"},
                    {"timestamp": 5000, "source": "microphone", "text": "unrelated content"},
                ]
            }
        )
        retriever = AssistantContextRetriever(db)

        # Ask about "alpha" - should filter to transcripts with "alpha"
        context = retriever.build_session_context(1, "tell me about alpha")

        # Should have limited results
        self.assertLessEqual(len(context.transcripts), DEFAULT_KEYWORD_MATCHES)

    def test_transcript_filtering_no_keywords_returns_recent(self):
        """Test transcript filtering returns recent when no keywords."""
        transcripts = [
            {"timestamp": i * 1000, "source": "microphone", "text": f"Transcript {i}"}
            for i in range(10)
        ]
        db = MockDatabase(transcripts={1: transcripts})
        retriever = AssistantContextRetriever(db)

        context = retriever.build_session_context(1, "hello")

        # Should return recent transcripts (no keywords to filter by)
        self.assertGreater(len(context.transcripts), 0)

    def test_long_transcripts_truncated(self):
        """Test that long transcripts are truncated."""
        long_text = "A" * 1000  # Very long transcript
        db = MockDatabase(
            transcripts={1: [{"timestamp": 1000, "source": "microphone", "text": long_text}]}
        )
        retriever = AssistantContextRetriever(db)

        context = retriever.build_session_context(1, "test")

        # Transcript should be truncated (max 500 chars + "..." if truncated)
        # Since 1000 > 500, it gets truncated to 500 + 3 = 503
        self.assertLessEqual(len(context.transcripts[0].text), MAX_TRANSCRIPT_LENGTH + 3)

    def test_screenshots_near_transcripts(self):
        """Test screenshots near relevant transcript timestamps."""
        db = MockDatabase(
            transcripts={1: [{"timestamp": 1000, "source": "microphone", "text": "test"}]},
            screenshots={
                1: [
                    {"timestamp": 1010, "filepath": "/screen1.png"},  # Within 60s
                    {"timestamp": 5000, "filepath": "/screen2.png"},  # Not within 60s
                ]
            },
        )
        retriever = AssistantContextRetriever(db)

        context = retriever.build_session_context(1, "test")

        # Should include screenshot near transcript (within 60s)
        self.assertEqual(len(context.screenshots), 1)
        self.assertEqual(context.screenshots[0].filepath, "/screen1.png")

    def test_no_transcripts_means_no_screenshot_filtering(self):
        """Test that no transcripts means no nearby screenshots."""
        db = MockDatabase(
            transcripts={1: []},
            screenshots={1: [{"timestamp": 1000, "filepath": "/screen1.png"}]},
        )
        retriever = AssistantContextRetriever(db)

        context = retriever.build_session_context(1, "test")

        # No screenshots without transcripts to match against
        self.assertEqual(len(context.screenshots), 0)

    def test_session_not_found_uses_default_name(self):
        """Test that missing session uses default name."""
        db = MockDatabase()  # No sessions
        retriever = AssistantContextRetriever(db)

        context = retriever.build_session_context(999, "question")

        self.assertEqual(context.sessions[0].session_name, "Session 999")

    def test_keyword_extraction_filters_stop_words(self):
        """Test that keyword extraction filters common stop words."""
        db = MockDatabase()
        retriever = AssistantContextRetriever(db)

        keywords = retriever._extract_keywords("the and for that this with are was")

        # Stop words should be filtered out
        self.assertEqual(len(keywords), 0)

    def test_keyword_extraction_includes_content_words(self):
        """Test that content words are extracted as keywords."""
        db = MockDatabase()
        retriever = AssistantContextRetriever(db)

        keywords = retriever._extract_keywords("What was discussed about the project roadmap")

        # Should include content words > 3 chars
        self.assertIn("discussed", keywords)
        self.assertIn("project", keywords)
        self.assertIn("roadmap", keywords)

    def test_missing_get_transcripts_raises_error(self):
        """Test that missing get_transcripts raises clear TODO error."""
        class BrokenDB:
            def get_session(self, session_id):
                return {"name": "Test", "start_time": 1000}
            def get_summaries(self, session_id):
                return []
            def get_screenshots(self, session_id):
                return []
            # No get_transcripts method

        db = BrokenDB()
        retriever = AssistantContextRetriever(db)

        with self.assertRaises(NotImplementedError) as ctx:
            retriever.build_session_context(1, "test")

        self.assertIn("BU039", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
