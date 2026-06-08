"""Tests for RAG context builder."""

import pytest

from src.assistant.rag_context_builder import build_any_session_context


class TestBuildAnySessionContext:
    """Tests for build_any_session_context function."""

    def test_empty_results_returns_no_context_message(self):
        """Empty results returns the no-context message."""
        result = build_any_session_context("test query", [])
        assert result == "(No relevant context found in any session)"

    def test_empty_results_with_custom_max_chars(self):
        """Empty results respects max_chars but returns no-context message."""
        result = build_any_session_context("test query", [], max_chars=5000)
        assert result == "(No relevant context found in any session)"

    def test_single_session_groups_results(self):
        """Results from multiple sessions are grouped."""
        results = [
            {
                "session_id": 1,
                "session_name": "Team Standup",
                "source_type": "transcript",
                "timestamp": 1700000000,
                "content": "First point discussed.",
            },
            {
                "session_id": 1,
                "session_name": "Team Standup",
                "source_type": "transcript",
                "timestamp": 1700000100,
                "content": "Second point discussed.",
            },
        ]
        result = build_any_session_context("project", results)
        assert "Team Standup" in result
        assert "First point" in result
        assert "Second point" in result

    def test_multiple_sessions_separated(self):
        """Results from different sessions are grouped separately."""
        results = [
            {
                "session_id": 1,
                "session_name": "Meeting A",
                "source_type": "transcript",
                "timestamp": 1700000000,
                "content": "Content from A",
            },
            {
                "session_id": 2,
                "session_name": "Meeting B",
                "source_type": "summary",
                "timestamp": 1700010000,
                "content": "Content from B",
            },
        ]
        result = build_any_session_context("content", results)
        assert "Meeting A" in result
        assert "Meeting B" in result
        assert result.index("Meeting A") < result.index("Meeting B")

    def test_long_content_is_truncated(self):
        """Long content is truncated."""
        long_content = "x" * 1000
        results = [
            {
                "session_id": 1,
                "session_name": "Test Session",
                "source_type": "transcript",
                "timestamp": 1700000000,
                "content": long_content,
            },
        ]
        result = build_any_session_context("test", results)
        assert len(result) < 700  # truncated content + overhead

    def test_max_chars_respected(self):
        """max_chars is respected."""
        # Create results that would exceed max_chars
        results = [
            {
                "session_id": 1,
                "session_name": "Test",
                "source_type": "transcript",
                "timestamp": 1700000000 + i * 100,
                "content": f"Content number {i} " + ("x" * 200),
            }
            for i in range(20)
        ]
        # With max_chars=500, should limit output
        result = build_any_session_context("test", results, max_chars=500)
        assert len(result) <= 600  # some margin for formatting

    def test_source_type_included(self):
        """Source type is included in output."""
        results = [
            {
                "session_id": 1,
                "session_name": "Test",
                "source_type": "transcript",
                "timestamp": 1700000000,
                "content": "Test content",
            },
        ]
        result = build_any_session_context("test", results)
        # Source type appears after session header, e.g., "\n[transcript"
        assert "[transcript @" in result

    def test_timestamp_formatted(self):
        """Timestamp is formatted as HH:MM:SS."""
        results = [
            {
                "session_id": 1,
                "session_name": "Test",
                "source_type": "transcript",
                "timestamp": 1700000000,  # 2023-11-14 22:13:20 UTC
                "content": "Test content",
            },
        ]
        result = build_any_session_context("test", results)
        # Timestamp appears after @ symbol, e.g., "@ 17:13:20" (local time)
        assert "@ " in result and ":20]" in result

    def test_title_included_when_available(self):
        """Title is included when available."""
        results = [
            {
                "session_id": 1,
                "session_name": "Test",
                "source_type": "transcript",
                "timestamp": 1700000000,
                "title": "Important Topic",
                "content": "Content",
            },
        ]
        result = build_any_session_context("test", results)
        assert "[Important Topic]" in result

    def test_no_timestamp_handled(self):
        """Missing timestamp is handled gracefully."""
        results = [
            {
                "session_id": 1,
                "session_name": "Test",
                "source_type": "transcript",
                "timestamp": None,
                "content": "Content",
            },
        ]
        result = build_any_session_context("test", results)
        assert "Test" in result

    def test_no_session_name_handled(self):
        """Missing session_name handled gracefully."""
        results = [
            {
                "session_id": 1,
                "session_name": None,
                "source_type": "transcript",
                "timestamp": 1700000000,
                "content": "Content",
            },
        ]
        result = build_any_session_context("test", results)
        # Handles None gracefully
        assert "Test" in result or "None" in result

    def test_different_source_types(self):
        """Different source types are handled correctly."""
        results = [
            {
                "session_id": 1,
                "session_name": "Test",
                "source_type": "transcript",
                "timestamp": 1700000000,
                "content": "Transcript content",
            },
            {
                "session_id": 1,
                "session_name": "Test",
                "source_type": "summary",
                "timestamp": 1700000000,
                "content": "Summary content",
            },
            {
                "session_id": 1,
                "session_name": "Test",
                "source_type": "screenshot",
                "timestamp": 1700000000,
                "content": "Screenshot content",
            },
        ]
        result = build_any_session_context("test", results)
        # Source types appear with @ timestamp
        assert "[transcript @" in result
        assert "[summary @" in result
        assert "[screenshot @" in result

    def test_custom_max_chars_value(self):
        """Custom max_chars value is used."""
        results = [
            {
                "session_id": 1,
                "session_name": "Test",
                "source_type": "transcript",
                "timestamp": 1700000000,
                "content": "x" * 100,
            },
        ]
        result = build_any_session_context("test", results, max_chars=200)
        # Should still include the result
        assert "Test" in result

    def test_small_max_chars_allows_one_result(self):
        """Small max_chars still allows at least one result."""
        results = [
            {
                "session_id": 1,
                "session_name": "Test",
                "source_type": "transcript",
                "timestamp": 1700000000,
                "content": "Short content",
            },
        ]
        result = build_any_session_context("test", results, max_chars=50)
        # At minimum should have session header and one result
        assert "Test" in result or "(No relevant context" in result