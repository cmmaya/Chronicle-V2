"""BU133 - Send to Calendar button and event dialog (event service mocked)."""
import os
import unittest
from datetime import date, datetime, time, timedelta
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QDate, QTime
from PySide6.QtWidgets import QApplication, QMessageBox, QWidget

from src.app import calendar_dialog as cd
from src.app.pixel_widgets import PixelDueDateCard, PixelDueDateList
from src.app.window import build_summary_section
from src.calendar_sync import events
from src.calendar_sync import google_auth as ga
from src.calendar_sync.due_dates import DueDateEntry
from src.storage.database import Database

_app = QApplication.instance() or QApplication([])

DUE_BODY = """Title: Submit the lab report
Due date: next Friday (2026-09-25)
Calendar date: 2026-09-25
Description: Upload it to the portal.

Title: Team sync
Due date: Thursday or Friday
Calendar date: none
Description: Pick a slot."""


def _entry(title="Lab report", day=date(2026, 9, 25), start=None, end=None,
           due_text="next Friday", description="Upload it."):
    return DueDateEntry(title=title, due_text=due_text, description=description,
                        date=day, start_time=start, end_time=end, index=0)


def _wait(condition, timeout=5.0):
    deadline = datetime.now() + timedelta(seconds=timeout)
    while datetime.now() < deadline:
        _app.processEvents()
        if condition():
            return True
    return False


class FakeWindow:
    def __init__(self):
        self.open_settings = mock.Mock()
        self._settings_dialog = None


class _Base(unittest.TestCase):
    connected = True

    def setUp(self):
        state = (self.connected, "me@example.com", ga.SOURCE_CHRONICLE)
        self._patch = mock.patch.object(ga, "connection_state", lambda: state)
        self._patch.start()
        self.addCleanup(self._patch.stop)
        self.db = Database(":memory:")
        self.db.connect()
        self.addCleanup(self.db.disconnect)
        self.session_id = self.db.create_session("Physics lab", datetime(2026, 9, 18, 10))
        self.window = FakeWindow()
        self.parent = QWidget()
        self.addCleanup(self.parent.deleteLater)
        cd.DueDateSender._active_dialog = None

    def dialog(self, entry):
        dialog = cd.CalendarEventDialog(entry, self.session_id, "Physics lab",
                                        date(2026, 9, 18), self.db, self.window, self.parent)
        self.addCleanup(dialog.deleteLater)
        return dialog

    def sender(self):
        return cd.DueDateSender(self.window, self.db, self.session_id, "Physics lab",
                                date(2026, 9, 18), self.parent)


class SectionTests(_Base):
    def test_due_dates_get_one_card_per_entry(self):
        section, cards = build_summary_section("Due Dates", DUE_BODY)
        self.assertEqual(len(cards), 2)
        self.assertIsInstance(section.body_widget, PixelDueDateList)
        self.assertEqual(section.header.count.text(), "2")
        self.assertEqual([c.entry.title for c in cards], ["Submit the lab report", "Team sync"])
        self.assertTrue(all(c.button.text() == PixelDueDateCard.SEND_TEXT for c in cards))

    def test_other_sections_unchanged(self):
        section, cards = build_summary_section("Key Points", "1. One\n2. Two")
        self.assertEqual(cards, [])
        self.assertIsNone(section.body_widget)
        self.assertIn("One", section.body_label.text())

    def test_no_entries_keeps_text_body(self):
        for body in ("No due dates were mentioned in the transcript.", "Some prose."):
            section, cards = build_summary_section("Due Dates", body)
            self.assertEqual(cards, [])
            self.assertIsNone(section.body_widget)

    def test_stored_link_shows_in_calendar(self):
        link = {"google_event_id": "ev1", "html_link": "https://cal/ev1"}
        _, cards = build_summary_section(
            "Due Dates", DUE_BODY,
            lambda fp: link if fp == _entry("Team sync").fingerprint else None)
        self.assertFalse(cards[0].is_sent())
        self.assertTrue(cards[1].is_sent())
        self.assertEqual(cards[1].button.text(), PixelDueDateCard.SENT_TEXT)
        actions = [a.text() for a in cards[1].sent_menu().actions()]
        self.assertEqual(actions, ["Open in Google Calendar", "Send again…"])


class PrefillTests(_Base):
    def test_all_day_entry(self):
        d = self.dialog(_entry())
        self.assertEqual(d.title_edit.text(), "Lab report")
        self.assertEqual(d.date_edit.date(), QDate(2026, 9, 25))
        self.assertTrue(d.all_day_toggle.isChecked())
        self.assertTrue(d.time_row.isHidden())
        self.assertEqual(
            d.description_edit.toPlainText(),
            'Upload it.\n\nFrom Chronicle session "Physics lab" (2026-09-18) — due: next Friday')
        draft = d.draft()
        self.assertIsNone(draft.start_time)
        self.assertEqual(d.primary_button.text(), "Create Event")

    def test_timed_entry(self):
        d = self.dialog(_entry(start=time(15), end=time(15, 30)))
        self.assertFalse(d.all_day_toggle.isChecked())
        self.assertFalse(d.time_row.isHidden())
        self.assertEqual(d.start_edit.time(), QTime(15, 0))
        self.assertEqual(d.end_edit.time(), QTime(15, 30))
        self.assertEqual((d.draft().start_time, d.draft().end_time), (time(15), time(15, 30)))

    def test_missing_date_shows_hint_and_blocks_create(self):
        d = self.dialog(_entry(day=None, due_text="Thursday or Friday"))
        self.assertIsNone(d.selected_date())
        self.assertFalse(d.primary_button.isEnabled())
        self.assertFalse(d.date_note.isHidden())
        self.assertIn("Thursday or Friday", d.date_note.text())
        self.assertIn("Pick one", d.date_note.text())
        d.date_edit.setDate(QDate(2026, 9, 25))
        self.assertTrue(d.primary_button.isEnabled())

    def test_past_date_warns_without_blocking(self):
        d = self.dialog(_entry(day=date.today() - timedelta(days=3)))
        self.assertIn("in the past", d.date_note.text())
        self.assertTrue(d.primary_button.isEnabled())

    def test_end_defaults_to_start_plus_hour_and_order_is_checked(self):
        d = self.dialog(_entry())
        d.all_day_toggle.toggle()
        self.assertFalse(d.time_row.isHidden())
        self.assertEqual(d.start_edit.time(), QTime(9, 0))
        self.assertEqual(d.end_edit.time(), QTime(10, 0))
        d.start_edit.setTime(QTime(14, 30))
        self.assertEqual(d.end_edit.time(), QTime(15, 30))
        self.assertTrue(d.primary_button.isEnabled())
        d.end_edit.setTime(QTime(14, 0))
        self.assertFalse(d.primary_button.isEnabled())
        self.assertFalse(d.time_error.isHidden())


class CreateTests(_Base):
    def test_success_saves_link_and_flips_card(self):
        entry = _entry()
        card = PixelDueDateCard(entry)
        sender = self.sender()
        sender.attach(card)
        created = []
        sender.event_created.connect(created.append)
        with mock.patch.object(events, "create_event",
                               return_value=events.CreatedEvent("ev1", "https://cal/ev1")) as create:
            card.send_requested.emit()
            dialog = cd.DueDateSender._active_dialog
            self.assertIsNotNone(dialog)
            dialog.title_edit.setText("Lab report (final)")
            dialog.primary_button.click()
            self.assertEqual(dialog.primary_button.text(), "Creating…")
            self.assertTrue(_wait(lambda: card.is_sent()))
        draft, session_id, fingerprint = create.call_args[0]
        self.assertEqual(draft.title, "Lab report (final)")
        self.assertEqual((session_id, fingerprint), (self.session_id, entry.fingerprint))
        link = self.db.get_calendar_link(self.session_id, entry.fingerprint)
        self.assertEqual((link["google_event_id"], link["html_link"], link["event_date"]),
                         ("ev1", "https://cal/ev1", "2026-09-25"))
        self.assertEqual(card.button.text(), PixelDueDateCard.SENT_TEXT)
        self.assertEqual(created[0]["html_link"], "https://cal/ev1")
        self.assertIsNone(cd.DueDateSender._active_dialog)

    def _fail_with(self, error):
        d = self.dialog(_entry())
        d.show()
        d.title_edit.setText("Edited title")
        with mock.patch.object(events, "create_event", side_effect=error):
            d.primary_button.click()
            self.assertTrue(_wait(lambda: not d._creating))
        return d

    def test_expired_connection_offers_reconnect(self):
        d = self._fail_with(ga.GoogleAuthExpired("Reconnect your Google account."))
        self.assertTrue(d.isVisible())
        self.assertEqual(d.message_label.text(), cd.EXPIRED_MESSAGE)
        self.assertEqual(d.primary_button.text(), "Connect Google…")
        d.primary_button.click()
        self.window.open_settings.assert_called_once_with("Calendar")

    def test_calendar_error_keeps_dialog_and_edits(self):
        d = self._fail_with(events.CalendarError("Could not reach Google Calendar."))
        self.assertTrue(d.isVisible())
        self.assertEqual(d.message_label.text(), "Could not reach Google Calendar.")
        self.assertEqual(d.title_edit.text(), "Edited title")
        self.assertTrue(d.title_edit.isEnabled())
        self.assertEqual(d.primary_button.text(), "Create Event")
        self.assertIsNone(self.db.get_calendar_link(self.session_id, _entry().fingerprint))


class ResendTests(_Base):
    def _sent_card(self):
        entry = _entry()
        self.db.save_calendar_link(self.session_id, entry.fingerprint, "old", "https://cal/old")
        card = PixelDueDateCard(entry, self.db.get_calendar_link(self.session_id, entry.fingerprint))
        sender = self.sender()
        sender.attach(card)
        return card

    def test_existing_event_asks_first(self):
        card = self._sent_card()
        with mock.patch.object(events, "event_exists", return_value=True) as exists, \
                mock.patch.object(QMessageBox, "question", return_value=QMessageBox.No) as ask:
            card.resend_requested.emit()
            self.assertTrue(_wait(lambda: ask.called))
        exists.assert_called_once_with("old")
        self.assertIn("already in your calendar", ask.call_args[0][2])
        self.assertIsNone(cd.DueDateSender._active_dialog)
        self.assertTrue(card.is_sent())

    def test_deleted_event_skips_confirmation_and_drops_link(self):
        card = self._sent_card()
        with mock.patch.object(events, "event_exists", return_value=False), \
                mock.patch.object(QMessageBox, "question") as ask:
            card.resend_requested.emit()
            self.assertTrue(_wait(lambda: cd.DueDateSender._active_dialog is not None))
        ask.assert_not_called()
        self.assertFalse(card.is_sent())
        self.assertIsNone(self.db.get_calendar_link(self.session_id, card.entry.fingerprint))
        with mock.patch.object(events, "create_event",
                               return_value=events.CreatedEvent("new", "https://cal/new")):
            cd.DueDateSender._active_dialog.primary_button.click()
            self.assertTrue(_wait(lambda: card.is_sent()))
        self.assertEqual(
            self.db.get_calendar_link(self.session_id, card.entry.fingerprint)["google_event_id"],
            "new")

    def test_one_dialog_at_a_time(self):
        sender = self.sender()
        first = sender.open_dialog(PixelDueDateCard(_entry("A")))
        self.assertIsNotNone(first)
        self.assertIsNone(sender.open_dialog(PixelDueDateCard(_entry("B"))))
        self.assertIs(cd.DueDateSender._active_dialog, first)
        first.done(0)
        self.assertIsNone(cd.DueDateSender._active_dialog)


class NotConnectedTests(_Base):
    connected = False

    def test_primary_opens_settings_calendar(self):
        d = self.dialog(_entry())
        self.assertEqual(d.primary_button.text(), "Connect Google…")
        self.assertTrue(d.primary_button.isEnabled())
        with mock.patch.object(events, "create_event") as create:
            d.primary_button.click()
        create.assert_not_called()
        self.window.open_settings.assert_called_once_with("Calendar")

    def test_returning_connected_offers_create(self):
        d = self.dialog(_entry())
        with mock.patch.object(ga, "connection_state", lambda: (True, "me@example.com", "chronicle")):
            d._on_settings_closed()
            self.assertEqual(d.primary_button.text(), "Create Event")


class DiscardTests(_Base):
    def test_discard_without_edits_closes(self):
        d = self.dialog(_entry())
        d.show()
        with mock.patch.object(QMessageBox, "question") as ask:
            d.discard_button.click()
        ask.assert_not_called()
        self.assertFalse(d.isVisible())

    def test_discard_after_edits_asks(self):
        d = self.dialog(_entry())
        d.show()
        d.title_edit.setText("Changed")
        with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.No) as ask:
            d.discard_button.click()
        ask.assert_called_once()
        self.assertTrue(d.isVisible())
        with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.Yes):
            d.reject()
        self.assertFalse(d.isVisible())


if __name__ == "__main__":
    unittest.main()
