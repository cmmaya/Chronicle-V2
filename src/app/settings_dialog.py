"""Settings pop-up window (BU128).

Replaces the hidden ``QMenuBar`` the sidebar Settings button used to toggle.
Every setting lives on one of the pages in the left rail and applies as soon
as it changes - there is no OK/Cancel.

The checkable ``QAction``s on ``MainWindow`` stay the source of truth for the
three on/off settings (other code reads ``action.isChecked()``); the dialog
only mirrors them, and flips them with ``action.trigger()`` so the window's
existing handlers persist the change exactly as the menu used to.
"""
from __future__ import annotations

from PySide6.QtCore import (
    QEasingCurve, QParallelAnimationGroup, QPoint, QPropertyAnimation, QRectF,
    Qt, QThread, QTimer, QVariantAnimation, Signal,
)
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup, QComboBox, QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel,
    QLineEdit, QMessageBox, QPushButton, QScrollArea, QSlider, QStackedWidget,
    QVBoxLayout, QWidget,
)

from .. import paths
from ..config import ALLOWED_MODELS, APP_VERSION, get_selected_model
from . import theme
from .pixel_theme import asset_path
from .pixel_widgets import (
    PANEL_BORDER_INNER, NAVY, PixelButton, PixelPanel, PixelSectionTitle,
    _label_qss, pixel_round_rect_path, set_accent,
)

MUTED = theme.hex("#8EA7D8")
GOLD = theme.hex("#FFE9A8")
CREAM = theme.hex("#FFF0BF")
FOCUS = theme.hex("#6FD3FF")

# Debounce for the VAD sliders, so dragging one posts a single status line.
VAD_APPLY_DELAY_MS = 300


class ApiKeyCheckThread(QThread):
    """Runs secrets.check_api_key off the UI thread (BU122)."""

    result_signal = Signal(bool, str)  # (ok, message)

    def __init__(self, key: str):
        super().__init__()
        self._key = key

    def run(self):
        from .. import secrets
        try:
            ok, message = secrets.check_api_key(self._key)
        except Exception as e:  # noqa: BLE001
            ok, message = False, f"Could not test the key: {secrets.redact(e, self._key)}"
        self.result_signal.emit(ok, message)


class GoogleCallThread(QThread):
    """Runs a Google auth call (sign-in, disconnect) off the UI thread (BU131)."""

    result_signal = Signal(bool, str)  # (ok, result or error message)

    def __init__(self, call):
        super().__init__()
        self._call = call

    def run(self):
        from ..calendar_sync.google_auth import GoogleAuthError
        try:
            ok, text = True, str(self._call() or "")
        except GoogleAuthError as e:
            ok, text = False, str(e)
        except Exception as e:  # noqa: BLE001 - network, keyring, ...
            ok, text = False, f"Something went wrong ({type(e).__name__}). Try again."
        self.result_signal.emit(ok, text)


# =========================
# Controls
# =========================

class PixelToggle(QWidget):
    """Chunky on/off switch: navy track and blue knob when off, gold track
    and navy knob when on. Click, Space or Enter flips it."""

    toggled = Signal(bool)

    W, H, KNOB = 60, 28, 18
    if not theme.is_pixel():
        W, H, KNOB = 40, 22, 16  # BU159: a slim pill switch

    def __init__(self, checked: bool = False, parent=None):
        super().__init__(parent)
        self._checked = bool(checked)
        self._pos = 1.0 if self._checked else 0.0  # knob travel, 0..1
        self.setFixedSize(self.W, self.H)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(120)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._on_anim)

    def isChecked(self) -> bool:
        return self._checked

    def setChecked(self, checked: bool):
        """Set the state without emitting ``toggled`` (mirrors external state)."""
        checked = bool(checked)
        if checked == self._checked:
            return
        self._checked = checked
        self._anim.stop()
        self._pos = 1.0 if checked else 0.0
        self.update()

    def toggle(self):
        self._checked = not self._checked
        self._anim.stop()
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(1.0 if self._checked else 0.0)
        self._anim.start()
        self.toggled.emit(self._checked)

    def _on_anim(self, value):
        self._pos = float(value)
        self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self.rect().contains(event.position().toPoint()):
            self.toggle()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Space, Qt.Key_Return, Qt.Key_Enter):
            self.toggle()
            event.accept()
            return
        super().keyPressEvent(event)

    def _paint_pill(self, p):
        """Boring Corporate: white track and black knob when on, grey track
        and light knob when off; no ON/OFF text."""
        on = self._checked
        track = QColor("#F9F9F9") if on else QColor("#3A3A3A")
        knob = QColor("#0D0D0D") if on else QColor("#D4D4D4")
        p.setPen(QPen(QColor("#8F8F8F"), 1) if self.hasFocus() else Qt.NoPen)
        p.setBrush(track)
        p.drawPath(theme.rounded_rect_path(0, 0, self.W - 1, self.H - 1, self.H / 2))
        inset = (self.H - self.KNOB) / 2
        x = inset + (self.W - self.KNOB - 2 * inset) * self._pos
        p.setPen(Qt.NoPen)
        p.setBrush(knob)
        p.drawEllipse(QRectF(x, inset, self.KNOB, self.KNOB))

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, theme.antialias())
        if not theme.is_pixel():
            self._paint_pill(p)
            p.end()
            return
        on = self._checked
        track = QColor(GOLD) if on else theme.qcolor("#071D52")
        border = QColor(FOCUS) if self.hasFocus() else theme.qcolor("#FFEFC1" if on else "#3A67C7")
        knob = theme.qcolor("#071846") if on else QColor(MUTED)

        p.setPen(QPen(border, theme.pen_width(2)))
        p.setBrush(track)
        p.drawPath(pixel_round_rect_path(1, 1, self.W - 3, self.H - 3, 5))

        # Label on the side the knob is not covering.
        font = QFont("Courier New")
        font.setPixelSize(10)
        font.setBold(True)
        p.setFont(font)
        p.setPen(theme.qcolor("#071846") if on else QColor(MUTED))
        text_rect = QRectF(6, 0, self.W - 12 - self.KNOB, self.H) if on \
            else QRectF(8 + self.KNOB, 0, self.W - 14 - self.KNOB, self.H)
        p.drawText(text_rect, Qt.AlignCenter, "ON" if on else "OFF")

        travel = self.W - self.KNOB - 10
        x = 5 + round(travel * self._pos)
        y = (self.H - self.KNOB) // 2
        p.setPen(Qt.NoPen)
        p.setBrush(knob)
        p.drawPath(pixel_round_rect_path(x, y, self.KNOB, self.KNOB, 3))
        # A one-pixel highlight on the knob keeps it reading as a raised block.
        p.fillRect(x + 3, y + 3, self.KNOB - 6, 2, QColor(255, 255, 255, 60))
        p.end()


_SLIDER_QSS = f"""
QSlider {{ background: transparent; min-height: 26px; }}
QSlider::groove:horizontal {{
    height: 8px; background: #071D52; border: 2px solid #3A67C7;
}}
QSlider::sub-page:horizontal {{
    background: {GOLD}; border: 2px solid #3A67C7;
}}
QSlider::handle:horizontal {{
    background: #F6E0A6; border: 2px solid #071846;
    width: 14px; margin: -7px 0px;
}}
QSlider::handle:horizontal:hover {{ background: #FFFFFF; }}
"""
if not theme.is_pixel():
    _SLIDER_QSS = theme.NATIVE_QSS + """
    QSlider { background: transparent; min-height: 26px; }
    QSlider::groove:horizontal { height: 4px; background: #3A3A3A; border: none; border-radius: 2px; }
    QSlider::sub-page:horizontal { background: #ECECEC; border: none; border-radius: 2px; }
    QSlider::handle:horizontal {
        background: #FAFAFA; border: none; width: 14px; height: 14px;
        margin: -5px 0px; border-radius: 7px;
    }
    QSlider::handle:horizontal:hover { background: #FFFFFE; }
    """

_NAV_QSS = f"""
QPushButton#SettingsNav {{
    color: #C9D6F5; background: transparent;
    border: 2px solid transparent; border-radius: 7px;
    padding: 8px 10px; text-align: left;
    font-family: 'Courier New'; font-size: 11pt; font-weight: 700;
}}
QPushButton#SettingsNav:hover {{ background: #10306F; color: {CREAM}; }}
QPushButton#SettingsNav:checked {{
    background: #F6E0A6; color: #071846; border: 2px solid #FFEFC1;
}}
"""
if not theme.is_pixel():
    _NAV_QSS = theme.NATIVE_QSS + """
    QPushButton#SettingsNav {
        color: #B4B4B4; background: transparent; border: none; border-radius: 8px;
        padding: 8px 10px; text-align: left; font-size: 14px; font-weight: 400;
    }
    QPushButton#SettingsNav:hover { background: #1A1A1A; color: #ECECEC; }
    QPushButton#SettingsNav:checked { background: #212121; color: #ECECEC; }
    """


def _label(text: str, color: str = CREAM, pt: float = 11, bold: bool = False,
           wrap: bool = False) -> QLabel:
    label = QLabel(text)
    label.setStyleSheet(_label_qss(color, pt, bold=bold))
    label.setWordWrap(wrap)
    return label


def _centered_button(text: str, min_width: int) -> PixelButton:
    button = PixelButton(text)
    button.setMinimumWidth(min_width)
    if theme.is_pixel():  # Boring Corporate buttons are centred already
        button.setStyleSheet(button.styleSheet() + "QPushButton#PixelButton { text-align: center; }")
    return button


def _value_chip(text: str) -> QLabel:
    chip = QLabel(text)
    chip.setAlignment(Qt.AlignCenter)
    chip.setMinimumWidth(70)
    chip.setStyleSheet(
        "QLabel { color: #071846; background: #F6E0A6; border: 2px solid #FFEFC1;"
        " border-radius: 5px; padding: 2px 8px; font-family: 'Courier New';"
        " font-size: 10pt; font-weight: 700; }"
    )
    return chip


class _SettingCard(PixelPanel):
    """One row: title + muted description on the left, a control on the right.
    Anything placed under the row goes in ``body``."""

    def __init__(self, title: str, description: str = "", control: QWidget = None):
        super().__init__(inner=True)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 12, 16, 12)
        outer.setSpacing(8)

        row = QHBoxLayout()
        row.setSpacing(14)
        text = QVBoxLayout()
        text.setSpacing(3)
        text.addWidget(_label(title, CREAM, 11.5, bold=True))
        if description:
            text.addWidget(_label(description, MUTED, 9.5, wrap=True))
        row.addLayout(text, 1)
        if control is not None:
            row.addWidget(control, 0, Qt.AlignVCenter | Qt.AlignRight)
        outer.addLayout(row)

        self.body = QVBoxLayout()
        self.body.setSpacing(6)
        outer.addLayout(self.body)


class _ThemePreview(QWidget):
    """A thumbnail of the main window painted in one theme's colours."""

    def __init__(self, name: str, parent=None):
        super().__init__(parent)
        self._name = name
        self.setFixedHeight(92)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        mapping = theme.REMAP[name]
        self._c = {key: QColor(mapping.get(value, value)) for key, value in {
            "backdrop": "#0E2A6B", "panel": "#061946", "border": "#254D9C",
            "button": "#274F9B", "button_border": "#3A67C7", "cream": "#F6E0A6",
            "bubble": "#294F9D", "bubble_border": "#3765BD", "gold": "#FFE9A8",
        }.items()}
        self._c["brand"] = QColor(theme.ROLES[name]["brand"])
        self._c["primary"] = QColor("#6E0258") if name == theme.SYNTHWAVE else self._c["button"]
        self._background = None
        image = theme._BACKGROUNDS.get(name)
        if image:
            pixmap = QPixmap(str(paths.asset_path("themes", image)))
            if not pixmap.isNull():
                self._background = pixmap

    def _paint_corporate(self, p):
        """Boring Corporate thumbnail (BU159): black canvas, a flush sidebar
        behind a hairline, a centred heading over a grey input pill, line
        rows for the sidebar and rounded grey bubbles for the transcripts."""
        from PySide6.QtGui import QPainterPath

        def pill(x, y, w, h, r, fill, pen=None):
            path = QPainterPath()
            path.addRoundedRect(QRectF(x, y, w, h), r, r)
            p.setPen(pen or Qt.NoPen)
            p.setBrush(fill)
            p.drawPath(path)

        p.setRenderHint(QPainter.Antialiasing, True)
        w, h = self.width(), self.height()
        p.fillRect(0, 0, w, h, QColor("#000000"))
        side = right = max(34, w // 5)
        hairline = QColor("#262626")
        p.fillRect(side, 0, 1, h, hairline)
        p.fillRect(w - right, 0, 1, h, hairline)
        # Sidebar: brand, three nav rows (the first hovered).
        pill(8, 9, side - 26, 4, 2, QColor("#ECECEC"))
        pill(6, 19, side - 12, 10, 3, QColor("#212121"))
        for i in range(3):
            pill(10, 22 + i * 13, 5, 4, 1, QColor("#B4B4B4"))
            pill(18, 22 + i * 13, side - 32, 4, 2, QColor("#8F8F8F"))
        # Centre: heading and input pill.
        mid_x, mid_w = side + 1, w - side - right - 1
        pill(mid_x + mid_w * 0.3, h * 0.36, mid_w * 0.4, 5, 2.5, QColor("#ECECEC"))
        pill(mid_x + 12, h * 0.5, mid_w - 24, 13, 6.5, QColor("#262626"),
             QPen(QColor("#333333"), 1))
        pill(mid_x + mid_w - 24, h * 0.5 + 2.5, 8, 8, 4, QColor("#F9F9F9"))
        # Transcripts: two grey bubbles.
        rx = w - right + 1
        pill(rx + 6, 12, right - 22, 3, 1.5, QColor("#8F8F8F"))
        pill(rx + 6, 22, right - 14, 24, 6, QColor("#1A1A1A"))
        pill(rx + 6, 52, right - 14, 18, 6, QColor("#262626"))

    def paintEvent(self, event):
        c = self._c
        p = QPainter(self)
        if self._name == theme.BORING_CORPORATE:
            self._paint_corporate(p)
            p.end()
            return
        p.setRenderHint(QPainter.Antialiasing, theme.antialias())
        w, h = self.width(), self.height()
        p.fillRect(0, 0, w, h, c["backdrop"])

        side = right = max(34, w // 5)
        mid_x, mid_w = side + 8, w - side - right - 16
        for x, pw in ((4, side - 1), (mid_x, mid_w), (w - right - 4, right - 1)):
            p.setPen(QPen(c["border"], 1))
            p.setBrush(c["panel"])
            p.drawPath(pixel_round_rect_path(x, 4, pw, h - 9, 3))

        if self._background is not None:
            scaled = self._background.scaledToWidth(mid_w, Qt.SmoothTransformation)
            p.save()
            p.setClipPath(pixel_round_rect_path(mid_x + 1, 5, mid_w - 2, h - 11, 3))
            p.fillRect(mid_x, 4, mid_w, h - 9,
                       scaled.toImage().pixelColor(scaled.width() // 2, 0))
            p.drawPixmap(mid_x, h - 5 - scaled.height(), scaled)
            p.restore()
            p.setPen(QPen(c["border"], 1))
            p.setBrush(Qt.NoBrush)
            p.drawPath(pixel_round_rect_path(mid_x, 4, mid_w, h - 9, 3))

        # Sidebar: brand, the New Chat button and two plain ones.
        p.fillRect(10, 10, side - 22, 4, c["brand"])
        for i, fill in enumerate((c["primary"], c["button"], c["button"])):
            p.setPen(QPen(c["button_border"] if i else c["border"], 1))
            p.setBrush(fill)
            p.drawRect(9, 20 + i * 13, side - 12, 9)
        # Centre: search bar and chat input.
        p.setPen(QPen(c["border"], 1))
        p.setBrush(c["cream"])
        p.drawRect(mid_x + 12, 12, mid_w - 24, 9)
        p.drawRect(mid_x + 12, h - 22, mid_w - 24, 10)
        # Transcripts: title and two bubbles.
        rx = w - right
        p.fillRect(rx + 6, 10, right - 16, 3, c["gold"])
        p.setPen(QPen(c["bubble_border"], 1))
        p.setBrush(c["bubble"])
        p.drawRect(rx + 4, 20, right - 14, 22)
        p.setPen(Qt.NoPen)
        p.setBrush(c["cream"])
        p.drawRect(rx + 8, 48, right - 16, 18)
        p.end()


class _ThemeOption(QWidget):
    """One selectable theme: preview, name, description and its state."""

    clicked = Signal(str)

    def __init__(self, name: str, parent=None):
        super().__init__(parent)
        self.name = name
        self._selected = False
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        # Three themes share one row of the page (BU159).
        self.setMinimumWidth(140)
        label, description = theme.THEMES[name]
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(6)
        layout.addWidget(_ThemePreview(name))
        self.title = _label(label, CREAM, 11.5, bold=True, wrap=True)
        self.description = _label(description, MUTED, 9, wrap=True)
        self.state = _label("", GOLD, 9, bold=True)
        for child in (self.title, self.description, self.state):
            child.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            layout.addWidget(child)

    def is_selected(self) -> bool:
        return self._selected

    def set_state(self, selected: bool, text: str):
        self._selected = bool(selected)
        self.state.setText(text)
        self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit(self.name)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Space, Qt.Key_Return, Qt.Key_Enter):
            self.clicked.emit(self.name)
            event.accept()
            return
        super().keyPressEvent(event)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, theme.antialias())
        if self._selected:
            border, width = QColor(GOLD), 3
        elif self.hasFocus():
            border, width = QColor(FOCUS), 2
        else:
            border, width = theme.qcolor("#3A67C7"), 2
        p.setPen(QPen(border, theme.pen_width(width)))
        p.setBrush(NAVY)
        p.drawPath(pixel_round_rect_path(1, 1, self.width() - 3, self.height() - 3, 7))
        p.end()


class _Scrim(QWidget):
    """Navy veil over the main window while the pop-up is open."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setGeometry(parent.rect())

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(3, 10, 32, 150) if theme.is_pixel() else QColor(0, 0, 0, 170))
        p.end()


# =========================
# Dialog
# =========================

class SettingsDialog(QDialog):
    """The Settings pop-up. ``window`` is the ``MainWindow`` whose state it edits."""

    PAGES = ("General", "Appearance", "Audio", "Model", "API Key", "Calendar",
             "Maintenance", "About")

    def __init__(self, window):
        super().__init__(window)
        self._window = window
        self._scrim = None
        self._pop = None
        self.setObjectName("SettingsDialog")
        self.setWindowTitle("Settings")
        self.setModal(True)
        self.resize(780, 560)
        self.setMinimumSize(640, 460)

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 14)
        root.setSpacing(12)

        body = QHBoxLayout()
        body.setSpacing(12)
        root.addLayout(body, 1)

        # Left rail: a game-menu list with a ▶ cursor on the open page.
        rail = PixelPanel(inner=True)
        rail.setFixedWidth(200)
        rail_layout = QVBoxLayout(rail)
        rail_layout.setContentsMargins(10, 14, 10, 12)
        rail_layout.setSpacing(4)
        if not theme.is_pixel():
            rail.set_flat(True)  # BU159: the nav sits flush, like the sidebar
            rail_layout.setContentsMargins(4, 8, 4, 4)
            rail_layout.setSpacing(2)
        rail_layout.addWidget(PixelSectionTitle("SETTINGS"))
        rail_layout.addSpacing(8)

        self.stack = QStackedWidget()
        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)
        self.nav_buttons = []
        builders = (self._build_general, self._build_appearance, self._build_audio,
                    self._build_model,
                    self._build_api_key, self._build_calendar,
                    self._build_maintenance, self._build_about)
        for index, (name, build) in enumerate(zip(self.PAGES, builders)):
            button = QPushButton()
            button.setObjectName("SettingsNav")
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            button.setFocusPolicy(Qt.NoFocus)  # arrow keys / Ctrl+Tab would fight the pages' fields
            button.setStyleSheet(_NAV_QSS)
            button.setText("  " + name if theme.is_pixel() else name)
            button.setProperty("pageName", name)
            self.nav_group.addButton(button, index)
            self.nav_buttons.append(button)
            rail_layout.addWidget(button)
            self.stack.addWidget(self._page(name, build))
        rail_layout.addStretch(1)
        rail_layout.addWidget(_label("Esc to close", MUTED, 8.5))
        self.nav_group.idToggled.connect(self._on_nav_toggled)
        body.addWidget(rail)
        if not theme.is_pixel():
            divider = QFrame()
            divider.setFixedWidth(1)
            divider.setStyleSheet(theme.NATIVE_QSS + "QFrame { background: #262626; border: none; }")
            body.addWidget(divider)
        body.addWidget(self.stack, 1)

        # Footer.
        footer = QHBoxLayout()
        footer.setSpacing(10)
        dot = QLabel()
        dot.setFixedSize(8, 8)
        dot.setStyleSheet(f"QLabel {{ background: {GOLD}; border: none; }}")
        dot.setVisible(theme.is_pixel())
        footer.addWidget(dot, 0, Qt.AlignVCenter)
        footer.addWidget(_label("Changes save automatically", MUTED, 9.5), 0, Qt.AlignVCenter)
        footer.addStretch(1)
        self.done_button = _centered_button("Done", 120)
        if not theme.is_pixel():
            set_accent(self.done_button, "primary")
        self.done_button.clicked.connect(self.accept)
        footer.addWidget(self.done_button)
        root.addLayout(footer)

        self._vad_timer = QTimer(self)
        self._vad_timer.setSingleShot(True)
        self._vad_timer.setInterval(VAD_APPLY_DELAY_MS)
        self._vad_timer.timeout.connect(self._apply_vad)

        self.select_page("General")

    # -- structure ------------------------------------------------------------

    def _page(self, name: str, build) -> QWidget:
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(6, 4, 10, 4)
        layout.setSpacing(10)
        layout.addWidget(_label(name, CREAM, 17, bold=True))
        subtitle = _label("", MUTED, 9.5, wrap=True)
        layout.addWidget(subtitle)
        rule = QFrame()
        rule.setFixedHeight(2 if theme.is_pixel() else 1)
        rule.setStyleSheet(f"QFrame {{ background: {PANEL_BORDER_INNER.name()}; border: none; }}")
        layout.addWidget(rule)
        subtitle.setText(build(layout))
        layout.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(content)
        return scroll

    def select_page(self, name: str):
        self.nav_buttons[self.PAGES.index(name)].setChecked(True)

    def current_page(self) -> str:
        return self.PAGES[self.stack.currentIndex()]

    def _on_nav_toggled(self, index: int, checked: bool):
        button = self.nav_buttons[index]
        name = button.property("pageName")
        if theme.is_pixel():
            button.setText(("▶ " if checked else "  ") + name)
        else:
            button.setText(name)
        if checked:
            self.stack.setCurrentIndex(index)

    # -- pages ----------------------------------------------------------------

    def _action_toggle(self, action) -> PixelToggle:
        toggle = PixelToggle(action.isChecked())
        toggle.toggled.connect(lambda value, a=action: self._set_action(a, value))
        return toggle

    @staticmethod
    def _set_action(action, value: bool):
        # trigger() flips the check state and fires the window's handler,
        # which persists the preference just as the menu item did.
        if action.isChecked() != value:
            action.trigger()

    def _build_general(self, layout) -> str:
        w = self._window
        self.live_toggle = self._action_toggle(w.live_transcription_action)
        self.summary_toggle = self._action_toggle(w.auto_summary_action)
        self.logs_toggle = self._action_toggle(w.show_app_logs_action)
        layout.addWidget(_SettingCard(
            "Live transcription",
            "Transcribe audio chunks while the session is recording.",
            self.live_toggle))
        layout.addWidget(_SettingCard(
            "Auto-generate summary",
            "Write a summary as soon as a session stops.",
            self.summary_toggle))
        layout.addWidget(_SettingCard(
            "Show log messages",
            "Keep the one-line app log under the chat input.",
            self.logs_toggle))
        return "Recording and interface behaviour."

    def _build_appearance(self, layout) -> str:
        card = _SettingCard(
            "Theme",
            "Colours of the whole app. A new theme takes effect when Chronicle "
            "restarts.")
        row = QHBoxLayout()
        row.setSpacing(10)
        self.theme_options = {}
        for name in theme.THEMES:
            option = _ThemeOption(name)
            option.clicked.connect(self._on_theme_clicked)
            self.theme_options[name] = option
            row.addWidget(option, 1)
        card.body.addLayout(row)

        restart_row = QHBoxLayout()
        restart_row.setSpacing(10)
        self.theme_restart_label = _label(
            "Restart Chronicle to switch themes.", GOLD, 9.5, wrap=True)
        restart_row.addWidget(self.theme_restart_label, 1)
        self.theme_restart_button = _centered_button("Restart now", 140)
        self.theme_restart_button.clicked.connect(self._on_theme_restart)
        restart_row.addWidget(self.theme_restart_button)
        card.body.addLayout(restart_row)
        layout.addWidget(card)
        self._refresh_theme()
        return "How Chronicle looks."

    def _refresh_theme(self):
        saved = self._window.saved_theme()
        running = theme.active()
        for name, option in self.theme_options.items():
            if name == running and name == saved:
                text = "IN USE"
            elif name == running:
                text = "IN USE UNTIL RESTART"
            elif name == saved:
                text = "APPLIES AFTER RESTART"
            else:
                text = ""
            if not theme.is_pixel():
                text = text.capitalize()
            option.set_state(name == saved, text)
        pending = saved != running
        self.theme_restart_label.setVisible(pending)
        self.theme_restart_button.setVisible(pending)

    def _on_theme_clicked(self, name: str):
        if name != self._window.saved_theme():
            self._window._save_theme(name)
        self._refresh_theme()

    def _on_theme_restart(self):
        # Close the pop-up first: the restart may still ask about an active
        # session, and that question must not sit behind a modal dialog.
        window = self._window
        self.accept()
        QTimer.singleShot(0, window._restart_app)

    def _build_audio(self, layout) -> str:
        w = self._window
        self.threshold_slider = QSlider(Qt.Horizontal)
        self.threshold_slider.setRange(5, 100)
        self.threshold_slider.setValue(w._vad_threshold)
        self.threshold_slider.setStyleSheet(_SLIDER_QSS)
        self.threshold_chip = _value_chip(f"{w._vad_threshold}%")
        card = _SettingCard(
            "Speech threshold",
            "Share of audio frames that must contain speech for a chunk to be "
            "kept. Lower values catch shorter utterances.",
            self.threshold_chip)
        card.body.addWidget(self.threshold_slider)
        card.body.addLayout(self._scale("5% sensitive", "100% strict"))
        layout.addWidget(card)

        self.agg_slider = QSlider(Qt.Horizontal)
        self.agg_slider.setRange(0, 3)
        self.agg_slider.setPageStep(1)
        self.agg_slider.setValue(w._vad_aggressiveness)
        self.agg_slider.setStyleSheet(_SLIDER_QSS)
        self.agg_chip = _value_chip(f"Mode {w._vad_aggressiveness}")
        card = _SettingCard(
            "Aggressiveness",
            "How hard the voice detector filters noise. Use higher modes in "
            "noisy rooms.",
            self.agg_chip)
        card.body.addWidget(self.agg_slider)
        card.body.addLayout(self._scale("0 least filtering", "3 most"))
        layout.addWidget(card)

        self.threshold_slider.valueChanged.connect(self._on_vad_changed)
        self.agg_slider.valueChanged.connect(self._on_vad_changed)
        return "Voice activity detection. Applies to the next recorded chunk."

    @staticmethod
    def _scale(left: str, right: str) -> QHBoxLayout:
        row = QHBoxLayout()
        row.addWidget(_label(left, MUTED, 8.5))
        row.addStretch(1)
        row.addWidget(_label(right, MUTED, 8.5))
        return row

    def _on_vad_changed(self, _value=None):
        self.threshold_chip.setText(f"{self.threshold_slider.value()}%")
        self.agg_chip.setText(f"Mode {self.agg_slider.value()}")
        self._vad_timer.start()

    def _apply_vad(self):
        self._vad_timer.stop()
        threshold, agg = self.threshold_slider.value(), self.agg_slider.value()
        w = self._window
        if (threshold, agg) != (w._vad_threshold, w._vad_aggressiveness):
            w._apply_vad_settings(threshold, agg)

    def _build_model(self, layout) -> str:
        self.model_combo = QComboBox()
        self.model_combo.setMinimumWidth(280)
        chevron = asset_path("icon_chevron_down_dark.svg").replace("\\", "/")
        self.model_combo.setStyleSheet(
            f"QComboBox::down-arrow {{ image: url({chevron}); width: 12px; height: 8px;"
            " border: none; margin-right: 10px; }")
        for model_id in ALLOWED_MODELS:
            self.model_combo.addItem(model_id, model_id)
        index = self.model_combo.findData(get_selected_model())
        if index >= 0:
            self.model_combo.setCurrentIndex(index)
        self.model_combo.currentIndexChanged.connect(self._on_model_changed)
        card = _SettingCard(
            "Language model",
            "Used for summaries and the assistant. Kept across restarts.")
        card.body.addWidget(self.model_combo)
        layout.addWidget(card)
        return "Which OpenRouter model Chronicle talks to."

    def _on_model_changed(self, _index):
        model_id = self.model_combo.currentData()
        if model_id:
            self._window._apply_selected_model(model_id)

    def _build_api_key(self, layout) -> str:
        from .. import secrets

        card = _SettingCard(
            "OpenRouter API key",
            "Stored in the Windows Credential Manager. The saved key is never "
            "shown - only its last 4 characters.")
        self.key_input = QLineEdit()
        self.key_input.setEchoMode(QLineEdit.Password)
        self.key_input.setPlaceholderText("sk-or-...")
        card.body.addWidget(self.key_input)
        self.key_current_label = _label("", MUTED, 9.5, wrap=True)
        card.body.addWidget(self.key_current_label)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.key_test_button = _centered_button("Test", 96)
        self.key_save_button = _centered_button("Save", 96)
        self.key_remove_button = _centered_button("Remove", 96)
        for button in (self.key_test_button, self.key_save_button, self.key_remove_button):
            buttons.addWidget(button)
        buttons.addStretch(1)
        card.body.addLayout(buttons)
        self.key_result_label = _label("", GOLD, 10, wrap=True)
        card.body.addWidget(self.key_result_label)
        layout.addWidget(card)

        self._key_check_thread = None
        self.key_test_button.clicked.connect(self._on_key_test)
        self.key_save_button.clicked.connect(self._on_key_save)
        self.key_remove_button.clicked.connect(self._on_key_remove)
        self._secrets = secrets
        self._refresh_key()
        return "Chronicle needs a key for summaries and the assistant."

    def _refresh_key(self):
        secrets = self._secrets
        source = secrets.key_source()
        current = secrets.get_api_key()
        if source == "environment":
            self.key_current_label.setText(
                f"In use: {secrets.mask(current)} (from OPENROUTER_API_KEY / .env, "
                "which takes priority over a saved key).")
        elif source == "keyring":
            self.key_current_label.setText(f"Saved: {secrets.mask(current)}")
        else:
            self.key_current_label.setText("No API key is set.")
        self.key_remove_button.setEnabled(secrets.has_saved_key())

    def _on_key_test(self):
        key = self.key_input.text().strip() or (self._secrets.get_api_key() or "")
        if not key:
            self.key_result_label.setText("Enter an API key first.")
            return
        self.key_test_button.setEnabled(False)
        self.key_result_label.setText("Testing...")
        thread = ApiKeyCheckThread(key)
        thread.result_signal.connect(self._on_key_tested)
        thread.finished.connect(thread.deleteLater)
        self._key_check_thread = thread  # keep alive while running
        thread.start()

    def _on_key_tested(self, ok: bool, message: str):
        self.key_result_label.setText(("✓ " if ok else "✗ ") + message)
        self.key_test_button.setEnabled(True)

    def _on_key_save(self):
        secrets = self._secrets
        key = self.key_input.text().strip()
        if not key:
            self.key_result_label.setText("Type the new key in the field above, then Save.")
            return
        try:
            secrets.set_api_key(key)
        except Exception as e:  # noqa: BLE001
            self.key_result_label.setText(f"✗ Could not save the key: {secrets.redact(e, key)}")
            return
        self.key_input.clear()
        self.key_result_label.setText("Saved.")
        self._window._on_status_update("API key saved")
        self._refresh_key()

    def _on_key_remove(self):
        try:
            self._secrets.clear_api_key()
        except Exception as e:  # noqa: BLE001
            self.key_result_label.setText(f"✗ Could not remove the key: {type(e).__name__}")
            return
        self.key_result_label.setText("Removed.")
        self._window._on_status_update("API key removed")
        self._refresh_key()

    def _build_calendar(self, layout) -> str:
        from ..calendar_sync import google_auth

        self._google = google_auth
        self._google_thread = None
        self._google_flow = None  # the SignInFlow while the browser step runs

        self.google_connect_button = _centered_button("Connect", 150)
        self.google_cancel_button = _centered_button("Cancel", 96)
        self.google_cancel_button.hide()
        buttons = QWidget()
        row = QHBoxLayout(buttons)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        row.addWidget(self.google_cancel_button)
        row.addWidget(self.google_connect_button)
        card = _SettingCard(
            "Google account",
            "Lets Chronicle add due dates to your Google Calendar. The sign-in "
            "is kept in the Windows Credential Manager.",
            buttons)
        self.google_status_label = _label("", GOLD, 10, bold=True, wrap=True)
        self.google_error_label = _label("", MUTED, 9.5, wrap=True)
        card.body.addWidget(self.google_status_label)
        card.body.addWidget(self.google_error_label)
        layout.addWidget(card)

        card = _SettingCard("Calendar", "Where new events go.")
        self.google_calendar_label = _label("", CREAM, 10, wrap=True)
        card.body.addWidget(self.google_calendar_label)
        layout.addWidget(card)

        self.google_source_combo = QComboBox()
        self.google_source_combo.setMinimumWidth(280)
        chevron = asset_path("icon_chevron_down_dark.svg").replace("\\", "/")
        self.google_source_combo.setStyleSheet(
            f"QComboBox::down-arrow {{ image: url({chevron}); width: 12px; height: 8px;"
            " border: none; margin-right: 10px; }")
        self.google_source_combo.addItem("Chronicle (recommended)", google_auth.SOURCE_CHRONICLE)
        self.google_source_combo.addItem("Custom", google_auth.SOURCE_CUSTOM)
        card = _SettingCard(
            "OAuth client",
            "The Google Cloud app Chronicle signs in through. Use Custom to "
            "import a \"Desktop app\" client from your own Google Cloud project.")
        card.body.addWidget(self.google_source_combo)
        self.google_source_hint = _label("", MUTED, 9.5, wrap=True)
        card.body.addWidget(self.google_source_hint)
        custom_row = QHBoxLayout()
        custom_row.setSpacing(10)
        self.google_import_button = _centered_button("Import client_secret.json...", 260)
        custom_row.addWidget(self.google_import_button)
        self.google_client_label = _label("", MUTED, 9.5, wrap=True)
        custom_row.addWidget(self.google_client_label, 1)
        card.body.addLayout(custom_row)
        layout.addWidget(card)

        connected, _email, source = self._google_state()
        if connected and source in (google_auth.SOURCE_CHRONICLE, google_auth.SOURCE_CUSTOM):
            self._google_source = source
        elif self._google_client(google_auth.SOURCE_CHRONICLE) is not None:
            self._google_source = google_auth.SOURCE_CHRONICLE
        else:
            self._google_source = google_auth.SOURCE_CUSTOM

        self.google_connect_button.clicked.connect(self._on_google_connect)
        self.google_cancel_button.clicked.connect(self._on_google_cancel)
        self.google_source_combo.currentIndexChanged.connect(self._on_google_source_changed)
        self.google_import_button.clicked.connect(self._on_google_import)
        self._refresh_calendar()
        return "Send due dates to your Google Calendar."

    def _google_state(self):
        try:
            return self._google.connection_state()
        except Exception:  # noqa: BLE001 - unreadable prefs/keyring reads as "not connected"
            return False, None, None

    def _google_client(self, source):
        """The OAuth client for ``source``, or None (an invalid file counts as none)."""
        try:
            return self._google.load_client(source)
        except self._google.GoogleAuthError:
            return None

    def _refresh_calendar(self):
        google = self._google
        connected, email, _source = self._google_state()
        busy = self._google_thread is not None
        waiting = self._google_flow is not None

        if connected:
            self.google_status_label.setText(f"Connected as {email or 'your Google account'}")
            self.google_calendar_label.setText(f"Primary calendar ({email or 'Google account'})")
        else:
            self.google_status_label.setText("Not connected")
            self.google_calendar_label.setText("Connect a Google account first.")

        chronicle_client = self._google_client(google.SOURCE_CHRONICLE)
        custom_client = self._google_client(google.SOURCE_CUSTOM)
        combo = self.google_source_combo
        combo.blockSignals(True)
        combo.model().item(0).setEnabled(chronicle_client is not None)
        combo.setCurrentIndex(combo.findData(self._google_source))
        combo.blockSignals(False)
        combo.setEnabled(not connected and not busy)
        if connected:
            hint = "Disconnect first to change the client."
        elif chronicle_client is None:
            hint = "Chronicle client: Not available in this build."
        else:
            hint = ""
        self.google_source_hint.setText(hint)
        self.google_source_hint.setVisible(bool(hint))

        custom = self._google_source == google.SOURCE_CUSTOM
        self.google_import_button.setVisible(custom)
        self.google_client_label.setVisible(custom)
        self.google_import_button.setEnabled(not connected and not busy)
        if custom_client is not None:
            client_id = custom_client.client_id
            shown = client_id if len(client_id) <= 28 else client_id[:25] + "..."
            self.google_client_label.setText(f"Client ID: {shown}")
        else:
            self.google_client_label.setText("No client imported.")

        if waiting:
            self.google_connect_button.setText("Waiting for browser...")
            self.google_connect_button.setEnabled(False)
        elif busy:
            self.google_connect_button.setText("Disconnecting...")
            self.google_connect_button.setEnabled(False)
        else:
            self.google_connect_button.setText("Disconnect" if connected else "Connect")
            selected = custom_client if custom else chronicle_client
            self.google_connect_button.setEnabled(connected or selected is not None)
        self.google_cancel_button.setVisible(waiting)

    def _on_google_source_changed(self, _index):
        source = self.google_source_combo.currentData()
        if source:
            self._google_source = source
            self.google_error_label.setText("")
            self._refresh_calendar()

    def _on_google_import(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Import client_secret.json", "", "JSON files (*.json);;All files (*)")
        if not path:
            return
        try:
            self._google.import_custom_client(path)
        except self._google.GoogleAuthError as e:
            self.google_error_label.setText(str(e))
            return
        except OSError as e:
            self.google_error_label.setText(f"Could not import the client file: {type(e).__name__}.")
            return
        self.google_error_label.setText("Client imported.")
        self._refresh_calendar()

    def _on_google_connect(self):
        if self._google_thread is not None:
            return
        if self._google_state()[0]:
            self._confirm_google_disconnect()
            return
        try:
            client = self._google.load_client(self._google_source)
        except self._google.GoogleAuthError as e:
            self.google_error_label.setText(str(e))
            return
        if client is None:
            self.google_error_label.setText("Choose an OAuth client first.")
            return
        self.google_error_label.setText("")
        self._google_flow = self._google.SignInFlow(client)
        self._start_google_call(self._google_flow.run, self._on_google_signed_in)

    def _confirm_google_disconnect(self):
        reply = QMessageBox.question(
            self, "Disconnect Google account",
            "Chronicle will no longer be able to add events. Events already "
            "created stay in your calendar.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply != QMessageBox.Yes:
            return
        self.google_error_label.setText("")
        self._start_google_call(self._google.disconnect, self._on_google_disconnected)

    def _start_google_call(self, call, on_result):
        thread = GoogleCallThread(call)
        thread.result_signal.connect(on_result)
        thread.finished.connect(thread.deleteLater)
        self._google_thread = thread  # keep alive while running
        self._refresh_calendar()
        thread.start()

    def _on_google_cancel(self):
        if self._google_flow is not None:
            self._google_flow.cancel()

    def _on_google_signed_in(self, ok: bool, text: str):
        self._google_thread = None
        self._google_flow = None
        self.google_error_label.setText("" if ok else text)
        if ok:
            self._window._on_status_update("Google account connected")
        self._refresh_calendar()

    def _on_google_disconnected(self, ok: bool, text: str):
        self._google_thread = None
        self.google_error_label.setText("" if ok else text)
        if ok:
            self._window._on_status_update("Google account disconnected")
        self._refresh_calendar()

    def _build_maintenance(self, layout) -> str:
        action = self._window._reindex_action
        self.reindex_button = _centered_button("Reindex...", 140)
        self.reindex_button.setEnabled(action.isEnabled())
        self.reindex_button.clicked.connect(self._window._on_reindex_all_clicked)
        action.changed.connect(self._sync_reindex)
        layout.addWidget(_SettingCard(
            "Reindex all (RAG)",
            "Rebuild chunks, embeddings and session profiles for every session. "
            "Runs in the background; use it if search results look stale.",
            self.reindex_button))
        self.log_folder_button = _centered_button("Open...", 140)
        self.log_folder_button.clicked.connect(self._window._open_log_folder_action.trigger)
        layout.addWidget(_SettingCard(
            "Log folder",
            "Chronicle writes chronicle.log here. Attach it when reporting a problem.",
            self.log_folder_button))
        return "Recovery tools."

    def _sync_reindex(self):
        self.reindex_button.setEnabled(self._window._reindex_action.isEnabled())

    def _build_about(self, layout) -> str:
        card = _SettingCard(f"CHRONICLE {APP_VERSION}")
        card.body.addWidget(_label(
            "Meeting capture and transcription tool. Captures audio and "
            "screenshots, transcribes locally and writes summaries.",
            CREAM, 10.5, wrap=True))
        layout.addWidget(card)
        return "What this is."

    # -- open / close ---------------------------------------------------------

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
        self._pop_in()

    def _pop_in(self):
        """Fade in and rise a few pixels into the centre of the main window."""
        parent = self.parentWidget()
        if parent is not None:
            center = parent.mapToGlobal(parent.rect().center())
            target = QPoint(center.x() - self.width() // 2, center.y() - self.height() // 2)
        else:
            target = self.pos()
        fade = QPropertyAnimation(self, b"windowOpacity", self)
        fade.setDuration(160)
        fade.setStartValue(0.0)
        fade.setEndValue(1.0)
        rise = QPropertyAnimation(self, b"pos", self)
        rise.setDuration(160)
        rise.setStartValue(target + QPoint(0, 18))
        rise.setEndValue(target)
        rise.setEasingCurve(QEasingCurve.OutCubic)
        self._pop = QParallelAnimationGroup(self)
        self._pop.addAnimation(fade)
        self._pop.addAnimation(rise)
        self._pop.start()

    def done(self, result):
        self._apply_vad()  # a slider moved within the debounce window
        if self._scrim is not None:
            self._scrim.deleteLater()
            self._scrim = None
        thread = self._key_check_thread
        if thread is not None:
            try:
                thread.result_signal.disconnect()
                thread.wait()
            except RuntimeError:
                pass  # already finished and deleted
            self._key_check_thread = None
        if self._google_flow is not None:
            self._google_flow.cancel()  # closing mid sign-in cancels it
        thread = self._google_thread
        if thread is not None:
            try:
                thread.result_signal.disconnect()
                thread.wait()
            except RuntimeError:
                pass  # already finished and deleted
            self._google_thread = None
            self._google_flow = None
        super().done(result)
