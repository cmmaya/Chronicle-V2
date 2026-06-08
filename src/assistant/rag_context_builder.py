"""RAG context builder for any-session searches.

Provides compact context builders for global search results grouped by session.
"""

from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional


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