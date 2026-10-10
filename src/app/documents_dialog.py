"""The Documents pop-up of a session (BU148).

:class:`SessionDocumentsDialog` is where a user adds documents to a session
(drag and drop, or Import), sees what is stored, previews the text the
assistant reads, and removes documents. Extraction and the database write run
through ``run_job`` so a long PDF never freezes the window.

``run_job(fn, on_done)`` runs ``fn()`` off the UI thread and calls
``on_done(result, error)`` on the UI thread. ``MainWindow`` supplies one built
on ``SessionManager.submit_job``; tests pass an inline one.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Callable, List, Optional

from PySide6.QtCore import QStandardPaths, QTimer, Qt, Signal
from PySide6.QtGui import QPainter, QPen
from PySide6.QtWidgets import (
    QDialog, QFileDialog, QHBoxLayout, QPushButton, QTextEdit,
    QVBoxLayout, QWidget,
)

from ..assistant.session_documents import (
    DocumentError, extract_document, is_allowed_document,
)
from ..config import SESSION_DOCUMENTS
from .pixel_widgets import NAVY, PANEL_BORDER_INNER, PixelDropOverlay, _label_qss
from .settings_dialog import (
    CREAM, GOLD, MUTED, _centered_button, _label, _Scrim, _SettingCard,
)

logger = logging.getLogger(__name__)

ERROR = "#FF8A7A"
EMPTY_TEXT = ("No documents yet. Answers for this session use only its "
              "transcript, summary and screenshots.")
SCOPE_TEXT = ("Used for this session's Specific Session questions and the "
              "transcripts window. Not used in Any Session.")

# Remembered for the app session, like the upload dialogs.
_last_folder: Optional[str] = None

_ROW_QSS = (
    "QWidget#DocRow { background: #0B2260; border: 2px solid #254D9C; }"
    "QWidget#DocRow[selected=\"true\"] { background: #14337F; border: 2px solid #F6E0A6; }"
)
_REMOVE_QSS = (
    "QPushButton { color: #FF8A7A; background: transparent; border: 2px solid #254D9C;"
    " font-family: 'Courier New'; font-size: 11pt; font-weight: 700; padding: 0 6px; }"
    "QPushButton:hover { border: 2px solid #FF8A7A; }"
    "QPushButton:disabled { color: #4A5C8C; }"
)
_PREVIEW_QSS = (
    "QTextEdit { background: #F6E0A6; color: #071846; border: 2px solid #254D9C;"
    " font-family: 'Courier New'; font-size: 10pt; }"
)


def _size_label(size: Optional[int]) -> str:
    if not size:
        return ""
    if size >= 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MB"
    return f"{max(1, size // 1024)} KB"


def _date_label(epoch: Optional[int]) -> str:
    try:
        return datetime.fromtimestamp(epoch).strftime("%Y-%m-%d")
    except (OverflowError, OSError, TypeError, ValueError):
        return ""


class _DocRow(QWidget):
    """One document (or one being read) in the list."""

    selected = Signal(object)
    remove_requested = Signal(object)

    def __init__(self, key, name: str, meta: str, removable: bool):
        super().__init__()
        self.key = key  # the document id, or a token for a pending import
        self.setObjectName("DocRow")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(_ROW_QSS)
        self.setProperty("selected", False)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 6, 6, 6)
        layout.setSpacing(8)
        text = QVBoxLayout()
        text.setSpacing(2)
        self.name_label = _label(name, CREAM, 10.5, bold=True)
        self.meta_label = _label(meta, MUTED, 9)
        text.addWidget(self.name_label)
        text.addWidget(self.meta_label)
        layout.addLayout(text, 1)
        self.remove_button = QPushButton("✕")
        self.remove_button.setToolTip("Remove this document")
        self.remove_button.setFixedSize(30, 28)
        self.remove_button.setStyleSheet(_REMOVE_QSS)
        self.remove_button.clicked.connect(self._ask_confirm)
        # Parent it before touching its visibility: showing a parentless
        # widget opens it as its own top-level window, and once reparented its
        # click area no longer matches where the ✕ is drawn.
        layout.addWidget(self.remove_button)
        self.remove_button.setVisible(removable)

        # Confirmation sits in the row itself: a separate message box over this
        # window-modal pop-up never reliably answered, so the delete never ran.
        self.confirm_label = _label("Remove?", GOLD, 9.5, bold=True)
        self.confirm_yes = QPushButton("Yes")
        self.confirm_no = QPushButton("No")
        for button in (self.confirm_yes, self.confirm_no):
            button.setFixedSize(46, 28)
            button.setStyleSheet(_REMOVE_QSS)
            button.setAutoDefault(False)
        self.confirm_yes.clicked.connect(lambda: self.remove_requested.emit(self.key))
        self.confirm_no.clicked.connect(lambda: self._set_confirming(False))
        for widget in (self.confirm_label, self.confirm_yes, self.confirm_no):
            widget.hide()
            layout.addWidget(widget)
        self._removable = removable

    def _ask_confirm(self):
        self._set_confirming(True)

    def _set_confirming(self, value: bool):
        self.remove_button.setVisible(self._removable and not value)
        for widget in (self.confirm_label, self.confirm_yes, self.confirm_no):
            widget.setVisible(value)

    def set_selected(self, value: bool):
        self.setProperty("selected", bool(value))
        self.style().unpolish(self)
        self.style().polish(self)

    def mousePressEvent(self, event):
        self.selected.emit(self.key)
        super().mousePressEvent(event)


class SessionDocumentsDialog(QDialog):
    """Add, list, preview and remove one session's documents."""

    documents_changed = Signal(int)  # session_id

    REJECT_LINGER_MS = 1600

    def __init__(self, db, session_id: int, session_name: str,
                 run_job: Callable, parent=None):
        super().__init__(parent)
        self._db = db
        self._session_id = session_id
        self._run_job = run_job
        self._scrim = None
        self._rows: List[_DocRow] = []
        self._documents: List[dict] = []
        self._pending = {}  # token -> _DocRow of an import in flight
        self._next_token = 0
        self._selected = None
        self._drop_ready = False

        self.setObjectName("SessionDocumentsDialog")
        self.setWindowTitle(f"Documents — {session_name}")
        self.setWindowModality(Qt.WindowModal)
        self.setAcceptDrops(True)
        self.resize(640, 700)
        self.setMinimumSize(540, 560)
        self.setStyleSheet(_PREVIEW_QSS)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 14, 18, 16)
        root.setSpacing(10)
        root.addWidget(_label("Documents", CREAM, 17, bold=True))
        root.addWidget(_label(f"For “{session_name}”", MUTED, 9.5, wrap=True))

        # Drop area + Import.
        self.drop_card = _SettingCard("Drag files here or Import")
        types = ", ".join(e.lstrip(".") for e in SESSION_DOCUMENTS["allowed_extensions"])
        self.limits_label = _label(
            f"{types} · up to {SESSION_DOCUMENTS['max_per_session']} · "
            f"{_size_label(SESSION_DOCUMENTS['max_file_bytes'])} each", MUTED, 9.5)
        self.import_button = _centered_button("Import…", 130)
        self.import_button.setAutoDefault(False)
        row = QHBoxLayout()
        row.addWidget(self.limits_label, 1)
        row.addWidget(self.import_button)
        self.drop_card.body.addLayout(row)
        root.addWidget(self.drop_card)

        self.status_label = _label("", GOLD, 9.5, wrap=True)
        self.status_label.hide()
        root.addWidget(self.status_label)

        # List.
        self.empty_label = _label(EMPTY_TEXT, MUTED, 10, wrap=True)
        root.addWidget(self.empty_label)
        self.list_layout = QVBoxLayout()
        self.list_layout.setSpacing(6)
        root.addLayout(self.list_layout)

        # Preview.
        self.preview_title = _label("Preview — what the assistant reads", MUTED, 9.5)
        self.preview = QTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setAcceptRichText(False)
        self.preview.setMinimumHeight(140)
        root.addWidget(self.preview_title)
        root.addWidget(self.preview, 1)

        root.addWidget(_label(SCOPE_TEXT, MUTED, 9, wrap=True))
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.close_button = _centered_button("Close", 120)
        self.close_button.setAutoDefault(False)
        buttons.addWidget(self.close_button)
        root.addLayout(buttons)

        self._drop_overlay = PixelDropOverlay(self)
        self._drop_overlay.setGeometry(self.rect())
        self._reject_timer = QTimer(self)
        self._reject_timer.setSingleShot(True)
        self._reject_timer.timeout.connect(self._hide_drop_overlay)

        self.import_button.clicked.connect(self._pick_files)
        self.close_button.clicked.connect(self.accept)
        self.reload()

    # -- list -----------------------------------------------------------------

    def reload(self):
        """Redraw the list from the database; in-flight imports stay listed."""
        try:
            self._documents = self._db.list_session_documents(self._session_id)
        except Exception as e:  # noqa: BLE001 - shown, not raised into a slot
            logger.error("Could not list session documents: %s", e)
            self._documents = []
            self._set_status(f"Could not load the documents: {e}", error=True)
        pending = list(self._pending.items())
        for row in self._rows:
            self.list_layout.removeWidget(row)
            row.deleteLater()
        self._rows = []
        for doc in self._documents:
            meta = " · ".join(part for part in (
                doc["file_type"].upper(), _size_label(doc.get("file_bytes")),
                f"{doc['char_count']:,} chars", _date_label(doc.get("added_at"))) if part)
            self._add_row(_DocRow(doc["id"], doc["name"], meta, removable=True))
        for token, old in pending:
            row = _DocRow(token, old.name_label.text(), "Reading…", removable=False)
            self._pending[token] = row
            self._add_row(row)
        if self._selected not in {r.key for r in self._rows}:
            self._selected = None
        if self._selected is None and self._documents:
            self._selected = self._documents[0]["id"]
        self._refresh_state()

    def _add_row(self, row: _DocRow):
        row.selected.connect(self._select)
        row.remove_requested.connect(self._confirm_remove)
        self.list_layout.addWidget(row)
        self._rows.append(row)

    def _select(self, key):
        if isinstance(key, int) and key in {d["id"] for d in self._documents}:
            self._selected = key
            self._refresh_state()

    def _refresh_state(self):
        for row in self._rows:
            row.set_selected(row.key == self._selected)
        self.empty_label.setVisible(not self._rows)
        full = self._free_slots() <= 0
        self.import_button.setEnabled(not full)
        if full:
            self._set_status(
                f"This session already has {SESSION_DOCUMENTS['max_per_session']} "
                "documents. Remove one to add another.")
        if self._selected is None:
            self.preview.clear()
            self.preview_title.setVisible(False)
            self.preview.setVisible(False)
        else:
            self.preview_title.setVisible(True)
            self.preview.setVisible(True)
            self.preview.setPlainText(self._document_text(self._selected))

    def _document_text(self, document_id: int) -> str:
        try:
            for doc in self._db.get_session_documents(self._session_id):
                if doc["id"] == document_id:
                    return doc["text"]
        except Exception as e:  # noqa: BLE001
            logger.error("Could not read a session document: %s", e)
        return ""

    def _free_slots(self) -> int:
        return (SESSION_DOCUMENTS["max_per_session"]
                - len(self._documents) - len(self._pending))

    def _set_status(self, text: str, error: bool = False):
        self.status_label.setText(text)
        self.status_label.setStyleSheet(_label_qss(ERROR if error else GOLD, 9.5))
        self.status_label.setVisible(bool(text))

    # -- import ---------------------------------------------------------------

    def _pick_files(self):
        global _last_folder
        start = _last_folder or QStandardPaths.writableLocation(
            QStandardPaths.DocumentsLocation)
        patterns = " ".join(f"*{e}" for e in SESSION_DOCUMENTS["allowed_extensions"])
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Add documents", start, f"Documents ({patterns})")
        if paths:
            _last_folder = os.path.dirname(paths[0])
            self.import_paths(paths)

    def import_paths(self, paths):
        """Import files one by one; a bad file never stops the others."""
        self._set_status("")
        for path in paths:
            if self._free_slots() <= 0:
                self._set_status(
                    f"This session already has {SESSION_DOCUMENTS['max_per_session']} "
                    "documents. Remove one to add another.", error=True)
                break
            self._start_import(path)

    def _start_import(self, path: str):
        token = f"pending-{self._next_token}"
        self._next_token += 1
        name = os.path.basename(path)
        row = _DocRow(token, name, "Reading…", removable=False)
        self._pending[token] = row
        self._add_row(row)
        self._refresh_state()

        db, session_id = self._db, self._session_id

        def work():
            doc = extract_document(path)
            return db.add_session_document(
                session_id, doc.name, doc.file_type, doc.file_bytes, doc.text)

        self._run_job(work, lambda result, error: self._import_done(token, name, result, error))

    def _import_done(self, token, name, result, error):
        self._pending.pop(token, None)
        if error is None:
            self._selected = result
            self._set_status("")
        elif isinstance(error, DocumentError):
            self._set_status(str(error), error=True)
        else:
            logger.warning("Adding %s failed: %s", name, error)
            self._set_status(str(error) or f"Could not add {name}.", error=True)
        self.reload()
        if error is None:
            self.documents_changed.emit(self._session_id)

    # -- remove ---------------------------------------------------------------

    def _confirm_remove(self, document_id):
        """Delete one document; the row already asked "Remove?" inline."""
        name = next((d["name"] for d in self._documents if d["id"] == document_id), "this document")
        try:
            self._db.delete_session_document(document_id)
        except Exception as e:  # noqa: BLE001
            self._set_status(f"Could not remove {name}: {e}", error=True)
            return
        if self._selected == document_id:
            self._selected = None
        self._set_status("")
        self.reload()
        self.documents_changed.emit(self._session_id)

    # -- drag and drop --------------------------------------------------------

    @staticmethod
    def _local_paths(mime) -> list:
        if mime is None or not mime.hasUrls():
            return []
        return [u.toLocalFile() for u in mime.urls() if u.isLocalFile()]

    def drop_error(self, paths) -> Optional[str]:
        """Why a drag cannot be taken, or None when at least one file can."""
        if self._free_slots() <= 0:
            return "Document limit reached"
        if not paths:
            return "Only local files can be added"
        if not any(is_allowed_document(p) for p in paths):
            if len(paths) == 1 and os.path.isdir(paths[0]):
                return "Folders cannot be added"
            return "Use " + ", ".join(SESSION_DOCUMENTS["allowed_extensions"]) + " files"
        return None

    def dragEnterEvent(self, event):
        paths = self._local_paths(event.mimeData())
        error = self.drop_error(paths)
        if error:
            self._drop_ready = False
            self._drop_overlay.show_state(error.upper(), accepting=False)
            self._reject_timer.start(self.REJECT_LINGER_MS)
            self._set_status(error, error=True)
            event.ignore()
            return
        self._reject_timer.stop()
        self._drop_ready = True
        self._drop_overlay.show_state("DROP TO ADD TO THIS SESSION")
        event.acceptProposedAction()

    def dragMoveEvent(self, event):
        if self._drop_ready:
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragLeaveEvent(self, event):
        self._hide_drop_overlay()
        super().dragLeaveEvent(event)

    def dropEvent(self, event):
        self._hide_drop_overlay()
        paths = self._local_paths(event.mimeData())
        if self.drop_error(paths):
            event.ignore()
            return
        event.acceptProposedAction()
        self.import_paths(paths)

    def _hide_drop_overlay(self):
        self._reject_timer.stop()
        self._drop_ready = False
        self._drop_overlay.hide()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._drop_overlay.setGeometry(self.rect())

    # -- window ---------------------------------------------------------------

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), NAVY)
        p.setPen(QPen(PANEL_BORDER_INNER, 2))
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
