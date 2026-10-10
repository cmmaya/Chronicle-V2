"""Assistant context retrieval for single session queries."""

import json
import re
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
from ..config import SESSION
from ..rag.indexer import _chunk_transcripts
from .live_qa import collapse_overlap, strip_transcript_evidence
from ..screenshots.search import search_session_screenshots
from ..storage.database import fts_terms


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
DEFAULT_KEYWORD_MATCHES = 12   # Maximum keyword-matched windows (in-memory search)
MAX_TRANSCRIPT_LENGTH = 900    # Maximum characters per transcript excerpt
DEFAULT_CONVERSATION_LIMIT = 10  # Maximum conversation turns to include
# In-memory search (BU141): the newest windows, up to this many characters, are
# always included - "what did she just say" is the common live question.
RECENT_TAIL_CHARS = 3000
# Session statuses that mean the recording is still running (src/app/session.py).
LIVE_STATUSES = ('active', 'paused')
# A question with fewer search terms than this is a follow-up ("is it about a
# report?"): the previous question is searched along with it (BU143).
FOLLOW_UP_MAX_TERMS = 3


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
        # chunk_id -> audio source, filled by the last _get_rag_chunks call.
        # RetrievedChunk has no source field, so the per-chunk source recorded
        # by the indexer travels alongside it.
        self._chunk_sources: dict[int, str] = {}

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

        # Get conversation history
        conversation_history = self._get_conversation_history(conversation_id)
        search_query = self._search_query(question, conversation_history)

        # Transcripts: the RAG index only exists once the session is stopped
        # and indexed (BU141). A live session, or one whose index does not
        # cover every row yet, is searched in memory from its raw rows.
        rows = self._load_transcript_rows(session_id)
        live = session.get("status") in LIVE_STATUSES
        indexed = not live and self._index_covers(session_id, rows)

        # BU144: a session short enough to send whole is not searched at all.
        full = self._full_transcript(session_id, session_name, rows)
        if full is not None:
            transcripts = full
        elif indexed:
            transcripts = self._get_transcripts_rag_first(
                session_id, session_name, search_query, rows
            )
        else:
            transcripts = self._get_transcripts(
                session_id, session_name, search_query, rows
            )

        # Screenshot index, with full details for the relevant ones. The whole
        # transcript anchors nothing, so only the search results do.
        screenshots = self._get_screenshots(
            session_id, session_name, search_query,
            [] if full is not None else transcripts,
        )

        return AssistantContext(
            query=question,
            sessions=[session_candidate],
            transcripts=transcripts,
            summaries=summaries,
            screenshots=screenshots,
            conversation_history=conversation_history,
            transcript_live=live,
            transcript_indexed=indexed,
            transcript_until=max(
                (int(r.get("timestamp") or 0) for r in rows), default=0
            ),
            transcript_full=full is not None,
            transcript_chars=sum(len(t.text) for t in (full or [])),
        )

    @staticmethod
    def _full_transcript(
        session_id: int, session_name: str, rows: List[dict[str, Any]]
    ) -> Optional[List[TranscriptExcerpt]]:
        """Every row of the session in time order, or None to search instead.

        Consecutive rows of one source overlap (the recorder's chunks do), so
        the repeated leading words are dropped. None when whole-transcript
        mode is off, there is nothing to send, or the text is over the limit.
        """
        limit = int(SESSION.get("full_transcript_max_chars", 0) or 0)
        if limit <= 0:
            return None
        excerpts = []
        previous: dict[str, str] = {}
        total = 0
        for row in rows:
            text = (row.get("text") or "").strip()
            source = row.get("source") or "microphone"
            collapsed = collapse_overlap(previous.get(source, ""), text)
            previous[source] = text
            if not collapsed:
                continue
            total += len(collapsed)
            if total > limit:
                return None
            excerpts.append(TranscriptExcerpt(
                session_id=session_id,
                session_name=session_name,
                timestamp=int(row.get("timestamp") or 0),
                source=source,
                text=collapsed,
            ))
        return excerpts or None

    @staticmethod
    def _search_query(question: str, history: List[ConversationTurn]) -> str:
        """The text searched for ``question`` (BU143).

        A short follow-up carries too few terms to find anything on its own,
        so the previous user question is searched with it. The transcript
        evidence a live-answer question embeds is left out (BU144): minutes of
        speech would drive the search terms.
        """
        question = strip_transcript_evidence(question)
        if len(fts_terms(question)) >= FOLLOW_UP_MAX_TERMS:
            return question
        previous = next(
            (t.content for t in reversed(history) if t.role == "user"), ""
        )
        return f"{question} {previous}".strip()

    def _load_transcript_rows(self, session_id: int) -> List[dict[str, Any]]:
        """Raw transcript rows of a session, oldest first."""
        try:
            rows = self._db.get_transcripts(session_id) or []
        except Exception:
            return []
        return sorted(rows, key=lambda r: r.get("timestamp") or 0)

    def _index_covers(self, session_id: int, rows: List[dict[str, Any]]) -> bool:
        """Whether the session's RAG transcript document covers every row.

        Indexing runs only after Stop, so right after it (and after a
        re-transcription) the index is missing or short. A database that
        cannot answer is trusted, so retrieval keeps its RAG-first behaviour.
        """
        if not rows:
            return True
        try:
            doc = self._db.get_rag_document("transcript", session_id)
        except Exception:
            return True
        if doc is None:
            return False
        try:
            count = json.loads(doc.get("metadata_json") or "{}").get("transcript_count")
        except Exception:
            return True
        return not isinstance(count, int) or count >= len(rows)

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
        self,
        session_id: int,
        session_name: str,
        question: str,
        rows: Optional[List[dict[str, Any]]] = None,
    ) -> List[TranscriptExcerpt]:
        """Search a session's raw transcript rows in memory (BU141).

        Used while the session is live or not yet indexed, and when the index
        finds nothing. Rows are grouped into the same time windows the indexer
        builds; the windows that match the question are kept, plus the newest
        windows up to ``RECENT_TAIL_CHARS``. Excerpts come back in time order.
        """
        if rows is None:
            rows = self._load_transcript_rows(session_id)
        windows = _chunk_transcripts(rows)
        if not windows:
            return []

        # Terms are prefixes (BU143), as in the FTS query.
        keywords = self._extract_keywords(question)
        scored = []
        for index, window in enumerate(windows):
            words = set(re.findall(r'\w+', window["content"].lower()))
            score = sum(
                1 for k in keywords if any(w.startswith(k) for w in words)
            )
            if score:
                scored.append((score, index))
        scored.sort(key=lambda s: (-s[0], s[1]))
        picked = {index for _, index in scored[:DEFAULT_KEYWORD_MATCHES]}

        used = 0
        for index in range(len(windows) - 1, -1, -1):
            size = len(windows[index]["content"])
            if used and used + size > RECENT_TAIL_CHARS:
                break
            picked.add(index)
            used += size

        excerpts = []
        for index in sorted(picked):
            window = windows[index]
            text = window["content"]
            if len(text) > MAX_TRANSCRIPT_LENGTH:
                text = text[:MAX_TRANSCRIPT_LENGTH] + "..."
            excerpts.append(TranscriptExcerpt(
                session_id=session_id,
                session_name=session_name,
                timestamp=window["start_timestamp"],
                source=window["source"] or "microphone",
                text=text,
            ))
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

            # Per-chunk source recorded at indexing time; chunks indexed before
            # BU087 have none, so fall back to the document title.
            source = self._chunk_sources.get(chunk.chunk_id)
            if not source:
                source = "microphone"
                if chunk.title and "system" in chunk.title.lower():
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
        rows: Optional[List[dict[str, Any]]] = None,
    ) -> List[TranscriptExcerpt]:
        """Get transcripts using RAG first, then the in-memory search.

        Args:
            session_id: ID of the session.
            session_name: Name of the session for context.
            question: User question for relevance filtering.
            rows: The session's transcript rows, when already loaded.

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

        # Nothing matched in the index: search the raw rows instead
        return self._get_transcripts(session_id, session_name, question, rows)

    def _extract_keywords(self, text: str) -> List[str]:
        """Search terms of ``text``: stems with stop words dropped, matched as
        prefixes - the same terms the FTS query uses (BU143)."""
        return fts_terms(text)

    def _get_screenshots(
        self,
        session_id: int,
        session_name: str,
        question: str,
        transcripts: List[TranscriptExcerpt],
    ) -> List[ScreenshotReference]:
        """Screenshot index for the session, with full details for the few
        relevant ones (BU107 two-tier search).

        The transcripts retrieved for the question anchor the search in time:
        a screenshot taken while the relevant topic was discussed ranks higher.
        """
        try:
            hits = search_session_screenshots(
                self._db,
                session_id,
                question,
                anchor_timestamps=[t.timestamp for t in transcripts],
            )
        except Exception:
            return []

        references = []
        for hit in hits:
            details = hit.details or {}
            references.append(ScreenshotReference(
                session_id=session_id,
                session_name=session_name,
                timestamp=hit.timestamp,
                filepath=hit.filepath,
                description=hit.description or None,
                screenshot_id=hit.screenshot_id,
                preview=hit.preview,
                tier=hit.tier,
                ai_summary=details.get("ai_summary", ""),
                visible_text=list(details.get("visible_text", [])),
                keywords=list(details.get("keywords", [])),
            ))
        return references

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
        self._chunk_sources = {}
        for row in rows:
            chunk_source = row.get("source")
            if chunk_source:
                self._chunk_sources[row.get("chunk_id", 0)] = chunk_source

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
