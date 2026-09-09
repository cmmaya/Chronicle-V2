"""RAG context builder for any-session searches.

Provides compact context builders for global search results grouped by session.
"""

from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional

# Per-excerpt and total budgets shared by every Any Session context builder.
MAX_EXCERPT_CHARS = 500
MAX_CONTEXT_CHARS = 12000
MAX_CHUNKS_PER_SESSION = 4


def build_any_session_context(
    query: str,
    results: List[Dict[str, Any]],
    max_chars: int = 12000,
) -> str:
    """
    Build a compact context string from any-session search results.

    Groups results by session and includes source type, session name, timestamp,
    and truncated content for each result. Respects max_chars to avoid
    exceeding context limits.

    Args:
        query: The original search query.
        results: List of result dictionaries from RAG search.
               Expected keys: chunk_id, document_id, source_type, source_id,
               session_id, timestamp, title, content, rank.
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

    # Build context string with character limit tracking
    parts = []
    current_length = 0

    # Process each session in order
    for session_id in sorted(sessions.keys()):
        session_results = sessions[session_id]

        # Get session name from first result
        session_name = session_results[0].get("session_name", f"Session {session_id}")

        # Check if adding session header would exceed limit
        header = f"\n## {session_name}\n"
        if current_length + len(header) > max_chars:
            break

        parts.append(header)
        current_length += len(header)

        # Add each result from this session
        for result in session_results:
            # Format the result line
            result_text = _format_result(result)
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


def build_routed_session_context(
    db: Any,
    query: str,
    routed: List[Any],
    max_chars: int = MAX_CONTEXT_CHARS,
) -> str:
    """Build Any Session context from router-selected sessions (BU089).

    For each routed session: a header with the match reason, the session
    summary (truncated), and a bounded number of the session's top chunks for
    ``query`` (Tier-2 search scoped to that session only). Honours the
    per-excerpt and total character budgets.

    Args:
        db: Database with ``get_summaries`` and ``search_rag_fts``.
        query: The user's question.
        routed: List of ``RoutedSession`` (session_id, name, start_time, score, reason).
        max_chars: Total character budget.

    Returns:
        Formatted context string, or the no-context message when nothing fits.
    """
    if not routed:
        return "(No relevant context found in any session)"

    parts: List[str] = []
    length = 0

    def add(text: str) -> bool:
        nonlocal length
        if length + len(text) > max_chars:
            return False
        parts.append(text)
        length += len(text)
        return True

    for candidate in routed:
        session_id = getattr(candidate, "session_id", None)
        if session_id is None:
            continue
        name = getattr(candidate, "name", f"Session {session_id}")
        reason = getattr(candidate, "reason", "") or ""
        header = f"\n## {name}"
        if reason:
            header += f"  ({reason})"
        header += "\n"
        if not add(header):
            break

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
        for chunk in chunks:
            if not add(_format_result(chunk)):
                return "\n".join(parts)

    if not any(p.strip() and not p.startswith("\n## ") for p in parts):
        return "(No relevant context found in any session)"
    return "\n".join(parts)


def _truncate(text: str, limit: int = MAX_EXCERPT_CHARS) -> str:
    text = text or ""
    if len(text) > limit:
        return text[:limit].rstrip() + "..."
    return text


def _format_result(result: Dict[str, Any]) -> str:
    """Format a single RAG result as a context line."""
    # Get source type
    source_type = result.get("source_type", "unknown")

    # Get timestamp if available
    timestamp = result.get("timestamp")
    if timestamp:
        try:
            dt = datetime.fromtimestamp(timestamp)
            time_str = dt.strftime("%H:%M:%S")
            timestamp_str = f" @ {time_str}"
        except (ValueError, OSError):
            timestamp_str = ""
    else:
        timestamp_str = ""

    # Get title if available
    title = result.get("title")
    title_str = f" [{title}]" if title else ""

    # Get content and truncate if needed
    content = result.get("content", "")
    max_content_len = 500
    if len(content) > max_content_len:
        content = content[:max_content_len].rstrip() + "..."

    # Format: [source_type@timestamp] [title]: content
    return f"[{source_type}{timestamp_str}]{title_str}: {content}\n"