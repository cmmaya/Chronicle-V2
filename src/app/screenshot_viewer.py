"""Screenshot viewer window (BU109, BU110).

One image-viewer style window per session, in the Summary window's navy/cream
pixel theme: a large zoomable stage with prev/next, a details panel (user
description, preview, AI summary, visible text, keywords), a thumbnail
filmstrip, and actions to generate AI context, open the file's folder, or
delete a screenshot. A search box filters screenshots by their visible text,
in this session or across all sessions (BU110).
"""
import logging
import os
import subprocess
import sys
from typing import Any, Dict, List, Optional

from PySide6.QtCore import Qt, QPoint, QSize, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import (
    QBrush, QColor, QDesktopServices, QFont, QIcon, QImageReader, QKeySequence,
    QPainter, QPixmap, QShortcut, QTextDocument,
)
from PySide6.QtWidgets import (
    QAbstractItemView, QButtonGroup, QDialog, QFrame, QGraphicsPixmapItem,
    QGraphicsScene, QGraphicsView, QHBoxLayout, QLabel, QLineEdit, QListView,
    QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit, QScrollArea,
    QSplitter, QVBoxLayout, QWidget,
)

from . import theme
from ..config import SCREENSHOT
from ..screenshots.context_generator import ScreenshotContextGenerator
from ..screenshots.metadata import ensure_previews, transcript_window
from ..screenshots.viewer_logic import (
    MATCH_END,
    MATCH_START,
    STATE_AI,
    can_generate_context,
    capture_meta,
    context_state,
    context_state_label,
    description_file_for,
    detail_sections,
    filter_screenshots,
    help_sections,
    index_after_delete,
    missing_visible_text,
    next_zoom,
    search_terms,
    step_index,
    wrap_index,
)
from .pixel_widgets import (
    FIND_TERM_ON_BLUE,
    PixelButton,
    PixelCollapsibleSection,
    PixelPanel,
    PixelSectionTitle,
    PixelStatusChip,
    pixel_filter_chip,
    pixel_mini_button,
)

logger = logging.getLogger(__name__)

THUMB_SIZE = QSize(168, 96)
THUMBS_PER_TICK = 3
DETAILS_SCALE = 0.9
SEARCH_DEBOUNCE_MS = 220
ALL_SESSIONS_RESULT_LIMIT = 200

# Background context threads outlive the viewer if it is closed mid-run; a
# QThread destroyed while running aborts the process, so keep a reference
# until each one finishes.
_RUNNING_THREADS = set()


def _keep_alive(thread: QThread) -> None:
    _RUNNING_THREADS.add(thread)
    thread.finished.connect(lambda t=thread: _RUNNING_THREADS.discard(t))


def _qss(color: str, pt: float, bold: bool = False, extra: str = "") -> str:
    # Font sizes live in the widget's own stylesheet: the app-wide QSS sets a
    # font-size for QWidget, which beats setFont.
    weight = " font-weight: 700;" if bold else ""
    return (
        f"QLabel {{ color: {color}; background: transparent; border: none;"
        f" font-family: 'Courier New'; font-size: {pt:.1f}pt;{weight} {extra} }}"
    )


def load_thumbnail(filepath: str, max_w: int, max_h: int) -> QPixmap:
    """Decode an image straight to a size that fits (max_w, max_h).

    QImageReader.setScaledSize decodes at the target size instead of loading
    the full screen-resolution image and scaling it down afterwards. Returns a
    null QPixmap if the file can't be read.
    """
    reader = QImageReader(filepath)
    reader.setAutoTransform(True)
    size = reader.size()
    if size.isValid() and size.width() > 0 and size.height() > 0:
        scale = min(max_w / size.width(), max_h / size.height(), 1.0)
        reader.setScaledSize(QSize(
            max(1, round(size.width() * scale)), max(1, round(size.height() * scale))
        ))
    image = reader.read()
    return QPixmap.fromImage(image) if not image.isNull() else QPixmap()


def _thumb_icon(pixmap: QPixmap) -> QIcon:
    # Same pixmap for the Selected mode, or the style tints the selected
    # thumbnail with the highlight colour; the item border marks selection.
    icon = QIcon(pixmap)
    icon.addPixmap(pixmap, QIcon.Selected)
    return icon


def _load_full_image(filepath: str) -> QPixmap:
    reader = QImageReader(filepath)
    reader.setAutoTransform(True)
    image = reader.read()
    return QPixmap.fromImage(image) if not image.isNull() else QPixmap()


class ScreenshotContextThread(QThread):
    """Generate AI context for one screenshot off the UI thread."""

    finished_signal = Signal(object)  # the context dict
    error_signal = Signal(str)

    def __init__(self, screenshot_path, summary, transcript_excerpt, db):
        super().__init__()
        self.screenshot_path = screenshot_path
        self.summary = summary
        self.transcript_excerpt = transcript_excerpt
        self.db = db

    def run(self):
        try:
            context_gen = ScreenshotContextGenerator(database=self.db)
            context = context_gen.generate_context(
                screenshot_path=self.screenshot_path,
                summary=self.summary,
                transcript_excerpt=self.transcript_excerpt,
                store=True
            )
            self.finished_signal.emit(context)
        except Exception as e:
            self.error_signal.emit(str(e))
        finally:
            # This thread's lazily-opened SQLite connection (one per thread -
            # see storage.database.Database) would otherwise stay open.
            self.db.release_thread_connection()


class ScreenshotContextBatchThread(QThread):
    """Generate AI context for several screenshots, one after another."""

    progress_signal = Signal(int, int, object, str)  # current, total, context/None, filepath
    finished_signal = Signal(list)  # list of (context, filepath)

    def __init__(self, screenshots_data, summary, db):
        """
        Args:
            screenshots_data: dicts with 'filepath', 'timestamp' and 'session_id'
            summary: Session summary text (may be empty)
            db: Database instance
        """
        super().__init__()
        self.screenshots_data = screenshots_data
        self.summary = summary
        self.db = db

    def run(self):
        results = []
        try:
            context_gen = ScreenshotContextGenerator(database=self.db)
            total = len(self.screenshots_data)
            transcripts_by_session = {}
            for idx, screenshot in enumerate(self.screenshots_data):
                filepath = screenshot.get('filepath', '')
                if not filepath:
                    self.progress_signal.emit(idx + 1, total, None, filepath)
                    continue

                session_id = screenshot.get('session_id', 0)
                if session_id not in transcripts_by_session:
                    transcripts_by_session[session_id] = self.db.get_transcripts(session_id)
                # Transcript said around the capture time (±20 s)
                transcript_excerpt = transcript_window(
                    transcripts_by_session[session_id], screenshot.get('timestamp', 0)
                )

                try:
                    context = context_gen.generate_context(
                        screenshot_path=filepath,
                        summary=self.summary or None,
                        transcript_excerpt=transcript_excerpt or None,
                        store=True
                    )
                    results.append((context, filepath))
                    self.progress_signal.emit(idx + 1, total, context, filepath)
                except Exception as e:
                    logger.warning(f"Failed to generate context for {filepath}: {e}")
                    self.progress_signal.emit(idx + 1, total, None, filepath)
        except Exception as e:
            logger.error(f"Batch screenshot context generation failed: {e}")
        finally:
            self.finished_signal.emit(results)
            self.db.release_thread_connection()


class _ImageStage(QGraphicsView):
    """Zoomable, pannable image area with prev/next overlay buttons."""

    zoom_changed = Signal(float)
    double_clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self._item = QGraphicsPixmapItem()
        self._item.setTransformationMode(Qt.SmoothTransformation)
        self._scene.addItem(self._item)
        self._message = self._scene.addText("")
        self._message.setDefaultTextColor(theme.qcolor("#8EA7D8"))
        message_font = QFont("Courier New")
        message_font.setPointSize(11)
        self._message.setFont(message_font)

        self._fit_mode = True
        self._zoom = 1.0

        self.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorViewCenter)
        self.setBackgroundBrush(QBrush(theme.qcolor("#071D52")))
        self.setFrameShape(QFrame.NoFrame)
        self.setStyleSheet(
            "QGraphicsView { border: 2px solid #254D9C; border-radius: 6px;"
            " background: #071D52; }"
        )
        self.setMinimumSize(320, 220)

        self.prev_button = pixel_mini_button("◀", "Previous screenshot (←)", 36, 52)
        self.next_button = pixel_mini_button("▶", "Next screenshot (→)", 36, 52)
        for button in (self.prev_button, self.next_button):
            button.setParent(self)
            button.setFocusPolicy(Qt.NoFocus)

    def zoom_level(self) -> float:
        return self._zoom

    def set_image(self, pixmap: QPixmap, message: str = ""):
        """Show ``pixmap``, or ``message`` in place of the image when it's null."""
        if pixmap.isNull():
            self._item.setPixmap(QPixmap())
            self._message.setPlainText(message)
            self._scene.setSceneRect(self._message.boundingRect())
        else:
            self._message.setPlainText("")
            self._item.setPixmap(pixmap)
            self._scene.setSceneRect(self._item.boundingRect())
        self.fit()

    def fit(self):
        """Fit the image to the view (never upscaled beyond 100%)."""
        self._fit_mode = True
        self.resetTransform()
        if not self._item.pixmap().isNull():
            self.fitInView(self._item, Qt.KeepAspectRatio)
            if self.transform().m11() > 1.0:
                self.resetTransform()
        else:
            self.centerOn(self._message)
        self._zoom = self.transform().m11()
        self.zoom_changed.emit(self._zoom)

    def zoom(self, direction: int):
        if self._item.pixmap().isNull():
            return
        target = next_zoom(self._zoom, direction)
        factor = target / self._zoom if self._zoom else 1.0
        self.scale(factor, factor)
        self._zoom = target
        self._fit_mode = False
        self.zoom_changed.emit(self._zoom)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.double_clicked.emit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def wheelEvent(self, event):
        if event.modifiers() & Qt.ControlModifier:
            self.zoom(1 if event.angleDelta().y() > 0 else -1)
            event.accept()
            return
        super().wheelEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._fit_mode:
            self.fit()
        y = (self.height() - self.prev_button.height()) // 2
        self.prev_button.move(10, y)
        self.next_button.move(self.width() - self.next_button.width() - 10, y)


class _FullscreenImage(QWidget):
    """The selected screenshot alone, filling the screen.

    Esc (or another double-click) closes it; ←/→ keep browsing. A child
    window of the viewer, so it is never blocked when the viewer is modal.
    """

    navigate = Signal(int)
    closed = Signal()

    def __init__(self, viewer: QWidget):
        super().__init__(viewer, Qt.Window | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self.setStyleSheet("background: #030C24;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 8)
        layout.setSpacing(6)
        self.stage = _ImageStage()
        self.stage.setBackgroundBrush(QBrush(theme.qcolor("#030C24")))
        self.stage.setStyleSheet("QGraphicsView { border: none; background: #030C24; }")
        self.stage.double_clicked.connect(self.close)
        self.stage.prev_button.clicked.connect(lambda: self.navigate.emit(-1))
        self.stage.next_button.clicked.connect(lambda: self.navigate.emit(1))
        layout.addWidget(self.stage, 1)

        self.caption = QLabel("")
        self.caption.setAlignment(Qt.AlignCenter)
        self.caption.setStyleSheet(_qss("#8EA7D8", 10))
        layout.addWidget(self.caption, 0)

        bindings = (
            ("Esc", self.close),
            ("F", self.close),
            ("F11", self.close),
            ("Left", lambda: self.navigate.emit(-1)),
            ("Right", lambda: self.navigate.emit(1)),
            ("+", lambda: self.stage.zoom(1)),
            ("=", lambda: self.stage.zoom(1)),
            ("-", lambda: self.stage.zoom(-1)),
            ("0", self.stage.fit),
        )
        for key, handler in bindings:
            QShortcut(QKeySequence(key), self).activated.connect(handler)

    def show_image(self, pixmap: QPixmap, caption: str, has_prev: bool, has_next: bool):
        self.stage.set_image(pixmap, "Could not load the image.")
        self.caption.setText(f"{caption}   ·   Esc or double-click to close")
        self.stage.prev_button.setEnabled(has_prev)
        self.stage.next_button.setEnabled(has_next)

    def closeEvent(self, event):
        self.closed.emit()
        super().closeEvent(event)


class ScreenshotViewer(QDialog):
    """Image-viewer window for one session's screenshots."""

    # Emitted with the session id whenever screenshots or their metadata change.
    screenshots_changed = Signal(int)

    # Remembered for the rest of the app session, like the Summary window.
    _remembered_size: Optional[QSize] = None
    _remembered_splitter: Optional[List[int]] = None

    def __init__(self, db, session_id: int, session_name: str, parent=None,
                 focus_screenshot_id: Optional[int] = None, on_status=None):
        super().__init__(parent)
        self._db = db
        self._session_id = session_id
        self._session_name = session_name
        self._on_status = on_status or (lambda message: None)
        # Every screenshot of this session, and the ones currently shown
        # (all of them, or the search results - possibly from other sessions).
        self._session_rows: List[Dict[str, Any]] = []
        self._rows: List[Dict[str, Any]] = []
        self._index = -1
        self._thumbs: Dict[str, QPixmap] = {}
        self._thumb_queue: List[int] = []
        self._busy = False
        self._session_start = None
        # session_id -> (has_summary, has_transcripts), for context generation.
        self._session_flags: Dict[int, tuple] = {}
        # Image-only full screen view, while open.
        self._fullscreen: Optional[_FullscreenImage] = None

        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(SEARCH_DEBOUNCE_MS)
        self._search_timer.timeout.connect(lambda: self._apply_filter())

        self.setWindowTitle(f"Screenshots - {session_name}")
        self.setStyleSheet("QDialog { background: #061946; }")
        self.setWindowFlag(Qt.WindowMaximizeButtonHint, True)
        self.setWindowFlag(Qt.WindowMinimizeButtonHint, True)
        self.setSizeGripEnabled(True)
        self.setMinimumSize(760, 540)
        self.resize(ScreenshotViewer._remembered_size or QSize(1120, 780))

        self._thumb_timer = QTimer(self)
        self._thumb_timer.setInterval(0)
        self._thumb_timer.timeout.connect(self._load_next_thumbnails)

        self._build_ui()
        self._install_shortcuts()
        self.reload(focus_screenshot_id)

    # ---- construction ------------------------------------------------------

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        panel = PixelPanel()
        outer.addWidget(panel)

        layout = QVBoxLayout(panel)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        # Title row: heading, zoom controls, position counter.
        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        title_row.addWidget(PixelSectionTitle("SESSION SCREENSHOTS"), 1)
        self.zoom_out_button = pixel_mini_button("−", "Zoom out (-)")
        self.fit_button = pixel_mini_button("Fit", "Fit to window (0)", width=48)
        self.zoom_in_button = pixel_mini_button("+", "Zoom in (+)")
        self.zoom_label = QLabel("100%")
        self.zoom_label.setFixedWidth(52)
        self.zoom_label.setAlignment(Qt.AlignCenter)
        self.zoom_label.setStyleSheet(_qss("#8EA7D8", 9))
        self.counter_label = QLabel("0 / 0")
        self.counter_label.setMinimumWidth(70)
        self.counter_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.counter_label.setStyleSheet(_qss("#FFE9A8", 10, bold=True))
        self.help_button = pixel_mini_button("?", "How to use this window (F1)", width=34)
        self.help_button.clicked.connect(self.show_help)
        for widget in (self.zoom_out_button, self.fit_button, self.zoom_in_button,
                       self.zoom_label, self.counter_label, self.help_button):
            title_row.addWidget(widget, 0)
        layout.addLayout(title_row)

        # Session name + meta row for the selected screenshot.
        self.name_label = QLabel(self._session_name)
        self.name_label.setWordWrap(True)
        self.name_label.setStyleSheet(_qss("#FFF0BF", 13, bold=True))
        layout.addWidget(self.name_label)

        meta_row = QHBoxLayout()
        meta_row.setSpacing(10)
        self.meta_label = QLabel("")
        self.meta_label.setStyleSheet(_qss("#8EA7D8", 10))
        self.id_badge = QLabel("")
        self.id_badge.setStyleSheet(_qss(
            "#071846", 9, bold=True,
            extra="background: #F6E0A6; border: 2px solid #FFEFC1;"
                  " border-radius: 8px; padding: 1px 8px;"
        ))
        self.state_chip = PixelStatusChip("AI context")
        meta_row.addWidget(self.meta_label, 0)
        meta_row.addWidget(self.id_badge, 0)
        meta_row.addWidget(self.state_chip, 0)
        meta_row.addStretch(1)
        layout.addLayout(meta_row)

        layout.addLayout(self._build_search_row())
        layout.addWidget(self._build_search_hint())

        # Stage | details.
        self.splitter = QSplitter(Qt.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setHandleWidth(6)
        self.splitter.setStyleSheet("QSplitter::handle { background: #1E3F82; }")

        self.stage = _ImageStage()
        self.stage.zoom_changed.connect(
            lambda zoom: self.zoom_label.setText(f"{round(zoom * 100)}%")
        )
        self.stage.prev_button.clicked.connect(lambda: self.step(-1))
        self.stage.next_button.clicked.connect(lambda: self.step(1))
        self.stage.double_clicked.connect(self.open_fullscreen)
        self.stage.setToolTip("Double-click to see it full screen")
        self.splitter.addWidget(self.stage)
        self.splitter.addWidget(self._build_details_panel())
        self.splitter.setStretchFactor(0, 3)
        self.splitter.setStretchFactor(1, 2)
        self.splitter.setSizes(ScreenshotViewer._remembered_splitter or [720, 360])
        layout.addWidget(self.splitter, 1)

        # Filmstrip.
        self.filmstrip = QListWidget()
        self.filmstrip.setViewMode(QListView.IconMode)
        self.filmstrip.setFlow(QListView.LeftToRight)
        self.filmstrip.setWrapping(False)
        self.filmstrip.setMovement(QListView.Static)
        self.filmstrip.setResizeMode(QListView.Adjust)
        self.filmstrip.setIconSize(THUMB_SIZE)
        self.filmstrip.setSpacing(4)
        self.filmstrip.setUniformItemSizes(True)
        self.filmstrip.setSelectionMode(QAbstractItemView.SingleSelection)
        self.filmstrip.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.filmstrip.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.filmstrip.setFixedHeight(THUMB_SIZE.height() + 58)
        self.filmstrip.setStyleSheet(
            """
            QListWidget {
                background: #071D52; border: 2px solid #254D9C; border-radius: 6px;
                color: #8EA7D8; font-family: 'Courier New'; font-size: 8pt;
            }
            QListWidget::item {
                border: 2px solid transparent; border-radius: 4px; padding: 3px;
            }
            QListWidget::item:hover { background: #0B2762; }
            QListWidget::item:selected {
                background: #12306E; border: 2px solid #F6E0A6; color: #FFF0BF;
            }
            """
        )
        self.filmstrip.currentRowChanged.connect(self._on_filmstrip_row)
        self.filmstrip.itemDoubleClicked.connect(lambda _item: self.open_fullscreen())
        layout.addWidget(self.filmstrip)

        # Action bar.
        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.generate_button = PixelButton("Generate context")
        self.generate_missing_button = PixelButton("Generate missing")
        self.open_folder_button = PixelButton("Open folder")
        self.delete_button = PixelButton("Delete")
        self.close_button = PixelButton("Close")
        self.close_button.setFixedWidth(120)
        self.generate_button.clicked.connect(self._generate_selected)
        self.generate_missing_button.clicked.connect(self._generate_missing)
        self.open_folder_button.clicked.connect(self._open_folder)
        self.delete_button.clicked.connect(self._delete_selected)
        self.close_button.clicked.connect(self.close)
        for button in (self.generate_button, self.generate_missing_button,
                       self.open_folder_button, self.delete_button):
            actions.addWidget(button, 0)
        actions.addStretch(1)
        actions.addWidget(self.close_button, 0)
        layout.addLayout(actions)

        self.zoom_out_button.clicked.connect(lambda: self.stage.zoom(-1))
        self.zoom_in_button.clicked.connect(lambda: self.stage.zoom(1))
        self.fit_button.clicked.connect(self.stage.fit)

    def _build_search_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(8)
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search visible text…  (Ctrl+F)")
        self.search_input.setClearButtonEnabled(True)
        self.search_input.setMinimumHeight(34)
        self.search_input.setStyleSheet(
            "QLineEdit { color: #FFF0BF; background: #071D52; border: 2px solid #3A67C7;"
            " border-radius: 6px; padding: 2px 8px; font-family: 'Courier New'; font-size: 10pt; }"
            "QLineEdit:focus { border-color: #F6E0A6; }"
        )
        self.search_input.textChanged.connect(lambda _text: self._search_timer.start())
        self.search_input.returnPressed.connect(lambda: self.next_match(1))
        row.addWidget(self.search_input, 1)

        self.scope_session_chip = pixel_filter_chip("This session")
        self.scope_all_chip = pixel_filter_chip("All sessions")
        self.scope_session_chip.setChecked(True)
        scope_group = QButtonGroup(self)
        scope_group.setExclusive(True)
        scope_group.addButton(self.scope_session_chip)
        scope_group.addButton(self.scope_all_chip)
        for chip in (self.scope_session_chip, self.scope_all_chip):
            chip.toggled.connect(lambda _checked: self._apply_filter())
            row.addWidget(chip, 0)

        self.match_label = QLabel("")
        self.match_label.setMinimumWidth(96)
        self.match_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.match_label.setStyleSheet(_qss("#8EA7D8", 9))
        row.addWidget(self.match_label, 0)
        return row

    def _build_search_hint(self) -> QWidget:
        self.search_hint = QWidget()
        hint_layout = QHBoxLayout(self.search_hint)
        hint_layout.setContentsMargins(2, 0, 0, 0)
        hint_layout.setSpacing(8)
        self.search_hint_label = QLabel("")
        self.search_hint_label.setStyleSheet(_qss("#8EA7D8", 9))
        self.search_hint_button = pixel_mini_button(
            "Generate missing", "Describe them with AI so they become searchable",
            width=190, height=28,
        )
        self.search_hint_button.clicked.connect(self._generate_missing)
        hint_layout.addWidget(self.search_hint_label, 0)
        hint_layout.addWidget(self.search_hint_button, 0)
        hint_layout.addStretch(1)
        self.search_hint.setVisible(False)
        return self.search_hint

    def _build_details_panel(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setMinimumWidth(260)
        container = QWidget()
        column = QVBoxLayout(container)
        column.setContentsMargins(0, 0, 6, 0)
        column.setSpacing(10)

        self._sections: Dict[str, PixelCollapsibleSection] = {}
        for title, _body in detail_sections({}):
            section = PixelCollapsibleSection(title, "")
            section.set_scale(DETAILS_SCALE)
            self._sections[title] = section
            column.addWidget(section)
            if title == "Description":
                column.addWidget(self._build_description_editor())

        column.addStretch(1)
        scroll.setWidget(container)
        self._details_scroll = scroll
        return scroll

    def _build_description_editor(self) -> QWidget:
        box = QWidget()
        box_layout = QVBoxLayout(box)
        box_layout.setContentsMargins(0, 0, 0, 0)
        box_layout.setSpacing(6)

        self.description_editor = QPlainTextEdit()
        self.description_editor.setPlaceholderText("Describe this screenshot…")
        self.description_editor.setFixedHeight(84)
        self.description_editor.setStyleSheet(
            "QPlainTextEdit { color: #FFF0BF; background: #071D52;"
            " border: 2px solid #3A67C7; border-radius: 5px;"
            " font-family: 'Courier New'; font-size: 10pt; }"
        )
        self.description_editor.setVisible(False)
        box_layout.addWidget(self.description_editor)

        row = QHBoxLayout()
        row.setSpacing(6)
        self.edit_description_button = pixel_mini_button("Edit", "Edit the description", width=64)
        self.save_description_button = pixel_mini_button("Save", "Save the description", width=64)
        self.cancel_description_button = pixel_mini_button("Cancel", "Discard changes", width=76)
        self.save_description_button.setVisible(False)
        self.cancel_description_button.setVisible(False)
        self.edit_description_button.clicked.connect(self._begin_description_edit)
        self.save_description_button.clicked.connect(self._save_description)
        self.cancel_description_button.clicked.connect(self._end_description_edit)
        row.addWidget(self.edit_description_button)
        row.addWidget(self.save_description_button)
        row.addWidget(self.cancel_description_button)
        row.addStretch(1)
        box_layout.addLayout(row)
        return box

    def _install_shortcuts(self):
        bindings = (
            ("Left", lambda: self.step(-1)),
            ("Right", lambda: self.step(1)),
            ("Home", lambda: self.select(0)),
            ("End", lambda: self.select(len(self._rows) - 1)),
            ("Del", self._delete_selected),
            ("F", self.open_fullscreen),
            ("F11", self.open_fullscreen),
            ("F1", self.show_help),
            ("+", lambda: self.stage.zoom(1)),
            ("=", lambda: self.stage.zoom(1)),
            ("-", lambda: self.stage.zoom(-1)),
            ("0", self.stage.fit),
            ("Ctrl+F", self._focus_search),
            ("F3", lambda: self.next_match(1)),
            ("Shift+F3", lambda: self.next_match(-1)),
        )
        for key, handler in bindings:
            QShortcut(QKeySequence(key), self).activated.connect(handler)

    # ---- data --------------------------------------------------------------

    @property
    def session_id(self) -> int:
        return self._session_id

    def current_row(self) -> Optional[Dict[str, Any]]:
        if 0 <= self._index < len(self._rows):
            return self._rows[self._index]
        return None

    def reload(self, focus_screenshot_id: Optional[int] = None):
        """Re-read the session's screenshots, keeping (or moving) the selection."""
        keep_id = focus_screenshot_id
        if keep_id is None and self.current_row():
            keep_id = self.current_row().get('id')

        try:
            ensure_previews(self._db, self._session_id)
        except Exception as e:
            logger.warning(f"Could not refresh screenshot previews: {e}")
        try:
            self._session_rows = self._db.get_screenshots(self._session_id)
        except Exception as e:
            logger.error(f"Failed to load screenshots: {e}")
            self._session_rows = []
        try:
            session = self._db.get_session(self._session_id) or {}
            self._session_start = session.get('start_time')
        except Exception:
            pass
        # Transcripts / summaries may have arrived since the last look.
        self._session_flags.clear()
        self._apply_filter(keep_id)

    def refresh_after_capture(self):
        """A screenshot was just added to this session (BU110 live viewing):
        show it, and jump to it if the user was looking at the latest one."""
        was_on_last = self._index >= len(self._rows) - 1
        self.reload()
        if was_on_last and self._rows and not self._search_active():
            self.select(len(self._rows) - 1)

    # ---- search (BU110) ----------------------------------------------------

    def _search_active(self) -> bool:
        return bool(search_terms(self.search_input.text()))

    def _search_all_sessions(self) -> bool:
        return self.scope_all_chip.isChecked()

    def _focus_search(self):
        self.search_input.setFocus()
        self.search_input.selectAll()

    def next_match(self, delta: int):
        if self._search_active() and self._rows:
            self.select(wrap_index(self._index, delta, len(self._rows)))

    def _apply_filter(self, keep_id: Optional[int] = None):
        """Show every screenshot of the session, or the search results."""
        if keep_id is None and self.current_row():
            keep_id = self.current_row().get('id')
        query = self.search_input.text()

        if not self._search_active():
            rows = self._session_rows
        elif self._search_all_sessions():
            try:
                candidates = self._db.get_searchable_screenshots()
            except Exception as e:
                logger.error(f"Screenshot search failed: {e}")
                candidates = []
            rows = filter_screenshots(candidates, query, limit=ALL_SESSIONS_RESULT_LIMIT)
        else:
            rows = filter_screenshots(self._session_rows, query)

        self._rows = rows
        self._populate_filmstrip()
        index = 0
        if keep_id is not None:
            index = next((i for i, r in enumerate(self._rows) if r.get('id') == keep_id), 0)
        self._index = -1
        self.select(index)
        self._update_search_status()

    def _update_search_status(self):
        if not self._search_active():
            self.match_label.setText("")
            self.search_hint.setVisible(False)
            return
        count = len(self._rows)
        self.match_label.setText(f"{count} match{'es' if count != 1 else ''}")
        # Screenshots of this session that the search can't see yet.
        missing = missing_visible_text(self._session_rows)
        if missing:
            self.search_hint_label.setText(
                f"{missing} screenshot{'s' if missing != 1 else ''} of this session "
                f"{'have' if missing != 1 else 'has'} no visible text yet:"
            )
        self.search_hint.setVisible(bool(missing))

    def _populate_filmstrip(self):
        self.filmstrip.blockSignals(True)
        self.filmstrip.clear()
        placeholder = QPixmap(THUMB_SIZE)
        placeholder.fill(theme.qcolor("#0B2762"))
        for row in self._rows:
            thumb = self._thumbs.get(row.get('filepath', ''))
            item = QListWidgetItem(_thumb_icon(thumb if thumb is not None else placeholder),
                                   capture_meta(row).split(' ')[0])
            item.setData(Qt.UserRole, row.get('id'))
            tooltip = (row.get('preview_description') or row.get('description') or '')[:200]
            if row.get('session_id') != self._session_id and row.get('session_name'):
                tooltip = f"{row['session_name']}\n{tooltip}"
            item.setToolTip(tooltip)
            item.setTextAlignment(Qt.AlignHCenter | Qt.AlignBottom)
            self.filmstrip.addItem(item)
        self.filmstrip.blockSignals(False)
        self._thumb_queue = [i for i, r in enumerate(self._rows)
                             if r.get('filepath', '') not in self._thumbs]
        if self._thumb_queue:
            self._thumb_timer.start()

    def _load_next_thumbnails(self):
        for _ in range(THUMBS_PER_TICK):
            if not self._thumb_queue:
                self._thumb_timer.stop()
                return
            index = self._thumb_queue.pop(0)
            if index >= len(self._rows) or index >= self.filmstrip.count():
                continue
            filepath = self._rows[index].get('filepath', '')
            pixmap = load_thumbnail(filepath, THUMB_SIZE.width(), THUMB_SIZE.height())
            if not pixmap.isNull():
                self._thumbs[filepath] = pixmap
                self.filmstrip.item(index).setIcon(_thumb_icon(pixmap))

    # ---- selection ---------------------------------------------------------

    def step(self, delta: int):
        if self._rows:
            self.select(step_index(self._index, delta, len(self._rows)))

    def select(self, index: int):
        self._end_description_edit()
        count = len(self._rows)
        self._index = index if 0 <= index < count else (0 if count else -1)
        row = self.current_row()

        if self.filmstrip.currentRow() != self._index:
            self.filmstrip.blockSignals(True)
            self.filmstrip.setCurrentRow(self._index)
            self.filmstrip.blockSignals(False)
        if self._index >= 0:
            self.filmstrip.scrollToItem(self.filmstrip.item(self._index))

        self.counter_label.setText(f"{self._index + 1} / {count}" if count else "0 / 0")
        self.stage.prev_button.setEnabled(self._index > 0)
        self.stage.next_button.setEnabled(0 <= self._index < count - 1)

        if row is None:
            if self._fullscreen is not None:
                self._fullscreen.close()
            self.name_label.setText(self._session_name)
            self.stage.set_image(QPixmap(), "No matching screenshots." if self._search_active()
                                 else "No screenshots in this session yet.")
            self.meta_label.setText("")
            self.id_badge.setVisible(False)
            self.state_chip.setVisible(False)
        else:
            filepath = row.get('filepath', '')
            own_session = row.get('session_id', self._session_id) == self._session_id
            self.name_label.setText(
                self._session_name if own_session
                else row.get('session_name') or f"Session {row.get('session_id')}"
            )
            pixmap = _load_full_image(filepath)
            self.stage.set_image(pixmap, f"Could not load the image:\n{filepath}")
            self.meta_label.setText(
                capture_meta(row, self._session_start if own_session else None)
            )
            if self._fullscreen is not None:
                self._show_in_fullscreen(row, pixmap)
            self.id_badge.setText(f"#{row.get('id')}")
            self.id_badge.setToolTip("The assistant refers to this screenshot by this id")
            self.id_badge.setVisible(True)
            self.state_chip.setVisible(True)
        self._refresh_details()
        self._update_actions()

    def focus_screenshot(self, screenshot_id: int) -> bool:
        """Select a screenshot by id, clearing a search that hides it. False
        when it isn't in this session."""
        if not any(r.get('id') == screenshot_id for r in self._rows) \
                and any(r.get('id') == screenshot_id for r in self._session_rows):
            self.search_input.blockSignals(True)
            self.search_input.clear()
            self.search_input.blockSignals(False)
            self._apply_filter(screenshot_id)
        for index, row in enumerate(self._rows):
            if row.get('id') == screenshot_id:
                self.select(index)
                return True
        return False

    def _on_filmstrip_row(self, row: int):
        if row >= 0 and row != self._index:
            self.select(row)

    def _refresh_details(self):
        row = self.current_row() or {}
        terms = search_terms(self.search_input.text())
        first_match = None
        for title, body in detail_sections(row, terms):
            section = self._sections[title]
            section.set_body(body if row else "", match_marks=(MATCH_START, MATCH_END))
            if row and MATCH_START in body:
                section.set_expanded(True)  # never hide a highlighted match
                first_match = first_match or section
        if first_match is not None:
            # After the layout settles, slide to the first highlighted word.
            QTimer.singleShot(0, lambda s=first_match: self._scroll_to_first_match(s))
        if row:
            state = context_state(row)
            self.state_chip.set_state(
                "ready" if state == STATE_AI else "pending", context_state_label(row)
            )
            self.state_chip.setToolTip(
                "The vision model has described this screenshot" if state == STATE_AI
                else "Only the automatic preview exists - use Generate context"
            )

    def _scroll_to_first_match(self, section: PixelCollapsibleSection):
        """Scroll the details panel so the first highlighted word of
        ``section`` sits in the upper third of the view - not just the
        section's top, which can leave a match deep in a long list off-screen.
        """
        try:
            label = section.body_label
            doc = QTextDocument()
            doc.setDefaultFont(label.font())
            doc.setDocumentMargin(0)
            doc.setHtml(label.text())
            doc.setTextWidth(max(1, label.contentsRect().width()))

            highlight = QColor(FIND_TERM_ON_BLUE[0]).name().lower()
            position = None
            block = doc.begin()
            while block.isValid() and position is None:
                it = block.begin()
                while not it.atEnd():
                    fragment = it.fragment()
                    if fragment.isValid() and \
                            fragment.charFormat().background().color().name().lower() == highlight:
                        position = fragment.position()
                        break
                    it += 1
                block = block.next()

            y = 0
            if position is not None:
                block = doc.findBlock(position)
                line = block.layout().lineForTextPosition(position - block.position())
                y = doc.documentLayout().blockBoundingRect(block).top()
                if line.isValid():
                    y += line.y()

            container = self._details_scroll.widget()
            target = label.mapTo(container, QPoint(0, int(y))).y()
            viewport = self._details_scroll.viewport().height()
            bar = self._details_scroll.verticalScrollBar()
            bar.setValue(max(0, min(bar.maximum(), target - viewport // 3)))
        except RuntimeError:
            pass  # the viewer closed before the scroll ran

    def _flags_for(self, session_id: int) -> tuple:
        """``(has_summary, has_transcripts)`` for a session, cached per reload."""
        if session_id not in self._session_flags:
            try:
                self._session_flags[session_id] = (
                    bool(self._db.get_summaries(session_id)),
                    bool(self._db.get_transcripts(session_id)),
                )
            except Exception:
                self._session_flags[session_id] = (False, False)
        return self._session_flags[session_id]

    @staticmethod
    def _generate_hint(has_summary: bool, has_transcripts: bool) -> str:
        if not can_generate_context(has_summary, has_transcripts):
            return "Needs a transcript or a summary for the session first."
        if not has_summary:
            return "A session summary improves the result."
        return "Uses the session summary and what was said around the capture."

    def _update_actions(self):
        row = self.current_row()
        has_row = row is not None
        row_flags = self._flags_for(row.get('session_id', self._session_id)) if row else (False, False)
        own_flags = self._flags_for(self._session_id)
        missing = any(context_state(r) != STATE_AI for r in self._session_rows)
        self.generate_button.setEnabled(
            has_row and can_generate_context(*row_flags) and not self._busy)
        self.generate_missing_button.setEnabled(
            missing and can_generate_context(*own_flags) and not self._busy)
        self.generate_button.setToolTip(
            f"Describe the selected screenshot with AI. {self._generate_hint(*row_flags)}")
        self.generate_missing_button.setToolTip(
            "Describe every screenshot of this session that has no AI context yet. "
            f"{self._generate_hint(*own_flags)}")
        self.search_hint_button.setEnabled(self.generate_missing_button.isEnabled())
        self.open_folder_button.setEnabled(has_row)
        self.delete_button.setEnabled(has_row and not self._busy)
        self.edit_description_button.setEnabled(has_row)

    # ---- description editing ----------------------------------------------

    def _begin_description_edit(self):
        row = self.current_row()
        if row is None:
            return
        self.description_editor.setPlainText(row.get('description') or '')
        self._set_editing(True)
        self.description_editor.setFocus()

    def _end_description_edit(self):
        if hasattr(self, 'description_editor'):
            self._set_editing(False)

    def _set_editing(self, editing: bool):
        self.description_editor.setVisible(editing)
        self.save_description_button.setVisible(editing)
        self.cancel_description_button.setVisible(editing)
        self.edit_description_button.setVisible(not editing)

    def _save_description(self):
        row = self.current_row()
        if row is None:
            return
        text = self.description_editor.toPlainText().strip()
        try:
            self._db.update_screenshot_description(row['filepath'], text)
            # The auto preview is built from the description, so edits reach
            # the assistant's screenshot search right away (BU106).
            ensure_previews(self._db, row.get('session_id', self._session_id))
        except Exception as e:
            QMessageBox.warning(self, "Save Failed", f"Could not save the description: {e}")
            return
        self._end_description_edit()
        self.reload()
        self.screenshots_changed.emit(self._session_id)
        self._on_status("Screenshot description saved")

    # ---- AI context --------------------------------------------------------

    def _latest_summary(self, session_id: int) -> str:
        try:
            summaries = self._db.get_summaries(session_id)
        except Exception:
            return ""
        return summaries[-1].get('content', '') if summaries else ""

    def _set_busy(self, busy: bool, text: str = ""):
        self._busy = busy
        if busy:
            self.state_chip.setVisible(True)
            self.state_chip.set_state("busy", text or "Generating…")
        self._update_actions()
        if not busy:
            self._refresh_details()

    def _generate_selected(self):
        row = self.current_row()
        if row is None or self._busy:
            return
        session_id = row.get('session_id', self._session_id)
        try:
            transcripts = self._db.get_transcripts(session_id)
        except Exception:
            transcripts = []
        thread = ScreenshotContextThread(
            screenshot_path=row.get('filepath', ''),
            summary=self._latest_summary(session_id) or None,
            transcript_excerpt=transcript_window(transcripts, row.get('timestamp', 0)) or None,
            db=self._db,
        )
        thread.finished_signal.connect(self._on_context_generated)
        thread.error_signal.connect(self._on_context_error)
        self._set_busy(True, "Generating…")
        _keep_alive(thread)
        thread.start()

    def _on_context_generated(self, _context):
        self._set_busy(False)
        self.reload()
        self.screenshots_changed.emit(self._session_id)
        self._on_status("Generated context for the screenshot")

    def _on_context_error(self, message: str):
        self._set_busy(False)
        logger.error(f"Failed to generate screenshot context: {message}")
        QMessageBox.warning(self, "Context Generation Failed", message)

    def _generate_missing(self):
        if self._busy:
            return
        pending = [
            {'filepath': r.get('filepath', ''), 'timestamp': r.get('timestamp', 0),
             'session_id': self._session_id}
            for r in self._session_rows if context_state(r) != STATE_AI
        ]
        if not pending:
            return
        thread = ScreenshotContextBatchThread(
            pending, self._latest_summary(self._session_id), self._db)
        thread.progress_signal.connect(self._on_batch_progress)
        thread.finished_signal.connect(self._on_batch_finished)
        self._set_busy(True, f"Generating 0/{len(pending)}")
        _keep_alive(thread)
        thread.start()

    def _on_batch_progress(self, current: int, total: int, _context, _filepath: str):
        self.state_chip.set_state("busy", f"Generating {current}/{total}")

    def _on_batch_finished(self, results: list):
        self._set_busy(False)
        self.reload()
        self.screenshots_changed.emit(self._session_id)
        self._on_status(f"Generated context for {len(results)} screenshot(s)")

    # ---- file actions ------------------------------------------------------

    def _open_folder(self):
        row = self.current_row()
        if row is None:
            return
        path = os.path.normpath(row.get('filepath', ''))
        try:
            if sys.platform == 'win32' and os.path.exists(path):
                subprocess.Popen(f'explorer /select,"{path}"')
            else:
                QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(path)))
        except Exception as e:
            QMessageBox.warning(self, "Open Folder", f"Could not open the folder: {e}")

    def _delete_selected(self):
        row = self.current_row()
        if row is None or self._busy:
            return
        filepath = row.get('filepath', '')
        reply = QMessageBox.question(
            self,
            "Delete Screenshot",
            f"Delete screenshot #{row.get('id')}?\n\n{filepath}\n\n"
            "This removes it from the session and deletes the file.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        try:
            if row.get('id') is not None:
                self._db.delete_screenshot(row['id'])
            else:
                self._db.delete_screenshot_by_filepath(filepath)
            for path in (filepath, description_file_for(filepath)):
                if path and os.path.exists(path):
                    os.remove(path)
        except Exception as e:
            logger.error(f"Failed to delete screenshot: {e}")
            QMessageBox.warning(self, "Delete Failed", f"Failed to delete screenshot: {e}")
            return

        deleted_index = self._index
        self._thumbs.pop(filepath, None)
        self._session_rows = [r for r in self._session_rows if r is not row]
        self._rows = [r for r in self._rows if r is not row]
        self._populate_filmstrip()
        self._index = -1
        self.select(index_after_delete(deleted_index, len(self._rows)))
        self._update_search_status()
        self.screenshots_changed.emit(row.get('session_id', self._session_id))
        self._on_status("Screenshot deleted")

    # ---- window ------------------------------------------------------------

    def open_fullscreen(self):
        """Show the selected screenshot alone, full screen (double-click / F)."""
        row = self.current_row()
        if row is None:
            return
        if self._fullscreen is None:
            self._fullscreen = _FullscreenImage(self)
            self._fullscreen.navigate.connect(self.step)
            self._fullscreen.closed.connect(self._on_fullscreen_closed)
        self._show_in_fullscreen(row, _load_full_image(row.get('filepath', '')))
        self._fullscreen.showFullScreen()
        self._fullscreen.activateWindow()
        self._fullscreen.raise_()

    def _show_in_fullscreen(self, row: Dict[str, Any], pixmap: QPixmap):
        own_session = row.get('session_id', self._session_id) == self._session_id
        caption = (f"#{row.get('id')}   ·   "
                   f"{capture_meta(row, self._session_start if own_session else None)}   ·   "
                   f"{self._index + 1} / {len(self._rows)}")
        self._fullscreen.show_image(pixmap, caption, self._index > 0,
                                    0 <= self._index < len(self._rows) - 1)

    def _on_fullscreen_closed(self):
        self._fullscreen = None
        self.activateWindow()

    def show_help(self):
        """Explain every button, key and label of this window (? / F1)."""
        dialog = QDialog(self)
        dialog.setWindowTitle("Screenshots - Help")
        dialog.setStyleSheet("QDialog { background: #061946; }")
        dialog.setSizeGripEnabled(True)
        dialog.resize(600, 680)

        outer = QVBoxLayout(dialog)
        outer.setContentsMargins(0, 0, 0, 0)
        panel = PixelPanel()
        outer.addWidget(panel)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        layout.addWidget(PixelSectionTitle("HOW TO USE THIS WINDOW"))
        intro = QLabel("Click a section title to open or close it.")
        intro.setWordWrap(True)
        intro.setStyleSheet(_qss("#8EA7D8", 10))
        layout.addWidget(intro)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        container = QWidget()
        column = QVBoxLayout(container)
        column.setContentsMargins(0, 0, 6, 0)
        column.setSpacing(10)
        for title, body in help_sections(SCREENSHOT.get("global_hotkey", "")):
            section = PixelCollapsibleSection(title, body)
            section.set_scale(0.95)
            column.addWidget(section)
        column.addStretch(1)
        scroll.setWidget(container)
        layout.addWidget(scroll, 1)

        close_row = QHBoxLayout()
        close_row.addStretch(1)
        close_button = PixelButton("Got it")
        close_button.setFixedWidth(120)
        close_button.clicked.connect(dialog.accept)
        close_row.addWidget(close_button, 0)
        layout.addLayout(close_row)
        dialog.exec()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            if self.description_editor.isVisible():
                self._end_description_edit()
                return
            if self.search_input.text():
                self.search_input.clear()
                self._apply_filter()
                return
        super().keyPressEvent(event)

    def hideEvent(self, event):
        if not self.isFullScreen() and not self.isMaximized():
            ScreenshotViewer._remembered_size = self.size()
        ScreenshotViewer._remembered_splitter = self.splitter.sizes()
        super().hideEvent(event)
