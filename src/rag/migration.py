"""One-time backfill + recovery reindex for the RAG corpus (BU091).

BU087 changed transcript chunking and BU089 added per-session router profiles,
but neither rewrites the sessions that were indexed under the old scheme. This
module brings the whole corpus up to date:

  - re-chunk every session under the BU087 time-windowed scheme
  - compute per-chunk embeddings when the embedding service is available
  - (re)build one router profile + vector per session
  - keep the FTS index in sync

It is idempotent and resumable: :func:`index_session_content` skips a document
whose content hash is unchanged and whose chunks already carry current-model
vectors, so an interrupted run can simply be started again. If the embedding
service is unavailable the lexical index is still built and a later run fills in
the vectors.

There are no schema changes here - only what BU087 / BU089 already introduced.
"""
from __future__ import annotations

import logging
from typing import Callable, Dict, Optional

from .indexer import index_session_content

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[int, int], None]


def backfill_corpus(
    db,
    progress_callback: Optional[ProgressCallback] = None,
    force: bool = False,
) -> Dict[str, object]:
    """Re-index every session under the current chunking + embedding scheme.

    Args:
        db: Database instance with RAG methods.
        progress_callback: Invoked as ``(done, total)`` after each session.
        force: Rewrite every document regardless of content hash (use for
               rebuilding a corrupted or stale index).

    Returns:
        A summary dict: ``sessions`` (total), ``processed`` (touched by this
        run), ``failed`` (count), ``embeddings`` (whether vectors were written).
    """
    try:
        from .embeddings import get_embedding_service
        embeddings_available = get_embedding_service().is_available
    except Exception:  # noqa: BLE001
        embeddings_available = False

    if not embeddings_available:
        logger.warning(
            "Embedding service unavailable - backfill will build the lexical "
            "index only; re-run later to add vectors."
        )

    sessions = db.list_sessions()
    total = len(sessions)
    processed = 0
    failed = 0

    for position, session in enumerate(sessions, start=1):
        session_id = session.get("id")
        if session_id is not None:
            try:
                if index_session_content(db, session_id, force=force):
                    processed += 1
                else:
                    failed += 1
            except Exception as exc:  # noqa: BLE001 - one bad session must not abort the run
                failed += 1
                logger.error("Backfill failed for session %s: %s", session_id, exc)
        if progress_callback:
            progress_callback(position, total)

    # Belt-and-braces: guarantee FTS is consistent after a forced rebuild even
    # if no per-session write reported a change.
    if force:
        try:
            db.rebuild_rag_fts()
        except Exception as exc:  # noqa: BLE001
            logger.error("FTS rebuild after backfill failed: %s", exc)

    summary = {
        "sessions": total,
        "processed": processed,
        "failed": failed,
        "embeddings": embeddings_available,
    }
    logger.info("RAG backfill complete: %s", summary)
    return summary
