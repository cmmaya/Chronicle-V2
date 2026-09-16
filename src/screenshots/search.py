"""Two-tier screenshot search for one session (BU107).

Tier 1 ranks every screenshot of the session using only its light fields
(preview, user description, keywords) plus its capture time. Tier 2 then
loads the full metadata (AI summary, visible text) for the few winners only,
so the assistant prompt carries a cheap index of all screenshots and the
expensive details of just the relevant ones.

Sessions hold tens of screenshots, so ranking is done in memory.
"""
import json
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional

from .metadata import ensure_previews

MAX_PREVIEW_CARDS = 12
MAX_FULL_DETAILS = 3
PREVIEW_CHARS = 240
DETAIL_CHARS = 900

# Score needed for Tier-2 promotion. Tuned so that closeness in time alone
# (TEMPORAL_WEIGHT) is not enough, but time plus one matching word is.
PROMOTE_MIN_SCORE = 0.4
TEXT_WEIGHT = 1.0
KEYWORD_WEIGHT = 1.5
TEMPORAL_WEIGHT = 0.35
DEICTIC_BOOST = 0.5
REFERENCE_BOOST = 10.0

TEMPORAL_FULL_SECONDS = 60
TEMPORAL_ZERO_SECONDS = 180

STOP_WORDS = frozenset("""
    the and for that this with are was were have has had been but not you all can
    her she him his its our they them their what when where who whom which will
    from into about there here then than also just like some any how why does did
    doing done said say says tell told show shown please could would should
    screenshot screenshots screen screens image images picture capture
    que para por con los las del una uno unos unas como pero mas esta este esto
    estos estas ese esa eso esos esas son fue ser hay han habia tiene tienen sobre
    donde cuando quien cual cuales porque entre desde hasta tambien muy sin dijo
    dijeron decir dime muestra mostrar captura capturas pantalla pantallas
    pantallazo imagen imagenes
""".split())

_WORD = re.compile(r'\w+', re.UNICODE)

# "screenshot #42", "screenshot 42", "captura 42", "captura nº 42", or a bare "#42".
_REFERENCE = re.compile(
    r'(?:#\s*(\d+))'
    r'|(?:\b(?:screen\s*shot|screenshot|captura|pantallazo|imagen|image)\s*'
    r'(?:n[o°º]\.?\s*|number\s*|numero\s*)?(\d+)\b)'
)

# Questions that point at "the screenshot" / "what was on screen" without
# naming its content.
_DEICTIC = re.compile(
    r'\b(?:screen\s*shots?|screenshots?|on (?:the )?screen|capturas?|pantallazos?'
    r'|en (?:la )?pantalla|imagen(?:es)?|images?|slides?|diapositivas?)\b'
)


@dataclass
class ScreenshotHit:
    screenshot_id: int
    timestamp: int
    filepath: str
    preview: str
    description: str = ''  # the user's own note, when they typed one
    score: float = 0.0
    # Subset of: lexical, temporal, reference, deictic.
    reasons: List[str] = field(default_factory=list)
    tier: str = 'preview'  # 'preview' | 'full'
    # Set for tier 'full': ai_summary (str), visible_text (list), keywords (list).
    details: Optional[Dict[str, Any]] = None


def fold(text: str) -> str:
    """Lowercase and strip accents ("Reunión" -> "reunion")."""
    decomposed = unicodedata.normalize('NFKD', text or '')
    return ''.join(c for c in decomposed if not unicodedata.combining(c)).lower()


def normalize_tokens(text: str) -> List[str]:
    """Accent-folded content words (3+ chars, English/Spanish stop words out)."""
    return [t for t in _WORD.findall(fold(text)) if len(t) >= 3 and t not in STOP_WORDS]


def _tokens_match(a: str, b: str) -> bool:
    """Equal, or the same word up to a short suffix (ticket / tickets,
    reunion / reuniones)."""
    if a == b:
        return True
    if min(len(a), len(b)) < 4 or abs(len(a) - len(b)) > 2:
        return False
    return a.startswith(b) or b.startswith(a)


def _matches_any(token: str, pool: Iterable[str]) -> bool:
    return any(_tokens_match(token, other) for other in pool)


def _json_list(value) -> List[str]:
    if not value:
        return []
    if isinstance(value, list):
        return [str(v) for v in value if str(v).strip()]
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return [part.strip() for part in str(value).split(',') if part.strip()]
    if isinstance(parsed, list):
        return [str(v) for v in parsed if str(v).strip()]
    return [str(parsed)] if str(parsed).strip() else []


def _clip(text: str, limit: int) -> str:
    text = ' '.join((text or '').split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + '…'


def referenced_ids(question: str) -> List[int]:
    """Screenshot ids the question names explicitly, in order of mention."""
    ids = []
    for match in _REFERENCE.finditer(fold(question)):
        value = int(match.group(1) or match.group(2))
        if value not in ids:
            ids.append(value)
    return ids


def _temporal_score(ts: int, anchors: List[int]) -> float:
    if not anchors or not ts:
        return 0.0
    distance = min(abs(ts - a) for a in anchors)
    if distance <= TEMPORAL_FULL_SECONDS:
        return 1.0
    span = TEMPORAL_ZERO_SECONDS - TEMPORAL_FULL_SECONDS
    return max(0.0, 1.0 - (distance - TEMPORAL_FULL_SECONDS) / span)


def rank_previews(previews: List[Dict[str, Any]], question: str,
                  anchor_timestamps: Iterable[int] = ()) -> List[ScreenshotHit]:
    """Tier 1: score screenshots from their light fields and capture time.

    Returns every screenshot as a ``ScreenshotHit``, best first (ties broken by
    capture time).
    """
    q_tokens = set(normalize_tokens(question))
    anchors = [int(a) for a in anchor_timestamps if a]
    refs = set(referenced_ids(question))

    hits = []
    for row in previews:
        preview = row.get('preview_description') or row.get('description') or ''
        text_pool = set(normalize_tokens(f"{preview} {row.get('description') or ''}"))
        keyword_pool = set(normalize_tokens(' '.join(_json_list(row.get('keywords')))))

        hit = ScreenshotHit(
            screenshot_id=row['id'],
            timestamp=row.get('timestamp') or 0,
            filepath=row.get('filepath') or '',
            preview=_clip(preview, PREVIEW_CHARS),
            description=' '.join((row.get('description') or '').split()),
        )

        if q_tokens:
            weight = 0.0
            for token in q_tokens:
                if _matches_any(token, keyword_pool):
                    weight += KEYWORD_WEIGHT
                elif _matches_any(token, text_pool):
                    weight += TEXT_WEIGHT
            if weight:
                hit.score += weight / len(q_tokens)
                hit.reasons.append('lexical')

        temporal = _temporal_score(hit.timestamp, anchors)
        if temporal:
            hit.score += TEMPORAL_WEIGHT * temporal
            hit.reasons.append('temporal')

        if hit.screenshot_id in refs:
            hit.score += REFERENCE_BOOST
            hit.reasons.append('reference')

        hits.append(hit)

    # "What was on the screenshot?" with no content words to go on: point at
    # the screenshot closest to the relevant speech, else the latest one.
    if hits and not refs and _DEICTIC.search(fold(question)) \
            and not any('lexical' in h.reasons for h in hits):
        if anchors:
            target = max(hits, key=lambda h: (_temporal_score(h.timestamp, anchors), h.timestamp))
        else:
            target = max(hits, key=lambda h: h.timestamp)
        target.score += DEICTIC_BOOST
        target.reasons.append('deictic')

    hits.sort(key=lambda h: (-h.score, h.timestamp))
    return hits


def promote(hits: List[ScreenshotHit], db, max_full: int = MAX_FULL_DETAILS,
            min_score: float = PROMOTE_MIN_SCORE,
            rows_by_id: Optional[Dict[int, Dict[str, Any]]] = None) -> List[ScreenshotHit]:
    """Tier 2: load full metadata for the top hits that clear ``min_score``.

    Explicitly referenced screenshots always qualify. Details are fetched in a
    single ``get_screenshot_details`` call; ``rows_by_id`` is used instead when
    the database has no such method (full rows were already loaded).
    Returns the promoted hits, which are also updated in place.
    """
    winners = [h for h in hits if 'reference' in h.reasons or h.score >= min_score][:max_full]
    if not winners:
        return []

    ids = [h.screenshot_id for h in winners]
    if hasattr(db, 'get_screenshot_details'):
        details_by_id = {r['id']: r for r in db.get_screenshot_details(ids)}
    else:
        details_by_id = {i: (rows_by_id or {}).get(i, {}) for i in ids}

    promoted = []
    for hit in winners:
        row = details_by_id.get(hit.screenshot_id)
        if not row:
            continue
        visible, used = [], 0
        for item in _json_list(row.get('visible_text')):
            item = ' '.join(item.split())
            if used + len(item) > DETAIL_CHARS:
                break
            visible.append(item)
            used += len(item) + 2
        hit.details = {
            'ai_summary': _clip(row.get('ai_summary') or '', DETAIL_CHARS),
            'visible_text': visible,
            'keywords': _json_list(row.get('keywords'))[:15],
        }
        hit.tier = 'full'
        promoted.append(hit)
    return promoted


def _load_rows(db, session_id: int) -> List[Dict[str, Any]]:
    """Tier-1 rows with fresh previews; degrades for minimal database fakes."""
    if hasattr(db, 'get_screenshot_previews'):
        try:
            return ensure_previews(db, session_id)
        except Exception:
            return db.get_screenshot_previews(session_id)
    return db.get_screenshots(session_id)


def search_session_screenshots(db, session_id: int, question: str,
                               anchor_timestamps: Iterable[int] = (),
                               max_preview: int = MAX_PREVIEW_CARDS,
                               max_full: int = MAX_FULL_DETAILS) -> List[ScreenshotHit]:
    """Screenshots of one session to show the assistant for ``question``.

    Returns at most ``max_preview`` hits: the promoted ('full') ones first in
    relevance order, then the remaining index cards in capture order. When
    the session has more screenshots than fit, relevant ones are kept first
    and the most recent ones fill the rest.
    """
    try:
        rows = _load_rows(db, session_id)
    except Exception:
        return []
    rows = [r for r in rows if r.get('id') is not None]
    if not rows:
        return []

    ranked = rank_previews(rows, question, anchor_timestamps)
    promoted = promote(ranked, db, max_full=max_full,
                       rows_by_id={r['id']: r for r in rows})
    promoted_ids = {h.screenshot_id for h in promoted}

    relevant = [h for h in ranked if h.score > 0 and h.screenshot_id not in promoted_ids]
    rest = sorted((h for h in ranked if h.score <= 0), key=lambda h: -h.timestamp)
    cards = (relevant + rest)[:max(0, max_preview - len(promoted))]
    cards.sort(key=lambda h: h.timestamp)
    return promoted + cards
