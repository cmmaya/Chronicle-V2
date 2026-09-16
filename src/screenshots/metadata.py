"""Preliminary screenshot descriptions built without any AI call (BU106).

A screenshot's preview is the short text retrieval ranks on before it decides
which screenshots deserve their full metadata. Until the vision model has
described a screenshot, the preview is derived here from the user's own
description and what was being said around the capture time.
"""
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional

PREVIEW_MAX_CHARS = 240
WINDOW_BEFORE_SECONDS = 20
WINDOW_AFTER_SECONDS = 20

PREVIEW_SOURCE_AUTO = 'auto'
PREVIEW_SOURCE_AI = 'ai'


def _clip(text: str, limit: int) -> str:
    """Cut ``text`` to ``limit`` chars on a word boundary, marking the cut."""
    text = ' '.join(text.split())
    if len(text) <= limit:
        return text
    cut = text[:max(0, limit - 1)]
    space = cut.rfind(' ')
    if space > limit // 2:
        cut = cut[:space]
    return cut.rstrip(' ,.;:') + '…'


def _row_span(row: Dict[str, Any]) -> tuple:
    start = row.get('timestamp') or 0
    end = row.get('end_timestamp') or start
    return start, max(start, end)


def _distance(row: Dict[str, Any], ts: float) -> float:
    start, end = _row_span(row)
    if start <= ts <= end:
        return 0
    return min(abs(ts - start), abs(ts - end))


def transcript_window(transcripts: Iterable[Dict[str, Any]], ts: float,
                      before: int = WINDOW_BEFORE_SECONDS,
                      after: int = WINDOW_AFTER_SECONDS,
                      max_chars: int = 1200) -> str:
    """Chronological transcript text overlapping ``[ts - before, ts + after]``.

    Falls back to the two rows nearest ``ts`` when nothing overlaps the window,
    so a screenshot taken during a silence still gets some context. Rows
    closest to ``ts`` win when the text exceeds ``max_chars``.
    """
    rows = [r for r in transcripts if (r.get('text') or '').strip()]
    if not rows:
        return ''
    lo, hi = ts - before, ts + after
    window = [r for r in rows if _row_span(r)[1] >= lo and _row_span(r)[0] <= hi]
    if not window:
        window = sorted(rows, key=lambda r: _distance(r, ts))[:2]

    kept, used = [], 0
    for row in sorted(window, key=lambda r: _distance(r, ts)):
        text = ' '.join(row['text'].split())
        if used and used + len(text) + 1 > max_chars:
            continue
        kept.append(row)
        used += len(text) + 1
    kept.sort(key=lambda r: (_row_span(r)[0], r.get('id') or 0))
    return _clip(' '.join(' '.join(r['text'].split()) for r in kept), max_chars)


def capture_time_label(ts: Optional[float], session_start: Optional[float] = None) -> str:
    """``HH:MM:SS`` of the capture, plus ``(+HH:MM:SS)`` into the session."""
    if not ts:
        return ''
    try:
        label = datetime.fromtimestamp(ts).strftime('%H:%M:%S')
    except (OverflowError, OSError, ValueError):
        return ''
    if session_start and ts >= session_start:
        offset = int(ts - session_start)
        label += f' (+{offset // 3600:02d}:{offset % 3600 // 60:02d}:{offset % 60:02d})'
    return label


def build_preliminary_description(row: Dict[str, Any],
                                  transcripts: Iterable[Dict[str, Any]],
                                  session_start: Optional[float] = None,
                                  max_chars: int = PREVIEW_MAX_CHARS) -> str:
    """Deterministic preview: user description, what was said, capture time."""
    ts = row.get('timestamp') or 0
    time_label = capture_time_label(ts, session_start)
    suffix = f' · {time_label}' if time_label else ''
    budget = max(40, max_chars - len(suffix))

    parts = []
    description = ' '.join((row.get('description') or '').split())
    if description:
        parts.append(_clip(description, budget))
    remaining = budget - sum(len(p) for p in parts) - (3 if parts else 0)
    if remaining > 24:
        spoken = transcript_window(transcripts, ts)
        if spoken:
            parts.append(_clip(f'Discussed: {spoken}', remaining))
    if not parts:
        parts.append('Screenshot (no description or nearby speech)')
    return ' · '.join(parts) + suffix


def ensure_previews(db, session_id: int) -> List[Dict[str, Any]]:
    """Fill in / refresh the auto previews of one session's screenshots.

    Rows without a preview, or with an 'auto' one, are rebuilt from the
    current transcripts and written back only when the text changed, so a
    live session's previews improve as speech arrives. AI previews are never
    touched. Returns the Tier-1 rows (``get_screenshot_previews``) with the
    refreshed previews applied.
    """
    rows = db.get_screenshot_previews(session_id)
    pending = [r for r in rows if r.get('preview_source') != PREVIEW_SOURCE_AI]
    if not pending:
        return rows

    transcripts = db.get_transcripts(session_id)
    try:
        session = db.get_session(session_id) or {}
    except Exception:
        session = {}
    session_start = session.get('start_time')

    for row in pending:
        text = build_preliminary_description(row, transcripts, session_start)
        if text != row.get('preview_description') or row.get('preview_source') is None:
            db.update_screenshot_preview(row['id'], text, PREVIEW_SOURCE_AUTO)
            row['preview_description'] = text
            row['preview_source'] = PREVIEW_SOURCE_AUTO
    return rows
