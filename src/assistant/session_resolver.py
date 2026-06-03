"""Session resolver for inferring user intent about session scope."""

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from .context_models import SessionCandidate


class ScopeResolution(Enum):
    """Possible outcomes from resolving session scope."""
    CURRENT_SESSION = "current_session"
    SELECTED_SESSION = "selected_session"
    ALL_SESSIONS = "all_sessions"
    AMBIGUOUS = "ambiguous"
    NEEDS_CLARIFICATION = "needs_clarification"


@dataclass
class ResolutionResult:
    """Result of session scope resolution."""
    scope: ScopeResolution
    session_ids: List[int] = field(default_factory=list)
    candidates: List["SessionCandidate"] = field(default_factory=list)
    reason: str = ""


class AssistantSessionResolver:
    """
    Resolves session scope from user questions.
    
    Determines whether a question targets:
    - Current active session
    - A specific selected session
    - All sessions
    - Multiple ambiguous sessions
    
    Returns needs_clarification when no session can be inferred.
    """

    # Patterns indicating cross-session intent (should be checked first)
    CROSS_SESSION_PATTERNS = [
        "all sessions",
        "all of my sessions",
        "every session",
        "previous sessions",
        "past sessions",
        "earlier sessions",
        "compare",
        "comparing",
        "where did we discuss",
        "where did i mention",
        "across sessions",
        "in all sessions",
        "throughout sessions",
    ]

    # Patterns indicating they want to search everything
    ALL_SESSIONS_PATTERNS = [
        "all sessions",
        "every session",
        "everything",
        "all of my",
        "all the sessions",
        "search everything",
    ]

    def __init__(self, db: Any = None):
        """
        Initialize resolver with optional database for session inference.
        
        Args:
            db: Database instance with find_sessions method. If None,
                session inference by keyword will not be available.
        """
        self._db = db

    def resolve(
        self,
        question: str,
        active_session_id: Optional[int] = None,
        selected_session_id: Optional[int] = None,
        explicit_scope: Optional[str] = None,
    ) -> ResolutionResult:
        """
        Resolve the session scope for a question.
        
        Args:
            question: The user's question/input
            active_session_id: Currently active session ID, if any
            selected_session_id: Currently selected session ID in UI, if any
            explicit_scope: Explicit scope hint - "current_session", "any_session", or None
            
        Returns:
            ResolutionResult with scope and candidate sessions
        """
        question_lower = question.lower().strip()

        # Step 1: Handle explicit scope overrides
        if explicit_scope == "current_session":
            return self._resolve_current_session(
                active_session_id, selected_session_id
            )

        if explicit_scope == "any_session":
            return ResolutionResult(
                scope=ScopeResolution.ALL_SESSIONS,
                reason="explicit_scope requested all sessions",
            )

        # Step 2: Check for cross-session wording first (before single-session inference)
        if self._is_cross_session_intent(question_lower):
            return ResolutionResult(
                scope=ScopeResolution.ALL_SESSIONS,
                reason="cross-session intent detected in question",
            )

        # Step 3: Try to infer session from question text
        if self._db is not None:
            candidates = self._search_sessions(question_lower)
            
            if len(candidates) == 1:
                # Single match - use it
                return ResolutionResult(
                    scope=ScopeResolution.SELECTED_SESSION,
                    session_ids=[candidates[0].session_id],
                    candidates=candidates,
                    reason=f"matched session: {candidates[0].session_name}",
                )
            elif len(candidates) > 1:
                # Multiple matches - ambiguous
                return ResolutionResult(
                    scope=ScopeResolution.AMBIGUOUS,
                    session_ids=[c.session_id for c in candidates],
                    candidates=candidates,
                    reason=f"multiple candidates found ({len(candidates)})",
                )

        # Step 4: Fall back to active/selected session if available
        if active_session_id is not None:
            return ResolutionResult(
                scope=ScopeResolution.CURRENT_SESSION,
                session_ids=[active_session_id],
                reason="using active session",
            )

        if selected_session_id is not None:
            return ResolutionResult(
                scope=ScopeResolution.SELECTED_SESSION,
                session_ids=[selected_session_id],
                reason="using selected session",
            )

        # Step 5: Cannot determine - ask for clarification
        return ResolutionResult(
            scope=ScopeResolution.NEEDS_CLARIFICATION,
            reason="no session could be inferred from question or context",
        )

    def _resolve_current_session(
        self,
        active_session_id: Optional[int],
        selected_session_id: Optional[int],
    ) -> ResolutionResult:
        """Resolve to current/active session when explicit_scope is current_session."""
        if active_session_id is not None:
            return ResolutionResult(
                scope=ScopeResolution.CURRENT_SESSION,
                session_ids=[active_session_id],
                reason="using active session (explicit current_session)",
            )

        if selected_session_id is not None:
            return ResolutionResult(
                scope=ScopeResolution.SELECTED_SESSION,
                session_ids=[selected_session_id],
                reason="using selected session (fallback when no active session)",
            )

        return ResolutionResult(
            scope=ScopeResolution.NEEDS_CLARIFICATION,
            reason="explicit current_session but no active or selected session available",
        )

    def _is_cross_session_intent(self, question: str) -> bool:
        """Check if question indicates cross-session intent."""
        for pattern in self.CROSS_SESSION_PATTERNS:
            if pattern in question:
                return True
        return False

    def _search_sessions(self, question: str) -> List["SessionCandidate"]:
        """
        Search for relevant sessions based on question keywords.
        
        Uses database find_sessions method if available.
        """
        if self._db is None:
            return []

        # Extract search terms from question
        search_terms = self._extract_search_terms(question)
        
        if not search_terms:
            return []

        try:
            # Use first significant term for search
            results = self._db.find_sessions(search_terms[0], limit=5)
            
            # Convert to SessionCandidate objects
            from .context_models import SessionCandidate
            
            candidates = []
            for row in results:
                candidates.append(SessionCandidate(
                    session_id=row["id"],
                    session_name=row["name"],
                    start_timestamp=row["start_time"],
                    relevance_score=1.0,  # Default score since find_sessions doesn't return score
                ))
            
            return candidates
            
        except Exception:
            # Database error - return empty list
            return []

    def _extract_search_terms(self, question: str) -> List[str]:
        """
        Extract meaningful search terms from question.
        
        Filters out common stop words and returns significant words.
        """
        # Common stop words to filter
        stop_words = {
            "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
            "have", "has", "had", "do", "does", "did", "will", "would", "could",
            "should", "may", "might", "must", "shall", "can", "need", "dare",
            "to", "of", "in", "for", "on", "with", "at", "by", "from", "as",
            "into", "through", "during", "before", "after", "above", "below",
            "between", "under", "again", "further", "then", "once", "here",
            "there", "when", "where", "why", "how", "all", "each", "few",
            "more", "most", "other", "some", "such", "no", "nor", "not",
            "only", "own", "same", "so", "than", "too", "very", "just",
            "what", "which", "who", "whom", "this", "that", "these", "those",
            "am", "and", "but", "if", "or", "because", "as", "until", "while",
            "about", "against", "out", "up", "down", "over", "any", "both",
            "each", "few", "more", "most", "other", "some", "such", "only",
            "i", "me", "my", "myself", "we", "our", "ours", "ourselves", "you",
            "your", "yours", "yourself", "yourselves", "he", "him", "his",
            "himself", "she", "her", "hers", "herself", "it", "its", "itself",
            "they", "them", "their", "theirs", "themselves", "themself",
            "show", "shows", "showed", "show me", "find", "find me", "get",
            "give", "tell", "tell me", "remind", "remember", "when was", "when did",
            "did we", "do we", "can you", "could you", "would you", "should you",
            # Common question words that aren't useful for session search
            "what", "when", "where", "why", "how", "who", "which",
        }

        # Split and filter
        words = question.split()
        terms = [
            w.strip(".,!?;:()[]{}")  # Remove punctuation
            for w in words
            if len(w) > 3 and w.lower() not in stop_words
        ]
        
        return terms[:3]  # Limit to 3 terms
