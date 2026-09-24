"""BU132 - calendar event service and sent-event tracking, HTTP mocked."""
import unittest
from datetime import date, datetime, time
from unittest import mock

from src.calendar_sync import events
from src.calendar_sync.due_dates import DueDateEntry
from src.calendar_sync.events import CalendarError, EventDraft
from src.calendar_sync.google_auth import GoogleAuthExpired
from src.storage.database import Database


def _response(status, body=None):
    response = mock.Mock(status_code=status)
    response.json.return_value = body or {}
    return response


def _entry(title):
    return DueDateEntry(title=title, due_text="", description="", date=None,
                        start_time=None, end_time=None, index=0)


class ValidateTests(unittest.TestCase):
    def _problems(self, **changes):
        fields = dict(title="Lab report", description="", date=date(2026, 9, 24))
        fields.update(changes)
        return EventDraft(**fields).validate()

    def test_valid_drafts(self):
        self.assertEqual(self._problems(), [])
        self.assertEqual(self._problems(start_time=time(15)), [])
        self.assertEqual(self._problems(start_time=time(15), end_time=time(15, 30)), [])

    def test_each_rule(self):
        self.assertIn("Enter a title.", self._problems(title="   "))
        self.assertIn("Choose a date.", self._problems(date=None))
        self.assertIn("Set a start time before an end time.",
                      self._problems(end_time=time(16)))
        self.assertIn("The end time must be after the start time.",
                      self._problems(start_time=time(15), end_time=time(15)))
        self.assertIn("The end time must be after the start time.",
                      self._problems(start_time=time(15), end_time=time(14)))
        self.assertEqual(len(self._problems(title="x" * 1025)), 1)
        self.assertEqual(self._problems(title="x" * 1024), [])
        self.assertEqual(len(self._problems(description="x" * 8001)), 1)
        self.assertEqual(self._problems(description="x" * 8000), [])


class BodyTests(unittest.TestCase):
    def test_all_day_end_is_exclusive(self):
        body = EventDraft("T", "D", date(2026, 9, 24)).to_event_body(7, "fp")
        self.assertEqual(body["start"], {"date": "2026-09-24"})
        self.assertEqual(body["end"], {"date": "2026-09-25"})
        self.assertEqual(body["summary"], "T")
        self.assertEqual(body["description"], "D")
        self.assertEqual(body["reminders"], {"useDefault": True})

    def test_all_day_year_boundary(self):
        body = EventDraft("T", "", date(2026, 12, 31)).to_event_body(7, "fp")
        self.assertEqual(body["end"], {"date": "2027-01-01"})
        body = EventDraft("T", "", date(2026, 2, 28)).to_event_body(7, "fp")
        self.assertEqual(body["end"], {"date": "2026-03-01"})

    def test_timed_default_hour_with_local_offset(self):
        body = EventDraft("T", "", date(2026, 9, 24), time(15)).to_event_body(7, "fp")
        start = datetime.fromisoformat(body["start"]["dateTime"])
        end = datetime.fromisoformat(body["end"]["dateTime"])
        self.assertNotIn("date", body["start"])
        self.assertEqual((start.hour, start.minute), (15, 0))
        self.assertEqual((end - start).total_seconds(), 3600)
        expected = datetime(2026, 9, 24, 15).astimezone().utcoffset()
        self.assertEqual(start.utcoffset(), expected)

    def test_timed_explicit_end(self):
        body = EventDraft("T", "", date(2026, 9, 24), time(15), time(15, 30)).to_event_body(7, "fp")
        start = datetime.fromisoformat(body["start"]["dateTime"])
        end = datetime.fromisoformat(body["end"]["dateTime"])
        self.assertEqual((end - start).total_seconds(), 1800)

    def test_time_zone_omitted_without_iana_name(self):
        with mock.patch.object(events, "_local_timezone_name", return_value=None):
            body = EventDraft("T", "", date(2026, 9, 24), time(9)).to_event_body(7, "fp")
        self.assertNotIn("timeZone", body["start"])
        with mock.patch.object(events, "_local_timezone_name", return_value="America/Bogota"):
            body = EventDraft("T", "", date(2026, 9, 24), time(9)).to_event_body(7, "fp")
        self.assertEqual(body["start"]["timeZone"], "America/Bogota")
        self.assertEqual(body["end"]["timeZone"], "America/Bogota")

    def test_extended_properties(self):
        body = EventDraft("T", "", date(2026, 9, 24)).to_event_body(42, "abc123")
        self.assertEqual(body["extendedProperties"]["private"], {
            "chronicle_session_id": "42", "chronicle_fingerprint": "abc123",
        })


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.token = mock.patch.object(events.google_auth, "get_access_token", return_value="tok")
        self.get_token = self.token.start()
        self.request = mock.patch.object(events.requests, "request").start()
        self.addCleanup(mock.patch.stopall)
        self.draft = EventDraft("Lab report", "", date(2026, 9, 24))

    def test_create_success(self):
        self.request.return_value = _response(200, {"id": "ev1", "htmlLink": "https://cal/ev1"})
        created = events.create_event(self.draft, 7, "fp")
        self.assertEqual(created, events.CreatedEvent("ev1", "https://cal/ev1"))
        args, kwargs = self.request.call_args
        self.assertEqual(args, ("POST", events.EVENTS_URL))
        self.assertEqual(kwargs["timeout"], 10)
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer tok")

    def test_401_retries_once_after_refresh(self):
        self.request.side_effect = [_response(401), _response(200, {"id": "ev1"})]
        self.assertEqual(events.create_event(self.draft, 7, "fp").id, "ev1")
        self.assertEqual(self.get_token.call_args_list,
                         [mock.call(force_refresh=False), mock.call(force_refresh=True)])

    def test_second_401_raises(self):
        self.request.side_effect = [_response(401), _response(401)]
        with self.assertRaises(GoogleAuthExpired):
            events.create_event(self.draft, 7, "fp")
        self.assertEqual(self.request.call_count, 2)

    def test_403_insufficient_permissions(self):
        self.request.return_value = _response(
            403, {"error": {"errors": [{"reason": "insufficientPermissions"}]}})
        with self.assertRaises(GoogleAuthExpired):
            events.create_event(self.draft, 7, "fp")

    def test_other_errors_are_calendar_errors(self):
        for status, body in ((400, {"error": {"errors": [{"reason": "badRequest"}]}}),
                             (429, {}), (503, {})):
            self.request.return_value = _response(status, body)
            with self.assertRaises(CalendarError):
                events.create_event(self.draft, 7, "fp")
        self.request.side_effect = events.requests.ConnectionError()
        with self.assertRaises(CalendarError):
            events.create_event(self.draft, 7, "fp")

    def test_invalid_draft_is_not_sent(self):
        with self.assertRaises(CalendarError):
            events.create_event(EventDraft("", "", None), 7, "fp")
        self.request.assert_not_called()

    def test_event_exists(self):
        self.request.return_value = _response(200, {"status": "confirmed"})
        self.assertTrue(events.event_exists("ev1"))
        self.assertEqual(self.request.call_args[0], ("GET", events.EVENTS_URL + "/ev1"))
        self.request.return_value = _response(200, {"status": "cancelled"})
        self.assertFalse(events.event_exists("ev1"))
        self.request.return_value = _response(404)
        self.assertFalse(events.event_exists("ev1"))


class FingerprintTests(unittest.TestCase):
    def test_stable_across_case_spacing_punctuation(self):
        base = _entry("Submit the lab report").fingerprint
        for variant in ("submit the LAB report", "  Submit   the lab\treport ",
                        "Submit the lab report!", "Submit, the lab report."):
            self.assertEqual(_entry(variant).fingerprint, base, variant)
        self.assertNotEqual(_entry("Submit the lab reports").fingerprint, base)


class LinkTests(unittest.TestCase):
    def setUp(self):
        self.db = Database(":memory:")
        self.db.connect()
        self.addCleanup(self.db.disconnect)
        self.session_id = self.db.create_session("S", datetime.now())

    def test_upsert_and_lookup(self):
        self.assertIsNone(self.db.get_calendar_link(self.session_id, "fp"))
        self.db.save_calendar_link(self.session_id, "fp", "ev1", "https://a", "2026-09-24")
        self.db.save_calendar_link(self.session_id, "fp", "ev2", "https://b", "2026-09-25")
        link = self.db.get_calendar_link(self.session_id, "fp")
        self.assertEqual((link["google_event_id"], link["html_link"], link["event_date"]),
                         ("ev2", "https://b", "2026-09-25"))
        count = self.db.connection.execute("SELECT COUNT(*) FROM calendar_events").fetchone()[0]
        self.assertEqual(count, 1)
        self.db.delete_calendar_link(self.session_id, "fp")
        self.assertIsNone(self.db.get_calendar_link(self.session_id, "fp"))

    def test_survives_resummarize(self):
        self.db.save_calendar_link(self.session_id, "fp", "ev1")
        summary_id = self.db.add_summary(self.session_id, "full", "x", "m")
        self.db.delete_summary(summary_id)
        self.db.add_summary(self.session_id, "full", "y", "m")
        self.assertIsNotNone(self.db.get_calendar_link(self.session_id, "fp"))

    def test_session_delete_cascades(self):
        other = self.db.create_session("T", datetime.now())
        self.db.save_calendar_link(self.session_id, "fp", "ev1")
        self.db.save_calendar_link(other, "fp", "ev2")
        self.db.purge_session(self.session_id)
        self.assertIsNone(self.db.get_calendar_link(self.session_id, "fp"))
        self.assertIsNotNone(self.db.get_calendar_link(other, "fp"))
        self.db.delete_session(other)
        self.assertIsNone(self.db.get_calendar_link(other, "fp"))


if __name__ == "__main__":
    unittest.main()
