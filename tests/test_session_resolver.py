"""Unit tests for AssistantSessionResolver."""

import unittest
from unittest.mock import MagicMock

from src.assistant.session_resolver import (
    AssistantSessionResolver,
    ResolutionResult,
    ScopeResolution,
)


class TestAssistantSessionResolver(unittest.TestCase):
    """Tests for the session resolver."""

    def test_explicit_current_session_uses_active_session(self):
        """Test selected session wins when scope is current_session and selected_session_id exists."""
        resolver = AssistantSessionResolver()
        
        result = resolver.resolve(
            question="What did we discuss?",
            active_session_id=1,
            selected_session_id=2,
            explicit_scope="current_session",
        )
        
        self.assertEqual(result.scope, ScopeResolution.CURRENT_SESSION)
        self.assertEqual(result.session_ids, [1])
        self.assertIn("active session", result.reason.lower())

    def test_explicit_current_session_falls_back_to_selected(self):
        """Test fallback to selected when no active session."""
        resolver = AssistantSessionResolver()
        
        result = resolver.resolve(
            question="What did we discuss?",
            active_session_id=None,
            selected_session_id=2,
            explicit_scope="current_session",
        )
        
        self.assertEqual(result.scope, ScopeResolution.SELECTED_SESSION)
        self.assertEqual(result.session_ids, [2])
        self.assertIn("selected session", result.reason.lower())

    def test_explicit_current_session_needs_clarification_when_none(self):
        """Test needs_clarification when explicit current_session but no session available."""
        resolver = AssistantSessionResolver()
        
        result = resolver.resolve(
            question="What did we discuss?",
            active_session_id=None,
            selected_session_id=None,
            explicit_scope="current_session",
        )
        
        self.assertEqual(result.scope, ScopeResolution.NEEDS_CLARIFICATION)

    def test_explicit_any_session_returns_all_sessions(self):
        """Test any_session returns all_sessions scope."""
        resolver = AssistantSessionResolver()
        
        result = resolver.resolve(
            question="What did we discuss?",
            active_session_id=1,
            selected_session_id=2,
            explicit_scope="any_session",
        )
        
        self.assertEqual(result.scope, ScopeResolution.ALL_SESSIONS)
        self.assertIn("all sessions", result.reason.lower())

    def test_cross_session_intent_returns_all_sessions(self):
        """Test cross-session intent detected before single-session inference."""
        resolver = AssistantSessionResolver()
        
        result = resolver.resolve(
            question="Compare all sessions",
            active_session_id=1,
            selected_session_id=2,
            explicit_scope=None,
        )
        
        self.assertEqual(result.scope, ScopeResolution.ALL_SESSIONS)

    def test_cross_session_intent_previous_sessions(self):
        """Test 'previous sessions' phrasing."""
        resolver = AssistantSessionResolver()
        
        result = resolver.resolve(
            question="What did we discuss in previous sessions?",
            active_session_id=1,
            selected_session_id=2,
            explicit_scope=None,
        )
        
        self.assertEqual(result.scope, ScopeResolution.ALL_SESSIONS)

    def test_cross_session_intent_where_did_we_discuss(self):
        """Test 'where did we discuss' phrasing."""
        resolver = AssistantSessionResolver()
        
        result = resolver.resolve(
            question="Where did we discuss the project?",
            active_session_id=1,
            selected_session_id=2,
            explicit_scope=None,
        )
        
        self.assertEqual(result.scope, ScopeResolution.ALL_SESSIONS)

    def test_no_context_uses_active_session(self):
        """Test falls back to active session when no explicit scope."""
        resolver = AssistantSessionResolver()
        
        result = resolver.resolve(
            question="What is my name?",
            active_session_id=1,
            selected_session_id=2,
            explicit_scope=None,
        )
        
        self.assertEqual(result.scope, ScopeResolution.CURRENT_SESSION)
        self.assertEqual(result.session_ids, [1])

    def test_no_active_uses_selected_session(self):
        """Test falls back to selected session when no active session."""
        resolver = AssistantSessionResolver()
        
        result = resolver.resolve(
            question="What is my name?",
            active_session_id=None,
            selected_session_id=2,
            explicit_scope=None,
        )
        
        self.assertEqual(result.scope, ScopeResolution.SELECTED_SESSION)
        self.assertEqual(result.session_ids, [2])

    def test_no_session_needs_clarification(self):
        """Test needs_clarification when no session can be inferred."""
        resolver = AssistantSessionResolver()
        
        result = resolver.resolve(
            question="What is my name?",
            active_session_id=None,
            selected_session_id=None,
            explicit_scope=None,
        )
        
        self.assertEqual(result.scope, ScopeResolution.NEEDS_CLARIFICATION)

    def test_single_candidate_from_db(self):
        """Test single candidate returns selected session scope."""
        mock_db = MagicMock()
        mock_db.find_sessions.return_value = [
            {"id": 5, "name": "Team Meeting", "start_time": 1234567890}
        ]
        
        resolver = AssistantSessionResolver(db=mock_db)
        
        result = resolver.resolve(
            question="What about Team Meeting?",
            active_session_id=None,
            selected_session_id=None,
            explicit_scope=None,
        )
        
        self.assertEqual(result.scope, ScopeResolution.SELECTED_SESSION)
        self.assertEqual(result.session_ids, [5])
        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(result.candidates[0].session_name, "Team Meeting")

    def test_multiple_candidates_returns_ambiguous(self):
        """Test multiple candidates returns ambiguous scope."""
        mock_db = MagicMock()
        mock_db.find_sessions.return_value = [
            {"id": 1, "name": "Team Meeting", "start_time": 1234567890},
            {"id": 2, "name": "Team Standup", "start_time": 1234567891},
        ]
        
        resolver = AssistantSessionResolver(db=mock_db)
        
        result = resolver.resolve(
            question="What about the team meeting?",
            active_session_id=None,
            selected_session_id=None,
            explicit_scope=None,
        )
        
        self.assertEqual(result.scope, ScopeResolution.AMBIGUOUS)
        self.assertEqual(len(result.session_ids), 2)
        self.assertEqual(len(result.candidates), 2)

    def test_no_candidates_falls_back_to_context(self):
        """Test no candidates falls back to active/selected session."""
        mock_db = MagicMock()
        mock_db.find_sessions.return_value = []
        
        resolver = AssistantSessionResolver(db=mock_db)
        
        result = resolver.resolve(
            question="What about something unrelated?",
            active_session_id=1,
            selected_session_id=2,
            explicit_scope=None,
        )
        
        self.assertEqual(result.scope, ScopeResolution.CURRENT_SESSION)
        self.assertEqual(result.session_ids, [1])

    def test_resolver_never_guesses_ambiguous(self):
        """Test resolver returns ambiguous rather than guessing."""
        # When there are multiple candidates but no active/selected, should return ambiguous
        mock_db = MagicMock()
        mock_db.find_sessions.return_value = [
            {"id": 1, "name": "Meeting A", "start_time": 1234567890},
            {"id": 2, "name": "Meeting B", "start_time": 1234567891},
            {"id": 3, "name": "Meeting C", "start_time": 1234567892},
        ]
        
        resolver = AssistantSessionResolver(db=mock_db)
        
        result = resolver.resolve(
            question="meeting",
            active_session_id=None,
            selected_session_id=None,
            explicit_scope=None,
        )
        
        self.assertEqual(result.scope, ScopeResolution.AMBIGUOUS)
        self.assertEqual(len(result.candidates), 3)

    def test_db_error_returns_empty(self):
        """Test database error is handled gracefully."""
        mock_db = MagicMock()
        mock_db.find_sessions.side_effect = Exception("Database error")
        
        resolver = AssistantSessionResolver(db=mock_db)
        
        result = resolver.resolve(
            question="Team Meeting",
            active_session_id=None,
            selected_session_id=None,
            explicit_scope=None,
        )
        
        # Should fall back to needs_clarification when no active/selected
        self.assertEqual(result.scope, ScopeResolution.NEEDS_CLARIFICATION)

    def test_extract_search_terms_filters_stop_words(self):
        """Test search term extraction filters common words."""
        resolver = AssistantSessionResolver()
        
        terms = resolver._extract_search_terms("what did we discuss in the meeting")
        
        # Should filter out common words and keep significant terms
        self.assertIn("discuss", terms)
        self.assertIn("meeting", terms)
        # Common words should be filtered
        self.assertNotIn("what", terms)
        self.assertNotIn("did", terms)
        self.assertNotIn("the", terms)

    def test_extract_search_terms_handles_punctuation(self):
        """Test search term extraction handles punctuation."""
        resolver = AssistantSessionResolver()
        
        terms = resolver._extract_search_terms("what about the project?!")
        
        self.assertIn("project", terms)

    def test_deterministic_behavior(self):
        """Test that same inputs produce same outputs."""
        mock_db = MagicMock()
        mock_db.find_sessions.return_value = [
            {"id": 1, "name": "Test Session", "start_time": 1234567890}
        ]
        
        resolver = AssistantSessionResolver(db=mock_db)
        
        result1 = resolver.resolve(
            question="Test Session",
            active_session_id=None,
            selected_session_id=None,
            explicit_scope=None,
        )
        
        result2 = resolver.resolve(
            question="Test Session",
            active_session_id=None,
            selected_session_id=None,
            explicit_scope=None,
        )
        
        self.assertEqual(result1.scope, result2.scope)
        self.assertEqual(result1.session_ids, result2.session_ids)

    def test_multi_term_search_first_tries_full_phrase(self):
        """Test multi-term question first searches joined phrase."""
        mock_db = MagicMock()
        # Full phrase search returns results
        mock_db.find_sessions.side_effect = [
            [{"id": 10, "name": "Project Alpha Meeting", "start_time": 1234567890}],  # Full phrase
            [{"id": 1, "name": "Project", "start_time": 1234567891}],  # Individual term "project"
            [{"id": 2, "name": "Alpha", "start_time": 1234567892}],  # Individual term "alpha"
        ]
        
        resolver = AssistantSessionResolver(db=mock_db)
        
        result = resolver.resolve(
            question="Project Alpha Meeting",
            active_session_id=None,
            selected_session_id=None,
            explicit_scope=None,
        )
        
        # First call should be full phrase
        self.assertEqual(mock_db.find_sessions.call_args_list[0][0][0], "project alpha meeting")
        self.assertEqual(result.candidates[0].session_id, 10)

    def test_multi_term_falls_back_to_individual_terms(self):
        """Test falls back to individual terms when full phrase returns empty."""
        mock_db = MagicMock()
        # Full phrase returns empty, individual terms return results
        mock_db.find_sessions.side_effect = [
            [],  # Full phrase returns empty
            [{"id": 1, "name": "Project Meeting", "start_time": 1234567891}],
            [{"id": 2, "name": "Alpha Meeting", "start_time": 1234567892}],
            [{"id": 3, "name": "Meeting", "start_time": 1234567893}],
        ]
        
        resolver = AssistantSessionResolver(db=mock_db)
        
        result = resolver.resolve(
            question="Project Alpha Meeting",
            active_session_id=None,
            selected_session_id=None,
            explicit_scope=None,
        )
        
        # First call should be full phrase, then individual terms
        self.assertEqual(mock_db.find_sessions.call_args_list[0][0][0], "project alpha meeting")
        # Should have at least tried individual terms after empty full phrase
        self.assertGreaterEqual(len(mock_db.find_sessions.call_args_list), 2)
        self.assertGreater(len(result.candidates), 0)

    def test_deduplication_by_session_id(self):
        """Test candidates are deduplicated by session_id."""
        mock_db = MagicMock()
        # Different searches return same session with different IDs (shouldn't happen but testing)
        mock_db.find_sessions.side_effect = [
            [{"id": 1, "name": "Team Meeting", "start_time": 1234567890}],
            [{"id": 1, "name": "Team Meeting", "start_time": 1234567890}],
            [{"id": 2, "name": "Standup", "start_time": 1234567891}],
        ]
        
        resolver = AssistantSessionResolver(db=mock_db)
        
        result = resolver.resolve(
            question="Team Meeting Standup",
            active_session_id=None,
            selected_session_id=None,
            explicit_scope=None,
        )
        
        # Should only have unique session IDs
        session_ids = [c.session_id for c in result.candidates]
        self.assertEqual(len(session_ids), len(set(session_ids)))

    def test_db_exception_is_contained(self):
        """Test database exception is handled gracefully."""
        mock_db = MagicMock()
        mock_db.find_sessions.side_effect = Exception("Database error")
        
        resolver = AssistantSessionResolver(db=mock_db)
        
        result = resolver.resolve(
            question="Team Meeting",
            active_session_id=None,
            selected_session_id=None,
            explicit_scope=None,
        )
        
        # Should fall back to needs_clarification without crashing
        self.assertEqual(result.scope, ScopeResolution.NEEDS_CLARIFICATION)


if __name__ == "__main__":
    unittest.main()
