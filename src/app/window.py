from PySide6.QtWidgets import (QMainWindow, QWidget, QVBoxLayout, 
                                QHBoxLayout, QPushButton, QLabel, QStatusBar,
                                QMessageBox, QApplication, QListWidget, QGroupBox,
                                QListWidgetItem, QMenu, QTableWidget, QTableWidgetItem,
                                QHeaderView, QComboBox, QDialog, QTextBrowser, QScrollArea, 
                                QGridLayout, QTextEdit, QCheckBox,
                                QFrame, QAbstractItemView, QSplitter, QLineEdit, QCompleter,
                                QToolButton, QToolBar, QBoxLayout, QSizePolicy, QInputDialog,
                                QTabWidget, QButtonGroup, QGraphicsOpacityEffect, QFileDialog,
                                QProgressBar)
from PySide6.QtCore import (Qt, QTimer, Slot, QThread, Signal,
                             QStringListModel, QSize, QPoint, QStandardPaths,
                             QPropertyAnimation, QEasingCurve, QObject, QEvent)
from PySide6.QtGui import (QAction, QPixmap, QColor, QIcon, QShortcut, QKeySequence, QFont)
from typing import Optional, Callable
from dataclasses import dataclass
from functools import partial
import hashlib
import logging
import os
import re
from datetime import datetime, timedelta

from .session_manager import SessionManager
from .pixel_theme import app_qss, asset_path
from . import theme
from .. import paths
from .pixel_widgets import (
    PixelPanel, PixelSectionTitle, PixelButton, PixelToolButton, PixelScopePrompt,
    PixelFindBar, PixelCollapsibleSection, aligned_bubble, aligned_bubble_with_time,
    bubble_of_row, NAVY_INNER, PixelConversationDelegate, PixelChatInput,
    parse_summary_sections, pixel_mini_button, PixelSessionCard, pixel_filter_chip,
    pixel_group_header, transcript_gap_separator, pixel_empty_hint,
    PixelAnswerCard, PixelCandidateCard,
    pixel_spend_chip, pixel_rail_notice, pixel_group_label,
    format_answer_html, PixelDropOverlay, PixelReferenceChip,
    PixelTitleBar, PixelResizeFrame, set_accent, enable_native_snap, snap_overhang,
    PixelDueDateCard, PixelDueDateList, PixelSearchResultCard, search_snippet,
)
from ..audio_capture.core import ChunkedAudioRecorder
from ..audio.importer import SUPPORTED_EXTENSIONS as UPLOAD_AUDIO_EXTENSIONS
from ..transcription.text_import import SUPPORTED_EXTENSIONS as UPLOAD_TRANSCRIPT_EXTENSIONS
from .session import Session
from ..summarization import SummaryGenerator
from ..config import (ASSISTANT_AGENTS, SESSION, SCREENSHOT, ALLOWED_MODELS,
                      LIVE_QA, REFERENCE_DOC, get_selected_model,
                      set_selected_model)
from ..assistant.service import AssistantAnswerService
from ..assistant.screenshot_contract import parse_screenshot_refs
from ..assistant.live_qa import (
    answer_instruction, build_question_for_candidate,
    build_question_from_records, strip_transcript_evidence, timestamp_range,
    QuestionDetector, mode_policy_reason,
)
from ..assistant.reference_doc import (
    ReferenceDocError, build_reference_block, is_allowed_extension,
    load_reference_text, select_relevant_excerpt,
)
from ..assistant.scope_offer import (
    format_scope_offer_prompt,
    decline_scope_offer as apply_scope_offer_decline,
)

# Labels for the candidate picker. Default is the original ambiguity-clarification
# flow; the "handoff" variant is reached from the in-chat scope prompt's
# "Choose another session" link (BU093).
# Markdown emphasis / heading / quote markers, dropped from search snippets.
_MARKDOWN_MARKS = re.compile(r"[*#`>]+")

_CANDIDATE_TITLE_DEFAULT = "SELECT A SESSION"
_CANDIDATE_BTN_DEFAULT = "Use Selected Session"
_CANDIDATE_TITLE_HANDOFF = "ASK IN SPECIFIC SESSION"
_CANDIDATE_BTN_HANDOFF = "Ask in Specific Session"

# BU118: the two mute toggles in the chat section's top-right corner. Each
# entry is (source, label, active icon, muted icon, fallback glyph).
_MUTE_TOGGLES = (
    ('mic', 'Microphone', 'icon_mic_on.svg', 'icon_mic_off.svg', '🎙'),
    ('system', 'System audio', 'icon_audio_on.svg', 'icon_audio_off.svg', '🔊'),
)
# BU119: the transcript source filter's stored values, in combo order.
TRANSCRIPT_FILTERS = ('all', 'mic', 'system')

_MUTE_BUTTON_SIZE = 34  # matches PixelToolButton(compact=True)
_MUTE_ROW_SPACING = 6
# Width of the toggle row, mirrored as a spacer on the other side of the search
# bar so the bar stays centred in the column.
_MUTE_ROW_WIDTH = len(_MUTE_TOGGLES) * _MUTE_BUTTON_SIZE + _MUTE_ROW_SPACING
from .screenshot_viewer import ScreenshotViewer
from .settings_dialog import SettingsDialog
from .calendar_dialog import DueDateSender
from ..calendar_sync.due_dates import parse_due_date_entries
from .global_hotkey import GlobalHotkey
from ..screenshots.hotkeys import ClipboardDeduper

logger = logging.getLogger(__name__)

# BU103: a live transcript bubble keeps growing through consecutive same-source
# chunks until either a real pause shows up or it's been running too long.
# A gap between one chunk's end and the next chunk's start bigger than ~1.5x
# the chunk duration means the VAD check upstream silently dropped a window -
# that dropped window *is* the pause (see docs/build_plan/BU103.md).
TRANSCRIPT_PAUSE_GAP_SECONDS = ChunkedAudioRecorder.CHUNK_DURATION * 1.5
TRANSCRIPT_MAX_GROUP_SECONDS = 60


def build_summary_section(title: str, body: str, link_for=None):
    """One summary section and its due-date cards (BU133).

    A "Due Dates" section with parsed entries gets a card per entry, each with
    its own Send to Calendar button; ``link_for(fingerprint)`` returns the
    stored calendar link for an entry, or None. Every other section keeps the
    rich-text body. Returns ``(section, cards)``.
    """
    entries = parse_due_date_entries(body) if title == "Due Dates" else []
    if not entries:
        return PixelCollapsibleSection(title, body), []
    cards = [
        PixelDueDateCard(entry, link_for(entry.fingerprint) if link_for else None)
        for entry in entries
    ]
    return PixelCollapsibleSection(title, body, body_widget=PixelDueDateList(cards)), cards


@dataclass
class TranscriptRecord:
    """One transcribed chunk, kept structured instead of pre-formatted (BU111).

    The display string is carried along in ``display_text`` so the BU101
    transcript download and the existing find/filter paths keep working
    unchanged, but ``start_dt`` / ``end_dt`` / ``transcript_id`` survive too,
    which is what lets a bubble be mapped back to the chunks it was built
    from.
    """

    text: str
    source: str  # 'mic' | 'system'
    start_dt: Optional[datetime]
    end_dt: Optional[datetime]
    transcript_id: Optional[int]
    display_text: str


def should_extend_group(group: Optional[dict], start_dt, end_dt) -> bool:
    """Whether a chunk starting at ``start_dt`` extends ``group``'s open bubble.

    The one grouping rule both transcript panels use (BU103, extracted in
    BU111): a chunk joins the source's currently open bubble while the pause
    before it stays under TRANSCRIPT_PAUSE_GAP_SECONDS and the bubble has not
    already been running for TRANSCRIPT_MAX_GROUP_SECONDS. Unknown timing
    (either datetime missing) always starts a new bubble, as before.
    """
    if group is None or start_dt is None or end_dt is None:
        return False
    last_end = group.get('last_end')
    group_start = group.get('start_dt')
    if last_end is None or group_start is None:
        return False
    gap = (start_dt - last_end).total_seconds()
    duration = (end_dt - group_start).total_seconds()
    return gap <= TRANSCRIPT_PAUSE_GAP_SECONDS and duration <= TRANSCRIPT_MAX_GROUP_SECONDS


# BU112: a silence longer than this between two bubbles gets its own separator
# row in the detached stream. Five grouped-bubble lifetimes is long enough that
# it is a real break in the room rather than someone pausing for breath.
TRANSCRIPT_GAP_SEPARATOR_SECONDS = TRANSCRIPT_MAX_GROUP_SECONDS * 5


def gap_separator_label(previous_end, next_start) -> Optional[str]:
    """Label for a break between two bubbles, or None if they sit close enough.

    Returns the resumption time ("10:42") for a same-day gap, and the date as
    well ("Sep 16 - 10:42") once the two sides fall on different days.
    """
    if previous_end is None or next_start is None:
        return None
    if (next_start - previous_end).total_seconds() <= TRANSCRIPT_GAP_SEPARATOR_SECONDS:
        return None
    if next_start.date() != previous_end.date():
        return next_start.strftime('%b %d - %H:%M')
    return next_start.strftime('%H:%M')


# BU112: pixels one wheel notch moves the detached transcript stream. With
# ScrollPerPixel the scrollbar's single step is what a notch multiplies, so a
# fixed value here makes a notch cover the same distance regardless of how tall
# the bubble under the cursor happens to be.
DETACHED_SCROLL_PIXELS_PER_STEP = 24

# The one menu look every popup in the detached window uses - the filter menu
# and the BU113 transcript context menu.
DETACHED_MENU_QSS = """
    QMenu {
        background-color: #071D52;
        border: 2px solid #3E6B9B;
        color: #FFFFFF;
        padding: 4px;
    }
    QMenu::item:selected {
        background-color: #3E6B9B;
    }
    QMenu::item:disabled {
        color: #6D7FB4;
    }
    QMenu::indicator {
        width: 14px;
        height: 14px;
    }
"""

# BU112: how wide a transcript bubble may grow in the detached window. The
# main panel's 250 px was sized for the old 320 px-wide detached window; in the
# two-column layout the transcript column is roughly three times that, and a
# 250 px bubble left most of it empty.
DETACHED_BUBBLE_MAX_WIDTH = 420

# BU115: the three detection modes, as one visible control rather than a mode
# plus a hidden sub-option. "Auto" used to mean auto-*detect* while answering
# stayed behind a menu toggle, which made the chip read as something it wasn't.
#
#   manual  - no detector at all, no spend; you pick chunks yourself
#   suggest - detect and offer cards; answering still costs only what you click
#   auto    - detect and answer immediately above min_confidence
#
# suggest is the default and stays it: the detector was validated against one
# lecture, which is not enough to answer unattended.
LIVE_QA_MODES = ('manual', 'suggest', 'auto')
LIVE_QA_DEFAULT_MODE = 'manual'


def normalize_live_qa_mode(value, auto_answer=None) -> str:
    """Coerce a stored mode to one of LIVE_QA_MODES.

    ``auto_answer`` migrates the older two-setting shape, where the mode was
    manual/auto and a separate flag decided whether candidates answered
    themselves: the old "auto" splits into "suggest" or "auto" depending on
    that flag. Preferences always carry the flag (it is written to agree with
    the mode), so passing it is how the loader reads a stored value; the mode
    chips call this without it, where "auto" simply means "auto".
    """
    if value == 'auto' and auto_answer is not None:
        # Either shape: the pre-split one, where "auto" only meant detect and
        # this flag decided whether it answered, or the current one, which
        # writes the flag to agree with the mode. Checked before the
        # pass-through, since "auto" is a valid mode in both.
        return 'auto' if auto_answer else 'suggest'
    if value in LIVE_QA_MODES:
        return value
    return LIVE_QA_DEFAULT_MODE


# BU116: how an answer is written, which is a different question from whether
# one is written at all. The detection modes above decide when to answer; these
# decide what the answer looks like, so they are a second control and not two
# more values of the first.
#
#   transcripts - session evidence first, general knowledge only as a
#                 labelled fallback
#   general     - answer directly, no evidence check
LIVE_QA_ANSWER_MODES = ('transcripts', 'general')
LIVE_QA_DEFAULT_ANSWER_MODE = LIVE_QA.get('answer_mode', 'transcripts')


def normalize_live_qa_answer_mode(value) -> str:
    """Coerce a stored answer mode to one of LIVE_QA_ANSWER_MODES."""
    if value in LIVE_QA_ANSWER_MODES:
        return value
    return LIVE_QA_DEFAULT_ANSWER_MODE


def reference_drop_error(paths) -> Optional[str]:
    """Why a dropped payload cannot be attached, or None when it can (BU117).

    Qt-free, so the rule is testable without a drag. Follows BU104's audio
    import in shape - an extension allow-list and a message fit for the status
    bar - rather than inventing a second validation vocabulary.
    """
    if not paths:
        return "Only a local .txt file can be attached"
    if len(paths) > 1:
        return "Drop one .txt file at a time"
    path = paths[0]
    if os.path.isdir(path):
        return "Folders cannot be attached - drop a .txt file"
    if not is_allowed_extension(path):
        allowed = ", ".join(REFERENCE_DOC.get('allowed_extensions', ['.txt']))
        return f"Only {allowed} files can be attached"
    return None


class DetachedTranscriptsDialog(QDialog):
    """The detached transcripts window, which also takes a dropped .txt (BU117).

    A plain QDialog cannot refuse a drop *visibly*: with no handlers it ignores
    the drag silently, and with Qt's default highlight it says only that
    something is being dragged. So the handlers live here, on the window
    itself, and drive a themed overlay that distinguishes "this will attach"
    from "this will not".

    The dialog only reports; MainWindow decides what to do with the file.
    """

    file_dropped = Signal(str)
    drop_rejected = Signal(str)

    # A drag we ignore may never deliver dragLeaveEvent, so the refusal
    # overlay clears itself rather than waiting for an event that is not
    # promised.
    REJECT_LINGER_MS = 1600

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self._drop_ready = False
        self._drop_overlay = PixelDropOverlay(self)
        self._drop_overlay.setGeometry(self.rect())
        self._reject_timer = QTimer(self)
        self._reject_timer.setSingleShot(True)
        self._reject_timer.timeout.connect(self._hide_drop_overlay)

    # --- drag and drop ---------------------------------------------------

    @staticmethod
    def _local_paths(mime) -> list:
        if mime is None or not mime.hasUrls():
            return []
        return [u.toLocalFile() for u in mime.urls() if u.isLocalFile()]

    def dragEnterEvent(self, event):
        paths = self._local_paths(event.mimeData())
        error = reference_drop_error(paths)
        if error:
            self._drop_ready = False
            self._drop_overlay.show_state(error.upper(), accepting=False)
            self._reject_timer.start(self.REJECT_LINGER_MS)
            self.drop_rejected.emit(error)
            event.ignore()
            return
        self._reject_timer.stop()
        self._drop_ready = True
        self._drop_overlay.show_state(
            f"DROP TO ATTACH {os.path.basename(paths[0]).upper()}"
        )
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
        error = reference_drop_error(paths)
        if error:
            self.drop_rejected.emit(error)
            event.ignore()
            return
        event.acceptProposedAction()
        self.file_dropped.emit(paths[0])

    def _hide_drop_overlay(self):
        self._reject_timer.stop()
        self._drop_ready = False
        self._drop_overlay.hide()

    def resizeEvent(self, event):
        # The overlay is a free child covering the whole window, so it has to
        # be resized by hand - it is deliberately not in the layout, which
        # belongs to the two columns.
        super().resizeEvent(event)
        self._drop_overlay.setGeometry(self.rect())

def resolve_chunk_selection(order, selected, anchor, clicked, modifiers,
                            extending: bool = False) -> tuple:
    """New (selection, anchor) after a click or drag onto chunk ``clicked``.

    Pure list math over ``order`` - the record indices of the chunks currently
    visible, in display order - so the gesture semantics are testable without a
    window. A *chunk* is the unit, not the bubble: a grouped bubble can hold a
    minute of speech, and the user asks about one thing inside it.

    - plain click replaces the selection with the clicked chunk;
    - ``extending`` (a sustained click dragged through the stream) selects the
      contiguous run from the anchor to wherever the cursor is now, so the
      selection follows the drag in both directions;
    - Ctrl+click toggles the clicked chunk and moves the anchor to it;
    - Ctrl+drag adds the dragged run to what was already selected;
    - Shift+click selects the run from the anchor, the same as a drag would.

    Chunks absent from ``order`` (hidden by a filter, or gone) drop out of the
    returned selection - which is what prunes a selection when a filter hides
    the bubble it was pointing at.
    """
    present = [i for i in order if i in set(selected)]
    if clicked not in order:
        return present, (anchor if anchor in order else None)

    ctrl = bool(modifiers & Qt.ControlModifier)
    shift = bool(modifiers & Qt.ShiftModifier)

    if (extending or shift) and anchor in order:
        lo, hi = sorted((order.index(anchor), order.index(clicked)))
        run = order[lo:hi + 1]
        if ctrl:
            # Ctrl+drag is additive: the run joins what was already held.
            keep = set(present) | set(run)
            return [i for i in order if i in keep], anchor
        return run, anchor

    if ctrl:
        if clicked in present:
            return [i for i in present if i != clicked], clicked
        keep = set(present) | {clicked}
        return [i for i in order if i in keep], clicked

    return [clicked], clicked


class BubbleClickFilter(QObject):
    """Routes mouse events on a transcript row to the window (BU113).

    Installed on the row and on every widget inside it, because an event lands
    on the QLabel holding the text, not on the row. Press, move and release are
    all forwarded: a sustained click dragged through the stream selects every
    chunk it touches, which is why move matters here and did not before.

    ``obj`` is passed along so the window can resolve *which* chunk inside a
    grouped bubble the event landed on.
    """

    _FORWARDED = (
        QEvent.MouseButtonPress,
        QEvent.MouseMove,
        QEvent.MouseButtonRelease,
    )

    def __init__(self, row, on_event, parent=None):
        super().__init__(parent)
        self._row = row
        self._on_event = on_event

    def eventFilter(self, obj, event):
        if event.type() in self._FORWARDED:
            try:
                return bool(self._on_event(self._row, obj, event))
            except RuntimeError:
                # The row was deleted while the filter was still attached.
                return False
        return False


class AssistantQueryThread(QThread):
    """Thread for running assistant queries asynchronously."""

    # Signals to communicate with the main thread
    finished_signal = Signal(object)  # Emits the AnswerResponse
    error_signal = Signal(str)  # Emits error message
    delta_signal = Signal(str)  # Emits the answer so far, while it streams

    def __init__(self, assistant_service, question, agent_id, explicit_scope, active_session_id, selected_session_id, conversation_id, system_instruction=None, persist=True, use_context=True, model=None, stream=False, reasoning_effort=None, max_tokens=None):
        super().__init__()
        self.max_tokens = max_tokens or None
        self.model = model or None
        self.stream = stream
        self.reasoning_effort = reasoning_effort or None
        # False sends no session context at all (General mode).
        self.use_context = use_context
        self.assistant_service = assistant_service
        self.question = question
        self.agent_id = agent_id
        self.explicit_scope = explicit_scope
        self.active_session_id = active_session_id
        self.selected_session_id = selected_session_id
        self.conversation_id = conversation_id
        # BU116: a live answer runs the chat panel's agent under a different
        # system instruction. None is the chat panel's own ask flow, unchanged.
        self.system_instruction = system_instruction
        # False keeps the answer out of the saved conversations (live answers).
        self.persist = persist

    def _effective_agent_id(self) -> str:
        """The agent id this query asks under.

        This is where the BU116 instruction override enters. ``ask_async``
        takes an agent id, not a system instruction, and widening it would mean
        editing ``assistant/service.py``, which is outside this BU - so the
        thread instead derives an agent from the chat panel's own (same model,
        same everything else, different prompt) and registers it in the
        service's agent registry under a private id. The id is a digest of the
        instruction, so two answer modes in flight at once get two entries and
        re-registering the same instruction rewrites the same value. Nothing
        lists these ids, so the agent picker never shows them.
        """
        if not self.system_instruction:
            return self.agent_id

        agents = getattr(self.assistant_service, '_agents', None)
        if agents is None:
            return self.agent_id
        base = agents.get(self.agent_id) or agents.get(
            ASSISTANT_AGENTS.get('default')) or {}
        digest = hashlib.sha1(self.system_instruction.encode('utf-8')).hexdigest()[:8]
        derived_id = f"{self.agent_id}::live:{digest}"
        agents[derived_id] = dict(base, system_instruction=self.system_instruction)
        return derived_id

    def run(self):
        """Run the async assistant query in a separate thread."""
        import asyncio

        agent_id = self._effective_agent_id()
        streamed = []

        def on_delta(text):
            streamed.append(text)
            self.delta_signal.emit("".join(streamed))

        async def run_query():
            return await self.assistant_service.ask_async(
                question=self.question,
                agent_id=agent_id,
                explicit_scope=self.explicit_scope,
                active_session_id=self.active_session_id,
                selected_session_id=self.selected_session_id,
                conversation_id=self.conversation_id,
                persist=self.persist,
                use_context=self.use_context,
                model=self.model,
                on_delta=on_delta if self.stream else None,
                reasoning_effort=self.reasoning_effort,
                max_tokens=self.max_tokens,
            )

        try:
            # Run the async function in a new event loop
            response = asyncio.run(run_query())
            self.finished_signal.emit(response)
        except Exception as e:
            self.error_signal.emit(str(e))
        finally:
            # assistant_service holds the DB and opened a connection for this
            # thread; release it before the thread exits (_db is private, but
            # this is the one place outside the service that needs it).
            db = getattr(self.assistant_service, '_db', None)
            if db is not None and hasattr(db, 'release_thread_connection'):
                db.release_thread_connection()


class RagBackfillThread(QThread):
    """Background thread for the BU091 corpus backfill / reindex.

    Never touches the UI directly - it only emits signals. The backfill itself
    is idempotent and resumable, so a killed thread leaves a repairable state.
    """

    progress_signal = Signal(int, int)   # (done, total)
    finished_signal = Signal(object)     # summary dict
    error_signal = Signal(str)

    def __init__(self, db, force: bool = False):
        super().__init__()
        self._db = db
        self._force = force

    def run(self):
        try:
            from ..rag.migration import backfill_corpus
            summary = backfill_corpus(
                self._db,
                progress_callback=lambda done, total: self.progress_signal.emit(done, total),
                force=self._force,
            )
            self.finished_signal.emit(summary)
        except Exception as e:  # noqa: BLE001
            self.error_signal.emit(str(e))
        finally:
            self._db.release_thread_connection()


class PaneFocusController:
    """Pure focus-state core for the three top-level panes.

    Tracks which of ``("left", "center", "right")`` is the active pane. No Qt,
    no widget access - ``MainWindow`` owns one and translates the returned
    change-set into ``PixelPanel.set_active`` calls.
    """

    PANES = ("left", "center", "right")

    def __init__(self):
        self.active: Optional[str] = None

    def set_active(self, name: str) -> set:
        """Make ``name`` the active pane.

        Returns the set of pane names whose active-ness changed: ``{old, new}``
        on a real switch, ``{new}`` from empty, or an empty set when the name is
        invalid or already active.
        """
        if name not in self.PANES or name == self.active:
            return set()
        changed = {name}
        if self.active is not None:
            changed.add(self.active)
        self.active = name
        return changed


def resolve_pane_for_widget(widget, shells: dict) -> Optional[str]:
    """Walk ``widget``'s parent chain until it reaches one of ``shells``' values.

    ``shells`` maps pane name -> shell widget. Returns the matching pane name or
    ``None`` if the widget is unparented / outside all shells.
    """
    node = widget
    while node is not None:
        for name, shell in shells.items():
            if node is shell:
                return name
        node = node.parent()
    return None


class FindController:
    """Qt-free / DB-free match engine for the in-pane find bar (BU099).

    ``MainWindow`` injects three callables that surface the searchable corpus:
    ``search_conversations(terms) -> list[int]`` (left mode), and
    ``list_center_texts()`` / ``list_right_texts()`` returning the per-row text
    in display order (center / right modes). Matching rule, shared by every
    mode: a row matches when *every* term is a case-insensitive substring of it.
    """

    MODES = ("left", "center", "right")

    def __init__(self, search_conversations, list_center_texts, list_right_texts):
        self._search_conversations = search_conversations
        self._list_center_texts = list_center_texts
        self._list_right_texts = list_right_texts
        self.mode = "center"
        self.query = ""
        self.matches = []
        self.cursor = -1

    @staticmethod
    def parse_terms(query):
        return [t for t in (query or "").lower().split() if t]

    def _recompute(self):
        terms = self.parse_terms(self.query)
        if not terms:
            self.matches = []
        elif self.mode == "left":
            self.matches = list(self._search_conversations(terms))
        else:
            texts = (
                self._list_center_texts()
                if self.mode == "center"
                else self._list_right_texts()
            )
            self.matches = [
                i for i, text in enumerate(texts)
                if all(term in (text or "").lower() for term in terms)
            ]
        self.cursor = 0 if self.matches else -1

    def set_mode(self, mode):
        if mode in self.MODES:
            self.mode = mode
            self._recompute()

    def set_query(self, query):
        self.query = query
        self._recompute()

    def current(self):
        if 0 <= self.cursor < len(self.matches):
            return self.matches[self.cursor]
        return None

    def next(self):
        if not self.matches:
            return None
        self.cursor = (self.cursor + 1) % len(self.matches)
        return self.current()

    def prev(self):
        if not self.matches:
            return None
        self.cursor = (self.cursor - 1) % len(self.matches)
        return self.current()

    def match_label(self):
        if not self.matches:
            return (0, 0)
        return (self.cursor + 1, len(self.matches))


class MainWindow(QMainWindow):
    """Main application window with session controls."""

    # Floor for the main window - small enough that the user can scale the
    # window freely, large enough that the collapsed-wing layout still fits.
    MAIN_MIN_SIZE = (760, 480)

    # Below this width the right transcripts wing is auto-collapsed even if
    # the window is technically maximized on a very small screen.
    RIGHT_WING_MIN_WINDOW_WIDTH = 1240

    # Text-scale presets for the summary window (BU100); index 1 is 100%.
    SUMMARY_SCALES = (0.85, 1.0, 1.2, 1.45, 1.75)

    # BU103: thread-safe hop for live transcription results (text, source,
    # timestamp_start, timestamp_end). A Qt signal - rather than
    # QMetaObject.invokeMethod/Q_ARG, which silently drops arguments beyond 3
    # in this PySide6 build - is used because Qt's own signal/slot argument
    # marshaling reliably carries all 4 strings across the audio thread ->
    # UI thread hop.
    _live_transcription_ready = Signal(str, str, str, str)

    # Perf rework: SessionManager now runs live transcription and
    # finalization (transcribe/index/summarize on Stop) on its own background
    # threads, so its status_callback / session_finalized_callback can fire
    # from a thread other than the UI's. These two signals are the only
    # thread-safe way in; _on_status_update / _on_session_finalized (the
    # callbacks SessionManager holds) do nothing but emit, and the real work
    # runs in the connected slot, on the UI thread, via QueuedConnection.
    # BU115: candidates arrive on the detector's own worker thread; this is
    # the only way they reach the UI, queued like every other cross-thread hop
    # in this window.
    _detected_ready = Signal(object)

    _status_ready = Signal(str, bool)
    _session_finalized_ready = Signal(int, object)  # (session_id, outcome dict)

    # Generic version of the same hop, for a SessionManager.submit_job()
    # completion callback (Transcribe / Summarize / Upload Audio): carries a
    # zero-arg callable to run on the UI thread instead of a fixed payload.
    # See _post_to_ui().
    _ui_callback_ready = Signal(object)

    def __init__(self):
        super().__init__()
        self.setWindowTitle('Chronicle')
        # Builds its own title bar (_create_central_widget). Must be flagged
        # before showMaximized() below polishes the window, or the app-wide
        # PixelWindowChrome pins a second bar over it at (0, 0) - off-screen
        # by the snap overhang once maximized.
        self.setProperty('pixelChrome', False)
        # The window is free to be dragged to any scale: the floor is only
        # what the smallest column combination physically needs. Below full
        # size the right transcripts wing auto-collapses (see
        # _sync_right_wing), which is what keeps the centre workspace usable
        # at small widths.
        self.setMinimumSize(*self.MAIN_MIN_SIZE)
        # Opens maximized; this is the size un-maximizing goes back to, so it
        # must fit the screen: ~80% of the work area, centred.
        screen = QApplication.primaryScreen()
        if screen is not None:
            avail = screen.availableGeometry()
            w = max(self.MAIN_MIN_SIZE[0], min(1400, int(avail.width() * 0.8)))
            h = max(self.MAIN_MIN_SIZE[1], min(900, int(avail.height() * 0.8)))
            self.setGeometry(avail.x() + (avail.width() - w) // 2,
                             avail.y() + (avail.height() - h) // 2, w, h)
        self.showMaximized()

        
        sessions_path = str(paths.sessions_dir())
        
        # Session manager
        self.session_manager: SessionManager = None
        
        # UI state
        self._is_recording = False
        
        # Detached transcription window
        self._detached_window = None
        
        # Detached assistant window
        self._detached_assistant_window = None
        
        # VAD settings
        self._vad_threshold = 30  # Default 30%
        self._vad_aggressiveness = 2  # Default mode 2
        
        # Flag to track if we're viewing historical transcripts (not live)
        self._viewing_historical_transcripts = False
        # BU138: the displayed session is an inserted (text-file) transcript.
        self._displaying_inserted_transcript = False

        # Transcription view components (for chat-like display)
        self._transcription_scroll_area = None
        self._transcription_container = None
        self._transcription_layout = None
        # BU111: structured chunks, not display strings. _transcription_history
        # stays available as a read-only property of display_text values for
        # the BU101 download and any other string consumer.
        self._transcript_records: list[TranscriptRecord] = []
        self._transcription_filter = 'all'  # Filter state: 'all', 'mic', or 'system'
        self._transcription_autoscroll = True  # Stick to bottom while the user hasn't scrolled up
        # BU103: one "open group" per source ('mic' / 'system') the live stream
        # is currently appending to - {row, bubble, start_dt, last_end_dt}.
        self._transcript_groups = {}
        # BU111: the detached window groups with the same rule but keeps its
        # own open-group state - one panel may be filtered while the other is
        # not, so they must not share a bubble.
        self._detached_transcript_groups = {}
        # BU112: end of the last chunk rendered in the detached stream, used to
        # decide whether the next one needs a gap separator above it.
        self._detached_last_end = None
        # BU113: record indices of the chunks the user has picked in the
        # detached stream (a grouped bubble holds several, and the chunk is the
        # unit), the anchor a drag or Shift+click ranges from, the live-drag
        # state, and the in-flight answer threads keyed by card id so two
        # answers can never populate each other's card.
        self._detached_selected_chunks = []
        self._detached_selection_anchor = None
        self._detached_drag_active = False
        self._detached_drag_modifiers = Qt.NoModifier
        self._detached_drag_base = []
        self._detached_answer_threads = {}
        self._detached_answer_cards = {}
        self._detached_answer_seq = 0
        # BU115: auto-mode detector state. Manual is the default and makes no
        # detector calls at all; "answer automatically" is a separate opt-in
        # on top of Auto, off until multi-session validation says otherwise.
        self._detected_ready.connect(self._on_candidates_detected, Qt.QueuedConnection)
        self._question_detector = None
        self._detached_candidate_cards = {}
        # Detections the mode policy hid this detector run, as (question,
        # reason): the spend chip shows them, so "found" never silently
        # disagrees with what is on screen.
        self._live_qa_hidden = []
        self._detached_cap_notice = None
        self._live_qa_mode = LIVE_QA_DEFAULT_MODE
        self._live_qa_answer_mode = LIVE_QA_DEFAULT_ANSWER_MODE
        # BU117: the reference document belongs to the open detached window and
        # to nothing else - not preferences, not the database, not the RAG
        # corpus. Closing the window drops it.
        self._reference_doc = None
        self._reference_chip = None
        self._live_qa_answer_mode_chips = {}
        self._live_qa_directed_only = False
        self._live_qa_window_chunks = LIVE_QA.get('window_chunks', 3)
        self._live_qa_detector_model = LIVE_QA.get('detector_model')
        self._live_transcription_ready.connect(
            self._append_transcription, Qt.QueuedConnection
        )
        self._status_ready.connect(self._apply_status_update, Qt.QueuedConnection)
        self._session_finalized_ready.connect(
            self._apply_session_finalized, Qt.QueuedConnection
        )
        self._ui_callback_ready.connect(self._run_ui_callback, Qt.QueuedConnection)

        # Assistant panel state
        self._current_question = None  # Store original question for retry
        self._current_candidates = []  # Store current candidates for selection
        self._candidate_list_widget = None  # List widget for candidate selection
        self._current_conversation_id = None  # Store conversation ID for follow-up questions
        self._screenshot_refs_shown = False  # "View screenshot" buttons appear once per chat
        self._thinking_message_widget = None  # Track "Thinking..." message for replacement
        self._assistant_thread = None  # Track active assistant query thread
        self._pending_scope_prompt = None  # Live BU093 in-chat scope offer (main view)
        self._pending_detached_scope_prompt = None  # (frame, yes_btn, no_btn) in detached view
        
        # Session search components
        self._recent_sessions = []  # Store sessions for dropdown

        # Perf rework: sessions currently finalizing (transcribe/index/
        # summarize) on SessionManager's background job thread, and the
        # currently-open All Sessions dialog if any (kept in sync when a
        # background finalize completes while it's open).
        self._finalizing_session_ids = set()
        self._all_sessions_dialog = None

        # BU110: open screenshot viewers (session_id -> viewer; non-modal, one
        # per session), the system-wide capture hotkey held while a session
        # is live, and the opt-in clipboard import of Win+Shift+S snips.
        self._screenshot_viewers = {}
        self._summary_windows = {}  # session_id -> open (non-modal) summary window
        self._viewers_hidden_for_capture = []
        self._capture_in_progress = False
        self._capture_hotkey = GlobalHotkey(lambda: int(self.winId()), parent=self)
        self._capture_hotkey.activated.connect(self._on_capture_hotkey)
        self._capture_hotkey_fallback = None
        self._capture_hotkey_failed_spec = None
        self._clipboard_deduper = ClipboardDeduper()
        if SCREENSHOT.get("import_clipboard_snips"):
            QApplication.clipboard().dataChanged.connect(self._on_clipboard_changed)

        # Right transcripts wing: hidden automatically whenever the window is
        # not at full size, restored when it is. _right_wing_user_hidden is the
        # separate, explicit reason to keep it hidden (the transcripts are
        # detached into their own window), which auto-restore must not undo.
        self._right_wing_user_hidden = False

        # The detached transcripts window is an ordinary window by default:
        # it only floats above everything else when the user pins it, and that
        # choice is remembered across detaches.
        self._detached_on_top = bool(
            self._read_preferences().get('detached_window_on_top', False)
        )

        # Sidebar collapse state. The collapsed rail keeps action/session icons visible
        # and gives the reclaimed horizontal space only to the answers viewport.
        self._sidebar_collapsed = False
        self._sidebar_expanded_min_width = 250
        self._sidebar_expanded_max_width = 278
        self._sidebar_collapsed_width = 86
        self._center_control_max_width = 1620  # Expanded horizontal width
        self._search_bar_max_width = 750  # Session scope/search bar at the top
        
        # Create UI components
        self._create_settings_actions()
        self._load_preferences()
        self._load_live_qa_preferences()
        self._create_central_widget()
        self._create_status_bar()
        self._sync_right_wing()
        
        # Initialize session manager
        self._init_session_manager(sessions_path)

    def eventFilter(self, obj, event):
        if (event.type() == event.Type.Resize
                and obj is getattr(self, '_detached_find_host', None)):
            self._position_detached_find_bar()
        if (event.type() == event.Type.Resize
                and getattr(self, '_detached_scroll_area', None) is not None
                and obj is self._detached_scroll_area.viewport()):
            self._apply_detached_bubble_width()

        """Clear the session-name display in the search bar when the user
        clicks into it to start a new search."""
        if event.type() == event.Type.MouseButtonPress:
            if obj is getattr(self, 'session_search_input', None) and getattr(self, '_search_input_shows_session', False):
                self._search_input_shows_session = False
                self.session_search_input.clear()
        return super().eventFilter(obj, event)

    @property
    def _transcription_history(self) -> list:
        """Display strings for the chunks currently on screen (BU111 shim).

        Read-only: the records in ``_transcript_records`` are the source of
        truth. Kept so the BU101 transcript download keeps writing exactly the
        lines it wrote before the records landed.
        """
        return [r.display_text for r in self._transcript_records]
    
    def keyPressEvent(self, event):
        """Handle key press events."""
        if event.key() == Qt.Key_Escape:
            # A visible find bar consumes Esc first (BU099).
            if getattr(self, "_find_bar", None) and self._find_bar.isVisible():
                self._close_find_bar()
                return
            # Clear scope when ESC is pressed
            self._clear_scope()
        else:
            super().keyPressEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._pad_snap_overhang()
        self._sync_right_wing()
        if getattr(self, "_find_bar", None) and self._find_bar.isVisible():
            self._position_find_bar()

    def moveEvent(self, event):
        super().moveEvent(event)
        # Maximizing resizes before it moves: the overhang is only right
        # once both have landed.
        self._pad_snap_overhang()

    def _pad_snap_overhang(self):
        """Maximized, the snap-enabled frame overhangs the screen edges;
        push the content back inside the visible work area."""
        layout = getattr(self, '_backdrop_layout', None)
        if layout is None:
            return
        o = snap_overhang(self)
        grip = PixelResizeFrame.GRIP
        layout.setContentsMargins(grip + o.left(), o.top(), grip + o.right(), grip + o.bottom())

    def showEvent(self, event):
        super().showEvent(event)
        # Aero Snap for the frameless window; re-applied on every show since
        # re-flagging the window recreates its native handle.
        enable_native_snap(self)

    def changeEvent(self, event):
        # Maximize / restore arrives as a window-state change, not always as a
        # resize, so the wing has to be re-evaluated here too.
        super().changeEvent(event)
        if event.type() == QEvent.WindowStateChange:
            self._sync_right_wing()
            if getattr(self, "title_bar", None):
                self.title_bar.sync_state()

    def _is_full_size(self) -> bool:
        """True when the window is maximized or full screen and wide enough
        to actually hold the three-column layout."""
        if not (self.isMaximized() or self.isFullScreen()):
            return False
        return self.width() >= self.RIGHT_WING_MIN_WINDOW_WIDTH

    def _sync_right_wing(self):
        """Show the right transcripts wing only at full size.

        Anything smaller gives its width back to the centre workspace, which
        is the column that has to stay usable while the window is scaled down.
        A wing hidden for an explicit reason - the transcripts are detached -
        stays hidden whatever the window size.
        """
        right_shell = getattr(self, 'right_shell', None)
        if right_shell is None:
            return
        should_show = (self._is_full_size()
                       and not getattr(self, '_right_wing_user_hidden', False))
        if right_shell.isVisible() != should_show:
            right_shell.setVisible(should_show)
    
    def _clear_scope(self):
        """Clear the current scope and reset to default state."""
        # Check if there's an active session
        has_active_session = False
        if self.session_manager:
            active_session = self.session_manager.get_active_session()
            has_active_session = active_session is not None
        
        # Suppress the redundant transcript clear/reload that setCurrentIndex
        # would trigger via _on_scope_changed; this method reloads explicitly.
        self._clearing_scope = True
        try:
            if has_active_session:
                # Case 1: There's an active session - set scope to "Specific Session"
                active_session = self.session_manager.get_active_session()
                self._selected_session_id = active_session.id
                self.scope_combo.setCurrentIndex(0)  # "Specific Session"
                self.session_search_input.setText(active_session.name)
                self._search_input_shows_session = True
                self._clear_transcription_view()
                self._load_transcripts_for_session(active_session.id, allow_live=True)
            else:
                # Case 2: No active session - clear selection and set scope to "Any Session"
                self._selected_session_id = None
                self._clear_transcription_view()

                # Close detached window if exists
                if hasattr(self, '_detached_window') and self._detached_window:
                    self._on_close_detached_window()

                # Reset scope combo to "Any Session" (index 1)
                self.scope_combo.setCurrentIndex(1)

                # Clear the session search input
                self.session_search_input.clear()
                self._search_input_shows_session = False
        finally:
            self._clearing_scope = False

        # Update the scope label
        self._update_scope_label()

        logger.info("Scope cleared via ESC key")

    def _init_session_manager(self, sessions_path: str):
        """Initialize the session manager.
        
        Args:
            sessions_path: Path to sessions directory
        """
        try:
            self.session_manager = SessionManager(
                base_path=sessions_path,
                db_path=str(paths.db_path()),
                status_callback=self._on_status_update,
                live_transcription_ui_callback=self._on_live_transcription,
                session_finalized_callback=self._on_session_finalized,
            )

            # Initialize Assistant Answer Service
            self.assistant_service = AssistantAnswerService(
                db=self.session_manager.db
            )

            # Track active/selected session for assistant
            self._active_session_id = None
            self._selected_session_id = None
            self._search_input_shows_session = False

            # BU119: the session/scope the user left behind is restored. The
            # chat deliberately is not - _load_past_conversations only lists
            # past conversations, and a launch always starts a new one.
            self._update_ui_state()
            self._load_past_conversations()
            self._refresh_session_completer()
            self._restore_session_scope()
            self._on_status_update('App Started')

            # Last, so its warning is the line left on the status bar rather
            # than being overwritten by 'App Started'. Nothing can record
            # before this point, so the mute is still in force for the first
            # session of the run.
            self._restore_muted_sources()

            # Finish any session the last run left mid-way (closed or
            # crashed while active/paused/processing, or stopped without
            # finalizing) - runs in the background, one at a time.
            try:
                pending = self.session_manager.finalize_pending_sessions()
                if pending:
                    self._finalizing_session_ids.update(pending)
                    self._on_status_update(
                        f"Finishing {len(pending)} session(s) left over from last time…"
                    )
            except Exception as e:
                logger.error(f"Failed to queue pending session finalization: {e}")
        except Exception as e:
            self._on_status_update(f'Failed to initialize: {str(e)}', is_error=True)
            QMessageBox.critical(self, 'Error', f'Failed to initialize: {str(e)}')
    
    def _load_past_sessions(self):
        """Load and display past sessions from the database."""
        try:
            sessions = self.session_manager.db.list_sessions()
            
            # Disconnect signal to prevent triggering during programmatic updates
            self.sessions_list.cellChanged.disconnect()
            
            self.sessions_list.setRowCount(len(sessions))
            
            for row, session in enumerate(sessions):
                trans_status = session.get('transcription_status', 'none')
                sum_status = session.get('summary_status', 'none')
                
                # FIX: Verify transcription status against actual transcripts in database
                # This corrects cases where transcription_status is 'none' but transcripts exist
                session_id = session['id']
                if trans_status == 'none':
                    actual_transcripts = self.session_manager.db.get_transcripts(session_id)
                    if actual_transcripts:
                        # Fix the inconsistent state in database
                        self.session_manager.db.update_session(session_id, transcription_status='transcribed')
                        trans_status = 'transcribed'
                
                # FIX: Verify summary status against actual summaries in database
                # This corrects cases where summary_status is 'none' but summaries exist
                if sum_status == 'none':
                    actual_summaries = self.session_manager.db.get_summaries(session_id)
                    if actual_summaries:
                        # Fix the inconsistent state in database
                        self.session_manager.db.update_session(session_id, summary_status='summarized')
                        sum_status = 'summarized'
                
                # Session name (editable)
                name_item = QTableWidgetItem(session['name'])
                name_item.setData(Qt.UserRole, session['id'])
                name_item.setFlags(name_item.flags() | Qt.ItemIsEditable)
                self.sessions_list.setItem(row, 0, name_item)
                
                # Transcription status (read-only)
                trans_item = QTableWidgetItem(trans_status)
                trans_item.setFlags(trans_item.flags() & ~Qt.ItemIsEditable)
                self.sessions_list.setItem(row, 1, trans_item)
                
                # Summary status (read-only)
                sum_item = QTableWidgetItem(sum_status)
                sum_item.setFlags(sum_item.flags() & ~Qt.ItemIsEditable)
                self.sessions_list.setItem(row, 2, sum_item)
                
                # Actions column with dropdown (only enabled when transcription_status is None or 'none')
                action_combo = QComboBox()
                action_combo.addItem("Select Action", "none")
                
                # Add transcribe option only if transcription status is None or 'none'
                if trans_status in (None, 'none'):
                    action_combo.addItem("Transcribe", "transcribe")
                
                # Add summarize option only if transcription is complete and summary is None or 'none'
                if trans_status == 'transcribed' and sum_status in (None, 'none'):
                    action_combo.addItem("Summarize", "summarize")
                
                # Add view summary option only if summary exists
                if sum_status == 'summarized':
                    action_combo.addItem("View Summary", "view_summary")
                
                # Set current index based on status
                if trans_status == 'transcribed' and sum_status == 'summarized':
                    action_combo.setCurrentIndex(0)  # Keep "Select Action" visible, show options on dropdown
                    action_combo.setEnabled(True)
                elif trans_status == 'transcribed' and sum_status in (None, 'none'):
                    # Has transcribe option at index 1, summarize at index 2
                    action_combo.setCurrentIndex(0)
                else:
                    action_combo.setCurrentIndex(0)
                
                action_combo.setProperty('session_id', session['id'])
                action_combo.currentIndexChanged.connect(lambda index, sid=session['id'], combo=action_combo: self._on_action_selected(sid, index, combo))
                self.sessions_list.setCellWidget(row, 3, action_combo)
            
            # Reconnect signal after loading
            self.sessions_list.cellChanged.connect(self._on_session_name_changed)
            
        except Exception as e:
            logger.warning(f"Failed to load past sessions: {str(e)}")
    
    def _load_past_conversations(self):
        """Load and display past conversations from the database."""
        try:
            conversations = self.session_manager.db.list_conversations()
            
            self.conversations_list.clear()
            
            for conv in conversations:
                conv_id = conv['id']
                session_id = conv.get('session_id')
                title = conv.get('title')
                created_at = conv.get('created_at', 0)
                updated_at = conv.get('updated_at', 0)
                
                # Pixel sidebar card: a two-line title over a small date, both
                # laid out by PixelConversationDelegate (which also elides).
                date_str = datetime.fromtimestamp(updated_at).strftime("%d %b %Y · %H:%M")
                title_text = " ".join((title or f"Conversation #{conv_id}").split())

                item = QListWidgetItem(title_text)
                item.setData(PixelConversationDelegate.DATE_ROLE, date_str)
                item.setData(Qt.UserRole, conv_id)
                scope_hint = "Session-specific" if session_id else "All sessions"
                item.setToolTip(f"{title_text}\n{scope_hint}")
                self.conversations_list.addItem(item)
            
            # Force UI update
            self.conversations_list.repaint()
            logger.info(f"Loaded {len(conversations)} conversations")
            
        except Exception as e:
            logger.warning(f"Failed to load past conversations: {str(e)}")
    
    def _on_conversation_selected(self, item):
        """Handle the selection of a conversation from the list."""
        conv_id = item.data(Qt.UserRole)
        if conv_id is None:
            return
        self._load_conversation(conv_id)

    def _load_conversation(self, conv_id):
        """Load a past conversation into the assistant panel by its id."""
        # Load this conversation in the assistant panel
        # First, get the conversation messages
        try:
            messages = self.session_manager.db.get_messages(conv_id)
            
            # Clear the current answer display
            self._answer_container = QWidget()
            self._answer_layout = QVBoxLayout(self._answer_container)
            self._answer_layout.setSpacing(24)
            self._answer_layout.setContentsMargins(42, 18, 42, 18)
            self._answer_layout.addStretch()
            # A reloaded conversation never re-creates the transient scope prompt (BU093)
            self._pending_scope_prompt = None
            # Only the first answer that pointed at a screenshot keeps its
            # pointer line; the chat then offers no more "View" buttons.
            self._screenshot_refs_shown = False

            # Display messages in the conversation view using the same method as new messages
            for msg in messages:
                role = msg.get('role', 'unknown')
                content = msg.get('content', '')
                if role == 'assistant':
                    stripped, _ = parse_screenshot_refs(content, ())
                    if stripped != content:
                        if self._screenshot_refs_shown:
                            content = stripped
                        self._screenshot_refs_shown = True

                # Use the same method as new messages to ensure proper formatting
                self._add_message_to_conversation(role, content)
            
            # Update the scroll area
            self._answer_scroll_area.setWidget(self._answer_container)
            
            # Store conversation ID for follow-up questions
            self._current_conversation_id = conv_id
            
            self._on_status_update(f"Loaded conversation #{conv_id}")
            
        except Exception as e:
            logger.error(f"Failed to load conversation: {str(e)}")
            self._on_status_update(f"Error loading conversation: {str(e)}", is_error=True)
    
    def _show_conversation_context_menu(self, position):
        """Show a right-click menu for a past-conversation sidebar item."""
        item = self.conversations_list.itemAt(position)
        if item is None:
            return

        menu = QMenu(self.conversations_list)
        menu.setStyleSheet(self._ALL_SESSIONS_MENU_QSS)
        delete_action = menu.addAction("Delete")

        chosen_action = menu.exec(self.conversations_list.mapToGlobal(position))
        if chosen_action == delete_action:
            self._delete_conversation(item)

    def _delete_conversation(self, item):
        """Permanently delete a past conversation and every trace of it."""
        conv_id = item.data(Qt.UserRole)
        if conv_id is None:
            return

        reply = QMessageBox.question(
            self,
            'Delete Conversation',
            "Permanently delete this conversation? This removes it and all its "
            "messages from the database and cannot be undone.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return

        try:
            self.session_manager.db.delete_conversation(conv_id)

            # Drop any in-memory references the answer service holds.
            service = getattr(self, 'assistant_service', None)
            if service is not None and hasattr(service, 'forget_conversation'):
                try:
                    service.forget_conversation(conv_id)
                except Exception:
                    logger.warning(
                        f"forget_conversation({conv_id}) failed", exc_info=True
                    )

            # If the deleted conversation is the one on screen, reset the panel.
            if self._current_conversation_id == conv_id:
                self._on_new_chat_clicked()

            self._load_past_conversations()
            logger.info(f"Deleted conversation {conv_id}")
            self._on_status_update("Conversation deleted.")

        except Exception as e:
            logger.error(f"Failed to delete conversation {conv_id}: {str(e)}")
            QMessageBox.warning(
                self, 'Delete Failed',
                f"Could not delete the conversation: {str(e)}"
            )

    def _on_session_name_changed(self, row, column):
        """Handle the renaming of a session."""
        if column != 0:
            return  # Only allow editing the name column
        
        try:
            item = self.sessions_list.item(row, 0)
            session_id = item.data(Qt.UserRole)
            new_name = item.text()
            
            if session_id is None:
                return
            
            # Update the database
            self.session_manager.db.update_session(session_id, name=new_name)
            
            logger.info(f"Renamed session {session_id} to '{new_name}'")
            
        except Exception as e:
            logger.error(f"Failed to rename session: {str(e)}")
            self._on_status_update(f"Error renaming session.", is_error=True)
    
    def _on_action_selected(self, session_id: int, index: int, combo: QComboBox):
        """Handle the action selection from the dropdown."""
        if index == 0:  # "Select Action" - do nothing
            return
        
        action = combo.currentData()
        
        if action == "transcribe":
            # Disable the combo to prevent multiple clicks
            combo.setEnabled(False)
            self._on_transcribe_clicked(session_id, combo)
        elif action == "summarize":
            # Disable the combo to prevent multiple clicks
            combo.setEnabled(False)
            self._on_summarize_clicked(session_id, combo)
        elif action == "view_summary":
            # Find the row for this session_id and show summary
            for r in range(self.sessions_list.rowCount()):
                item = self.sessions_list.item(r, 0)
                if item and item.data(Qt.UserRole) == session_id:
                    self._show_session_summary(r)
                    break
    
    def _on_transcribe_clicked(self, session_id: int, combo: QComboBox):
        """Handle the transcribe action for a session."""
        try:
            self._run_transcription(session_id, combo)
        except Exception as e:
            logger.error(f"Failed to start transcription: {str(e)}")
            self._on_status_update(f"Error: {str(e)}", is_error=True)
            # Reload to reset state
            self._refresh_session_completer()

    def _run_transcription(self, session_id: int, combo: Optional[QComboBox] = None,
                           on_done: Optional[Callable[[], None]] = None,
                           dialog: Optional[QDialog] = None):
        """Transcribe a session's audio that has no transcript yet, then index it.

        Runs on SessionManager's background job thread and returns at once -
        this used to load a Parakeet model and run inference synchronously on
        the UI thread. TranscriptionProcessor skips any chunk that already has
        a transcript row, so this stays cheap when live transcription already
        covered the session.

        Args:
            session_id: Session to transcribe.
            combo: Unused; kept for callers still passing it.
            on_done: Called on the UI thread after the built-in status/dialog
                handling, whether the job succeeded or failed (e.g. to refresh
                an All Sessions card).
            dialog: The All Sessions window, when started from there. Its
                progress bar then follows the job chunk by chunk and the
                card's Transcript chip stays busy across list reloads.
        """
        def progress_ui(text, percent):
            try:
                dialog._busy_update(text, percent)
            except RuntimeError:  # window closed meanwhile
                pass

        def on_progress(finished, total):
            # Job thread. Chunks map onto 5-95% so the bar never looks done
            # (or untouched) before the job really ends.
            percent = 5 + 90 * finished / total if total else 95
            text = f"Transcribing chunk {finished}/{total}…" if total else "Transcribing…"
            self._post_to_ui(lambda: progress_ui(text, percent))

        if dialog is not None:
            dialog._mark_busy(session_id, 'transcript_chip', 'Transcribing')
            dialog._busy_begin("Transcribing…", 5)

        def done(outcome, error):
            def apply():
                if dialog is not None:
                    try:
                        dialog._clear_busy(session_id)
                        dialog._busy_end()
                    except RuntimeError:
                        pass
                if error is not None:
                    logger.error(f"Transcription failed: {error}")
                    self._on_status_update(f"Transcription failed: {error}", is_error=True)
                    QMessageBox.warning(self, 'Transcription Failed', str(error))
                elif outcome.get('has_transcripts'):
                    count = outcome.get('transcribed', 0)
                    self._on_status_update(
                        f"Transcribed {count} audio chunk(s)" if count else 'Already transcribed'
                    )
                else:
                    self._on_status_update('No audio files found to transcribe')
                self._refresh_session_completer()
                if on_done:
                    on_done()
            self._post_to_ui(apply)

        self.session_manager.submit_job(
            f'transcribe session {session_id}',
            lambda: self.session_manager.transcribe_session(
                session_id, progress=on_progress if dialog is not None else None),
            done,
        )

    def _on_summarize_clicked(self, session_id: int, combo: QComboBox):
        """Handle the summarize action for a session."""
        try:
            self._run_summarization(session_id, combo)
        except Exception as e:
            logger.error(f"Failed to start summarization: {str(e)}")
            self._on_status_update(f"Error: {str(e)}", is_error=True)
            # Reload to reset state
            self._refresh_session_completer()

    def _run_summarization(self, session_id: int, combo: Optional[QComboBox] = None,
                           on_done: Optional[Callable[[], None]] = None):
        """Generate and store a summary, then re-index. Runs on
        SessionManager's background job thread and returns at once.

        Args:
            session_id: Session to summarize.
            combo: Unused; kept for callers still passing it.
            on_done: Called on the UI thread after the built-in status/dialog
                handling, whether the job succeeded or failed.
        """
        def done(result, error):
            def apply():
                if isinstance(error, ValueError) and 'transcript' in str(error).lower():
                    self._on_status_update('No transcript text found')
                    QMessageBox.warning(
                        self, 'No Transcripts', 'No transcripts available. Please transcribe first.'
                    )
                elif error is not None:
                    logger.error(f"Summarization failed: {error}")
                    self._on_status_update(f"Summarization failed: {error}", is_error=True)
                    QMessageBox.warning(self, 'Summarization Failed', str(error))
                else:
                    self._on_status_update('Summary generated')
                    self._update_summary_icon_state()
                self._refresh_session_completer()
                if on_done:
                    on_done()
            self._post_to_ui(apply)

        self.session_manager.submit_job(
            f'summarize session {session_id}',
            lambda: self.session_manager.summarize_session(session_id),
            done,
        )
    
    def _show_session_context_menu(self, position):
        """Show a context menu for the right-clicked session item."""
        # Get row from position
        row = self.sessions_list.row(self.sessions_list.itemAt(position))
        
        if row < 0:
            return
        
        menu = QMenu()
        view_summary_action = menu.addAction("View Summary")
        view_screenshots_action = menu.addAction("View Screenshots")
        menu.addSeparator()
        delete_action = menu.addAction("Delete Session")
        
        chosen_action = menu.exec(self.sessions_list.mapToGlobal(position))
        
        if chosen_action == view_summary_action:
            self._show_session_summary(row)
        elif chosen_action == view_screenshots_action:
            self._show_session_screenshots(row)
        elif chosen_action == delete_action:
            self._delete_session(row)
    
    def _delete_session(self, row):
        """Delete the selected session after confirmation."""
        try:
            session_id = self.sessions_list.item(row, 0).data(Qt.UserRole)
            session_name = self.sessions_list.item(row, 0).text()
            
            if session_id is None:
                return
            
            reply = QMessageBox.question(
                self,
                'Delete Session',
                f"Are you sure you want to permanently delete '{session_name}'?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )
            
            if reply == QMessageBox.Yes:
                self._purge_session_data(session_id)
                self.sessions_list.removeRow(row)

                logger.info(f"Deleted session {session_id} ('{session_name}')")
                self._on_status_update(f"Session '{session_name}' deleted.")

        except Exception as e:
            logger.error(f"Failed to delete session: {str(e)}")
            self._on_status_update(f"Error deleting session: {str(e)}", is_error=True)

    def _purge_session_data(self, session_id: int) -> None:
        """Permanently remove a session: every DB row plus its on-disk folder.

        Covers transcripts, screenshots (rows + image files), summaries, the
        RAG index, the session-router profile, assistant chat history, and the
        audio recordings under ``sessions/session_XXX/``.
        """
        # Database rows (transactional).
        self.session_manager.db.purge_session(session_id)

        # On-disk assets: audio/, screenshots/, transcripts/ for this session.
        import shutil
        try:
            session_dir = self.session_manager._get_session_path(session_id)
            if session_dir.exists():
                shutil.rmtree(session_dir, ignore_errors=True)
        except Exception as e:
            logger.warning(f"Could not remove session folder for {session_id}: {e}")

    def _delete_session_by_id(self, session_id: int) -> bool:
        """Delete a session by its ID, including all of its data on disk and in
        the database. Returns True if the session was actually deleted.
        """
        try:
            # Get session name first
            sessions = self.session_manager.db.list_sessions()
            session_name = None
            for s in sessions:
                if s['id'] == session_id:
                    session_name = s['name']
                    break

            if session_name is None:
                return False

            reply = QMessageBox.question(
                self,
                'Delete Session',
                f"Permanently delete '{session_name}'?\n\n"
                "This removes its transcripts, summary, screenshots, audio "
                "recordings and search index. This cannot be undone.",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )

            if reply == QMessageBox.Yes:
                self._purge_session_data(session_id)

                logger.info(f"Deleted session {session_id} ('{session_name}')")
                self._on_status_update(f"Session '{session_name}' deleted.")

                # Refresh completer
                self._refresh_session_completer()
                return True
            return False

        except Exception as e:
            logger.error(f"Failed to delete session: {str(e)}")
            self._on_status_update(f"Error deleting session: {str(e)}", is_error=True)
            QMessageBox.critical(self, 'Error', 'Could not delete the session from the database.')
            return False
    
    def _on_session_double_clicked(self, row, column):
        """Handle double-click on a session row to view summary."""
        self._show_session_summary(row)
    
    def _open_summary_window(self, session_id: int, session_name: str) -> bool:
        """Open the summary for a session as a collapsible, scalable view (BU100).

        Every entry point into the summary window goes through here. Returns
        False (after informing the user) when the session has no summary.

        Non-modal and one per session (see _present_window), so it never
        blocks an open screenshot viewer.
        """
        existing = self._summary_windows.get(session_id)
        if existing is not None:
            self._present_window(existing)
            return True
        try:
            summaries = self.session_manager.db.get_summaries(session_id)

            if not summaries:
                QMessageBox.information(
                    self,
                    'No Summary',
                    f"Session '{session_name}' does not have a summary yet.\n\n"
                    "Please transcribe and summarize the session first."
                )
                return False

            # Oldest-first ordering from the database; index 0 is the summary
            # the previous plain-text view showed.
            summary = summaries[0]
            summary_content = summary.get('content', '')
            summary_type = summary.get('summary_type', 'full')
            model_used = summary.get('model_used', 'unknown')
            created_at = summary.get('created_at')

            dialog = QDialog(self)
            dialog.setWindowTitle(f"Summary - {session_name}")
            dialog.setStyleSheet("QDialog { background: #0E2A6B; }")
            dialog.setWindowFlag(Qt.WindowMaximizeButtonHint, True)
            dialog.setSizeGripEnabled(True)
            # Floor is set by the bottom control row, which keeps a fixed size
            # at every text scale.
            dialog.setMinimumSize(560, 400)
            dialog.resize(getattr(self, '_summary_window_size', None) or QSize(780, 660))

            outer = QVBoxLayout(dialog)
            outer.setContentsMargins(0, 0, 0, 0)
            panel = PixelPanel()
            outer.addWidget(panel)

            layout = QVBoxLayout(panel)
            layout.setContentsMargins(16, 14, 16, 14)
            layout.setSpacing(10)

            # Title row: heading on the left, text-scale stepper on the right.
            title_row = QHBoxLayout()
            title_row.setSpacing(8)
            title_row.addWidget(PixelSectionTitle("SESSION SUMMARY"), 1)
            zoom_out_button = pixel_mini_button("A-", "Smaller text (Ctrl+-)")
            zoom_label = QLabel("100%")
            zoom_label.setAlignment(Qt.AlignCenter)
            zoom_label.setFixedWidth(56)
            zoom_label.setStyleSheet("QLabel { color: #8EA7D8; background: transparent; }")
            zoom_in_button = pixel_mini_button("A+", "Larger text (Ctrl++)")
            title_row.addWidget(zoom_out_button, 0)
            title_row.addWidget(zoom_label, 0)
            title_row.addWidget(zoom_in_button, 0)
            layout.addLayout(title_row)

            name_label = QLabel(session_name)
            name_label.setWordWrap(True)
            name_label.setStyleSheet("QLabel { color: #FFF0BF; background: transparent; }")
            layout.addWidget(name_label)

            meta_bits = [f"Type: {summary_type}", f"Model: {model_used}"]
            if created_at:
                from datetime import datetime
                meta_bits.append(
                    datetime.fromtimestamp(created_at).strftime('%Y-%m-%d %H:%M')
                )
            meta_label = QLabel("   ·   ".join(meta_bits))
            meta_label.setWordWrap(True)
            meta_label.setStyleSheet("QLabel { color: #8EA7D8; background: transparent; }")
            layout.addWidget(meta_label)

            sections_data = parse_summary_sections(summary_content)

            # Scrolling column of collapsible sections.
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            container = QWidget()
            container_layout = QVBoxLayout(container)
            container_layout.setContentsMargins(0, 0, 6, 0)
            container_layout.setSpacing(12)

            db = self.session_manager.db

            def link_for(fingerprint):
                try:
                    return db.get_calendar_link(session_id, fingerprint)
                except Exception as e:  # noqa: BLE001 - the card just shows "Send"
                    logger.warning(f"Could not read calendar link: {e}")
                    return None

            sections = []
            due_sender = None
            for title, body in sections_data:
                section, due_cards = build_summary_section(title, body, link_for)
                if due_cards and due_sender is None:
                    session_row = db.get_session(session_id) or {}
                    started = session_row.get('start_time')
                    due_sender = DueDateSender(
                        self, db, session_id, session_name,
                        datetime.fromtimestamp(started).date() if started else None,
                        dialog,
                    )
                for card in due_cards:
                    due_sender.attach(card)
                sections.append(section)
                container_layout.addWidget(section)

            if not sections:
                empty_label = QLabel("This summary is empty.")
                empty_label.setAlignment(Qt.AlignCenter)
                empty_label.setStyleSheet("QLabel { color: #8EA7D8; background: transparent; }")
                container_layout.addWidget(empty_label)

            container_layout.addStretch(1)
            scroll.setWidget(container)
            layout.addWidget(scroll, 1)

            if due_sender is not None:
                # "Event created ... Open" after a due date is sent (BU133).
                calendar_status = QLabel()
                calendar_status.setTextFormat(Qt.RichText)
                calendar_status.setOpenExternalLinks(True)
                calendar_status.setWordWrap(True)
                calendar_status.setStyleSheet(
                    "QLabel { color: #FFE9A8; background: transparent; }")
                calendar_status.hide()
                layout.addWidget(calendar_status)

                def show_created(link):
                    import html
                    text = "Event created in Google Calendar."
                    href = (link or {}).get('html_link')
                    if href:
                        text += (f' <a href="{html.escape(href, quote=True)}"'
                                 ' style="color:#FFF0BF;">Open</a>')
                    calendar_status.setText(theme.remap(text))
                    calendar_status.show()

                due_sender.event_created.connect(show_created)

            # Bottom bar: deploy / undeploy everything, then close.
            bottom_row = QHBoxLayout()
            bottom_row.setSpacing(8)
            expand_button = pixel_mini_button("Expand all", width=150, height=42)
            collapse_button = pixel_mini_button("Collapse all", width=150, height=42)
            expand_button.clicked.connect(
                lambda: [s.set_expanded(True) for s in sections]
            )
            collapse_button.clicked.connect(
                lambda: [s.set_expanded(False) for s in sections]
            )
            bottom_row.addWidget(expand_button, 0)
            bottom_row.addWidget(collapse_button, 0)
            bottom_row.addStretch(1)
            close_button = PixelButton("Close")
            close_button.setFixedWidth(120)
            close_button.clicked.connect(dialog.close)
            bottom_row.addWidget(close_button, 0)
            layout.addLayout(bottom_row)

            # Text scale, kept for the rest of the app session so the user sets
            # their comfortable size once.
            scale_index = getattr(self, '_summary_scale_index', 1)

            def apply_scale(index):
                self._summary_scale_index = index
                scale = self.SUMMARY_SCALES[index]
                for section in sections:
                    section.set_scale(scale)
                # Sizes must come from each widget's own stylesheet: the
                # app-wide QSS sets a font-size for QWidget, which beats setFont.
                name_label.setStyleSheet(
                    "QLabel { color: #FFF0BF; background: transparent;"
                    f" font-family: 'Courier New'; font-size: {13 * scale:.1f}pt;"
                    " font-weight: 700; }"
                )
                meta_label.setStyleSheet(
                    "QLabel { color: #8EA7D8; background: transparent;"
                    f" font-family: 'Courier New'; font-size: {10 * scale:.1f}pt; }}"
                )
                zoom_label.setText(f"{round(scale * 100)}%")
                zoom_out_button.setEnabled(index > 0)
                zoom_in_button.setEnabled(index < len(self.SUMMARY_SCALES) - 1)

            def step_scale(delta):
                index = max(
                    0,
                    min(len(self.SUMMARY_SCALES) - 1,
                        getattr(self, '_summary_scale_index', 1) + delta)
                )
                apply_scale(index)

            zoom_out_button.clicked.connect(lambda: step_scale(-1))
            zoom_in_button.clicked.connect(lambda: step_scale(1))
            QShortcut(QKeySequence("Ctrl+="), dialog).activated.connect(lambda: step_scale(1))
            QShortcut(QKeySequence("Ctrl++"), dialog).activated.connect(lambda: step_scale(1))
            QShortcut(QKeySequence("Ctrl+-"), dialog).activated.connect(lambda: step_scale(-1))
            QShortcut(QKeySequence("Ctrl+0"), dialog).activated.connect(lambda: apply_scale(1))
            apply_scale(scale_index)

            # Remember the size the user left the window at.
            dialog.finished.connect(
                lambda _: setattr(self, '_summary_window_size', dialog.size())
            )

            dialog.setAttribute(Qt.WA_DeleteOnClose, True)
            dialog.destroyed.connect(
                lambda _=None, sid=session_id: self._summary_windows.pop(sid, None)
            )
            self._summary_windows[session_id] = dialog
            self._present_window(dialog)
            return True

        except Exception as e:
            logger.error(f"Failed to show summary: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            QMessageBox.critical(
                self,
                'Error',
                f"Failed to load summary: {str(e)}"
            )
            return False

    @staticmethod
    def _present_window(window: QDialog):
        """Show a summary / screenshot window in front of everything else.

        These windows are non-modal, so several can stay open side by side
        and none blocks another. The exception is when a modal dialog (All
        Sessions) is up: a non-modal window would open behind it and ignore
        input, so it becomes modal too and stacks on top until closed.
        """
        if not window.isVisible() and QApplication.activeModalWidget() is not None:
            window.setWindowModality(Qt.ApplicationModal)
        if window.isMinimized():
            window.showNormal()
        window.show()
        window.raise_()
        window.activateWindow()

    def _show_session_summary(self, row):
        """Show the summary for a session in a separate window."""
        session_id = self.sessions_list.item(row, 0).data(Qt.UserRole)
        session_name = self.sessions_list.item(row, 0).text()
        summary_status = self.sessions_list.item(row, 2).text()

        if session_id is None:
            return

        if summary_status != 'summarized':
            QMessageBox.information(
                self,
                'No Summary',
                f"Session '{session_name}' does not have a summary yet.\n\n"
                "Please transcribe and summarize the session first."
            )
            return

        self._open_summary_window(session_id, session_name)

    def _show_summary_by_session_id(self, session_id: int, session_name: str):
        """Show the summary for a session by session_id.

        Args:
            session_id: The session ID
            session_name: The session name
        """
        self._open_summary_window(session_id, session_name)

    def _open_screenshot_viewer(self, session_id: int, session_name: str,
                                focus_screenshot_id: Optional[int] = None):
        """Open the screenshot viewer for a session (BU109).

        Every screenshot entry point goes through here. The viewer is
        non-modal - the main window stays usable while a session records -
        and there is one per session: asking again brings it to the front
        (BU110).
        """
        try:
            viewer = self._screenshot_viewers.get(session_id)
            if viewer is not None:
                if focus_screenshot_id is not None:
                    viewer.focus_screenshot(focus_screenshot_id)
                self._present_window(viewer)
                return

            viewer = ScreenshotViewer(
                self.session_manager.db,
                session_id,
                session_name,
                parent=self,
                focus_screenshot_id=focus_screenshot_id,
                on_status=self._on_status_update,
            )
            viewer.setAttribute(Qt.WA_DeleteOnClose, True)
            viewer.destroyed.connect(
                lambda _=None, sid=session_id: self._screenshot_viewers.pop(sid, None)
            )
            self._screenshot_viewers[session_id] = viewer
            self._present_window(viewer)
        except Exception as e:
            logger.error(f"Failed to show screenshots: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            QMessageBox.critical(self, 'Error', f"Failed to load screenshots: {str(e)}")

    def _show_screenshots_by_session_id(self, session_id: int, session_name: str):
        """Show the screenshots of a session, or say it has none. The live
        session always opens, so new captures show up as they are taken."""
        try:
            has_screenshots = bool(self.session_manager.db.get_screenshots(session_id))
        except Exception:
            has_screenshots = True  # let the viewer report the failure
        live = self._live_session_state()
        if not has_screenshots and not (live and live[0] == session_id):
            QMessageBox.information(
                self,
                'No Screenshots',
                f"Session '{session_name}' does not have any screenshots."
            )
            return
        self._open_screenshot_viewer(session_id, session_name)

    def _show_session_screenshots(self, row):
        """Show the screenshots of the session in a session-list row."""
        session_id = self.sessions_list.item(row, 0).data(Qt.UserRole)
        session_name = self.sessions_list.item(row, 0).text()
        if session_id is None:
            return
        self._show_screenshots_by_session_id(session_id, session_name)

    def _open_screenshot_reference(self, screenshot_id: int):
        """Open the viewer on a screenshot the assistant pointed to (BU109)."""
        db = self.session_manager.db
        try:
            row = db.get_screenshot(screenshot_id)
        except Exception:
            self._on_status_update(
                f"Screenshot #{screenshot_id} no longer exists", is_error=True
            )
            return
        session_id = row['session_id']
        session = db.get_session(session_id) or {}
        self._open_screenshot_viewer(
            session_id,
            session.get('name') or f"Session {session_id}",
            focus_screenshot_id=screenshot_id,
        )


    def _create_settings_actions(self):
        """Create the settings state the Settings pop-up edits (BU128).

        These used to live in a QMenuBar. They are still checkable QActions
        because other code reads ``isChecked()`` / toggles ``setEnabled``; the
        pop-up flips them with ``trigger()`` so these handlers still persist.
        """
        self.live_transcription_action = QAction('Enable Live Transcription', self)
        self.live_transcription_action.setCheckable(True)
        self.live_transcription_action.setChecked(True)
        self.live_transcription_action.triggered.connect(lambda checked: self._on_live_transcription_toggled(checked))

        self.auto_summary_action = QAction('Auto-generate Summary', self)
        self.auto_summary_action.setCheckable(True)
        self.auto_summary_action.setChecked(True)
        self.auto_summary_action.triggered.connect(lambda checked: self._on_auto_summary_toggled(checked))

        # Show/hide the one-line app log under the chat input bar
        self.show_app_logs_action = QAction('Show Log Messages', self)
        self.show_app_logs_action.setCheckable(True)
        self.show_app_logs_action.setChecked(True)
        self.show_app_logs_action.triggered.connect(self._on_show_app_logs_toggled)

        # Reindex all RAG content (BU091) - recovery for a stale/corrupt index
        self._reindex_action = QAction('Reindex All (RAG)...', self)
        self._reindex_action.triggered.connect(self._on_reindex_all_clicked)

        # Where chronicle.log lives (BU125); the Help menu's job now that
        # there is no menu bar - shown on the Settings > Maintenance page.
        self._open_log_folder_action = QAction('Open Log Folder', self)
        self._open_log_folder_action.triggered.connect(self._open_log_folder)

        self._settings_dialog = None
        # Set when the user asks to restart (theme change, BU129); main()
        # relaunches the app once the event loop has exited.
        self.restart_requested = False
        QShortcut(QKeySequence("Ctrl+,"), self).activated.connect(self._open_settings_dialog)

    def _open_log_folder(self):
        from PySide6.QtGui import QDesktopServices
        from PySide6.QtCore import QUrl
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(paths.logs_dir())))

    def _on_live_transcription_toggled(self, checked):
        """Handle live transcription toggle from menu."""
        self.live_transcription_action.setChecked(checked)
        self._save_preferences()

    def _on_auto_summary_toggled(self, checked):
        """Handle auto summary toggle from menu."""
        self.auto_summary_action.setChecked(checked)
        self._save_preferences()

    def _on_show_app_logs_toggled(self, checked):
        """Show or hide the app log line and remember the choice."""
        self._apply_app_logs_visibility(checked)
        self._update_preferences({'show_app_logs': bool(checked)})

    def _apply_app_logs_visibility(self, visible: bool):
        if hasattr(self, 'app_logs_container'):
            self.app_logs_container.setVisible(visible)

    def _get_preferences_path(self):
        """Get the path to the preferences file."""
        return str(paths.preferences_path())

    def _read_preferences(self) -> dict:
        """Raw preferences dict from disk, or {} if there is nothing readable."""
        import os, json
        prefs_path = self._get_preferences_path()
        if not prefs_path or not os.path.exists(prefs_path):
            return {}
        try:
            with open(prefs_path, 'r') as f:
                loaded = json.load(f)
        except Exception as e:
            # Every setting is about to fall back to its default; say so
            # somewhere rather than starting over in silence (BU119).
            logger.warning(f"Could not read {prefs_path} ({e}); using defaults")
            return {}
        if not isinstance(loaded, dict):
            logger.warning(f"{prefs_path} is not a JSON object; using defaults")
            return {}
        return loaded

    def _write_preferences(self, prefs: dict) -> bool:
        """Write the whole preferences dict, atomically (BU119).

        Writes a temporary file beside the real one and renames it into place,
        so an interrupted write cannot leave a half-written preferences.json -
        which `_read_preferences` would have to discard, silently resetting
        every setting the app has.
        """
        import os, json, tempfile
        prefs_path = self._get_preferences_path()
        if not prefs_path:
            return False
        directory = os.path.dirname(prefs_path) or '.'
        tmp_path = None
        try:
            fd, tmp_path = tempfile.mkstemp(dir=directory, prefix='.preferences-', suffix='.tmp')
            with os.fdopen(fd, 'w') as f:
                json.dump(prefs, f)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, prefs_path)
            return True
        except Exception as e:
            logger.error(f"Failed to save preferences: {e}")
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
            return False

    def _load_preferences(self):
        """Load preferences from file."""
        prefs = self._read_preferences()
        # BU119: restored even with an otherwise empty file, so a hand-written
        # preferences.json holding only one of these still takes effect.
        self._restore_selected_model(prefs)
        self._restore_transcription_filter(prefs)
        if not prefs:
            return {'enable_live_transcription': True, 'auto_summary_after_stop': True}
        # Also update SESSION config for session_manager
        SESSION['auto_summary_after_stop'] = prefs.get('auto_summary_after_stop', True)
        # Set menu actions state
        if hasattr(self, 'live_transcription_action'):
            self.live_transcription_action.setChecked(prefs.get('enable_live_transcription', True))
        if hasattr(self, 'auto_summary_action'):
            self.auto_summary_action.setChecked(prefs.get('auto_summary_after_stop', True))
        if hasattr(self, 'show_app_logs_action'):
            self.show_app_logs_action.setChecked(bool(prefs.get('show_app_logs', True)))
        return prefs

    # --- BU119: state that used to be forgotten at every launch -------------

    def _restore_selected_model(self, prefs: dict):
        """Put the stored answering model back in force.

        `set_selected_model` validates against ALLOWED_MODELS, so a model that
        has since been removed from the list leaves DEFAULT_MODEL in place
        instead of pointing every call at something that no longer exists.
        """
        stored = prefs.get('selected_model')
        if not stored:
            return
        if not set_selected_model(stored):
            logger.warning(f"Stored model {stored!r} is not allowed; keeping {get_selected_model()}")

    def _restore_transcription_filter(self, prefs: dict):
        """Restore the transcript source filter ('all' / 'mic' / 'system')."""
        stored = prefs.get('transcription_filter')
        self._transcription_filter = stored if stored in TRANSCRIPT_FILTERS else 'all'

    def _save_transcription_filter(self):
        self._update_preferences({'transcription_filter': self._transcription_filter})

    def _save_session_scope(self):
        """Remember the scope and the session in it (BU119).

        Called from `_update_scope_label`, which every path that changes the
        scope or the selection already funnels through - one save instead of
        eight. Writes only when the pair actually changed, because that label
        is refreshed far more often than the selection moves.
        """
        if getattr(self, '_restoring_session_scope', False):
            return
        if not hasattr(self, 'scope_combo'):
            return

        values = {
            'assistant_scope': self.scope_combo.currentData(),
            'selected_session_id': self._selected_session_id,
        }
        if values == getattr(self, '_saved_session_scope', None):
            return
        self._saved_session_scope = values
        self._update_preferences(values)

    def _restore_session_scope(self):
        """Reopen on the session and scope the last run was left in (BU119).

        A stored session that has since been deleted is not an error the user
        can act on, so it falls back to Any Session with nothing selected.
        """
        prefs = self._read_preferences()
        scope = prefs.get('assistant_scope')
        if scope not in ('current', 'any'):
            scope = 'any'

        try:
            session_id = prefs.get('selected_session_id')
            session_id = int(session_id) if session_id is not None else None
        except (TypeError, ValueError):
            session_id = None

        session = None
        if session_id is not None and self.session_manager:
            try:
                session = self.session_manager.db.get_session(session_id)
            except Exception as e:
                logger.warning(f"Could not restore session {session_id}: {e}")
        if session is None:
            if session_id is not None:
                logger.info(f"Stored session {session_id} is gone; restoring Any Session")
            session_id = None
            scope = 'any'

        self._restoring_session_scope = True
        # _on_scope_changed skips its own reload while this is set, so the
        # transcripts are loaded once, below, whether or not changing the combo
        # fires the signal at all.
        self._clearing_scope = True
        try:
            self._selected_session_id = session_id
            index = self.scope_combo.findData(scope)
            if index >= 0:
                self.scope_combo.setCurrentIndex(index)
            if session is not None:
                self.session_search_input.setText(session['name'])
                self._search_input_shows_session = True
        finally:
            self._clearing_scope = False
            self._restoring_session_scope = False

        self._saved_session_scope = {
            'assistant_scope': scope,
            'selected_session_id': session_id,
        }

        if session_id is not None:
            self._clear_transcription_view()
            self._load_transcripts_for_session(session_id)
        self._update_scope_label()

    def stored_muted_sources(self) -> dict:
        """The mute state on disk, normalised (BU118/BU119).

        Anything unexpected in the file - a list, a string, an unknown source,
        a non-boolean - reads as unmuted rather than raising on the way up.
        """
        stored = self._read_preferences().get('muted_sources')
        if not isinstance(stored, dict):
            return {source: False for source, *_rest in _MUTE_TOGGLES}
        return {source: bool(stored.get(source, False)) for source, *_rest in _MUTE_TOGGLES}

    def _save_muted_sources(self):
        self._update_preferences({'muted_sources': dict(self._muted_sources)})

    def _restore_muted_sources(self):
        """Apply the stored mute state to the manager, before any session.

        Announced rather than left to the icon: coming back from a restart with
        a muted mic and recording an hour of silence is exactly the failure
        this has to not cause.
        """
        if self.session_manager is None:
            return

        stored = self.stored_muted_sources()
        for source, muted in stored.items():
            self.session_manager.set_source_muted(source, muted)
        self._update_mute_controls()

        muted_labels = [label for source, label, *_rest in _MUTE_TOGGLES if stored.get(source)]
        if muted_labels:
            self._on_status_update(f"{' and '.join(muted_labels)} still muted from last session")

    def _save_preferences(self):
        """Save preferences to file.

        Merges onto whatever is already on disk rather than rewriting the file
        from the two menu toggles, so settings owned by other parts of the UI
        (the detached window's layout, BU112) survive a menu toggle.
        """
        # Use menu actions state (they always exist)
        enable_live = self.live_transcription_action.isChecked() if hasattr(self, 'live_transcription_action') else True
        auto_summary = self.auto_summary_action.isChecked() if hasattr(self, 'auto_summary_action') else True
        prefs = self._read_preferences()
        prefs['enable_live_transcription'] = enable_live
        prefs['auto_summary_after_stop'] = auto_summary
        self._write_preferences(prefs)
        # Also update SESSION config for session_manager
        SESSION['auto_summary_after_stop'] = auto_summary

    def _update_preferences(self, values: dict):
        """Merge ``values`` into preferences.json and write it back."""
        prefs = self._read_preferences()
        prefs.update(values)
        self._write_preferences(prefs)

    def _make_icon(self, filename: str) -> QIcon:
        """Load a pixel SVG icon from src/assets/pixel."""
        try:
            return QIcon(asset_path(filename))
        except Exception:
            return QIcon()

    def _create_icon_tool_button(self, icon_filename: str, fallback_text: str, tooltip: str, callback) -> QToolButton:
        """Create a square pixel icon tool button with SVG fallback text."""
        button = PixelToolButton()
        button.setToolTip(tooltip)
        button.setIcon(self._make_icon(icon_filename))
        button.setText(fallback_text)
        button.setIconSize(QSize(28, 28))
        button.setMinimumSize(54, 54)
        button.clicked.connect(callback)
        return button

    def _build_mute_toggle_row(self) -> QWidget:
        """Build the mic / system-audio mute toggles (BU118).

        The buttons mirror SessionManager's mute state rather than owning it:
        the un-slashed icon means the source is live, the slashed one means its
        audio is being dropped. They are clickable before a session exists -
        the choice is remembered and handed to the recorder when recording
        starts, so a source can be silenced *before* hitting record.
        """
        row = QWidget()
        row.setFixedWidth(_MUTE_ROW_WIDTH)
        row_layout = QHBoxLayout(row)
        # Top inset 8 + the panel's 4px top margin = 12, matching the 12px
        # right margin so the toggles sit equally off both edges.
        row_layout.setContentsMargins(0, 8, 0, 0)
        row_layout.setSpacing(_MUTE_ROW_SPACING)

        self._mute_buttons = {}
        self._muted_sources = {}
        for source, _label, _on_icon, _off_icon, fallback in _MUTE_TOGGLES:
            button = PixelToolButton(compact=True)
            button.setFixedSize(_MUTE_BUTTON_SIZE, _MUTE_BUTTON_SIZE)
            button.setIconSize(QSize(22, 22))
            button.setText(fallback)
            button.clicked.connect(partial(self._on_mute_toggle_clicked, source))
            self._mute_buttons[source] = button
            self._muted_sources[source] = False
            row_layout.addWidget(button)

        self._refresh_mute_button_faces()
        # Slightly see-through so the toggles don't compete with the chat.
        opacity = QGraphicsOpacityEffect(row)
        opacity.setOpacity(0.7)
        row.setGraphicsEffect(opacity)
        return row

    def _refresh_mute_button_faces(self):
        """Point each toggle's icon and tooltip at what clicking it will do."""
        for source, label, on_icon, off_icon, _fallback in _MUTE_TOGGLES:
            button = self._mute_buttons.get(source)
            if button is None:
                continue
            muted = self._muted_sources.get(source, False)
            button.setIcon(self._make_icon(off_icon if muted else on_icon))
            button.setToolTip(f"{'Unmute' if muted else 'Mute'} {label.lower()}")

    def _update_mute_controls(self):
        """Sync the toggles with SessionManager. Called from _update_ui_state."""
        if not getattr(self, '_mute_buttons', None) or self.session_manager is None:
            return

        for source, *_rest in _MUTE_TOGGLES:
            # The manager is the source of truth - it holds the state across
            # sessions and hands it to each new recorder - so the UI never
            # invents a mute or has to reset one.
            self._muted_sources[source] = self.session_manager.is_source_muted(source)
        self._refresh_mute_button_faces()

    def _on_mute_toggle_clicked(self, source: str):
        """Toggle one capture source's mute (BU118).

        Works with no session: the manager remembers the choice and applies it
        to the recorder it builds when recording starts.
        """
        if self.session_manager is None:
            return

        label = next((t[1] for t in _MUTE_TOGGLES if t[0] == source), source)
        target = not self._muted_sources.get(source, False)
        try:
            applied = self.session_manager.set_source_muted(source, target)
        except Exception as e:
            logger.error(f'Failed to set {source} mute: {e}')
            self._on_status_update(f'Failed to mute {label.lower()}: {e}', is_error=True)
            applied = False

        if not applied:
            # set_source_muted already said why; leave the button as it was so
            # it never claims a mute that capture never got.
            self._update_mute_controls()
            return

        self._muted_sources[source] = target
        self._refresh_mute_button_faces()
        self._save_muted_sources()  # BU119

        state = 'muted' if target else 'unmuted'
        session = self.session_manager.get_active_session()
        recording = bool(session and session.status == Session.STATUS_ACTIVE)
        suffix = '' if recording else ' (applies when recording starts)'
        self._on_status_update(f'{label} {state}{suffix}')

    def _create_central_widget(self):
        """Create the pixel-art three-column Chronicle workspace."""
        self.setStyleSheet(app_qss())

        # Frameless window: a pixel-art title bar replaces the Windows chrome,
        # and the backdrop's outer margin is the resize grip.
        self.setWindowFlags(self.windowFlags() | Qt.FramelessWindowHint)
        # Has its own title bar; the app-wide PixelWindowChrome skips it.
        self.setProperty('pixelChrome', False)
        backdrop = PixelResizeFrame(self)
        # Explicit blue backdrop behind the three panels (was reading as black).
        backdrop.setObjectName("CentralBackdrop")
        backdrop.setAttribute(Qt.WA_StyledBackground, True)
        backdrop.setStyleSheet("QWidget#CentralBackdrop { background: #0E2A6B; }")
        self.setCentralWidget(backdrop)
        grip = PixelResizeFrame.GRIP
        backdrop_layout = QVBoxLayout(backdrop)
        backdrop_layout.setContentsMargins(grip, 0, grip, grip)
        backdrop_layout.setSpacing(0)
        self._backdrop_layout = backdrop_layout
        self.title_bar = PixelTitleBar(self, 'Chronicle')
        # The sidebar already shows the name; the bar only carries the buttons.
        self.title_bar.title_label.hide()
        backdrop_layout.addWidget(self.title_bar, 0)

        central_widget = QWidget()
        backdrop_layout.addWidget(central_widget, 1)
        central_layout = QHBoxLayout(central_widget)
        central_layout.setContentsMargins(0, 0, 0, 0)
        central_layout.setSpacing(8)
        self.central_layout = central_layout

        self.left_shell = self._build_left_sidebar()
        self.center_shell = self._build_center_workspace()
        self.right_shell = self._build_transcripts_panel()

        # Three zones:
        # - left_shell is collapsible
        # - right_shell remains fixed
        # - center_shell is the only column with horizontal stretch
        # Inside center_shell, only the answers viewport consumes the extra width.
        central_layout.addWidget(self.left_shell, 0)
        central_layout.addWidget(self.center_shell, 1)
        central_layout.addWidget(self.right_shell, 0)

        self._wire_pane_focus()
        self._wire_find_bar()

    # =========================
    # Active pane focus model (BU098)
    # =========================

    def _wire_pane_focus(self):
        """Set up the mutually-exclusive active-pane substrate over the three
        top-level shells. Background clicks and keyboard focus both drive it.
        """
        self._panes = {
            "left": self.left_shell,
            "center": self.center_shell,
            "right": self.right_shell,
        }
        self._pane_focus = PaneFocusController()

        for name, shell in self._panes.items():
            shell.clicked.connect(lambda n=name: self._set_active_pane(n))

        app = QApplication.instance()
        if app is not None:
            app.focusChanged.connect(self._on_global_focus_changed)

        self._set_active_pane("center")

    @property
    def _active_pane(self) -> Optional[str]:
        return self._pane_focus.active

    def _set_active_pane(self, name: str):
        changed = self._pane_focus.set_active(name)
        for p in changed:
            self._panes[p].set_active(self._pane_focus.active == p)
        # A find bar left open over another pane loses its relevance.
        if changed and getattr(self, "_find_bar", None) and self._find_bar.isVisible():
            if self._pane_focus.active != getattr(self, "_find_bar_pane", None):
                self._close_find_bar()

    def _on_global_focus_changed(self, old, now):
        if now is None:
            return
        pane = resolve_pane_for_widget(now, self._panes)
        if pane is not None:
            self._set_active_pane(pane)

    # =========================
    # In-pane find bar (BU099)
    # =========================

    def _wire_find_bar(self):
        self._find_bar = PixelFindBar(self)
        self._find_bar.hide()
        self._find_bar_pane = None
        self._find_highlighted_row = None
        self._find = FindController(
            self._find_search_conversations,
            self._find_center_texts,
            self._find_right_texts,
        )
        self._find_bar.query_changed.connect(self._on_find_query_changed)
        self._find_bar.next_match.connect(lambda: self._find_step("next"))
        self._find_bar.prev_match.connect(lambda: self._find_step("prev"))
        self._find_bar.return_pressed.connect(self._on_find_return)
        self._find_bar.closed.connect(self._close_find_bar)

        self._find_shortcut = QShortcut(QKeySequence.Find, self)
        self._find_shortcut.activated.connect(self._open_find_bar)

    # --- corpus adapters (the only DB / widget access) ------------------

    def _layout_rows(self, layout):
        return [
            layout.itemAt(i).widget()
            for i in range(layout.count())
            if layout.itemAt(i).widget() is not None
        ]

    def _find_center_texts(self):
        return [
            r.property("message_text") or ""
            for r in self._layout_rows(self._answer_layout)
        ]

    def _find_right_texts(self):
        texts = []
        for r in self._layout_rows(self._transcription_layout):
            t = r.property("find_text")
            if not t:
                bubble = bubble_of_row(r)
                t = bubble.label.text() if bubble is not None else ""
            texts.append(t or "")
        return texts

    def _find_search_conversations(self, terms):
        try:
            rows = self.session_manager.db.search_conversations(
                " ".join(terms), limit=200
            )
            return [r["id"] for r in rows]
        except Exception as e:  # noqa: BLE001
            logger.error(f"Find: conversation search failed: {e}")
            return []

    # --- open / close / position -------------------------------------

    def _open_find_bar(self):
        pane = self._active_pane or "center"
        if self._find_bar.isVisible() and self._find_bar_pane == pane:
            self._find_bar.focus_field()
            return
        self._clear_find_highlight()
        self._find_bar_pane = pane
        self._find_bar.setParent(self._panes[pane])
        self._find.set_mode(pane)
        self._find_bar.show()
        self._find_bar.raise_()
        self._position_find_bar()
        self._find_bar.focus_field()
        self._on_find_query_changed(self._find_bar.query_text())

    def _position_find_bar(self):
        pane = self._find_bar_pane
        if not pane or not self._find_bar.isVisible():
            return
        host = self._panes[pane]
        margin = 10  # clears the panel's pixel-cut border + corner
        # Shrink to fit inside a narrow pane (sidebar / transcripts panel).
        avail = host.width() - 2 * margin
        width = max(150, min(self._find_bar.preferred_width, avail))
        self._find_bar.setFixedWidth(width)
        x = max(margin, host.width() - width - margin)
        self._find_bar.move(x, margin)

    def _close_find_bar(self):
        self._clear_find_highlight()
        if getattr(self, "_find_bar", None):
            self._find_bar.hide()
        pane = self._find_bar_pane
        self._find_bar_pane = None
        if pane and pane in self._panes:
            self._panes[pane].setFocus()

    # --- match application ------------------------------------------

    def _clear_find_highlight(self):
        row = getattr(self, "_find_highlighted_row", None)
        if row is not None:
            bubble = bubble_of_row(row)
            if bubble is not None:
                bubble.set_highlighted(False)
                bubble.set_match_terms([])
            self._find_highlighted_row = None

    def _on_find_query_changed(self, query):
        self._clear_find_highlight()
        self._find.set_query(query)
        self._apply_find_current()
        self._find_bar.set_match_count(*self._find.match_label())

    def _find_step(self, direction):
        self._clear_find_highlight()
        (self._find.next if direction == "next" else self._find.prev)()
        self._apply_find_current()
        self._find_bar.set_match_count(*self._find.match_label())

    def _on_find_return(self):
        if self._find.mode == "left":
            conv_id = self._find.current()
            if conv_id is not None:
                self._load_conversation(conv_id)
            self._close_find_bar()
        else:
            self._find_step("next")

    def _apply_find_current(self):
        match = self._find.current()
        if match is None:
            return
        mode = self._find.mode
        if mode == "left":
            self._find_select_conversation(match)
            return
        if mode == "center":
            layout, scroll = self._answer_layout, self._answer_scroll_area
        else:
            layout, scroll = self._transcription_layout, self._transcription_scroll_area
        rows = self._layout_rows(layout)
        if not (0 <= match < len(rows)):
            return
        row = rows[match]
        bubble = bubble_of_row(row)
        if bubble is not None:
            bubble.set_highlighted(True)
            bubble.set_match_terms(FindController.parse_terms(self._find.query))
        self._find_highlighted_row = row
        scroll.ensureWidgetVisible(row, 0, 40)

    def _find_select_conversation(self, conv_id):
        lst = self.conversations_list
        for i in range(lst.count()):
            item = lst.item(i)
            if item.data(Qt.UserRole) == conv_id:
                lst.setCurrentItem(item)
                lst.scrollToItem(item)
                return

    def _build_left_sidebar(self) -> QWidget:
        """Build the left Chronicle sidebar: brand, actions, history, controls."""
        panel = PixelPanel()
        panel.setMinimumWidth(self._sidebar_expanded_min_width)
        panel.setMaximumWidth(self._sidebar_expanded_max_width)
        # Same type as the session cards in the All Sessions window:
        # Courier New, bold, 11pt, cream.
        panel.setStyleSheet(
            "QListWidget, QPushButton, QToolButton { font-family: 'Courier New';"
            " font-size: 11pt; font-weight: 700; color: #FFF0BF; }"
        )
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)
        self._sidebar_layout = layout

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(8)
        self.sidebar_brand = QLabel("Chronicle")
        self.sidebar_brand.setObjectName("BrandTitle")
        header.addWidget(self.sidebar_brand, 1)

        self.sidebar_menu_button = PixelToolButton()
        self.sidebar_menu_button.setIcon(self._make_icon("icon_menu.svg"))
        self.sidebar_menu_button.setIconSize(QSize(30, 30))
        self.sidebar_menu_button.setText("☰")
        self.sidebar_menu_button.setToolTip("Collapse sidebar")
        self.sidebar_menu_button.setMinimumSize(50, 50)
        self.sidebar_menu_button.clicked.connect(self._toggle_left_sidebar)
        set_accent(self.sidebar_menu_button, "menu")
        header.addWidget(self.sidebar_menu_button, 0)
        self.sidebar_header_layout = header
        layout.addLayout(header)

        self.new_chat_button = PixelButton("  New Chat", sidebar=True)
        self.new_chat_button.setIcon(self._make_icon(
            "icon_grid_light.svg" if theme.is_classic() else "icon_grid.svg"))
        set_accent(self.new_chat_button, "primary")
        self.new_chat_button.setIconSize(QSize(28, 28))
        self.new_chat_button.clicked.connect(self._on_new_chat_clicked)
        layout.addWidget(self.new_chat_button)

        self.search_chats_button = PixelButton("  Search Chats", sidebar=True)
        self.search_chats_button.setIcon(self._make_icon(
            "icon_search_light.svg" if theme.is_classic() else "icon_search_synth.svg"))
        self.search_chats_button.setIconSize(QSize(28, 28))
        self.search_chats_button.clicked.connect(self._open_search_dialog)
        layout.addWidget(self.search_chats_button)

        self.settings_button = PixelButton("  Settings", sidebar=True)
        self.settings_button.setIcon(self._make_icon(
            "icon_settings.svg" if theme.is_classic() else "icon_settings_synth.svg"))
        self.settings_button.setIconSize(QSize(28, 28))
        self.settings_button.clicked.connect(self._open_settings_dialog)
        layout.addWidget(self.settings_button)

        self._sidebar_action_buttons = [
            (self.new_chat_button, "  New Chat", "New chat"),
            (self.search_chats_button, "  Search Chats", "Search chats"),
            (self.settings_button, "  Settings", "Settings"),
        ]

        self.sidebar_history_panel = PixelPanel(inner=True)
        history_panel = self.sidebar_history_panel
        history_layout = QVBoxLayout(history_panel)
        history_layout.setContentsMargins(8, 8, 8, 8)
        history_layout.setSpacing(6)
        history_layout.addWidget(PixelSectionTitle("PAST CONVERSATIONS", alt=True))

        self.conversations_list = QListWidget()
        self.conversations_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.conversations_list.setSpacing(4)
        self.conversations_list.setItemDelegate(
            PixelConversationDelegate(self.conversations_list)
        )
        self.conversations_list.setUniformItemSizes(True)
        self.conversations_list.itemClicked.connect(self._on_conversation_selected)
        self.conversations_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.conversations_list.customContextMenuRequested.connect(
            self._show_conversation_context_menu
        )
        history_layout.addWidget(self.conversations_list, 1)
        layout.addWidget(history_panel, 1)

        self.sidebar_collapse_spacer = QWidget()
        self.sidebar_collapse_spacer.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Expanding)
        self.sidebar_collapse_spacer.setVisible(False)
        layout.addWidget(self.sidebar_collapse_spacer, 1)

        controls = QHBoxLayout()
        controls.setContentsMargins(0, 4, 0, 0)
        controls.setSpacing(8)
        self.sidebar_controls_layout = controls

        self.play_stop_button = self._create_icon_tool_button("icon_play.svg", "▶", "Start Session", self._on_play_stop_clicked)
        controls.addWidget(self.play_stop_button)

        self.stop_visual_button = self._create_icon_tool_button("icon_stop.svg", "■", "Stop Session", self._on_stop_session)
        controls.addWidget(self.stop_visual_button)

        self.screenshot_icon_button = self._create_icon_tool_button("icon_camera.svg", "▣", "Take Screenshot", self._on_take_screenshot)
        controls.addWidget(self.screenshot_icon_button)

        self.view_summary_icon_button = self._create_icon_tool_button("icon_summary.svg", "▤", "View Summary", self._on_view_summary_icon_clicked)
        controls.addWidget(self.view_summary_icon_button)

        self.view_screenshots_icon_button = self._create_icon_tool_button("icon_export.svg", "□", "View Screenshots", self._on_view_screenshots_icon_clicked)
        self.view_screenshots_icon_button.setVisible(False)
        controls.addWidget(self.view_screenshots_icon_button)

        self._sidebar_session_control_buttons = [
            self.play_stop_button,
            self.stop_visual_button,
            self.screenshot_icon_button,
            self.view_summary_icon_button,
            self.view_screenshots_icon_button,
        ]

        layout.addLayout(controls)
        return panel

    def _set_style_property(self, widget: QWidget, name: str, value):
        """Set a dynamic Qt style property and force QSS to re-evaluate it."""
        widget.setProperty(name, value)
        widget.style().unpolish(widget)
        widget.style().polish(widget)
        widget.update()

    def _toggle_left_sidebar(self):
        """Collapse/expand the left navigation panel."""
        self._set_left_sidebar_collapsed(not self._sidebar_collapsed)

    def _set_left_sidebar_collapsed(self, collapsed: bool):
        """
        Collapsed layout contract:
        - the left rail becomes icon-only;
        - New Chat, Search Chats and Settings remain visible as icons;
        - session controls remain visible as icon buttons at the bottom;
        - history/brand text disappear;
        - the right transcript panel and center control bars keep their dimensions;
        - only the answer scroll viewport receives the reclaimed horizontal space.
        """
        self._sidebar_collapsed = collapsed

        if collapsed:
            self.left_shell.setMinimumWidth(self._sidebar_collapsed_width)
            self.left_shell.setMaximumWidth(self._sidebar_collapsed_width)
            self.sidebar_brand.setVisible(False)
            self.sidebar_history_panel.setVisible(False)
            self.sidebar_collapse_spacer.setVisible(True)

            self.sidebar_menu_button.setText("›")
            self.sidebar_menu_button.setToolTip("Expand sidebar")
            self.sidebar_menu_button.setMinimumSize(54, 54)
            self.sidebar_menu_button.setMaximumSize(54, 54)

            for button, _expanded_text, tooltip in self._sidebar_action_buttons:
                button.setText("")
                button.setToolTip(tooltip)
                self._set_style_property(button, "iconOnly", True)
                button.setMinimumSize(54, 54)
                button.setMaximumSize(54, 54)
                self._sidebar_layout.setAlignment(button, Qt.AlignHCenter)
            self.sidebar_header_layout.setAlignment(self.sidebar_menu_button, Qt.AlignHCenter)

            self.sidebar_controls_layout.setDirection(QBoxLayout.TopToBottom)
            self.sidebar_controls_layout.setAlignment(Qt.AlignHCenter | Qt.AlignBottom)
            self.sidebar_controls_layout.setSpacing(8)
            for button in self._sidebar_session_control_buttons:
                button.setMinimumSize(54, 54)
                button.setMaximumSize(54, 54)
                self.sidebar_controls_layout.setAlignment(button, Qt.AlignHCenter)

            self._answer_layout.setContentsMargins(32, 18, 32, 18)
        else:
            self.left_shell.setMinimumWidth(self._sidebar_expanded_min_width)
            self.left_shell.setMaximumWidth(self._sidebar_expanded_max_width)
            self.sidebar_brand.setVisible(True)
            self.sidebar_history_panel.setVisible(True)
            self.sidebar_collapse_spacer.setVisible(False)

            self.sidebar_menu_button.setText("☰")
            self.sidebar_menu_button.setToolTip("Collapse sidebar")
            self.sidebar_menu_button.setMinimumSize(50, 50)
            self.sidebar_menu_button.setMaximumSize(16777215, 16777215)

            for button, expanded_text, tooltip in self._sidebar_action_buttons:
                button.setText(expanded_text)
                button.setToolTip(tooltip)
                self._set_style_property(button, "iconOnly", False)
                button.setMinimumHeight(52)
                button.setMaximumSize(16777215, 16777215)
                self._sidebar_layout.setAlignment(button, Qt.Alignment())
            self.sidebar_header_layout.setAlignment(self.sidebar_menu_button, Qt.Alignment())

            self.sidebar_controls_layout.setDirection(QBoxLayout.LeftToRight)
            self.sidebar_controls_layout.setAlignment(Qt.AlignLeft | Qt.AlignBottom)
            self.sidebar_controls_layout.setSpacing(8)
            for button in self._sidebar_session_control_buttons:
                button.setMinimumSize(54, 54)
                button.setMaximumSize(16777215, 16777215)
                self.sidebar_controls_layout.setAlignment(button, Qt.Alignment())

            self._answer_layout.setContentsMargins(42, 18, 42, 18)

        self.left_shell.updateGeometry()
        self.center_shell.updateGeometry()
        self._answer_scroll_area.updateGeometry()

    def _build_center_workspace(self) -> QWidget:
        """Build the central answers/chat workspace."""
        panel = PixelPanel()
        panel.set_backdrop(theme.background_image())  # None in the classic theme
        panel.setMinimumWidth(420)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 4, 12, 10)
        layout.setSpacing(10)

        # Unified top selector/search bar: visually one cream control like the mockup,
        # while preserving the existing combo, search input and button attributes.
        top_bar_frame = QFrame()
        top_bar_frame.setObjectName("UnifiedSearchBar")
        top_bar_frame.setMinimumWidth(360)
        top_bar_frame.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        top_bar_frame.setMinimumHeight(54)
        top_bar = QHBoxLayout(top_bar_frame)
        top_bar.setContentsMargins(12, 4, 8, 4)
        top_bar.setSpacing(8)

        self.scope_combo = QComboBox()
        self.scope_combo.setObjectName("ScopeCombo")
        self.scope_combo.addItem("Specific Session", "current")
        self.scope_combo.addItem("Any Session", "any")
        self.scope_combo.setCurrentIndex(1)
        self.scope_combo.currentIndexChanged.connect(self._on_scope_changed)
        self.scope_combo.setMinimumHeight(46)
        self.scope_combo.setMinimumWidth(140)
        self.scope_combo.setMaximumWidth(210)
        top_bar.addWidget(self.scope_combo, 0)

        self.session_search_input = QLineEdit()
        self.session_search_input.setObjectName("SessionSearchInput")
        self.session_search_input.setPlaceholderText("Search sessions...")
        self.session_search_input.setMinimumHeight(46)
        self.session_search_input.installEventFilter(self)
        top_bar.addWidget(self.session_search_input, 1)

        self.session_search_button = PixelToolButton()
        self.session_search_button.setIcon(self._make_icon("icon_search_dark.svg"))
        self.session_search_button.setIconSize(QSize(28, 28))
        self.session_search_button.setText("⌕")
        self.session_search_button.setToolTip("Show all sessions")
        self.session_search_button.clicked.connect(self._show_all_sessions_window)
        self.session_search_button.setStyleSheet("QToolButton { background: transparent; color: #071C4B; border: none; min-width: 46px; min-height: 46px; max-width: 50px; max-height: 50px; }")
        top_bar.addWidget(self.session_search_button, 0)
        self.show_all_sessions_button = self.session_search_button

        self._session_completer = QCompleter()
        self._session_completer.setFilterMode(Qt.MatchContains)
        self._session_completer.setCaseSensitivity(Qt.CaseInsensitive)
        self._session_completer.setMaxVisibleItems(5)
        self._session_completer.activated.connect(self._on_session_completer_selected)
        self.session_search_input.setCompleter(self._session_completer)
        
        # Apply navy style to completer popup
        self._session_completer.popup().setStyleSheet("""
            QAbstractItemView {
                background-color: #071D52;
                color: #FFFFFF;
                border: 2px solid #3E6B9B;
                selection-background-color: #3E6B9B;
                selection-color: #FFFFFF;
                font-family: "Courier New";
                font-size: 14px;
            }
        """)
        self._refresh_session_completer()

        # BU118: mic / system-audio mute toggles, in the top-right corner of
        # the chat section. They get their own row above the search bar rather
        # than sharing it: the search frame has a 360px minimum, so in a narrow
        # centre column there is no width left to share and the two would
        # overlap.
        # Mute toggles on the top-right row; the search bar sits directly
        # beneath them with no gap.
        header_grid = QGridLayout()
        header_grid.setContentsMargins(0, 0, 0, 0)
        header_grid.setVerticalSpacing(0)
        header_grid.addWidget(self._build_mute_toggle_row(), 0, 0, Qt.AlignRight | Qt.AlignTop)
        # Centred, but free to widen up to its cap: an AlignHCenter cell would
        # pin it to its size hint, which is narrower than the chat column.
        top_bar_frame.setMaximumWidth(self._search_bar_max_width)
        search_row = QHBoxLayout()
        search_row.setContentsMargins(0, 0, 0, 0)
        search_row.addStretch(1)
        search_row.addWidget(top_bar_frame, 10)
        search_row.addStretch(1)
        header_grid.addLayout(search_row, 1, 0, Qt.AlignTop)
        layout.addLayout(header_grid, 0)

        # Tab buttons removed - keeping UI cleaner
        # Original tabs: ANSWERS and CHAT

        self._scope_label = QLabel("")
        self._scope_label.setObjectName("ScopeLabel")
        self._scope_label.setAlignment(Qt.AlignCenter)
        self._scope_label.setStyleSheet("color: #0078d4; font-weight: bold;")
        self._scope_label.setVisible(False)
        layout.addWidget(self._scope_label)

        self.session_name_label = QLabel("Session Name:")
        self.session_name_label.setVisible(False)
        self.session_name_input = QLabel("New Session")
        self.session_name_input.setVisible(False)

        self.status_label = QLabel("Ready")
        self.status_label.setAlignment(Qt.AlignCenter)
        self.status_label.setObjectName("ScopeLabel")
        self.status_label.setVisible(False)
        layout.addWidget(self.status_label)

        self._answer_scroll_area = QScrollArea()
        self._answer_scroll_area.setWidgetResizable(True)
        self._answer_scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._answer_container = QWidget()
        self._answer_layout = QVBoxLayout(self._answer_container)
        self._answer_layout.setSpacing(20)
        self._answer_layout.setContentsMargins(38, 16, 38, 16)
        self._answer_layout.addStretch()
        self._answer_scroll_area.setWidget(self._answer_container)
        # Same pinning as the transcript stream: follow the bottom while the
        # user is there, re-pinning once the new bubble has its real height.
        self._answer_autoscroll = True
        self._answer_scroll_area.verticalScrollBar().valueChanged.connect(
            self._on_answer_scroll_changed
        )
        self._answer_scroll_area.verticalScrollBar().rangeChanged.connect(
            self._on_answer_range_changed
        )
        layout.addWidget(self._answer_scroll_area, 1)
        self.answer_display = None

        input_bar = QFrame()
        input_bar.setObjectName("ChatInputBar")
        input_bar.setMinimumWidth(360)
        input_bar.setMaximumWidth(self._center_control_max_width)
        input_bar.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        input_bar.setMinimumHeight(64)
        input_layout = QHBoxLayout(input_bar)
        input_layout.setContentsMargins(16, 7, 12, 7)
        input_layout.setSpacing(8)

        self.question_input = PixelChatInput()
        self.question_input.setObjectName("QuestionInput")
        self.question_input.setPlaceholderText("Ready to help...")
        self.question_input.setMaximumHeight(50)
        self.question_input.setMinimumHeight(50)
        self.question_input.submitted.connect(self._on_ask_clicked)
        input_layout.addWidget(self.question_input, 1)

        self.agent_combo = QComboBox()
        self.agent_combo.setObjectName("AgentCombo")
        agents = ASSISTANT_AGENTS.get('agents', {})
        default_agent_id = ASSISTANT_AGENTS.get('default', '')
        for agent_id, agent_info in agents.items():
            # Show actual agent name instead of generic "Agent" label
            self.agent_combo.addItem(agent_info.get('label', agent_id), agent_id)
        default_index = self.agent_combo.findData(default_agent_id)
        if default_index >= 0:
            self.agent_combo.setCurrentIndex(default_index)
        # A compact, background-less agent label + chevron hugging the bar's
        # right edge, sized to its text rather than a fixed 140px block.
        self.agent_combo.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        self.agent_combo.setFixedHeight(34)
        self.agent_combo.setMaximumWidth(200)
        self.agent_combo.setCursor(Qt.PointingHandCursor)
        self.agent_combo.setToolTip("Assistant agent")
        input_layout.addWidget(self.agent_combo, 0, Qt.AlignVCenter | Qt.AlignRight)

        self.ask_button = PixelButton("Ask")
        self.ask_button.clicked.connect(self._on_ask_clicked)
        self.ask_button.setVisible(False)
        input_layout.addWidget(self.ask_button, 0)

        self.detach_assistant_button = PixelButton("Detach")
        self.detach_assistant_button.clicked.connect(self._on_detach_assistant)
        self.detach_assistant_button.setVisible(False)
        input_layout.addWidget(self.detach_assistant_button, 0)

        # Keep the cleaned visual bar usable without the visible Ask button.
        self.ask_shortcut = QShortcut(QKeySequence("Ctrl+Return"), self.question_input)
        self.ask_shortcut.activated.connect(self._on_ask_clicked)
        self.ask_enter_shortcut = QShortcut(QKeySequence("Ctrl+Enter"), self.question_input)
        self.ask_enter_shortcut.activated.connect(self._on_ask_clicked)

        # Fixed-width horizontal container for input bar
        input_bar_container = QHBoxLayout()
        input_bar_container.setContentsMargins(0, 0, 0, 0)
        # Side stretches keep the bar centred at ~5/6 of the panel width
        # (AlignHCenter pinned it to its narrow size hint).
        input_bar_container.addStretch(1)
        input_bar_container.addWidget(input_bar, 10)
        input_bar_container.addStretch(1)
        layout.addLayout(input_bar_container, 0)

        self.candidate_group = PixelPanel(inner=True)
        candidate_layout = QVBoxLayout(self.candidate_group)
        candidate_layout.setContentsMargins(10, 10, 10, 10)
        self._candidate_title = PixelSectionTitle("SELECT A SESSION")
        candidate_layout.addWidget(self._candidate_title)
        self._candidate_list_widget = QListWidget()
        self._candidate_list_widget.setSelectionMode(QAbstractItemView.SingleSelection)
        self._candidate_list_widget.setMaximumHeight(112)
        self._candidate_list_widget.itemClicked.connect(self._on_candidate_selected)
        candidate_layout.addWidget(self._candidate_list_widget)
        self.use_candidate_button = PixelButton("Use Selected Session")
        self.use_candidate_button.clicked.connect(self._on_use_candidate_clicked)
        self.use_candidate_button.setEnabled(False)
        candidate_layout.addWidget(self.use_candidate_button)
        self.candidate_group.setVisible(False)
        layout.addWidget(self.candidate_group)

        # App Logs widget - muestra solo el último log (una línea)
        self.app_logs_container = QWidget()
        self.app_logs_container.setObjectName("AppLogsContainer")
        self.app_logs_container.setMaximumWidth(1620)
        self.app_logs_container.setMinimumHeight(30)
        # Hiding the log line must not move the chat bar: keep its slot.
        retain = self.app_logs_container.sizePolicy()
        retain.setRetainSizeWhenHidden(True)
        self.app_logs_container.setSizePolicy(retain)
        self.app_logs_layout = QVBoxLayout(self.app_logs_container)
        self.app_logs_layout.setContentsMargins(0, 0, 0, 0)
        self.app_logs_layout.setSpacing(0)
        self.app_logs_layout.addStretch()

        # Establecer fuente un poco más pequeña
        log_font = QFont("Courier New")
        log_font.setPointSize(10)
        log_font.setBold(True)
        self.app_logs_container.setFont(log_font)

        layout.addWidget(self.app_logs_container, 0, Qt.AlignBottom | Qt.AlignHCenter)
        # The menu action was restored from preferences before this existed.
        self._apply_app_logs_visibility(self.show_app_logs_action.isChecked())

        # Almacenar mensajes de log para referencia (solo guardamos el último)
        self._app_logs_messages = []

        return panel

    @staticmethod
    def _make_inserted_caption(alignment) -> QLabel:
        """Small muted "Inserted Transcript" label under a transcripts title
        (BU138 main panel, BU139 detached window). Starts hidden."""
        caption = QLabel("Inserted Transcript")
        caption.setAlignment(alignment)
        caption.setStyleSheet(
            "QLabel { color: #7E8FC2; background: transparent; border: none; }"
        )
        font = QFont("Courier New")
        font.setPointSize(9)
        font.setBold(True)
        caption.setFont(font)
        caption.setVisible(False)
        return caption

    def _build_transcripts_panel(self) -> QWidget:
        """Build the right transcripts panel."""
        panel = PixelPanel()
        panel.setMinimumWidth(300)
        panel.setMaximumWidth(340)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(8)
        top.addStretch()
        self.transcript_filter_button = PixelToolButton(compact=True)
        self.transcript_filter_button.setIcon(self._make_icon("icon_filter_smooth.svg"))
        self.transcript_filter_button.setToolTip("Cycle transcript filter")
        self.transcript_filter_button.setProperty("quietDisabled", True)
        self.transcript_filter_button.clicked.connect(self._cycle_transcription_filter)
        top.addWidget(self.transcript_filter_button)

        self.download_transcript_button = PixelToolButton(compact=True)
        self.download_transcript_button.setIcon(self._make_icon("icon_download_smooth.svg"))
        self.download_transcript_button.setToolTip("Download transcript")
        self.download_transcript_button.clicked.connect(self._on_download_transcripts)
        top.addWidget(self.download_transcript_button)

        self.detach_transcription_button = PixelToolButton(compact=True)
        self.detach_transcription_button.setIcon(self._make_icon("icon_detach_smooth.svg"))
        self.detach_transcription_button.setToolTip("Detach transcripts window")
        self.detach_transcription_button.clicked.connect(self._on_detach_transcription)
        top.addWidget(self.detach_transcription_button)
        layout.addLayout(top)

        layout.addWidget(PixelSectionTitle("TRANSCRIPTS WINDOW", center=True))

        # BU138: shown only while an inserted transcript is displayed.
        self._inserted_transcript_caption = self._make_inserted_caption(Qt.AlignCenter)
        layout.addWidget(self._inserted_transcript_caption)

        transcription_view = self._create_transcription_view()
        layout.addWidget(transcription_view, 1)
        return panel

    def _cycle_transcription_filter(self):
        """Show a dropdown menu with transcript filter options."""
        if not hasattr(self, '_transcription_filter_combo'):
            return
        
        # Create the menu if it doesn't exist
        menu = QMenu(self.transcript_filter_button)
        menu.setStyleSheet("""
            QMenu {
                background-color: #071D52;
                border: 2px solid #3E6B9B;
                color: #FFFFFF;
                padding: 4px;
            }
            QMenu::item:selected {
                background-color: #3E6B9B;
            }
            QMenu::indicator {
                width: 14px;
                height: 14px;
            }
        """)
        
        # Get current filter to check the active option
        current_filter = self._transcription_filter
        
        # Add filter options
        all_action = menu.addAction("All")
        all_action.setCheckable(True)
        all_action.setChecked(current_filter == 'all')
        
        mic_action = menu.addAction("Mic")
        mic_action.setCheckable(True)
        mic_action.setChecked(current_filter == 'mic')
        
        system_action = menu.addAction("System")
        system_action.setCheckable(True)
        system_action.setChecked(current_filter == 'system')
        
        # Connect actions to filter change
        def set_filter(filter_value):
            # Find and set the combo index
            for i in range(self._transcription_filter_combo.count()):
                if self._transcription_filter_combo.itemData(i) == filter_value:
                    self._transcription_filter_combo.setCurrentIndex(i)
                    break
        
        all_action.triggered.connect(lambda: set_filter('all'))
        mic_action.triggered.connect(lambda: set_filter('mic'))
        system_action.triggered.connect(lambda: set_filter('system'))
        
        # Show menu below the button
        menu.exec(self.transcript_filter_button.mapToGlobal(
            QPoint(0, self.transcript_filter_button.height())))
    
    def _create_status_bar(self):
        """Create the status bar."""
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage('Ready')
        self.status_bar.hide()
    
    def _create_transcription_view(self) -> QWidget:
        """Create the right-panel pixel transcript stream."""
        wrapper = PixelPanel(inner=True)
        wrapper.setObjectName("TranscriptViewport")
        wrapper_layout = QVBoxLayout(wrapper)
        # Keep the scroll area inside PixelPanel's painted border (drawn at
        # rect.adjusted(2, 2, -3, -3) with a 2px pen); with zero margins a
        # bubble scrolled past the top/bottom was drawn over the frame.
        wrapper_layout.setContentsMargins(4, 4, 5, 5)
        wrapper_layout.setSpacing(0)

        self._transcription_filter_combo = QComboBox()
        self._transcription_filter_combo.addItem("All", "all")
        self._transcription_filter_combo.addItem("Mic", "mic")
        self._transcription_filter_combo.addItem("System", "system")
        self._transcription_filter_combo.currentIndexChanged.connect(self._on_transcription_filter_changed)
        self._transcription_filter_combo.setVisible(False)
        # BU119: the combo starts on whatever filter was restored, not on "All".
        stored_index = self._transcription_filter_combo.findData(self._transcription_filter)
        if stored_index > 0:
            self._transcription_filter_combo.setCurrentIndex(stored_index)

        self._transcription_scroll_area = QScrollArea()
        self._transcription_scroll_area.setWidgetResizable(True)
        self._transcription_scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._transcription_scroll_area.verticalScrollBar().valueChanged.connect(
            self._on_transcription_scroll_changed
        )
        self._transcription_scroll_area.verticalScrollBar().rangeChanged.connect(
            self._on_transcription_range_changed
        )

        self._transcription_container = QWidget()
        self._transcription_scroll_area.setWidget(self._transcription_container)

        self._transcription_layout = QVBoxLayout(self._transcription_container)
        self._transcription_layout.setSpacing(18)
        self._transcription_layout.setContentsMargins(12, 14, 12, 14)
        self._transcription_layout.addStretch()

        wrapper_layout.addWidget(self._transcription_filter_combo)
        wrapper_layout.addWidget(self._transcription_scroll_area, 1)
        return wrapper

    def add_transcription_to_view(self, text: str, source: str, timestamp: str = '',
                                   start_dt=None, end_dt=None, animate: bool = True):
        """Add a transcription chunk to the pixel transcript window.

        Grouped per source (BU103): consecutive chunks with no real pause
        between them extend the source's currently open bubble instead of
        starting a new one, so a continuous stretch of talk reads as one
        growing bubble with a single ``HH:MM`` timestamp. ``start_dt``/
        ``end_dt`` (datetimes) drive that decision when provided; without
        them (unknown timing) every chunk starts its own bubble, as before.

        Args:
            animate: Play the fade-in on a freshly inserted bubble. Loading a
                past session replays its whole transcript at once through
                this method (see _load_transcripts_for_session) and passes
                False, so opening a long session doesn't fire one
                QPropertyAnimation per historical line.
        """
        group = self._transcript_groups.get(source)
        now = datetime.now()

        if should_extend_group(group, start_dt, end_dt):
            # No fade here: this row's bubble is actively resizing (growing
            # taller as text wraps), and re-triggering a QGraphicsOpacityEffect
            # on a widget mid-resize - especially while it's scrolled out of
            # the viewport - corrupted its rendering. Only a freshly inserted
            # bubble (below, whose size is settled before the fade starts)
            # gets the fade-in.
            bubble = group['bubble']
            bubble.append_text(text)
            merged_find_text = f"{group['row'].property('find_text')} {text}"
            group['row'].setProperty('find_text', merged_find_text)
            group['last_end'] = end_dt
            return

        display_source = "Mic" if source == 'mic' else "System"
        if start_dt is not None:
            bubble_time = start_dt.strftime('%H:%M')
        elif timestamp:
            bubble_time = timestamp[:5]
        else:
            bubble_time = ''
        display_text = f"{display_source}: {text}"
        align = "right" if source == 'mic' else "left"
        variant = "blue" if source == 'mic' else "cream"

        row = aligned_bubble_with_time(
            display_text, variant=variant, align=align, max_width=250, time_text=bubble_time
        )
        row.setProperty('source', source)
        row.setProperty('find_text', f"{display_source}: {text}")
        row.setToolTip(timestamp or '')

        if self._transcription_filter == 'mic' and source != 'mic':
            row.hide()
        elif self._transcription_filter == 'system' and source != 'system':
            row.hide()

        # Sticking to the bottom (or not) is handled by _on_transcription_range_changed,
        # which reacts once the scroll area's content has actually finished resizing
        # for this new bubble - inserting here is enough to trigger it.
        self._transcription_layout.insertWidget(
            self._transcription_layout.count() - 1,
            row
        )

        self._transcript_groups[source] = {
            'row': row,
            'bubble': bubble_of_row(row),
            'start_dt': start_dt or now,
            'last_end': end_dt or now,
        }
        if animate:
            self._play_transcript_fade(row)

    def _play_transcript_fade(self, row: QWidget):
        """Brief opacity fade-in on a freshly inserted bubble row (BU103).

        The QGraphicsOpacityEffect is removed again as soon as the animation
        finishes, rather than left attached: leaving it on the row meant any
        *later* resize of that same row (the bubble growing via
        ``append_text``) still ran through a graphics effect that was never
        re-triggered for the new size, which corrupted the row's rendering
        when it happened off-screen (scrolled out of the viewport). With the
        effect removed right after use, a later append resizes a perfectly
        plain widget.
        """
        effect = QGraphicsOpacityEffect(row)
        effect.setOpacity(0.35)
        row.setGraphicsEffect(effect)

        anim = QPropertyAnimation(effect, b"opacity", row)
        anim.setDuration(200)
        anim.setStartValue(0.35)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.OutCubic)

        def _cleanup():
            if row.graphicsEffect() is effect:
                row.setGraphicsEffect(None)
            row.setProperty('_fade_anim', None)

        anim.finished.connect(_cleanup)
        row.setProperty('_fade_anim', anim)  # keep a live reference while running
        anim.start()

    def _on_transcription_scroll_changed(self, value: int):
        """Track whether the transcript stream is scrolled to the bottom.

        Updates _transcription_autoscroll live as the user scrolls, so new
        transcripts only auto-follow once the user is actually back at the
        bottom - scrolling up stops the auto-follow, and it resumes as soon
        as they scroll back down themselves.
        """
        scroll_bar = self._transcription_scroll_area.verticalScrollBar()
        self._transcription_autoscroll = (scroll_bar.maximum() - value) <= 40

    def _on_transcription_range_changed(self, minimum: int, maximum: int):
        """Keep the transcript stream pinned to the bottom while auto-follow is on.

        Fires whenever the scroll content's height actually changes (new bubble
        added, filter toggled, etc.) - including any later pass where a bubble's
        wrapped text grows its final height - so the view always lands on the
        true last message instead of snapping early to a not-yet-final maximum.
        """
        if self._transcription_autoscroll:
            self._transcription_scroll_area.verticalScrollBar().setValue(maximum)

    def _sync_inserted_transcript_chrome(self):
        """Show the "Inserted Transcript" caption and switch the filter off
        while an inserted transcript is displayed (BU138)."""
        inserted = self._displaying_inserted_transcript
        caption = getattr(self, '_inserted_transcript_caption', None)
        if caption is not None:
            caption.setVisible(inserted)
        button = getattr(self, 'transcript_filter_button', None)
        if button is not None:
            button.setEnabled(not inserted)
            button.setToolTip(
                "Not available for inserted transcripts" if inserted else "Cycle transcript filter")
        self._sync_detached_inserted_mode()

    DETACHED_ANSWERS_HINT = (
        "Click or drag across transcript chunks, then right-click to ask about them")
    DETACHED_ANSWERS_HINT_INSERTED = "Answers aren't available for inserted transcripts"

    def _sync_detached_inserted_mode(self):
        """Switch the detached window's transcript-only controls off while an
        inserted transcript is displayed (BU139).

        Disabled and unchecked, but the saved Live QA modes are never touched:
        the chip-sync helpers repaint them from those modes as soon as a
        recorded session is shown again.
        """
        if not getattr(self, '_detached_window', None):
            return
        inserted = self._displaying_inserted_transcript

        caption = getattr(self, '_detached_inserted_caption', None)
        if caption is not None:
            caption.setVisible(inserted)

        self._detached_filter_button.setEnabled(not inserted)
        self._detached_filter_button.setToolTip(
            "Not available for inserted transcripts" if inserted else "Filter transcripts")
        self._live_qa_settings_button.setEnabled(not inserted)
        for chip in (*self._live_qa_mode_chips.values(),
                     *self._live_qa_answer_mode_chips.values()):
            chip.setEnabled(not inserted)
        self._sync_live_qa_mode_chips()
        self._sync_live_qa_answer_mode_chips()

        empty = getattr(self, '_detached_answers_empty', None)
        if empty is not None:
            empty.setText(
                self.DETACHED_ANSWERS_HINT_INSERTED if inserted else self.DETACHED_ANSWERS_HINT)

    def _add_inserted_paragraph_to_view(self, text: str):
        """One plain cream bubble per paragraph: no prefix, no time, no
        grouping (BU138)."""
        row = aligned_bubble_with_time(
            text, variant='cream', align='left', max_width=250, time_text='')
        row.setProperty('source', 'inserted')
        row.setProperty('find_text', text)
        self._transcription_layout.insertWidget(self._transcription_layout.count() - 1, row)

    def _clear_transcription_view(self):
        """Clear all transcriptions from the view."""
        # Clear history
        self._transcript_records = []

        # Reset viewing flag - go back to live mode
        self._viewing_historical_transcripts = False
        self._displaying_inserted_transcript = False
        self._sync_inserted_transcript_chrome()
        self._transcription_autoscroll = True
        self._detached_autoscroll = True
        self._transcript_groups = {}
        self._detached_transcript_groups = {}
        self._detached_last_end = None
        self._detached_selected_chunks = []
        self._detached_selection_anchor = None
        self._detached_drag_active = False

        if hasattr(self, '_transcription_layout') and self._transcription_layout:
            # Remove all widgets except the stretch (last item)
            while self._transcription_layout.count() > 1:
                item = self._transcription_layout.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()

        # The detached stream shows the same records, and its rows carry
        # record_indices into them (BU111) - leaving it populated after the
        # records are dropped would leave those indices pointing at nothing.
        if getattr(self, '_detached_layout', None):
            while self._detached_layout.count() > 1:
                item = self._detached_layout.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()
    
    def _add_message_to_conversation(self, role: str, text: str):
        """Add a pixel-styled message bubble to the conversation view."""
        if not hasattr(self, '_answer_layout') or not self._answer_layout:
            return None

        if text == "Thinking..." and self._thinking_message_widget:
            self._thinking_message_widget.deleteLater()
            self._thinking_message_widget = None

        align = "right" if role == 'user' else "left"
        variant = "cream" if role == 'user' else "blue"
        row = aligned_bubble(text, variant=variant, align=align, max_width=400)
        row.setProperty('role', role)
        row.setProperty('message_text', text)

        if text == "Thinking...":
            self._thinking_message_widget = row

        self._answer_layout.insertWidget(
            self._answer_layout.count() - 1,
            row
        )

        self._scroll_answer_to_bottom()

        if hasattr(self, '_detached_answer_layout') and self._detached_answer_layout:
            self._add_message_to_detached_conversation(role, text)

        return row

    def _scroll_answer_to_bottom(self):
        """Jump the chat to its last message and keep following it.

        Sending or receiving a message always brings the chat down, even if
        the user had scrolled up. The jump itself happens in
        _on_answer_range_changed, once the new bubble has been laid out.
        """
        self._answer_autoscroll = True
        scroll_bar = self._answer_scroll_area.verticalScrollBar()
        scroll_bar.setValue(scroll_bar.maximum())

    def _on_answer_scroll_changed(self, value: int):
        """Track whether the chat is scrolled to the bottom."""
        scroll_bar = self._answer_scroll_area.verticalScrollBar()
        self._answer_autoscroll = (scroll_bar.maximum() - value) <= 40

    def _on_answer_range_changed(self, minimum: int, maximum: int):
        """Keep the chat pinned to the bottom while auto-follow is on."""
        if getattr(self, '_answer_autoscroll', True):
            self._answer_scroll_area.verticalScrollBar().setValue(maximum)

    def _clear_conversation_view(self):
        """Clear all messages from the conversation view."""
        if hasattr(self, '_answer_layout') and self._answer_layout:
            # Remove all widgets except the stretch (last item)
            while self._answer_layout.count() > 1:
                item = self._answer_layout.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()
        # Reset thinking message tracker
        self._thinking_message_widget = None
        # A rebuilt view holds no live scope prompt (BU093)
        self._pending_scope_prompt = None

    def _replace_thinking_message(self, new_text: str):
        """Replace the pixel 'Thinking...' bubble with the assistant response."""
        if not hasattr(self, '_answer_layout') or not self._answer_layout:
            return

        if self._thinking_message_widget:
            index = self._answer_layout.indexOf(self._thinking_message_widget)
            if index >= 0:
                self._answer_layout.takeAt(index)
                self._thinking_message_widget.deleteLater()

        row = aligned_bubble(new_text, variant="blue", align="left", max_width=400)
        row.setProperty('role', 'assistant')
        row.setProperty('message_text', new_text)
        self._answer_layout.insertWidget(
            self._answer_layout.count() - 1,
            row
        )
        self._thinking_message_widget = None

        self._scroll_answer_to_bottom()

        if hasattr(self, '_detached_answer_layout') and self._detached_answer_layout:
            self._add_message_to_detached_conversation('assistant', new_text)

    def _add_message_to_detached_conversation(self, role: str, text: str):
        """Add a message to the detached window's conversation view.
        
        Args:
            role: 'user' or 'assistant'
            text: The message text
        """
        if not hasattr(self, '_detached_answer_layout') or not self._detached_answer_layout:
            return
        
        # Create a bubble frame
        bubble_frame = QFrame()
        bubble_frame.setFrameShape(QFrame.StyledPanel)
        bubble_frame.setFrameShadow(QFrame.Raised)
        
        # Set layout for the bubble
        bubble_layout = QVBoxLayout(bubble_frame)
        bubble_layout.setContentsMargins(10, 8, 10, 8)
        bubble_layout.setSpacing(4)
        
        # Header with role label
        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        
        role_label = QLabel(f"{'You' if role == 'user' else 'Assistant'}")
        role_font = role_label.font()
        role_font.setPointSize(10)
        role_font.setBold(True)
        role_label.setFont(role_font)
        
        header_layout.addWidget(role_label)
        header_layout.addStretch()
        bubble_layout.addLayout(header_layout)
        
        # Message text
        text_label = QLabel(text)
        text_label.setWordWrap(True)
        text_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        bubble_layout.addWidget(text_label)
        
        # Style based on role
        if role == 'user':
            bubble_frame.setStyleSheet("""
                QFrame {
                    background-color: #E3F2FD;
                    border-radius: 10px;
                    border: 1px solid #90CAF9;
                }
            """)
        else:
            bubble_frame.setStyleSheet("""
                QFrame {
                    background-color: #E8F5E9;
                    border-radius: 10px;
                    border: 1px solid #A5D6A7;
                }
            """)
        
        # Add to layout (before the stretch)
        self._detached_answer_layout.insertWidget(
            self._detached_answer_layout.count() - 1,
            bubble_frame
        )
        
        # Store role and text as properties for later retrieval
        bubble_frame.setProperty('role', role)
        bubble_frame.setProperty('message_text', text)
        
        # Auto-scroll to bottom
        self._detached_answer_scroll.verticalScrollBar().setValue(
            self._detached_answer_scroll.verticalScrollBar().maximum()
        )

    def _on_transcription_filter_changed(self, index: int):
        """Handle the transcription filter selection change.
        
        Args:
            index: The index of the selected filter option
        """
        # Get the selected filter value from combo box
        filter_value = self._transcription_filter_combo.currentData()
        self._transcription_filter = filter_value
        self._save_transcription_filter()  # BU119

        # Update visibility of all transcription bubbles
        self._update_transcription_filter()
    
    def _update_transcription_filter(self):
        """Update the visibility of transcriptions based on the current filter."""
        if not hasattr(self, '_transcription_layout') or not self._transcription_layout:
            return
        
        # Iterate through all transcription bubble widgets
        # The last item is the stretch, so we iterate up to count - 1
        for i in range(self._transcription_layout.count() - 1):
            item = self._transcription_layout.itemAt(i)
            if item and item.widget():
                bubble_frame = item.widget()
                # Get the source stored in the bubble
                source = bubble_frame.property('source')
                
                # Show or hide based on filter
                if self._transcription_filter == 'all':
                    bubble_frame.show()
                elif self._transcription_filter == 'mic':
                    if source == 'mic':
                        bubble_frame.show()
                    else:
                        bubble_frame.hide()
                elif self._transcription_filter == 'system':
                    if source == 'system':
                        bubble_frame.show()
                    else:
                        bubble_frame.hide()

    def _on_detached_scope_changed(self, index: int):
        """Propagate a detached-window scope change to the main combo."""
        if getattr(self, '_scope_sync_guard', False):
            return
        self._scope_sync_guard = True
        try:
            # Fires _on_scope_changed, which performs the transcript reload;
            # the guard only suppresses the echo back to the detached combo.
            self.scope_combo.setCurrentIndex(index)
        finally:
            self._scope_sync_guard = False

    def _on_scope_changed(self, index: int):
        """Handle scope selection change to load session transcripts.
        
        Args:
            index: The index of the selected scope option
        """
        scope_value = self.scope_combo.currentData()
        logger.info(f"Scope changed to: {scope_value}, selected_session_id: {self._selected_session_id}")

        # Keep the detached scope combo in sync (guarded against signal recursion)
        if not getattr(self, '_scope_sync_guard', False):
            if hasattr(self, '_detached_scope_combo') and self._detached_scope_combo:
                self._scope_sync_guard = True
                try:
                    self._detached_scope_combo.setCurrentIndex(index)
                finally:
                    self._scope_sync_guard = False

        # Skip transcript reload when triggered from _clear_scope (it handles that itself)
        if getattr(self, '_clearing_scope', False):
            self._update_scope_label()
            return

        # Clear current transcriptions when scope changes
        self._clear_transcription_view()

        if scope_value == 'current':
            # Load transcripts from the current/active session
            # Allow live transcriptions to be shown (use allow_live=True)
            self._load_session_transcripts_for_current_session(allow_live=True)
        elif scope_value == 'any':
            # Any Session scope doesn't pin a session name in the search bar
            if getattr(self, '_search_input_shows_session', False):
                self._search_input_shows_session = False
                self.session_search_input.clear()
            # When scope is "Any Session", check if a specific session is selected
            if self._selected_session_id is not None:
                self._load_transcripts_for_session(self._selected_session_id)
            # Otherwise, keep it empty (waiting for session selection)
        
        # Update the scope label
        self._update_scope_label()
    
    def _load_session_transcripts_for_current_session(self, allow_live: bool = False):
        """Load transcripts from the current/active session.
        
        Args:
            allow_live: If True, allow live transcriptions to be shown
        """
        try:
            # Selected session from UI first, then the active/recording one.
            session_id = self._current_display_session_id()

            if session_id is not None:
                self._load_transcripts_for_session(session_id, allow_live=allow_live)
                logger.info(f"Loaded transcripts for current session: {session_id}")
            else:
                self._on_status_update("No session selected or active")
        except Exception as e:
            logger.error(f"Failed to load transcripts for current session: {e}")
    
    def _load_transcripts_for_session(self, session_id: int, allow_live: bool = False):
        """Load and display transcripts for a specific session.
        
        Args:
            session_id: The session ID to load transcripts for
            allow_live: If True, allow live transcriptions to be shown alongside historical ones
        """
        try:
            logger.info(f"Loading transcripts for session {session_id}")
            
            # Set flag to indicate we're viewing historical transcripts (unless allow_live is True)
            self._viewing_historical_transcripts = not allow_live
            inserted = self.session_manager.db.is_inserted_transcript(session_id)
            self._displaying_inserted_transcript = inserted
            self._sync_inserted_transcript_chrome()

            # Get transcripts from database
            transcripts = self.session_manager.db.get_transcripts(session_id)
            
            if not transcripts:
                # Don't show message for active/current session - no transcripts expected yet
                if not allow_live:
                    logger.info(f"No transcripts found for session {session_id}")
                    self._on_status_update(f"No transcripts found for session {session_id}")
                return
            
            logger.info(f"Found {len(transcripts)} transcripts for session {session_id}")
            
            # Sort transcripts by timestamp
            sorted_transcripts = sorted(transcripts, key=lambda t: t.get('timestamp', 0))
            
            # Add each transcript to the view
            for transcript in sorted_transcripts:
                text = transcript.get('text', '')
                source = transcript.get('source', 'microphone')
                timestamp = transcript.get('timestamp', 0)
                
                if not text:
                    continue

                if inserted:
                    self._transcript_records.append(TranscriptRecord(
                        text=text, source='inserted', start_dt=None, end_dt=None,
                        transcript_id=transcript.get('id'), display_text=text))
                    self._add_inserted_paragraph_to_view(text)
                    if getattr(self, '_detached_window', None):
                        self._add_transcription_to_detached(
                            self._transcript_records[-1], len(self._transcript_records) - 1
                        )
                    continue

                # Normalize source: 'microphone' -> 'mic', otherwise keep as-is
                if source == 'microphone':
                    source = 'mic'
                # Also handle case where source might be stored as 'mic' already
                elif source == 'mic':
                    pass  # Already normalized
                # Otherwise keep as-is (e.g., 'system')
                
                # Format timestamp
                time_str = ''
                start_dt = None
                if timestamp:
                    try:
                        start_dt = datetime.fromtimestamp(timestamp)
                        time_str = start_dt.strftime('%H:%M:%S')
                    except:
                        time_str = str(timestamp)

                # Format display text
                source_label = 'Mic' if source == 'mic' else 'System'
                display_text = f"[{time_str}] {source_label}: {text}" if time_str else f"{source_label}: {text}"

                end_dt = (
                    start_dt + timedelta(seconds=ChunkedAudioRecorder.CHUNK_DURATION)
                    if start_dt else None
                )
                record = TranscriptRecord(
                    text=text,
                    source=source,
                    start_dt=start_dt,
                    end_dt=end_dt,
                    transcript_id=transcript.get('id'),
                    display_text=display_text,
                )
                self._transcript_records.append(record)

                # Add to view (using the same grouped call live results use, so
                # a reloaded session renders with the same grouped bubbles).
                # animate=False: this is a bulk replay of history, not a
                # chunk arriving live - a fade-in per bubble here meant
                # opening a long session fired dozens of animations at once.
                self.add_transcription_to_view(
                    text, source, time_str, start_dt=start_dt, end_dt=end_dt, animate=False
                )

                # Also add to detached window if it exists
                if getattr(self, '_detached_window', None):
                    self._add_transcription_to_detached(
                        record, len(self._transcript_records) - 1
                    )
            
            logger.info(f"Loaded {len(sorted_transcripts)} transcripts for session {session_id}")
            self._on_status_update(f"Loaded {len(sorted_transcripts)} transcripts")
            
        except Exception as e:
            logger.error(f"Failed to load transcripts for session {session_id}: {e}")
            self._on_status_update(f"Error loading transcripts: {str(e)}", is_error=True)

    def _resolve_transcript_session_name(self) -> str:
        """Resolve a filesystem-safe session name for the current transcript view."""
        session_name = None
        try:
            if self._selected_session_id is not None:
                session = self.session_manager.db.get_session(self._selected_session_id)
                if session:
                    session_name = session.get('name')
            elif self.session_manager:
                active_session = self.session_manager.get_active_session()
                if active_session:
                    session_name = active_session.name
        except Exception as e:
            logger.error(f"Failed to resolve session name for transcript download: {e}")

        session_name = session_name or "session"
        safe_name = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in session_name)
        return safe_name or "session"

    def _on_download_transcripts(self):
        """Save the currently displayed transcript stream to a .txt file in Downloads."""
        if not self._transcription_history:
            self._on_status_update("No transcripts to download")
            return

        try:
            from datetime import datetime

            downloads_dir = QStandardPaths.writableLocation(QStandardPaths.DownloadLocation)
            if not downloads_dir:
                self._on_status_update("Could not find Downloads folder", is_error=True)
                return

            session_name = self._resolve_transcript_session_name()
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename = f"chronicle_transcript_{session_name}_{timestamp}.txt"
            file_path = os.path.join(downloads_dir, filename)

            # Inserted transcripts export the paragraphs as-is, blank line between.
            separator = "\n\n" if self._displaying_inserted_transcript else "\n"
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(separator.join(self._transcription_history))

            self._on_status_update(f"Transcript downloaded: {filename}")
        except Exception as e:
            logger.error(f"Failed to download transcript: {e}")
            self._on_status_update(f"Failed to download transcript: {str(e)}", is_error=True)

    def _on_status_update(self, message: str, is_error: bool = False):
        """Status-update entry point passed to SessionManager as its
        status_callback, and called directly all over this file.

        SessionManager now fires this from its live-transcription worker and
        job threads (not just the UI thread), so it does nothing but emit a
        signal; Qt.QueuedConnection marshals the call onto the UI thread
        before _apply_status_update touches any widget - including when this
        is called from the UI thread itself, so every caller keeps working
        unchanged.
        """
        self._status_ready.emit(message, is_error)

    @Slot(str, bool)
    def _apply_status_update(self, message: str, is_error: bool = False):
        """Actually update the status bar / app log. UI thread only."""
        logger.debug(f"Status update: {message}")
        self.status_label.setText(message)
        self.status_bar.showMessage(message)

        if is_error:
            self.status_label.setStyleSheet("color: red;")
        else:
            self.status_label.setStyleSheet("")

        # Also show in app logs widget
        self._add_app_log(message, is_error)

    def _on_session_finalized(self, session_id: int, outcome: dict):
        """SessionManager callback (background job thread) fired once a
        stopped session's transcribe/index/summarize pass has finished.
        Only emits - see _on_status_update for why."""
        self._session_finalized_ready.emit(session_id, outcome)

    @Slot(int, object)
    def _apply_session_finalized(self, session_id: int, outcome: dict):
        """Report a background finalize's outcome and refresh anything that
        shows session status. UI thread only."""
        self._finalizing_session_ids.discard(session_id)
        outcome = outcome or {}
        error = outcome.get('error')
        if error:
            self._on_status_update(
                f"Finishing session {session_id} failed: {error}", is_error=True
            )
        else:
            bits = []
            if outcome.get('transcribed'):
                bits.append(f"{outcome['transcribed']} chunk(s) transcribed")
            if outcome.get('summarized'):
                bits.append('summary generated')
            detail = ', '.join(bits) if bits else 'already up to date'
            self._on_status_update(f"Session {session_id} finalized ({detail})")

        self._refresh_session_completer()
        self._update_summary_icon_state()
        # An open All Sessions window won't otherwise notice a background
        # finalize completing (it only reloads on its own actions / the
        # 600ms live-indicator tick), so refresh it here too.
        if self._all_sessions_dialog is not None:
            self._refresh_all_sessions_window(self._all_sessions_dialog)

    def _post_to_ui(self, fn: Callable[[], None]) -> None:
        """Schedule ``fn()`` to run on the UI thread.

        A SessionManager.submit_job() completion callback runs on its
        background job thread; wrap any UI-touching code in it with this
        (rather than calling it directly) before it reaches a widget.
        Safe to call from the UI thread too - it just adds one queued event.
        """
        self._ui_callback_ready.emit(fn)

    @Slot(object)
    def _run_ui_callback(self, fn: Callable[[], None]) -> None:
        fn()

    def _add_app_log(self, message: str, is_error: bool = False):
        """Add a log message to the app logs widget (shows only the last message).

        Args:
            message: The log message to display
            is_error: Whether this is an error message (not used - same style for all)
        """
        if not hasattr(self, 'app_logs_layout'):
            return

        # Remove previous log message (only keep one)
        while self._app_logs_messages:
            old_log = self._app_logs_messages.pop()
            old_log.deleteLater()

        # Create log message as clickable label
        log_label = QPushButton(message)
        log_label.setObjectName("AppLogMessage")
        log_label.setCursor(Qt.PointingHandCursor)
        log_label.setFlat(True)

        # Mismo estilo: Courier New 10pt bold
        log_label.setStyleSheet("""
            QPushButton#AppLogMessage {
                color: #FFF0BF;
                background: transparent;
                border: none;
                text-align: left;
                padding: 0px 4px;
                font-family: 'Courier New';
                font-size: 10pt;
                font-weight: bold;
            }
            QPushButton#AppLogMessage:hover {
                background: transparent;
            }
            QPushButton#AppLogMessage:pressed {
                background: transparent;
            }
        """)

        # Make it clickable to hide
        log_label.clicked.connect(lambda: self._hide_app_log(log_label))

        # Insert before the stretch
        self.app_logs_layout.insertWidget(self.app_logs_layout.count() - 1, log_label)

        # Store reference
        self._app_logs_messages.append(log_label)

    def _hide_app_log(self, log_label):
        """Hide a log message when clicked."""
        log_label.hide()
        if log_label in self._app_logs_messages:
            self._app_logs_messages.remove(log_label)
    
    def _current_display_session_id(self) -> Optional[int]:
        """Id of the session whose transcript stream is on screen right now
        (selected session first, else the one actively recording), or None."""
        if self._selected_session_id is not None:
            return self._selected_session_id
        if self.session_manager:
            active_session = self.session_manager.get_active_session()
            if active_session:
                return active_session.id
        return None

    def _on_live_transcription(self, result: dict):
        """Handle live transcription results from the session manager.

        Args:
            result: Dictionary with keys: text, source, timestamp_start,
                timestamp_end, session_id

        Thread-safe: hops onto the UI thread via the `_live_transcription_ready`
        signal (queued across threads by Qt).

        Live transcription now runs on a queue that can lag behind by a few
        chunks (see TranscriptionWorker) rather than inline with capture, so a
        result reaching here can belong to a session the user has since
        stopped and moved away from - most commonly the tail end of a session
        arriving just after Stop, once the user has already started or opened
        a different one. session_id (present on every result since the perf
        rework) is checked against what's actually on screen so a straggler
        never gets appended to the wrong session's view.
        """
        # Skip if we're viewing historical transcripts (not live)
        if self._viewing_historical_transcripts:
            return

        result_session_id = result.get('session_id')
        if result_session_id is not None and result_session_id != self._current_display_session_id():
            return

        try:
            text = result.get('text', '')
            source = result.get('source', 'unknown')
            timestamp_start = result.get('timestamp_start', result.get('timestamp', '')) or ''
            timestamp_end = result.get('timestamp_end', '') or ''

            if not text:
                return

            # text, source, timestamp_start and timestamp_end are carried
            # through as-is (rather than pre-formatted into one display
            # string) so the grouping logic in add_transcription_to_view can
            # compare timestamp_end/timestamp_start across chunks to detect
            # pauses.
            self._live_transcription_ready.emit(
                text, source, str(timestamp_start), str(timestamp_end)
            )

        except Exception as e:
            logger.error(f"Failed to display live transcription: {e}")

    @Slot(str, str, str, str)
    def _append_transcription(self, text: str, source: str, timestamp_start: str, timestamp_end: str):
        """Append a transcription chunk to the display.

        Connected to `_live_transcription_ready` with Qt.QueuedConnection, so
        this always runs on the UI thread even though the signal is emitted
        from the audio/transcription thread.

        Args:
            text: The transcribed text
            source: 'mic' or 'system'
            timestamp_start: ISO-format start timestamp of the chunk, if known
            timestamp_end: ISO-format end timestamp of the chunk, if known
        """
        if self._displaying_inserted_transcript:
            return

        def _parse(value: str):
            if not value:
                return None
            try:
                return datetime.fromisoformat(value)
            except ValueError:
                return None

        start_dt = _parse(timestamp_start)
        end_dt = _parse(timestamp_end) or start_dt
        time_str = start_dt.strftime('%H:%M:%S') if start_dt else ''

        source_label = 'Mic' if source == 'mic' else 'System'
        display_text = f"[{time_str}] {source_label}: {text}" if time_str else f"{source_label}: {text}"

        record = TranscriptRecord(
            text=text,
            source=source,
            start_dt=start_dt,
            end_dt=end_dt,
            transcript_id=None,
            display_text=display_text,
        )
        self._transcript_records.append(record)

        # Use the grouped chat-like view method
        self.add_transcription_to_view(text, source, time_str, start_dt=start_dt, end_dt=end_dt)

        # The detached window applies the same grouping rule, over its own
        # group state (BU111).
        if getattr(self, '_detached_window', None):
            self._add_transcription_to_detached(record, len(self._transcript_records) - 1)

        # Auto mode only, and only for live chunks - replaying a past session
        # is not something to spend detector calls on (BU115).
        self._feed_question_detector(record, len(self._transcript_records) - 1)
    
    DETACHED_MIN_HEIGHT = 480

    def _on_detach_transcription(self):
        """Create a detached window for live transcriptions.

        Two columns (BU112): answers on the left, the transcript stream on the
        right. It always opens at its narrowest complete layout, in the
        bottom-right corner of the screen, so it sits beside whatever the user
        is working in rather than on top of it.
        """
        if self._detached_window:
            # Window already exists, just bring it to front
            self._detached_window.show()
            self._detached_window.activateWindow()
            self._detached_window.raise_()
            return

        # Hide the transcripts panel in the main UI. This is an explicit
        # reason to hide it, so auto-restore at full size must not override it.
        self._right_wing_user_hidden = True
        self.right_shell.hide()

        # Create detached window with no parent (standalone window)
        # This ensures it doesn't minimize when main window minimizes
        self._detached_window = DetachedTranscriptsDialog(None)  # standalone
        self._detached_window.setWindowTitle('Transcriptions')
        # Same backdrop as the main window and the title bar, so the margin
        # around the panel does not show the platform's light dialog colour.
        self._detached_window.setObjectName('DetachedTranscriptsWindow')
        self._detached_window.setStyleSheet(
            'QDialog#DetachedTranscriptsWindow { background: #0E2A6B; }'
        )
        # Minimum width comes from the columns' own layouts (see
        # _place_detached_compact); only the height has a floor here.
        self._detached_window.setMinimumHeight(self.DETACHED_MIN_HEIGHT)
        # No maximum size: allow the user to maximize / resize freely.
        self._detached_window.setSizeGripEnabled(True)

        # Set window flags. Stay-on-top is opt-in (the pin button in the
        # transcript header) rather than permanent, so the window can sit
        # behind whatever the user is actually working in.
        self._apply_detached_on_top(self._detached_window_on_top(), reshow=False)

        # Prevent the detached window from activating the main window when minimized
        self._detached_window.setAttribute(Qt.WA_QuitOnClose, False)

        # Connect the finished signal to restore the transcripts panel when closed
        self._detached_window.finished.connect(self._on_close_detached_window)
        self._detached_window.file_dropped.connect(self._attach_reference_doc)
        self._detached_window.drop_rejected.connect(
            lambda message: self._on_status_update(message, is_error=True)
        )

        # Use PixelPanel for consistent styling - same structure as main transcripts panel
        main_panel = PixelPanel()

        self._detached_window.setLayout(QVBoxLayout(self._detached_window))
        self._detached_window.layout().setContentsMargins(0, 0, 0, 0)
        self._detached_window.layout().addWidget(main_panel)

        layout = QVBoxLayout(main_panel)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        self._detached_splitter = QSplitter(Qt.Horizontal)
        self._detached_splitter.setChildrenCollapsible(False)
        self._detached_splitter.setHandleWidth(10)
        # The handle reads as the gutter between two panels, so it carries the
        # panel background rather than a colour of its own - each column
        # already has its own painted border to separate them.
        self._detached_splitter.setStyleSheet(
            "QSplitter::handle { background: transparent; }"
        )
        self._detached_splitter.addWidget(self._build_detached_answers_column())
        self._detached_transcript_column = self._build_detached_transcript_column()
        self._detached_splitter.addWidget(self._detached_transcript_column)
        self._detached_splitter.setStretchFactor(0, 0)
        self._detached_splitter.setStretchFactor(1, 1)
        layout.addWidget(self._detached_splitter, 1)

        # Hosted on the transcript column: that is the only pane it searches,
        # and it should travel with the column as the splitter moves.
        self._wire_detached_find_bar(self._detached_transcript_column)

        self._detached_clear_selection_shortcut = QShortcut(
            QKeySequence(Qt.Key_Escape), self._detached_window
        )
        self._detached_clear_selection_shortcut.activated.connect(
            self._clear_detached_selection
        )
        # Copy existing transcriptions from the records already on screen
        for index, record in enumerate(self._transcript_records):
            self._add_transcription_to_detached(record, index)
        self._sync_detached_inserted_mode()

        self._place_detached_compact()
        # Opened from the main window, so it comes up in front of it.
        self._detached_window.raise_()
        self._detached_window.activateWindow()
        self._on_status_update('Transcript Window Detached')

    def _build_detached_answers_column(self) -> QWidget:
        """Left column of the detached window: answer cards, newest first.

        Empty until BU113 fills it, so it ships with a hint explaining what
        puts something here rather than reading as dead space.
        """
        panel = PixelPanel(inner=True)
        # No fixed minimum width: the header rows' own minimum is the column's,
        # so neither the splitter nor a resize can clip a mode chip.

        outer = QVBoxLayout(panel)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(8)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(8)
        self._detached_answers_title = PixelSectionTitle("ANSWERS")
        header.addWidget(self._detached_answers_title)
        header.addStretch(1)

        self._live_qa_settings_button = PixelToolButton(compact=True)
        self._live_qa_settings_button.setText("⚙")
        self._live_qa_settings_button.setToolTip("Detection settings")
        self._live_qa_settings_button.setProperty("quietDisabled", True)
        self._live_qa_settings_button.clicked.connect(self._show_live_qa_menu)
        header.addWidget(self._live_qa_settings_button, 0)

        # Same control as the main window's sidebar toggle: collapses the
        # column to a rail holding only this button.
        # Compact, like the settings button beside it.
        self._detached_answers_toggle = PixelToolButton(compact=True)
        self._detached_answers_toggle.setIcon(self._make_icon("icon_menu.svg"))
        self._detached_answers_toggle.setText("☰")
        self._detached_answers_toggle.setToolTip("Collapse answers")
        self._detached_answers_toggle.clicked.connect(self._toggle_detached_answers)
        set_accent(self._detached_answers_toggle, "menu")
        header.addWidget(self._detached_answers_toggle, 0)
        self._detached_answers_header = header
        outer.addLayout(header)

        # Everything below the header, so collapsing hides it in one go.
        self._detached_answers_body = QWidget()
        body = QVBoxLayout(self._detached_answers_body)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(8)
        outer.addWidget(self._detached_answers_body, 1)
        self._detached_answers_panel = panel
        self._detached_answers_outer = outer
        self._detached_answers_collapsed = False

        # One chip per mode, so what the app will do is readable off the
        # header instead of hidden one level down in a menu. On their own row,
        # labelled like the ANSWER row under it: sharing a row with the title
        # made the header alone wider than the whole compact window.
        detect_row = QHBoxLayout()
        detect_row.setContentsMargins(0, 0, 0, 0)
        detect_row.setSpacing(6)
        detect_row.addWidget(pixel_group_label("DETECT"), 0)
        self._live_qa_mode_chips = {}
        for mode, label, tip in (
            ('manual', "Manual",
             "No detection. Select chunks and right-click to ask."),
            ('suggest', "Suggest",
             "Detect questions and offer them as cards. Nothing is answered "
             "until you click."),
            ('auto', "Auto",
             "Detect questions and answer them immediately. Costs a detector "
             "call and an answer call per question."),
        ):
            chip = pixel_filter_chip(label, compact=True)
            chip.setMinimumHeight(28)
            chip.setToolTip(tip)
            chip.clicked.connect(lambda _checked=False, m=mode: self._set_live_qa_mode(m))
            detect_row.addWidget(chip, 0)
            self._live_qa_mode_chips[mode] = chip
        detect_row.addStretch(1)
        self._sync_live_qa_mode_chips()
        body.addLayout(detect_row)

        # BU116: the answer modes get their own row under their own label. On
        # one row with the detection chips they would read as a five-way
        # control, and they answer a different question - not whether to
        # answer, but how.
        answer_row = QHBoxLayout()
        answer_row.setContentsMargins(0, 0, 0, 0)
        answer_row.setSpacing(6)
        answer_row.addWidget(pixel_group_label("ANSWER"), 0)

        self._live_qa_answer_mode_chips = {}
        for entry in LIVE_QA.get('answer_modes', []):
            chip = pixel_filter_chip(entry['label'], compact=True)
            chip.setMinimumHeight(26)
            chip.setToolTip(entry.get('note', ''))
            chip.clicked.connect(
                lambda _checked=False, m=entry['id']: self._set_live_qa_answer_mode(m)
            )
            answer_row.addWidget(chip, 0)
            self._live_qa_answer_mode_chips[entry['id']] = chip
        answer_row.addStretch(1)
        self._sync_live_qa_answer_mode_chips()
        body.addLayout(answer_row)

        status_row = QHBoxLayout()
        status_row.setContentsMargins(0, 0, 0, 0)
        status_row.setSpacing(8)
        self._live_qa_spend_chip = pixel_spend_chip("")
        status_row.addWidget(self._live_qa_spend_chip, 0)
        status_row.addStretch(1)

        # BU117: only present once something is attached. An empty placeholder
        # chip would take the same room and say nothing.
        self._reference_chip = PixelReferenceChip()
        self._reference_chip.replace_requested.connect(self._pick_reference_doc)
        self._reference_chip.remove_requested.connect(self._clear_reference_doc)
        status_row.addWidget(self._reference_chip, 0)
        body.addLayout(status_row)

        self._refresh_live_qa_spend_chip()
        self._sync_reference_chip()

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.verticalScrollBar().setSingleStep(DETACHED_SCROLL_PIXELS_PER_STEP)
        scroll.setStyleSheet("background-color: #071D52; border: none;")
        self._detached_answers_scroll_area = scroll

        container = QWidget()
        container.setStyleSheet("background-color: #071D52;")
        scroll.setWidget(container)

        self._detached_answers_layout = QVBoxLayout(container)
        self._detached_answers_layout.setSpacing(10)
        self._detached_answers_layout.setContentsMargins(2, 2, 2, 2)

        self._detached_answers_empty = pixel_empty_hint(self.DETACHED_ANSWERS_HINT)
        self._detached_answers_layout.addWidget(self._detached_answers_empty)
        self._detached_answers_layout.addStretch()

        body.addWidget(scroll, 1)

        self._detached_answers_rail_spacer = QWidget()
        self._detached_answers_rail_spacer.setSizePolicy(
            QSizePolicy.Minimum, QSizePolicy.Expanding
        )
        self._detached_answers_rail_spacer.setVisible(False)
        outer.addWidget(self._detached_answers_rail_spacer, 1)
        return panel

    def _toggle_detached_answers(self):
        self._set_detached_answers_collapsed(not self._detached_answers_collapsed)

    # Side margin of the collapsed answers rail around its toggle button.
    DETACHED_RAIL_MARGIN = 6

    def _set_detached_answers_collapsed(self, collapsed: bool):
        """Collapse the answers column to a rail holding only its toggle.

        The window keeps its position on screen and only its width changes by
        what the column gains or loses, so the transcript column keeps its
        size. Collapsed, the window's minimum drops to the transcript column
        plus the rail, so it can be squeezed down to just the transcripts.
        """
        window = self._detached_window
        panel = self._detached_answers_panel
        if not window or panel is None or collapsed == self._detached_answers_collapsed:
            return
        self._detached_answers_collapsed = collapsed
        old_width = panel.width()
        # Read before anything changes: showing the column lets Qt grow the
        # window on its own.
        position = window.pos()
        window_width = window.width()
        window_min = window.minimumWidth()

        self._detached_answers_title.setVisible(not collapsed)
        self._live_qa_settings_button.setVisible(not collapsed)
        self._detached_answers_body.setVisible(not collapsed)
        self._detached_answers_rail_spacer.setVisible(collapsed)

        button = self._detached_answers_toggle
        if collapsed:
            button.setToolTip("Expand answers")
            margin = self.DETACHED_RAIL_MARGIN
            self._detached_answers_outer.setContentsMargins(margin, 10, margin, 10)
            # Exactly the button plus its margins, so it sits centred.
            panel.setFixedWidth(button.width() + 2 * margin)
        else:
            button.setToolTip("Collapse answers")
            self._detached_answers_outer.setContentsMargins(10, 10, 10, 10)
            panel.setMinimumWidth(0)
            panel.setMaximumWidth(16777215)

        window.layout().activate()
        new_width = panel.width() if collapsed else panel.minimumSizeHint().width()
        transcript_width = self._detached_splitter.widget(1).width()
        delta = new_width - old_width
        # The splitter's size hints lag behind the column's, so the minimum
        # moves by exactly what the column gains or loses.
        window.setMinimumWidth(max(window_min + delta, 0))
        window.resize(max(window_width + delta,
                          window.minimumWidth()), window.height())
        window.move(position)
        self._detached_splitter.setSizes([new_width, max(transcript_width, 1)])

    def _build_detached_transcript_column(self) -> QWidget:
        """Right column of the detached window: the transcript stream itself.

        The filter control lives in this column's header rather than the
        window's, now that the window holds two columns and the filter only
        governs one of them.
        """
        panel = PixelPanel(inner=True)
        panel.setObjectName("TranscriptViewport")
        panel.setMinimumWidth(320)

        outer = QVBoxLayout(panel)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(8)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(10)
        header.addWidget(PixelSectionTitle("TRANSCRIPTS"))
        header.addStretch(1)

        self._detached_filter_button = PixelToolButton(compact=True)
        self._detached_filter_button.setIcon(self._make_icon("icon_filter_smooth.svg"))
        self._detached_filter_button.setToolTip("Filter transcripts")
        self._detached_filter_button.setProperty("quietDisabled", True)
        self._detached_filter_button.clicked.connect(self._show_detached_filter_menu)
        header.addWidget(self._detached_filter_button)

        self._detached_pin_button = PixelToolButton(compact=True)
        self._detached_pin_button.setCheckable(True)
        self._detached_pin_button.clicked.connect(self._toggle_detached_on_top)
        header.addWidget(self._detached_pin_button)
        self._sync_detached_pin_button()
        outer.addLayout(header)

        # BU139: shown only while an inserted transcript is displayed.
        self._detached_inserted_caption = self._make_inserted_caption(
            Qt.AlignLeft | Qt.AlignVCenter)
        outer.addWidget(self._detached_inserted_caption)

        # Hidden filter combo for state management
        self._detached_filter_combo = QComboBox()
        self._detached_filter_combo.addItem("All", "all")
        self._detached_filter_combo.addItem("Mic", "mic")
        self._detached_filter_combo.addItem("System", "system")
        self._detached_filter_combo.currentIndexChanged.connect(self._on_detached_filter_changed)
        self._detached_filter_combo.setVisible(False)
        outer.addWidget(self._detached_filter_combo)

        self._detached_scroll_area = QScrollArea()
        self._detached_scroll_area.setWidgetResizable(True)
        self._detached_scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        # A QScrollArea already scrolls per pixel (setVerticalScrollMode is
        # item-view API and does not exist here), so what actually decides how
        # far a wheel notch travels is the scrollbar's single step - Qt
        # multiplies it by the system's wheel-scroll-lines. Pinning it to a
        # fixed pixel value is what makes a notch cover the same distance
        # whatever the height of the bubble under the cursor (BU112).
        self._detached_scroll_area.verticalScrollBar().setSingleStep(
            DETACHED_SCROLL_PIXELS_PER_STEP
        )
        # Scoped selectors: an unscoped background here would cascade onto
        # every bubble row and paint bands behind them.
        page_bg = NAVY_INNER.name()
        self._detached_scroll_area.setStyleSheet(
            f"QScrollArea {{ background-color: {page_bg}; border: none; }}"
            f" QScrollBar {{ background-color: {page_bg}; }}"
        )
        # Same stick-to-bottom behaviour as the main transcript panel: follow
        # new content only while the user is at the bottom, and re-pin on every
        # real range change so a bubble that grows after insertion still lands
        # in view.
        self._detached_autoscroll = True
        self._detached_bubble_width = DETACHED_BUBBLE_MAX_WIDTH
        self._detached_scroll_area.verticalScrollBar().valueChanged.connect(
            self._on_detached_scroll_changed
        )
        self._detached_scroll_area.verticalScrollBar().rangeChanged.connect(
            self._on_detached_range_changed
        )
        # Bubbles widen with the window (e.g. maximised) instead of staying at
        # a fixed width.
        self._detached_scroll_area.viewport().installEventFilter(self)

        self._detached_container = QWidget()
        # Same fill as the surrounding inner panel so the stream blends in.
        self._detached_container.setObjectName("DetachedTranscriptContainer")
        self._detached_container.setStyleSheet(
            f"QWidget#DetachedTranscriptContainer {{ background-color: {page_bg}; }}"
        )
        self._detached_scroll_area.setWidget(self._detached_container)

        self._detached_layout = QVBoxLayout(self._detached_container)
        self._detached_layout.setSpacing(18)
        self._detached_layout.setContentsMargins(12, 14, 12, 14)
        self._detached_layout.addStretch()

        # A press that reaches the container fell between bubbles, so it
        # clears the selection (BU113); presses on a bubble are handled by that
        # row's own filter and never get here.
        self._detached_container.installEventFilter(
            BubbleClickFilter(None, self._on_transcript_background_press,
                              parent=self._detached_container)
        )

        outer.addWidget(self._detached_scroll_area, 1)
        return panel

    def _on_transcript_background_press(self, _row, _obj, event) -> bool:
        if event.type() != QEvent.MouseButtonPress:
            # Move / release on empty space only matter while a drag that
            # started on a bubble is still running.
            if event.type() == QEvent.MouseButtonRelease:
                self._detached_drag_active = False
            return False
        if event.button() == Qt.RightButton:
            self._show_detached_transcript_menu(event.globalPosition().toPoint())
            return True
        if event.button() == Qt.LeftButton:
            self._clear_detached_selection()
        return False

    def _detached_window_on_top(self) -> bool:
        """Whether the detached transcripts window is pinned above others.

        Read lazily so the flag survives paths that reach the detached window
        without going through __init__.
        """
        if not hasattr(self, '_detached_on_top'):
            self._detached_on_top = bool(
                self._read_preferences().get('detached_window_on_top', False)
            )
        return self._detached_on_top

    def _sync_detached_pin_button(self):
        button = getattr(self, '_detached_pin_button', None)
        if button is None:
            return
        on_top = self._detached_window_on_top()
        button.setChecked(on_top)
        button.setText("◆" if on_top else "◇")
        button.setToolTip(
            "Unpin: let other windows cover this one"
            if on_top else
            "Pin: keep this window above other windows"
        )

    def _apply_detached_on_top(self, on_top: bool, reshow: bool = True):
        """Set the detached window's stay-on-top flag.

        Changing window flags on an already-shown window hides it on Windows,
        so a visible window has to be shown again afterwards; during creation
        (``reshow=False``) the caller shows it itself.
        """
        if not self._detached_window:
            return
        flags = (
            Qt.Window |
            Qt.WindowCloseButtonHint |
            Qt.WindowMinimizeButtonHint |
            Qt.WindowMaximizeButtonHint |
            Qt.FramelessWindowHint  # keep the pixel chrome across re-flagging
        )
        if on_top:
            flags |= Qt.WindowStaysOnTopHint
        self._detached_window.setWindowFlags(flags)
        if reshow:
            self._detached_window.show()

    def _toggle_detached_on_top(self):
        self._detached_on_top = not self._detached_window_on_top()
        self._apply_detached_on_top(self._detached_on_top)
        self._sync_detached_pin_button()
        self._update_preferences({'detached_window_on_top': self._detached_on_top})

    def _place_detached_compact(self):
        """Show the detached window at its narrowest, bottom-right (BU120).

        The width is the sum of what the two columns' layouts need to show
        every control whole, so it follows the real font metrics instead of a
        hard-coded size. The transcript column takes whatever the splitter
        does not give the answers column.
        """
        window = self._detached_window
        window.layout().activate()
        hint = window.minimumSizeHint()
        width = hint.width()
        height = max(hint.height(), self.DETACHED_MIN_HEIGHT)
        window.setMinimumWidth(width)
        window.resize(width, height)

        answers_width = self._detached_splitter.widget(0).minimumSizeHint().width()
        self._detached_splitter.setSizes([answers_width, max(width - answers_width, 1)])

        window.show()
        # The frame (title bar, borders) is only known once shown; place the
        # frame, not the client area, flush with the corner.
        screen = (self.screen() if self.isVisible() else None) or QApplication.primaryScreen()
        if screen is not None:
            area = screen.availableGeometry()
            frame = window.frameGeometry()
            # Clamped so a window wider or taller than the screen keeps its
            # title bar on screen, where it can still be dragged.
            left = max(area.left(), area.right() - frame.width() + 1)
            top = max(area.top(), area.bottom() - frame.height() + 1)
            window.move(window.pos() + QPoint(left, top) - frame.topLeft())

    # --- auto mode and detected questions (BU115) ----------------------

    def _set_live_qa_mode(self, mode: str):
        """Switch between Manual, Suggest and Auto. Manual spends nothing."""
        self._live_qa_mode = normalize_live_qa_mode(mode)
        self._sync_live_qa_mode_chips()

        if self._detection_enabled:
            self._ensure_question_detector()
        else:
            self._teardown_question_detector()
        self._save_live_qa_preferences()
        self._refresh_live_qa_spend_chip()

    def _sync_live_qa_mode_chips(self):
        for mode, chip in getattr(self, '_live_qa_mode_chips', {}).items():
            chip.setChecked(
                not self._displaying_inserted_transcript and mode == self._live_qa_mode)

    def _set_live_qa_answer_mode(self, mode: str):
        """Switch between Transcripts and General Knowledge (BU116).

        Takes effect on the next answer; answers already on screen keep the
        mode they were asked under.
        """
        self._live_qa_answer_mode = normalize_live_qa_answer_mode(mode)
        self._sync_live_qa_answer_mode_chips()
        self._save_live_qa_preferences()

    def _sync_live_qa_answer_mode_chips(self):
        for mode, chip in getattr(self, '_live_qa_answer_mode_chips', {}).items():
            chip.setChecked(
                not self._displaying_inserted_transcript and mode == self._live_qa_answer_mode)

    # --- reference document (BU117) --------------------------------------

    def _attach_reference_doc(self, path: str):
        """Attach a dropped or picked .txt as answer-side evidence.

        Exactly one document at a time: a second one replaces the first, and
        says so. Any failure leaves whatever was attached before untouched -
        a bad drop must not cost the user the document that was working.
        """
        previous = self._reference_doc
        try:
            doc = load_reference_text(path)
        except ReferenceDocError as e:
            self._on_status_update(str(e), is_error=True)
            return
        except Exception as e:  # noqa: BLE001 - an unreadable file, whatever the cause
            logger.error(f"Reference document could not be loaded: {e}")
            self._on_status_update(f"Could not read that file: {e}", is_error=True)
            return

        self._reference_doc = doc
        self._sync_reference_chip()
        if previous is not None and previous.path != doc.path:
            self._on_status_update(
                f"Replaced {previous.name} with {doc.name} ({doc.size_label})"
            )
        else:
            self._on_status_update(f"Attached {doc.name} ({doc.size_label})")

    def _clear_reference_doc(self):
        """Detach the document. Answers go back to transcript-only."""
        doc = self._reference_doc
        self._reference_doc = None
        self._sync_reference_chip()
        if doc is not None:
            self._on_status_update(f"Detached {doc.name}")

    def _pick_reference_doc(self):
        """Replace the attached document through a file picker."""
        patterns = ' '.join(
            f"*{ext}" for ext in REFERENCE_DOC.get('allowed_extensions', ['.txt'])
        )
        path, _ = QFileDialog.getOpenFileName(
            self._detached_window or self, "Attach a reference document", "",
            f"Text files ({patterns})"
        )
        if path:
            self._attach_reference_doc(path)

    def _sync_reference_chip(self):
        chip = getattr(self, '_reference_chip', None)
        if chip is None:
            return
        doc = self._reference_doc
        if doc is None:
            chip.clear_document()
        else:
            chip.set_document(doc.name, doc.size_label)

    def _reference_evidence_name(self) -> str:
        """The attached document's name, when this answer mode uses it.

        Empty in General Knowledge mode: that is the mode that does not check
        evidence, and an attached document is evidence.
        """
        doc = self._reference_doc
        if doc is None or self._live_qa_answer_mode == 'general':
            return ''
        return doc.name

    def _with_reference_evidence(self, question: str) -> str:
        """Append the document excerpt this question earns, if any.

        The excerpt is chosen per question rather than sent whole, so a long
        handout costs the same per answer as a short one.
        """
        if not self._reference_evidence_name():
            return question
        block = build_reference_block(select_relevant_excerpt(
            self._reference_doc, strip_transcript_evidence(question)))
        return f"{question}\n\n{block}" if block else question

    @property
    def _detection_enabled(self) -> bool:
        """Whether the detector should be running and spending."""
        return self._live_qa_mode in ('suggest', 'auto')

    @property
    def _live_qa_auto_answer(self) -> bool:
        """Whether a confident candidate answers itself. That is what Auto is."""
        return self._live_qa_mode == 'auto'

    def _show_live_qa_menu(self):
        """Detection settings: context window, detector model, auto-answer."""
        menu = QMenu(self._live_qa_settings_button)
        menu.setStyleSheet(DETACHED_MENU_QSS)

        low, high = LIVE_QA.get('window_chunks_range', (1, 5))
        chunk_seconds = ChunkedAudioRecorder.CHUNK_DURATION
        window_menu = menu.addMenu("Detection window")
        for size in range(low, high + 1):
            # Labelled in time as well as chunks: "3" means nothing, "~15 s of
            # context" is the thing the user is actually choosing.
            action = window_menu.addAction(
                f"{size} chunk{'s' if size > 1 else ''} (~{int(size * chunk_seconds)} s)"
            )
            action.setCheckable(True)
            action.setChecked(size == self._live_qa_window_chunks)
            action.triggered.connect(
                lambda _checked=False, v=size: self._set_live_qa_window_chunks(v)
            )

        model_menu = menu.addMenu("Detector model")
        for entry in LIVE_QA.get('detector_models', []):
            action = model_menu.addAction(f"{entry['label']} - {entry['note']}")
            action.setCheckable(True)
            action.setChecked(entry['id'] == self._live_qa_detector_model)
            action.triggered.connect(
                lambda _checked=False, v=entry['id']: self._set_live_qa_detector_model(v)
            )

        menu.addSeparator()

        # No "Answer automatically" here any more: that is the Auto chip in the
        # header, so the choice sits in one place instead of two.
        directed_action = menu.addAction("Only questions aimed at me")
        directed_action.setCheckable(True)
        directed_action.setChecked(self._live_qa_directed_only)
        directed_action.toggled.connect(self._set_live_qa_directed_only)

        menu.exec(self._live_qa_settings_button.mapToGlobal(
            QPoint(0, self._live_qa_settings_button.height())))

    def _set_live_qa_window_chunks(self, value: int):
        self._live_qa_window_chunks = value
        LIVE_QA['window_chunks'] = value
        if self._question_detector is not None:
            # Takes effect on the next detection; no session restart.
            self._question_detector.set_window_chunks(value)
        self._save_live_qa_preferences()

    def _set_live_qa_detector_model(self, model_id: str):
        self._live_qa_detector_model = model_id
        if self._question_detector is not None:
            self._question_detector.set_detector_model(model_id)
        self._save_live_qa_preferences()

    def _set_live_qa_directed_only(self, enabled: bool):
        self._live_qa_directed_only = bool(enabled)
        self._save_live_qa_preferences()

    # --- detector lifecycle ---------------------------------------------

    def _ensure_question_detector(self):
        """Stand the detector up, if Auto mode is on and it is not running."""
        if self._question_detector is not None:
            return self._question_detector
        try:
            from ..assistant.openrouter_client import OpenRouterClient
            client = OpenRouterClient(model=self._live_qa_detector_model, temperature=0)
        except Exception as e:  # noqa: BLE001 - missing key, bad config
            logger.error(f"Question detector unavailable: {e}")
            self._on_status_update(f"Detection unavailable: {e}", is_error=True)
            self._live_qa_mode = 'manual'
            self._sync_live_qa_mode_chips()
            return None

        detector = QuestionDetector(
            client,
            self._detected_ready.emit,  # hops to the UI thread, queued
            config={
                'detector_model': self._live_qa_detector_model,
                'window_chunks': self._live_qa_window_chunks,
            },
        )
        detector.start()
        self._question_detector = detector
        self._live_qa_hidden = []
        return detector

    def _teardown_question_detector(self):
        """Stop detecting and release the worker thread."""
        detector = self._question_detector
        self._question_detector = None
        if detector is not None:
            detector.stop()

    def _feed_question_detector(self, record, index: int):
        """Hand one live chunk to the detector, if Auto mode is on.

        Called from the transcription path, so it swallows everything: a broken
        detector must not be able to interrupt recording.
        """
        if (not self._detection_enabled or not self._detached_window
                or self._displaying_inserted_transcript):
            return
        try:
            detector = self._ensure_question_detector()
            if detector is not None:
                detector.feed(record, index=index)
        except Exception as e:  # noqa: BLE001
            logger.error(f"Feeding the question detector failed: {e}")

    # --- candidates -------------------------------------------------------

    @Slot(object)
    def _on_candidates_detected(self, candidates):
        """Surface detected questions in the answers rail (UI thread)."""
        if not getattr(self, '_detached_answers_layout', None):
            return
        for candidate in candidates or []:
            reason = mode_policy_reason(candidate, self._live_qa_directed_only)
            if reason is not None:
                logger.info(f"Detected question hidden ({reason}): {candidate.text!r}")
                self._live_qa_hidden.append((candidate.text, reason))
                continue
            self._post_candidate_card(candidate)

        self._refresh_live_qa_spend_chip()
        detector = self._question_detector
        if detector is not None and detector.cap_reached:
            self._show_detection_cap_notice()

    def _post_candidate_card(self, candidate):
        self._detached_answer_seq += 1
        card_id = self._detached_answer_seq

        time_text = (candidate.detected_at.strftime('%H:%M:%S')
                     if candidate.detected_at else '--:--:--')
        card = PixelCandidateCard(
            candidate.text, time_text,
            "Mic" if candidate.asker == 'mic' else "System",
            candidate.kind,
        )
        indices = tuple(candidate.record_indices or ())
        card.timestamp_clicked.connect(
            lambda idx=indices: self._scroll_detached_to_records(idx))
        card.dismiss_requested.connect(
            lambda cid=card_id: self._dismiss_candidate_card(cid))
        card.answer_requested.connect(
            lambda cid=card_id: self._answer_candidate(cid))

        self._detached_answers_layout.insertWidget(0, card)
        if getattr(self, '_detached_answers_empty', None):
            self._detached_answers_empty.hide()
        self._detached_candidate_cards[card_id] = (card, candidate)

        if self._live_qa_auto_answer and candidate.confidence >= LIVE_QA.get(
                'min_confidence', 0.8):
            self._answer_candidate(card_id)
        return card

    def _dismiss_candidate_card(self, card_id: int):
        entry = self._detached_candidate_cards.pop(card_id, None)
        if entry is not None:
            entry[0].setParent(None)
            entry[0].deleteLater()
        self._restore_answers_empty_state()

    def _answer_candidate(self, card_id: int):
        """Replace a candidate card with a real answer card, in place.

        Runs the *same* path a manual selection runs: the chat panel's agent
        and get_selected_model(), never the detector model.
        """
        entry = self._detached_candidate_cards.pop(card_id, None)
        if entry is None:
            return
        card, candidate = entry
        if self.assistant_service is None:
            self._on_status_update("Assistant is not available", is_error=True)
            self._detached_candidate_cards[card_id] = entry
            return

        indices = [i for i in (candidate.record_indices or [])
                   if 0 <= i < len(self._transcript_records)]
        records = [self._transcript_records[i] for i in indices]
        # BU116: the detected question is stated, and the transcript up to it
        # follows as evidence - the answer was usually said before the
        # question, not inside the detection window.
        history = ([] if self._live_qa_answer_mode == 'general'
                   else self._transcript_records[:max(indices) + 1] if indices else [])
        question = build_question_for_candidate(candidate, history)

        position = self._detached_answers_layout.indexOf(card)
        card.setParent(None)
        card.deleteLater()

        answer_card = PixelAnswerCard(candidate.text, timestamp_range(records)
                                      or card.time_text)
        answer_card.dismiss_requested.connect(
            lambda cid=card_id: self._dismiss_detached_card(cid))
        answer_card.timestamp_clicked.connect(
            lambda idx=tuple(indices): self._scroll_detached_to_records(idx))
        self._detached_answers_layout.insertWidget(max(position, 0), answer_card)
        self._detached_answer_cards[card_id] = answer_card

        self._start_detached_answer(card_id, question)

    def _restore_answers_empty_state(self):
        if (not self._detached_answer_cards
                and not self._detached_candidate_cards
                and getattr(self, '_detached_answers_empty', None)):
            self._detached_answers_empty.show()

    def _show_detection_cap_notice(self):
        """Say the cap was hit rather than going quiet (BU115)."""
        if self._detached_cap_notice is not None:
            return
        if not getattr(self, '_detached_answers_layout', None):
            return
        self._detached_cap_notice = pixel_rail_notice(
            f"Detection limit reached ({LIVE_QA.get('max_detections_per_session', 40)} "
            "this session). Select bubbles and right-click to keep asking."
        )
        self._detached_answers_layout.insertWidget(0, self._detached_cap_notice)

    def _refresh_live_qa_spend_chip(self):
        chip = getattr(self, '_live_qa_spend_chip', None)
        if chip is None:
            return
        if not self._detection_enabled:
            chip.setText("Manual - no detector calls")
            chip.setToolTip("Auto mode is off; nothing is being spent on detection")
            return
        detector = self._question_detector
        summary = detector.spend_summary() if detector else {
            'calls': 0, 'detections': 0, 'estimated_cost': 0.0}
        hidden = self._live_qa_hidden
        text = (f"{summary['calls']} calls  ~${summary['estimated_cost']:.3f}  "
                f"{summary['detections']} found")
        tip = "Detector calls this session; cost is an estimate"
        if hidden:
            text += f", {len(hidden)} hidden"
            tip += "\n\nHidden questions:\n" + "\n".join(
                f"- {question} ({reason})" for question, reason in hidden[-10:])
        chip.setText(text)
        chip.setToolTip(tip)

    # --- preferences ------------------------------------------------------

    def _save_live_qa_preferences(self):
        self._update_preferences({
            'live_qa_mode': self._live_qa_mode,
            'live_qa_answer_mode': self._live_qa_answer_mode,
            # Written for anything still reading the pre-split shape; the mode
            # is what this app loads.
            'live_qa_auto_answer': self._live_qa_auto_answer,
            'live_qa_window_chunks': self._live_qa_window_chunks,
            'live_qa_detector_model': self._live_qa_detector_model,
            'live_qa_directed_only': self._live_qa_directed_only,
        })

    def _load_live_qa_preferences(self):
        """Restore the stored live-Q&A settings, ignoring anything unrecognised."""
        prefs = self._read_preferences()

        self._live_qa_mode = normalize_live_qa_mode(
            prefs.get('live_qa_mode'), prefs.get('live_qa_auto_answer'))
        self._live_qa_answer_mode = normalize_live_qa_answer_mode(
            prefs.get('live_qa_answer_mode'))

        low, high = LIVE_QA.get('window_chunks_range', (1, 5))
        try:
            size = int(prefs.get('live_qa_window_chunks',
                                 LIVE_QA.get('window_chunks', 3)))
        except (TypeError, ValueError):
            size = LIVE_QA.get('window_chunks', 3)
        self._live_qa_window_chunks = max(low, min(high, size))
        LIVE_QA['window_chunks'] = self._live_qa_window_chunks

        offered = [m['id'] for m in LIVE_QA.get('detector_models', [])]
        model = prefs.get('live_qa_detector_model')
        self._live_qa_detector_model = (
            model if model in offered else LIVE_QA.get('detector_model'))

        self._live_qa_directed_only = bool(prefs.get('live_qa_directed_only', False))

    # --- bubble selection and answering (BU113) ------------------------

    def _install_bubble_selection(self, row: QWidget):
        """Make ``row`` respond to selection clicks, drags and right-clicks.

        Safe to call again as the bubble grows, and it has to be: a grouped
        bubble gains a label per chunk through ``append_text`` long after the
        row was built, and a label with no filter on it never reports the drag
        it is holding. (A press still reached the window by propagating up to
        the bubble, which is why a click appeared to work and a drag did not.)
        Re-installing on a widget that already has this filter just moves it to
        the front of that widget's list.
        """
        click_filter = row.property('_click_filter')
        if click_filter is None:
            click_filter = BubbleClickFilter(row, self._on_bubble_mouse_event, parent=row)
            # Held on the row so it lives exactly as long as what it filters.
            row.setProperty('_click_filter', click_filter)
            row.installEventFilter(click_filter)
        # The row itself plus everything inside it: an event lands on the label
        # holding the text, never on the row.
        for child in row.findChildren(QWidget):
            child.installEventFilter(click_filter)

    # --- mapping between chunks, rows and segments ----------------------

    def _visible_chunk_order(self) -> list:
        """Record indices of every visible chunk, in display order.

        This is what a selection ranges over. Gap separators and filter-hidden
        rows contribute nothing, so a drag never picks up something the user
        cannot see.
        """
        order = []
        for row in self._detached_find_rows():
            indices = row.property('record_indices')
            if indices and not row.isHidden():
                order.extend(indices)
        return order

    def _chunk_at_event(self, row, obj, event):
        """The record index the pointer is over, or None.

        ``obj`` is the widget the event landed on - usually one of the chunk
        labels inside a grouped bubble. Falls back to hit-testing the bubble by
        position, which is what makes a *drag* work: during a drag every move
        event is delivered to the widget the press started on, so the object is
        no longer a reliable indication of where the cursor actually is.
        """
        indices = row.property('record_indices') or []
        if not indices:
            return None
        bubble = self._safe_bubble(row)
        if bubble is None or not getattr(bubble, 'segment_count', 0):
            return indices[0]

        segment = bubble.segment_index_of(obj)
        if segment < 0:
            segment = bubble.segment_at(
                bubble.mapFromGlobal(event.globalPosition().toPoint())
            )
        if segment < 0:
            return indices[0]
        return indices[min(segment, len(indices) - 1)]

    def _chunk_under_cursor(self, global_pos):
        """The record index under a global point, across every visible row."""
        for row in self._detached_find_rows():
            indices = row.property('record_indices')
            if not indices or row.isHidden():
                continue
            local = row.mapFromGlobal(global_pos)
            if not row.rect().contains(local):
                continue
            bubble = self._safe_bubble(row)
            if bubble is None or not getattr(bubble, 'segment_count', 0):
                return indices[0]
            segment = bubble.segment_at(bubble.mapFromGlobal(global_pos))
            if segment < 0:
                return indices[0]
            return indices[min(segment, len(indices) - 1)]
        return None

    # --- the gesture ------------------------------------------------------

    def _on_bubble_mouse_event(self, row, obj, event) -> bool:
        """Press / move / release on a transcript bubble. True consumes it."""
        if event.type() == QEvent.MouseButtonPress:
            return self._on_bubble_press(row, obj, event)
        if event.type() == QEvent.MouseMove:
            return self._on_bubble_drag(event)
        if event.type() == QEvent.MouseButtonRelease:
            if self._detached_drag_active:
                self._detached_drag_active = False
                return True
        return False

    def _on_bubble_press(self, row, obj, event) -> bool:
        chunk = self._chunk_at_event(row, obj, event)

        if event.button() == Qt.RightButton:
            if chunk is not None and chunk not in self._detached_selected_chunks:
                # Matches normal list behaviour: right-clicking outside the
                # selection moves the selection to what was clicked.
                self._set_detached_selection([chunk], anchor=chunk)
            self._show_detached_transcript_menu(event.globalPosition().toPoint())
            return True

        if event.button() != Qt.LeftButton or chunk is None:
            return False

        selection, anchor = resolve_chunk_selection(
            self._visible_chunk_order(),
            self._detached_selected_chunks,
            self._detached_selection_anchor,
            chunk,
            event.modifiers(),
        )
        self._set_detached_selection(selection, anchor=anchor)

        # A sustained click extends from here; the modifiers in force at press
        # decide whether the run replaces the selection or joins it.
        self._detached_drag_active = True
        self._detached_drag_modifiers = event.modifiers()
        self._detached_drag_base = list(selection)

        # Consumed: dragging inside a bubble now selects chunks, so it must not
        # also start the label's own text selection. "Copy text" in the context
        # menu is what copies a selection.
        return True

    def _on_bubble_drag(self, event) -> bool:
        if not self._detached_drag_active:
            return False
        chunk = self._chunk_under_cursor(event.globalPosition().toPoint())
        if chunk is None:
            return True
        selection, anchor = resolve_chunk_selection(
            self._visible_chunk_order(),
            self._detached_drag_base,
            self._detached_selection_anchor,
            chunk,
            self._detached_drag_modifiers,
            extending=True,
        )
        self._set_detached_selection(selection, anchor=anchor)
        return True

    # --- applying a selection --------------------------------------------

    def _set_detached_selection(self, chunks, anchor=None):
        """Paint ``chunks`` as the selection and drop whatever was selected."""
        wanted = set(chunks)
        previous = set(self._detached_selected_chunks)
        for index in previous | wanted:
            self._paint_chunk_selected(index, index in wanted)
        self._detached_selected_chunks = list(chunks)
        self._detached_selection_anchor = anchor if chunks else None

    def _paint_chunk_selected(self, record_index: int, selected: bool):
        for row in self._detached_find_rows():
            indices = row.property('record_indices') or []
            if record_index not in indices:
                continue
            bubble = self._safe_bubble(row)
            if bubble is None:
                return
            if getattr(bubble, 'segment_count', 0):
                bubble.set_segment_selected(indices.index(record_index), selected)
            else:
                bubble.set_selected(selected)
            return

    def _safe_bubble(self, row):
        """``bubble_of_row`` that tolerates a row Qt has already deleted."""
        try:
            return bubble_of_row(row)
        except RuntimeError:
            return None

    def _prune_detached_selection(self):
        order = self._visible_chunk_order()
        kept = [i for i in self._detached_selected_chunks if i in order]
        if len(kept) != len(self._detached_selected_chunks):
            self._set_detached_selection(
                kept,
                anchor=self._detached_selection_anchor
                if self._detached_selection_anchor in kept else None,
            )

    def _clear_detached_selection(self):
        self._set_detached_selection([])

    def _selected_detached_records(self) -> list:
        """The TranscriptRecords behind the current selection, de-duplicated.

        Returned in record order (the order they were captured in), not sorted
        by timestamp - ``build_question_from_records`` owns the time ordering,
        so doing it twice would just be two places to get it wrong.
        """
        return [
            self._transcript_records[i]
            for i in sorted(set(self._detached_selected_chunks))
            if 0 <= i < len(self._transcript_records)
        ]

    def _show_detached_transcript_menu(self, global_pos: QPoint):
        """Right-click menu over the transcript column."""
        menu = QMenu(self._detached_window)
        menu.setStyleSheet(DETACHED_MENU_QSS)

        has_selection = bool(self._detached_selected_chunks)

        answer_action = menu.addAction("Answer question")
        answer_action.setEnabled(has_selection)
        copy_action = menu.addAction("Copy text")
        copy_action.setEnabled(has_selection)
        menu.addSeparator()
        clear_action = menu.addAction("Clear selection")
        clear_action.setEnabled(has_selection)

        answer_action.triggered.connect(self._on_answer_selected_chunks)
        copy_action.triggered.connect(self._on_copy_selected_chunks)
        clear_action.triggered.connect(self._clear_detached_selection)

        menu.exec(global_pos)

    def _on_copy_selected_chunks(self):
        records = self._selected_detached_records()
        if not records:
            return
        QApplication.clipboard().setText(
            "\n".join(r.display_text for r in records)
        )
        self._on_status_update(f"Copied {len(records)} transcript chunks")

    def _on_answer_selected_chunks(self):
        """Ask the chat panel's agent about the selected chunks (BU113)."""
        records = self._selected_detached_records()
        selected = sorted(set(self._detached_selected_chunks))
        earlier = ([] if self._live_qa_answer_mode == 'general'
                   else self._transcript_records[:selected[0]] if selected else [])
        question = build_question_from_records(records, earlier)
        if not question:
            self._on_status_update("Nothing selected to ask about")
            return
        if self.assistant_service is None:
            self._on_status_update("Assistant is not available", is_error=True)
            return

        self._detached_answer_seq += 1
        card_id = self._detached_answer_seq
        card = self._post_detached_answer_card(
            card_id,
            question_preview=records[0].text if len(records) == 1
            else f"{len(records)} chunks selected",
            time_range=timestamp_range(records),
            row_indices=sorted(set(self._detached_selected_chunks)),
        )
        if card is None:
            return
        self._start_detached_answer(card_id, question)

    def _post_detached_answer_card(self, card_id: int, question_preview: str,
                                   time_range: str, row_indices: list):
        """Insert a pending answer card at the top of the answers rail."""
        if not getattr(self, '_detached_answers_layout', None):
            return None

        card = PixelAnswerCard(question_preview, time_range)
        card.dismiss_requested.connect(lambda cid=card_id: self._dismiss_detached_card(cid))
        card.timestamp_clicked.connect(
            lambda idx=tuple(row_indices): self._scroll_detached_to_records(idx)
        )
        # Newest first, above the empty-state hint.
        self._detached_answers_layout.insertWidget(0, card)
        if getattr(self, '_detached_answers_empty', None):
            self._detached_answers_empty.hide()
        self._detached_answer_cards[card_id] = card
        return card

    def _start_detached_answer(self, card_id: int, question: str):
        """Run one answer on its own thread, keyed to its card.

        Deliberately not the chat panel's single ``_assistant_thread`` slot:
        several detached answers may be in flight at once, and none of them
        should disable the chat panel's Ask button.
        """
        thread = AssistantQueryThread(
            assistant_service=self.assistant_service,
            # BU117: the reference excerpt rides with the question, so both the
            # manual and the candidate path get it from this one place.
            question=self._with_reference_evidence(question),
            agent_id=self.agent_combo.currentData(),
            explicit_scope="current_session",
            active_session_id=self._current_display_session_id(),
            selected_session_id=self._current_display_session_id(),
            conversation_id=None,
            # BU116: the answer mode's instruction, not the chat agent's. The
            # agent and get_selected_model() still decide which model runs.
            # BU117: naming the document is what earns the middle tier.
            system_instruction=answer_instruction(
                self._live_qa_answer_mode, self._reference_evidence_name()),
            # Live answers live on their cards only; they are not chat
            # history and must not show up in the past-conversations list.
            persist=False,
            # General mode answers without the session: no transcript, summary
            # or screenshots are fetched or sent.
            use_context=self._live_qa_answer_mode != 'general',
            model=LIVE_QA.get('answer_model'),
            stream=True,
            reasoning_effort=LIVE_QA.get('answer_reasoning_effort'),
            max_tokens=LIVE_QA.get('answer_max_tokens'),
        )
        self._detached_answer_threads[card_id] = thread
        # card_id is bound into every handler, so a late response can only ever
        # reach the card it was asked for.
        thread.finished_signal.connect(
            lambda response, cid=card_id: self._on_detached_answer_finished(cid, response)
        )
        thread.error_signal.connect(
            lambda message, cid=card_id: self._on_detached_answer_error(cid, message)
        )
        thread.delta_signal.connect(
            lambda text, cid=card_id: self._on_detached_answer_delta(cid, text)
        )
        thread.finished.connect(
            lambda cid=card_id: self._detached_answer_threads.pop(cid, None)
        )
        # Retry re-asks the same question on a fresh thread for the same card.
        card = self._detached_answer_cards.get(card_id)
        if card is not None:
            try:
                card.retry_requested.disconnect()
            except RuntimeError:
                pass
            card.retry_requested.connect(
                lambda cid=card_id, q=question: self._retry_detached_answer(cid, q)
            )
        thread.start()

    def _retry_detached_answer(self, card_id: int, question: str):
        card = self._detached_answer_cards.get(card_id)
        if card is None or card_id in self._detached_answer_threads:
            return
        card.set_answer("Thinking...")
        self._start_detached_answer(card_id, question)

    def _on_detached_answer_delta(self, card_id: int, text: str):
        """Show the answer as it streams; the finished response replaces it."""
        card = self._detached_answer_cards.get(card_id)
        if card is not None:
            card.set_answer(format_answer_html(text))

    def _on_detached_answer_finished(self, card_id: int, response):
        card = self._detached_answer_cards.get(card_id)
        if card is None:
            return  # dismissed while in flight
        if getattr(response, 'success', False):
            card.set_answer(format_answer_html(response.answer or ""))
            self._on_status_update("Answer received")
        else:
            card.set_error(getattr(response, 'error', None) or "No answer returned")

    def _on_detached_answer_error(self, card_id: int, message: str):
        logger.error(f"Detached answer failed: {message}")
        card = self._detached_answer_cards.get(card_id)
        if card is not None:
            card.set_error(message)

    def _dismiss_detached_card(self, card_id: int):
        card = self._detached_answer_cards.pop(card_id, None)
        if card is not None:
            card.setParent(None)
            card.deleteLater()
        self._restore_answers_empty_state()

    def _scroll_detached_to_records(self, indices) -> bool:
        """Scroll the transcript column to the bubble holding ``indices``."""
        wanted = set(indices or ())
        if not wanted or not getattr(self, '_detached_scroll_area', None):
            return False
        for row in self._detached_find_rows():
            row_indices = row.property('record_indices')
            if row_indices and wanted & set(row_indices):
                self._detached_scroll_area.ensureWidgetVisible(row, 0, 40)
                self._flash_detached_row(row)
                return True
        return False

    def _flash_detached_row(self, row: QWidget):
        """Briefly highlight a bubble the user was sent to."""
        bubble = self._safe_bubble(row)
        if bubble is None:
            return
        bubble.set_highlighted(True)
        QTimer.singleShot(1200, lambda: self._unflash_detached_row(bubble))

    def _unflash_detached_row(self, bubble):
        try:
            bubble.set_highlighted(False)
        except RuntimeError:
            pass

    # --- find bar, detached window -----------------------------------

    def _wire_detached_find_bar(self, host: QWidget):
        """Give the detached window its own Ctrl+F over its transcript stream.

        A separate bar and controller from the main window's (BU099): the two
        can be open at once over different content, and the detached stream has
        its own row order once a filter is applied.
        """
        self._detached_find_bar = PixelFindBar(host)
        self._detached_find_bar.hide()
        self._detached_find_host = host
        self._detached_find_highlighted_row = None
        self._detached_find = FindController(
            lambda terms: [],
            lambda: [],
            self._detached_find_texts,
        )
        self._detached_find.set_mode("right")

        self._detached_find_bar.query_changed.connect(self._on_detached_find_query_changed)
        self._detached_find_bar.next_match.connect(lambda: self._detached_find_step("next"))
        self._detached_find_bar.prev_match.connect(lambda: self._detached_find_step("prev"))
        self._detached_find_bar.return_pressed.connect(lambda: self._detached_find_step("next"))
        self._detached_find_bar.closed.connect(self._close_detached_find_bar)

        self._detached_find_shortcut = QShortcut(
            QKeySequence.Find, self._detached_window
        )
        self._detached_find_shortcut.activated.connect(self._open_detached_find_bar)

        # Keep the bar pinned to the transcript column as the window and the
        # splitter are dragged around.
        host.installEventFilter(self)

    def _detached_find_rows(self) -> list:
        """Bubble rows of the detached stream, in display order."""
        if not getattr(self, '_detached_layout', None):
            return []
        return [
            self._detached_layout.itemAt(i).widget()
            for i in range(self._detached_layout.count())
            if self._detached_layout.itemAt(i).widget() is not None
        ]

    def _detached_find_texts(self) -> list:
        """Searchable text per row, aligned index-for-index with the rows.

        A hidden row (filtered out) and a gap separator both contribute an
        empty string rather than being dropped, so a match index always lands
        on the row it was computed from.
        """
        texts = []
        for row in self._detached_find_rows():
            if row.isHidden() or row.property('record_indices') is None:
                texts.append("")
                continue
            text = row.property('find_text')
            if not text:
                bubble = bubble_of_row(row)
                text = bubble.label.text() if bubble is not None else ""
            texts.append(text or "")
        return texts

    def _open_detached_find_bar(self):
        if not getattr(self, '_detached_find_bar', None):
            return
        if self._detached_find_bar.isVisible():
            self._detached_find_bar.focus_field()
            return
        self._clear_detached_find_highlight()
        self._detached_find_bar.show()
        self._detached_find_bar.raise_()
        self._position_detached_find_bar()
        self._detached_find_bar.focus_field()
        self._on_detached_find_query_changed(self._detached_find_bar.query_text())

    def _position_detached_find_bar(self):
        bar = getattr(self, '_detached_find_bar', None)
        if not bar or not bar.isVisible():
            return
        host = self._detached_find_host
        margin = 10  # clears the panel's pixel-cut border + corner
        avail = host.width() - 2 * margin
        width = max(150, min(bar.preferred_width, avail))
        bar.setFixedWidth(width)
        bar.move(max(margin, host.width() - width - margin), margin)

    def _close_detached_find_bar(self):
        self._clear_detached_find_highlight()
        if getattr(self, '_detached_find_bar', None):
            self._detached_find_bar.hide()

    def _clear_detached_find_highlight(self):
        row = getattr(self, '_detached_find_highlighted_row', None)
        if row is None:
            return
        try:
            bubble = bubble_of_row(row)
        except RuntimeError:
            # The row was deleted under us (session reload while the bar is open).
            self._detached_find_highlighted_row = None
            return
        if bubble is not None:
            bubble.set_highlighted(False)
            bubble.set_match_terms([])
        self._detached_find_highlighted_row = None

    def _on_detached_find_query_changed(self, query):
        self._clear_detached_find_highlight()
        self._detached_find.set_query(query)
        self._apply_detached_find_current()
        self._detached_find_bar.set_match_count(*self._detached_find.match_label())

    def _detached_find_step(self, direction):
        self._clear_detached_find_highlight()
        step = self._detached_find.next if direction == "next" else self._detached_find.prev
        step()
        self._apply_detached_find_current()
        self._detached_find_bar.set_match_count(*self._detached_find.match_label())

    def _apply_detached_find_current(self):
        match = self._detached_find.current()
        if match is None:
            return
        rows = self._detached_find_rows()
        if not (0 <= match < len(rows)):
            return
        row = rows[match]
        bubble = bubble_of_row(row)
        if bubble is not None:
            bubble.set_highlighted(True)
            bubble.set_match_terms(FindController.parse_terms(self._detached_find.query))
        self._detached_find_highlighted_row = row
        self._detached_scroll_area.ensureWidgetVisible(row, 0, 40)

    def _show_detached_filter_menu(self):
        """Show dropdown menu for filter options in detached window."""
        if not hasattr(self, '_detached_filter_combo'):
            return
        
        menu = QMenu(self._detached_filter_button)
        menu.setStyleSheet(DETACHED_MENU_QSS)
        
        current_filter = self._transcription_filter
        
        all_action = menu.addAction("All")
        all_action.setCheckable(True)
        all_action.setChecked(current_filter == 'all')
        
        mic_action = menu.addAction("Mic")
        mic_action.setCheckable(True)
        mic_action.setChecked(current_filter == 'mic')
        
        system_action = menu.addAction("System")
        system_action.setCheckable(True)
        system_action.setChecked(current_filter == 'system')
        
        def set_filter(filter_value):
            for i in range(self._detached_filter_combo.count()):
                if self._detached_filter_combo.itemData(i) == filter_value:
                    self._detached_filter_combo.setCurrentIndex(i)
                    break
        
        all_action.triggered.connect(lambda: set_filter('all'))
        mic_action.triggered.connect(lambda: set_filter('mic'))
        system_action.triggered.connect(lambda: set_filter('system'))
        
        menu.exec(self._detached_filter_button.mapToGlobal(
            QPoint(0, self._detached_filter_button.height())))
    
    def _on_detached_filter_changed(self, index: int):
        """Handle the filter selection change in the detached window.
        
        Args:
            index: The index of the selected filter option
        """
        self._update_detached_filter()
    
    def _update_detached_filter(self):
        """Update the visibility of transcriptions in the detached window based on current filter."""
        if not hasattr(self, '_detached_layout') or not self._detached_layout:
            return
        
        filter_value = self._detached_filter_combo.currentData()
        
        # Iterate through all transcription bubble widgets. Gap separators
        # (BU112) carry no source and so drop out under a source filter: the
        # break they mark is a break in the full stream, and showing it over a
        # filtered subset would claim a silence that wasn't there.
        for i in range(self._detached_layout.count() - 1):
            item = self._detached_layout.itemAt(i)
            if item and item.widget():
                bubble_frame = item.widget()
                # Get the source stored in the bubble
                source = bubble_frame.property('source')

                # Show or hide based on filter
                if filter_value == 'all' or source == filter_value:
                    bubble_frame.show()
                else:
                    bubble_frame.hide()

        # A hidden row cannot stay selected - the user can no longer see what
        # they would be asking about (BU113).
        self._prune_detached_selection()
    
    def _add_transcription_to_detached(self, record: "TranscriptRecord", index: int):
        """Add one transcript chunk to the detached window's bubble stream.

        Applies the same grouping rule as the main panel (BU111), so a
        continuous stretch of talk from one source reads as a single growing
        bubble in both places. The group state is this panel's own
        (``_detached_transcript_groups``): the two panels can be filtered
        differently, so they must not share an open bubble.

        Args:
            record: The chunk to render.
            index: Its position in ``_transcript_records``, stored on the row
                so a bubble can be mapped back to the chunks it contains.
        """
        if not getattr(self, '_detached_layout', None):
            return

        if record.source == 'inserted':
            # BU139: text only - a cream, timeless bubble per paragraph, no
            # grouping and no selection filter, so nothing can be asked.
            width = getattr(self, '_detached_bubble_width', DETACHED_BUBBLE_MAX_WIDTH)
            row = aligned_bubble_with_time(
                record.text, variant='cream', align='left', max_width=width, time_text='')
            row.setProperty('source', 'inserted')
            row.setProperty('find_text', record.text)
            row.setProperty('record_indices', [index])
            bubble_of_row(row).set_max_width(width)
            self._detached_layout.insertWidget(self._detached_layout.count() - 1, row)
            return

        source = record.source if record.source in ('mic', 'system') else 'mic'
        display_source = "Mic" if source == 'mic' else "System"
        group = self._detached_transcript_groups.get(source)

        # Sticking to the bottom (or not) is handled by _on_detached_range_changed
        # once the content has actually resized, as in the main panel.
        if should_extend_group(group, record.start_dt, record.end_dt):
            row = group['row']
            group['bubble'].append_text(record.text)
            # The append just created a label for this chunk; it needs the
            # row's filter or the chunk cannot be clicked or dragged over.
            self._install_bubble_selection(row)
            row.setProperty(
                'find_text', f"{row.property('find_text')} {record.text}"
            )
            row.setProperty('record_indices', list(row.property('record_indices') or []) + [index])
            group['last_end'] = record.end_dt
        else:
            # A long silence before this chunk gets its own row, so the jump in
            # timestamps reads as a break rather than as missing transcript
            # (BU112). Measured against the stream as a whole, not this
            # source's group, since the pause is a pause in the room.
            separator = gap_separator_label(self._detached_last_end, record.start_dt)
            if separator is not None:
                self._detached_layout.insertWidget(
                    self._detached_layout.count() - 1,
                    transcript_gap_separator(separator),
                )
                # A separator ends every open bubble: the next chunk from
                # either source starts below the break, not above it.
                self._detached_transcript_groups.clear()

            bubble_time = record.start_dt.strftime('%H:%M') if record.start_dt else ''
            align = "right" if source == 'mic' else "left"
            variant = "blue" if source == 'mic' else "cream"

            row = aligned_bubble_with_time(
                f"{display_source}: {record.text}",
                variant=variant,
                align=align,
                max_width=getattr(self, '_detached_bubble_width', DETACHED_BUBBLE_MAX_WIDTH),
                time_text=bubble_time,
                # Each chunk stays its own label inside the bubble, so a
                # minute-long group can still be selected one chunk at a time.
                segmented=True,
            )
            row.setProperty('source', source)
            row.setProperty('find_text', f"{display_source}: {record.text}")
            row.setProperty('record_indices', [index])

            # Check if this bubble should be visible based on current filter
            filter_value = (
                self._detached_filter_combo.currentData()
                if getattr(self, '_detached_filter_combo', None) else 'all'
            )
            if filter_value in ('mic', 'system') and source != filter_value:
                row.hide()

            bubble_of_row(row).set_max_width(
                getattr(self, '_detached_bubble_width', DETACHED_BUBBLE_MAX_WIDTH)
            )
            self._install_bubble_selection(row)
            self._detached_layout.insertWidget(self._detached_layout.count() - 1, row)

            self._detached_transcript_groups[source] = {
                'row': row,
                'bubble': bubble_of_row(row),
                'start_dt': record.start_dt or datetime.now(),
                'last_end': record.end_dt or datetime.now(),
            }

        if record.end_dt is not None:
            self._detached_last_end = record.end_dt

    def _on_detached_scroll_changed(self, value: int):
        """Track whether the detached transcript stream is at the bottom."""
        scroll_bar = self._detached_scroll_area.verticalScrollBar()
        self._detached_autoscroll = (scroll_bar.maximum() - value) <= 40

    def _on_detached_range_changed(self, minimum: int, maximum: int):
        """Keep the detached stream pinned to the bottom while auto-follow is on."""
        if getattr(self, '_detached_autoscroll', True) and self._detached_scroll_area:
            self._detached_scroll_area.verticalScrollBar().setValue(maximum)

    def _apply_detached_bubble_width(self):
        """Size detached transcript bubbles to the transcript column's width.

        Bubbles take up to ~70% of the viewport, so a maximised window gets
        wider bubbles. The floor stays small: long text now actually fills
        this width, so a large floor would overflow a narrow window.
        """
        scroll_area = getattr(self, '_detached_scroll_area', None)
        if scroll_area is None:
            return
        width = max(240, int(scroll_area.viewport().width() * 0.7))
        if width == getattr(self, '_detached_bubble_width', None):
            return
        self._detached_bubble_width = width
        layout = self._detached_layout
        for i in range(layout.count()):
            item = layout.itemAt(i)
            row = item.widget() if item else None
            bubble = bubble_of_row(row) if row is not None else None
            if bubble is not None:
                bubble.set_max_width(width)

    def _on_close_detached_window(self):
        """Close the detached transcription window."""
        # Restore the transcripts panel in the main UI - subject to the same
        # full-size rule as every other show of the wing.
        self._right_wing_user_hidden = False
        self._sync_right_wing()
        
        if self._detached_window:
            # Disconnect the signal to prevent re-entrancy
            try:
                self._detached_window.finished.disconnect(self._on_close_detached_window)
            except RuntimeError:
                pass  # Signal was already disconnected
            self._detached_window.close()
            self._detached_window = None
            self._detached_scroll_area = None
            self._detached_container = None
            self._detached_layout = None
            self._detached_filter_combo = None
            self._detached_splitter = None
            self._detached_answers_scroll_area = None
            self._detached_answers_layout = None
            self._detached_answers_empty = None
            self._detached_answers_panel = None
            self._detached_answers_collapsed = False
            self._detached_transcript_groups = {}
            self._detached_last_end = None
            self._detached_find_bar = None
            self._detached_find_host = None
            self._detached_find_highlighted_row = None
            self._detached_find_shortcut = None
            self._detached_transcript_column = None
            self._detached_clear_selection_shortcut = None
            self._detached_selected_chunks = []
            self._detached_selection_anchor = None
            self._detached_drag_active = False
            self._detached_answer_cards = {}
            # In-flight answers outlive the window: their threads are left to
            # finish and drop themselves, and their handlers find no card and
            # do nothing.
            self._detached_answer_threads = {}
            # Closing the window ends detection: nothing would be shown, so
            # nothing should be spent (BU115).
            self._teardown_question_detector()
            self._detached_candidate_cards = {}
            self._detached_cap_notice = None
            self._live_qa_spend_chip = None
            self._live_qa_mode_chips = {}
            self._live_qa_answer_mode_chips = {}
            # BU117: the reference document is held by the window, so it goes
            # with it. Nothing was persisted, so there is no stale path to
            # re-read on the next detach.
            self._reference_doc = None
            self._reference_chip = None

    def _update_ui_state(self):
        """Update UI based on current session state."""
        if self.session_manager is None:
            return
            
        active_session = self.session_manager.get_active_session()
        
        if active_session and active_session.status == Session.STATUS_ACTIVE:
            self._is_recording = True
            self.session_name_input.setText(active_session.name)
            
            # Update play button to pause icon (single button toggle behavior)
            self.play_stop_button.setIcon(self._make_icon("icon_pause.svg"))
            self.play_stop_button.setText("⏸")
            self.play_stop_button.setToolTip("Pause Session")
            self.screenshot_icon_button.setEnabled(True)
            
        elif active_session and active_session.status == Session.STATUS_PAUSED:
            self._is_recording = False
            self.session_name_input.setText(active_session.name)
            
            # Update pause button to play icon (single button toggle behavior)
            self.play_stop_button.setIcon(self._make_icon("icon_play.svg"))
            self.play_stop_button.setText("▶")
            self.play_stop_button.setToolTip("Resume Session")
            self.screenshot_icon_button.setEnabled(True)
            
        elif active_session and active_session.status == Session.STATUS_PROCESSING:
            # Update icon row
            self.play_stop_button.setIcon(self._make_icon("icon_play.svg"))
            self.play_stop_button.setText("▶")
            self.play_stop_button.setToolTip("Start Session")
            self.screenshot_icon_button.setEnabled(False)
            
        else:
            # Enable View Screenshots button if there are past sessions
            sessions = self.session_manager.db.list_sessions()
            self._is_recording = False
            
            # Update icon row
            self.play_stop_button.setIcon(self._make_icon("icon_play.svg"))
            self.play_stop_button.setText("▶")
            self.play_stop_button.setToolTip("Start Session")
            self.screenshot_icon_button.setEnabled(False)
        
        # Update summary icon button based on selected session
        self._update_summary_icon_state()

        # BU118: the mute toggles follow the recorder, so they enable with
        # capture and come back unmuted for a new session.
        self._update_mute_controls()

        # BU110: the capture hotkey is held only while a session is live.
        self._sync_capture_hotkey(bool(
            active_session and active_session.status in (
                Session.STATUS_ACTIVE, Session.STATUS_PAUSED)
        ))

    def _on_start_session(self):
        """Handle start session button click."""
        try:
            # Clear live transcription display for new session
            self._clear_transcription_view()
            
            # Generate session name with timestamp
            from datetime import datetime
            now = datetime.now()
            session_name = f"Session {now.strftime('%Y-%m-%d %H:%M')}"
            
            # Start session via manager (auto-starts recording)
            enable_live = self.live_transcription_action.isChecked()
            self.session_manager.start_session(session_name, auto_record=True, enable_live_transcription=enable_live)
            
            # Update UI
            self._update_ui_state()
            self._on_status_update('Session recording started')
            
            # Update scope to "Specific Session" with the newly started session
            active_session = self.session_manager.get_active_session()
            if active_session:
                self._selected_session_id = active_session.id
                self.scope_combo.setCurrentIndex(0)  # "Specific Session"
                self.session_search_input.setText(active_session.name)
                self._search_input_shows_session = True
                self._update_scope_label()
                self._load_session_transcripts_for_current_session(allow_live=True)
            
        except Exception as e:
            self._on_status_update(f'Failed to start session: {str(e)}', is_error=True)
            QMessageBox.critical(self, 'Error', f'Failed to start session: {str(e)}')
    
    def _on_stop_session(self):
        """Handle stop session button click.

        Capture stops immediately. Transcribing whatever live transcription
        hadn't gotten to yet, RAG indexing, and the optional auto-summary all
        run on SessionManager's background job thread (background=True) so
        this never blocks the UI; _apply_session_finalized reports when it's
        done. auto_transcribe=True is now cheap even though it used to mean
        "re-transcribe everything": TranscriptionProcessor skips any chunk
        that already has a transcript row.
        """
        try:
            # Nothing more will arrive to detect on, so release the thread and
            # stop spending (BU115).
            self._teardown_question_detector()

            session = self.session_manager.stop_session(auto_transcribe=True, background=True)

            # Update UI via icon buttons
            self._update_ui_state()

            if session:
                self._finalizing_session_ids.add(session.id)
                self._on_status_update(f"Session '{session.name}' stopped. Finalizing…")

            # Reload sessions to show the new session
            self._refresh_session_completer()

        except Exception as e:
            self._on_status_update(f'Failed to stop session: {str(e)}', is_error=True)
            QMessageBox.critical(self, 'Error', f'Failed to stop session: {str(e)}')
            self._update_ui_state()
    
    def _on_pause_resume_session(self):
        """Handle pause/resume button click."""
        try:
            active_session = self.session_manager.get_active_session()
            if not active_session:
                return
            
            if active_session.status == Session.STATUS_ACTIVE:
                self.session_manager.pause_session()
                # Detection resumes with the session; a paused session has no
                # chunks to detect on (BU115).
                self._teardown_question_detector()
                self._on_status_update('Session Paused')
            elif active_session.status == Session.STATUS_PAUSED:
                self.session_manager.resume_session()
                if self._detection_enabled and self._detached_window:
                    self._ensure_question_detector()
                self._on_status_update('Session Resumed')
            
            # Update UI state
            self._update_ui_state()
            
            self._update_ui_state()
        except Exception as e:
            self._on_status_update(f'Failed to pause/resume: {str(e)}', is_error=True)
    
    def _on_take_screenshot(self):
        """Handle take screenshot button click."""
        if self._capture_in_progress:
            return
        self._capture_in_progress = True
        try:
            # Open screenshot viewers would end up inside the capture (BU110).
            self._viewers_hidden_for_capture = [
                v for v in self._screenshot_viewers.values() if v.isVisible()
            ]
            for viewer in self._viewers_hidden_for_capture:
                viewer.hide()

            # Minimizar la ventana antes de tomar la captura
            self.showMinimized()

            # Esperar a que la ventana se minimice completamente antes de mostrar el dialog
            # Usar QTimer.singleShot para dar tiempo al sistema de minimizar
            QTimer.singleShot(300, self._execute_screenshot_capture)

        except Exception as e:
            # Asegurar que la ventana se restaure en caso de error
            self.showNormal()
            self.activateWindow()
            self.raise_()
            self._finish_capture()

            logger.error(f"Failed to take screenshot: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            self._on_status_update(f'Failed to take screenshot: {str(e)}', is_error=True)
            QMessageBox.critical(self, 'Error', f'Failed to take screenshot: {str(e)}')

    def _execute_screenshot_capture(self):
        """Execute the screenshot capture after window is minimized."""
        try:
            # Capture screenshot via session manager (interactive region selection)
            screenshot_path = self.session_manager.capture_interactive_region()

            # Restaurar la ventana
            self.showNormal()
            self.activateWindow()
            self.raise_()
            self._finish_capture()

            if screenshot_path:
                self._on_status_update('Screenshot Taken')
                self._notify_screenshot_added()
            else:
                self._on_status_update('Screenshot cancelled', is_error=False)

        except Exception as e:
            # Asegurar que la ventana se restaure en caso de error
            self.showNormal()
            self.activateWindow()
            self.raise_()
            self._finish_capture()

            logger.error(f"Failed to take screenshot: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            self._on_status_update(f'Failed to take screenshot: {str(e)}', is_error=True)
            QMessageBox.critical(self, 'Error', f'Failed to take screenshot: {str(e)}')

    # ---- BU110: live screenshot viewing, capture hotkey, clipboard snips ----

    def _finish_capture(self):
        """Bring back the viewers hidden for the snip and allow a new capture."""
        for viewer in self._viewers_hidden_for_capture:
            try:
                viewer.show()
            except RuntimeError:
                pass  # closed (and deleted) meanwhile
        self._viewers_hidden_for_capture = []
        self._capture_in_progress = False

    def _notify_screenshot_added(self):
        """Refresh the live session's open viewer after a new screenshot."""
        live = self._live_session_state()
        viewer = self._screenshot_viewers.get(live[0]) if live else None
        if viewer is not None:
            viewer.refresh_after_capture()

    def _on_capture_hotkey(self):
        """System-wide capture shortcut pressed (only held while live)."""
        if self._live_session_state() is None:
            return
        self._on_take_screenshot()

    def _sync_capture_hotkey(self, live: bool):
        """Hold the capture hotkey only while a session is recording or
        paused, so the key combination is free for other apps otherwise."""
        spec = SCREENSHOT.get("global_hotkey") or ""
        if not live or not spec:
            self._capture_hotkey.unregister()
            if self._capture_hotkey_fallback is not None:
                self._capture_hotkey_fallback.setEnabled(False)
            return
        if self._capture_hotkey.register(spec):
            return
        # Could not grab it system-wide: keep it working inside Chronicle.
        if self._capture_hotkey_failed_spec != spec:
            self._capture_hotkey_failed_spec = spec
            self._on_status_update(
                f"Screenshot shortcut {spec} is not available system-wide; "
                "it only works while Chronicle is focused.", is_error=True
            )
        if self._capture_hotkey_fallback is None:
            self._capture_hotkey_fallback = QShortcut(QKeySequence(spec), self)
            self._capture_hotkey_fallback.setContext(Qt.ApplicationShortcut)
            self._capture_hotkey_fallback.activated.connect(self._on_capture_hotkey)
        self._capture_hotkey_fallback.setEnabled(True)

    def _on_clipboard_changed(self):
        """Opt-in (SCREENSHOT["import_clipboard_snips"]): while a session is
        live, save an image put on the clipboard - e.g. by Win+Shift+S - as a
        session screenshot. One snip fires several notifications; repeats of
        the same image within a few seconds are ignored."""
        if self._live_session_state() is None or self._capture_in_progress:
            return
        clipboard = QApplication.clipboard()
        mime = clipboard.mimeData()
        if mime is None or not mime.hasImage():
            return
        image = clipboard.image()
        if image.isNull():
            return
        import hashlib
        thumb = image.scaled(64, 36).convertToFormat(image.Format.Format_RGB32)
        key = hashlib.md5(bytes(thumb.constBits())).hexdigest() + f"{image.width()}x{image.height()}"
        if self._clipboard_deduper.is_duplicate(key):
            return
        try:
            self.session_manager.import_clipboard_screenshot(image)
        except Exception as e:
            logger.error(f"Failed to import clipboard screenshot: {e}")
            self._on_status_update(f"Failed to import clipboard screenshot: {e}", is_error=True)
            return
        self._on_status_update("Screenshot imported from clipboard")
        self._notify_screenshot_added()

    def _on_play_stop_clicked(self):
        """Handle play/stop icon button click - toggles between start/pause/resume."""
        active_session = self.session_manager.get_active_session() if self.session_manager else None
        
        if active_session and active_session.status == Session.STATUS_ACTIVE:
            # Session is recording - pause it (toggle button to play)
            self.session_manager.pause_session()
            self._on_status_update('Session Paused')
            self._update_ui_state()
        elif active_session and active_session.status == Session.STATUS_PAUSED:
            # Session is paused - resume it (toggle button to pause)
            self.session_manager.resume_session()
            self._on_status_update('Session Resumed')
            self._update_ui_state()
        else:
            # No session running - start one
            self._on_start_session()
    
    def _on_view_screenshots_icon_clicked(self):
        """Handle view screenshots icon button click - uses selected session."""
        # Delegate to the complete handler
        self._on_view_screenshots()
    
    def _on_view_summary_icon_clicked(self):
        """Handle view summary icon button click - uses selected session."""
        try:
            session_id = None
            session_name = None
            
            # First check for selected session
            if self._selected_session_id:
                session_id = self._selected_session_id
                # Get session name from database
                sessions = self.session_manager.db.list_sessions()
                for s in sessions:
                    if s['id'] == session_id:
                        session_name = s['name']
                        break
                
                # Check summary status
                if session_id:
                    summaries = self.session_manager.db.get_summaries(session_id)
                    if not summaries:
                        QMessageBox.information(
                            self,
                            'No Summary',
                            f"Session '{session_name}' does not have a summary yet.\n\n"
                            "Please transcribe and summarize the session first."
                        )
                        return
            else:
                # Fall back to active session or most recent
                active_session = self.session_manager.get_active_session()
                if active_session:
                    session_id = active_session.id
                    session_name = active_session.name
                else:
                    sessions = self.session_manager.db.list_sessions()
                    if sessions:
                        session_id = sessions[0]['id']
                        session_name = sessions[0]['name']
            
            if not session_id:
                QMessageBox.information(
                    self,
                    'No Sessions',
                    'There are no sessions with summaries.'
                )
                return

            self._open_summary_window(session_id, session_name)

        except Exception as e:
            logger.error(f"Failed to view summary: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            QMessageBox.critical(self, 'Error', f'Failed to view summary: {str(e)}')
    
    def _on_view_screenshots(self):
        """Handle view screenshots button click."""
        try:
            session_id = None
            session_name = None

            # First check for selected session (scope)
            if self._selected_session_id:
                session_id = self._selected_session_id
                # Get session name from database
                sessions = self.session_manager.db.list_sessions()
                for s in sessions:
                    if s['id'] == session_id:
                        session_name = s['name']
                        break
            # Then check if there's an active session
            elif not session_id:
                active_session = self.session_manager.get_active_session()
                if active_session:
                    session_id = active_session.id
                    session_name = active_session.name
            
            # Fall back to most recent session
            if not session_id:
                sessions = self.session_manager.db.list_sessions()
                if sessions:
                    session_id = sessions[0]['id']
                    session_name = sessions[0]['name']

            if not session_id:
                QMessageBox.information(
                    self,
                    'No Sessions',
                    'There are no sessions to view screenshots for.'
                )
                return

            # Use the complete screenshots window with context generator
            self._show_screenshots_by_session_id(session_id, session_name)
            
        except Exception as e:
            logger.error(f"Failed to view screenshots: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            QMessageBox.critical(
                self,
                'Error',
                f'Failed to view screenshots: {str(e)}'
            )
    
    def _process_transcription(self, session):
        """Process transcriptions after session stops."""
        print(f"[DEBUG] _process_transcription called with session: {session}")
        try:
            # Show status before processing
            self._on_status_update('Processing transcriptions...')
            QApplication.processEvents()
            print("[DEBUG] Set status to Processing transcriptions...")
            
            results = self.session_manager.process_transcriptions(session)
            
            # Update UI
            self._update_ui_state()
            
            # Generate summary
            self._on_status_update('Generating summary...')
            QApplication.processEvents()
            
            try:
                # Get transcripts from database
                # Ensure db is connected
                transcripts = []
                if session.db is None:
                    logger.error("session.db is None")
                elif session.db.connection is None:
                    logger.info("Database not connected, connecting...")
                    session.db.connect()
                    transcripts = session.db.get_transcripts(session.id)
                else:
                    transcripts = session.db.get_transcripts(session.id)
                
                if transcripts:
                    # Combine all transcript text
                    full_transcript = ' '.join(
                        t.get('text', '') for t in transcripts if t.get('text')
                    )
                    
                    if full_transcript.strip():
                        # Create summary generator (reads API key from .env)
                        summary_gen = SummaryGenerator(db=session.db)
                        
                        # Generate and store summary
                        summary_result = summary_gen.generate_and_store(
                            transcript=full_transcript,
                            session_id=session.id,
                            summary_type='full'
                        )
                        
                        summary_content = summary_result.get('content', '')
                        logger.info(f"Summary generated for session {session.id}")
                        
                        # Update summary status in database
                        self.session_manager.db.update_session(session.id, summary_status='summarized')
                    else:
                        summary_content = None
                        logger.warning(f"No transcript text found for session {session.id}")
                else:
                    summary_content = None
                    logger.warning(f"No transcripts found for session {session.id}")
                    
            except Exception as e:
                logger.error(f"Summary generation failed: {str(e)}")
                import traceback
                logger.error(traceback.format_exc())
                summary_content = None
            
            # Update status to show session finished
            self._on_status_update('Session complete')
            
            # Reload sessions to show updated status
            self._refresh_session_completer()
            
            # Show completion message
            mic_count = len(results.get('microphone', []))
            sys_count = len(results.get('system', []))
            
            msg = f'Session saved successfully.\n\n'
            msg += f'Microphone transcriptions: {mic_count}\n'
            msg += f'System audio transcriptions: {sys_count}\n'
            
            if summary_content:
                msg += f'\nSummary: Generated'
            else:
                msg += f'\nSummary: Not available'
            
            QMessageBox.information(
                self, 
                'Session Complete',
                msg
            )
            
        except Exception as e:
            QMessageBox.warning(self, 'Warning', f'Transcription failed: {str(e)}')
            self._update_ui_state()
    
    def _open_settings_dialog(self):
        """Open the Settings pop-up (BU128); focus it if it is already open."""
        self.open_settings()

    def open_settings(self, page: str = None):
        """Open (or focus) Settings, optionally on ``page`` - e.g. "Calendar" (BU131)."""
        if self._settings_dialog is not None:
            if page:
                self._settings_dialog.select_page(page)
            self._settings_dialog.raise_()
            self._settings_dialog.activateWindow()
            return
        dialog = SettingsDialog(self)
        if page:
            dialog.select_page(page)
        dialog.setAttribute(Qt.WA_DeleteOnClose, True)
        dialog.finished.connect(self._on_settings_dialog_closed)
        self._settings_dialog = dialog
        dialog.open()

    def _on_settings_dialog_closed(self, _result=None):
        self._settings_dialog = None

    def saved_theme(self) -> str:
        """The UI theme stored in preferences - what the next start will use."""
        return theme.read_preference(self._read_preferences())

    def _save_theme(self, name: str):
        """Store the UI theme for the next start (BU129)."""
        name = theme.normalize(name)
        self._update_preferences({theme.PREF_KEY: name})
        label = theme.THEMES[name][0]
        if name == theme.active():
            self._on_status_update(f"Theme: {label}")
        else:
            self._on_status_update(f"Theme: {label} (applies after a restart)")

    def _restart_app(self):
        """Close Chronicle and start it again (BU129).

        Goes through the normal close, so an active session still asks before
        stopping; if the user keeps it, nothing restarts.
        """
        self.restart_requested = True
        if not self.close():
            self.restart_requested = False
            return
        QApplication.quit()

    def _apply_vad_settings(self, threshold: int, aggressiveness: int):
        """Use new VAD settings from the next recorded chunk on."""
        self._vad_threshold = threshold
        self._vad_aggressiveness = aggressiveness
        if self.session_manager:
            self.session_manager.vad_threshold = threshold / 100.0
            self.session_manager.vad_aggressiveness = aggressiveness
        self._on_status_update(f'VAD settings updated: {threshold}% threshold, Mode {aggressiveness}')

    def _apply_selected_model(self, model_id: str):
        """Switch the summary/assistant model and remember it (BU119)."""
        if set_selected_model(model_id):
            self._update_preferences({'selected_model': model_id})
            self._on_status_update(f'Model changed to: {model_id}')

    def _on_new_chat_clicked(self):
        """Handle the New Chat button click - resets conversation context."""
        self._current_conversation_id = None
        self._screenshot_refs_shown = False
        self.question_input.clear()
        self._clear_conversation_view()
        self._clear_candidates()
        
        # Also clear the detached window conversation if it exists
        if hasattr(self, '_detached_question_input') and self._detached_question_input:
            self._detached_question_input.clear()
        
        if hasattr(self, '_detached_answer_layout') and self._detached_answer_layout:
            # Remove all widgets except the stretch (last item)
            while self._detached_answer_layout.count() > 1:
                item = self._detached_answer_layout.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()

        self._pending_detached_scope_prompt = None

        # Refresh the conversations list
        self._load_past_conversations()
        
        self._on_status_update("New conversation started")
    
    def _on_ask_clicked(self):
        """Handle the Ask button click - wires to AssistantAnswerService."""
        # Get the question from the input
        question = self.question_input.toPlainText().strip()
        
        if not question:
            self._add_message_to_conversation('assistant', "Please enter a question.")
            return
        
        # Clear the search bar after getting the question
        self.question_input.clear()
        
        # Add user's question to conversation view
        self._add_message_to_conversation('user', question)

        # Clear previous candidates when asking a new question
        self._clear_candidates()
        # Retire any live BU093 scope offer - at most one may be open (BU093)
        self._retire_pending_scope_prompt()

        # Store the original question for potential retry
        self._current_question = question
        
        # Get selected agent
        agent_id = self.agent_combo.currentData()
        
        # Get scope from combo
        scope_value = self.scope_combo.currentData()  # "current" or "any"
        
        # Get active session ID if there's an active session
        active_session = self.session_manager.get_active_session()
        active_session_id = active_session.id if active_session else None
        
        # Get selected session ID from the sessions table (if any session is selected)
        # Falls back to getting session from conversation if conversation is selected
        selected_session_id = self._get_selected_session_id()
        
        # If no session selected but we have a conversation, get session from conversation
        if selected_session_id is None and self._current_conversation_id is not None:
            conv = self.session_manager.db.get_conversation(self._current_conversation_id)
            if conv:
                selected_session_id = conv.get('session_id')
        
        # Determine explicit_scope based on scope combo
        # When "current", use selected session if available, otherwise active session
        # When "any", use any_session to search all sessions
        if scope_value == "current":
            # Use selected session (from UI) if available, otherwise active session
            if selected_session_id is not None:
                explicit_scope = "current_session"
                active_session_id = selected_session_id  # Override: use selected session
            elif active_session_id is not None:
                explicit_scope = "current_session"
                # active_session_id already has the active session
            else:
                explicit_scope = "current_session"  # Will cause clarification
        else:
            explicit_scope = "any_session"
        
        # Record which scope produced the upcoming answer (for the bubble marker)
        self._last_scope_marker = self._scope_marker_text(
            explicit_scope, selected_session_id or active_session_id
        )

        # Disable the Ask button while processing
        self.ask_button.setEnabled(False)
        self._add_message_to_conversation('assistant', "Thinking...")
        
        # Run the assistant service call in the background using QTimer to keep UI responsive
        QTimer.singleShot(50, lambda: self._run_assistant_query(
            question=question,
            agent_id=agent_id,
            explicit_scope=explicit_scope,
            active_session_id=active_session_id,
            selected_session_id=selected_session_id
        ))
    
    def _run_assistant_query(
        self,
        question: str,
        agent_id: str,
        explicit_scope: str,
        active_session_id: Optional[int],
        selected_session_id: Optional[int]
    ):
        """Execute the assistant query in a background thread."""
        # Wait for any previous thread to finish before starting a new one
        if self._assistant_thread is not None and self._assistant_thread.isRunning():
            self._assistant_thread.wait()
        
        # Create and configure the thread
        self._assistant_thread = AssistantQueryThread(
            assistant_service=self.assistant_service,
            question=question,
            agent_id=agent_id,
            explicit_scope=explicit_scope,
            active_session_id=active_session_id,
            selected_session_id=selected_session_id,
            conversation_id=self._current_conversation_id
        )
        
        # Connect signals to handlers
        self._assistant_thread.finished_signal.connect(self._on_assistant_query_finished)
        self._assistant_thread.error_signal.connect(self._on_assistant_query_error)
        # Drop the reference only once run() has fully returned: finished_signal
        # fires before run()'s finally block, and a QThread garbage-collected
        # while still running aborts the process.
        thread = self._assistant_thread
        thread.finished.connect(lambda t=thread: self._on_assistant_thread_done(t))

        # Start the thread
        self._assistant_thread.start()
    
    def _on_assistant_thread_done(self, thread):
        if self._assistant_thread is thread:
            self._assistant_thread = None

    def _on_assistant_query_finished(self, response):
        """Handle the assistant query response."""
        # Re-enable the Ask button
        self.ask_button.setEnabled(True)
        
        # Handle the response
        if response.success:
            # Clear candidates on successful answer (preserve the question so a
            # BU090 handoff pick can re-ask it).
            pending_question = self._current_question
            self._clear_candidates()
            self._current_question = pending_question
            answer_text = self._drop_repeat_screenshot_pointer(
                response.answer or "", response.screenshot_refs
            )
            # Replace "Thinking..." with the actual response
            self._replace_thinking_message(answer_text + self._scope_suffix())
            # BU108/BU109: the answer points at screenshots -> "View" buttons.
            if response.screenshot_refs:
                self._add_screenshot_refs_to_conversation(response.screenshot_refs)
            # BU092/BU093: the answer points at a concrete meeting -> in-chat
            # prompt offering a scope switch (with a "choose another" path to
            # the full candidate list). No standalone auto-picker any more.
            if response.scope_offer is not None:
                self._add_scope_offer_to_conversation(
                    response.scope_offer, response.candidate_sessions
                )
            # Save conversation_id for follow-up questions
            if response.conversation_id:
                self._current_conversation_id = response.conversation_id
                logger.info(f"New conversation created with ID: {response.conversation_id}")
                # Refresh the conversations list to show the new conversation (use QTimer to ensure it runs in main thread)
                QTimer.singleShot(0, self._load_past_conversations)
            self._on_status_update("Answer received")
        elif response.needs_clarification:
            # Show clarification question and candidates
            clarification_text = response.clarification_question or ""
            # Replace "Thinking..." with clarification
            self._replace_thinking_message(clarification_text)
            
            # Display candidates if available
            if response.candidates:
                self._display_candidates(response.candidates)
            else:
                # Hide candidate UI if no candidates
                self._clear_candidates()
                
            self._on_status_update("Clarification needed")
        else:
            # Show error
            error_text = response.error or "Unknown error occurred"
            self._add_message_to_conversation('assistant', f"Error: {error_text}")
            self._on_status_update(f"Assistant error: {error_text}", is_error=True)
            # Clear candidates on error
            self._clear_candidates()
    
    def _on_assistant_query_error(self, error_message: str):
        """Handle assistant query errors."""
        # Re-enable the Ask button
        self.ask_button.setEnabled(True)
        
        logger.error(f"Assistant query failed: {error_message}")
        import traceback
        logger.error(traceback.format_exc())
        self._add_message_to_conversation('assistant', f"Error: {error_message}")
        self._on_status_update(f"Assistant error: {error_message}", is_error=True)
        # Clear candidates on exception
        self._clear_candidates()

    # ---- BU109: screenshot citations ----------------------------------------

    def _drop_repeat_screenshot_pointer(self, answer_text, screenshot_ids):
        """Once a chat has pointed at a screenshot, later answers lose their
        "might be contained in the screenshot" line (and get no button)."""
        if not screenshot_ids or not self._screenshot_refs_shown:
            return answer_text
        stripped, _ = parse_screenshot_refs(answer_text, ())
        return stripped

    def _add_screenshot_refs_to_conversation(self, screenshot_ids):
        """Under the answer bubble, one button per screenshot the assistant
        pointed to; each opens the viewer on it. Transient UI, like the scope
        prompt - not rebuilt when a conversation is reloaded. Only the first
        answer in a chat that cites screenshots gets the buttons."""
        if self._screenshot_refs_shown:
            return
        self._screenshot_refs_shown = True
        ids = list(screenshot_ids)
        if hasattr(self, '_answer_layout') and self._answer_layout:
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(6)
            for screenshot_id in ids:
                button = pixel_mini_button(
                    f"View screenshot #{screenshot_id}",
                    "Open this screenshot in the viewer",
                    width=190, height=32,
                )
                button.clicked.connect(
                    lambda _=False, sid=screenshot_id: self._open_screenshot_reference(sid)
                )
                row_layout.addWidget(button, 0, Qt.AlignLeft)
            row_layout.addStretch(1)
            self._answer_layout.insertWidget(self._answer_layout.count() - 1, row)
            self._scroll_answer_to_bottom()

        if hasattr(self, '_detached_answer_layout') and self._detached_answer_layout:
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            for screenshot_id in ids:
                button = QPushButton(f"View screenshot #{screenshot_id}")
                button.clicked.connect(
                    lambda _=False, sid=screenshot_id: self._open_screenshot_reference(sid)
                )
                row_layout.addWidget(button, 0, Qt.AlignLeft)
            row_layout.addStretch(1)
            self._detached_answer_layout.insertWidget(
                self._detached_answer_layout.count() - 1, row
            )

    # ---- BU093: in-chat scope switch prompt ---------------------------------

    def _add_scope_offer_to_conversation(self, offer, candidates=None):
        """Insert the pixel-themed scope switch prompt right after the answer
        bubble, then mirror it into the detached window. Transient UI only -
        never persisted, never rebuilt on conversation reload.

        `candidates` are the routed sessions; the prompt's "Choose another
        session" link opens the picker seeded with them.
        """
        self._retire_pending_scope_prompt()
        candidates = list(candidates or [])

        if hasattr(self, '_answer_layout') and self._answer_layout:
            prompt = PixelScopePrompt(
                format_scope_offer_prompt(offer.session_name, offer.start_time),
                max_width=400,
                allow_choose_another=bool(candidates),
            )
            prompt.accepted.connect(lambda o=offer: self._on_scope_offer_accepted(o))
            prompt.declined.connect(lambda o=offer: self._on_scope_offer_declined(o))
            prompt.choose_another.connect(
                lambda c=candidates: self._display_candidates(c, handoff=True)
                if c else None
            )

            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(0)
            row_layout.addWidget(prompt, 0, Qt.AlignLeft)
            row_layout.addStretch(1)

            self._answer_layout.insertWidget(self._answer_layout.count() - 1, row)
            self._pending_scope_prompt = prompt
            self._scroll_answer_to_bottom()

        if hasattr(self, '_detached_answer_layout') and self._detached_answer_layout:
            self._add_scope_offer_to_detached_conversation(offer, candidates)

    def _add_scope_offer_to_detached_conversation(self, offer, candidates=None):
        """Mirror the scope switch prompt in the detached window using its own
        plainer QFrame bubble style and plain QPushButtons (BU093).
        """
        prompt_text = format_scope_offer_prompt(offer.session_name, offer.start_time)
        candidates = list(candidates or [])

        frame = QFrame()
        frame.setFrameShape(QFrame.StyledPanel)
        frame.setStyleSheet(
            "QFrame { background-color: #E3F2FD; border-radius: 10px; border: 1px solid #90CAF9; }"
        )
        v = QVBoxLayout(frame)
        v.setContentsMargins(10, 8, 10, 8)
        v.setSpacing(6)

        label = QLabel(prompt_text)
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        v.addWidget(label)

        yes_btn = QPushButton("YES")
        no_btn = QPushButton("NO")
        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(0, 0, 0, 0)
        btn_row.addWidget(yes_btn)
        btn_row.addWidget(no_btn)
        btn_row.addStretch(1)
        v.addLayout(btn_row)

        other_btn = QPushButton("Choose another session")
        other_btn.setFlat(True)
        other_btn.setStyleSheet(
            "QPushButton { color: #1565C0; background: transparent; border: none;"
            " text-align: left; padding: 0; text-decoration: underline; }"
        )
        other_btn.setVisible(bool(candidates))
        v.addWidget(other_btn)

        def settle(record: str):
            for b in (yes_btn, no_btn, other_btn):
                b.setEnabled(False)
                b.hide()
            label.setText(f"{prompt_text}\n\n{record}")

        def on_yes(_=False):
            if not yes_btn.isEnabled():
                return
            settle("> Scope switched to Specific Session.")
            self._on_scope_offer_accepted(offer)

        def on_no(_=False):
            if not no_btn.isEnabled():
                return
            settle("> Kept Any Session.")
            self._on_scope_offer_declined(offer)

        def on_other(_=False):
            if other_btn.isEnabled() and candidates:
                self._display_detached_candidates(candidates, handoff=True)

        yes_btn.clicked.connect(on_yes)
        no_btn.clicked.connect(on_no)
        other_btn.clicked.connect(on_other)

        self._detached_answer_layout.insertWidget(
            self._detached_answer_layout.count() - 1, frame
        )
        self._pending_detached_scope_prompt = (frame, yes_btn, no_btn, settle)
        self._detached_answer_scroll.verticalScrollBar().setValue(
            self._detached_answer_scroll.verticalScrollBar().maximum()
        )

    def _retire_pending_scope_prompt(self, record: str = "> No longer offered."):
        """Collapse any live, unanswered scope prompt (both views) so at most
        one offer is ever live. `record` is the static line it collapses to.
        """
        prompt = self._pending_scope_prompt
        if prompt is not None:
            try:
                prompt.retire(record)
            except RuntimeError:
                pass  # widget already deleted with the conversation view
            self._pending_scope_prompt = None

        detached = self._pending_detached_scope_prompt
        if detached is not None:
            _frame, yes_btn, _no_btn, settle = detached
            try:
                if yes_btn.isEnabled():
                    settle(record)
            except RuntimeError:
                pass
            self._pending_detached_scope_prompt = None

    def _switch_scope_to_specific(self, session_id: int, display_name: Optional[str] = None):
        """Select `session_id` and move the scope combo to Specific Session
        through the normal signal path, so the detached combo, the transcript
        panel and the scope label all follow (`_on_scope_changed` guards the
        recursion via `_scope_sync_guard`). Also pins the session's name in
        the search bar for as long as it stays selected.

        Args:
            display_name: Text to show in the search bar. Defaults to the
                bare session name; pass the caller's own (e.g. completer)
                display text to preserve richer formatting.
        """
        self._selected_session_id = session_id
        name = display_name or self._session_name_for_id(session_id)
        if name and hasattr(self, 'session_search_input'):
            self.session_search_input.setText(name)
            self._search_input_shows_session = True
        index = self.scope_combo.findData("current")
        if index < 0:
            self._update_scope_label()
            return
        if self.scope_combo.currentIndex() == index:
            self._on_scope_changed(index)
        else:
            self.scope_combo.setCurrentIndex(index)

    def _on_scope_offer_accepted(self, offer):
        """YES: open a blank new chat scoped to the offered session.

        Nothing is re-asked - the Any Session question stays in its own
        conversation. The new chat starts empty, with the scope combo moved
        to Specific Session for `offer.session_id` (transcript panel and scope
        label follow), so the user types their next question there already
        scoped to that meeting.
        """
        self._retire_pending_scope_prompt("> Switched to that session.")
        self._on_new_chat_clicked()
        self._switch_scope_to_specific(offer.session_id)

    def _on_scope_offer_declined(self, offer):
        """NO: record the decline so this session is not offered again in the
        conversation.
        """
        self._retire_pending_scope_prompt("> Kept Any Session.")
        apply_scope_offer_decline(
            offer, self._current_conversation_id, self.assistant_service.decline_scope_offer
        )
        self._on_status_update("Kept Any Session scope")

    def _on_reindex_all_clicked(self):
        """Run the BU091 corpus backfill on a background thread (force rebuild)."""
        if getattr(self, "_rag_backfill_thread", None) is not None and self._rag_backfill_thread.isRunning():
            self._on_status_update("Reindex already running", is_error=True)
            return

        reply = QMessageBox.question(
            self,
            "Reindex All",
            "Rebuild the RAG index (chunks, embeddings and session profiles) "
            "for every session?\n\nThis runs in the background and may take a "
            "while on a large history.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        self._reindex_action.setEnabled(False)
        self._on_status_update("Reindex started...")

        self._rag_backfill_thread = RagBackfillThread(self.session_manager.db, force=True)
        self._rag_backfill_thread.progress_signal.connect(
            lambda done, total: self._on_status_update(f"Reindexing sessions {done}/{total}")
        )
        self._rag_backfill_thread.finished_signal.connect(self._on_reindex_finished)
        self._rag_backfill_thread.error_signal.connect(self._on_reindex_error)
        # Same as the assistant thread: release it only after run() returns.
        self._rag_backfill_thread.finished.connect(self._on_reindex_thread_done)
        self._rag_backfill_thread.start()

    def _on_reindex_thread_done(self):
        self._rag_backfill_thread = None

    def _on_reindex_finished(self, summary: dict):
        self._reindex_action.setEnabled(True)
        note = "" if summary.get("embeddings") else " (lexical only - embedding model unavailable)"
        self._on_status_update(
            f"Reindex complete: {summary.get('processed', 0)}/{summary.get('sessions', 0)} "
            f"sessions, {summary.get('failed', 0)} failed{note}"
        )
        try:
            from ..rag.router import invalidate_profile_cache
            invalidate_profile_cache()
        except Exception:
            pass

    def _on_reindex_error(self, message: str):
        self._reindex_action.setEnabled(True)
        self._on_status_update(f"Reindex failed: {message}", is_error=True)

    def _get_selected_session_id(self) -> Optional[int]:
        """Get the currently selected session ID from the sessions table.
        
        Returns the _selected_session_id if set, None otherwise.
        """
        return self._selected_session_id
    
    def _load_recent_sessions(self, limit: int = 5):
        """Load the most recent sessions for the search dropdown.
        
        Args:
            limit: Maximum number of sessions to load (default 5)
        """
        # Store recent sessions in memory for dropdown
        self._recent_sessions = self._get_filtered_sessions("", limit)
    
    def _filter_sessions_by_name(self, filter_text: str):
        """Filter sessions by name matching the filter text.
        
        Args:
            filter_text: Text to filter session names by
        """
        # Store filtered sessions in memory for dropdown
        self._recent_sessions = self._get_filtered_sessions(filter_text, 5)
    
    def _on_session_search_text_changed(self, text: str):
        """Handle text changes in the session search input.
        
        Args:
            text: The current text in the search field
        """
        # Filter and show dropdown automatically
        self._show_session_search_dropdown()
    
    def _refresh_session_completer(self):
        """Refresh the completer with current session list."""
        # Skip if session_manager not initialized yet
        if not self.session_manager:
            return
        
        try:
            sessions = self.session_manager.db.list_sessions()
            
            # Create list of display strings
            from datetime import datetime
            session_list = []
            session_map = {}  # Maps display text to session_id
            
            for session in sessions:
                session_id = session.get('id')
                session_name = session.get('name', 'Unnamed')
                start_time = session.get('start_time', 0)
                
                # Format display text
                if start_time:
                    try:
                        dt = datetime.fromtimestamp(start_time)
                        display_text = f"{session_name} - {dt.strftime('%Y-%m-%d %H:%M')}"
                    except:
                        display_text = f"{session_name} (ID: {session_id})"
                else:
                    display_text = f"{session_name} (ID: {session_id})"
                
                session_list.append(display_text)
                session_map[display_text] = session_id
            
            # Sort by most recent
            session_list.sort()
            
            # Update completer
            self._session_completer_model = QStringListModel(session_list)
            self._session_completer.setModel(self._session_completer_model)
            self._session_completer_map = session_map
            
        except Exception as e:
            logger.error(f"Failed to refresh session completer: {e}")
    
    def _session_name_for_id(self, session_id) -> Optional[str]:
        """Return the display name for a session id, or None."""
        if session_id is None:
            return None
        try:
            for session in self.session_manager.db.list_sessions():
                if session.get('id') == session_id:
                    return session.get('name', 'Unnamed')
        except Exception as e:
            logger.error(f"Failed to look up session name: {e}")
        return None

    def _scope_marker_text(self, explicit_scope: str, session_id) -> str:
        """Short human label of the scope that produced an answer."""
        if explicit_scope == "current_session":
            name = self._session_name_for_id(session_id)
            return f"Specific Session — {name}" if name else "Specific Session"
        return "Any Session"

    def _scope_suffix(self) -> str:
        """Marker appended to an assistant answer bubble to show its scope."""
        marker = getattr(self, '_last_scope_marker', None)
        return f"\n\n— via {marker}" if marker else ""

    def _update_scope_label(self):
        """Update the scope mode indicator to reflect the active scope mode."""
        if not hasattr(self, '_scope_label'):
            return

        try:
            scope_value = self.scope_combo.currentData() if hasattr(self, 'scope_combo') else 'any'
            if scope_value == 'current':
                session_id = self._selected_session_id
                if session_id is None and self.session_manager:
                    active_session = self.session_manager.get_active_session()
                    session_id = active_session.id if active_session else None
                name = self._session_name_for_id(session_id)
                detail = name if name else "(no session selected)"
                self._scope_label.setText(f"Scope: {detail}")
                self._scope_label.setStyleSheet("color: #0078d4; font-weight: bold;")
                self._scope_label.setVisible(True)
            else:
                # The scope combo/search bar already conveys "Any Session";
                # the label is only shown to name the specific session in scope.
                self._scope_label.setVisible(False)
            self._save_session_scope()  # BU119
        except Exception as e:
            logger.error(f"Failed to update scope label: {e}")
            self._scope_label.setText("Scope: (error)")
            self._scope_label.setStyleSheet("color: gray; font-style: italic;")
            self._scope_label.setVisible(True)

        # Also update summary icon state
        self._update_summary_icon_state()
    
    def _update_summary_icon_state(self):
        """Update the summary icon button based on selected session."""
        if not hasattr(self, 'view_summary_icon_button'):
            return
        
        # Check if selected session has a summary
        if self._selected_session_id:
            try:
                summaries = self.session_manager.db.get_summaries(self._selected_session_id)
                self.view_summary_icon_button.setEnabled(len(summaries) > 0)
                self.view_summary_icon_button.setToolTip("View Summary" if summaries else "No summary available")
            except Exception:
                self.view_summary_icon_button.setEnabled(False)
        else:
            # Check active session
            active_session = self.session_manager.get_active_session() if self.session_manager else None
            if active_session:
                try:
                    summaries = self.session_manager.db.get_summaries(active_session.id)
                    self.view_summary_icon_button.setEnabled(len(summaries) > 0)
                    self.view_summary_icon_button.setToolTip("View Summary" if summaries else "No summary available")
                except Exception:
                    self.view_summary_icon_button.setEnabled(False)
            else:
                self.view_summary_icon_button.setEnabled(False)
                self.view_summary_icon_button.setToolTip("No session selected")
    
    def _on_session_completer_selected(self, text: str):
        """Handle session selection from completer.
        
        Args:
            text: The selected text
        """
        session_id = self._session_completer_map.get(text)
        if session_id is not None:
            self._select_session_by_id(session_id, display_name=text)

    def _select_session_by_id(self, session_id: int, display_name: Optional[str] = None):
        """Select a session as the assistant's transcript scope and load its lines.

        Moves the scope combo to Specific Session for this session (selecting
        a session always scopes to it) via `_switch_scope_to_specific`.
        """
        if hasattr(self, '_detached_window') and self._detached_window:
            self._on_close_detached_window()
        self._switch_scope_to_specific(session_id, display_name=display_name)
        logger.info(f"Selected session for assistant: {session_id}")
    
    # ------------------------------------------------------------------
    # "All Sessions" browser window
    # ------------------------------------------------------------------
    _ALL_SESSIONS_MENU_QSS = """
        QMenu {
            background-color: #071D52;
            border: 2px solid #3A67C7;
            color: #FFF0BF;
            padding: 5px;
            font-family: "Courier New";
            font-size: 13px;
        }
        QMenu::item { padding: 6px 24px 6px 14px; border-radius: 4px; }
        QMenu::item:selected { background-color: #315DB1; }
        QMenu::item:disabled { color: #5E75A8; }
        QMenu::separator { height: 1px; background: #254D9C; margin: 5px 6px; }
    """

    _ALL_SESSIONS_DIALOG_QSS = """
        QDialog#AllSessionsDialog { background: #061946; }
        QLabel#AllSessionsMuted {
            color: #8EA7D8; font-family: "Courier New"; font-size: 9pt;
        }
        QProgressBar#AllSessionsProgress {
            background: #071D52; border: 2px solid #254D9C; border-radius: 5px;
        }
        QProgressBar#AllSessionsProgress::chunk { background: #E37C25; border-radius: 2px; }
        QLineEdit#AllSessionsFilter {
            border-radius: 6px;
            padding: 5px 10px;
            min-height: 22px;
            font-family: "Courier New";
            font-size: 10pt;
            font-weight: 700;
        }
        QLineEdit#AllSessionsFilter:focus { border: 2px solid #FFEFC1; }
        QPushButton#AllSessionsUpload, QPushButton#AllSessionsUploadTranscript,
        QPushButton#AllSessionsClose {
            border-radius: 5px;
            padding: 0px 16px;
            font-family: "Courier New";
            font-size: 9.5pt;
            font-weight: 700;
        }
        QPushButton#AllSessionsUpload, QPushButton#AllSessionsUploadTranscript {
            color: #071846;
            background: #E37C25;
            border: 2px solid #F4A25C;
        }
        QPushButton#AllSessionsUpload:hover,
        QPushButton#AllSessionsUploadTranscript:hover { background: #EE8C38; }
        QPushButton#AllSessionsUpload:pressed,
        QPushButton#AllSessionsUploadTranscript:pressed { background: #C8661A; border-color: #E37C25; }
        QPushButton#AllSessionsClose {
            color: #FFF0BF;
            background: transparent;
            border: 2px solid #3A67C7;
        }
        QPushButton#AllSessionsClose:hover { background: #274F9B; }
        QPushButton#AllSessionsClose:pressed { background: #1E3F82; }
    """

    # (key, label) for the segmented filter above the card list.
    _ALL_SESSIONS_FILTERS = (
        ("all", "All"),
        ("needs_transcript", "Needs transcript"),
        ("needs_summary", "Needs summary"),
    )

    def _live_session_state(self):
        """``(session_id, "recording" | "paused")`` for the session being
        captured right now, or None. Read from the in-memory session manager,
        not the DB ``status`` column, which can be left "active" by a crash."""
        current = getattr(self.session_manager, 'current_session', None)
        if current is None:
            return None
        if current.status == Session.STATUS_ACTIVE:
            return (current.id, 'recording')
        if current.status == Session.STATUS_PAUSED:
            return (current.id, 'paused')
        return None

    def _load_sessions_for_browser(self) -> list:
        """All sessions, newest first, with the stored status flags reconciled
        against the transcripts / summaries that actually exist.

        One EXISTS-based query for the whole list (list_sessions_with_flags)
        instead of a get_transcripts()/get_summaries() round trip per
        session - the previous version fetched every transcript row of every
        session just to check whether the list was non-empty.
        """
        sessions = self.session_manager.db.list_sessions_with_flags()
        for session in sessions:
            if session.get('transcription_status', 'none') == 'none' and session['has_transcripts']:
                session['transcription_status'] = 'transcribed'
            if session.get('summary_status', 'none') == 'none' and session['has_summary']:
                session['summary_status'] = 'summarized'
        return sessions

    @staticmethod
    def _format_elapsed(seconds: float) -> str:
        seconds = max(0, int(seconds))
        hours, rest = divmod(seconds, 3600)
        minutes, secs = divmod(rest, 60)
        return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"

    def _session_card_meta(self, session: dict, live_state) -> str:
        """Second line of a session card: time span / duration, or live timer."""
        from datetime import datetime

        start = session.get('start_time') or 0
        end = session.get('end_time') or 0
        try:
            start_dt = datetime.fromtimestamp(start) if start else None
        except (ValueError, OSError, OverflowError):
            start_dt = None
        start_str = start_dt.strftime('%H:%M') if start_dt else 'Unknown time'

        if live_state == 'recording':
            current = self.session_manager.current_session
            began = getattr(current, 'start_time', None) or start_dt
            elapsed = (datetime.now() - began).total_seconds() if began else 0
            return f"Started {start_str}  ·  recording {self._format_elapsed(elapsed)}"
        if live_state == 'paused':
            return f"Started {start_str}  ·  paused"

        if session.get('origin') == 'text':
            day = start_dt.strftime('%d %b %Y') if start_dt else 'Unknown date'
            return f"{day}  ·  Inserted transcript"

        if start and end and end > start:
            try:
                end_str = datetime.fromtimestamp(end).strftime('%H:%M')
            except (ValueError, OSError, OverflowError):
                end_str = ''
            minutes = max(1, round((end - start) / 60))
            duration = f"{minutes} min" if minutes < 60 else f"{minutes // 60} h {minutes % 60:02d} min"
            return f"{start_str} – {end_str}  ·  {duration}" if end_str else f"{start_str}  ·  {duration}"
        return start_str

    @staticmethod
    def _session_day_label(start_time) -> str:
        """Group title for a session's day: Today / Yesterday / weekday + date."""
        from datetime import datetime, date, timedelta

        if not start_time:
            return "Undated"
        try:
            day = datetime.fromtimestamp(start_time).date()
        except (ValueError, OSError, OverflowError):
            return "Undated"
        today = date.today()
        if day == today:
            return "Today"
        if day == today - timedelta(days=1):
            return "Yesterday"
        return f"{day:%a} · {day:%b} {day.day}, {day.year}"

    def _show_all_sessions_window(self):
        """Browse every session as cards grouped by day (BU102).

        Each card shows the session's processing state, an Open button and a
        "•••" actions menu; the session being recorded right now is marked
        with a pulsing LIVE (or PAUSED) badge and a running timer.
        """
        from datetime import datetime

        dialog = QDialog(self)
        dialog.setObjectName("AllSessionsDialog")
        dialog.setWindowTitle("All Sessions")
        dialog.setStyleSheet(self._ALL_SESSIONS_DIALOG_QSS)
        dialog.setWindowFlag(Qt.WindowMaximizeButtonHint, True)
        dialog.setSizeGripEnabled(True)
        dialog.setMinimumSize(700, 460)
        dialog.resize(getattr(self, '_all_sessions_window_size', None) or QSize(920, 640))
        self._all_sessions_dialog = dialog

        outer = QVBoxLayout(dialog)
        outer.setContentsMargins(0, 0, 0, 0)
        panel = PixelPanel()
        outer.addWidget(panel)

        layout = QVBoxLayout(panel)
        layout.setContentsMargins(18, 16, 18, 14)
        layout.setSpacing(10)

        # Title row: heading on the left, counts on the right.
        title_row = QHBoxLayout()
        title_row.addWidget(PixelSectionTitle("ALL SESSIONS"), 1)
        count_label = QLabel()
        count_label.setObjectName("AllSessionsMuted")
        count_label.setTextFormat(Qt.RichText)
        title_row.addWidget(count_label, 0)
        layout.addLayout(title_row)

        # Filter row: free-text search plus the segmented status filter.
        filter_row = QHBoxLayout()
        filter_row.setSpacing(8)
        search = QLineEdit()
        search.setObjectName("AllSessionsFilter")
        search.setPlaceholderText("Search by name or date…")
        search.setClearButtonEnabled(True)
        filter_row.addWidget(search, 1)
        filter_group = QButtonGroup(dialog)
        filter_group.setExclusive(True)
        for key, label in self._ALL_SESSIONS_FILTERS:
            chip = pixel_filter_chip(label)
            chip.setProperty("filterKey", key)
            chip.setChecked(key == getattr(self, '_all_sessions_filter', 'all'))
            filter_group.addButton(chip)
            filter_row.addWidget(chip, 0)
        if filter_group.checkedButton() is None:
            filter_group.buttons()[0].setChecked(True)
        layout.addLayout(filter_row)

        # Scrolling column of session cards.
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        container = QWidget()
        list_layout = QVBoxLayout(container)
        list_layout.setContentsMargins(0, 0, 8, 4)
        list_layout.setSpacing(8)
        scroll.setWidget(container)
        layout.addWidget(scroll, 1)

        # Bottom bar: usage hint, then upload / close. No Refresh: the list
        # already reloads after every action and whenever recording changes.
        bottom_row = QHBoxLayout()
        bottom_row.setSpacing(8)
        hint = QLabel("Double-click a card or press Open to load a session")
        hint.setObjectName("AllSessionsMuted")
        bottom_row.addWidget(hint, 1)
        # Upload progress: hidden until an upload is running.
        progress = QProgressBar()
        progress.setObjectName("AllSessionsProgress")
        progress.setRange(0, 100)
        progress.setTextVisible(False)
        progress.setFixedSize(180, 12)
        progress.hide()
        bottom_row.addWidget(progress, 0, Qt.AlignVCenter)
        upload_btn = QPushButton("Upload Audio")
        upload_btn.setObjectName("AllSessionsUpload")
        upload_btn.setToolTip("Add a WAV, iPhone (.m4a) or WhatsApp (.opus) recording as a new session and transcribe it")
        upload_transcript_btn = QPushButton("Upload Transcript")
        upload_transcript_btn.setObjectName("AllSessionsUploadTranscript")
        upload_transcript_btn.setToolTip("Add a .txt transcript as a new session and summarize it")
        close_btn = QPushButton("Close")
        close_btn.setObjectName("AllSessionsClose")
        for btn in (upload_btn, upload_transcript_btn, close_btn):
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFixedHeight(32)
        bottom_row.addWidget(upload_btn, 0)
        bottom_row.addWidget(upload_transcript_btn, 0)
        bottom_row.addWidget(close_btn, 0)
        layout.addLayout(bottom_row)

        state = {
            'sessions': [],
            'cards': {},       # session_id -> PixelSessionCard (visible ones)
            'order': [],       # visible session ids, top to bottom
            'selected': None,
            'live': None,      # last seen _live_session_state()
            'busy': {},        # session_id -> (chip attribute, text) while a job runs
        }

        def open_session(session_id):
            self._select_session_by_id(session_id)
            QApplication.processEvents()
            logger.info(f"Selected session from All Sessions window: {session_id}")
            dialog.close()

        def select(session_id):
            previous = state['cards'].get(state['selected'])
            if previous is not None:
                previous.set_selected(False)
            state['selected'] = session_id
            card = state['cards'].get(session_id)
            if card is not None:
                card.set_selected(True)

        def matches(session) -> bool:
            key = filter_group.checkedButton().property("filterKey")
            trans_ready = session['transcription_status'] == 'transcribed'
            sum_ready = session['summary_status'] == 'summarized'
            if key == 'needs_transcript' and trans_ready:
                return False
            if key == 'needs_summary' and (sum_ready or not trans_ready):
                return False
            needle = search.text().strip().lower()
            if not needle:
                return True
            start = session.get('start_time') or 0
            try:
                stamp = datetime.fromtimestamp(start).strftime('%Y-%m-%d %H:%M') if start else ''
            except (ValueError, OSError, OverflowError):
                stamp = ''
            return needle in session['name'].lower() or needle in stamp

        def clear_list():
            while list_layout.count():
                item = list_layout.takeAt(0)
                widget = item.widget()
                if widget is not None:
                    widget.hide()
                    widget.deleteLater()
            state['cards'] = {}
            state['order'] = []

        def render():
            clear_list()
            live = state['live']
            live_id = live[0] if live else None
            sessions = state['sessions']
            visible = [s for s in sessions if matches(s)]

            total = len(sessions)
            counts = f"{total} session{'s' if total != 1 else ''}"
            if len(visible) != total:
                counts = f"{len(visible)} of {counts}"
            if live:
                dot = "#FF6B5E" if live[1] == 'recording' else "#F2B84B"
                word = "recording" if live[1] == 'recording' else "paused"
                counts += f'  ·  <span style="color:{dot};">●</span> 1 {word}'
            count_label.setText(counts)

            if not visible:
                if total:
                    message = "No sessions match this search or filter."
                else:
                    message = "No sessions yet.\nStart a recording and it will show up here."
                empty = QLabel(message)
                empty.setAlignment(Qt.AlignCenter)
                empty.setObjectName("AllSessionsMuted")
                empty.setStyleSheet("QLabel { padding: 40px 0; font-size: 11pt; }")
                list_layout.addWidget(empty)
                list_layout.addStretch(1)
                return

            groups = []  # [(label, [session, ...])], preserving newest-first order
            for session in visible:
                label = self._session_day_label(session.get('start_time'))
                if not groups or groups[-1][0] != label:
                    groups.append((label, []))
                groups[-1][1].append(session)

            for label, members in groups:
                list_layout.addWidget(pixel_group_header(label, len(members)))
                for session in members:
                    session_id = session['id']
                    live_state = live[1] if session_id == live_id else None
                    card = PixelSessionCard(
                        session['name'],
                        self._session_card_meta(session, live_state),
                        session['transcription_status'] == 'transcribed',
                        session['summary_status'] == 'summarized',
                        live_state=live_state,
                        shot_count=session.get('screenshot_count', 0),
                    )
                    card.actions_button.setMenu(
                        self._build_session_actions_menu(session, card, dialog, live_state)
                    )
                    busy = state['busy'].get(session_id)
                    if busy is not None:
                        getattr(card, busy[0]).set_state('busy', busy[1])
                    card.clicked.connect(lambda sid=session_id: select(sid))
                    card.open_requested.connect(lambda sid=session_id: open_session(sid))
                    list_layout.addWidget(card)
                    state['cards'][session_id] = card
                    state['order'].append(session_id)

            list_layout.addStretch(1)
            if state['selected'] in state['cards']:
                state['cards'][state['selected']].set_selected(True)

        def reload():
            state['live'] = self._live_session_state()
            try:
                state['sessions'] = self._load_sessions_for_browser()
            except Exception as e:
                logger.error(f"Failed to list sessions: {e}")
                state['sessions'] = []
            render()

        def on_filter_changed(button):
            self._all_sessions_filter = button.property("filterKey")
            render()

        def open_selected():
            session_id = state['selected'] if state['selected'] in state['cards'] else None
            if session_id is None and state['order']:
                session_id = state['order'][0]
            if session_id is not None:
                open_session(session_id)

        def focus_session(session_id):
            # Clear search / filter so the card is listed, then select it and
            # scroll it into view. New cards are shown and laid out over the
            # next few event-loop passes, so the scroll waits for them.
            search.clear()
            for chip in filter_group.buttons():
                chip.setChecked(chip.property("filterKey") == "all")
            self._all_sessions_filter = "all"
            reload()
            select(session_id)
            card = state['cards'].get(session_id)
            if card is not None:
                QTimer.singleShot(30, lambda: scroll.ensureWidgetVisible(card))
            return card

        # Live indicator: blink the badge, advance the timer, and rebuild the
        # list if recording starts, stops, pauses or resumes meanwhile.
        def tick():
            live = self._live_session_state()
            if live != state['live']:
                reload()
                return
            if not live:
                return
            card = state['cards'].get(live[0])
            if card is None:
                return
            card.live_badge.pulse()
            session = next((s for s in state['sessions'] if s['id'] == live[0]), None)
            if session is not None:
                card.set_meta(self._session_card_meta(session, live[1]))

        live_timer = QTimer(dialog)
        live_timer.setInterval(600)
        live_timer.timeout.connect(tick)

        search.textChanged.connect(lambda _text: render())
        search.returnPressed.connect(open_selected)
        filter_group.buttonClicked.connect(on_filter_changed)
        upload_btn.clicked.connect(lambda: self._upload_audio_from_all_sessions(dialog))
        upload_transcript_btn.clicked.connect(lambda: self._upload_transcript_from_all_sessions(dialog))
        close_btn.clicked.connect(dialog.close)

        # Upload progress. The bar is determinate but the work is not: it
        # creeps toward the current stage's ceiling and jumps to full when the
        # last job ends, so it reads as "working" without promising a time.
        hint_text = hint.text()
        busy_state = {'jobs': 0, 'value': 0.0, 'ceiling': 0, 'generation': 0}
        progress_timer = QTimer(dialog)
        progress_timer.setInterval(100)

        def advance_progress():
            busy_state['value'] += (busy_state['ceiling'] - busy_state['value']) * 0.05
            progress.setValue(int(busy_state['value']))

        progress_timer.timeout.connect(advance_progress)

        def busy_update(text, ceiling):
            hint.setText(text)
            busy_state['ceiling'] = ceiling

        def busy_begin(text, ceiling):
            busy_state['jobs'] += 1
            busy_state['generation'] += 1
            if busy_state['jobs'] == 1:
                busy_state['value'] = 0.0
                progress.setValue(0)
                progress.show()
                progress_timer.start()
            busy_update(text, ceiling)

        def busy_end():
            busy_state['jobs'] = max(0, busy_state['jobs'] - 1)
            if busy_state['jobs']:
                return
            progress_timer.stop()
            progress.setValue(100)
            generation = busy_state['generation']

            def finish():
                if busy_state['jobs'] == 0 and busy_state['generation'] == generation:
                    progress.hide()
                    hint.setText(hint_text)

            QTimer.singleShot(350, finish)

        def mark_busy(session_id, chip, text):
            state['busy'][session_id] = (chip, text)
            card = state['cards'].get(session_id)
            if card is not None:
                getattr(card, chip).set_state('busy', text)

        def clear_busy(session_id):
            state['busy'].pop(session_id, None)

        # Rename / delete / transcribe / summarize refresh the list in place;
        # an upload also brings its new card into view.
        dialog._reload_sessions = reload
        dialog._focus_session = focus_session
        dialog._busy_begin = busy_begin
        dialog._busy_update = busy_update
        dialog._busy_end = busy_end
        dialog._mark_busy = mark_busy
        dialog._clear_busy = clear_busy

        def on_finished(_result):
            live_timer.stop()
            self._all_sessions_window_size = dialog.size()
            if self._all_sessions_dialog is dialog:
                self._all_sessions_dialog = None

        dialog.finished.connect(on_finished)

        reload()
        live_timer.start()
        search.setFocus()
        dialog.exec()

    def _build_session_actions_menu(self, session: dict, card, dialog: QDialog, live_state) -> QMenu:
        """Build the "•••" menu of a session card in the All Sessions window.

        Processing and deleting are disabled while the session is still being
        recorded - there is no finished audio to work on yet.
        """
        session_id = session['id']
        session_name = session['name']
        trans_ready = session['transcription_status'] == 'transcribed'
        sum_ready = session['summary_status'] == 'summarized'
        is_live = live_state is not None

        menu = QMenu(card)
        menu.setStyleSheet(self._ALL_SESSIONS_MENU_QSS)

        def run_step(chip, busy_text, runner, **extra):
            # Show progress on the card. runner() submits a background job
            # and returns at once; the chip stays busy until the job's own
            # callback refreshes this dialog with the outcome.
            chip.set_state('busy', busy_text)
            runner(session_id, None, on_done=lambda: self._refresh_all_sessions_window(dialog),
                   **extra)

        if not trans_ready:
            action = menu.addAction(
                "Transcribe" if not is_live else "Transcribe (after recording stops)",
                lambda: run_step(card.transcript_chip, "Transcribing", self._run_transcription,
                                 dialog=dialog),
            )
            action.setEnabled(not is_live)
        elif not sum_ready:
            action = menu.addAction(
                "Summarize" if not is_live else "Summarize (after recording stops)",
                lambda: run_step(card.summary_chip, "Summarizing", self._run_summarization),
            )
            action.setEnabled(not is_live)
        if sum_ready:
            menu.addAction("View summary",
                           lambda: self._show_summary_by_session_id(session_id, session_name))
        menu.addAction("View screenshots",
                       lambda: self._show_screenshots_by_session_id(session_id, session_name))
        menu.addAction("Rename…",
                       lambda: self._rename_session_from_dialog(session_id, session_name, dialog))
        # STATUS_PROCESSING: startup repair (SessionManager._recover_after_restart)
        # now flips a session left mid-finalize back to 'stopped', but a
        # session can still show 'processing' for a moment before that repair
        # runs - kept resumable defensively, matching resume_stopped_session.
        if not is_live and session.get('origin') != 'text' and session.get('status') in (
            Session.STATUS_STOPPED, Session.STATUS_PAUSED,
            Session.STATUS_COMPLETED, Session.STATUS_PROCESSING,
        ):
            other_live = self._live_session_state()
            resume_action = menu.addAction(
                "Resume" if not other_live else "Resume (stop current session first)",
                lambda: self._resume_session_from_all_sessions(session_id, dialog),
            )
            resume_action.setEnabled(other_live is None)
        menu.addSeparator()
        delete_action = menu.addAction(
            "Delete session" if not is_live else "Delete (stop recording first)",
            lambda: self._delete_from_all_sessions(session_id, dialog),
        )
        delete_action.setEnabled(not is_live)
        return menu

    def _rename_session_from_dialog(self, session_id: int, old_name: str, dialog: QDialog):
        """Prompt for a new session name and persist it."""
        new_name, ok = QInputDialog.getText(
            dialog, "Rename Session", "Session name:", text=old_name
        )
        if not ok:
            return
        new_name = new_name.strip()
        if not new_name or new_name == old_name:
            return
        try:
            self.session_manager.db.update_session(session_id, name=new_name)
            logger.info(f"Renamed session {session_id} to '{new_name}'")
            self._refresh_session_completer()
            self._refresh_all_sessions_window(dialog)
        except Exception as e:
            logger.error(f"Failed to rename session: {e}")
            self._on_status_update(f"Error renaming session: {e}", is_error=True)

    def _delete_from_all_sessions(self, session_id: int, dialog: QDialog):
        """Delete a session from the All Sessions window and refresh it."""
        if self._delete_session_by_id(session_id):
            self._refresh_all_sessions_window(dialog)

    def _resume_session_from_all_sessions(self, session_id: int, dialog: QDialog):
        """Resume a stopped/paused session from its All Sessions card.

        Continues recording into the same session_id (same start_time, name,
        transcripts and screenshots) rather than creating a new session.
        """
        try:
            session = self.session_manager.resume_stopped_session(session_id)
            if not session:
                return

            self._clear_transcription_view()
            self._selected_session_id = session.id
            self.scope_combo.setCurrentIndex(0)  # "Specific Session"
            self.session_search_input.setText(session.name)
            self._search_input_shows_session = True
            self._update_scope_label()
            self._load_session_transcripts_for_current_session(allow_live=True)

            self._update_ui_state()
            self._on_status_update(f"Session '{session.name}' resumed")
            self._refresh_all_sessions_window(dialog)
            dialog.close()
        except Exception as e:
            logger.error(f"Failed to resume session: {e}")
            self._on_status_update(f"Error resuming session: {e}", is_error=True)

    def _upload_audio_from_all_sessions(self, dialog: QDialog):
        """Import a WAV / iPhone recording as a new session and transcribe it (BU104).

        Both decoding the file (PyAV, can take a while for a long recording)
        and transcribing it run on SessionManager's background job thread, so
        this dialog stays interactive throughout instead of freezing under a
        wait cursor. The new card is brought into view with a busy Transcript
        chip once the import completes; if transcription fails the session
        stays listed as "Needs transcript" so it can be retried from its
        "•••" menu.
        """
        start_dir = getattr(self, '_upload_audio_dir', None) or \
            QStandardPaths.writableLocation(QStandardPaths.DownloadLocation)
        patterns = ' '.join(f'*{ext}' for ext in UPLOAD_AUDIO_EXTENSIONS)
        path, _ = QFileDialog.getOpenFileName(
            dialog, "Upload Audio", start_dir,
            f"Audio recordings ({patterns});;All files (*)",
        )
        if not path:
            return
        self._upload_audio_dir = os.path.dirname(path)
        file_name = os.path.basename(path)

        self._on_status_update(f"Importing {file_name}…")

        def import_done(session, error):
            def apply():
                if error is not None:
                    logger.error(f"Failed to import audio {path}: {error}")
                    self._on_status_update(f"Could not import {file_name}: {error}", is_error=True)
                    QMessageBox.warning(dialog, "Upload Audio", f"Could not import {file_name}.\n\n{error}")
                    return
                self._refresh_session_completer()
                card = dialog._focus_session(session.id)
                if card is not None:
                    card.transcript_chip.set_state('busy', 'Transcribing')
                self._transcribe_uploaded_session(session, dialog)
            self._post_to_ui(apply)

        self.session_manager.submit_job(
            f'import {file_name}',
            lambda: self.session_manager.import_audio_file(path),
            import_done,
        )

    def _upload_transcript_from_all_sessions(self, dialog: QDialog):
        """Import a text transcript as a new session, then summarize it (BU137).

        Mirrors the audio upload: both steps run on SessionManager's job
        thread. If the summary fails the session stays listed as "Needs
        summary" and can be retried from its "•••" menu.
        """
        start_dir = getattr(self, '_upload_transcript_dir', None) or \
            QStandardPaths.writableLocation(QStandardPaths.DownloadLocation)
        patterns = ' '.join(f'*{ext}' for ext in UPLOAD_TRANSCRIPT_EXTENSIONS)
        path, _ = QFileDialog.getOpenFileName(
            dialog, "Upload Transcript", start_dir,
            f"Transcripts ({patterns});;All files (*)",
        )
        if not path:
            return
        self._upload_transcript_dir = os.path.dirname(path)
        file_name = os.path.basename(path)

        self._on_status_update(f"Importing {file_name}…")
        dialog._busy_begin(f"Importing {file_name}…", 40)

        def import_done(session, error):
            def apply():
                if error is not None:
                    dialog._busy_end()
                    logger.error(f"Failed to import transcript {path}: {error}")
                    self._on_status_update(f"Could not import {file_name}: {error}", is_error=True)
                    QMessageBox.warning(dialog, "Upload Transcript", f"Could not import {file_name}.\n\n{error}")
                    return
                self._refresh_session_completer()
                dialog._mark_busy(session.id, 'summary_chip', 'Summarizing')
                dialog._focus_session(session.id)
                dialog._busy_update(f"Summarizing '{session.name}'…", 92)
                self._summarize_uploaded_session(session, dialog)
            self._post_to_ui(apply)

        self.session_manager.submit_job(
            f'import {file_name}',
            lambda: self.session_manager.import_transcript_file(path),
            import_done,
        )

    def _summarize_uploaded_session(self, session: Session, dialog: QDialog):
        """Summarize a just-imported transcript session (background job)."""
        self._on_status_update(f"Summarizing '{session.name}'…")

        def done(outcome, error):
            def apply():
                # Back to the stored state before the list reloads, so the card
                # lands on its real chips (Ready, or "Needs summary" on failure).
                dialog._clear_busy(session.id)
                dialog._busy_end()
                if error is not None:
                    logger.error(f"Failed to summarize uploaded session {session.id}: {error}")
                    self._on_status_update(f"Summary failed: {error}", is_error=True)
                    QMessageBox.warning(dialog, "Summary Failed", str(error))
                else:
                    self._on_status_update(f"Uploaded '{session.name}' summarized")
                self._refresh_all_sessions_window(dialog)
            self._post_to_ui(apply)

        self.session_manager.submit_job(
            f'summarize uploaded session {session.id}',
            lambda: self.session_manager.summarize_session(session.id),
            done,
        )

    def _transcribe_uploaded_session(self, session: Session, dialog: QDialog):
        """Transcribe a just-uploaded session and RAG-index it (background job)."""
        self._on_status_update(f"Transcribing '{session.name}'…")
        self._run_transcription(
            session.id, dialog=dialog,
            on_done=lambda: self._refresh_all_sessions_window(dialog),
        )

    def _refresh_all_sessions_window(self, dialog):
        """Rebuild the All Sessions card list in place."""
        reload = getattr(dialog, '_reload_sessions', None)
        if reload is not None:
            reload()

    def _on_session_search_selected(self, index: int):
        """Handle session selection from the combobox.
        
        Args:
            index: The selected index
        """
        session_id = self.session_search_combo.currentData()
        if session_id is not None:
            self._selected_session_id = session_id
            self._update_scope_label()
            # Clear previous transcripts and load new ones
            self._clear_transcription_view()
            if hasattr(self, '_detached_window') and self._detached_window:
                self._on_close_detached_window()
            self._load_transcripts_for_session(session_id)
            logger.info(f"Selected session for assistant: {session_id}")
        else:
            self._selected_session_id = None
            self._update_scope_label()
    
    def _on_session_search_dropdown_opened(self, index: int):
        """Handle when the dropdown is opened - load recent sessions.
        
        Args:
            index: The selected index (can be -1 if no selection)
        """
        # Load recent sessions when dropdown is opened
        current_text = self.session_search_input.text()
        if not current_text:
            self._load_recent_sessions(5)
    
    def _show_session_search_dropdown(self):
        """Show the session search dropdown popup."""
        # Load recent sessions first
        current_text = self.session_search_input.text()
        if not current_text:
            self._load_recent_sessions(5)
        
        # Get current sessions from the internal list
        sessions_to_show = self._get_filtered_sessions(current_text)
        
        # Create a popup list
        popup = QDialog(self)
        popup.setWindowFlags(Qt.Popup)
        popup.setAttribute(Qt.WA_DeleteOnClose)
        
        layout = QVBoxLayout(popup)
        layout.setContentsMargins(0, 0, 0, 0)
        
        # Create list widget
        list_widget = QListWidget()
        list_widget.setMaximumHeight(200)
        
        # Populate with sessions
        from datetime import datetime
        for session in sessions_to_show:
            session_id = session.get('id')
            session_name = session.get('name', 'Unnamed')
            start_time = session.get('start_time', 0)
            
            # Format display text
            if start_time:
                try:
                    dt = datetime.fromtimestamp(start_time)
                    display_text = f"{session_name} - {dt.strftime('%Y-%m-%d %H:%M')}"
                except:
                    display_text = f"{session_name} (ID: {session_id})"
            else:
                display_text = f"{session_name} (ID: {session_id})"
            
            item = QListWidgetItem(display_text)
            item.setData(Qt.UserRole, session_id)
            list_widget.addItem(item)
        
        layout.addWidget(list_widget)
        
        # Handle selection
        def on_item_clicked(item):
            session_id = item.data(Qt.UserRole)
            if session_id is not None:
                self._selected_session_id = session_id
                self._update_scope_label()
                # Clear previous transcripts and load new ones
                self._clear_transcription_view()
                if hasattr(self, '_detached_window') and self._detached_window:
                    self._on_close_detached_window()
                self._load_transcripts_for_session(session_id)
                # Also update the input text
                self.session_search_input.setText(item.text())
                logger.info(f"Selected session for assistant: {session_id}")
            popup.close()
        
        list_widget.itemClicked.connect(on_item_clicked)
        
        # Position popup below the button
        pos = self.session_search_button.mapToGlobal(self.session_search_button.rect().bottomLeft())
        popup.move(pos)
        popup.exec()
    
    def _get_filtered_sessions(self, filter_text: str = "", limit: int = 5):
        """Get sessions filtered by text.
        
        Args:
            filter_text: Text to filter session names by
            limit: Maximum number of sessions to return
        Returns:
            List of session dictionaries
        """
        try:
            all_sessions = self.session_manager.db.list_sessions()
            
            # Filter by name (case-insensitive)
            if filter_text:
                filtered = [s for s in all_sessions if filter_text.lower() in s.get('name', '').lower()]
            else:
                filtered = all_sessions
            
            # Sort by start_time descending (newest first) and take limit
            sorted_sessions = sorted(filtered, key=lambda s: s.get('start_time', 0), reverse=True)[:limit]
            return sorted_sessions
            
        except Exception as e:
            logger.error(f"Failed to get filtered sessions: {e}")
            return []
    
    def _display_candidates(self, candidates: list, handoff: bool = False):
        """Display candidate sessions for user selection.

        Args:
            candidates: List of candidate session dictionaries with session_id,
                       session_name, and start_time keys.
            handoff: When True, relabel the picker toward asking in Specific
                     Session (BU090 Any Session -> detail handoff).
        """
        from datetime import datetime

        self._candidate_title.setText(
            _CANDIDATE_TITLE_HANDOFF if handoff else _CANDIDATE_TITLE_DEFAULT
        )
        self.use_candidate_button.setText(
            _CANDIDATE_BTN_HANDOFF if handoff else _CANDIDATE_BTN_DEFAULT
        )

        self._current_candidates = candidates
        self._candidate_list_widget.clear()
        
        for candidate in candidates:
            session_name = candidate.get('session_name', 'Unknown')
            session_id = candidate.get('session_id', 0)
            start_time = candidate.get('start_time', 0)
            
            # Format display text
            if start_time:
                try:
                    dt = datetime.fromtimestamp(start_time)
                    display_text = f"{session_name} - {dt.strftime('%Y-%m-%d %H:%M')}"
                except:
                    display_text = f"{session_name} (ID: {session_id})"
            else:
                display_text = f"{session_name} (ID: {session_id})"
            
            item = QListWidgetItem(display_text)
            item.setData(Qt.UserRole, session_id)
            self._candidate_list_widget.addItem(item)
        
        self.candidate_group.setVisible(True)
        self.use_candidate_button.setEnabled(False)
    
    def _clear_candidates(self):
        """Clear candidate selection UI."""
        self._current_candidates = []
        self._current_question = None
        self._candidate_list_widget.clear()
        self.candidate_group.setVisible(False)
        self.use_candidate_button.setEnabled(False)
        self._candidate_title.setText(_CANDIDATE_TITLE_DEFAULT)
        self.use_candidate_button.setText(_CANDIDATE_BTN_DEFAULT)
    
    def _on_candidate_selected(self, item: QListWidgetItem):
        """Handle candidate selection - enable the use button.
        
        Args:
            item: The selected list widget item
        """
        self.use_candidate_button.setEnabled(item is not None)
    
    def _on_use_candidate_clicked(self):
        """Handle the Use Selected Session button click - retry with selected session."""
        selected_items = self._candidate_list_widget.selectedItems()
        if not selected_items:
            return
        
        item = selected_items[0]
        selected_session_id = item.data(Qt.UserRole)
        
        if selected_session_id and self._current_question:
            # Disable button during processing
            self.use_candidate_button.setEnabled(False)
            
            # Get current settings
            agent_id = self.agent_combo.currentData()
            
            # Run query with selected session (force current_session scope)
            QTimer.singleShot(50, lambda: self._run_assistant_query(
                question=self._current_question,
                agent_id=agent_id,
                explicit_scope="current_session",
                active_session_id=None,
                selected_session_id=selected_session_id
            ))
    
    def _on_past_conversations_clicked(self):
        """Show a dialog with past assistant conversations."""
        # Get database instance
        db = self.session_manager.db
        
        # Get all conversations
        try:
            conversations = db.list_conversations()
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to load conversations: {str(e)}")
            return
        
        # Create dialog
        dialog = QDialog(self)
        dialog.setWindowTitle("Past Conversations")
        dialog.resize(600, 500)
        
        main_layout = QVBoxLayout(dialog)
        
        if not conversations:
            # No conversations yet
            no_conv_label = QLabel("No past conversations yet.\nStart a new conversation with the Assistant!")
            no_conv_label.setAlignment(Qt.AlignCenter)
            main_layout.addWidget(no_conv_label)
            
            close_button = QPushButton("Close")
            close_button.clicked.connect(dialog.close)
            main_layout.addWidget(close_button)
            
            dialog.exec_()
            return
        
        # Create split layout: list on left, messages on right
        split_layout = QSplitter(Qt.Horizontal)
        
        # Left side: conversation list
        list_widget = QListWidget()
        list_widget.setMaximumWidth(200)
        
        # Right side: messages display
        messages_scroll = QScrollArea()
        messages_widget = QWidget()
        messages_layout = QVBoxLayout(messages_widget)
        messages_scroll.setWidget(messages_widget)
        messages_scroll.setWidgetResizable(True)
        
        # Store conversation data
        conversation_data = {}
        
        for conv in conversations:
            conv_id = conv['id']
            session_id = conv.get('session_id')
            title = conv.get('title')
            created_at = conv.get('created_at', 0)
            updated_at = conv.get('updated_at', 0)
            
            # Format display text
            from datetime import datetime
            date_str = datetime.fromtimestamp(updated_at).strftime("%Y-%m-%d %H:%M")
            
            if title:
                display_text = f"{title}\n{date_str}"
            else:
                display_text = f"Conversation #{conv_id}\n{date_str}"
            
            # Add scope indicator
            if session_id:
                display_text += "\n(Session-specific)"
            else:
                display_text += "\n(All sessions)"
            
            item = QListWidgetItem(display_text)
            item.setData(Qt.UserRole, conv_id)
            list_widget.addItem(item)
            
            conversation_data[conv_id] = conv
        
        split_layout.addWidget(list_widget)
        split_layout.addWidget(messages_scroll)
        split_layout.setStretchFactor(0, 1)
        split_layout.setStretchFactor(1, 2)
        
        main_layout.addWidget(split_layout)
        
        # Close button
        close_button = QPushButton("Close")
        close_button.clicked.connect(dialog.close)
        main_layout.addWidget(close_button)
        
        # Handle selection
        def on_selection_changed():
            selected_items = list_widget.selectedItems()
            if not selected_items:
                return
            
            conv_id = selected_items[0].data(Qt.UserRole)
            
            # Clear previous messages
            while messages_layout.count():
                item = messages_layout.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()
            
            # Get messages for this conversation
            try:
                messages = db.get_messages(conv_id)
            except Exception as e:
                messages_label = QLabel(f"Error loading messages: {str(e)}")
                messages_layout.addWidget(messages_label)
                return
            
            if not messages:
                no_msg_label = QLabel("No messages in this conversation.")
                messages_layout.addWidget(no_msg_label)
                return
            
            # Display messages
            for msg in messages:
                role = msg.get('role', 'unknown')
                content = msg.get('content', '')
                timestamp = msg.get('timestamp', 0)
                time_str = datetime.fromtimestamp(timestamp).strftime("%H:%M")
                
                # Create message bubble
                msg_frame = QFrame()
                msg_frame.setFrameShape(QFrame.StyledPanel)
                msg_layout = QVBoxLayout(msg_frame)
                
                # Role label
                role_label = QLabel(f"{role.capitalize()} - {time_str}")
                role_font = role_label.font()
                role_font.setBold(True)
                role_label.setFont(role_font)
                
                if role == 'user':
                    role_label.setStyleSheet("color: #0066cc;")
                elif role == 'assistant':
                    role_label.setStyleSheet("color: #008800;")
                
                msg_layout.addWidget(role_label)
                
                # Content
                content_label = QLabel(content)
                content_label.setWordWrap(True)
                msg_layout.addWidget(content_label)
                
                messages_layout.addWidget(msg_frame)
            
            messages_layout.addStretch()
        
        list_widget.itemClicked.connect(on_selection_changed)

        dialog.exec_()

    # ------------------------------------------------------------------
    # "Search" window (sidebar "Search Chats")
    # ------------------------------------------------------------------
    _SEARCH_DIALOG_QSS = """
        QDialog#SearchDialog { background: #061946; }
        QLabel#SearchMuted {
            color: #8EA7D8; font-family: "Courier New"; font-size: 9pt;
        }
        QLabel#SearchTips {
            color: #B9C8EA; font-family: "Courier New"; font-size: 10pt;
            background: #071D52; border: 1px dashed #3A5C9E; border-radius: 6px;
            padding: 12px 14px;
        }
        QLineEdit#SearchQuery {
            border-radius: 6px;
            padding: 6px 10px;
            min-height: 26px;
            font-family: "Courier New";
            font-size: 12pt;
            font-weight: 700;
        }
        QLineEdit#SearchQuery:focus { border: 2px solid #FFEFC1; }
        QToolButton#SearchLink {
            color: #FFE9A8; background: transparent; border: none;
            font-family: "Courier New"; font-size: 9pt; font-weight: 700;
            padding: 2px 6px;
        }
        QToolButton#SearchLink:hover { color: #FFFFFF; }
        QPushButton#SearchRecent {
            color: #FFF0BF; background: #0B2762;
            border: 1px solid #254D9C; border-radius: 5px;
            padding: 7px 12px;
            font-family: "Courier New"; font-size: 10pt; font-weight: 700;
            text-align: left;
        }
        QPushButton#SearchRecent:hover { background: #12306E; border-color: #4A78D8; }
        QPushButton#SearchClose {
            color: #FFF0BF;
            background: transparent;
            border: 2px solid #3A67C7;
            border-radius: 5px;
            padding: 0px 16px;
            font-family: "Courier New";
            font-size: 9.5pt;
            font-weight: 700;
        }
        QPushButton#SearchClose:hover { background: #274F9B; }
        QPushButton#SearchClose:pressed { background: #1E3F82; }
    """

    # (key, label) for the result-kind filter; "all" shows a preview of each.
    _SEARCH_KINDS = (
        ("all", "All"),
        ("transcript", "Transcripts"),
        ("summary", "Summaries"),
        ("chat", "Chats"),
    )
    _SEARCH_LIMITS = {"transcript": 100, "summary": 40, "chat": 50}
    _SEARCH_PREVIEW = 4  # hits per kind under "All" before "Show all"
    _SEARCH_MIN_CHARS = 3  # shorter queries match nearly everything
    _RECENT_SEARCHES_MAX = 8

    def _recent_searches(self) -> list:
        stored = self._read_preferences().get('recent_searches')
        if not isinstance(stored, list):
            return []
        return [q for q in stored if isinstance(q, str) and q.strip()][:self._RECENT_SEARCHES_MAX]

    def _remember_search(self, query: str):
        query = " ".join((query or "").split())
        if not query:
            return
        recents = [q for q in self._recent_searches() if q.lower() != query.lower()]
        self._update_preferences({'recent_searches': [query, *recents][:self._RECENT_SEARCHES_MAX]})

    def _search_when(self, ts) -> str:
        """Short date for a search hit: "Today 14:32", "Sep 12 · 09:10"."""
        try:
            dt = datetime.fromtimestamp(ts) if ts else None
        except (ValueError, OSError, OverflowError, TypeError):
            dt = None
        if dt is None:
            return ""
        day = self._session_day_label(ts)
        if day in ("Today", "Yesterday"):
            return f"{day} {dt:%H:%M}"
        stamp = f"{dt:%b} {dt.day}" if dt.year == datetime.now().year else f"{dt:%b} {dt.day}, {dt.year}"
        return f"{stamp} · {dt:%H:%M}"

    def _search_hits(self, query: str) -> dict:
        """Run ``query`` against transcripts, summaries and chats.

        Returns ``{kind: [hit, ...]}`` where each hit is a display-ready dict
        (title / meta / snippet) plus what opening it needs.
        """
        db = self.session_manager.db
        terms = FindController.parse_terms(query)
        hits = {kind: [] for kind in self._SEARCH_LIMITS}

        for r in db.search_transcripts(query, limit=self._SEARCH_LIMITS["transcript"]):
            ts = r.get("timestamp") or 0
            source = {"mic": "Mic", "microphone": "Mic", "inserted": "Text"}.get(
                r.get("source"), "System")
            name = r.get("session_name") or f"Session {r.get('session_id')}"
            try:
                clock = datetime.fromtimestamp(ts).strftime("%H:%M:%S") if ts else ""
            except (ValueError, OSError, OverflowError):
                clock = ""
            hits["transcript"].append({
                "kind": "transcript",
                "title": name,
                "meta": f"{source}  ·  {self._search_when(ts)}",
                "line_title": f"{clock}  ·  {source}" if clock else source,
                "group_label": f"{name}  ·  {self._session_day_label(ts)}",
                "snippet": search_snippet(r.get("text"), terms),
                "session_id": r.get("session_id"),
                "text": r.get("text") or "",
            })

        seen_sessions = set()
        for r in db.search_summaries(query, limit=self._SEARCH_LIMITS["summary"]):
            session_id = r.get("session_id")
            if session_id in seen_sessions:  # newest summary per session only
                continue
            seen_sessions.add(session_id)
            name = r.get("session_name") or f"Session {session_id}"
            content = _MARKDOWN_MARKS.sub("", r.get("content") or "")
            hits["summary"].append({
                "kind": "summary",
                "title": name,
                "meta": self._search_when(r.get("session_start_time") or r.get("created_at")),
                "snippet": search_snippet(content, terms, 200),
                "session_id": session_id,
                "session_name": name,
            })

        for r in db.search_conversations(query, limit=self._SEARCH_LIMITS["chat"]):
            count = r.get("match_count") or 0
            meta = self._search_when(r.get("updated_at"))
            if count:
                meta = f"{count} message{'s' if count != 1 else ''}  ·  {meta}"
            hits["chat"].append({
                "kind": "chat",
                "title": r.get("title") or f"Conversation #{r.get('id')}",
                "meta": meta,
                "snippet": search_snippet(_MARKDOWN_MARKS.sub("", r.get("snippet") or ""), terms),
                "conv_id": r.get("id"),
            })
        return hits

    def _open_search_dialog(self):
        """Global keyword search over transcripts, summaries and chats.

        Opened from the sidebar "Search Chats" button. Results update as the
        user types (debounced, from ``_SEARCH_MIN_CHARS`` letters on); every
        whitespace-separated keyword must match
        (AND, case-insensitive). The kind chips filter the list - "All" shows
        a few hits of each kind with a "Show all" link. Opening a hit:

        - transcript: loads its session and lands the transcripts pane's find
          bar on that line, pre-filled so ▲/▼ walk the session's other hits;
        - summary: loads its session and opens the summary window;
        - chat: loads the conversation in the assistant panel.

        With no query the window lists recent searches (kept in preferences).
        """
        dialog = QDialog(self)
        dialog.setObjectName("SearchDialog")
        dialog.setWindowTitle("Search")
        dialog.setStyleSheet(self._SEARCH_DIALOG_QSS)
        dialog.setWindowFlag(Qt.WindowMaximizeButtonHint, True)
        dialog.setSizeGripEnabled(True)
        dialog.setMinimumSize(560, 420)
        dialog.resize(getattr(self, '_search_window_size', None) or QSize(780, 620))

        outer = QVBoxLayout(dialog)
        outer.setContentsMargins(0, 0, 0, 0)
        panel = PixelPanel()
        outer.addWidget(panel)

        layout = QVBoxLayout(panel)
        layout.setContentsMargins(18, 16, 18, 14)
        layout.setSpacing(10)

        title_row = QHBoxLayout()
        title_row.addWidget(PixelSectionTitle("SEARCH"), 1)
        count_label = QLabel()
        count_label.setObjectName("SearchMuted")
        title_row.addWidget(count_label, 0)
        layout.addLayout(title_row)

        query_input = QLineEdit()
        query_input.setObjectName("SearchQuery")
        query_input.setPlaceholderText("Search transcripts, summaries and chats…")
        query_input.setClearButtonEnabled(True)
        query_input.addAction(self._make_icon("icon_search_dark.svg"), QLineEdit.LeadingPosition)
        layout.addWidget(query_input)

        chip_row = QHBoxLayout()
        chip_row.setSpacing(8)
        kind_group = QButtonGroup(dialog)
        kind_group.setExclusive(True)
        chips = {}
        for key, label in self._SEARCH_KINDS:
            chip = pixel_filter_chip(label)
            chip.setProperty("kindKey", key)
            chip.setChecked(key == getattr(self, '_search_kind', 'all'))
            kind_group.addButton(chip)
            chip_row.addWidget(chip, 0)
            chips[key] = chip
        if kind_group.checkedButton() is None:
            chips["all"].setChecked(True)
        chip_row.addStretch(1)
        layout.addLayout(chip_row)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setFocusPolicy(Qt.NoFocus)
        container = QWidget()
        list_layout = QVBoxLayout(container)
        list_layout.setContentsMargins(0, 0, 8, 4)
        list_layout.setSpacing(8)
        scroll.setWidget(container)
        layout.addWidget(scroll, 1)

        bottom_row = QHBoxLayout()
        bottom_row.setSpacing(8)
        hint = QLabel("↑ ↓ move  ·  Enter open  ·  Esc close")
        hint.setObjectName("SearchMuted")
        bottom_row.addWidget(hint, 1)
        close_btn = QPushButton("Close")
        close_btn.setObjectName("SearchClose")
        close_btn.setCursor(Qt.PointingHandCursor)
        close_btn.setFixedHeight(32)
        # Enter in the query field opens a hit; it must never "press" Close.
        close_btn.setAutoDefault(False)
        close_btn.setDefault(False)
        bottom_row.addWidget(close_btn, 0)
        layout.addLayout(bottom_row)

        state = {
            'query': '',
            'hits': {kind: [] for kind in self._SEARCH_LIMITS},
            'cards': [],     # [(card, hit)] in display order
            'selected': -1,
            'error': None,
            'short': False,  # something typed, but too little to search
        }
        debounce = QTimer(dialog)
        debounce.setSingleShot(True)
        debounce.setInterval(220)

        def current_kind():
            return kind_group.checkedButton().property("kindKey")

        def kind_count(kind):
            n = len(state['hits'][kind])
            return f"{n}+" if n >= self._SEARCH_LIMITS[kind] else str(n)

        def total_count():
            hits = state['hits']
            total = sum(len(v) for v in hits.values())
            capped = any(len(hits[k]) >= self._SEARCH_LIMITS[k] for k in hits)
            return total, f"{total}{'+' if capped else ''}"

        def refresh_chips():
            _total, total_text = total_count()
            for key, label in self._SEARCH_KINDS:
                if not state['query']:
                    chips[key].setText(label)
                elif key == "all":
                    chips[key].setText(f"{label}  {total_text}")
                else:
                    chips[key].setText(f"{label}  {kind_count(key)}")

        def clear_list():
            while list_layout.count():
                item = list_layout.takeAt(0)
                widget = item.widget()
                if widget is not None:
                    widget.hide()
                    widget.deleteLater()
            state['cards'] = []
            state['selected'] = -1

        def select(index, reveal=True):
            cards = state['cards']
            if not cards:
                return
            index = max(0, min(index, len(cards) - 1))
            if 0 <= state['selected'] < len(cards):
                cards[state['selected']][0].set_selected(False)
            state['selected'] = index
            card = cards[index][0]
            card.set_selected(True)
            if reveal:
                scroll.ensureWidgetVisible(card, 0, 12)

        def add_card(hit, title=None, show_title=True, meta=None):
            card = PixelSearchResultCard(
                hit['kind'], title if title is not None else hit['title'],
                hit['meta'] if meta is None else meta,
                hit['snippet'], FindController.parse_terms(state['query']),
                show_title=show_title,
            )
            card.activated.connect(lambda h=hit: open_hit(h))
            list_layout.addWidget(card)
            state['cards'].append((card, hit))

        def add_message(text):
            label = QLabel(text)
            label.setWordWrap(True)
            label.setAlignment(Qt.AlignCenter)
            label.setObjectName("SearchMuted")
            label.setStyleSheet("QLabel { padding: 40px 12px; font-size: 11pt; }")
            list_layout.addWidget(label)

        def forget_recents():
            self._update_preferences({'recent_searches': []})
            render()

        def render_start():
            recents = self._recent_searches()
            if recents:
                head = QHBoxLayout()
                head.setContentsMargins(0, 0, 0, 0)
                head_widget = QWidget()
                head_widget.setLayout(head)
                head.addWidget(pixel_group_header("Recent searches"), 1)
                forget = QToolButton()
                forget.setObjectName("SearchLink")
                forget.setText("Clear")
                forget.setCursor(Qt.PointingHandCursor)
                forget.setFocusPolicy(Qt.NoFocus)
                forget.clicked.connect(forget_recents)
                head.addWidget(forget, 0, Qt.AlignBottom)
                list_layout.addWidget(head_widget)
                for recent in recents:
                    btn = QPushButton()
                    btn.setObjectName("SearchRecent")
                    btn.setAutoDefault(False)
                    btn.setText(f"↺  {recent}")
                    btn.setCursor(Qt.PointingHandCursor)
                    btn.setFocusPolicy(Qt.NoFocus)
                    btn.clicked.connect(lambda _=False, q=recent: run_now(q))
                    list_layout.addWidget(btn)
                list_layout.addSpacing(8)
            tips = QLabel(
                "Search every transcript line, session summary and assistant chat at once.<br><br>"
                "&#8226;&nbsp;Several words: a result must contain <b>all</b> of them.<br>"
                "&#8226;&nbsp;Open a transcript hit to jump to that exact line in its session.<br>"
                "&#8226;&nbsp;Open a summary hit to read that session's summary.<br>"
                "&#8226;&nbsp;Ctrl+F searches inside the pane you're working in."
            )
            tips.setObjectName("SearchTips")
            tips.setTextFormat(Qt.RichText)
            tips.setWordWrap(True)
            list_layout.addWidget(tips)

        def render():
            clear_list()
            query = state['query']
            hits = state['hits']
            total, total_text = total_count()

            if not query:
                count_label.setText(
                    f"Type at least {self._SEARCH_MIN_CHARS} letters" if state['short'] else ""
                )
                render_start()
            elif state['error']:
                count_label.setText("")
                add_message(f"Search failed: {state['error']}")
            elif not total:
                count_label.setText("No matches")
                add_message(f"Nothing matches “{query}”.\n"
                            "Every word has to appear - try fewer or shorter keywords.")
            else:
                count_label.setText(f"{total_text} match{'es' if total != 1 else ''}")
                kind = current_kind()
                if kind == "all":
                    for key, label in self._SEARCH_KINDS[1:]:
                        kind_hits = hits[key]
                        if not kind_hits:
                            continue
                        list_layout.addWidget(pixel_group_header(label, len(kind_hits)))
                        for hit in kind_hits[:self._SEARCH_PREVIEW]:
                            add_card(hit)
                        if len(kind_hits) > self._SEARCH_PREVIEW:
                            more = QToolButton()
                            more.setObjectName("SearchLink")
                            more.setText(f"Show all {kind_count(key)} {label.lower()}  →")
                            more.setCursor(Qt.PointingHandCursor)
                            more.setFocusPolicy(Qt.NoFocus)
                            more.clicked.connect(lambda _=False, k=key: show_kind(k))
                            list_layout.addWidget(more, 0, Qt.AlignRight)
                elif not hits[kind]:
                    add_message(f"No {dict(self._SEARCH_KINDS)[kind].lower()} match “{query}”.")
                elif kind == "transcript":
                    # One header per session; the cards then only need the time.
                    groups = {}
                    for hit in hits["transcript"]:
                        groups.setdefault(hit['session_id'], []).append(hit)
                    for members in groups.values():
                        list_layout.addWidget(pixel_group_header(members[0]['group_label'], len(members)))
                        for hit in members:
                            add_card(hit, title=hit['line_title'], meta="")
                else:
                    for hit in hits[kind]:
                        add_card(hit)

            list_layout.addStretch(1)
            # Start at the top, so the first group header stays in view.
            scroll.verticalScrollBar().setValue(0)
            if state['cards']:
                select(0, reveal=False)

        def run_search():
            debounce.stop()
            query = " ".join(query_input.text().split())
            state['short'] = 0 < len(query.replace(" ", "")) < self._SEARCH_MIN_CHARS
            if state['short']:
                query = ""
            state['query'] = query
            state['error'] = None
            state['hits'] = {kind: [] for kind in self._SEARCH_LIMITS}
            if query:
                try:
                    state['hits'] = self._search_hits(query)
                except Exception as e:  # noqa: BLE001
                    logger.error(f"Search dialog query failed: {e}")
                    state['error'] = str(e)
            refresh_chips()
            render()

        def run_now(query):
            query_input.blockSignals(True)
            query_input.setText(query)
            query_input.blockSignals(False)
            query_input.setFocus()
            run_search()

        def show_kind(kind):
            chips[kind].setChecked(True)
            on_kind_changed(chips[kind])

        def on_kind_changed(button):
            self._search_kind = button.property("kindKey")
            render()

        def open_hit(hit):
            self._remember_search(state['query'])
            dialog.accept()
            if hit['kind'] == "transcript":
                if hit['session_id'] is not None:
                    self._open_transcript_hit(hit['session_id'], hit['text'], state['query'])
            elif hit['kind'] == "summary":
                self._select_session_by_id(hit['session_id'])
                self._show_summary_by_session_id(hit['session_id'], hit['session_name'])
            else:
                self._load_conversation(hit['conv_id'])
                self._find_select_conversation(hit['conv_id'])

        def open_selected():
            if debounce.isActive():
                run_search()
            if 0 <= state['selected'] < len(state['cards']):
                open_hit(state['cards'][state['selected']][1])

        class _QueryKeys(QObject):
            """Arrow keys move the selection; Enter opens it."""

            def eventFilter(self_, obj, event):
                if event.type() != QEvent.KeyPress:
                    return False
                key = event.key()
                if key == Qt.Key_Down:
                    select(state['selected'] + 1)
                    return True
                if key == Qt.Key_Up:
                    select(state['selected'] - 1)
                    return True
                if key == Qt.Key_PageDown:
                    select(state['selected'] + 5)
                    return True
                if key == Qt.Key_PageUp:
                    select(state['selected'] - 5)
                    return True
                if key in (Qt.Key_Return, Qt.Key_Enter):
                    open_selected()
                    return True
                return False

        query_keys = _QueryKeys(dialog)
        query_input.installEventFilter(query_keys)

        def on_text_changed(text):
            if len("".join(text.split())) >= self._SEARCH_MIN_CHARS:
                debounce.start()
            else:
                run_search()

        query_input.textChanged.connect(on_text_changed)
        debounce.timeout.connect(run_search)
        kind_group.buttonClicked.connect(on_kind_changed)
        close_btn.clicked.connect(dialog.reject)

        def on_finished(_result):
            debounce.stop()
            self._search_window_size = dialog.size()

        dialog.finished.connect(on_finished)

        run_search()
        query_input.setFocus()
        dialog.exec()

    def _open_transcript_hit(self, session_id: int, hit_text: str, query: str):
        """Open a transcript search hit: load its session, then land the
        transcripts pane's find bar on the matching line.

        The find bar comes up pre-filled with the query, so ▲/▼ step through
        the session's other hits from there.
        """
        # A highlight left on a row that the reload is about to delete.
        self._close_find_bar()
        self._select_session_by_id(session_id)
        # Wait for the Search dialog to finish closing: focus returning to the
        # main window would otherwise move the active pane, and the find bar
        # closes whenever the active pane leaves it.
        QTimer.singleShot(80, lambda: self._focus_transcript_hit(hit_text, query))

    def _focus_transcript_hit(self, hit_text: str, query: str):
        if not self.right_shell.isVisible():
            self._on_status_update("Session opened - maximize the window to see the matching transcript line")
            return
        self._set_active_pane("right")
        self._open_find_bar()
        self._find_bar.set_query_text(query)
        # Stop following the live tail, or the next layout pass would pull
        # the stream back down to the newest line.
        self._transcription_autoscroll = False
        self._on_find_query_changed(query)

        needle = " ".join((hit_text or "").split()).lower()
        if not needle:
            return
        rows = self._layout_rows(self._transcription_layout)
        target = next(
            (i for i, row in enumerate(rows)
             if needle in " ".join((row.property('find_text') or "").split()).lower()),
            None,
        )
        if target in self._find.matches and self._find.current() != target:
            self._clear_find_highlight()
            self._find.cursor = self._find.matches.index(target)
            self._apply_find_current()
            self._find_bar.set_match_count(*self._find.match_label())

    def _on_detach_assistant(self):
        """Create a detached window for the assistant chat panel."""
        if self._detached_assistant_window:
            # Window already exists, just bring it to front
            self._detached_assistant_window.show()
            self._detached_assistant_window.activateWindow()
            self._detached_assistant_window.raise_()
            return
        
        # Create detached window with no parent (standalone window)
        # This ensures it doesn't minimize when main window minimizes
        self._detached_assistant_window = QDialog(None)  # No parent - standalone window
        self._detached_assistant_window.setWindowTitle('Assistant Chat')
        self._detached_assistant_window.resize(450, 600)
        
        # Set window flags: stay on top but not as modal
        self._detached_assistant_window.setWindowFlags(
            Qt.Window | 
            Qt.WindowStaysOnTopHint | 
            Qt.WindowCloseButtonHint | 
            Qt.WindowMinimizeButtonHint |
            Qt.FramelessWindowHint
        )
        
        # Prevent the detached window from activating the main window when minimized
        self._detached_assistant_window.setAttribute(Qt.WA_QuitOnClose, False)
        
        # Create main layout
        main_layout = QVBoxLayout(self._detached_assistant_window)
        
        # Title
        title_label = QLabel('Assistant Chat')
        title_font = title_label.font()
        title_font.setPointSize(16)
        title_font.setBold(True)
        title_label.setFont(title_font)
        main_layout.addWidget(title_label)
        
        # Agent selector
        agent_layout = QHBoxLayout()
        agent_label = QLabel("Agent:")
        agent_layout.addWidget(agent_label)
        
        # Create agent combo for detached window (copies main window's agents)
        self._detached_agent_combo = QComboBox()
        for i in range(self.agent_combo.count()):
            self._detached_agent_combo.addItem(
                self.agent_combo.itemText(i),
                self.agent_combo.itemData(i)
            )
        self._detached_agent_combo.setMinimumHeight(50)
        self._detached_agent_combo.setMinimumWidth(200)
        self._detached_agent_combo.setMaximumWidth(220)
        agent_layout.addWidget(self._detached_agent_combo)
        
        # Scope selector
        scope_label = QLabel("Scope:")
        agent_layout.addWidget(scope_label)
        
        self._detached_scope_combo = QComboBox()
        for i in range(self.scope_combo.count()):
            self._detached_scope_combo.addItem(
                self.scope_combo.itemText(i),
                self.scope_combo.itemData(i)
            )
        # Preserve the current selection from main window
        self._detached_scope_combo.setCurrentIndex(self.scope_combo.currentIndex())
        # Sync scope changes back to main window (guarded against signal recursion)
        self._detached_scope_combo.currentIndexChanged.connect(
            self._on_detached_scope_changed
        )
        agent_layout.addWidget(self._detached_scope_combo)
        
        agent_layout.addStretch()
        main_layout.addLayout(agent_layout)
        
        # Answer display (scrollable conversation on top)
        answer_label = QLabel("Answer:")
        main_layout.addWidget(answer_label)
        
        # Create scrollable conversation view for detached window
        self._detached_answer_scroll = QScrollArea()
        self._detached_answer_scroll.setWidgetResizable(True)
        self._detached_answer_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        
        # Container for conversation messages
        self._detached_answer_container = QWidget()
        self._detached_answer_layout = QVBoxLayout(self._detached_answer_container)
        self._detached_answer_layout.setSpacing(10)
        self._detached_answer_layout.setContentsMargins(5, 5, 5, 5)
        self._detached_answer_layout.addStretch()  # Push content to top
        
        self._detached_answer_scroll.setWidget(self._detached_answer_container)
        self._detached_answer_scroll.setMinimumHeight(300)
        self._detached_answer_scroll.setMaximumHeight(500)
        main_layout.addWidget(self._detached_answer_scroll)
        
        # Question input (on bottom)
        question_label = QLabel("Question:")
        main_layout.addWidget(question_label)
        
        self._detached_question_input = QTextEdit()
        self._detached_question_input.setPlaceholderText("Ask a question about your sessions...")
        self._detached_question_input.setMaximumHeight(80)
        main_layout.addWidget(self._detached_question_input)
        
        # Ask button
        button_layout = QHBoxLayout()
        
        self._detached_ask_button = QPushButton("Ask")
        self._detached_ask_button.clicked.connect(self._on_detached_ask_clicked)
        button_layout.addWidget(self._detached_ask_button)
        
        self._detached_new_chat_button = QPushButton("New Chat")
        self._detached_new_chat_button.clicked.connect(self._on_new_chat_clicked)
        button_layout.addWidget(self._detached_new_chat_button)
        
        button_layout.addStretch()
        main_layout.addLayout(button_layout)
        
        # Candidate session selection (initially hidden) - same as main window
        self._detached_candidate_group = QGroupBox("Select a Session:")
        candidate_layout = QVBoxLayout()
        
        self._detached_candidate_list = QListWidget()
        self._detached_candidate_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self._detached_candidate_list.setMaximumHeight(100)
        self._detached_candidate_list.itemClicked.connect(self._on_detached_candidate_selected)
        candidate_layout.addWidget(self._detached_candidate_list)
        
        self._detached_use_candidate_button = QPushButton("Use Selected Session")
        self._detached_use_candidate_button.clicked.connect(self._on_detached_use_candidate)
        self._detached_use_candidate_button.setEnabled(False)
        candidate_layout.addWidget(self._detached_use_candidate_button)
        
        self._detached_candidate_group.setLayout(candidate_layout)
        self._detached_candidate_group.setVisible(False)
        main_layout.addWidget(self._detached_candidate_group)
        
        # Copy existing conversation from main window to detached window
        self._copy_conversation_to_detached()
        
        # Connect main window's answer display to also update detached window
        # This keeps both windows in sync when main window gets an answer
        
        self._detached_assistant_window.show()
    
    def _copy_conversation_to_detached(self):
        """Copy all existing conversation messages from main window to detached window."""
        if not hasattr(self, '_answer_layout') or not self._answer_layout:
            return
        
        if not hasattr(self, '_detached_answer_layout') or not self._detached_answer_layout:
            return
        
        # Iterate through all message widgets in the main conversation (excluding stretch)
        for i in range(self._answer_layout.count() - 1):
            item = self._answer_layout.itemAt(i)
            if item and item.widget():
                original_frame = item.widget()
                
                # Get the role from the property we stored earlier
                role = original_frame.property('role')
                if not role:
                    continue
                
                # Get the text from the property we stored
                text = original_frame.property('message_text')
                if not text:
                    continue
                
                if text:
                    # Add to detached window
                    self._add_message_to_detached_conversation(role, text)
    
    def _on_detached_ask_clicked(self):
        """Handle the Ask button click in the detached assistant window."""
        # Get the question from the detached input
        question = self._detached_question_input.toPlainText().strip()
        
        if not question:
            self._add_message_to_conversation('assistant', "Please enter a question.")
            return
        
        # Clear the search bar after getting the question
        self._detached_question_input.clear()
        
        # Add user's question to conversation view
        self._add_message_to_conversation('user', question)

        # Clear previous candidates when asking a new question
        self._detached_candidate_group.setVisible(False)
        # Retire any live BU093 scope offer - at most one may be open (BU093)
        self._retire_pending_scope_prompt()

        # Store the original question for potential retry
        self._current_question = question
        
        # Get selected agent
        agent_id = self._detached_agent_combo.currentData()
        
        # Get scope from combo
        scope_value = self._detached_scope_combo.currentData()
        explicit_scope = "current_session" if scope_value == "current" else "any_session"
        
        # Get active session ID if there's an active session
        active_session = self.session_manager.get_active_session()
        active_session_id = active_session.id if active_session else None
        
        # Get selected session ID from the sessions table (if any session is selected)
        # Falls back to getting session from conversation if conversation is selected
        selected_session_id = self._get_selected_session_id()
        
        # If no session selected but we have a conversation, get session from conversation
        if selected_session_id is None and self._current_conversation_id is not None:
            conv = self.session_manager.db.get_conversation(self._current_conversation_id)
            if conv:
                selected_session_id = conv.get('session_id')
        
        # Determine explicit_scope based on scope combo
        # When "current", use selected session if available, otherwise active session
        # When "any", use any_session to search all sessions
        if scope_value == "current":
            # Use selected session (from UI) if available, otherwise active session
            if selected_session_id is not None:
                explicit_scope = "current_session"
                active_session_id = selected_session_id  # Override: use selected session
            elif active_session_id is not None:
                explicit_scope = "current_session"
                # active_session_id already has the active session
            else:
                explicit_scope = "current_session"  # Will cause clarification
        else:
            explicit_scope = "any_session"
        
        # Record which scope produced the upcoming answer (for the bubble marker)
        self._last_scope_marker = self._scope_marker_text(
            explicit_scope, selected_session_id or active_session_id
        )

        # Disable the Ask button while processing
        self._detached_ask_button.setEnabled(False)
        self._add_message_to_conversation('assistant', "Thinking...")
        
        # Run the assistant service call in the background
        QTimer.singleShot(50, lambda: self._run_detached_assistant_query(
            question=question,
            agent_id=agent_id,
            explicit_scope=explicit_scope,
            active_session_id=active_session_id,
            selected_session_id=selected_session_id,
            conversation_id=self._current_conversation_id
        ))
    
    def _run_detached_assistant_query(
        self,
        question: str,
        agent_id: str,
        explicit_scope: str,
        active_session_id: Optional[int],
        selected_session_id: Optional[int],
        conversation_id: Optional[int]
    ):
        """Execute the assistant query in the background for detached window."""
        try:
            # Call the assistant service
            response = self.assistant_service.ask(
                question=question,
                agent_id=agent_id,
                explicit_scope=explicit_scope,
                active_session_id=active_session_id,
                selected_session_id=selected_session_id,
                conversation_id=conversation_id
            )
            
            # Handle the response
            if response.success:
                # Clear candidates on successful answer
                self._detached_candidate_group.setVisible(False)
                answer_text = self._drop_repeat_screenshot_pointer(
                    response.answer or "", response.screenshot_refs
                )
                # Add assistant's response to conversation view
                self._add_message_to_conversation('assistant', answer_text + self._scope_suffix())
                if response.screenshot_refs:
                    self._add_screenshot_refs_to_conversation(response.screenshot_refs)
                if response.scope_offer is not None:
                    self._add_scope_offer_to_conversation(
                        response.scope_offer, response.candidate_sessions
                    )
                # Save conversation_id for follow-up questions
                if response.conversation_id:
                    self._current_conversation_id = response.conversation_id
                    logger.info(f"New conversation created with ID (detached): {response.conversation_id}")
                    # Refresh the conversations list to show the new conversation (use QTimer to ensure it runs in main thread)
                    QTimer.singleShot(0, self._load_past_conversations)
                self._on_status_update("Answer received")
            elif response.needs_clarification:
                # Show clarification question and candidates
                clarification_text = response.clarification_question or ""
                self._add_message_to_conversation('assistant', clarification_text)
                
                # Display candidates in detached window
                if response.candidates:
                    self._display_detached_candidates(response.candidates)
                else:
                    self._detached_candidate_group.setVisible(False)
                    
                self._on_status_update("Clarification needed")
            else:
                # Show error
                error_text = response.error or "Unknown error occurred"
                self._add_message_to_conversation('assistant', f"Error: {error_text}")
                self._on_status_update(f"Assistant error: {error_text}", is_error=True)
                # Clear candidates on error
                self._detached_candidate_group.setVisible(False)
                
        except Exception as e:
            logger.error(f"Assistant query failed (detached): {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            self._add_message_to_conversation('assistant', f"Error: {str(e)}")
            self._on_status_update(f"Assistant error: {str(e)}", is_error=True)
            # Clear candidates on exception
            self._detached_candidate_group.setVisible(False)
        finally:
            # Re-enable the Ask button
            self._detached_ask_button.setEnabled(True)
    
    def _display_detached_candidates(self, candidates: list, handoff: bool = False):
        """Display candidate sessions in the detached window for selection."""
        self._detached_candidate_group.setTitle(
            _CANDIDATE_TITLE_HANDOFF.title() + ":" if handoff else "Select a Session:"
        )
        self._detached_use_candidate_button.setText(
            _CANDIDATE_BTN_HANDOFF if handoff else _CANDIDATE_BTN_DEFAULT
        )
        self._detached_candidate_list.clear()
        
        for candidate in candidates:
            # Format: "Session name - YYYY-MM-DD HH:MM"
            session_name = candidate.get('session_name', 'Unknown Session')
            start_time = candidate.get('start_time', '')
            display_text = f"{session_name} - {start_time}"
            self._detached_candidate_list.addItem(display_text)
        
        # Store candidates for later use
        self._current_candidates = candidates
        
        # Show the candidate group
        self._detached_candidate_group.setVisible(True)
    
    def _on_detached_candidate_selected(self, item):
        """Handle candidate selection in the detached window."""
        self._detached_use_candidate_button.setEnabled(True)
    
    def _on_detached_use_candidate(self):
        """Handle the Use Selected Session button in the detached window."""
        selected_index = self._detached_candidate_list.currentRow()
        
        if selected_index >= 0 and selected_index < len(self._current_candidates):
            selected_candidate = self._current_candidates[selected_index]
            selected_session_id = selected_candidate.get('session_id')
            
            # Retry the question with the selected session
            if selected_session_id and self._current_question:
                # Disable button during processing
                self._detached_use_candidate_button.setEnabled(False)
                self._add_message_to_conversation('assistant', "Thinking...")
                
                agent_id = self._detached_agent_combo.currentData()

                # The picker always re-asks in Specific Session against the
                # chosen session - both for the ambiguity flow and the BU090
                # Any Session -> detail handoff.
                QTimer.singleShot(50, lambda: self._run_detached_assistant_query(
                    question=self._current_question,
                    agent_id=agent_id,
                    explicit_scope="current_session",
                    active_session_id=None,  # Explicit session selected
                    selected_session_id=selected_session_id,
                    conversation_id=self._current_conversation_id
                ))
    
    def closeEvent(self, event):
        """Handle window close event."""
        # Wait for any active assistant thread to finish
        if self._assistant_thread is not None and self._assistant_thread.isRunning():
            self._assistant_thread.wait()

        # Wait for a running RAG backfill (BU091); it is resumable regardless.
        _backfill = getattr(self, "_rag_backfill_thread", None)
        if _backfill is not None and _backfill.isRunning():
            _backfill.wait()

        # Check if there's an active session
        if self.session_manager and self.session_manager.get_active_session():
            reply = QMessageBox.question(
                self,
                'Active Session',
                'There is an active session. Stop and close?',
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )
            
            if reply == QMessageBox.Yes:
                try:
                    # finalize=False: stop capture and flag the session for
                    # finalization, but don't transcribe/index/summarize here
                    # - that used to re-transcribe every audio file on close
                    # (loading a second copy of the model) and could block
                    # quitting for minutes. finalize_pending_sessions() picks
                    # this session up in the background on the next start.
                    self.session_manager.stop_session(finalize=False)
                except Exception:
                    pass  # Ignore errors during shutdown
                event.accept()
            else:
                event.ignore()
                return

        # Closing for real: give the capture hotkey back to the system (BU110).
        self._capture_hotkey.unregister()

        # Clean up session manager
        if self.session_manager:
            try:
                self.session_manager.close()
            except Exception:
                pass
        
        event.accept()
