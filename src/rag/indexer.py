"""RAG indexer for populating RAG tables with session content."""
import logging
import hashlib
import re
from datetime import datetime
from typing import List, Dict, Any, Optional, Callable

logger = logging.getLogger(__name__)

# Default chunk size for text chunking
DEFAULT_CHUNK_SIZE = 1000
DEFAULT_CHUNK_OVERLAP = 100

# Transcript chunking: a window closes on whichever bound is reached first.
TRANSCRIPT_WINDOW_SECONDS = 60
TRANSCRIPT_WINDOW_CHARS = 800


def _chunk_text(text: str, chunk_size: int = DEFAULT_CHUNK_SIZE,
                overlap: int = DEFAULT_CHUNK_OVERLAP) -> List[Dict[str, Any]]:
    """Split text into overlapping chunks.
    
    Args:
        text: The text to chunk
        chunk_size: Maximum size of each chunk in characters
        overlap: Number of characters to overlap between chunks
        
    Returns:
        List of chunk dictionaries with 'content' and 'chunk_index' keys
    """
    if not text or not text.strip():
        return []
    
    chunks = []
    text = text.strip()
    start = 0
    chunk_index = 0
    
    while start < len(text):
        end = start + chunk_size
        chunk_text = text[start:end]
        
        # Don't split in the middle of a word if possible
        if end < len(text) and ' ' in chunk_text[-(min(50, len(chunk_text))):]:
            # Find the last space to avoid cutting words
            last_space = chunk_text.rfind(' ')
            if last_space > chunk_size // 2:  # Only trim if not too close to chunk end
                chunk_text = chunk_text[:last_space]
                end = start + last_space
        
        chunks.append({
            'content': chunk_text.strip(),
            'chunk_index': chunk_index
        })
        
        chunk_index += 1
        start = end - overlap
        
        # Prevent infinite loop for very small texts
        if start <= chunks[-1]['chunk_index'] * chunk_size:
            break
    
    return chunks


def _chunk_transcripts(transcripts: List[Dict[str, Any]],
                       window_seconds: int = TRANSCRIPT_WINDOW_SECONDS,
                       max_chars: int = TRANSCRIPT_WINDOW_CHARS) -> List[Dict[str, Any]]:
    """Group transcript rows into time-windowed chunks.

    A window is closed when any of these happens: the audio source changes,
    the elapsed time since the window started reaches ``window_seconds``, or
    adding the next row would push the window past ``max_chars``. A single row
    longer than ``max_chars`` is split on the size bound, and every piece keeps
    the row's timestamp and source.

    Args:
        transcripts: Transcript rows ordered by timestamp, each with
                     'text', 'timestamp' and 'source' keys.
        window_seconds: Elapsed-time bound for a window.
        max_chars: Size bound for a window, in characters.

    Returns:
        List of chunk dictionaries with 'content', 'chunk_index',
        'start_timestamp', 'end_timestamp' and 'source' keys.
    """
    chunks: List[Dict[str, Any]] = []
    texts: List[str] = []
    start_ts = 0
    end_ts = 0
    source: Optional[str] = None

    def flush() -> None:
        nonlocal texts
        if not texts:
            return
        chunks.append({
            'content': ' '.join(texts).strip(),
            'chunk_index': len(chunks),
            'start_timestamp': start_ts,
            'end_timestamp': end_ts,
            'source': source,
        })
        texts = []

    for row in transcripts or []:
        text = (row.get('text') or '').strip()
        if not text:
            continue

        row_ts = int(row.get('timestamp') or 0)
        row_source = row.get('source')

        if texts and (row_source != source
                      or row_ts - start_ts >= window_seconds
                      or sum(len(t) for t in texts) + 1 + len(text) > max_chars):
            flush()

        if not texts:
            start_ts = row_ts
            source = row_source

        # A row that cannot fit in any window is split on the size bound.
        if len(text) > max_chars:
            flush()
            for piece in _chunk_text(text, chunk_size=max_chars, overlap=0):
                chunks.append({
                    'content': piece['content'],
                    'chunk_index': len(chunks),
                    'start_timestamp': row_ts,
                    'end_timestamp': row_ts,
                    'source': row_source,
                })
            continue

        texts.append(text)
        end_ts = row_ts

    flush()
    return chunks


def _compute_content_hash(content: str) -> str:
    """Compute a hash of the content for deduplication.
    
    Args:
        content: The content to hash
        
    Returns:
        SHA256 hash of the content as a hex string
    """
    return hashlib.sha256(content.encode('utf-8')).hexdigest()


def _embed_chunks(chunks: List[Dict[str, Any]]) -> Optional[str]:
    """Attach ``embedding`` / ``embedding_model`` to each chunk dict in place.

    Best-effort: returns the embedding model id when vectors were produced, or
    ``None`` when the embedding service is unavailable (the chunks are then
    stored with NULL vectors and a later run can repair them).
    """
    if not chunks:
        return None
    from .embeddings import get_embedding_service

    embedder = get_embedding_service()
    if not embedder.is_available:
        return None
    vectors = embedder.embed_passages([c['content'] for c in chunks])
    if vectors is None or len(vectors) != len(chunks):
        return None
    model_id = embedder.model_id
    for chunk, vec in zip(chunks, vectors):
        chunk['embedding'] = vec.astype('float32').tobytes()
        chunk['embedding_model'] = model_id
    return model_id


def _sync_document(
    db,
    *,
    source_type: str,
    source_id: int,
    session_id: int,
    timestamp: int,
    title: str,
    content_text: str,
    chunks: List[Dict[str, Any]],
    metadata_json: str,
    force: bool,
) -> bool:
    """Upsert one RAG document + its chunks + chunk embeddings, idempotently.

    Returns ``True`` when anything was written (so the caller knows to rebuild
    the FTS index), ``False`` when the document was already up to date.
    """
    content_hash = _compute_content_hash(content_text)

    from .embeddings import get_embedding_service

    embedder = get_embedding_service()
    model_id = embedder.model_id if embedder.is_available else None

    existing = None
    try:
        existing = db.get_rag_document(source_type, source_id)
    except Exception:
        existing = None

    if not force and existing is not None and existing.get('content_hash') == content_hash:
        # Content unchanged; only re-touch if chunks are missing vectors for the
        # current embedding model (covers a model / revision change).
        try:
            missing = db.count_chunks_missing_embedding(existing['id'], model_id)
        except Exception:
            missing = 0
        if model_id is None or missing == 0:
            return False

    _embed_chunks(chunks)

    doc_id = db.upsert_rag_document(
        source_type=source_type,
        source_id=source_id,
        session_id=session_id,
        timestamp=timestamp,
        title=title,
        content_hash=content_hash,
        metadata_json=metadata_json,
    )
    db.replace_rag_chunks(doc_id, chunks)
    logger.info(
        "Indexed %d %s chunks for session %s (embeddings=%s)",
        len(chunks), source_type, session_id, model_id is not None,
    )
    return True


def index_session_content(db, session_id: int, force: bool = False) -> bool:
    """Index session content (transcripts and summaries) into RAG tables.

    Fetches transcripts and summaries, chunks them under the current scheme,
    computes chunk embeddings when the embedding service is available, stores
    everything, and refreshes the FTS index and the router profile.

    Idempotent: a document whose content hash is unchanged and whose chunks
    already carry current-model vectors is left untouched, so re-running on
    unchanged data performs no writes. ``force=True`` bypasses that skip.

    Returns True if indexing succeeded (including the no-op case), False on error.
    """
    try:
        logger.info(f"Starting RAG indexing for session {session_id}")
        import json
        changed = False

        # --- transcripts -------------------------------------------------
        transcripts = db.get_transcripts(session_id)
        if transcripts:
            # Time-windowed chunks; each keeps its own timestamp and source.
            chunks = _chunk_transcripts(transcripts)
            # Hash the chunked text so a change in chunking is visible in the hash.
            full_transcript = ' '.join(c['content'] for c in chunks)

            if full_transcript.strip() and chunks:
                timestamp = int(transcripts[0].get('timestamp', 0)) if transcripts else 0
                if timestamp == 0:
                    timestamp = int(datetime.now().timestamp())

                metadata_json = json.dumps({
                    'source': 'transcript',
                    'transcript_count': len(transcripts),
                })
                changed |= _sync_document(
                    db,
                    source_type='transcript',
                    source_id=session_id,  # session_id doubles as source_id here
                    session_id=session_id,
                    timestamp=timestamp,
                    title=f'Session {session_id} Transcript',
                    content_text=full_transcript,
                    chunks=chunks,
                    metadata_json=metadata_json,
                    force=force,
                )

        # --- summaries -------------------------------------------------
        for summary in db.get_summaries(session_id):
            summary_content = summary.get('content', '')
            if not summary_content:
                continue

            summary_timestamp = summary.get('created_at', 0)
            if summary_timestamp == 0:
                summary_timestamp = int(datetime.now().timestamp())

            summary_type = summary.get('summary_type', 'unknown')
            metadata_json = json.dumps({
                'source': 'summary',
                'summary_type': summary_type,
                'model_used': summary.get('model_used', 'unknown'),
            })

            chunks = _chunk_text(summary_content)
            if not chunks:
                continue
            changed |= _sync_document(
                db,
                source_type='summary',
                source_id=summary.get('id'),
                session_id=session_id,
                timestamp=summary_timestamp,
                title=f'Session {session_id} - {summary_type}',
                content_text=summary_content,
                chunks=chunks,
                metadata_json=metadata_json,
                force=force,
            )

        # Rebuild the FTS index only when chunk rows actually changed.
        if changed:
            db.rebuild_rag_fts()

        # Refresh the router profile (BU089). Best-effort: a profile failure must
        # not fail content indexing.
        try:
            index_session_profile(db, session_id, force=force)
        except Exception as e:
            logger.warning(f"Session profile refresh failed for {session_id}: {e}")

        logger.info(
            "RAG indexing completed for session %s (changed=%s)", session_id, changed
        )
        return True

    except Exception as e:
        logger.error(f"RAG indexing failed for session {session_id}: {str(e)}")
        return False


# --- Session router profiles (BU089) -------------------------------------------

# Small bilingual (EN/ES) stop-word set for keyword extraction. Kept short on
# purpose: the goal is to drop filler, not to do real NLP.
_PROFILE_STOP_WORDS = frozenset({
    "the", "a", "an", "and", "or", "but", "if", "of", "to", "in", "on", "for",
    "with", "at", "by", "from", "as", "is", "are", "was", "were", "be", "been",
    "this", "that", "these", "those", "it", "its", "we", "our", "you", "your",
    "they", "their", "he", "she", "his", "her", "not", "no", "so", "than",
    "then", "there", "here", "what", "when", "where", "which", "who", "how",
    "about", "into", "over", "after", "before", "during", "session", "meeting",
    "el", "la", "los", "las", "un", "una", "unos", "unas", "y", "o", "u", "de",
    "del", "al", "en", "con", "por", "para", "que", "se", "su", "sus", "lo",
    "es", "son", "fue", "fueron", "ser", "este", "esta", "estos", "estas",
    "eso", "esa", "ese", "como", "mas", "pero", "si", "no", "ya", "muy",
    "nos", "nuestro", "nuestra", "sobre", "entre", "cuando", "donde", "sesion",
    "reunion", "hola", "gracias",
})

_WORD_RE = re.compile(r"[^\W\d_]{3,}", re.UNICODE)


def extract_keywords(text: str, max_keywords: int = 12) -> List[str]:
    """Return the most frequent meaningful words in ``text`` (EN/ES aware)."""
    if not text:
        return []
    counts: Dict[str, int] = {}
    order: Dict[str, int] = {}
    for position, match in enumerate(_WORD_RE.finditer(text.lower())):
        word = match.group(0)
        if word in _PROFILE_STOP_WORDS:
            continue
        counts[word] = counts.get(word, 0) + 1
        order.setdefault(word, position)
    ranked = sorted(counts, key=lambda w: (-counts[w], order[w]))
    return ranked[:max_keywords]


def build_session_profile(db, session_id: int) -> Optional[Dict[str, Any]]:
    """Assemble the router profile text + keywords for a session.

    Profile text = session name + start date + summary text. Keywords are
    extracted from name + summary. Returns ``None`` when the session is unknown.
    """
    try:
        session = db.get_session(session_id)
    except Exception:
        session = None
    if not session:
        return None

    name = (session.get("name") or f"Session {session_id}").strip()
    start_time = session.get("start_time")
    date_str = ""
    if start_time:
        try:
            date_str = datetime.fromtimestamp(int(start_time)).strftime("%Y-%m-%d")
        except (ValueError, OSError, TypeError):
            date_str = ""

    summaries = []
    try:
        summaries = db.get_summaries(session_id) or []
    except Exception:
        summaries = []
    summary_text = "\n".join(
        (s.get("content") or "").strip() for s in summaries if s.get("content")
    ).strip()

    keywords = extract_keywords(f"{name}\n{summary_text}")

    parts = [name]
    if date_str:
        parts.append(date_str)
    if summary_text:
        parts.append(summary_text)
    if keywords:
        parts.append("Keywords: " + ", ".join(keywords))
    profile_text = "\n".join(parts).strip()

    return {
        "session_id": session_id,
        "profile_text": profile_text,
        "keywords": keywords,
        "content_hash": _compute_content_hash(profile_text),
    }


def index_session_profile(db, session_id: int, force: bool = False) -> bool:
    """Build and store the router profile (+ embedding) for one session.

    Skips the embedding recompute when the profile text and the embedding model
    are both unchanged, unless ``force`` is set. Missing embedding model is not
    an error: the profile is stored with a NULL vector and the router falls back
    to lexical-only for that session.
    """
    profile = build_session_profile(db, session_id)
    if profile is None:
        return False

    from .embeddings import get_embedding_service

    embedder = get_embedding_service()
    model_id = embedder.model_id if embedder.is_available else None

    existing = None
    try:
        existing = db.get_session_profile(session_id)
    except Exception:
        existing = None

    unchanged = (
        not force
        and existing is not None
        and existing.get("content_hash") == profile["content_hash"]
        and existing.get("embedding_model") == model_id
        and existing.get("embedding") is not None
    )
    if unchanged:
        return True

    embedding_bytes = None
    if embedder.is_available:
        vectors = embedder.embed_passages([profile["profile_text"]])
        if vectors is not None and len(vectors):
            embedding_bytes = vectors[0].astype("float32").tobytes()

    try:
        db.upsert_session_profile(
            session_id=session_id,
            profile_text=profile["profile_text"],
            keywords=", ".join(profile["keywords"]),
            embedding=embedding_bytes,
            embedding_model=model_id if embedding_bytes is not None else None,
            content_hash=profile["content_hash"],
        )
    except Exception as e:
        logger.error(f"Failed to store session profile for {session_id}: {e}")
        return False
    return True


def reindex_all_session_profiles(
    db,
    progress_callback: Optional[Callable[[int, int], None]] = None,
    force: bool = False,
) -> int:
    """Rebuild the router profile for every session. Returns the count stored."""
    sessions = db.list_sessions()
    total = len(sessions)
    stored = 0
    for position, session in enumerate(sessions, start=1):
        session_id = session.get("id")
        if session_id is not None and index_session_profile(db, session_id, force=force):
            stored += 1
        if progress_callback:
            progress_callback(position, total)
    logger.info(f"Rebuilt {stored}/{total} session router profiles")
    return stored


def reindex_all_sessions(
    db,
    progress_callback: Optional[Callable[[int, int], None]] = None,
    force: bool = False,
) -> int:
    """Re-index every session so existing data adopts the current chunking.

    Chunks are replaced per document and the operation is idempotent, so this is
    safe to run repeatedly and safe to re-enter after an interruption. Pass
    ``force=True`` to rewrite every document regardless of content hash.

    Args:
        db: Database instance with RAG methods
        progress_callback: Optional callable invoked as (done, total) after
                           each session
        force: Bypass the unchanged-content skip.

    Returns:
        Number of sessions indexed successfully
    """
    sessions = db.list_sessions()
    total = len(sessions)
    indexed = 0

    for position, session in enumerate(sessions, start=1):
        session_id = session.get('id')
        if session_id is not None and index_session_content(db, session_id, force=force):
            indexed += 1
        if progress_callback:
            progress_callback(position, total)

    logger.info(f"Re-indexed {indexed}/{total} sessions")
    return indexed
