"""Qt-free logic behind the screenshot viewer window (BU109).

Kept apart from ``src/app/screenshot_viewer.py`` so it can be unit-tested
without PySide6.
"""
import json
import os
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .metadata import capture_time_label
from .search import fold

ZOOM_STEP = 1.25
MIN_ZOOM = 0.1
MAX_ZOOM = 8.0

STATE_AI = 'ai'
STATE_PREVIEW = 'preview'


def clamp_index(index: int, count: int) -> int:
    """``index`` limited to ``[0, count - 1]``; -1 when there is nothing."""
    if count <= 0:
        return -1
    return max(0, min(index, count - 1))


def step_index(current: int, delta: int, count: int) -> int:
    """Move ``delta`` items from ``current`` without wrapping."""
    return clamp_index(current + delta, count)


def wrap_index(current: int, delta: int, count: int) -> int:
    """Move ``delta`` items from ``current``, wrapping around (next match)."""
    if count <= 0:
        return -1
    return (current + delta) % count


def index_after_delete(deleted_index: int, count_after: int) -> int:
    """Where the selection lands after removing ``deleted_index``: the item
    that took its place, or the new last item."""
    return clamp_index(deleted_index, count_after)


def next_zoom(zoom: float, direction: int) -> float:
    """One zoom step in or out (``direction`` > 0 zooms in), clamped."""
    factor = ZOOM_STEP if direction > 0 else 1 / ZOOM_STEP
    return max(MIN_ZOOM, min(MAX_ZOOM, zoom * factor))


def _as_list(value) -> List[str]:
    if not value:
        return []
    if isinstance(value, list):
        items = value
    else:
        try:
            items = json.loads(value)
        except (TypeError, ValueError):
            return [str(value).strip()] if str(value).strip() else []
        if not isinstance(items, list):
            items = [items]
    return [str(v).strip() for v in items if v is not None and str(v).strip()]


def visible_text_items(row: Dict[str, Any]) -> List[str]:
    return _as_list(row.get('visible_text'))


def keyword_items(row: Dict[str, Any]) -> List[str]:
    return _as_list(row.get('keywords'))


def context_state(row: Dict[str, Any]) -> str:
    """'ai' once the vision model described the screenshot, else 'preview'."""
    return STATE_AI if (row.get('ai_summary') or '').strip() else STATE_PREVIEW


def context_state_label(row: Dict[str, Any]) -> str:
    return 'AI context' if context_state(row) == STATE_AI else 'Preview only'


def capture_meta(row: Dict[str, Any], session_start: Optional[float] = None) -> str:
    """"14:03:12 (+00:12:31)" for the header meta row."""
    return capture_time_label(row.get('timestamp'), session_start) or 'unknown time'


def can_generate_context(has_summary: bool, has_transcripts: bool) -> bool:
    """The ±20 s transcript window is enough context; a summary only helps."""
    return bool(has_summary or has_transcripts)


def detail_sections(row: Dict[str, Any], terms: Iterable[str] = ()) -> List[Tuple[str, str]]:
    """``(title, body)`` for each details-panel section, empty ones carrying
    a hint instead of disappearing. Search ``terms`` are marked in the
    visible text - the only field the search matches (BU110)."""
    terms = list(terms)
    visible = visible_text_items(row)
    keywords = keyword_items(row)
    no_ai = 'Not generated yet. Use "Generate context".'
    return [
        ('Description', (row.get('description') or '').strip()
         or 'No description. Use "Edit" to add one.'),
        ('Preview', (row.get('preview_description') or '').strip() or 'No preview yet.'),
        ('AI Summary', (row.get('ai_summary') or '').strip() or no_ai),
        ('Visible Text', '\n'.join(f'- {mark_terms(item, terms)}' for item in visible)
         if visible else no_ai),
        ('Keywords', ', '.join(keywords) if keywords else no_ai),
    ]


# ---- visible-text search (BU110) ------------------------------------------

def search_terms(query: str) -> List[str]:
    """Accent- and case-folded words of a search query."""
    return [t for t in fold(query or '').split() if t]


def match_screenshot(row: Dict[str, Any], terms: List[str]) -> bool:
    """True when every term appears in the screenshot's visible text."""
    if not terms:
        return False
    joined = '\n'.join(fold(item) for item in visible_text_items(row))
    return all(term in joined for term in terms)


def filter_screenshots(rows: List[Dict[str, Any]], query: str,
                       limit: Optional[int] = None) -> List[Dict[str, Any]]:
    terms = search_terms(query)
    matches = [row for row in rows if match_screenshot(row, terms)]
    return matches[:limit] if limit else matches


def missing_visible_text(rows: List[Dict[str, Any]]) -> int:
    """How many screenshots have no visible text to search yet."""
    return sum(1 for row in rows if not visible_text_items(row))


# Private-use characters bracketing a search match. They pass untouched
# through HTML escaping, and the details panel swaps them for a highlight span.
MATCH_START = ''
MATCH_END = ''


def mark_terms(text: str, terms: Iterable[str]) -> str:
    """Bracket accent/case-insensitive occurrences of ``terms`` with
    ``MATCH_START`` / ``MATCH_END`` so the details panel can highlight them."""
    terms = [t for t in terms if t]
    if not text or not terms:
        return text
    folded, origin = [], []
    for index, char in enumerate(text):
        for folded_char in fold(char):
            folded.append(folded_char)
            origin.append(index)
    folded = ''.join(folded)

    spans = []
    for term in terms:
        start = folded.find(term)
        while start != -1:
            end = start + len(term)
            spans.append((origin[start], origin[end - 1] + 1))
            start = folded.find(term, end)
    if not spans:
        return text

    spans.sort()
    merged = [list(spans[0])]
    for start, end in spans[1:]:
        if start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])

    out, cursor = [], 0
    for start, end in merged:
        out.append(text[cursor:start])
        out.append(f'{MATCH_START}{text[start:end]}{MATCH_END}')
        cursor = end
    out.append(text[cursor:])
    return ''.join(out)


def help_sections(capture_hotkey: str = 'Ctrl+Alt+S') -> List[Tuple[str, str]]:
    """``(title, body)`` pairs for the viewer's help window. Bodies use the
    bullet / ``**bold**`` subset the collapsible sections render."""
    hotkey = f'**{capture_hotkey}**' if capture_hotkey else 'the capture shortcut'
    return [
        ('Get around', '\n'.join([
            '- **← / →** or the **◀ ▶** buttons on the image: previous / next screenshot',
            '- **Click a thumbnail** in the strip at the bottom to jump to it',
            '- **Home / End**: first / last screenshot',
        ])),
        ('Look closer', '\n'.join([
            '- **Double-click the image** (or press **F**) to see it full screen. '
            '**Esc** closes full screen',
            '- **Ctrl + mouse wheel** or **+ / −**: zoom in and out',
            '- **Drag** the image to move around when it is zoomed in',
            '- **Fit** (or **0**): fit the whole image back in the window',
        ])),
        ('Find a screenshot', '\n'.join([
            '- Type in the **search box** (**Ctrl+F**) to find text that appears '
            'inside the screenshots. Accents and capitals don\'t matter',
            '- **This session / All sessions**: where to look',
            '- Matches are highlighted and the details panel slides to the first one',
            '- **Enter** or **F3**: next match. **Esc** clears the search',
            '- Only screenshots with AI context have text to search: use '
            '**Generate missing** first',
        ])),
        ('Buttons', '\n'.join([
            '- **Generate context**: the AI describes the selected screenshot (summary, '
            'visible text, keywords) using what was said when it was taken',
            '- **Generate missing**: the same, for every screenshot that doesn\'t have it yet',
            '- **Edit** (under the description): write your own note. It also helps '
            'the assistant find this screenshot',
            '- **Open folder**: shows the image file in File Explorer',
            '- **Delete** (or the **Del** key): removes the screenshot and its file. '
            'It asks first',
            '- **?** (or **F1**): opens this help',
        ])),
        ('What the labels mean', '\n'.join([
            '- **#42**: the screenshot number. The assistant uses it when it says '
            '"the information might be contained in the screenshot: #42"',
            '- **AI context**: the AI has already described this screenshot',
            '- **Preview only**: just an automatic description built from what was '
            'being said at that moment',
            '- **14:03:12 (+00:12:31)**: when it was taken, and how far into the session',
        ])),
        ('Taking screenshots', '\n'.join([
            f'- Use the camera button in the main window, or press {hotkey} from any '
            'app while a session is recording or paused',
            '- This window can stay open while you record: new screenshots show up '
            'here on their own',
        ])),
        ('Ask the assistant', '\n'.join([
            '- In **Specific Session** scope, ask about something you captured. The '
            'answer points to the screenshot, and a **View screenshot #N** button '
            'opens it right here',
        ])),
    ]


def description_file_for(filepath: str) -> str:
    """Path of the ``description_<name>.txt`` sidecar written at capture time."""
    directory, filename = os.path.split(filepath)
    return os.path.join(directory, f'description_{os.path.splitext(filename)[0]}.txt')
