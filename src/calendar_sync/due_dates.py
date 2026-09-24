"""Parse the Due Dates section of a summary into structured entries (BU129).

Each entry the summary template produces looks like::

    Title: Submit the lab report
    Due date: next Friday at 3 pm (2026-09-25)
    Calendar date: 2026-09-25 15:00
    Description: Upload the report to the course portal.

``Calendar date`` is the machine-readable copy of ``Due date``. Summaries
written before BU129 lack it; for those a single ISO date inside the ``Due
date`` text is used as an all-day date.
"""

import hashlib
import re
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import List, Optional, Tuple

NO_DUE_DATES_SENTENCE = "No due dates were mentioned in the transcript."

# Same label shapes the summary window recognizes (pixel_widgets._FIELD_LINE_RE).
_FIELD_LINE_RE = re.compile(
    r"^\s*\*{0,2}(Title|Description|Calendar date|Due date|Due|Deadline|Responsible|Owner"
    r"|Assignee|Status)\*{0,2}\s*:\s*(.*)$",
    re.IGNORECASE,
)
_CALENDAR_DATE_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2})(?:\s+(\d{2}:\d{2})(?:\s*-\s*(\d{2}:\d{2}))?)?$"
)
_ISO_DATE_RE = re.compile(r"(?<!\d)\d{4}-\d{2}-\d{2}(?!\d)")

_FIELD_KEYS = {
    "title": "title",
    "description": "description",
    "calendar date": "calendar",
    "due date": "due",
    "due": "due",
    "deadline": "due",
}

CalendarDate = Tuple[Optional[date], Optional[time], Optional[time]]
_NO_DATE: CalendarDate = (None, None, None)


@dataclass
class DueDateEntry:
    title: str
    due_text: str
    description: str
    date: Optional[date]
    start_time: Optional[time]
    end_time: Optional[time]
    index: int

    @property
    def fingerprint(self) -> str:
        """Stable ID for the entry: SHA-1 of the title, lowercased, without
        punctuation and with whitespace collapsed (see BU132)."""
        normalized = re.sub(r"[^\w\s]", "", self.title.lower())
        normalized = " ".join(normalized.split())
        return hashlib.sha1(normalized.encode("utf-8")).hexdigest()


def _parse_time(value: str) -> Optional[time]:
    try:
        return datetime.strptime(value, "%H:%M").time()
    except ValueError:
        return None


def parse_calendar_date(value: str) -> CalendarDate:
    """Parse a ``Calendar date:`` value into ``(date, start, end)``.

    Accepts ``YYYY-MM-DD``, ``YYYY-MM-DD HH:MM`` and ``YYYY-MM-DD HH:MM-HH:MM``.
    ``none``, impossible dates or times, ``end <= start`` and anything else
    give ``(None, None, None)``.
    """
    match = _CALENDAR_DATE_RE.match((value or "").strip().strip("*").strip())
    if not match:
        return _NO_DATE
    day_text, start_text, end_text = match.groups()
    try:
        day = date.fromisoformat(day_text)
    except ValueError:
        return _NO_DATE
    if start_text is None:
        return day, None, None
    start = _parse_time(start_text)
    if start is None:
        return _NO_DATE
    if end_text is None:
        return day, start, None
    end = _parse_time(end_text)
    if end is None or end <= start:
        return _NO_DATE
    return day, start, end


def _legacy_date(due_text: str) -> Optional[date]:
    """Pre-BU129 fallback: the one ISO date in the Due date text, or None."""
    found = _ISO_DATE_RE.findall(due_text or "")
    if len(found) != 1:
        return None
    try:
        return date.fromisoformat(found[0])
    except ValueError:
        return None


def parse_due_date_entries(body: str) -> List[DueDateEntry]:
    """Split a Due Dates section body into entries, one per ``Title:`` line.

    Hard-wrapped continuation lines join the field they follow. Other labels
    (``Owner:``, ``Status:``...) are ignored.
    """
    body = (body or "").strip()
    if not body or body == NO_DUE_DATES_SENTENCE:
        return []

    raw_entries = []
    current = None
    field = None
    for raw in body.splitlines():
        line = raw.strip()
        if not line:
            field = None
            continue
        match = _FIELD_LINE_RE.match(line)
        if match:
            field = _FIELD_KEYS.get(match.group(1).lower())
            if field == "title":
                current = {}
                raw_entries.append(current)
            if field and current is not None:
                current[field] = [match.group(2).strip()]
            continue
        if field and current is not None:
            current[field].append(line)

    entries = []
    for index, fields in enumerate(raw_entries):
        text = {key: " ".join(parts).strip() for key, parts in fields.items()}
        due_text = text.get("due", "")
        if "calendar" in text:
            day, start, end = parse_calendar_date(text["calendar"])
        else:
            day, start, end = _legacy_date(due_text), None, None
        entries.append(
            DueDateEntry(
                title=text.get("title", ""),
                due_text=due_text,
                description=text.get("description", ""),
                date=day,
                start_time=start,
                end_time=end,
                index=index,
            )
        )
    return entries
