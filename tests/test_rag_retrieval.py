"""Tests for RAG retrieval tools and context building.

Tests search_everything validation and build_any_session_context functionality
without requiring external services or a real production database.
"""

import unittest
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

from src.assistant.tools import AssistantRetrievalTools, MAX_SEARCH_LIMIT, ALLOWED_SOURCE_TYPES
from src.assistant.rag_context_builder import build_any_session_context


class MockDatabase:
    """Mock database for testing search_everything validation."""

    def __init__(self, results: List[Dict[str, Any]] = None, should_fail: bool = False):
        self._results = results or []
        self._should_fail = should_fail
        self.last_call: Dict[str, Any] = {}

    def search_rag_fts(
        self,
        query: str,
        limit: int,
        session_id: Optional[int] = None,
        source_types: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Mock FTS search that tracks call parameters."""
        self.last_call = {
            "query": query,
            "limit": limit,
            "session_id": session_id,
            "source_types": source_types,
        }
        if self._should_fail:
            raise RuntimeError("Database error")
        return self._results


class TestSearchEverythingValidation(unittest.TestCase):
    """Test search_everything input validation."""

    def setUp(self):
        """Create tools with mock database."""
        self.db = MockDatabase()
        self.tools = AssistantRetrievalTools(self.db)

    def test_empty_query_raises_value_error(self):
        """search_everything rejects empty query."""
        with self.assertRaises(ValueError) as ctx:
            self.tools.search_everything("")
        self.assertIn("empty", str(ctx.exception).lower())

    def test_whitespace_only_query_raises_value_error(self):
        """search_everything rejects whitespace-only query."""
        with self.assertRaises(ValueError) as ctx:
            self.tools.search_everything("   ")
        self.assertIn("empty", str(ctx.exception).lower())

    def test_limit_is_capped_at_50(self):
        """search_everything caps limit at 50."""
        self.db._results = [{"chunk_id": 1}]
        result = self.tools.search_everything("test", limit=100)
        # Limit should be capped at 50
        self.assertEqual(self.db.last_call["limit"], MAX_SEARCH_LIMIT)

    def test_limit_0_raises_value_error(self):
        """search_everything rejects limit of 0."""
        with self.assertRaises(ValueError) as ctx:
            self.tools.search_everything("test", limit=0)
        self.assertIn("at least 1", str(ctx.exception).lower())

    def test_negative_limit_raises_value_error(self):
        """search_everything rejects negative limit."""
        with self.assertRaises(ValueError) as ctx:
            self.tools.search_everything("test", limit=-1)
        self.assertIn("at least 1", str(ctx.exception).lower())

    def test_invalid_session_id_raises_value_error(self):
        """search_everything rejects invalid session_id."""
        with self.assertRaises(ValueError) as ctx:
            self.tools.search_everything("test", session_id=0)
        self.assertIn("invalid session_id", str(ctx.exception).lower())

    def test_negative_session_id_raises_value_error(self):
        """search_everything rejects negative session_id."""
        with self.assertRaises(ValueError) as ctx:
            self.tools.search_everything("test", session_id=-5)
        self.assertIn("invalid session_id", str(ctx.exception).lower())

    def test_invalid_source_type_raises_value_error(self):
        """search_everything rejects invalid source_type."""
        with self.assertRaises(ValueError) as ctx:
            self.tools.search_everything("test", source_types=["invalid_type"])
        self.assertIn("invalid source_type", str(ctx.exception).lower())

    def test_valid_source_types_accepted(self):
        """search_everything accepts valid source types."""
        self.db._results = [{"chunk_id": 1}]
        # All allowed types should work without raising
        for source_type in ALLOWED_SOURCE_TYPES:
            self.tools.search_everything("test", source_types=[source_type])

    def test_multiple_valid_source_types_accepted(self):
        """search_everything accepts multiple valid source types."""
        self.db._results = [{"chunk_id": 1}]
        result = self.tools.search_everything(
            "test",
            source_types=["transcript", "summary", "screenshot"]
        )
        self.assertEqual(len(result), 1)

    def test_source_types_must_be_list(self):
        """search_everything requires source_types to be a list."""
        with self.assertRaises(ValueError) as ctx:
            self.tools.search_everything("test", source_types="transcript")
        self.assertIn("list", str(ctx.exception).lower())


class TestSearchEverythingFunctionality(unittest.TestCase):
    """Test search_everything core functionality."""

    def test_passes_parameters_to_db(self):
        """search_everything passes validated parameters to database."""
        self.db = MockDatabase([{"chunk_id": 1}])
        self.tools = AssistantRetrievalTools(self.db)

        result = self.tools.search_everything(
            "test query",
            limit=25,
            session_id=5,
            source_types=["transcript"]
        )

        self.assertEqual(self.db.last_call["query"], "test query")
        self.assertEqual(self.db.last_call["limit"], 25)
        self.assertEqual(self.db.last_call["session_id"], 5)
        self.assertEqual(self.db.last_call["source_types"], ["transcript"])

    def test_returns_normalized_results(self):
        """search_everything returns normalized result format."""
        db = MockDatabase([
            {
                "chunk_id": 1,
                "document_id": 10,
                "source_type": "transcript",
                "source_id": 100,
                "session_id": 5,
                "timestamp": 1700000000,
                "title": "mic",
                "content": "Test content",
                "rank": 1.0,
            }
        ])
        tools = AssistantRetrievalTools(db)

        result = tools.search_everything("test")

        self.assertEqual(len(result), 1)
        self.assertIn("chunk_id", result[0])
        self.assertIn("source_type", result[0])
        self.assertIn("content", result[0])
        self.assertEqual(result[0]["source_type"], "transcript")

    def test_database_error_returns_error_dict(self):
        """search_everything returns error dict on database failure."""
        self.db = MockDatabase(should_fail=True)
        self.tools = AssistantRetrievalTools(self.db)

        result = self.tools.search_everything("test")

        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 1)
        self.assertIn("error", result[0])


class TestBuildAnySessionContext(unittest.TestCase):
    """Test build_any_session_context formatting."""

    def test_empty_results_returns_no_context_message(self):
        """build_any_session_context returns no-context message for empty results."""
        result = build_any_session_context("test query", [])
        self.assertEqual(result, "(No relevant context found in any session)")

    def test_single_session_grouping(self):
        """Results from one session are grouped together."""
        results = [
            {
                "session_id": 1,
                "session_name": "Team Standup",
                "source_type": "transcript",
                "timestamp": 1700000000,
                "content": "First point.",
            },
            {
                "session_id": 1,
                "session_name": "Team Standup",
                "source_type": "transcript",
                "timestamp": 1700000100,
                "content": "Second point.",
            },
        ]
        context = build_any_session_context("test", results)

        self.assertIn("Team Standup", context)
        self.assertIn("First point", context)
        self.assertIn("Second point", context)

    def test_multiple_sessions_grouped_separately(self):
        """Results from different sessions are grouped separately."""
        results = [
            {
                "session_id": 1,
                "session_name": "Session A",
                "source_type": "transcript",
                "timestamp": 1700000000,
                "content": "Content A",
            },
            {
                "session_id": 2,
                "session_name": "Session B",
                "source_type": "summary",
                "timestamp": 1700010000,
                "content": "Content B",
            },
        ]
        context = build_any_session_context("test", results)

        self.assertIn("Session A", context)
        self.assertIn("Session B", context)
        # Sessions should be separate sections
        self.assertTrue(context.index("Session A") < context.index("Session B"))

    def test_context_includes_source_type(self):
        """Context output includes source_type for each result."""
        results = [
            {
                "session_id": 1,
                "session_name": "Test",
                "source_type": "transcript",
                "timestamp": 1700000000,
                "content": "Test content",
            },
        ]
        context = build_any_session_context("test", results)

        self.assertIn("transcript", context)

    def test_context_includes_session_identifier(self):
        """Context output includes session name as identifier."""
        results = [
            {
                "session_id": 1,
                "session_name": "My Meeting",
                "source_type": "summary",
                "timestamp": 1700000000,
                "content": "Summary content",
            },
        ]
        context = build_any_session_context("test", results)

        self.assertIn("My Meeting", context)

    def test_max_chars_respected(self):
        """build_any_session_context respects max_chars limit."""
        # Create results that would exceed max_chars
        long_content = "x" * 1000
        results = [
            {
                "session_id": 1,
                "session_name": "Test",
                "source_type": "transcript",
                "timestamp": 1700000000 + i * 100,
                "content": long_content,
            }
            for i in range(20)
        ]

        # Use a small max_chars to test truncation
        context = build_any_session_context("test", results, max_chars=500)

        # Should not exceed 500 characters
        self.assertLessEqual(len(context), 500)

    def test_timestamp_included_when_available(self):
        """Context includes timestamp when available."""
        results = [
            {
                "session_id": 1,
                "session_name": "Test",
                "source_type": "transcript",
                "timestamp": 1700000000,  # 2023-11-14 22:13:20 UTC -> 17:13:20 local
                "content": "Test content",
            },
        ]
        context = build_any_session_context("test", results)

        # Should include formatted time (HH:MM:SS in local timezone)
        self.assertIn("17:13:20", context)

    def test_empty_source_type_shows_unknown(self):
        """Missing source_type shows as unknown."""
        results = [
            {
                "session_id": 1,
                "session_name": "Test",
                "timestamp": 1700000000,
                "content": "Test content",
            },
        ]
        context = build_any_session_context("test", results)

        self.assertIn("unknown", context)


class TestSearchEverythingIntegration(unittest.TestCase):
    """Integration tests combining search and context building."""

    def test_full_search_to_context_pipeline(self):
        """End-to-end test: search returns results that build context formats."""
        db_results = [
            {
                "chunk_id": 1,
                "document_id": 10,
                "source_type": "transcript",
                "source_id": 100,
                "session_id": 1,
                "session_name": "Team Standup",
                "timestamp": 1700000000,
                "title": "mic",
                "content": "Let's discuss the project status.",
                "rank": 1.0,
            },
            {
                "chunk_id": 2,
                "document_id": 11,
                "source_type": "summary",
                "source_id": 101,
                "session_id": 2,
                "session_name": "Sprint Planning",
                "timestamp": 1700010000,
                "title": None,
                "content": "Completed: feature A. Next: feature B.",
                "rank": 2.0,
            },
        ]
        db = MockDatabase(db_results)
        tools = AssistantRetrievalTools(db)

        # Search
        search_results = tools.search_everything("project status", limit=20)

        # Build context - uses session_id when session_name not available
        context = build_any_session_context("project status", search_results)

        # Verify context includes both source types (falls back to Session {id} when no name)
        self.assertIn("transcript", context)
        self.assertIn("summary", context)
        # Different session IDs should be grouped separately
        self.assertIn("Session 1", context)
        self.assertIn("Session 2", context)


if __name__ == "__main__":
    unittest.main()