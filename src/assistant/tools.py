"""Whitelisted retrieval tools for assistant queries.

Provides controlled access to session data without exposing raw database
or SQL execution capabilities to the answer service.
"""

from typing import Any, Dict, List, Optional, Protocol

from .context import AssistantContextRetriever
from .context_models import (
    AssistantContext,
)


# Default limits for tool outputs
DEFAULT_SESSION_LIMIT = 10
MAX_SESSION_LIMIT = 50
DEFAULT_TRANSCRIPT_LIMIT = 10
MAX_TRANSCRIPT_LIMIT = 50
DEFAULT_SUMMARY_LIMIT = 10
MAX_SUMMARY_LIMIT = 50
DEFAULT_SCREENSHOT_LIMIT = 10
MAX_SCREENSHOT_LIMIT = 50
DEFAULT_CONTEXT_TRANSCRIPT_LIMIT = 20
MAX_TIMESTAMP_DIFF = 60  # seconds


class DatabaseLike(Protocol):
    """Protocol for database access required by retrieval tools."""

    def get_session(self, session_id: int) -> Dict[str, Any]: ...
    def get_summaries(self, session_id: int) -> List[Dict[str, Any]]: ...
    def get_screenshots(self, session_id: int) -> List[Dict[str, Any]]: ...
    def get_transcripts(self, session_id: int) -> List[Dict[str, Any]]: ...
    def find_sessions(self, query: str, limit: int) -> List[Dict[str, Any]]: ...
    def search_transcripts(
        self, query: str, limit: int, session_id: Optional[int]
    ) -> List[Dict[str, Any]]: ...
    def search_summaries(
        self, query: str, limit: int, session_id: Optional[int]
    ) -> List[Dict[str, Any]]: ...
    def list_sessions(self) -> List[Dict[str, Any]]: ...


class AssistantRetrievalTools:
    """
    Whitelisted retrieval tools for answering assistant queries.
    
    Each tool provides safe access to session data with validation,
    bounded limits, and no arbitrary SQL execution.
    """

    def __init__(self, db: DatabaseLike):
        """
        Initialize retrieval tools with a database instance.
        
        Args:
            db: Database instance implementing required methods.
        """
        self._db = db
        self._retriever = AssistantContextRetriever(db)

    def find_sessions(
        self, query: str, limit: int = DEFAULT_SESSION_LIMIT
    ) -> List[Dict[str, Any]]:
        """
        Find sessions by name, summary, or transcript content.
        
        Args:
            query: Search term to match against session data.
            limit: Maximum number of sessions to return (capped at 50).
            
        Returns:
            List of session dictionaries with id, name, start_time, and matched content.
            
        Raises:
            ValueError: If query is empty or limit is invalid.
        """
        # Validate query
        if not query or not query.strip():
            raise ValueError("Query cannot be empty")

        # Validate and cap limit
        if not isinstance(limit, int):
            raise ValueError("Limit must be an integer")
        if limit < 1:
            raise ValueError("Limit must be at least 1")
        limit = min(limit, MAX_SESSION_LIMIT)

        # Sanitize query
        query = query.strip()

        try:
            results = self._db.find_sessions(query, limit=limit)
            return [
                {
                    "id": r.get("id"),
                    "name": r.get("name"),
                    "start_time": r.get("start_time"),
                    "matched_summary": r.get("matched_summary"),
                    "matched_transcript": r.get("matched_transcript"),
                }
                for r in results
            ]
        except Exception as e:
            return [{"error": f"Search failed: {str(e)}"}]

    def list_sessions(
        self, limit: int = DEFAULT_SESSION_LIMIT
    ) -> List[Dict[str, Any]]:
        """
        List all sessions with metadata.
        
        Args:
            limit: Maximum number of sessions to return (capped at 50).
            
        Returns:
            List of session dictionaries with id, name, start_time, 
            transcription_status, and summary_status.
        """
        # Validate and cap limit
        if not isinstance(limit, int):
            raise ValueError("Limit must be an integer")
        if limit < 1:
            raise ValueError("Limit must be at least 1")
        limit = min(limit, MAX_SESSION_LIMIT)

        try:
            results = self._db.list_sessions()
            # Apply limit and format results
            return [
                {
                    "id": r.get("id"),
                    "name": r.get("name"),
                    "start_time": r.get("start_time"),
                    "end_time": r.get("end_time"),
                    "status": r.get("status"),
                    "transcription_status": r.get("transcription_status"),
                    "summary_status": r.get("summary_status"),
                }
                for r in results[:limit]
            ]
        except Exception as e:
            return [{"error": f"Session listing failed: {str(e)}"}]

    def get_session_context(
        self,
        session_id: int,
        question: str,
        transcript_limit: int = DEFAULT_CONTEXT_TRANSCRIPT_LIMIT,
    ) -> Dict[str, Any]:
        """
        Get bounded context for a specific session.
        
        Builds context from a single session including summaries, relevant
        transcripts (filtered by keywords), and nearby screenshots.
        
        Args:
            session_id: ID of the session to retrieve context for.
            question: User question to filter relevant content.
            transcript_limit: Maximum transcripts to include (capped at 50).
            
        Returns:
            Dictionary with session info, summaries, transcripts, and screenshot refs.
            
        Raises:
            ValueError: If session_id is invalid.
        """
        # Validate session_id
        if not isinstance(session_id, int) or session_id < 1:
            raise ValueError("Invalid session_id")

        # Validate and cap transcript limit
        if not isinstance(transcript_limit, int):
            raise ValueError("transcript_limit must be an integer")
        if transcript_limit < 1:
            raise ValueError("transcript_limit must be at least 1")
        transcript_limit = min(transcript_limit, MAX_TRANSCRIPT_LIMIT)

        try:
            context = self._retriever.build_session_context(session_id, question)
            return self._context_to_dict(context)
        except Exception as e:
            return {"error": f"Context retrieval failed: {str(e)}"}

    def search_transcripts(
        self,
        query: str,
        limit: int = DEFAULT_TRANSCRIPT_LIMIT,
        session_id: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        Search transcripts by text content.
        
        Args:
            query: Search term to match against transcript text.
            limit: Maximum number of results (capped at 50).
            session_id: Optional session ID to limit search to specific session.
            
        Returns:
            List of transcript dictionaries with session metadata.
            
        Raises:
            ValueError: If query is empty or limit is invalid.
        """
        # Validate query
        if not query or not query.strip():
            raise ValueError("Query cannot be empty")

        # Validate and cap limit
        if not isinstance(limit, int):
            raise ValueError("Limit must be an integer")
        if limit < 1:
            raise ValueError("Limit must be at least 1")
        limit = min(limit, MAX_TRANSCRIPT_LIMIT)

        # Validate session_id if provided
        if session_id is not None:
            if not isinstance(session_id, int) or session_id < 1:
                raise ValueError("Invalid session_id")

        # Sanitize query
        query = query.strip()

        try:
            results = self._db.search_transcripts(query, limit=limit, session_id=session_id)
            return [
                {
                    "session_id": r.get("session_id"),
                    "session_name": r.get("session_name"),
                    "timestamp": r.get("timestamp"),
                    "source": r.get("source"),
                    "text": r.get("text"),
                }
                for r in results
            ]
        except Exception as e:
            return [{"error": f"Transcript search failed: {str(e)}"}]

    def search_summaries(
        self,
        query: str,
        limit: int = DEFAULT_SUMMARY_LIMIT,
        session_id: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        Search summaries by content.
        
        Args:
            query: Search term to match against summary content.
            limit: Maximum number of results (capped at 50).
            session_id: Optional session ID to limit search to specific session.
            
        Returns:
            List of summary dictionaries with session metadata.
            
        Raises:
            ValueError: If query is empty or limit is invalid.
        """
        # Validate query
        if not query or not query.strip():
            raise ValueError("Query cannot be empty")

        # Validate and cap limit
        if not isinstance(limit, int):
            raise ValueError("Limit must be an integer")
        if limit < 1:
            raise ValueError("Limit must be at least 1")
        limit = min(limit, MAX_SUMMARY_LIMIT)

        # Validate session_id if provided
        if session_id is not None:
            if not isinstance(session_id, int) or session_id < 1:
                raise ValueError("Invalid session_id")

        # Sanitize query
        query = query.strip()

        try:
            results = self._db.search_summaries(query, limit=limit, session_id=session_id)
            return [
                {
                    "session_id": r.get("session_id"),
                    "session_name": r.get("session_name"),
                    "summary_type": r.get("summary_type"),
                    "content": r.get("content"),
                    "created_at": r.get("created_at"),
                }
                for r in results
            ]
        except Exception as e:
            return [{"error": f"Summary search failed: {str(e)}"}]

    def get_screenshots_near(
        self,
        session_id: int,
        timestamp: int,
        tolerance_seconds: int = DEFAULT_SCREENSHOT_LIMIT,
        limit: int = DEFAULT_SCREENSHOT_LIMIT,
    ) -> List[Dict[str, Any]]:
        """
        Get screenshots near a specific timestamp within a session.
        
        Args:
            session_id: ID of the session to search screenshots for.
            timestamp: Reference timestamp (Unix epoch seconds).
            tolerance_seconds: Time window in seconds to search around timestamp.
            limit: Maximum number of screenshots to return (capped at 50).
            
        Returns:
            List of screenshot dictionaries with timestamps and filepaths.
            
        Raises:
            ValueError: If session_id or timestamp is invalid.
        """
        # Validate session_id
        if not isinstance(session_id, int) or session_id < 1:
            raise ValueError("Invalid session_id")

        # Validate timestamp
        if not isinstance(timestamp, (int, float)) or timestamp < 0:
            raise ValueError("Invalid timestamp")

        # Validate and cap tolerance
        if not isinstance(tolerance_seconds, (int, float)):
            raise ValueError("tolerance_seconds must be a number")
        if tolerance_seconds < 1:
            raise ValueError("tolerance_seconds must be at least 1")
        tolerance_seconds = min(int(tolerance_seconds), MAX_TIMESTAMP_DIFF)

        # Validate and cap limit
        if not isinstance(limit, int):
            raise ValueError("Limit must be an integer")
        if limit < 1:
            raise ValueError("Limit must be at least 1")
        limit = min(limit, MAX_SCREENSHOT_LIMIT)

        try:
            # Get all screenshots for the session
            screenshot_rows = self._db.get_screenshots(session_id)
            
            # Filter by timestamp within tolerance
            nearby = []
            for row in screenshot_rows:
                row_timestamp = row.get("timestamp", 0)
                if abs(row_timestamp - timestamp) <= tolerance_seconds:
                    nearby.append({
                        "session_id": session_id,
                        "timestamp": row_timestamp,
                        "filepath": row.get("filepath"),
                        "description": row.get("description"),
                    })
                    if len(nearby) >= limit:
                        break

            return nearby
        except Exception as e:
            return [{"error": f"Screenshot retrieval failed: {str(e)}"}]

    def _context_to_dict(self, context: AssistantContext) -> Dict[str, Any]:
        """Convert AssistantContext to dictionary for tool output."""
        result = {
            "query": context.query,
            "sessions": [],
            "transcripts": [],
            "summaries": [],
            "screenshots": [],
        }

        # Add sessions
        for s in context.sessions:
            result["sessions"].append({
                "session_id": s.session_id,
                "session_name": s.session_name,
                "start_timestamp": s.start_timestamp,
                "relevance_score": s.relevance_score,
            })

        # Add transcripts
        for t in context.transcripts:
            result["transcripts"].append({
                "session_id": t.session_id,
                "session_name": t.session_name,
                "timestamp": t.timestamp,
                "source": t.source,
                "text": t.text,
            })

        # Add summaries
        for s in context.summaries:
            result["summaries"].append({
                "session_id": s.session_id,
                "session_name": s.session_name,
                "summary_type": s.summary_type,
                "content": s.content,
            })

        # Add screenshots
        for sc in context.screenshots:
            result["screenshots"].append({
                "session_id": sc.session_id,
                "session_name": sc.session_name,
                "timestamp": sc.timestamp,
                "filepath": sc.filepath,
                "description": sc.description,
            })

        return result
