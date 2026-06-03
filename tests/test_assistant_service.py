"""Tests for AssistantAnswerService."""

import pytest
from unittest.mock import MagicMock, patch

from src.assistant.service import AssistantAnswerService, AnswerResponse
from src.assistant.session_resolver import ScopeResolution, ResolutionResult


class MockDatabase:
    """Mock database for testing answer service."""

    def __init__(self):
        self.conversations = []
        self.messages = []
        self.conversation_id_counter = 1

    def find_sessions(self, query, limit=10):
        """Return mock session search results."""
        return [
            {"id": 1, "name": "Meeting 1", "start_time": 1000},
            {"id": 2, "name": "Meeting 2", "start_time": 2000},
        ]

    def get_session(self, session_id):
        """Return mock session."""
        return {"id": session_id, "name": f"Meeting {session_id}", "start_time": 1000}

    def create_conversation(self, session_id=None, title=None):
        """Create a mock conversation."""
        conv_id = self.conversation_id_counter
        self.conversation_id_counter += 1
        self.conversations.append({
            "id": conv_id,
            "session_id": session_id,
            "title": title,
        })
        return conv_id

    def add_message(self, conversation_id, role, content):
        """Add a mock message."""
        self.messages.append({
            "conversation_id": conversation_id,
            "role": role,
            "content": content,
        })
        return len(self.messages)

    def get_messages(self, conversation_id):
        """Get mock messages for a conversation."""
        return [m for m in self.messages if m["conversation_id"] == conversation_id]


class MockOpenRouterClient:
    """Mock OpenRouter client for testing."""

    def __init__(self, should_fail=False):
        self.should_fail = should_fail
        self.calls = []
        self.chat_results = [
            "This is a mock response from the assistant.",
            "Based on the meeting transcript, the key points are...",
        ]
        self.call_count = 0

    def chat(self, messages, model=None, temperature=None):
        """Return mock chat response."""
        self.calls.append({"messages": messages, "model": model})
        if self.should_fail:
            raise Exception("API error")
        response = self.chat_results[self.call_count % len(self.chat_results)]
        self.call_count += 1
        return response


class TestAssistantAnswerService:
    """Test cases for AssistantAnswerService class."""

    def setup_method(self):
        """Set up test fixtures."""
        self.db = MockDatabase()
        self.mock_client = MockOpenRouterClient()
        self.service = AssistantAnswerService(
            db=self.db,
            openrouter_client=self.mock_client,
        )

    # === Ambiguous cases - should return clarification without OpenRouter call ===

    def test_needs_clarification_returns_clarification_question(self):
        """Test that needs_clarification scope returns clarification without API call."""
        with patch.object(self.service._resolver, 'resolve') as mock_resolve:
            mock_resolve.return_value = ResolutionResult(
                scope=ScopeResolution.NEEDS_CLARIFICATION,
                reason="no session could be inferred",
            )

            result = self.service.ask("What did we discuss?")

            assert result.needs_clarification is True
            assert result.clarification_question is not None
            assert "Which session" in result.clarification_question
            assert result.success is False
            # Should NOT call OpenRouter
            assert len(self.mock_client.calls) == 0

    def test_ambiguous_returns_candidates_without_api_call(self):
        """Test that ambiguous scope returns candidates without API call."""
        from src.assistant.context_models import SessionCandidate

        with patch.object(self.service._resolver, 'resolve') as mock_resolve:
            mock_resolve.return_value = ResolutionResult(
                scope=ScopeResolution.AMBIGUOUS,
                candidates=[
                    SessionCandidate(session_id=1, session_name="Meeting 1", start_timestamp=1000),
                    SessionCandidate(session_id=2, session_name="Meeting 2", start_timestamp=2000),
                ],
                reason="multiple candidates found",
            )

            result = self.service.ask("What about the budget?")

            assert result.needs_clarification is True
            assert result.candidates is not None
            assert len(result.candidates) == 2
            assert result.success is False
            # Should NOT call OpenRouter
            assert len(self.mock_client.calls) == 0

    # === Single-session path tests ===

    def test_single_session_retrieves_context_and_calls_openrouter(self):
        """Test that single-session scope retrieves context and calls OpenRouter."""
        with patch.object(self.service._resolver, 'resolve') as mock_resolve:
            with patch.object(self.service._tools, 'get_session_context') as mock_context:
                mock_resolve.return_value = ResolutionResult(
                    scope=ScopeResolution.SELECTED_SESSION,
                    session_ids=[1],
                    reason="matched session: Meeting 1",
                )
                mock_context.return_value = {
                    "sessions": [{"session_id": 1, "session_name": "Meeting 1"}],
                    "transcripts": [{"text": "Hello world", "source": "microphone"}],
                    "summaries": [{"content": "Meeting summary", "summary_type": "full"}],
                    "screenshots": [],
                }

                result = self.service.ask("What was discussed in Meeting 1?")

                assert result.success is True
                assert result.answer is not None
                # Should have called context retrieval
                assert mock_context.called
                # Should have called OpenRouter
                assert len(self.mock_client.calls) == 1

    def test_single_session_persists_messages(self):
        """Test that single-session path persists messages to database."""
        with patch.object(self.service._resolver, 'resolve') as mock_resolve:
            mock_resolve.return_value = ResolutionResult(
                scope=ScopeResolution.SELECTED_SESSION,
                session_ids=[1],
                reason="matched session",
            )

            result = self.service.ask("Tell me about the meeting", conversation_id=None)

            assert result.success is True
            assert result.conversation_id is not None
            # Check messages were persisted
            messages = self.db.get_messages(result.conversation_id)
            assert len(messages) >= 2  # user question + assistant answer
            user_msg = next((m for m in messages if m["role"] == "user"), None)
            assistant_msg = next((m for m in messages if m["role"] == "assistant"), None)
            assert user_msg is not None
            assert assistant_msg is not None
            assert "meeting" in user_msg["content"].lower()

    # === All-session path tests ===

    def test_all_sessions_uses_search_tools(self):
        """Test that all-session scope uses search tools for cross-session context."""
        with patch.object(self.service._resolver, 'resolve') as mock_resolve:
            with patch.object(self.service._tools, 'search_summaries') as mock_search_summaries:
                with patch.object(self.service._tools, 'search_transcripts') as mock_search_transcripts:
                    mock_resolve.return_value = ResolutionResult(
                        scope=ScopeResolution.ALL_SESSIONS,
                        reason="cross-session intent detected",
                    )
                    mock_search_summaries.return_value = [
                        {"session_name": "Meeting 1", "content": "Summary 1"},
                    ]
                    mock_search_transcripts.return_value = [
                        {"session_name": "Meeting 1", "text": "Transcript 1"},
                    ]

                    result = self.service.ask("What did we discuss across all sessions?")

                    assert result.success is True
                    # Should have called search tools
                    assert mock_search_summaries.called
                    assert mock_search_transcripts.called
                    # Should have called OpenRouter
                    assert len(self.mock_client.calls) == 1

    # === Error handling tests ===

    def test_agent_not_found_returns_error(self):
        """Test that unknown agent returns error."""
        with patch.object(self.service._resolver, 'resolve') as mock_resolve:
            mock_resolve.return_value = ResolutionResult(
                scope=ScopeResolution.SELECTED_SESSION,
                session_ids=[1],
                reason="matched session",
            )

            result = self.service.ask("Question", agent_id="nonexistent_agent")

            assert result.success is False
            assert "not found" in result.error.lower()

    def test_openrouter_failure_returns_error(self):
        """Test that OpenRouter API failure returns error without crashing."""
        with patch.object(self.service._resolver, 'resolve') as mock_resolve:
            mock_resolve.return_value = ResolutionResult(
                scope=ScopeResolution.SELECTED_SESSION,
                session_ids=[1],
                reason="matched session",
            )

            self.mock_client.should_fail = True
            result = self.service.ask("Question")

            assert result.success is False
            assert "failed" in result.error.lower() or "error" in result.error.lower()

    # === Conversation continuation tests ===

    def test_continues_existing_conversation(self):
        """Test that continuing an existing conversation adds to its history."""
        # Create a conversation first
        conv_id = self.db.create_conversation(session_id=1, title="Test")
        self.db.add_message(conv_id, "user", "First question")
        self.db.add_message(conv_id, "assistant", "First answer")

        with patch.object(self.service._resolver, 'resolve') as mock_resolve:
            mock_resolve.return_value = ResolutionResult(
                scope=ScopeResolution.SELECTED_SESSION,
                session_ids=[1],
                reason="matched session",
            )

            result = self.service.ask("Follow-up question", conversation_id=conv_id)

            assert result.success is True
            # Should have 4 messages now (2 existing + 2 new)
            messages = self.db.get_messages(conv_id)
            assert len(messages) == 4


class TestAnswerResponse:
    """Test cases for AnswerResponse dataclass."""

    def test_default_values(self):
        """Test that AnswerResponse has proper defaults."""
        response = AnswerResponse(success=True)

        assert response.success is True
        assert response.answer is None
        response2 = AnswerResponse(success=False)
        assert response2.candidates == []

    def test_clarification_response(self):
        """Test clarification response structure."""
        response = AnswerResponse(
            success=False,
            needs_clarification=True,
            clarification_question="Which session?",
            candidates=[{"session_id": 1, "session_name": "Meeting 1"}],
        )

        assert response.needs_clarification is True
        assert len(response.candidates) == 1
        assert response.candidates[0]["session_id"] == 1
