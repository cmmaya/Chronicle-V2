"""RAG context builder for any-session searches.

Provides compact context builders for global search results grouped by session.
"""

import re
from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional

from ..rag.indexer import SPEAKER_NOTE, _chunk_transcripts, is_diarized
from ..storage.database import fts_terms

# Per-excerpt and total budgets shared by every Any Session context builder.
MAX_EXCERPT_CHARS = 500
MAX_CONTEXT_CHARS = 12000
MAX_CHUNKS_PER_SESSION = 4

# Archive-wide search (BU161): routing reads only session profiles (summaries),
# so a passing remark the summary left out is never routed. Chunks anywhere in
# the archive that hold one of the question's rare words are added as well.
# A word is rare when at most this share of all chunks match it (with a floor
# for a small archive); a question with no rare word adds nothing, so common
# words never pull in unrelated meetings.
RARE_TERM_MAX_SHARE = 0.005
RARE_TERM_MIN_CHUNKS = 5
# Shorter terms are prefix searches ("go*") that match far more than they say.
RARE_TERM_MIN_LENGTH = 3
MAX_ARCHIVE_CHUNKS = 4
# Budget for archive matches in sessions routing did not pick, on top of
# MAX_CONTEXT_CHARS.
MAX_ARCHIVE_CHARS = 3000
# Assistant answers are indexed too; an old answer is not evidence.
ARCHIVE_SOURCE_TYPES = ["transcript", "summary", "screenshot"]


def _format_session_date(value: Any) -> str:
    """``YYYY-MM-DD HH:MM`` for a unix timestamp; ``""`` when missing/unparseable."""
    if not value:
        return ""
    try:
        return datetime.fromtimestamp(int(value)).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError, OSError, OverflowError):
        return ""


def _session_header(
    session_id: Any, session_name: Optional[str], session_date: str
) -> str:
    """Uniform per-session header for both Any Session builders:

        ## [S<id>] <name> (<YYYY-MM-DD HH:MM>)

    The ``[S<id>]`` prefix is what the answer contract's ``sessions`` field
    refers back to. The date is dropped, with its parentheses, when unknown.
    """
    name = session_name or f"Session {session_id}"
    header = f"\n## [S{session_id}] {name}"
    if session_date:
        header += f" ({session_date})"
    return header + "\n"


def build_any_session_context(
    query: str,
    results: List[Dict[str, Any]],
    max_chars: int = 12000,
) -> str:
    """
    Build a compact context string from any-session search results.

    Groups results by session under a ``## [S<id>] <name> (<date>)`` header
    (date taken from the session's earliest result), then lists each result as
    ``[<source> @ <YYYY-MM-DD HH:MM:SS>]: <content>``. Respects max_chars to
    avoid exceeding context limits.

    Args:
        query: The original search query.
        results: List of result dictionaries from RAG search.
               Expected keys: source_type, session_id, session_name (optional),
               timestamp, content.
        max_chars: Maximum character length for the context (default 12000).

    Returns:
        Formatted context string grouped by session, or the no-context message
        if results is empty.

    Example:
        >>> results = [
        ...     {"session_id": 1, "session_name": "Team Standup",
        ...      "source_type": "transcript", "timestamp": 1700000000,
        ...      "content": "Let's discuss the project status."},
        ...     {"session_id": 2, "session_name": "Sprint Planning",
        ...      "source_type": "summary", "timestamp": 1700010000,
        ...      "content": "Completed items and next steps."},
        ... ]
        >>> context = build_any_session_context("project status", results)
    """
    # Handle empty results
    if not results:
        return "(No relevant context found in any session)"

    # Group results by session_id
    sessions: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
    for result in results:
        session_id = result.get("session_id")
        if session_id is not None:
            sessions[session_id].append(result)

    terms = fts_terms(query)

    # Build context string with character limit tracking
    parts = []
    current_length = 0

    # Process each session in order
    for session_id in sorted(sessions.keys()):
        session_results = sessions[session_id]

        # Session name + date (earliest chunk in the group) for the header.
        session_name = session_results[0].get("session_name")
        session_date = _format_session_date(
            min(
                (r.get("timestamp") for r in session_results if r.get("timestamp")),
                default=None,
            )
        )

        # Check if adding session header would exceed limit
        header = _session_header(session_id, session_name, session_date)
        if current_length + len(header) > max_chars:
            break

        parts.append(header)
        current_length += len(header)

        # Add each result from this session
        for result in session_results:
            # Format the result line
            result_text = _format_result(result, terms)
            result_length = len(result_text)

            # Check if adding this result would exceed limit
            if current_length + result_length > max_chars:
                # Still add at least one result if possible
                if not parts[-1].startswith("\n## "):
                    break
                # Can't fit more - stop adding results
                return "\n".join(parts)

            parts.append(result_text)
            current_length += result_length

    if not parts:
        return "(No relevant context found in any session)"

    return "\n".join(parts)


def rank_query_terms(db: Any, query: str) -> Dict[str, List[str]]:
    """The question's search terms, rarest first, and the rare ones (BU161).

    ``{"ranked": [...], "rare": [...]}``. Terms no chunk matches are kept last
    in ``ranked`` and are never rare. Without counts (an error) the terms keep
    their question order and none is rare.
    """
    terms = fts_terms(query)
    try:
        counts = db.count_rag_fts_matches(terms, source_types=ARCHIVE_SOURCE_TYPES)
        total = int(counts["total"])
        by_term = {t: int(n) for t, n in counts["terms"].items()}
    except Exception:
        return {"ranked": terms, "rare": []}
    limit = max(RARE_TERM_MIN_CHUNKS, total * RARE_TERM_MAX_SHARE)
    found = sorted((t for t in terms if by_term.get(t)), key=lambda t: by_term[t])
    rare = [t for t in found if by_term[t] <= limit and len(t) >= RARE_TERM_MIN_LENGTH]
    return {"ranked": found + [t for t in terms if t not in found], "rare": rare}


def find_archive_chunks(db: Any, rare_terms: List[str]) -> List[Dict[str, Any]]:
    """Chunks anywhere in the archive holding a rare question term (BU161).

    Each term is searched alone, rarest first: searched together, a word
    repeated in one chunk outranks the single chunk with the rarest word.
    """
    found: List[Dict[str, Any]] = []
    seen = set()
    for term in rare_terms:
        if len(found) >= MAX_ARCHIVE_CHUNKS:
            break
        try:
            hits = db.search_rag_fts(
                term, limit=MAX_ARCHIVE_CHUNKS,
                source_types=ARCHIVE_SOURCE_TYPES, terms=[term],
            ) or []
        except Exception:
            continue
        for hit in hits:
            if len(found) < MAX_ARCHIVE_CHUNKS and hit.get("chunk_id") not in seen:
                seen.add(hit.get("chunk_id"))
                found.append(hit)
    return found


def build_routed_session_context(
    db: Any,
    query: str,
    routed: List[Any],
    max_chars: int = MAX_CONTEXT_CHARS,
    archive: Optional[List[Dict[str, Any]]] = None,
    terms: Optional[List[str]] = None,
) -> str:
    """Build Any Session context from router-selected sessions (BU089).

    For each routed session: a ``## [S<id>] <name> (<date>)`` header, the
    session summary (truncated), and a bounded number of the session's top
    chunks for ``query`` (Tier-2 search scoped to that session only). Honours
    the per-excerpt and total character budgets. The router's match reason is
    internal and is not rendered here (it stays on ``RoutedSession.reason``).

    ``archive`` chunks (BU161, from :func:`find_archive_chunks`) lead their
    session's chunks when it was routed; the rest follow in sessions of their
    own, within ``MAX_ARCHIVE_CHARS`` beyond ``max_chars``. Each excerpt is cut
    around the first of ``terms`` (rarest first) it contains.

    Args:
        db: Database with ``get_summaries``, ``get_session`` and ``search_rag_fts``.
        query: The user's question.
        routed: List of ``RoutedSession`` (session_id, name, start_time, score, reason).
        max_chars: Total character budget.
        archive: Archive-wide matches for the question's rare words.
        terms: Search terms in excerpt priority order; the query's by default.

    Returns:
        Formatted context string, or the no-context message when nothing fits.
    """
    archive = list(archive or [])
    if not routed and not archive:
        return "(No relevant context found in any session)"
    if terms is None:
        terms = fts_terms(query)

    archive_by_session: Dict[Any, List[Dict[str, Any]]] = defaultdict(list)
    for chunk in archive:
        archive_by_session[chunk.get("session_id")].append(chunk)

    parts: List[str] = []
    length = 0

    def add(text: str) -> bool:
        nonlocal length
        if length + len(text) > max_chars:
            return False
        parts.append(text)
        length += len(text)
        return True

    full = False
    for candidate in routed:
        session_id = getattr(candidate, "session_id", None)
        if session_id is None:
            continue
        name = getattr(candidate, "name", None)
        session_date = _format_session_date(getattr(candidate, "start_time", None))
        header = _session_header(session_id, name, session_date)
        if not add(header):
            break
        if _session_is_diarized(db, session_id):
            add(f"[speakers]: {SPEAKER_NOTE}\n")

        # Session summary
        try:
            summaries = db.get_summaries(session_id) or []
        except Exception:
            summaries = []
        summary_text = max(
            (s.get("content", "") or "" for s in summaries),
            key=len,
            default="",
        ).strip()
        if summary_text:
            add(f"[summary]: {_truncate(summary_text)}\n")

        # Tier-2: top chunks inside this session only
        try:
            chunks = db.search_rag_fts(query, limit=MAX_CHUNKS_PER_SESSION, session_id=session_id)
        except Exception:
            chunks = []
        # No summary and nothing matched: the opening of the transcript tells
        # the model what the session is about (BU152).
        if not chunks and not summary_text:
            chunks = _opening_chunks(db, session_id)
        # Archive matches lead; the same chunk is never sent twice.
        leading = archive_by_session.pop(session_id, [])
        lead_ids = {c.get("chunk_id") for c in leading} - {None}
        chunks = leading + [c for c in chunks if c.get("chunk_id") not in lead_ids]
        for chunk in chunks:
            if not add(_format_result(chunk, terms)):
                full = True
                break
        if full:
            break

    # Archive matches in sessions routing did not pick (BU161).
    max_chars += MAX_ARCHIVE_CHARS
    for session_id, chunks in archive_by_session.items():
        if session_id is None:
            continue
        session = archive_session(db, session_id)
        header = _session_header(
            session_id, session["session_name"], _format_session_date(session["start_time"]))
        if not add(header):
            break
        if _session_is_diarized(db, session_id):
            add(f"[speakers]: {SPEAKER_NOTE}\n")
        for chunk in chunks:
            if not add(_format_result(chunk, terms)):
                break

    if not any(p.strip() and not p.startswith("\n## ") for p in parts):
        return "(No relevant context found in any session)"
    return "\n".join(parts)


def archive_session(db: Any, session_id: int) -> Dict[str, Any]:
    """Name and start time (unix seconds) of a session an archive match is in."""
    try:
        session = db.get_session(session_id) or {}
    except Exception:
        session = {}
    start = session.get("start_time")
    if isinstance(start, datetime):
        start = int(start.timestamp())
    return {
        "session_id": session_id,
        "session_name": session.get("name") or f"Session {session_id}",
        "start_time": start,
    }


def _session_is_diarized(db: Any, session_id: Any) -> bool:
    try:
        return is_diarized(db.get_session(session_id))
    except Exception:
        return False


def _opening_chunks(db: Any, session_id: int) -> List[Dict[str, Any]]:
    """The session's first transcript chunks, in time order (BU152)."""
    try:
        rows = db.get_transcripts(session_id) or []
    except Exception:
        return []
    chunks = _chunk_transcripts(rows)[:MAX_CHUNKS_PER_SESSION]
    return [
        {
            "source_type": "transcript",
            "timestamp": chunk["start_timestamp"],
            "content": chunk["content"],
        }
        for chunk in chunks
    ]


def _truncate(text: str, limit: int = MAX_EXCERPT_CHARS) -> str:
    text = text or ""
    if len(text) > limit:
        return text[:limit].rstrip() + "..."
    return text


def _format_result(result: Dict[str, Any], terms: Optional[List[str]] = None) -> str:
    """Format a single RAG result as ``[<source> @ <YYYY-MM-DD HH:MM:SS>]: <content>``.

    The chunk timestamp carries the full date, not just the time of day, so
    multi-day sessions and cross-session "when" questions are answerable. The
    document title is intentionally not rendered - it is redundant with the
    ``[S<id>] <name> (<date>)`` session header.
    """
    source_type = result.get("source_type", "unknown")

    timestamp = result.get("timestamp")
    timestamp_str = ""
    if timestamp:
        try:
            timestamp_str = " @ " + datetime.fromtimestamp(timestamp).strftime(
                "%Y-%m-%d %H:%M:%S"
            )
        except (ValueError, OSError):
            timestamp_str = ""

    content = _excerpt(result.get("content", ""), terms)
    return f"[{source_type}{timestamp_str}]: {content}\n"


def _excerpt(text: str, terms: Optional[List[str]] = None, limit: int = MAX_EXCERPT_CHARS) -> str:
    """At most ``limit`` characters of ``text``, centred on the first of
    ``terms`` (in priority order) it contains (BU161); the opening when none
    matches. Cut ends are marked with ``...``."""
    if len(text) <= limit:
        return text
    pos = None
    for term in terms or []:
        match = re.search(r"\b" + re.escape(term), text, re.IGNORECASE)
        if match:
            pos = match.start()
            break
    if pos is None:
        return text[:limit].rstrip() + "..."
    start = min(max(0, pos - limit // 2), len(text) - limit)
    if start > 0:
        # Start on a whole word, never past the matched term.
        space = text.find(" ", start, pos)
        if space != -1:
            start = space + 1
    end = start + limit
    prefix = "..." if start > 0 else ""
    suffix = "..." if end < len(text) else ""
    return prefix + text[start:end].strip() + suffix