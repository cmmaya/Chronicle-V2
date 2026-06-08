"""Assistant context retrieval for single session queries."""

from typing import List, Optional, Any, Protocol

from .context_models import (
    AssistantContext,
    SessionCandidate,
    TranscriptExcerpt,
    SummaryExcerpt,
    ScreenshotReference,
    ConversationTurn,
)
from .rag_models import RetrievedChunk, SourceType


class DatabaseProtocol(Protocol):
    """Protocol for database access required by AssistantContextRetriever."""

    def get_session(self, session_id: int) -> dict[str, Any]: ...
    def get_summaries(self, session_id: int) -> List[dict[str, Any]]: ...
    def get_screenshots(self, session_id: int) -> List[dict[str, Any]]: ...
    def get_transcripts(self, session_id: int) -> List[dict[str, Any]]: ...
    def get_messages(self, conversation_id: int) -> List[dict[str, Any]]: ...
    def search_rag_fts(self, query: str, limit: int = 20,
                      session_id: Optional[int] = None) -> List[dict[str, Any]]: ...


# Default limits for context building
DEFAULT_TRANSCRIPT_LIMIT = 20  # Maximum transcript excerpts to include
DEFAULT_KEYWORD_MATCHES = 5    # Maximum keyword-matched excerpts
MAX_TRANSCRIPT_LENGTH = 500    # Maximum characters per transcript excerpt
DEFAULT_CONVERSATION_LIMIT = 10  # Maximum conversation turns to include


class AssistantContextRetriever:
    """Retrieves bounded context for a single session.

    Builds context from SQLite-backed resources (summaries, transcripts,
    screenshots) for a specific session to help answer user queries.
    """

    def __init__(self, db: DatabaseProtocol):
        """Initialize retriever with a database instance.

        Args:
            db: Database instance implementing required methods.
        """
        self._db = db

    def build_session_context(
        self,
        session_id: int,
        question: str,
        conversation_id: Optional[int] = None,
    ) -> AssistantContext:
        """Build context for a specific session based on user question.

        Args:
            session_id: ID of the session to retrieve context for.
            question: User question to filter relevant content.
            conversation_id: Optional conversation ID to load prior chat history.

        Returns:
            AssistantContext with session details, summaries, filtered
            transcripts, screenshot references, and conversation history.
        """
        # Get session info
        try:
            session = self._db.get_session(session_id)
        except Exception:
            session = {}

        session_name = session.get("name", f"Session {session_id}")
        start_timestamp = session.get("start_time", 0)

        # Build session candidate
        session_candidate = SessionCandidate(
            session_id=session_id,
            session_name=session_name,
            start_timestamp=start_timestamp,
            relevance_score=1.0,
        )

        # Get summaries
        summaries = self._get_summaries(session_id, session_name)

        # Get transcripts - try RAG first, then fallback to legacy
        transcripts = self._get_transcripts_rag_first(
            session_id, session_name, question
        )

        # Get screenshots near relevant transcript timestamps
        screenshots = self._get_screenshots_near_transcripts(
            session_id, session_name, transcripts
        )

        # Get conversation history
        conversation_history = self._get_conversation_history(conversation_id)

        return AssistantContext(
            query=question,
            sessions=[session_candidate],
            transcripts=transcripts,
            summaries=summaries,
            screenshots=screenshots,
            conversation_history=conversation_history,
        )

    def _get_summaries(
        self, session_id: int, session_name: str
    ) -> List[SummaryExcerpt]:
        """Retrieve summaries for a session.

        Args:
            session_id: ID of the session.
            session_name: Name of the session for context.

        Returns:
            List of SummaryExcerpt objects.
        """
        try:
            summary_rows = self._db.get_summaries(session_id)
        except Exception:
            return []

        excerpts = []
        for row in summary_rows:
            excerpts.append(
                SummaryExcerpt(
                    session_id=session_id,
                    session_name=session_name,
                    summary_type=row.get("summary_type", "full"),
                    content=row.get("content", ""),
                )
            )
        return excerpts

    def _get_transcripts(
        self, session_id: int, session_name: str, question: str
    ) -> List[TranscriptExcerpt]:
        """Retrieve and filter transcripts for a session.

        Uses simple keyword matching to filter relevant transcripts
        and limits the number of excerpts to control prompt size.

        Args:
            session_id: ID of the session.
            session_name: Name of the session for context.
            question: User question for keyword matching.

        Returns:
            List of filtered TranscriptExcerpt objects.
        """
        # Try to get transcripts from database
        try:
            transcript_rows = self._db.get_transcripts(session_id)
        except AttributeError:
            # Database doesn't have get_transcripts method
            raise NotImplementedError(
                "TODO: BU039 - Implement database.get_transcripts() method "
                "for transcript retrieval"
            )
        except Exception:
            return []

        if not transcript_rows:
            return []

        # Extract keywords from question (simple approach: words > 3 chars)
        keywords = self._extract_keywords(question)

        # Score and filter transcripts
        scored_transcripts = []
        for row in transcript_rows:
            text = row.get("text", "")
            timestamp = row.get("timestamp", 0)
            source = row.get("source", "microphone")

            # Calculate relevance score based on keyword matches
            score = 0
            if keywords:
                text_lower = text.lower()
                for keyword in keywords:
                    if keyword.lower() in text_lower:
                        score += 1

            scored_transcripts.append({
                "text": text,
                "timestamp": timestamp,
                "source": source,
                "score": score,
            })

        # Sort by score (descending) then by timestamp (ascending)
        scored_transcripts.sort(key=lambda x: (-x["score"], x["timestamp"]))

        # Take top matches based on keywords, fallback to recent if no keywords
        if keywords:
            # Take up to DEFAULT_KEYWORD_MATCHES keyword matches
            top_transcripts = scored_transcripts[:DEFAULT_KEYWORD_MATCHES]
        else:
            # No keywords, take most recent up to limit
            top_transcripts = scored_transcripts[-DEFAULT_TRANSCRIPT_LIMIT:]

        # Truncate long transcripts
        excerpts = []
        for t in top_transcripts:
            text = t["text"]
            if len(text) > MAX_TRANSCRIPT_LENGTH:
                text = text[:MAX_TRANSCRIPT_LENGTH] + "..."

            excerpts.append(
                TranscriptExcerpt(
                    session_id=session_id,
                    session_name=session_name,
                    timestamp=t["timestamp"],
                    source=t["source"],
                    text=text,
                )
            )

        return excerpts

    def _convert_rag_transcripts(
        self,
        session_id: int,
        session_name: str,
        chunks: List[RetrievedChunk],
    ) -> List[TranscriptExcerpt]:
        """Convert RAG transcript chunks to TranscriptExcerpt objects.

        Args:
            session_id: ID of the session.
            session_name: Name of the session for context.
            chunks: List of RAG chunks with transcript source type.

        Returns:
            List of TranscriptExcerpt objects.
        """
        excerpts = []
        for chunk in chunks:
            # Only convert transcript source chunks
            if chunk.source_type != SourceType.TRANSCRIPT:
                continue

            text = chunk.content
            # Truncate long transcripts
            if len(text) > MAX_TRANSCRIPT_LENGTH:
                text = text[:MAX_TRANSCRIPT_LENGTH] + "..."

            # Determine source from title or default to microphone
            source = "microphone"
            if chunk.title:
                if "system" in chunk.title.lower():
                    source = "system"

            excerpts.append(TranscriptExcerpt(
                session_id=session_id,
                session_name=session_name,
                timestamp=chunk.timestamp,
                source=source,
                text=text,
            ))

        return excerpts

    def _get_transcripts_rag_first(
        self,
        session_id: int,
        session_name: str,
        question: str,
    ) -> List[TranscriptExcerpt]:
        """Get transcripts using RAG first, then fallback to legacy keyword matching.

        Args:
            session_id: ID of the session.
            session_name: Name of the session for context.
            question: User question for relevance filtering.

        Returns:
            List of TranscriptExcerpt objects.
        """
        # Try RAG retrieval first
        rag_chunks = self._get_rag_chunks(session_id, question, limit=DEFAULT_TRANSCRIPT_LIMIT)

        # Filter to transcript source chunks
        transcript_chunks = [
            c for c in rag_chunks
            if c.source_type == SourceType.TRANSCRIPT
        ]

        if transcript_chunks:
            # Use RAG transcript chunks
            return self._convert_rag_transcripts(
                session_id, session_name, transcript_chunks
            )

        # Fallback to legacy keyword matching
        return self._get_transcripts(session_id, session_name, question)

    def _extract_keywords(self, text: str) -> List[str]:
        """Extract keywords from text for matching.

        Args:
            text: Input text to extract keywords from.

        Returns:
            List of keywords (words longer than 3 characters).
        """
        # Simple word extraction
        import re
        words = re.findall(r'\b\w+\b', text.lower())
        # Filter: words > 3 chars, exclude common stop words
        stop_words = {
            "the", "and", "for", "that", "this", "with", "are", "was",
            "have", "has", "been", "but", "not", "you", "all", "can",
            "had", "her", "she", "him", "his", "its", "our", "they",
            "what", "when", "where", "who", "will", "from", "have",
        }
        keywords = [w for w in words if len(w) > 3 and w not in stop_words]
        return keywords

    def _get_screenshots_near_transcripts(
        self,
        session_id: int,
        session_name: str,
        transcripts: List[TranscriptExcerpt],
    ) -> List[ScreenshotReference]:
        """Get screenshots near relevant transcript timestamps.

        Args:
            session_id: ID of the session.
            session_name: Name of the session for context.
            transcripts: List of relevant transcript excerpts.

        Returns:
            List of ScreenshotReference objects near transcript times.
        """
        try:
            screenshot_rows = self._db.get_screenshots(session_id)
        except Exception:
            return []

        if not transcripts or not screenshot_rows:
            return []

        # Get transcript timestamps
        transcript_times = {t.timestamp for t in transcripts}

        # Find screenshots within 60 seconds of any transcript timestamp
        nearby_screenshots = []
        time_window = 60  # seconds

        for row in screenshot_rows:
            screenshot_time = row.get("timestamp", 0)

            # Check if screenshot is near any transcript timestamp
            for trans_time in transcript_times:
                if abs(screenshot_time - trans_time) <= time_window:
                    nearby_screenshots.append(
                        ScreenshotReference(
                            session_id=session_id,
                            session_name=session_name,
                            timestamp=screenshot_time,
                            filepath=row.get("filepath", ""),
                            description=row.get("description"),
                        )
                    )
                    break  # Only include each screenshot once

        return nearby_screenshots

    def _get_conversation_history(
        self,
        conversation_id: Optional[int],
    ) -> List[ConversationTurn]:
        """Retrieve recent conversation history for a conversation.

        Args:
            conversation_id: ID of the conversation to retrieve history for.

        Returns:
            List of ConversationTurn objects (most recent first).
        """
        if conversation_id is None:
            return []

        try:
            message_rows = self._db.get_messages(conversation_id)
        except (AttributeError, Exception):
            # Database doesn't have get_messages method or other error
            return []

        if not message_rows:
            return []

        # Convert to ConversationTurn objects, limit to recent messages
        turns = []
        for row in message_rows[-DEFAULT_CONVERSATION_LIMIT:]:
            role = row.get("role", "user")
            content = row.get("content", "")
            if content:
                turns.append(ConversationTurn(role=role, content=content))

        return turns

    def _get_rag_chunks(
        self,
        session_id: int,
        question: str,
        limit: int = 12,
    ) -> List[RetrievedChunk]:
        """Retrieve relevant RAG chunks for a specific session.

        Uses FTS search to find relevant content within a session.

        Args:
            session_id: ID of the session to search within.
            question: User question for FTS search.
            limit: Maximum number of chunks to return (default 12).

        Returns:
            List of RetrievedChunk dictionaries matching rag_models fields.
        """
        try:
            rows = self._db.search_rag_fts(question, limit=limit, session_id=session_id)
        except Exception:
            return []

        if not rows:
            return []

        chunks = []
        for row in rows:
            # Convert source_type string to SourceType enum
            source_type_str = row.get("source_type", "transcript")
            try:
                source_type = SourceType(source_type_str)
            except ValueError:
                source_type = SourceType.TRANSCRIPT

            # Convert rank to score (BM25: lower rank = more relevant)
            # Invert: higher score = more relevant
            rank = row.get("rank", 0)
            score = -rank if rank else 1.0

            chunks.append(RetrievedChunk(
                chunk_id=row.get("chunk_id", 0),
                document_id=row.get("document_id", 0),
                source_type=source_type,
                source_id=row.get("source_id", 0),
                session_id=row.get("session_id", session_id),
                timestamp=row.get("timestamp", 0),
                title=row.get("title"),
                content=row.get("content", ""),
                score=score,
            ))

        return chunks
