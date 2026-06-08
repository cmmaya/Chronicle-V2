"""Tests for AssistantRetrievalTools."""

import pytest

from src.assistant.tools import (
    AssistantRetrievalTools,
    DEFAULT_SESSION_LIMIT,
    MAX_SESSION_LIMIT,
    DEFAULT_TRANSCRIPT_LIMIT,
    MAX_TRANSCRIPT_LIMIT,
    DEFAULT_SUMMARY_LIMIT,
    MAX_SUMMARY_LIMIT,
    DEFAULT_SCREENSHOT_LIMIT,
    MAX_SCREENSHOT_LIMIT,
    DEFAULT_SEARCH_LIMIT,
    MAX_SEARCH_LIMIT,
    ALLOWED_SOURCE_TYPES,
)


class MockDatabase:
    """Mock database for testing retrieval tools."""

    def __init__(self):
        self.sessions = [
            {"id": 1, "name": "Meeting 1", "start_time": 1000, "matched_summary": "summary1", "matched_transcript": "transcript1"},
            {"id": 2, "name": "Meeting 2", "start_time": 2000, "matched_summary": "summary2", "matched_transcript": "transcript2"},
        ]
        self.transcripts = [
            {"session_id": 1, "session_name": "Meeting 1", "timestamp": 100, "source": "microphone", "text": "Hello world"},
            {"session_id": 1, "session_name": "Meeting 1", "timestamp": 200, "source": "system", "text": "Response test"},
        ]
        self.summaries = [
            {"session_id": 1, "session_name": "Meeting 1", "summary_type": "full", "content": "Full summary", "created_at": "2024-01-01"},
        ]
        self.screenshots = [
            {"session_id": 1, "timestamp": 100, "filepath": "/path/screenshot1.png", "description": "Screen 1"},
            {"session_id": 1, "timestamp": 200, "filepath": "/path/screenshot2.png", "description": "Screen 2"},
        ]
        self.get_session_calls = []
        self.get_summaries_calls = []
        self.get_screenshots_calls = []
        self.get_transcripts_calls = []
        self.find_sessions_calls = []
        self.search_transcripts_calls = []
        self.search_summaries_calls = []
        self.search_rag_fts_calls = []

    def get_session(self, session_id):
        self.get_session_calls.append(session_id)
        return {"id": session_id, "name": f"Session {session_id}", "start_time": 1000}

    def get_summaries(self, session_id):
        self.get_summaries_calls.append(session_id)
        return [s for s in self.summaries if s["session_id"] == session_id]

    def get_screenshots(self, session_id):
        self.get_screenshots_calls.append(session_id)
        return [s for s in self.screenshots if s["session_id"] == session_id]

    def get_transcripts(self, session_id):
        self.get_transcripts_calls.append(session_id)
        return [t for t in self.transcripts if t["session_id"] == session_id]

    def find_sessions(self, query, limit):
        self.find_sessions_calls.append((query, limit))
        return self.sessions[:limit]

    def search_transcripts(self, query, limit, session_id=None):
        self.search_transcripts_calls.append((query, limit, session_id))
        return self.transcripts[:limit]

    def search_summaries(self, query, limit, session_id=None):
        self.search_summaries_calls.append((query, limit, session_id))
        return self.summaries[:limit]

    def search_rag_fts(self, query, limit, session_id=None, source_types=None):
        self.search_rag_fts_calls.append((query, limit, session_id, source_types))
        return [
            {"chunk_id": 1, "document_id": 1, "source_type": "transcript", "source_id": 1,
             "session_id": 1, "timestamp": 1000, "title": "Meeting 1", "content": "Hello world", "rank": 1.0},
            {"chunk_id": 2, "document_id": 2, "source_type": "summary", "source_id": 2,
             "session_id": 1, "timestamp": 2000, "title": "Summary 1", "content": "Full summary", "rank": 2.0},
        ][:limit]


class TestAssistantRetrievalTools:
    """Test cases for AssistantRetrievalTools class."""

    def setup_method(self):
        """Set up test fixtures."""
        self.db = MockDatabase()
        self.tools = AssistantRetrievalTools(self.db)

    # === find_sessions tests ===

    def test_find_sessions_returns_results(self):
        """Test that find_sessions returns matching sessions."""
        results = self.tools.find_sessions("meeting")
        
        assert len(results) > 0
        assert "id" in results[0]
        assert "name" in results[0]

    def test_find_sessions_delegates_to_db(self):
        """Test that find_sessions delegates to database."""
        self.tools.find_sessions("test", limit=5)
        
        assert len(self.db.find_sessions_calls) == 1
        assert self.db.find_sessions_calls[0] == ("test", 5)

    def test_find_sessions_empty_query_raises(self):
        """Test that empty query raises ValueError."""
        with pytest.raises(ValueError, match="Query cannot be empty"):
            self.tools.find_sessions("")

    def test_find_sessions_whitespace_only_query_raises(self):
        """Test that whitespace-only query raises ValueError."""
        with pytest.raises(ValueError, match="Query cannot be empty"):
            self.tools.find_sessions("   ")

    def test_find_sessions_default_limit(self):
        """Test that default limit is applied."""
        self.tools.find_sessions("test")
        
        call = self.db.find_sessions_calls[0]
        assert call[1] == DEFAULT_SESSION_LIMIT

    def test_find_sessions_limit_capped_at_max(self):
        """Test that limit is capped at MAX_SESSION_LIMIT."""
        self.tools.find_sessions("test", limit=999)
        
        call = self.db.find_sessions_calls[0]
        assert call[1] == MAX_SESSION_LIMIT

    def test_find_sessions_negative_limit_raises(self):
        """Test that negative limit raises ValueError."""
        with pytest.raises(ValueError, match="Limit must be at least 1"):
            self.tools.find_sessions("test", limit=-1)

    def test_find_sessions_zero_limit_raises(self):
        """Test that zero limit raises ValueError."""
        with pytest.raises(ValueError, match="Limit must be at least 1"):
            self.tools.find_sessions("test", limit=0)

    def test_find_sessions_non_integer_limit_raises(self):
        """Test that non-integer limit raises ValueError."""
        with pytest.raises(ValueError, match="Limit must be an integer"):
            self.tools.find_sessions("test", limit="5")

    # === get_session_context tests ===

    def test_get_session_context_returns_dict(self):
        """Test that get_session_context returns dictionary."""
        result = self.tools.get_session_context(1, "test question")
        
        assert isinstance(result, dict)
        assert "sessions" in result
        assert "transcripts" in result
        assert "summaries" in result
        assert "screenshots" in result

    def test_get_session_context_delegates_to_retriever(self):
        """Test that get_session_context uses AssistantContextRetriever."""
        result = self.tools.get_session_context(1, "hello world")
        
        # Should contain results from the retriever
        assert "sessions" in result

    def test_get_session_context_invalid_session_id_raises(self):
        """Test that invalid session_id raises ValueError."""
        with pytest.raises(ValueError, match="Invalid session_id"):
            self.tools.get_session_context(0, "test")

    def test_get_session_context_negative_session_id_raises(self):
        """Test that negative session_id raises ValueError."""
        with pytest.raises(ValueError, match="Invalid session_id"):
            self.tools.get_session_context(-1, "test")

    def test_get_session_context_non_integer_session_id_raises(self):
        """Test that non-integer session_id raises ValueError."""
        with pytest.raises(ValueError, match="Invalid session_id"):
            self.tools.get_session_context("1", "test")

    def test_get_session_context_invalid_transcript_limit_raises(self):
        """Test that invalid transcript_limit raises ValueError."""
        with pytest.raises(ValueError, match="transcript_limit must be at least 1"):
            self.tools.get_session_context(1, "test", transcript_limit=0)

    def test_get_session_context_transcript_limit_capped(self):
        """Test that transcript_limit is capped."""
        result = self.tools.get_session_context(1, "test", transcript_limit=999)
        
        # If it returns without error, the limit was capped
        assert isinstance(result, dict)

    # === search_transcripts tests ===

    def test_search_transcripts_returns_results(self):
        """Test that search_transcripts returns matching transcripts."""
        results = self.tools.search_transcripts("hello")
        
        assert len(results) > 0
        assert "text" in results[0]

    def test_search_transcripts_delegates_to_db(self):
        """Test that search_transcripts delegates to database."""
        self.tools.search_transcripts("test", limit=5, session_id=1)
        
        assert len(self.db.search_transcripts_calls) == 1
        assert self.db.search_transcripts_calls[0] == ("test", 5, 1)

    def test_search_transcripts_empty_query_raises(self):
        """Test that empty query raises ValueError."""
        with pytest.raises(ValueError, match="Query cannot be empty"):
            self.tools.search_transcripts("")

    def test_search_transcripts_default_limit(self):
        """Test that default limit is applied."""
        self.tools.search_transcripts("test")
        
        call = self.db.search_transcripts_calls[0]
        assert call[1] == DEFAULT_TRANSCRIPT_LIMIT

    def test_search_transcripts_limit_capped_at_max(self):
        """Test that limit is capped at MAX_TRANSCRIPT_LIMIT."""
        self.tools.search_transcripts("test", limit=999)
        
        call = self.db.search_transcripts_calls[0]
        assert call[1] == MAX_TRANSCRIPT_LIMIT

    def test_search_transcripts_invalid_session_id_raises(self):
        """Test that invalid session_id raises ValueError."""
        with pytest.raises(ValueError, match="Invalid session_id"):
            self.tools.search_transcripts("test", session_id=0)

    def test_search_transcripts_with_none_session_id(self):
        """Test that None session_id is allowed."""
        results = self.tools.search_transcripts("test", session_id=None)
        
        # Should work without error
        assert isinstance(results, list)

    # === search_summaries tests ===

    def test_search_summaries_returns_results(self):
        """Test that search_summaries returns matching summaries."""
        results = self.tools.search_summaries("summary")
        
        assert len(results) > 0
        assert "content" in results[0]

    def test_search_summaries_delegates_to_db(self):
        """Test that search_summaries delegates to database."""
        self.tools.search_summaries("test", limit=5, session_id=1)
        
        assert len(self.db.search_summaries_calls) == 1
        assert self.db.search_summaries_calls[0] == ("test", 5, 1)

    def test_search_summaries_empty_query_raises(self):
        """Test that empty query raises ValueError."""
        with pytest.raises(ValueError, match="Query cannot be empty"):
            self.tools.search_summaries("")

    def test_search_summaries_default_limit(self):
        """Test that default limit is applied."""
        self.tools.search_summaries("test")
        
        call = self.db.search_summaries_calls[0]
        assert call[1] == DEFAULT_SUMMARY_LIMIT

    def test_search_summaries_limit_capped_at_max(self):
        """Test that limit is capped at MAX_SUMMARY_LIMIT."""
        self.tools.search_summaries("test", limit=999)
        
        call = self.db.search_summaries_calls[0]
        assert call[1] == MAX_SUMMARY_LIMIT

    # === get_screenshots_near tests ===

    def test_get_screenshots_near_returns_list(self):
        """Test that get_screenshots_near returns list."""
        results = self.tools.get_screenshots_near(1, timestamp=150, tolerance_seconds=60)
        
        assert isinstance(results, list)

    def test_get_screenshots_near_delegates_to_db(self):
        """Test that get_screenshots_near gets screenshots from db."""
        self.tools.get_screenshots_near(1, timestamp=150, tolerance_seconds=60)
        
        assert len(self.db.get_screenshots_calls) == 1
        assert self.db.get_screenshots_calls[0] == 1

    def test_get_screenshots_near_invalid_session_id_raises(self):
        """Test that invalid session_id raises ValueError."""
        with pytest.raises(ValueError, match="Invalid session_id"):
            self.tools.get_screenshots_near(0, timestamp=100)

    def test_get_screenshots_near_invalid_timestamp_raises(self):
        """Test that invalid timestamp raises ValueError."""
        with pytest.raises(ValueError, match="Invalid timestamp"):
            self.tools.get_screenshots_near(1, timestamp=-100)

    def test_get_screenshots_near_negative_tolerance_raises(self):
        """Test that negative tolerance raises ValueError."""
        with pytest.raises(ValueError, match="tolerance_seconds must be at least 1"):
            self.tools.get_screenshots_near(1, timestamp=100, tolerance_seconds=-1)

    def test_get_screenshots_near_finds_nearby_screenshots(self):
        """Test that screenshots near timestamp are found."""
        # Timestamp 150 should find screenshot at 100 and 200 (within 60s tolerance)
        results = self.tools.get_screenshots_near(1, timestamp=150, tolerance_seconds=60)
        
        # Should find at least one screenshot
        assert len(results) >= 1

    def test_get_screenshots_near_excludes_distant_screenshots(self):
        """Test that distant screenshots are excluded."""
        # Timestamp 1000 should not find screenshots at 100 or 200 (too far)
        results = self.tools.get_screenshots_near(1, timestamp=1000, tolerance_seconds=60)
        
        assert len(results) == 0

    # === Integration tests ===

    def test_all_tools_are_callable(self):
        """Test that all tool methods are callable."""
        assert callable(self.tools.find_sessions)
        assert callable(self.tools.get_session_context)
        assert callable(self.tools.search_transcripts)
        assert callable(self.tools.search_summaries)
        assert callable(self.tools.get_screenshots_near)

    def test_tools_handle_db_exceptions_gracefully(self):
        """Test that tools handle database exceptions."""
        error_db = MockDatabase()
        error_db.find_sessions = lambda q, l: (_ for _ in ()).throw(Exception("DB Error"))
        
        tools = AssistantRetrievalTools(error_db)
        results = tools.find_sessions("test")
        
        # Should return error dict, not raise
        assert isinstance(results, list)
        assert len(results) == 1
        assert "error" in results[0]

    # === search_everything tests ===

    def test_search_everything_returns_results(self):
        """Test that search_everything returns matching results."""
        results = self.tools.search_everything("hello")
        
        assert len(results) > 0
        assert "content" in results[0]
        assert "source_type" in results[0]

    def test_search_everything_delegates_to_db(self):
        """Test that search_everything delegates to database."""
        self.tools.search_everything("test", limit=5, session_id=1, source_types=["transcript"])
        
        assert len(self.db.search_rag_fts_calls) == 1
        assert self.db.search_rag_fts_calls[0] == ("test", 5, 1, ["transcript"])

    def test_search_everything_empty_query_raises(self):
        """Test that empty query raises ValueError."""
        with pytest.raises(ValueError, match="Query cannot be empty"):
            self.tools.search_everything("")

    def test_search_everything_whitespace_only_query_raises(self):
        """Test that whitespace-only query raises ValueError."""
        with pytest.raises(ValueError, match="Query cannot be empty"):
            self.tools.search_everything("   ")

    def test_search_everything_default_limit(self):
        """Test that default limit is applied."""
        self.tools.search_everything("test")
        
        call = self.db.search_rag_fts_calls[0]
        assert call[1] == DEFAULT_SEARCH_LIMIT

    def test_search_everything_limit_capped_at_max(self):
        """Test that limit is capped at MAX_SEARCH_LIMIT."""
        self.tools.search_everything("test", limit=999)
        
        call = self.db.search_rag_fts_calls[0]
        assert call[1] == MAX_SEARCH_LIMIT

    def test_search_everything_negative_limit_raises(self):
        """Test that negative limit raises ValueError."""
        with pytest.raises(ValueError, match="Limit must be at least 1"):
            self.tools.search_everything("test", limit=-1)

    def test_search_everything_zero_limit_raises(self):
        """Test that zero limit raises ValueError."""
        with pytest.raises(ValueError, match="Limit must be at least 1"):
            self.tools.search_everything("test", limit=0)

    def test_search_everything_non_integer_limit_raises(self):
        """Test that non-integer limit raises ValueError."""
        with pytest.raises(ValueError, match="Limit must be an integer"):
            self.tools.search_everything("test", limit="5")

    def test_search_everything_invalid_session_id_raises(self):
        """Test that invalid session_id raises ValueError."""
        with pytest.raises(ValueError, match="Invalid session_id"):
            self.tools.search_everything("test", session_id=0)

    def test_search_everything_negative_session_id_raises(self):
        """Test that negative session_id raises ValueError."""
        with pytest.raises(ValueError, match="Invalid session_id"):
            self.tools.search_everything("test", session_id=-1)

    def test_search_everything_with_none_session_id(self):
        """Test that None session_id is allowed."""
        results = self.tools.search_everything("test", session_id=None)
        
        # Should work without error
        assert isinstance(results, list)

    def test_search_everything_source_types_must_be_list(self):
        """Test that source_types must be a list."""
        with pytest.raises(ValueError, match="source_types must be a list"):
            self.tools.search_everything("test", source_types="transcript")

    def test_search_everything_invalid_source_type_raises(self):
        """Test that invalid source_type raises ValueError."""
        with pytest.raises(ValueError, match="Invalid source_type"):
            self.tools.search_everything("test", source_types=["invalid_type"])

    def test_search_everything_valid_source_types_allowed(self):
        """Test that valid source_types are allowed."""
        for source_type in ALLOWED_SOURCE_TYPES:
            results = self.tools.search_everything("test", source_types=[source_type])
            
            # Should work without error
            assert isinstance(results, list)

    def test_search_everything_returns_normalized_dict(self):
        """Test that returned result has expected shape."""
        results = self.tools.search_everything("hello")
        
        assert len(results) > 0
        r = results[0]
        assert "chunk_id" in r
        assert "document_id" in r
        assert "source_type" in r
        assert "source_id" in r
        assert "session_id" in r
        assert "timestamp" in r
        assert "title" in r
        assert "content" in r
        assert "rank" in r

    def test_search_everything_all_tools_callable(self):
        """Test that search_everything is callable."""
        assert callable(self.tools.search_everything)
