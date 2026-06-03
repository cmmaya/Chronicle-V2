"""Unit tests for database search methods."""

import unittest
import os
import tempfile
from datetime import datetime
from src.storage.database import Database


class TestDatabaseSearchMethods(unittest.TestCase):
    def setUp(self):
        """Set up a temporary database for testing."""
        self.temp_db = tempfile.NamedTemporaryFile(delete=False, suffix='.db')
        self.temp_db.close()
        self.db = Database(self.temp_db.name)
        self.db.connect()
        self._create_test_data()

    def tearDown(self):
        """Clean up the temporary database."""
        self.db.disconnect()
        os.unlink(self.temp_db.name)

    def _create_test_data(self):
        """Create test sessions, transcripts, and summaries."""
        # Create sessions
        self.session1_id = self.db.create_session(
            name="Team Standup",
            start_time=datetime(2024, 1, 15, 9, 0),
            status="completed",
            transcription_status="transcribed",
            summary_status="completed"
        )
        self.session2_id = self.db.create_session(
            name="Design Review",
            start_time=datetime(2024, 1, 16, 14, 0),
            status="completed",
            transcription_status="transcribed",
            summary_status="completed"
        )
        self.session3_id = self.db.create_session(
            name="Sprint Planning",
            start_time=datetime(2024, 1, 17, 10, 0),
            status="completed",
            transcription_status="none",
            summary_status="none"
        )

        # Create transcripts
        self.db.add_transcript(
            session_id=self.session1_id,
            timestamp=datetime(2024, 1, 15, 9, 5),
            text="Let's discuss the roadmap for Q1.",
            source="microphone"
        )
        self.db.add_transcript(
            session_id=self.session1_id,
            timestamp=datetime(2024, 1, 15, 9, 10),
            text="We need to focus on performance improvements.",
            source="system"
        )
        self.db.add_transcript(
            session_id=self.session2_id,
            timestamp=datetime(2024, 1, 16, 14, 5),
            text="The UI mockups look great.",
            source="microphone"
        )

        # Create summaries
        self.db.add_summary(
            session_id=self.session1_id,
            summary_type="key_points",
            content="1. Q1 roadmap discussed. 2. Performance is priority.",
            model_used="gemini-flash"
        )
        self.db.add_summary(
            session_id=self.session2_id,
            summary_type="action_items",
            content="- Finalize UI designs - Schedule user testing",
            model_used="deepseek-chat"
        )

    def test_get_transcripts_returns_chronological(self):
        """Test get_transcripts returns chronological rows."""
        transcripts = self.db.get_transcripts(self.session1_id)
        self.assertEqual(len(transcripts), 2)
        # Should be sorted by timestamp ascending
        self.assertIn("roadmap", transcripts[0]['text'])
        self.assertIn("performance", transcripts[1]['text'])

    def test_search_transcripts_with_session_id(self):
        """Test search_transcripts is parameterized and supports optional session_id."""
        # Search within a specific session
        results = self.db.search_transcripts("roadmap", limit=10, session_id=self.session1_id)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['session_name'], "Team Standup")
        self.assertIn("roadmap", results[0]['text'])

    def test_search_transcripts_without_session_id(self):
        """Test search_transcripts across all sessions."""
        results = self.db.search_transcripts("performance", limit=10)
        self.assertEqual(len(results), 1)
        self.assertIn("performance", results[0]['text'])

    def test_search_transcripts_no_results(self):
        """Test search_transcripts returns empty when no match."""
        results = self.db.search_transcripts("nonexistent_term_xyz", limit=10)
        self.assertEqual(len(results), 0)

    def test_search_transcripts_limit(self):
        """Test search_transcripts respects limit."""
        results = self.db.search_transcripts("the", limit=1)
        self.assertLessEqual(len(results), 1)

    def test_search_summaries_returns_session_metadata(self):
        """Test search_summaries returns session metadata."""
        results = self.db.search_summaries("performance", limit=10)
        self.assertEqual(len(results), 1)
        self.assertIn('session_name', results[0])
        self.assertEqual(results[0]['session_name'], "Team Standup")

    def test_search_summaries_with_session_id(self):
        """Test search_summaries with specific session filter."""
        results = self.db.search_summaries("UI", limit=10, session_id=self.session2_id)
        self.assertEqual(len(results), 1)
        self.assertIn("UI", results[0]['content'])

    def test_search_summaries_no_match(self):
        """Test search_summaries returns empty when no match."""
        results = self.db.search_summaries("nonexistent_xyz", limit=10)
        self.assertEqual(len(results), 0)

    def test_find_sessions_by_name(self):
        """Test find_sessions searches by session name."""
        results = self.db.find_sessions("Standup", limit=10)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['name'], "Team Standup")

    def test_find_sessions_by_summary(self):
        """Test find_sessions searches by summary content."""
        results = self.db.find_sessions("performance", limit=10)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['name'], "Team Standup")

    def test_find_sessions_by_transcript(self):
        """Test find_sessions searches by transcript text."""
        results = self.db.find_sessions("mockups", limit=10)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['name'], "Design Review")

    def test_find_sessions_limit(self):
        """Test find_sessions respects limit."""
        results = self.db.find_sessions("the", limit=1)
        self.assertLessEqual(len(results), 1)

    def test_find_sessions_no_match(self):
        """Test find_sessions returns empty when no match."""
        results = self.db.find_sessions("nonexistent_query_xyz", limit=10)
        self.assertEqual(len(results), 0)


if __name__ == "__main__":
    unittest.main()
