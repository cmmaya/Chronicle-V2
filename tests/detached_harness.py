"""Shared test harness for the detached transcripts window (BU111-BU117).

``MainWindow.__init__`` builds the whole application - menus, panels, a session
manager and a database connection - so the tests cannot construct one. They
also should not re-declare its methods one at a time: every BU that adds a
dependency between two detached methods would break every harness that copied
only one of them.

``DetachedHarness`` instead *subclasses* ``MainWindow`` and skips only its
``__init__``, seeding by hand the handful of attributes the detached window
code reads. Every method under test is therefore the real one, reached through
the real class.
"""
import os
from datetime import datetime, timedelta

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMainWindow

from src.config import LIVE_QA
from src.app.window import (LIVE_QA_DEFAULT_ANSWER_MODE, LIVE_QA_DEFAULT_MODE,
                            MainWindow, TranscriptRecord)

T0 = datetime(2026, 9, 16, 10, 0, 0)


def app() -> QApplication:
    return QApplication.instance() or QApplication([])


class DetachedHarness(MainWindow):
    """A MainWindow whose detached-window code is live and whose app is not."""

    def __init__(self, prefs_path=None):
        QMainWindow.__init__(self)  # deliberately not MainWindow.__init__

        self.session_manager = None
        self.assistant_service = None
        self._selected_session_id = None
        self._prefs_path = prefs_path

        self._transcript_records = []
        self._transcript_groups = {}
        self._detached_transcript_groups = {}
        self._detached_last_end = None
        self._detached_window = None
        self._detached_selected_chunks = []
        self._detached_selection_anchor = None
        self._detached_drag_active = False
        self._detached_drag_modifiers = Qt.NoModifier
        self._detached_drag_base = []
        self._detached_answer_threads = {}
        self._detached_answer_cards = {}
        self._detached_answer_seq = 0
        self._detached_answers_empty = None
        self._detached_answers_layout = None

        # BU115 auto-mode state, mirroring MainWindow's own defaults.
        self._question_detector = None
        self._detached_candidate_cards = {}
        self._live_qa_hidden = []
        self._detached_cap_notice = None
        self._live_qa_mode = LIVE_QA_DEFAULT_MODE
        self._live_qa_answer_mode = LIVE_QA_DEFAULT_ANSWER_MODE
        self._live_qa_directed_only = False
        self._live_qa_mode_chips = {}
        self._live_qa_answer_mode_chips = {}
        self._live_qa_window_chunks = LIVE_QA.get('window_chunks', 3)
        self._live_qa_detector_model = LIVE_QA.get('detector_model')
        self._live_qa_spend_chip = None

        # BU151 documents chip in the header, built with the answers column.
        self._detached_documents_chip = None
        self._displaying_inserted_transcript = False
        self._live_qa_answer_model = LIVE_QA.get('answer_model')

        self.status_messages = []

    # --- stubs for the application surface the detached code touches ----

    def _make_icon(self, filename: str) -> QIcon:
        return QIcon()

    def _on_status_update(self, message: str, is_error: bool = False):
        self.status_messages.append((message, is_error))

    def _get_preferences_path(self):
        return self._prefs_path

    def _current_display_session_id(self):
        return self._selected_session_id

    # --- column construction, kept referenced so Qt does not delete it ---

    def build_transcript_column(self):
        self._transcript_column = self._build_detached_transcript_column()
        return self._transcript_column

    def build_answers_column(self):
        self._answers_column = self._build_detached_answers_column()
        return self._answers_column

    # --- feeding the stream ----------------------------------------------

    def add(self, text, source, offset, duration=5, transcript_id=None):
        start = T0 + timedelta(seconds=offset)
        end = start + timedelta(seconds=duration)
        label = "Mic" if source == "mic" else "System"
        record = TranscriptRecord(
            text=text,
            source=source,
            start_dt=start,
            end_dt=end,
            transcript_id=transcript_id,
            display_text=f"[{start.strftime('%H:%M:%S')}] {label}: {text}",
        )
        self._transcript_records.append(record)
        self._add_transcription_to_detached(record, len(self._transcript_records) - 1)
        return record

    # --- inspection --------------------------------------------------------

    def click_chunk(self, record_index, modifiers=Qt.NoModifier):
        """Select as a plain click on ``record_index`` would."""
        from src.app.window import resolve_chunk_selection
        selection, anchor = resolve_chunk_selection(
            self._visible_chunk_order(),
            self._detached_selected_chunks,
            self._detached_selection_anchor,
            record_index,
            modifiers,
        )
        self._set_detached_selection(selection, anchor=anchor)
        self._detached_drag_base = list(selection)
        self._detached_drag_modifiers = modifiers
        self._detached_drag_active = True
        return selection

    def drag_to_chunk(self, record_index):
        """Extend a running drag onto ``record_index``."""
        from src.app.window import resolve_chunk_selection
        selection, anchor = resolve_chunk_selection(
            self._visible_chunk_order(),
            self._detached_drag_base,
            self._detached_selection_anchor,
            record_index,
            self._detached_drag_modifiers,
            extending=True,
        )
        self._set_detached_selection(selection, anchor=anchor)
        return selection

    def rows(self):
        return [
            self._detached_layout.itemAt(i).widget()
            for i in range(self._detached_layout.count() - 1)
            if self._detached_layout.itemAt(i).widget() is not None
        ]

    def separators(self):
        return [r for r in self.rows() if r.property("record_indices") is None]

    def bubbles(self):
        return [r for r in self.rows() if r.property("record_indices") is not None]
