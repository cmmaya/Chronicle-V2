"""The Documents pop-up of a session (BU148).

:class:`SessionDocumentsDialog` is where a user adds documents and images to
a session (drag and drop anywhere on the window, or click the drop zone),
sees what is stored, previews the text the assistant reads, and removes
entries. A document's text is extracted; an image is described by a vision
model, which also transcribes its text. Both run through ``run_job`` so a long
PDF or a slow model never freezes the window.

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
    QDialog, QFileDialog, QHBoxLayout, QPushButton, QTextEdit, QVBoxLayout,
    QWidget,
)

from ..assistant.session_documents import (
    DocumentError, attachment_extensions, extract_attachment,
    is_allowed_attachment, is_allowed_image, is_image_type,
)
from ..config import SESSION_DOCUMENTS
from . import theme
from .pixel_widgets import (
    ATTACH_ERROR, FILE_ROW_QSS, NAVY, PANEL_BORDER_INNER, PRIMARY_BUTTON_QSS,
    PixelDropZone, PixelElidedLabel, _label_qss, attachment_icon,
    pending_text, pixel_caption, pixel_flat_glyph, short_char_count,
)
from .settings_dialog import _Scrim

logger = logging.getLogger(__name__)

CREAM = theme.hex("#FFF0BF")
GOLD = theme.hex("#FFE9A8")
MUTED = theme.hex("#8EA7D8")
ERROR = ATTACH_ERROR

EMPTY_TEXT = "No documents yet. Answers use the transcript, summary and screenshots."
SCOPE_TEXT = "Used in this session's questions and transcripts."

# Remembered for the app session, like the upload dialogs.
_last_folder: Optional[str] = None

_PREVIEW_QSS = """
QTextEdit {
    background: #071D52;
    color: #C9D6F5;
    border: 2px solid #284B94;
    border-radius: 6px;
    padding: 6px 4px;
    font-family: 'Courier New';
    font-size: 9.5pt;
    selection-background-color: #274F9B;
    selection-color: #FFF0BF;
}
"""


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


def _hint() -> str:
    types = "  ".join(e.lstrip(".") for e in attachment_extensions() if e != ".jpeg")
    return types


class _NameLabel(PixelElidedLabel):
    """Elides on screen; ``text()`` is still the whole name."""

    def text(self) -> str:  # noqa: D102
        return self.full_text()


class _DocRow(QWidget):
    """One stored document or image (or one being read), on a single line."""

    selected = Signal(object)
    remove_requested = Signal(object)

    def __init__(self, key, name: str, meta: str, removable: bool,
                 is_image: bool = False):
        super().__init__()
        self.key = key  # the document id, or a token for a pending import
        self.is_image = is_image
        self.setObjectName("FileRow")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(FILE_ROW_QSS)
        self.setProperty("selected", False)
        self.setFixedHeight(38)
        if removable:
            self.setCursor(Qt.PointingHandCursor)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 2, 6, 2)
        layout.setSpacing(8)
        layout.addWidget(attachment_icon(is_image))
        self.name_label = _NameLabel(name)
        self.name_label.setStyleSheet(_label_qss(CREAM, 9.5, bold=True))
        layout.addWidget(self.name_label, 1)
        self.meta_label = pixel_caption(meta, GOLD if not removable else MUTED,
                                        8.5, bold=not removable)
        layout.addWidget(self.meta_label)

        self.remove_button = pixel_flat_glyph("✕", "Remove")
        self.remove_button.clicked.connect(self._ask_confirm)
        # Parent it before touching its visibility: showing a parentless
        # widget opens it as its own top-level window.
        layout.addWidget(self.remove_button)
        self.remove_button.setVisible(removable)

        # Confirmation sits in the row itself: a separate message box over this
        # window-modal pop-up never reliably answered, so the delete never ran.
        self.confirm_label = pixel_caption("Remove?", GOLD, 8.5, bold=True)
        self.confirm_yes = pixel_flat_glyph("Yes", "Remove it")
        self.confirm_no = pixel_flat_glyph("No", "Keep it", quiet=True)
        self.confirm_yes.clicked.connect(lambda: self.remove_requested.emit(self.key))
        self.confirm_no.clicked.connect(lambda: self._set_confirming(False))
        for widget in (self.confirm_label, self.confirm_yes, self.confirm_no):
            layout.addWidget(widget)
            widget.setVisible(False)

    def set_pending_tick(self, tick: int):
        self.meta_label.setText(pending_text(self.is_image, tick))

    def _ask_confirm(self):
        self._set_confirming(True)

    def _set_confirming(self, value: bool):
        self.meta_label.setVisible(not value)
        self.remove_button.setVisible(not value)
        for widget in (self.confirm_label, self.confirm_yes, self.confirm_no):
            widget.setVisible(value)

    def set_selected(self, value: bool):
        self.setProperty("selected", bool(value))
        self.style().unpolish(self)
        self.style().polish(self)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.selected.emit(self.key)
        super().mousePressEvent(event)


class SessionDocumentsDialog(QDialog):
    """Add, list, preview and remove one session's documents and images."""

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
        self._tick = 0

        self.setObjectName("SessionDocumentsDialog")
        self.setWindowTitle(f"Documents — {session_name}")
        self.setWindowModality(Qt.WindowModal)
        self.setAcceptDrops(True)
        self.resize(540, 640)
        self.setMinimumSize(440, 500)

        root = QVBoxLayout(self)
        root.setContentsMargins(22, 18, 22, 18)
        root.setSpacing(0)

        # Header: caption over the session's name, count on the right.
        header = QHBoxLayout()
        title = QVBoxLayout()
        title.setSpacing(3)
        title.addWidget(pixel_caption("DOCUMENTS", theme.role("section_title"), 9, True, 2.5))
        self.session_label = _NameLabel(session_name)
        self.session_label.setStyleSheet(_label_qss(CREAM, 13, bold=True))
        title.addWidget(self.session_label)
        header.addLayout(title, 1)
        self.count_label = pixel_caption("", MUTED, 9, True, 1.5)
        header.addWidget(self.count_label, 0, Qt.AlignBottom)
        root.addLayout(header)
        root.addSpacing(16)

        # Drop zone: click to browse; drops are taken anywhere on the window.
        self.drop_zone = PixelDropZone("+  Drop files or browse", _hint(), height=58)
        self.drop_zone.disabled_text = (
            f"LIMIT OF {SESSION_DOCUMENTS['max_per_session']} REACHED")
        self.drop_zone.setToolTip(
            f"Documents up to {_size_label(SESSION_DOCUMENTS['max_file_bytes'])}, "
            f"images up to {_size_label(SESSION_DOCUMENTS['max_image_bytes'])}. "
            "Images are described and their text transcribed.")
        self.import_button = self.drop_zone
        root.addWidget(self.drop_zone)
        root.addSpacing(8)

        self.status_label = pixel_caption("", GOLD, 8.5)
        self.status_label.setWordWrap(True)
        self.status_label.hide()
        root.addWidget(self.status_label)
        root.addSpacing(6)

        # List.
        self.empty_label = pixel_caption(EMPTY_TEXT, MUTED, 9)
        self.empty_label.setWordWrap(True)
        self.empty_label.setAlignment(Qt.AlignCenter)
        root.addWidget(self.empty_label)
        self.list_layout = QVBoxLayout()
        self.list_layout.setSpacing(6)
        root.addLayout(self.list_layout)
        root.addSpacing(16)

        # Preview.
        preview_header = QHBoxLayout()
        self.preview_title = pixel_caption("PREVIEW", MUTED, 8.5, True, 2)
        self.preview_meta = pixel_caption("what the assistant reads", MUTED, 8)
        preview_header.addWidget(self.preview_title)
        preview_header.addStretch(1)
        preview_header.addWidget(self.preview_meta)
        self.preview_header = QWidget()
        self.preview_header.setLayout(preview_header)
        preview_header.setContentsMargins(0, 0, 0, 6)
        root.addWidget(self.preview_header)
        self.preview = QTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setAcceptRichText(False)
        self.preview.setMinimumHeight(120)
        self.preview.setStyleSheet(_PREVIEW_QSS)
        root.addWidget(self.preview, 1)
        self.preview_spacer = QWidget()
        root.addWidget(self.preview_spacer, 1)
        root.addSpacing(14)

        # Footer.
        footer = QHBoxLayout()
        footer.setSpacing(12)
        scope = pixel_caption(SCOPE_TEXT, MUTED, 8)
        scope.setWordWrap(True)
        footer.addWidget(scope, 1)
        self.close_button = QPushButton("DONE" if theme.is_pixel() else "Done")
        self.close_button.setObjectName("PrimaryButton")
        self.close_button.setCursor(Qt.PointingHandCursor)
        self.close_button.setStyleSheet(PRIMARY_BUTTON_QSS)
        self.close_button.setMinimumWidth(110)
        self.close_button.setAutoDefault(False)
        footer.addWidget(self.close_button)
        root.addLayout(footer)

        self._reject_timer = QTimer(self)
        self._reject_timer.setSingleShot(True)
        self._reject_timer.timeout.connect(lambda: self.drop_zone.set_drag(None))
        self._tick_timer = QTimer(self)
        self._tick_timer.setInterval(360)
        self._tick_timer.timeout.connect(self._tick_pending)

        self.drop_zone.clicked.connect(self._pick_files)
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
            row.hide()
            row.deleteLater()
        self._rows = []
        for doc in self._documents:
            image = is_image_type(doc["file_type"])
            meta = " · ".join(part for part in (
                doc["file_type"].upper(), _size_label(doc.get("file_bytes")),
                "described" if image else short_char_count(doc["char_count"])) if part)
            row = _DocRow(doc["id"], doc["name"], meta, removable=True, is_image=image)
            added = _date_label(doc.get("added_at"))
            if added:
                row.setToolTip(f"Added {added}")
            self._add_row(row)
        for token, old in pending:
            row = _DocRow(token, old.name_label.text(), pending_text(old.is_image, self._tick),
                          removable=False, is_image=old.is_image)
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
        limit = SESSION_DOCUMENTS["max_per_session"]
        used = len(self._documents) + len(self._pending)
        self.count_label.setText(f"{used} / {limit}")
        full = self._free_slots() <= 0
        self.import_button.setEnabled(not full)
        if full:
            self._set_status(
                f"This session already has {limit} documents. Remove one to add another.")
        if self._pending and not self._tick_timer.isActive():
            self._tick_timer.start()
        elif not self._pending:
            self._tick_timer.stop()

        has_preview = self._selected is not None
        self.preview_header.setVisible(has_preview)
        self.preview.setVisible(has_preview)
        self.preview_spacer.setVisible(not has_preview)
        if has_preview:
            text = self._document_text(self._selected)
            self.preview.setPlainText(text)
            self.preview_meta.setText(f"{len(text):,} chars · what the assistant reads")
        else:
            self.preview.clear()

    def _tick_pending(self):
        self._tick += 1
        for row in self._pending.values():
            row.set_pending_tick(self._tick)

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
        self.status_label.setStyleSheet(_label_qss(ERROR if error else GOLD, 8.5))
        self.status_label.setVisible(bool(text))

    # -- import ---------------------------------------------------------------

    def _pick_files(self):
        global _last_folder
        start = _last_folder or QStandardPaths.writableLocation(
            QStandardPaths.DocumentsLocation)
        docs = " ".join(f"*{e}" for e in SESSION_DOCUMENTS["allowed_extensions"])
        images = " ".join(f"*{e}" for e in SESSION_DOCUMENTS["image_extensions"])
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Add documents or images", start,
            f"Documents and images ({docs} {images});;Documents ({docs});;Images ({images})")
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
        image = is_allowed_image(path)
        row = _DocRow(token, name, pending_text(image, self._tick), removable=False,
                      is_image=image)
        self._pending[token] = row
        self._add_row(row)
        self._refresh_state()

        db, session_id = self._db, self._session_id

        def work():
            doc = extract_attachment(path)
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
        if not any(is_allowed_attachment(p) for p in paths):
            if len(paths) == 1 and os.path.isdir(paths[0]):
                return "Folders cannot be added"
            return "Use " + ", ".join(attachment_extensions()) + " files"
        return None

    def dragEnterEvent(self, event):
        paths = self._local_paths(event.mimeData())
        error = self.drop_error(paths)
        if error:
            self.drop_zone.set_drag("reject", error.upper())
            self._reject_timer.start(self.REJECT_LINGER_MS)
            self._set_status(error, error=True)
            event.ignore()
            return
        self._reject_timer.stop()
        self.drop_zone.set_drag("accept", "DROP TO ADD")
        event.acceptProposedAction()

    def dragMoveEvent(self, event):
        if self.drop_zone.drag_state == "accept":
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragLeaveEvent(self, event):
        if self.drop_zone.drag_state == "accept":
            self.drop_zone.set_drag(None)
        super().dragLeaveEvent(event)

    def dropEvent(self, event):
        self.drop_zone.set_drag(None)
        paths = self._local_paths(event.mimeData())
        if self.drop_error(paths):
            event.ignore()
            return
        event.acceptProposedAction()
        self.import_paths(paths)

    # -- window ---------------------------------------------------------------

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
