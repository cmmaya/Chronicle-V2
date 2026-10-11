"""Send a Due Dates entry to Google Calendar (BU133).

:class:`CalendarEventDialog` is the pop-up where the user checks and edits
the event before it is created. :class:`DueDateSender` connects the summary
window's :class:`PixelDueDateCard` buttons to it: it opens one dialog at a
time and, before sending an entry again, checks whether the earlier event
still exists.

Every Calendar API call runs on a :class:`CalendarCallThread`.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, time
from functools import partial
from typing import Optional

from PySide6.QtCore import QDate, QEvent, QObject, QThread, QTime, Qt, Signal
from PySide6.QtGui import QPainter, QPen
from PySide6.QtWidgets import (
    QDateEdit, QDialog, QHBoxLayout, QLineEdit, QMessageBox, QTextEdit, QTimeEdit,
    QVBoxLayout, QWidget,
)

from ..calendar_sync import events, google_auth
from ..calendar_sync.events import CalendarError, EventDraft
from ..calendar_sync.google_auth import GoogleAuthError, GoogleAuthExpired
from . import theme
from .pixel_widgets import NAVY, PANEL_BORDER_INNER, _label_qss, set_accent
from .settings_dialog import (
    CREAM, GOLD, MUTED, PixelToggle, _centered_button, _label, _Scrim, _SettingCard,
)

logger = logging.getLogger(__name__)

ERROR = theme.hex("#FF8A7A")
EXPIRED_MESSAGE = "Your Google connection expired. Reconnect."

# QDateEdit cannot be empty: its minimum date stands for "no date" and shows
# the special value text instead.
NO_DATE = QDate(2000, 1, 1)

_EDIT_QSS = """
QDateEdit, QTimeEdit {
    background: #F6E0A6; color: #071846; border: 2px solid #254D9C;
    padding: 5px 8px; min-height: 26px;
    font-family: 'Courier New'; font-size: 11pt; font-weight: 700;
}
QDateEdit:focus, QTimeEdit:focus { border: 2px solid #FFF0BF; }
QDateEdit:disabled, QTimeEdit:disabled { color: #8090B8; }
"""


class CalendarCallThread(QThread):
    """Runs one Calendar call off the UI thread; emits ``(result, error)``."""

    done_signal = Signal(object, object)

    def __init__(self, call):
        super().__init__()
        self._call = call

    def run(self):
        try:
            result, error = self._call(), None
        except Exception as e:  # noqa: BLE001 - handed to the UI to explain
            result, error = None, e
        self.done_signal.emit(result, error)


_running_threads = set()  # keeps each thread alive until it finishes


def run_calendar_call(call, on_done) -> CalendarCallThread:
    thread = CalendarCallThread(call)
    _running_threads.add(thread)
    thread.done_signal.connect(on_done)
    thread.finished.connect(lambda: _running_threads.discard(thread))
    thread.finished.connect(thread.deleteLater)
    thread.start()
    return thread


def is_google_connected() -> bool:
    try:
        return bool(google_auth.connection_state()[0])
    except Exception:  # noqa: BLE001 - unreadable prefs/keyring reads as "not connected"
        return False


def description_with_footer(entry, session_name: str, recorded_on: Optional[date]) -> str:
    """The entry's description plus a line saying where it came from."""
    footer = f'From Chronicle session "{session_name}"'
    if recorded_on is not None:
        footer += f" ({recorded_on.isoformat()})"
    if entry.due_text:
        footer += f" — due: {entry.due_text}"
    description = (entry.description or "").strip()
    return f"{description}\n\n{footer}" if description else footer


def _problem_field(message: str) -> str:
    """Which field an ``EventDraft.validate()`` message belongs under."""
    lowered = message.lower()
    for field in ("title", "description", "time", "date"):
        if field in lowered:
            return field
    return "date"


class CalendarEventDialog(QDialog):
    """Edit one Due Dates entry, then create it as a Google Calendar event.

    ``window`` is the ``MainWindow`` (for ``open_settings``); ``db`` stores
    the link once the event exists. ``created`` carries the saved link row.
    """

    created = Signal(object)

    def __init__(self, entry, session_id: int, session_name: str,
                 recorded_on: Optional[date], db, window, parent=None):
        super().__init__(parent)
        self.entry = entry
        self._session_id = session_id
        self._db = db
        self._window = window
        self._scrim = None
        self._creating = False
        self._needs_reconnect = False
        self._end_touched = entry.end_time is not None
        self.setObjectName("CalendarEventDialog")
        self.setWindowTitle("Send to Calendar")
        self.setWindowModality(Qt.WindowModal)
        self.resize(600, 640)
        self.setMinimumSize(520, 560)
        self.setStyleSheet(_EDIT_QSS)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 14, 18, 16)
        root.setSpacing(10)
        root.addWidget(_label("Send to Calendar", CREAM, 17, bold=True))
        root.addWidget(_label(f"From “{session_name}”", MUTED, 9.5, wrap=True))

        # Title.
        self.title_edit = QLineEdit(entry.title)
        self.title_edit.setMaxLength(events.MAX_TITLE_CHARS + 50)
        card = _SettingCard("Title")
        card.body.addWidget(self.title_edit)
        self.title_error = self._error_label(card)
        root.addWidget(card)

        # Description.
        self.description_edit = QTextEdit()
        self.description_edit.setAcceptRichText(False)
        self.description_edit.setPlainText(
            description_with_footer(entry, session_name, recorded_on))
        self.description_edit.setMinimumHeight(110)
        card = _SettingCard("Description")
        card.body.addWidget(self.description_edit)
        self.description_error = self._error_label(card)
        root.addWidget(card, 1)

        # When.
        self.date_edit = QDateEdit()
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setDisplayFormat("yyyy-MM-dd")
        self.date_edit.setMinimumDate(NO_DATE)
        self.date_edit.setSpecialValueText("Pick a date")
        self.date_edit.setDate(QDate(entry.date) if entry.date else NO_DATE)
        self.date_edit.setMinimumWidth(170)
        # An empty date would open the popup on January 2000.
        self.date_edit.calendarWidget().installEventFilter(self)

        self.all_day_toggle = PixelToggle(entry.start_time is None)
        start = entry.start_time or time(9, 0)
        self.start_edit = QTimeEdit(QTime(start.hour, start.minute))
        end = entry.end_time or self._default_end(start)
        self.end_edit = QTimeEdit(QTime(end.hour, end.minute))
        for edit in (self.start_edit, self.end_edit):
            edit.setDisplayFormat("HH:mm")
            edit.setMinimumWidth(96)

        card = _SettingCard("When", "No time means an all-day event.")
        date_row = QHBoxLayout()
        date_row.setSpacing(10)
        date_row.addWidget(_label("Date", MUTED, 10))
        date_row.addWidget(self.date_edit)
        date_row.addSpacing(12)
        date_row.addWidget(_label("All day", MUTED, 10))
        date_row.addWidget(self.all_day_toggle)
        date_row.addStretch(1)
        card.body.addLayout(date_row)
        self.date_note = _label("", GOLD, 9.5, wrap=True)
        card.body.addWidget(self.date_note)
        self.time_row = QWidget()
        time_layout = QHBoxLayout(self.time_row)
        time_layout.setContentsMargins(0, 0, 0, 0)
        time_layout.setSpacing(10)
        time_layout.addWidget(_label("Start", MUTED, 10))
        time_layout.addWidget(self.start_edit)
        time_layout.addSpacing(12)
        time_layout.addWidget(_label("End", MUTED, 10))
        time_layout.addWidget(self.end_edit)
        time_layout.addStretch(1)
        card.body.addWidget(self.time_row)
        self.time_error = self._error_label(card)
        root.addWidget(card)

        # Result of the last attempt, then the buttons.
        self.message_label = _label("", ERROR, 10, wrap=True)
        self.message_label.hide()
        root.addWidget(self.message_label)
        buttons = QHBoxLayout()
        buttons.setSpacing(10)
        buttons.addStretch(1)
        self.discard_button = _centered_button("Discard", 120)
        self.primary_button = _centered_button("Create Event", 190)
        if not theme.is_pixel():
            set_accent(self.primary_button, "primary")  # BU159: white pill
        for button in (self.discard_button, self.primary_button):
            button.setAutoDefault(False)
            button.setDefault(False)
        buttons.addWidget(self.discard_button)
        buttons.addWidget(self.primary_button)
        root.addLayout(buttons)

        self._initial = self._snapshot()

        self.title_edit.textChanged.connect(self._refresh)
        self.description_edit.textChanged.connect(self._refresh)
        self.date_edit.dateChanged.connect(self._refresh)
        self.all_day_toggle.toggled.connect(self._refresh)
        self.start_edit.timeChanged.connect(self._on_start_changed)
        self.end_edit.timeChanged.connect(self._on_end_changed)
        self.discard_button.clicked.connect(self.reject)
        self.primary_button.clicked.connect(self._on_primary)
        self._refresh()

    # -- form -----------------------------------------------------------------

    @staticmethod
    def _error_label(card):
        label = _label("", ERROR, 9.5, wrap=True)
        label.hide()
        card.body.addWidget(label)
        return label

    @staticmethod
    def _default_end(start: time) -> time:
        """Start + 1 h, kept on the same day."""
        end = datetime.combine(date.today(), start) + events.DEFAULT_DURATION
        return end.time() if end.date() == date.today() else time(23, 59)

    def selected_date(self) -> Optional[date]:
        value = self.date_edit.date()
        return None if value == NO_DATE else value.toPython()

    def draft(self) -> EventDraft:
        timed = not self.all_day_toggle.isChecked()
        return EventDraft(
            title=self.title_edit.text().strip(),
            description=self.description_edit.toPlainText().strip(),
            date=self.selected_date(),
            start_time=self.start_edit.time().toPython() if timed else None,
            end_time=self.end_edit.time().toPython() if timed else None,
        )

    def _snapshot(self):
        return (self.title_edit.text(), self.description_edit.toPlainText(),
                self.date_edit.date(), self.all_day_toggle.isChecked(),
                self.start_edit.time(), self.end_edit.time())

    def is_dirty(self) -> bool:
        return self._snapshot() != self._initial

    def _on_start_changed(self, value: QTime):
        if not self._end_touched:
            end = self._default_end(value.toPython())
            self.end_edit.blockSignals(True)
            self.end_edit.setTime(QTime(end.hour, end.minute))
            self.end_edit.blockSignals(False)
        self._refresh()

    def _on_end_changed(self, _value):
        self._end_touched = True
        self._refresh()

    def _mode(self) -> str:
        if self._creating:
            return "creating"
        if self._needs_reconnect or not is_google_connected():
            return "connect"
        return "create"

    def _refresh(self, *_args):
        draft = self.draft()
        problems = {}
        for message in draft.validate():
            problems.setdefault(_problem_field(message), message)

        for field, label in (("title", self.title_error),
                             ("description", self.description_error),
                             ("time", self.time_error)):
            label.setText(problems.get(field, ""))
            label.setVisible(field in problems)

        self.time_row.setVisible(not self.all_day_toggle.isChecked())
        if draft.date is None and self.entry.date is None:
            due = self.entry.due_text.strip()
            note = (f"The transcript didn't give a single date: ‘{due}’. Pick one."
                    if due else "The transcript didn't give a single date. Pick one.")
            color = GOLD
        elif draft.date is None:
            note, color = problems.get("date", "Choose a date."), ERROR
        elif draft.date < date.today():
            note, color = "This date is in the past.", GOLD
        else:
            note, color = "", GOLD
        self.date_note.setText(note)
        self.date_note.setStyleSheet(_label_qss(color, 9.5))
        self.date_note.setVisible(bool(note))

        mode = self._mode()
        editable = mode != "creating"
        for widget in (self.title_edit, self.description_edit, self.date_edit,
                       self.all_day_toggle, self.start_edit, self.end_edit,
                       self.discard_button):
            widget.setEnabled(editable)
        if mode == "creating":
            self.primary_button.setText("Creating…")
            self.primary_button.setEnabled(False)
        elif mode == "connect":
            self.primary_button.setText("Connect Google…")
            self.primary_button.setEnabled(True)
        else:
            self.primary_button.setText("Create Event")
            self.primary_button.setEnabled(not problems)

    def _show_message(self, text: str):
        self.message_label.setText(text)
        self.message_label.setVisible(bool(text))

    # -- actions --------------------------------------------------------------

    def _on_primary(self):
        mode = self._mode()
        if mode == "connect":
            self._open_google_settings()
        elif mode == "create":
            self._create()

    def _open_google_settings(self):
        self._window.open_settings("Calendar")
        settings = getattr(self._window, "_settings_dialog", None)
        if settings is not None:
            settings.finished.connect(self._on_settings_closed)

    def _on_settings_closed(self, _result=None):
        if is_google_connected():
            self._needs_reconnect = False
            self._show_message("")
        self._refresh()

    def _create(self):
        draft = self.draft()
        if draft.validate():
            return
        fingerprint = self.entry.fingerprint
        self._creating = True
        self._show_message("")
        self._refresh()
        run_calendar_call(
            lambda: events.create_event(draft, self._session_id, fingerprint),
            partial(self._on_create_done, draft, fingerprint),
        )

    def _on_create_done(self, draft, fingerprint, result, error):
        self._creating = False
        if error is None:
            link = {"session_id": self._session_id, "fingerprint": fingerprint,
                    "google_event_id": result.id, "html_link": result.html_link,
                    "event_date": draft.date.isoformat()}
            try:
                self._db.save_calendar_link(self._session_id, fingerprint, result.id,
                                            result.html_link, draft.date.isoformat())
            except Exception as e:  # noqa: BLE001 - the event exists; only the badge is lost
                logger.warning("Could not save the calendar link: %s", e)
            self.created.emit(link)
            self.done(QDialog.Accepted)
            return
        if isinstance(error, GoogleAuthExpired):
            self._needs_reconnect = True
            self._show_message(EXPIRED_MESSAGE)
        elif isinstance(error, (CalendarError, GoogleAuthError)):
            self._show_message(str(error))
        else:
            logger.warning("Creating the calendar event failed: %s", type(error).__name__)
            self._show_message(f"Something went wrong ({type(error).__name__}). Try again.")
        self._refresh()

    def reject(self):
        """Discard, Esc and the close button: nothing is created."""
        if self._creating:
            return
        if self.is_dirty():
            reply = QMessageBox.question(
                self, "Discard changes", "Discard your changes?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if reply != QMessageBox.Yes:
                return
        super().reject()

    # -- window ---------------------------------------------------------------

    def eventFilter(self, obj, event):
        if (event.type() == QEvent.Show and obj is self.date_edit.calendarWidget()
                and self.selected_date() is None):
            today = QDate.currentDate()
            obj.setCurrentPage(today.year(), today.month())
        return super().eventFilter(obj, event)

    def changeEvent(self, event):
        # Back from the browser / Settings: the connection may have changed.
        if event.type() == QEvent.ActivationChange and self.isActiveWindow():
            self._refresh()
        super().changeEvent(event)

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), NAVY)
        p.setPen(QPen(PANEL_BORDER_INNER, theme.pen_width(2)))
        p.setBrush(Qt.NoBrush)
        p.drawRect(self.rect().adjusted(1, 1, -1, -1))
        p.end()

    def showEvent(self, event):
        super().showEvent(event)
        parent = self.parentWidget()
        if parent is not None and self._scrim is None:
            self._scrim = _Scrim(parent)
            self._scrim.show()
            self._scrim.raise_()

    def done(self, result):
        if self._scrim is not None:
            self._scrim.deleteLater()
            self._scrim = None
        super().done(result)


class DueDateSender(QObject):
    """Wires a summary window's due-date cards to :class:`CalendarEventDialog`.

    ``event_created`` carries the saved link after an event was created.
    """

    event_created = Signal(object)

    _active_dialog: Optional[CalendarEventDialog] = None  # one open at a time

    def __init__(self, window, db, session_id: int, session_name: str,
                 recorded_on: Optional[date], parent: QWidget):
        super().__init__(parent)
        self._window = window
        self._db = db
        self._session_id = session_id
        self._session_name = session_name
        self._recorded_on = recorded_on
        self._parent_widget = parent

    def attach(self, card):
        card.send_requested.connect(partial(self.send, card))
        card.resend_requested.connect(partial(self.resend, card))

    def send(self, card):
        self.open_dialog(card)

    def resend(self, card):
        """Send an entry that has a link: confirm first if its event still exists."""
        if self._raise_active_dialog():
            return
        event_id = (card.link or {}).get("google_event_id")
        if not event_id or not is_google_connected():
            self._on_checked(card, True, None)
            return
        card.set_busy("Checking…")
        run_calendar_call(lambda: events.event_exists(event_id),
                          partial(self._on_checked, card))

    def _on_checked(self, card, exists, error):
        card.set_busy(None)
        if error is not None:
            logger.info("Could not check the calendar event: %s", type(error).__name__)
            question = ("Chronicle couldn't check whether this due date is still in "
                        "your calendar. Create another event?")
        elif exists:
            question = "This due date is already in your calendar. Create another event?"
        else:
            question = None
        if question is not None:
            reply = QMessageBox.question(
                self._parent_widget, "Send again", question,
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if reply != QMessageBox.Yes:
                return
        else:
            # Deleted in Google: the stored link is stale.
            try:
                self._db.delete_calendar_link(self._session_id, card.entry.fingerprint)
            except Exception as e:  # noqa: BLE001
                logger.warning("Could not remove the stale calendar link: %s", e)
            card.set_link(None)
        self.open_dialog(card)

    def _raise_active_dialog(self) -> bool:
        dialog = DueDateSender._active_dialog
        if dialog is None:
            return False
        try:
            dialog.raise_()
            dialog.activateWindow()
            return True
        except RuntimeError:  # deleted without finishing
            DueDateSender._active_dialog = None
            return False

    def open_dialog(self, card):
        if self._raise_active_dialog():
            return None
        dialog = CalendarEventDialog(
            card.entry, self._session_id, self._session_name, self._recorded_on,
            self._db, self._window, self._parent_widget)
        dialog.setAttribute(Qt.WA_DeleteOnClose, True)
        dialog.created.connect(partial(self._on_created, card))
        dialog.finished.connect(self._on_dialog_finished)
        DueDateSender._active_dialog = dialog
        dialog.open()
        return dialog

    @staticmethod
    def _on_dialog_finished(_result=None):
        DueDateSender._active_dialog = None

    def _on_created(self, card, link):
        card.set_link(link)
        self.event_created.emit(link)
