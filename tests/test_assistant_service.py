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

    def search_rag_fts(self, query, limit=20, session_id=None, source_types=None):
        """Return mock RAG search results."""
        return []

    def search_everything(self, query, limit=20, session_id=None, source_types=None):
        """Return mock unified search results."""
        return [
            {
                "chunk_id": 1,
                "document_id": 1,
                "source_type": "transcript",
                "source_id": 1,
                "session_id": 1,
                "session_name": "Meeting 1",
                "timestamp": 1000,
                "title": "microphone",
                "content": "This is a transcript about project status.",
                "rank": 1.0,
            },
            {
                "chunk_id": 2,
                "document_id": 2,
                "source_type": "summary",
                "source_id": 2,
                "session_id": 2,
                "session_name": "Meeting 2",
                "timestamp": 2000,
                "title": "full",
                "content": "Meeting summary about budget.",
                "rank": 2.0,
            },
        ]


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
        self.calls.append({"messages": messages, "model": model, "temperature": temperature})
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

    def test_prompt_history_is_capped_to_recent_messages(self):
        """A long conversation only replays its most recent turns into the prompt."""
        from src.assistant.service import MAX_HISTORY_MESSAGES

        conv_id = self.db.create_conversation(session_id=1, title="Long chat")
        for i in range(40):
            role = "user" if i % 2 == 0 else "assistant"
            self.db.add_message(conv_id, role, f"msg {i}")

        agent = self.service._get_agent_config(None)
        prompt = self.service._build_messages(agent, "ctx", "new question", conv_id)

        history = [m for m in prompt if m["content"].startswith("msg ")]
        assert len(history) == MAX_HISTORY_MESSAGES
        assert history[-1]["content"] == "msg 39"
        assert history[0]["content"] == f"msg {40 - MAX_HISTORY_MESSAGES}"


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


class TestAllSessionsContext:
    """Test cases for all-session context retrieval (BU080)."""

    def setup_method(self):
        """Set up test fixtures."""
        self.db = MockDatabase()
        self.mock_client = MockOpenRouterClient()
        self.service = AssistantAnswerService(
            db=self.db,
            openrouter_client=self.mock_client,
        )

    @patch('src.assistant.service.get_selected_model')
    @patch('src.assistant.service.OpenRouterClient')
    def test_all_sessions_uses_unified_search_when_available(self, mock_client_class, mock_get_model):
        """Test that all-session path uses unified search_everything first."""
        mock_get_model.return_value = "test-model"
        mock_client_instance = self.mock_client
        mock_client_class.return_value = mock_client_instance

        with patch.object(self.service._resolver, 'resolve') as mock_resolve:
            with patch.object(self.service._tools, 'search_everything') as mock_search_everything:
                mock_resolve.return_value = ResolutionResult(
                    scope=ScopeResolution.ALL_SESSIONS,
                    reason="cross-session intent detected",
                )
                mock_search_everything.return_value = [
                    {
                        "chunk_id": 1,
                        "document_id": 1,
                        "source_type": "transcript",
                        "source_id": 1,
                        "session_id": 1,
                        "session_name": "Meeting 1",
                        "timestamp": 1000,
                        "title": "microphone",
                        "content": "This is a transcript about project status.",
                        "rank": 1.0,
                    },
                ]

                result = self.service.ask("What did we discuss across all sessions?")

                assert result.success is True
                # Should have called unified search
                assert mock_search_everything.called
                # Should NOT call legacy search tools
                # (we can verify this indirectly since unified search succeeded)

    @patch('src.assistant.service.get_selected_model')
    @patch('src.assistant.service.OpenRouterClient')
    def test_all_sessions_falls_back_to_legacy_on_error(self, mock_client_class, mock_get_model):
        """Test that all-session path falls back to legacy when unified search fails."""
        mock_get_model.return_value = "test-model"
        mock_client_instance = self.mock_client
        mock_client_class.return_value = mock_client_instance

        with patch.object(self.service._resolver, 'resolve') as mock_resolve:
            with patch.object(self.service._tools, 'search_everything') as mock_search_everything:
                with patch.object(self.service._tools, 'search_summaries') as mock_search_summaries:
                    with patch.object(self.service._tools, 'search_transcripts') as mock_search_transcripts:
                        mock_resolve.return_value = ResolutionResult(
                            scope=ScopeResolution.ALL_SESSIONS,
                            reason="cross-session intent detected",
                        )
                        # Unified search raises exception
                        mock_search_everything.side_effect = Exception("Search unavailable")
                        # Legacy search returns results
                        mock_search_summaries.return_value = [
                            {"session_name": "Meeting 1", "content": "Summary 1"},
                        ]
                        mock_search_transcripts.return_value = [
                            {"session_name": "Meeting 1", "text": "Transcript 1", "session_id": 1},
                        ]

                        result = self.service.ask("What did we discuss across all sessions?")

                        assert result.success is True
                        # Unified search was called (and failed)
                        assert mock_search_everything.called
                        # Legacy fallback was triggered
                        assert mock_search_summaries.called
                        assert mock_search_transcripts.called

    @patch('src.assistant.service.get_selected_model')
    @patch('src.assistant.service.OpenRouterClient')
    def test_all_sessions_falls_back_to_legacy_on_empty_results(self, mock_client_class, mock_get_model):
        """Test that all-session path falls back to legacy when unified returns empty."""
        mock_get_model.return_value = "test-model"
        mock_client_instance = self.mock_client
        mock_client_class.return_value = mock_client_instance

        with patch.object(self.service._resolver, 'resolve') as mock_resolve:
            with patch.object(self.service._tools, 'search_everything') as mock_search_everything:
                with patch.object(self.service._tools, 'search_summaries') as mock_search_summaries:
                    with patch.object(self.service._tools, 'search_transcripts') as mock_search_transcripts:
                        mock_resolve.return_value = ResolutionResult(
                            scope=ScopeResolution.ALL_SESSIONS,
                            reason="cross-session intent detected",
                        )
                        # Unified search returns empty/error
                        mock_search_everything.return_value = [{"error": "No results"}]
                        # Legacy search returns results
                        mock_search_summaries.return_value = [
                            {"session_name": "Meeting 1", "content": "Summary 1"},
                        ]
                        mock_search_transcripts.return_value = [
                            {"session_name": "Meeting 1", "text": "Transcript 1", "session_id": 1},
                        ]

                        result = self.service.ask("What did we discuss across all sessions?")

                        assert result.success is True
                        # Unified search was called
                        assert mock_search_everything.called
                        # Legacy fallback was triggered
                        assert mock_search_summaries.called

    def test_ask_signature_unchanged(self):
        """Test that ask() signature is unchanged."""
        import inspect
        sig = inspect.signature(self.service.ask)
        params = list(sig.parameters.keys())

        assert "question" in params
        assert "agent_id" in params
        assert "explicit_scope" in params
        assert "active_session_id" in params
        assert "selected_session_id" in params
        assert "conversation_id" in params


class TestAnswerContractAndHandoff:
    """Tests for the Any Session answer contract and scope handoff (BU090)."""

    def setup_method(self):
        self.db = MockDatabase()
        self.mock_client = MockOpenRouterClient()
        self.service = AssistantAnswerService(db=self.db, openrouter_client=self.mock_client)

    def _run(self, mock_client_class, mock_get_model, scope, chat_result):
        from src.assistant.session_resolver import ScopeResolution, ResolutionResult

        mock_get_model.return_value = "test-model"
        mock_client_class.return_value = self.mock_client
        self.mock_client.chat_results = [chat_result]
        self.mock_client.call_count = 0

        with patch.object(self.service._resolver, "resolve") as mock_resolve:
            with patch.object(self.service._tools, "search_everything") as mock_se:
                mock_se.return_value = [{"error": "none"}]
                mock_resolve.return_value = ResolutionResult(scope=scope, session_ids=([1] if scope != ScopeResolution.ALL_SESSIONS else []), reason="test")
                return self.service.ask("What exactly did Ana say about the budget?")

    @patch("src.assistant.service.get_selected_model")
    @patch("src.assistant.service.OpenRouterClient")
    def test_contract_absent_from_specific_session_prompt(self, mock_client_class, mock_get_model):
        from src.assistant.session_resolver import ScopeResolution

        self._run(mock_client_class, mock_get_model, ScopeResolution.SELECTED_SESSION, "answer")
        system_msg = self.mock_client.calls[0]["messages"][0]["content"]
        assert "RESPONSE CONTRACT" not in system_msg

    @patch("src.assistant.service.get_selected_model")
    @patch("src.assistant.service.OpenRouterClient")
    def test_contract_present_in_any_session_prompt(self, mock_client_class, mock_get_model):
        from src.assistant.session_resolver import ScopeResolution

        self._run(mock_client_class, mock_get_model, ScopeResolution.ALL_SESSIONS, "answer\n\n@@CHRONICLE_META@@\n{\"intent\":\"overview\",\"evidence\":\"sufficient\",\"sessions\":[]}")
        system_msg = self.mock_client.calls[0]["messages"][0]["content"]
        assert "RESPONSE CONTRACT" in system_msg

    @patch("src.assistant.service.get_selected_model")
    @patch("src.assistant.service.OpenRouterClient")
    def test_any_session_uses_named_temperature(self, mock_client_class, mock_get_model):
        from src.assistant.session_resolver import ScopeResolution
        from src.config import ANY_SESSION_TEMPERATURE

        self._run(mock_client_class, mock_get_model, ScopeResolution.ALL_SESSIONS, "a\n\n@@CHRONICLE_META@@\n{}")
        assert mock_client_class.call_args.kwargs.get("temperature") == ANY_SESSION_TEMPERATURE

    @patch("src.assistant.service.get_selected_model")
    @patch("src.assistant.service.OpenRouterClient")
    def test_specific_session_keeps_client_default_temperature(self, mock_client_class, mock_get_model):
        from src.assistant.session_resolver import ScopeResolution

        self._run(mock_client_class, mock_get_model, ScopeResolution.SELECTED_SESSION, "answer")
        assert mock_client_class.call_args.kwargs.get("temperature") is None

    @patch("src.assistant.service.get_selected_model")
    @patch("src.assistant.service.OpenRouterClient")
    def test_persisted_answer_has_no_sentinel_or_metadata(self, mock_client_class, mock_get_model):
        from src.assistant.session_resolver import ScopeResolution

        result = self._run(
            mock_client_class, mock_get_model, ScopeResolution.ALL_SESSIONS,
            "Ana approved it.\n\n@@CHRONICLE_META@@\n{\"intent\":\"detail\",\"evidence\":\"partial\",\"sessions\":[2]}",
        )
        messages = self.db.get_messages(result.conversation_id)
        assistant_msg = next(m for m in messages if m["role"] == "assistant")
        assert "@@CHRONICLE_META@@" not in assistant_msg["content"]
        assert assistant_msg["content"] == "Ana approved it."
        assert result.answer == "Ana approved it."

    @patch("src.assistant.service.get_selected_model")
    @patch("src.assistant.service.OpenRouterClient")
    def test_detail_intent_surfaces_on_response(self, mock_client_class, mock_get_model):
        from src.assistant.session_resolver import ScopeResolution

        result = self._run(
            mock_client_class, mock_get_model, ScopeResolution.ALL_SESSIONS,
            "High level only.\n\n@@CHRONICLE_META@@\n{\"intent\":\"detail\",\"evidence\":\"partial\",\"sessions\":[2]}",
        )
        assert result.intent == "detail"
        assert result.evidence == "partial"
        assert result.scope_used == "any_session"


class TestScopeOffer:
    """Tests for the single-session scope switch offer (BU092)."""

    DETAIL_ANSWER = (
        "Only a high-level answer.\n\n@@CHRONICLE_META@@\n"
        '{"intent":"detail","evidence":"partial","sessions":[1]}'
    )
    # Overview answer that names no session and had enough context: no offer.
    NO_SESSION_ANSWER = (
        "Meetings generally cover planning and reviews.\n\n@@CHRONICLE_META@@\n"
        '{"intent":"overview","evidence":"sufficient","sessions":[]}'
    )
    # The answer is in none of the routed sessions: no offer, no picker.
    NOT_FOUND_ANSWER = (
        "That is not discussed in any session.\n\n@@CHRONICLE_META@@\n"
        '{"intent":"detail","evidence":"none","sessions":[]}'
    )

    def setup_method(self):
        self.db = MockDatabase()
        self.mock_client = MockOpenRouterClient()
        self.service = AssistantAnswerService(db=self.db, openrouter_client=self.mock_client)

    @staticmethod
    def _routed(*specs):
        from src.rag.router import RoutedSession

        return [
            RoutedSession(
                session_id=sid, name=name, start_time=start, score=score, reason="test"
            )
            for sid, name, start, score in specs
        ]

    def _ask(
        self,
        mock_client_class,
        mock_get_model,
        chat_result,
        routed,
        scope=None,
        conversation_id=None,
    ):
        from src.assistant.session_resolver import ScopeResolution, ResolutionResult

        scope = scope or ScopeResolution.ALL_SESSIONS
        is_any = scope == ScopeResolution.ALL_SESSIONS

        mock_get_model.return_value = "test-model"
        mock_client_class.return_value = self.mock_client
        self.mock_client.chat_results = [chat_result]
        self.mock_client.call_count = 0

        with patch.object(self.service._resolver, "resolve") as mock_resolve:
            with patch("src.assistant.service.route_sessions") as mock_route:
                with patch.object(self.service._tools, "search_everything") as mock_se:
                    with patch(
                        "src.assistant.service.build_routed_session_context"
                    ) as mock_ctx:
                        mock_route.return_value = routed
                        mock_ctx.return_value = "Routed context."
                        mock_se.return_value = [{"error": "none"}]
                        mock_resolve.return_value = ResolutionResult(
                            scope=scope,
                            session_ids=([] if is_any else [1]),
                            reason="test",
                        )
                        return self.service.ask(
                            "What exactly did Ana say?",
                            conversation_id=conversation_id,
                        )

    @patch("src.assistant.service.get_selected_model")
    @patch("src.assistant.service.OpenRouterClient")
    def test_answer_carries_offer_and_candidate_sessions(
        self, mock_client_class, mock_get_model
    ):
        result = self._ask(
            mock_client_class, mock_get_model, self.DETAIL_ANSWER,
            self._routed((1, "Budget Review", 1700, 0.9), (2, "Standup", 1800, 0.1)),
        )
        assert result.scope_offer is not None
        assert result.scope_offer.session_id == 1
        assert result.scope_offer.session_name == "Budget Review"
        assert result.scope_offer.start_time == 1700
        # The prompt's "choose another session" list stays available.
        assert [c["session_id"] for c in result.candidate_sessions] == [1, 2]

    @patch("src.assistant.service.get_selected_model")
    @patch("src.assistant.service.OpenRouterClient")
    def test_near_tied_candidates_still_carry_an_offer_plus_the_full_list(
        self, mock_client_class, mock_get_model
    ):
        result = self._ask(
            mock_client_class, mock_get_model, self.DETAIL_ANSWER,
            self._routed((1, "Budget Review", 1700, 0.50), (2, "Standup", 1800, 0.45)),
        )
        assert result.scope_offer is not None
        assert result.scope_offer.session_id == 1
        assert len(result.candidate_sessions) == 2

    @patch("src.assistant.service.get_selected_model")
    @patch("src.assistant.service.OpenRouterClient")
    def test_overview_answer_naming_no_session_carries_no_offer(
        self, mock_client_class, mock_get_model
    ):
        result = self._ask(
            mock_client_class, mock_get_model, self.NO_SESSION_ANSWER,
            self._routed((1, "Budget Review", 1700, 0.9)),
        )
        assert result.scope_offer is None

    @patch("src.assistant.service.get_selected_model")
    @patch("src.assistant.service.OpenRouterClient")
    def test_answer_not_found_in_any_session_carries_no_offer(
        self, mock_client_class, mock_get_model
    ):
        result = self._ask(
            mock_client_class, mock_get_model, self.NOT_FOUND_ANSWER,
            self._routed((1, "Budget Review", 1700, 0.9)),
        )
        assert result.scope_offer is None

    @patch("src.assistant.service.get_selected_model")
    @patch("src.assistant.service.OpenRouterClient")
    def test_specific_session_answer_never_carries_an_offer(
        self, mock_client_class, mock_get_model
    ):
        from src.assistant.session_resolver import ScopeResolution

        result = self._ask(
            mock_client_class, mock_get_model, self.DETAIL_ANSWER,
            self._routed((1, "Budget Review", 1700, 0.9)),
            scope=ScopeResolution.SELECTED_SESSION,
        )
        assert result.scope_offer is None

    def test_answer_response_scope_offer_defaults_to_none(self):
        assert AnswerResponse(success=True).scope_offer is None

    @patch("src.assistant.service.get_selected_model")
    @patch("src.assistant.service.OpenRouterClient")
    def test_decline_suppresses_offer_in_same_conversation_only(
        self, mock_client_class, mock_get_model
    ):
        routed = self._routed((1, "Budget Review", 1700, 0.9))

        first = self._ask(
            mock_client_class, mock_get_model, self.DETAIL_ANSWER, routed,
            conversation_id=None,
        )
        assert first.scope_offer is not None
        conv_id = first.conversation_id

        self.service.decline_scope_offer(conv_id, 1)

        again = self._ask(
            mock_client_class, mock_get_model, self.DETAIL_ANSWER, routed,
            conversation_id=conv_id,
        )
        assert again.scope_offer is None

        other = self._ask(
            mock_client_class, mock_get_model, self.DETAIL_ANSWER, routed,
            conversation_id=conv_id + 1,
        )
        assert other.scope_offer is not None

    @patch("src.assistant.service.get_selected_model")
    @patch("src.assistant.service.OpenRouterClient")
    def test_decline_on_new_conversation_does_not_collide_with_real_ones(
        self, mock_client_class, mock_get_model
    ):
        routed = self._routed((1, "Budget Review", 1700, 0.9))

        self.service.decline_scope_offer(None, 1)

        # A brand-new conversation (conversation_id=None) is suppressed...
        fresh = self._ask(
            mock_client_class, mock_get_model, self.DETAIL_ANSWER, routed,
            conversation_id=None,
        )
        assert fresh.scope_offer is None

        # ...while an existing conversation is untouched by that decline.
        existing = self._ask(
            mock_client_class, mock_get_model, self.DETAIL_ANSWER, routed,
            conversation_id=fresh.conversation_id,
        )
        assert existing.scope_offer is not None

    @patch("src.assistant.service.get_selected_model")
    @patch("src.assistant.service.OpenRouterClient")
    def test_offer_never_reaches_answer_text_or_stored_history(
        self, mock_client_class, mock_get_model
    ):
        result = self._ask(
            mock_client_class, mock_get_model, self.DETAIL_ANSWER,
            self._routed((1, "Budget Review", 1700, 0.9)),
        )
        assert result.scope_offer is not None
        assert result.answer == "Only a high-level answer."

        for msg in self.db.get_messages(result.conversation_id):
            assert "@@CHRONICLE_META@@" not in msg["content"]
            assert "Budget Review" not in msg["content"]
            assert "Specific" not in msg["content"]
        assistant_msg = next(
            m for m in self.db.get_messages(result.conversation_id)
            if m["role"] == "assistant"
        )
        assert assistant_msg["content"] == "Only a high-level answer."
