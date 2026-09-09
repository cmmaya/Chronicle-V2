"""Tier-1 session router for Any Session mode (BU089).

Given a natural-language question, pick the handful of sessions it is about out
of a corpus that may hold 1000+ meetings, so Tier-2 chunk search only ever runs
*inside* already-routed sessions.

Scoring is hybrid:

    score = alpha * normalised_cosine(q_vec, session_vec)
          + beta  * normalised_bm25(profile_text + name)

Cosine is a brute-force numpy dot product over an ``N x 384`` float32 matrix
held in memory (~1.5 MB at 1000 sessions). No vector database.

If the embedding service is unavailable the router still works using the
lexical (BM25) half alone.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Dict, List, Optional

import numpy as np

from .embeddings import get_embedding_service
from .indexer import extract_keywords

logger = logging.getLogger(__name__)

# Blend weights when both signals are available.
ALPHA_COSINE = 0.6
BETA_LEXICAL = 0.4

# Retrieval-tuned encoders like E5 pack real cosine similarity into a narrow
# high band, so an absolute rescale separates signal from noise better than a
# min-max over candidates (which would always hand some session a 1.0). Values
# below the floor are treated as "unrelated"; above the ceiling as "on topic".
COSINE_FLOOR = 0.70
COSINE_CEIL = 0.90

# A routed session must clear this blended score, otherwise it is dropped so an
# unrelated question yields no candidates rather than arbitrary ones.
MIN_SCORE = 0.15

DEFAULT_TOP_K = 5


@dataclass
class RoutedSession:
    session_id: int
    name: str
    start_time: Optional[int]
    score: float
    reason: str


class _ProfileCache:
    """In-memory copy of the session profile matrix, rebuilt when profiles change."""

    def __init__(self) -> None:
        self.signature: tuple = (-1, -1)
        self.model_id: Optional[str] = None
        self.session_ids: List[int] = []
        self.names: Dict[int, str] = {}
        self.start_times: Dict[int, Optional[int]] = {}
        self.keywords: Dict[int, List[str]] = {}
        self.matrix: Optional[np.ndarray] = None  # (N, dim), row i -> session_ids[i]
        self.has_vectors: List[bool] = []


_cache = _ProfileCache()
_cache_lock = threading.Lock()


def invalidate_profile_cache() -> None:
    """Force the next :func:`route_sessions` call to reload profiles."""
    with _cache_lock:
        _cache.signature = (-1, -1)


def _load_cache(db) -> _ProfileCache:
    embedder = get_embedding_service()
    model_id = embedder.model_id if embedder.is_available else None
    signature = db.session_profiles_signature()

    with _cache_lock:
        if _cache.signature == signature and _cache.model_id == model_id:
            return _cache

        rows = db.get_session_profiles()
        session_ids: List[int] = []
        names: Dict[int, str] = {}
        start_times: Dict[int, Optional[int]] = {}
        keywords: Dict[int, List[str]] = {}
        vectors: List[np.ndarray] = []
        has_vectors: List[bool] = []

        dim = embedder.dim
        for row in rows:
            sid = row["session_id"]
            session_ids.append(sid)
            names[sid] = row.get("session_name") or f"Session {sid}"
            start_times[sid] = row.get("start_time")
            kw = row.get("keywords") or ""
            keywords[sid] = [k.strip() for k in kw.split(",") if k.strip()]

            blob = row.get("embedding")
            usable = (
                blob is not None
                and model_id is not None
                and row.get("embedding_model") == model_id
            )
            if usable:
                vec = np.frombuffer(blob, dtype=np.float32)
                if vec.shape[0] == dim:
                    vectors.append(vec)
                    has_vectors.append(True)
                    continue
            vectors.append(np.zeros(dim, dtype=np.float32))
            has_vectors.append(False)

        _cache.signature = signature
        _cache.model_id = model_id
        _cache.session_ids = session_ids
        _cache.names = names
        _cache.start_times = start_times
        _cache.keywords = keywords
        _cache.matrix = np.vstack(vectors) if vectors else None
        _cache.has_vectors = has_vectors
        return _cache


def _min_max(values: Dict[int, float]) -> Dict[int, float]:
    if not values:
        return {}
    lo = min(values.values())
    hi = max(values.values())
    if hi - lo < 1e-9:
        return {k: 1.0 for k in values}
    return {k: (v - lo) / (hi - lo) for k, v in values.items()}


def route_sessions(db, query: str, k: int = DEFAULT_TOP_K) -> List[RoutedSession]:
    """Return up to ``k`` candidate sessions ranked by hybrid relevance."""
    query = (query or "").strip()
    if not query:
        return []

    try:
        cache = _load_cache(db)
    except Exception as e:  # noqa: BLE001
        logger.warning("Session profile cache load failed: %s", e)
        return []

    if not cache.session_ids:
        return []

    # --- lexical half -----------------------------------------------------
    # Search only on content words: raw questions are full of articles that the
    # FTS tokenizer would otherwise match against every profile.
    lexical_terms = extract_keywords(query, max_keywords=10)
    lexical_raw: Dict[int, float] = {}
    if lexical_terms:
        try:
            for hit in db.search_session_profiles_fts(" ".join(lexical_terms), limit=50):
                # bm25 rank is negative/low = better; invert so higher = better.
                lexical_raw[hit["session_id"]] = -float(hit["rank"])
        except Exception as e:  # noqa: BLE001
            logger.warning("Session profile FTS failed: %s", e)
    lexical = _min_max(lexical_raw)

    # --- semantic half --------------------------------------------------
    cosine: Dict[int, float] = {}
    embedder = get_embedding_service()
    if embedder.is_available and cache.matrix is not None and any(cache.has_vectors):
        q_vec = embedder.embed_query(query)
        if q_vec is not None:
            sims = cache.matrix @ q_vec.astype(np.float32)
            span = COSINE_CEIL - COSINE_FLOOR
            for idx, sid in enumerate(cache.session_ids):
                if cache.has_vectors[idx]:
                    rescaled = (float(sims[idx]) - COSINE_FLOOR) / span
                    cosine[sid] = min(1.0, max(0.0, rescaled))

    have_semantic = any(v > 0.0 for v in cosine.values())
    have_lexical = bool(lexical)
    if not have_semantic and not have_lexical:
        return []

    if have_semantic and have_lexical:
        alpha, beta = ALPHA_COSINE, BETA_LEXICAL
    elif have_semantic:
        alpha, beta = 1.0, 0.0
    else:
        alpha, beta = 0.0, 1.0

    query_terms = {t for t in query.lower().split() if len(t) > 3}

    scored: List[RoutedSession] = []
    candidate_ids = set(cosine) | set(lexical)
    for sid in candidate_ids:
        score = alpha * cosine.get(sid, 0.0) + beta * lexical.get(sid, 0.0)
        if score < MIN_SCORE:
            continue
        scored.append(RoutedSession(
            session_id=sid,
            name=cache.names.get(sid, f"Session {sid}"),
            start_time=cache.start_times.get(sid),
            score=round(score, 4),
            reason=_reason(sid, cosine, lexical, cache.keywords.get(sid, []), query_terms),
        ))

    scored.sort(key=lambda r: r.score, reverse=True)
    return scored[:k]


def _reason(
    session_id: int,
    cosine: Dict[int, float],
    lexical: Dict[int, float],
    keywords: List[str],
    query_terms: set,
) -> str:
    overlap = [k for k in keywords if k in query_terms]
    bits: List[str] = []
    if cosine.get(session_id, 0.0) >= 0.5:
        bits.append("topic match")
    if lexical.get(session_id, 0.0) >= 0.5:
        bits.append("keyword match")
    if overlap:
        bits.append("mentions " + ", ".join(overlap[:3]))
    return "; ".join(bits) if bits else "weak match"
