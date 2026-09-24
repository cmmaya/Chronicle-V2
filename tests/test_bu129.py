"""BU129 - Machine-readable Calendar date in Due Dates entries.

The summary-window formatter lives in src/app/pixel_widgets.py next to Qt
widgets, so it is extracted from source and exec'd in isolation (as in
test_bu100.py).
"""
import ast
import html as html_escape
import os
import re
import unittest
from datetime import date, time

from src.calendar_sync.due_dates import (
    parse_calendar_date,
    parse_due_date_entries,
)

_PIXEL_WIDGETS_PATH = os.path.join(
    os.path.dirname(__file__), "..", "src", "app", "pixel_widgets.py"
)
_WANTED = {
    "_NUMBERED_LINE_RE",
    "_BULLET_LINE_RE",
    "_FIELD_LINE_RE",
    "_MD_BOLD_RE",
    "escape_with_markdown_bold",
    "format_summary_body_html",
}


def _load_formatter():
    with open(_PIXEL_WIDGETS_PATH, "r", encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    ns = {"re": re, "html_escape": html_escape}
    for node in tree.body:
        name = getattr(node, "name", None) or next(
            (t.id for t in getattr(node, "targets", []) if isinstance(t, ast.Name)), None
        )
        if name in _WANTED:
            exec(compile(ast.Module([node], []), _PIXEL_WIDGETS_PATH, "exec"), ns)
    return ns["format_summary_body_html"]


format_summary_body_html = _load_formatter()

BODY = """Title: Submit the lab report
Due date: next Friday at 3 pm (2026-09-25)
Calendar date: 2026-09-25 15:00
Description: Upload the report to the course portal before the
deadline, including the appendix.

Title: Arrive before each shift
Due date: at least 15 minutes prior (to each scheduled shift)
Calendar date: none
Description: Staff must check in early.

Title: Confirm the venue
Due date: next week, Thursday or Friday (2026-09-24 or 2026-09-25)
Calendar date: none
Description: Call the venue.

Title: Team sync
Due date: Monday 10 to 11 (2026-09-21)
**Calendar date:** 2026-09-21 10:00-11:00
Description: Weekly sync.
"""

LEGACY_BODY = """Title: Pay the invoice
Due date: end of the month (2026-09-30)
Description: Pay supplier invoice.

Title: Pick a demo day
Due date: Thursday or Friday (2026-09-24 or 2026-09-25)
Description: Choose one.

Title: Call the client
Due date: next Tuesday
Description: No recording date.
"""


class ParseCalendarDateTests(unittest.TestCase):
    def test_all_day(self):
        self.assertEqual(parse_calendar_date("2026-09-25"), (date(2026, 9, 25), None, None))

    def test_timed(self):
        self.assertEqual(
            parse_calendar_date("2026-09-25 15:00"), (date(2026, 9, 25), time(15, 0), None)
        )

    def test_timed_with_end(self):
        self.assertEqual(
            parse_calendar_date("2026-09-25 09:30-11:00"),
            (date(2026, 9, 25), time(9, 30), time(11, 0)),
        )

    def test_none_and_invalid(self):
        for value in (
            "none",
            "2026-02-30",
            "2026-09-25 25:00",
            "2026-09-25 10:00-09:00",
            "2026-09-25 10:00-10:00",
            "",
            "next Friday",
            "2026-09-25 or 2026-09-26",
        ):
            with self.subTest(value=value):
                self.assertEqual(parse_calendar_date(value), (None, None, None))


class ParseDueDateEntriesTests(unittest.TestCase):
    def test_multi_entry_body(self):
        entries = parse_due_date_entries(BODY)
        self.assertEqual(
            [e.title for e in entries],
            ["Submit the lab report", "Arrive before each shift", "Confirm the venue", "Team sync"],
        )
        self.assertEqual([e.index for e in entries], [0, 1, 2, 3])
        first = entries[0]
        self.assertEqual(
            first.description,
            "Upload the report to the course portal before the deadline, including the appendix.",
        )
        self.assertEqual(first.due_text, "next Friday at 3 pm (2026-09-25)")
        self.assertEqual((first.date, first.start_time, first.end_time),
                         (date(2026, 9, 25), time(15, 0), None))
        self.assertIsNone(entries[1].date)
        self.assertIsNone(entries[2].date)
        self.assertEqual((entries[3].start_time, entries[3].end_time), (time(10), time(11)))

    def test_legacy_fallback(self):
        entries = parse_due_date_entries(LEGACY_BODY)
        self.assertEqual(entries[0].date, date(2026, 9, 30))
        self.assertIsNone(entries[0].start_time)
        self.assertIsNone(entries[1].date)
        self.assertIsNone(entries[2].date)

    def test_no_due_dates(self):
        self.assertEqual(
            parse_due_date_entries("No due dates were mentioned in the transcript."), []
        )
        self.assertEqual(parse_due_date_entries(""), [])

    def test_fingerprint_is_stable(self):
        a = parse_due_date_entries("Title: Submit the  Lab report!")[0]
        b = parse_due_date_entries("Title: submit the lab report")[0]
        self.assertEqual(a.fingerprint, b.fingerprint)


class SummaryWindowTests(unittest.TestCase):
    def test_calendar_date_line_hidden(self):
        html = format_summary_body_html(BODY + "Calendar date: 2026-09-25\nwrapped tail")
        self.assertNotIn("Calendar date", html)
        self.assertNotIn("wrapped tail", html)
        self.assertIn("Submit the lab report", html)
        self.assertIn("next Friday at 3 pm", html)

    def test_legacy_body_unchanged(self):
        html = format_summary_body_html(LEGACY_BODY)
        self.assertIn("Pay the invoice", html)
        self.assertIn("Due date:", html)


if __name__ == "__main__":
    unittest.main()
