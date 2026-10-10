"""The Start Session popover.

Clicking Start Session opens a small card that grows out of the button: the
session is named there, and documents or images can be attached first. Their
text is read (documents) or described by a vision model (images) right away,
while the user is still typing the name, so the session starts with its
context ready. Nothing touches the database until Start: the popover hands
the name and the extracted documents to ``start_requested``.

It is an overlay child of the main window, not a separate window, so the
file dialog it opens never dismisses it and drag-and-drop lands on it.
``run_job(fn, on_done)`` is the same contract as ``SessionDocumentsDialog``'s.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Dict, List, Optional

from PySide6.QtCore import (
    QEasingCurve, QEvent, QPoint, QStandardPaths, Qt, QTimer,
    QVariantAnimation, Signal,
)
from PySide6.QtGui import QColor, QKeySequence, QPainter, QPainterPath, QPen, QShortcut
from PySide6.QtWidgets import (
    QFileDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QVBoxLayout, QWidget,
)

from ..assistant.session_documents import (
    DocumentError, ExtractedDocument, attachment_extensions, extract_attachment,
    is_allowed_attachment, is_allowed_image,
)
from ..config import SESSION_DOCUMENTS
from . import theme
from .pixel_widgets import (
    ATTACH_ERROR, ATTACH_OK, BORDER_BLUE_ACTIVE, FILE_ROW_QSS, NAVY_INNER,
    PANEL_GLOW, PRIMARY_BUTTON_QSS, PixelDropZone, PixelElidedLabel, _label_qss,
    attachment_icon, pending_text, pixel_caption, pixel_flat_glyph,
    pixel_round_rect_path, short_char_count,
)

logger = logging.getLogger(__name__)

CARD_WIDTH = 344
TAIL_W = 18
TAIL_H = 9
SHADOW = 5
GAP = 4  # between the tail's tip and the button

CREAM = theme.hex("#FFF0BF")
GOLD = theme.hex("#FFE9A8")
MUTED = theme.hex("#8EA7D8")
OK = ATTACH_OK
ERROR = ATTACH_ERROR

# Remembered for the app session, like the upload dialogs.
_last_folder: Optional[str] = None

_NAME_QSS = """
QLineEdit#StartSessionName {
    background: #F6E0A6;
    color: #071846;
    border: 2px solid #F6E0A6;
    border-radius: 6px;
    padding: 6px 9px;
    font-family: 'Courier New';
    font-size: 11pt;
    font-weight: 700;
    selection-background-color: #274F9B;
    selection-color: #FFF0BF;
}
QLineEdit#StartSessionName:focus {
    border: 2px solid #FFF0BF;
}
"""

def default_session_name(now: Optional[datetime] = None) -> str:
    return f"Session {(now or datetime.now()).strftime('%Y-%m-%d %H:%M')}"


@dataclass
class _Staged:
    token: int
    path: str
    name: str
    is_image: bool
    state: str = "pending"  # pending | ready | error
    document: Optional[ExtractedDocument] = None
    error: str = ""
    row: Optional["_StagedRow"] = field(default=None, repr=False)


class _StagedRow(QWidget):
    """One attached file: icon, name, what happened to it, and a remove ✕."""

    remove_requested = Signal(int)

    def __init__(self, item: _Staged):
        super().__init__()
        self.token = item.token
        self._is_image = item.is_image
        self.setObjectName("FileRow")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(FILE_ROW_QSS)
        self.setFixedHeight(34)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 2, 4, 2)
        layout.setSpacing(7)
        layout.addWidget(attachment_icon(item.is_image))

        self.name_label = PixelElidedLabel(item.name)
        self.name_label.setStyleSheet(_label_qss(CREAM, 9.5, bold=True))
        layout.addWidget(self.name_label, 1)

        self.state_label = pixel_caption("", GOLD, 8.5, bold=True)
        layout.addWidget(self.state_label)

        remove = pixel_flat_glyph("✕", "Remove")
        remove.clicked.connect(lambda: self.remove_requested.emit(self.token))
        layout.addWidget(remove)

    def show_pending(self, dots: int):
        self.state_label.setText(pending_text(self._is_image, dots))
        self.state_label.setStyleSheet(_label_qss(GOLD, 8.5, bold=True))

    def show_ready(self, document: ExtractedDocument):
        text = "✓ described" if self._is_image else f"✓ {short_char_count(document.char_count)}"
        self.state_label.setText(text)
        self.state_label.setStyleSheet(_label_qss(OK, 8.5, bold=True))
        preview = document.text[:420] + ("…" if len(document.text) > 420 else "")
        self.setToolTip(preview)

    def show_error(self, message: str):
        self.state_label.setText("✕ failed")
        self.state_label.setStyleSheet(_label_qss(ERROR, 8.5, bold=True))
        self.setToolTip(message)


class _Card(QWidget):
    """The popover's body: pixel panel, hard drop shadow, and a tail that
    points down at the Start button."""

    def __init__(self, parent):
        super().__init__(parent)
        self.tail_x = 30

    def mousePressEvent(self, event):
        event.accept()  # a click inside never reaches the overlay's dismiss

    def _outline(self, dx: int = 0, dy: int = 0) -> QPainterPath:
        w = self.width() - SHADOW - 3
        h = self.height() - SHADOW - TAIL_H - 3
        body = pixel_round_rect_path(2 + dx, 2 + dy, w, h, 8)
        tx, by = self.tail_x + dx, 2 + dy + h
        # Drawn without antialiasing, so its edges step like the pixel corners.
        half = TAIL_W // 2
        tail = QPainterPath()
        tail.moveTo(tx - half, by - 2)
        tail.lineTo(tx, by + TAIL_H)
        tail.lineTo(tx + half, by - 2)
        tail.closeSubpath()
        return body.united(tail)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, False)
        p.fillPath(self._outline(SHADOW, SHADOW), QColor(1, 5, 18, 150))
        path = self._outline()
        p.fillPath(path, NAVY_INNER)
        if PANEL_GLOW:
            p.save()
            p.setRenderHint(QPainter.Antialiasing, True)
            for width, alpha in ((8, 30), (5, 60), (3, 95)):
                glow = QColor(BORDER_BLUE_ACTIVE)
                glow.setAlpha(alpha)
                p.setPen(QPen(glow, width))
                p.drawPath(path)
            p.restore()
        p.setPen(QPen(BORDER_BLUE_ACTIVE, 2))
        p.drawPath(path)


class StartSessionPopover(QWidget):
    """Name a session and attach its context, then start it."""

    start_requested = Signal(str, list)  # name, [ExtractedDocument]
    closed = Signal()

    OPEN_MS = 230
    CLOSE_MS = 140

    def __init__(self, window: QWidget, anchor: QWidget, run_job: Callable,
                 default_name: Optional[str] = None):
        super().__init__(window)
        self._anchor = anchor
        self._run_job = run_job
        self._default_name = default_name or default_session_name()
        self._items: Dict[int, _Staged] = {}
        self._next_token = 0
        self._dots = 0
        self._closing = False
        self._gone = False  # set once closed; late job results are dropped
        self._progress = 0.0
        self._snapshot = None

        self.setAcceptDrops(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setGeometry(window.rect())
        window.installEventFilter(self)

        self._anim = QVariantAnimation(self)
        self._anim.valueChanged.connect(self._on_progress)
        self._anim.finished.connect(self._on_anim_finished)
        self._dot_timer = QTimer(self)
        self._dot_timer.setInterval(360)
        self._dot_timer.timeout.connect(self._tick_dots)

        self._card = _Card(self)
        self._build(self._card)

        QShortcut(QKeySequence(Qt.Key_Escape), self, activated=self.dismiss,
                  context=Qt.WidgetWithChildrenShortcut)

    # -- layout ---------------------------------------------------------------

    def _build(self, card: QWidget):
        root = QVBoxLayout(card)
        root.setContentsMargins(16, 13, 16 + SHADOW, 15 + TAIL_H + SHADOW)
        root.setSpacing(9)

        header = QHBoxLayout()
        header.setSpacing(6)
        dot = QLabel()
        dot.setFixedSize(8, 8)
        dot.setStyleSheet(f"background: {ERROR}; border: none;")
        header.addWidget(dot, 0, Qt.AlignVCenter)
        title = pixel_caption("NEW SESSION", theme.role("section_title"), 9.5, True, 2.5)
        header.addWidget(title)
        header.addStretch(1)
        close = pixel_flat_glyph("✕", "Cancel (Esc)", quiet=True)
        close.clicked.connect(self.dismiss)
        header.addWidget(close)
        root.addLayout(header)

        self.name_input = QLineEdit(self._default_name)
        self.name_input.setObjectName("StartSessionName")
        self.name_input.setPlaceholderText(self._default_name)
        self.name_input.setStyleSheet(_NAME_QSS)
        self.name_input.setMaxLength(120)
        self.name_input.returnPressed.connect(self._request_start)
        root.addWidget(self.name_input)

        context = QHBoxLayout()
        context.setContentsMargins(0, 3, 0, 0)
        context.addWidget(pixel_caption("CONTEXT", MUTED, 8.5, True, 2))
        context.addStretch(1)
        self.count_label = pixel_caption("optional", MUTED, 8)
        context.addWidget(self.count_label)
        root.addLayout(context)

        hint = "  ".join(e.lstrip(".") for e in attachment_extensions() if e != ".jpeg")
        self.drop_zone = PixelDropZone("+  Drop files or browse", hint)
        self.drop_zone.clicked.connect(self._pick_files)
        root.addWidget(self.drop_zone)

        self.rows_layout = QVBoxLayout()
        self.rows_layout.setSpacing(5)
        root.addLayout(self.rows_layout)

        self.status_label = pixel_caption("", ERROR, 8.5)
        self.status_label.setWordWrap(True)
        self.status_label.hide()
        root.addWidget(self.status_label)

        root.addSpacing(2)
        self.start_button = QPushButton()
        self.start_button.setObjectName("PrimaryButton")
        self.start_button.setCursor(Qt.PointingHandCursor)
        self.start_button.setStyleSheet(PRIMARY_BUTTON_QSS)
        self.start_button.setMinimumHeight(40)
        self.start_button.clicked.connect(self._request_start)
        root.addWidget(self.start_button)

        card.setFixedWidth(CARD_WIDTH)
        self._refresh()

    def _fit(self):
        """Size the card to its content and keep its tail on the button; the
        card grows upwards, out of the button, as files are added."""
        layout = self._card.layout()
        layout.invalidate()
        layout.activate()
        height = (layout.heightForWidth(CARD_WIDTH) if layout.hasHeightForWidth()
                  else layout.sizeHint().height())
        self._card.setFixedHeight(max(height, layout.minimumSize().height()))
        self._place()

    def _anchor_tip(self) -> QPoint:
        """Where the tail points: the top centre of the Start button."""
        local = QPoint(self._anchor.width() // 2, 0)
        return self._anchor.mapTo(self.parentWidget(), local) - QPoint(0, GAP)

    def _place(self):
        tip = self._anchor_tip()
        w, h = self._card.width(), self._card.height()
        x = tip.x() - 30
        x = max(8, min(x, self.width() - w - 8))
        y = max(8, tip.y() - h + SHADOW)
        self._card.move(x, y)
        self._card.tail_x = max(16, min(tip.x() - x, w - SHADOW - 18))
        self._card.update()

    def eventFilter(self, obj, event):
        if obj is self.parentWidget() and event.type() == QEvent.Resize:
            self.setGeometry(obj.rect())
            self._place()
        return False

    # -- open / close ---------------------------------------------------------

    def open(self):
        self._fit()
        self.show()
        self.raise_()
        self._snapshot = self._card.grab()
        self._card.hide()
        self._animate(0.0, 1.0, self.OPEN_MS, QEasingCurve.OutBack)

    def dismiss(self):
        """Close without starting."""
        self._close()

    def _close(self):
        if self._closing:
            return
        self._closing = True
        self._dot_timer.stop()
        self._snapshot = self._card.grab()
        self._card.hide()
        self._animate(self._progress or 1.0, 0.0, self.CLOSE_MS, QEasingCurve.InCubic)

    def _animate(self, start: float, end: float, ms: int, curve):
        self._anim.stop()
        self._anim.setStartValue(start)
        self._anim.setEndValue(end)
        self._anim.setDuration(ms)
        self._anim.setEasingCurve(curve)
        self._anim.start()

    def _on_progress(self, value):
        self._progress = float(value)
        self.update()

    def _on_anim_finished(self):
        self._snapshot = None
        if self._closing:
            self._gone = True
            self.parentWidget().removeEventFilter(self)
            self.hide()
            self.closed.emit()
            self.deleteLater()
            return
        self._card.show()
        self.name_input.setFocus(Qt.PopupFocusReason)
        self.name_input.selectAll()
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        t = max(0.0, min(1.0, self._progress))
        p.fillRect(self.rect(), QColor(3, 10, 32, int(60 * t)))
        if self._snapshot is None:
            return
        # Scale the card out of the tail's tip, so it pops from the button.
        tip = self._anchor_tip()
        scale = 0.12 + 0.88 * self._progress
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        p.setOpacity(min(1.0, t * 1.8))
        p.translate(tip.x(), tip.y())
        p.scale(scale, scale)
        p.translate(-tip.x(), -tip.y())
        p.drawPixmap(self._card.pos(), self._snapshot)

    def mousePressEvent(self, event):
        # A click outside the card cancels - unless files are attached, which
        # a stray click should not throw away (Esc and ✕ still do).
        if not self._items:
            self.dismiss()
        event.accept()

    # -- files ----------------------------------------------------------------

    def _usable(self) -> List[_Staged]:
        return [i for i in self._items.values() if i.state != "error"]

    def _free_slots(self) -> int:
        return SESSION_DOCUMENTS["max_per_session"] - len(self._usable())

    def _pick_files(self):
        global _last_folder
        start = _last_folder or QStandardPaths.writableLocation(QStandardPaths.DocumentsLocation)
        docs = " ".join(f"*{e}" for e in SESSION_DOCUMENTS["allowed_extensions"])
        images = " ".join(f"*{e}" for e in SESSION_DOCUMENTS["image_extensions"])
        paths, _ = QFileDialog.getOpenFileNames(
            self.window(), "Attach context", start,
            f"Documents and images ({docs} {images});;Documents ({docs});;Images ({images})")
        if paths:
            _last_folder = os.path.dirname(paths[0])
            self.add_paths(paths)

    def add_paths(self, paths):
        """Stage files and start reading them; a bad file never stops the others."""
        self._set_status("")
        taken = {i.name.lower() for i in self._usable()}
        for path in paths:
            name = os.path.basename(path)
            image = is_allowed_image(path)
            if not is_allowed_attachment(path):
                self._set_status(f"{name} is not a supported file")
                continue
            if name.lower() in taken:
                self._set_status(f"{name} is already attached")
                continue
            if self._free_slots() <= 0:
                self._set_status(
                    f"A session holds up to {SESSION_DOCUMENTS['max_per_session']} files")
                break
            taken.add(name.lower())
            self._stage(path, name, image)
        self._refresh()

    def _stage(self, path: str, name: str, image: bool):
        item = _Staged(self._next_token, path, name, image)
        self._next_token += 1
        item.row = _StagedRow(item)
        item.row.remove_requested.connect(self._remove)
        item.row.show_pending(self._dots)
        self._items[item.token] = item
        self.rows_layout.addWidget(item.row)
        item.row.show()  # now, not on Qt's queued show, so _fit counts it
        token = item.token

        def work():
            return extract_attachment(path)

        self._run_job(work, lambda result, error: self._job_done(token, result, error))

    def _job_done(self, token: int, result, error):
        if self._gone:
            return
        item = self._items.get(token)
        if item is None:  # removed while it was being read
            return
        if error is None:
            item.state, item.document = "ready", result
            item.row.show_ready(result)
        else:
            if not isinstance(error, DocumentError):
                logger.warning("Reading %s failed: %s", item.name, error)
            item.state, item.error = "error", str(error) or f"Could not read {item.name}"
            item.row.show_error(item.error)
            self._set_status(item.error)
        self._refresh()

    def _remove(self, token: int):
        item = self._items.pop(token, None)
        if item is None:
            return
        self.rows_layout.removeWidget(item.row)
        item.row.hide()
        item.row.deleteLater()
        if item.state == "error":
            self._set_status("")
        self._refresh()

    def _tick_dots(self):
        self._dots += 1
        for item in self._items.values():
            if item.state == "pending":
                item.row.show_pending(self._dots)

    def _set_status(self, text: str):
        self.status_label.setText(text)
        self.status_label.setVisible(bool(text))

    def _refresh(self):
        pending = sum(1 for i in self._items.values() if i.state == "pending")
        ready = sum(1 for i in self._items.values() if i.state == "ready")
        if pending:
            self.start_button.setText(f"READING {pending} FILE{'S' if pending > 1 else ''}…")
            self.start_button.setEnabled(False)
            if not self._dot_timer.isActive():
                self._dot_timer.start()
        else:
            self.start_button.setText("▶  START SESSION")
            self.start_button.setEnabled(True)
            self._dot_timer.stop()
        limit = SESSION_DOCUMENTS["max_per_session"]
        self.count_label.setText(f"{len(self._usable())} / {limit}" if self._items else "optional")
        self.drop_zone.setVisible(self._free_slots() > 0)
        self.start_button.setToolTip(
            f"Start recording with {ready} file{'s' if ready != 1 else ''} attached"
            if ready else "Start recording (Enter)")
        if self.isVisible():
            self._fit()

    # -- start ----------------------------------------------------------------

    def _request_start(self):
        if self._closing or not self.start_button.isEnabled():
            return
        name = self.name_input.text().strip() or self._default_name
        documents = [i.document for i in self._items.values() if i.state == "ready"]
        self.start_requested.emit(name, documents)
        self._close()

    # -- drag and drop --------------------------------------------------------

    @staticmethod
    def _local_paths(mime) -> list:
        if mime is None or not mime.hasUrls():
            return []
        return [u.toLocalFile() for u in mime.urls() if u.isLocalFile()]

    def dragEnterEvent(self, event):
        if self._closing:
            event.ignore()
            return
        paths = self._local_paths(event.mimeData())
        usable = [p for p in paths if is_allowed_attachment(p)]
        if not self.drop_zone.isVisible():
            event.ignore()
            return
        if usable:
            self.drop_zone.set_drag("accept", "DROP TO ATTACH")
            event.acceptProposedAction()
        else:
            self.drop_zone.set_drag("reject", "UNSUPPORTED FILE")
            event.accept()  # keep receiving moves so the refusal stays visible

    def dragMoveEvent(self, event):
        if self.drop_zone.drag_state == "accept":
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragLeaveEvent(self, event):
        self.drop_zone.set_drag(None)

    def dropEvent(self, event):
        accepted = self.drop_zone.drag_state == "accept"
        self.drop_zone.set_drag(None)
        if not accepted:
            event.ignore()
            return
        event.acceptProposedAction()
        self.add_paths(self._local_paths(event.mimeData()))
