"""Create Google Calendar events from edited due dates (BU132).

An :class:`EventDraft` is what the user confirmed in the event dialog. No
time means an all-day event (``end.date`` is exclusive, so it is the next
day); a start time with no end means one hour. Events go to the primary
calendar with the calendar's default reminders, and carry the Chronicle
session ID and entry fingerprint as private extended properties.

Event bodies are never logged: they hold the user's meeting content.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import List, Optional

import requests

from src.calendar_sync import google_auth
from src.calendar_sync.google_auth import GoogleAuthExpired

logger = logging.getLogger(__name__)

EVENTS_URL = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
HTTP_TIMEOUT_SECONDS = 10
DEFAULT_DURATION = timedelta(hours=1)
MAX_TITLE_CHARS = 1024
MAX_DESCRIPTION_CHARS = 8000


class CalendarError(Exception):
    """A Calendar API call failed; the message is meant for the user."""


@dataclass(frozen=True)
class CreatedEvent:
    id: str
    html_link: str


def _local_timezone_name() -> Optional[str]:
    """The machine's IANA zone name, or None when the OS does not expose one."""
    tzinfo = datetime.now().astimezone().tzinfo
    key = getattr(tzinfo, "key", None)  # zoneinfo.ZoneInfo
    if key:
        return key
    name = tzinfo.tzname(None) if tzinfo else None
    # Windows gives names such as "SA Pacific Standard Time", not IANA ones.
    return name if name and "/" in name else None


@dataclass
class EventDraft:
    title: str
    description: str
    date: Optional[date]
    start_time: Optional[time] = None
    end_time: Optional[time] = None

    def validate(self) -> List[str]:
        """Messages for everything that stops the draft from becoming an event."""
        problems = []
        title = (self.title or "").strip()
        if not title:
            problems.append("Enter a title.")
        elif len(title) > MAX_TITLE_CHARS:
            problems.append(f"The title is longer than {MAX_TITLE_CHARS:,} characters.")
        if len(self.description or "") > MAX_DESCRIPTION_CHARS:
            problems.append(
                f"The description is longer than {MAX_DESCRIPTION_CHARS:,} characters."
            )
        if self.date is None:
            problems.append("Choose a date.")
        if self.end_time is not None and self.start_time is None:
            problems.append("Set a start time before an end time.")
        elif (self.start_time is not None and self.end_time is not None
              and self.end_time <= self.start_time):
            problems.append("The end time must be after the start time.")
        return problems

    def to_event_body(self, session_id: int, fingerprint: str) -> dict:
        body = {
            "summary": (self.title or "").strip(),
            "description": self.description or "",
            "reminders": {"useDefault": True},
            "extendedProperties": {"private": {
                "chronicle_session_id": str(session_id),
                "chronicle_fingerprint": fingerprint,
            }},
        }
        if self.start_time is None:
            body["start"] = {"date": self.date.isoformat()}
            body["end"] = {"date": (self.date + timedelta(days=1)).isoformat()}
            return body

        start = datetime.combine(self.date, self.start_time).astimezone()
        if self.end_time is not None:
            end = datetime.combine(self.date, self.end_time).astimezone()
        else:
            end = (datetime.combine(self.date, self.start_time) + DEFAULT_DURATION).astimezone()
        zone = _local_timezone_name()
        for key, moment in (("start", start), ("end", end)):
            body[key] = {"dateTime": moment.isoformat(timespec="seconds")}
            if zone:
                body[key]["timeZone"] = zone
        return body


def _error_reason(response: requests.Response) -> str:
    try:
        error = response.json().get("error") or {}
        errors = error.get("errors") or []
        return str((errors[0].get("reason") if errors else "") or error.get("status") or "")
    except (ValueError, AttributeError):
        return ""


def _send(method: str, url: str, **kwargs) -> requests.Response:
    """One authorized request; a 401 refreshes the token and retries once."""
    for attempt in range(2):
        token = google_auth.get_access_token(force_refresh=attempt > 0)
        try:
            response = requests.request(
                method, url,
                headers={"Authorization": f"Bearer {token}"},
                timeout=HTTP_TIMEOUT_SECONDS,
                **kwargs,
            )
        except requests.RequestException as exc:
            logger.warning("Calendar request failed: %s", type(exc).__name__)
            raise CalendarError(
                "Could not reach Google Calendar. Check your internet connection and try again."
            ) from None
        if response.status_code != 401:
            break
        logger.info("Calendar request got 401 (attempt %d)", attempt + 1)
    if response.status_code == 401:
        raise GoogleAuthExpired("Reconnect your Google account.")
    if response.status_code == 403 and _error_reason(response) in (
        "insufficientPermissions", "PERMISSION_DENIED",
    ):
        raise GoogleAuthExpired(
            "Chronicle is not allowed to add Calendar events. Reconnect your "
            "Google account and allow Calendar access."
        )
    return response


def _raise_for_status(response: requests.Response, action: str) -> None:
    if 200 <= response.status_code < 300:
        return
    reason = _error_reason(response)
    logger.warning("Calendar %s failed: HTTP %s %s", action, response.status_code, reason)
    if response.status_code == 429 or reason in ("rateLimitExceeded", "userRateLimitExceeded"):
        raise CalendarError("Google Calendar is busy. Wait a minute and try again.")
    if response.status_code >= 500:
        raise CalendarError("Google Calendar is not responding right now. Try again later.")
    detail = f"HTTP {response.status_code}" + (f", {reason}" if reason else "")
    raise CalendarError(f"Google Calendar could not {action} ({detail}).")


def create_event(draft: EventDraft, session_id: int, fingerprint: str) -> CreatedEvent:
    """Insert ``draft`` into the primary calendar.

    Raises :class:`GoogleAuthExpired` when access was revoked or lacks the
    Calendar permission, and :class:`CalendarError` for everything else.
    """
    problems = draft.validate()
    if problems:
        raise CalendarError(problems[0])
    response = _send("POST", EVENTS_URL, json=draft.to_event_body(session_id, fingerprint))
    _raise_for_status(response, "create the event")
    try:
        data = response.json()
        created = CreatedEvent(id=str(data["id"]), html_link=str(data.get("htmlLink") or ""))
    except (ValueError, KeyError, TypeError):
        raise CalendarError("Google Calendar sent an unexpected reply.") from None
    logger.info("Calendar event created for session %s", session_id)
    return created


def event_exists(event_id: str) -> bool:
    """Whether the event is still in the primary calendar (not deleted)."""
    response = _send("GET", f"{EVENTS_URL}/{requests.utils.quote(event_id, safe='')}")
    if response.status_code in (404, 410):
        return False
    _raise_for_status(response, "look up the event")
    try:
        return response.json().get("status") != "cancelled"
    except (ValueError, AttributeError):
        raise CalendarError("Google Calendar sent an unexpected reply.") from None
