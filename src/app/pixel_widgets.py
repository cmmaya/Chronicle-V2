from PySide6.QtWidgets import (
    QWidget,
    QLabel,
    QPushButton,
    QToolButton,
    QHBoxLayout,
    QVBoxLayout,
    QSizePolicy,
    QStyleOptionButton,
    QStyle,
    QLineEdit,
)
from PySide6.QtCore import Qt, QSize, Signal, QTimer, QEvent
import html as html_escape
import re
from PySide6.QtGui import (
    QColor,
    QPainter,
    QPainterPath,
    QPen,
    QBrush,
    QFont,
)


# =========================
# Palette
# =========================

NAVY = QColor("#061946")
NAVY_INNER = QColor("#071D52")

BORDER_BLUE = QColor("#254D9C")
BORDER_BLUE_LIGHT = QColor("#3A67C7")
BORDER_BLUE_ACTIVE = QColor("#4A78D8")

BUTTON_BLUE = QColor("#274F9B")
BUTTON_BLUE_HOVER = QColor("#315DB1")
BUTTON_BLUE_PRESSED = QColor("#1E3F82")

CREAM = QColor("#F6E0A6")
CREAM_BORDER = QColor("#FFEFC1")

BUBBLE_BLUE = QColor("#294F9D")
BUBBLE_BLUE_BORDER = QColor("#3765BD")

TEXT_LIGHT = QColor("#FFF0BF")
TEXT_DARK = QColor("#071846")

# In-text find highlight (BU099 follow-up): a chip behind the matched word(s).
# Two pairs, one per bubble fill, so the chip always contrasts with the
# bubble it's drawn on (a cream-on-cream chip over a "cream" bubble would be
# invisible - CREAM bubbles use the dark-on-light BUBBLE_BLUE pair instead).
FIND_TERM_ON_CREAM = ("#294F9D", "#FFF0BF")  # over CREAM / cream-variant bubbles
FIND_TERM_ON_BLUE = ("#FFEFC1", "#071846")   # over BUBBLE_BLUE / blue-variant bubbles


# =========================
# Pixel geometry helpers
# =========================

def pixel_round_rect_path(x: int, y: int, w: int, h: int, cut: int = 10) -> QPainterPath:
    """
    Rectángulo con esquinas pixeladas.
    No usa curvas; todo son segmentos rectos.
    """
    path = QPainterPath()
    path.moveTo(x + cut, y)
    path.lineTo(x + w - cut, y)
    path.lineTo(x + w, y + cut)
    path.lineTo(x + w, y + h - cut)
    path.lineTo(x + w - cut, y + h)
    path.lineTo(x + cut, y + h)
    path.lineTo(x, y + h - cut)
    path.lineTo(x, y + cut)
    path.closeSubpath()
    return path


def highlight_terms_html(text: str, terms, bg: str, fg: str) -> str:
    """Escape ``text`` for rich-text display, wrapping every case-insensitive
    occurrence of any ``terms`` substring in a ``bg``/``fg`` highlight span.
    Longest terms are matched first so one term can't shadow a longer one
    that contains it. Returns plain escaped text when ``terms`` is empty.
    """
    escaped = html_escape.escape(text)
    terms = sorted({t for t in (terms or []) if t}, key=len, reverse=True)
    if not terms:
        return escaped

    pattern = "|".join(re.escape(t) for t in terms)
    regex = re.compile(pattern, re.IGNORECASE)

    def _wrap(match: "re.Match") -> str:
        return f'<span style="background:{bg}; color:{fg};">{match.group(0)}</span>'

    return regex.sub(_wrap, escaped)


# =========================
# Panels
# =========================

class PixelPanel(QWidget):
    """
    Panel plano con borde pixelado, sin sombra ni bisel.
    Úsalo para sidebar, workspace, panel de transcripciones y cajas internas.
    """

    clicked = Signal()

    def __init__(self, parent=None, inner: bool = False):
        super().__init__(parent)
        self.inner = inner
        self._active = False
        self.setAttribute(Qt.WA_StyledBackground, False)
        self.setAutoFillBackground(False)

    def set_active(self, value: bool):
        value = bool(value)
        if value == self._active:
            return
        self._active = value
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, False)

        rect = self.rect().adjusted(2, 2, -3, -3)
        if rect.width() <= 0 or rect.height() <= 0:
            return

        # Fase 1: borde único, sin doble contorno interno.
        # El panel interno conserva la misma geometría, pero usa un trazo más ligero
        # para que se parezca al mockup de referencia y no genere efecto de bisel.
        cut = 9 if not self.inner else 7
        fill = NAVY_INNER if self.inner else NAVY
        border = BORDER_BLUE_LIGHT if self.inner else BORDER_BLUE
        pen_width = 2 if not self.inner else 2
        if self._active and not self.inner:
            border = BORDER_BLUE_ACTIVE
            pen_width = 3

        path = pixel_round_rect_path(rect.x(), rect.y(), rect.width(), rect.height(), cut)
        painter.setBrush(QBrush(fill))
        painter.setPen(QPen(border, pen_width))
        painter.drawPath(path)

        super().paintEvent(event)


# =========================
# Titles
# =========================

class PixelSectionTitle(QLabel):
    def __init__(self, text: str, parent=None, center: bool = False):
        super().__init__(text, parent)
        self.setObjectName("PixelSectionTitle")
        self.setAlignment(Qt.AlignCenter if center else Qt.AlignLeft)

        font = QFont("Courier New")
        font.setPointSize(11)
        font.setBold(True)
        font.setLetterSpacing(QFont.AbsoluteSpacing, 2)
        self.setFont(font)

        self.setStyleSheet(
            """
            QLabel#PixelSectionTitle {
                color: #FFE9A8;
                background: transparent;
                border: none;
            }
            """
        )


# =========================
# Buttons
# =========================

class PixelButton(QPushButton):
    """
    Botón pixelado plano.
    Acepta sidebar=True para compatibilidad con MainWindow.
    """

    def __init__(self, text: str = "", parent=None, sidebar: bool = False):
        super().__init__(text, parent)
        self.sidebar = sidebar
        self.setCursor(Qt.PointingHandCursor)
        self.setObjectName("PixelButton")

        if sidebar:
            self.setMinimumHeight(52)
        else:
            self.setMinimumHeight(42)

        font = QFont("Courier New")
        font.setPointSize(12 if sidebar else 10)
        font.setBold(True)
        self.setFont(font)

        self.setStyleSheet(
            """
            QPushButton#PixelButton {
                color: #FFF0BF;
                background: #274F9B;
                border: 2px solid #3A67C7;
                border-radius: 7px;
                padding: 7px 12px;
                text-align: left;
            }

            QPushButton#PixelButton:hover {
                background: #315DB1;
            }

            QPushButton#PixelButton:pressed {
                background: #1E3F82;
                padding-top: 10px;
                padding-left: 16px;
            }

            QPushButton#PixelButton[iconOnly="true"] {
                text-align: center;
                padding: 6px;
            }

            QPushButton#PixelButton[iconOnly="true"]:pressed {
                padding: 7px 5px 5px 7px;
            }

            QPushButton#PixelButton:disabled {
                color: #8090B8;
                background: #18336F;
                border: 2px solid #284B94;
            }
            """
        )


class PixelToolButton(QToolButton):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumSize(QSize(52, 52))
        self.setIconSize(QSize(28, 28))
        self.setObjectName("PixelToolButton")

        font = QFont("Courier New")
        font.setPointSize(11)
        font.setBold(True)
        self.setFont(font)

        self.setStyleSheet(
            """
            QToolButton#PixelToolButton {
                color: #FFF0BF;
                background: #274F9B;
                border: 2px solid #3A67C7;
                border-radius: 7px;
                padding: 6px;
            }

            QToolButton#PixelToolButton:hover {
                background: #315DB1;
            }

            QToolButton#PixelToolButton:pressed {
                background: #1E3F82;
            }

            QToolButton#PixelToolButton:disabled {
                color: #8090B8;
                background: #18336F;
                border: 2px solid #284B94;
            }
            """
        )


# =========================
# Chat bubbles
# =========================

# A footer such as "\n\n— via Any Session" appended by the assistant to mark
# the scope an answer was drawn from. Rendered as a small, muted line instead
# of inline with the answer text so it reads as a quiet attribution, not part
# of the message.
_SCOPE_FOOTER_RE = re.compile(r"\n\n(— via .+)$", re.DOTALL)


class PixelBubble(QWidget):
    """
    Burbuja plana tipo mockup 2:
    sin bisel, sin sombra, con esquinas pixeladas y cola pixelada.
    """

    def __init__(
        self,
        text: str,
        variant: str = "blue",
        tail: str = "left",
        max_width: int = 520,
        parent=None,
    ):
        super().__init__(parent)
        self.variant = variant
        self.tail = tail
        self.cut = 7
        self.tail_size = 13
        self._highlighted = False
        self._match_terms = []

        self.setAttribute(Qt.WA_StyledBackground, False)
        self.setAutoFillBackground(False)
        self.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Minimum)

        footer_match = _SCOPE_FOOTER_RE.search(text)
        body_text = text[: footer_match.start()] if footer_match else text
        footer_text = footer_match.group(1) if footer_match else None
        self._body_text = body_text

        text_color = "#071846" if variant == "cream" else "#FFF0BF"

        self.label = QLabel(body_text)
        self.label.setWordWrap(True)
        self.label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.label.setMaximumWidth(max_width)
        self.label.setTextFormat(Qt.MarkdownText)

        font = QFont("Courier New")
        font.setPointSize(12)
        font.setBold(False)
        self.label.setFont(font)
        self.label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        self.label.setStyleSheet(
            f"""
            QLabel {{
                color: {text_color};
                background: transparent;
                border: none;
                padding-right: 6px;
            }}
            """
        )

        self.footer_label = None
        if footer_text:
            self.footer_label = QLabel(footer_text)
            self.footer_label.setWordWrap(True)
            self.footer_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            self.footer_label.setMaximumWidth(max_width)
            self.footer_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)

            footer_font = QFont("Courier New")
            footer_font.setPointSize(9)
            footer_font.setBold(False)
            footer_font.setItalic(True)
            self.footer_label.setFont(footer_font)

            footer_color = "#5B6A94" if variant == "cream" else "#93A2CE"
            self.footer_label.setStyleSheet(
                f"""
                QLabel {{
                    color: {footer_color};
                    background: transparent;
                    border: none;
                    padding-right: 6px;
                }}
                """
            )

        content = QVBoxLayout()
        content.setContentsMargins(0, 0, 0, 0)
        content.setSpacing(6)
        content.addWidget(self.label)
        if self.footer_label:
            content.addWidget(self.footer_label)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(0)
        layout.addLayout(content)
        layout.setAlignment(content, Qt.AlignVCenter)

    def set_highlighted(self, value: bool):
        value = bool(value)
        if value == self._highlighted:
            return
        self._highlighted = value
        self.update()

    def set_match_terms(self, terms):
        """Wrap every occurrence of ``terms`` inside the bubble's own text in
        a cream highlight chip (BU099 find-in-pane). Empty/None restores the
        plain label text.
        """
        terms = [t for t in (terms or []) if t]
        if terms == self._match_terms:
            return
        self._match_terms = terms
        if terms:
            # Pick the pair that contrasts with *this* bubble's own fill.
            bg, fg = FIND_TERM_ON_CREAM if self.variant == "cream" else FIND_TERM_ON_BLUE
            self.label.setTextFormat(Qt.RichText)
            self.label.setText(highlight_terms_html(self._body_text, terms, bg, fg))
        else:
            self.label.setTextFormat(Qt.MarkdownText)
            self.label.setText(self._body_text)

    def _bubble_path(self) -> QPainterPath:
        rect = self.rect().adjusted(1, 1, -3, -3)

        if self.tail == "left":
            body = rect.adjusted(self.tail_size, 0, 0, 0)
        else:
            body = rect.adjusted(0, 0, -self.tail_size, 0)

        path = pixel_round_rect_path(body.x(), body.y(), body.width(), body.height(), self.cut)
        tail_y = body.bottom() - 14

        if self.tail == "left":
            path.moveTo(body.left(), tail_y)
            path.lineTo(body.left() - self.tail_size, tail_y + 7)
            path.lineTo(body.left(), tail_y + 11)
            path.closeSubpath()
        else:
            path.moveTo(body.right(), tail_y)
            path.lineTo(body.right() + self.tail_size, tail_y + 7)
            path.lineTo(body.right(), tail_y + 11)
            path.closeSubpath()

        return path

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, False)

        if self.variant == "cream":
            fill = CREAM
            border = CREAM_BORDER
        else:
            fill = BUBBLE_BLUE
            border = BUBBLE_BLUE_BORDER

        pen_width = 2
        if self._highlighted:
            border = CREAM_BORDER
            pen_width = 3

        path = self._bubble_path()
        painter.setBrush(QBrush(fill))
        painter.setPen(QPen(border, pen_width))
        painter.drawPath(path)

        super().paintEvent(event)


class PixelScopePrompt(QWidget):
    """In-chat prompt (BU093) offering a one-click switch to Specific Session
    for a single dominant meeting.

    Same pixel language as PixelBubble: pixel-cut corners, a left tail, blue
    variant colours, Courier New bold label. Two PixelButtons (YES / NO) sit
    below the text, with a "Choose another session" link beneath them. The
    widget holds no application logic - it only emits ``accepted`` /
    ``declined`` / ``choose_another`` and can collapse itself to a static
    record of the choice so the conversation still reads on scroll-back.
    """

    accepted = Signal()
    declined = Signal()
    choose_another = Signal()

    def __init__(self, text: str, max_width: int = 400, allow_choose_another: bool = True, parent=None):
        super().__init__(parent)
        self.cut = 7
        self.tail_size = 13
        self._answered = False

        self.setAttribute(Qt.WA_StyledBackground, False)
        self.setAutoFillBackground(False)
        self.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Minimum)

        self.label = QLabel(text)
        self.label.setWordWrap(True)
        self.label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.label.setMaximumWidth(max_width)

        font = QFont("Courier New")
        font.setPointSize(12)
        font.setBold(True)
        self.label.setFont(font)
        self.label.setStyleSheet(
            """
            QLabel {
                color: #FFF0BF;
                background: transparent;
                border: none;
            }
            """
        )

        self._yes = PixelButton("YES")
        self._no = PixelButton("NO")
        for btn in (self._yes, self._no):
            btn.setMinimumHeight(34)
            btn.setMaximumWidth(120)
        self._yes.clicked.connect(self._on_yes)
        self._no.clicked.connect(self._on_no)

        self._other = QPushButton("Choose another session")
        self._other.setCursor(Qt.PointingHandCursor)
        self._other.setFlat(True)
        self._other.setStyleSheet(
            """
            QPushButton {
                color: #BFD2FF;
                background: transparent;
                border: none;
                text-align: left;
                padding: 0;
                text-decoration: underline;
                font-family: 'Courier New';
                font-weight: bold;
            }
            QPushButton:hover { color: #FFF0BF; }
            QPushButton:disabled { color: #6E7CA8; }
            """
        )
        self._other.clicked.connect(self._on_other)
        self._other.setVisible(allow_choose_another)

        self._buttons = QWidget()
        button_row = QHBoxLayout(self._buttons)
        button_row.setContentsMargins(0, 0, 0, 0)
        button_row.setSpacing(8)
        button_row.addWidget(self._yes)
        button_row.addWidget(self._no)
        button_row.addStretch(1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14 + self.tail_size, 14, 14, 14)
        layout.setSpacing(10)
        layout.addWidget(self.label)
        layout.addWidget(self._buttons)
        layout.addWidget(self._other)

    # --- choice ----------------------------------------------------------

    def _on_yes(self):
        if self._answered:
            return
        self._settle("> Scope switched to Specific Session.")
        self.accepted.emit()

    def _on_no(self):
        if self._answered:
            return
        self._settle("> Kept Any Session.")
        self.declined.emit()

    def _on_other(self):
        if self._answered:
            return
        self.choose_another.emit()

    def _settle(self, record: str):
        """Disable the controls and collapse to a short static record so the
        prompt cannot be triggered twice.
        """
        self._answered = True
        self._yes.setEnabled(False)
        self._no.setEnabled(False)
        self._other.setEnabled(False)
        self._buttons.hide()
        self._other.hide()
        self.label.setText(f"{self.label.text()}\n\n{record}")
        self.updateGeometry()

    def retire(self, record: str = "> No longer offered."):
        """Collapse an unanswered prompt when a newer question supersedes it."""
        if not self._answered:
            self._settle(record)

    # --- painting (PixelBubble blue + left tail) -------------------------

    def _bubble_path(self) -> QPainterPath:
        rect = self.rect().adjusted(1, 1, -3, -3)
        body = rect.adjusted(self.tail_size, 0, 0, 0)
        path = pixel_round_rect_path(
            body.x(), body.y(), body.width(), body.height(), self.cut
        )
        tail_y = body.bottom() - 14
        path.moveTo(body.left(), tail_y)
        path.lineTo(body.left() - self.tail_size, tail_y + 7)
        path.lineTo(body.left(), tail_y + 11)
        path.closeSubpath()
        return path

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, False)
        path = self._bubble_path()
        painter.setBrush(QBrush(BUBBLE_BLUE))
        painter.setPen(QPen(BUBBLE_BLUE_BORDER, 2))
        painter.drawPath(path)
        super().paintEvent(event)


def aligned_bubble(text: str, variant: str = "blue", align: str = "left", max_width: int = 520) -> QWidget:
    """
    Retorna una fila con burbuja alineada.
    MainWindow ya usa esta función para chat y transcripts.
    """
    row = QWidget()
    row.setAttribute(Qt.WA_StyledBackground, False)
    row.setAutoFillBackground(False)

    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)

    tail = "right" if align == "right" else "left"
    bubble = PixelBubble(text=text, variant=variant, tail=tail, max_width=max_width)

    if align == "right":
        layout.addStretch(1)
        layout.addWidget(bubble, 0, Qt.AlignRight)
    else:
        layout.addWidget(bubble, 0, Qt.AlignLeft)
        layout.addStretch(1)

    return row


def bubble_of_row(row) -> "PixelBubble | None":
    """Return the ``PixelBubble`` inside a row built by ``aligned_bubble``."""
    if row is None:
        return None
    if isinstance(row, PixelBubble):
        return row
    found = row.findChild(PixelBubble)
    return found


# =========================
# Find bar (BU099)
# =========================

class PixelFindBar(QWidget):
    """Small pixel-styled in-pane find bar: a cream keyword field, a
    ``current / total`` counter and compact previous / next buttons.

    Pure UI - it holds no search logic, only emits signals. ``Esc`` closes,
    ``Return`` steps to the next match, ``Shift+Return`` to the previous, and
    ``textChanged`` is debounced (~150 ms) before ``query_changed`` fires.
    """

    query_changed = Signal(str)
    next_match = Signal()
    prev_match = Signal()
    return_pressed = Signal()
    closed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, False)
        self.setAutoFillBackground(False)
        # Height is fixed; width adapts to the host pane in
        # MainWindow._position_find_bar so the bar never spills outside a
        # narrow sidebar / transcripts panel.
        self.preferred_width = 330
        self.setFixedHeight(46)
        self.resize(self.preferred_width, 46)

        font = QFont("Courier New")
        font.setPointSize(11)
        font.setBold(True)

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(150)
        self._debounce.timeout.connect(
            lambda: self.query_changed.emit(self._field.text())
        )

        self._field = QLineEdit()
        self._field.setPlaceholderText("Find…")
        self._field.setFont(font)
        self._field.textChanged.connect(lambda _: self._debounce.start())
        self._field.installEventFilter(self)
        self._field.setStyleSheet(
            """
            QLineEdit {
                color: #071846;
                background: #F6E0A6;
                border: 2px solid #FFEFC1;
                border-radius: 5px;
                padding: 3px 6px;
            }
            """
        )

        self._counter = QLabel("0 / 0")
        self._counter.setFont(font)
        self._counter.setAlignment(Qt.AlignCenter)
        self._counter.setStyleSheet(
            "QLabel { color: #FFF0BF; background: transparent; border: none; }"
        )

        self._prev = QToolButton()
        self._prev.setText("▲")
        self._next = QToolButton()
        self._next.setText("▼")
        for btn in (self._prev, self._next):
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFixedSize(30, 30)
            btn.setFont(font)
            btn.setStyleSheet(
                """
                QToolButton {
                    color: #FFF0BF;
                    background: #274F9B;
                    border: 2px solid #3A67C7;
                    border-radius: 5px;
                }
                QToolButton:hover { background: #315DB1; }
                QToolButton:pressed { background: #1E3F82; }
                QToolButton:disabled { color: #8090B8; background: #18336F; }
                """
            )
        self._prev.clicked.connect(self.prev_match.emit)
        self._next.clicked.connect(self.next_match.emit)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(6)
        layout.addWidget(self._field, 1)
        layout.addWidget(self._counter, 0)
        layout.addWidget(self._prev, 0)
        layout.addWidget(self._next, 0)

    # --- painting -------------------------------------------------------

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, False)
        rect = self.rect().adjusted(1, 1, -2, -2)
        path = pixel_round_rect_path(
            rect.x(), rect.y(), rect.width(), rect.height(), 8
        )
        painter.setBrush(QBrush(NAVY))
        painter.setPen(QPen(BORDER_BLUE_LIGHT, 2))
        painter.drawPath(path)
        super().paintEvent(event)

    # --- keys ----------------------------------------------------------

    def eventFilter(self, obj, event):
        if obj is self._field and event.type() == QEvent.KeyPress:
            key = event.key()
            if key == Qt.Key_Escape:
                self.closed.emit()
                return True
            if key in (Qt.Key_Return, Qt.Key_Enter):
                if event.modifiers() & Qt.ShiftModifier:
                    self.prev_match.emit()
                else:
                    self.return_pressed.emit()
                return True
        return super().eventFilter(obj, event)

    # --- API used by MainWindow ---------------------------------------

    def set_match_count(self, current: int, total: int):
        self._counter.setText(f"{current} / {total}" if total else "0 / 0")
        self._prev.setEnabled(total > 0)
        self._next.setEnabled(total > 0)

    def focus_field(self):
        self._field.setFocus()
        self._field.selectAll()

    def query_text(self) -> str:
        return self._field.text()


# =========================
# Summary view (BU100)
# =========================


def pixel_mini_button(
    text: str, tooltip: str = "", width: int = 32, height: int = 30
) -> QToolButton:
    """Small pixel-styled tool button, matching PixelFindBar's stepper buttons.
    Used for compact controls (zoom, expand/collapse all)."""
    btn = QToolButton()
    btn.setText(text)
    btn.setCursor(Qt.PointingHandCursor)
    btn.setFixedSize(width, height)
    if tooltip:
        btn.setToolTip(tooltip)

    font = QFont("Courier New")
    font.setPointSize(11)
    font.setBold(True)
    btn.setFont(font)

    btn.setStyleSheet(
        """
        QToolButton {
            color: #FFF0BF;
            background: #274F9B;
            border: 2px solid #3A67C7;
            border-radius: 5px;
            padding: 0px 4px;
        }
        QToolButton:hover { background: #315DB1; }
        QToolButton:pressed { background: #1E3F82; }
        QToolButton:disabled { color: #8090B8; background: #18336F; }
        """
    )
    return btn


# Section titles the summarization templates emit (see summarization/templates.py:
# FULL and GENERAL_TRANSCRIPT). Keys are normalized header text, values the
# display title. Only these are treated as section breaks - anything else stays
# inside the section it was written under, so an unexpected numbered line in the
# body ("1. The team agreed ...") can never be mistaken for a header.
_SUMMARY_SECTION_ALIASES = {
    "overview": "Overview",
    "general overview": "Overview",
    "summary": "Overview",
    "key points": "Key Points",
    "keypoints": "Key Points",
    "main points": "Key Points",
    "highlights": "Key Points",
    "action items": "Action Items",
    "action points": "Action Items",
    "actions": "Action Items",
    "tasks": "Action Items",
    "decisions": "Decisions",
    "decisions made": "Decisions",
    "agreements": "Decisions",
    "next steps": "Next Steps",
    "follow ups": "Next Steps",
    "follow-ups": "Next Steps",
    "open questions or unclear points": "Open Questions",
    "open questions and unclear points": "Open Questions",
    "open questions": "Open Questions",
    "unclear points": "Open Questions",
    "questions": "Open Questions",
    "notable moments": "Notable Moments",
    "participants": "Participants",
}

# Strips the decoration around a header line: markdown hashes/bold markers, a
# leading "3." / "3)" ordinal and a trailing colon.
_HEADER_DECORATION_RE = re.compile(
    r"^\s*#{0,4}\s*\*{0,2}_{0,2}\s*(?:\d{1,2}\s*[.)\-]\s*)?(.*?)\s*_{0,2}\*{0,2}\s*:?\s*$"
)

# Body lines that carry their own structure.
_NUMBERED_LINE_RE = re.compile(r"^\s*(\d{1,2})\s*[.)]\s+(.*)$")
_BULLET_LINE_RE = re.compile(r"^\s*[-*•]\s+(.*)$")
_FIELD_LINE_RE = re.compile(
    r"^\s*\*{0,2}(Title|Description|Due date|Due|Deadline|Responsible|Owner|Assignee|Status)"
    r"\*{0,2}\s*:\s*(.*)$",
    re.IGNORECASE,
)


def _section_title_for(line: str):
    """Return the display title if ``line`` is a known summary section header."""
    if len(line) > 80:
        return None
    match = _HEADER_DECORATION_RE.match(line)
    if not match:
        return None
    normalized = re.sub(r"\s+", " ", match.group(1)).strip().lower()
    return _SUMMARY_SECTION_ALIASES.get(normalized)


def parse_summary_sections(text: str):
    """Split raw summary text into ``[(title, body), ...]`` using the numbered
    section headers the summarization templates produce.

    Falls back to a single ``("Summary", text)`` section when no known header is
    found, so legacy or off-template content is still shown in full.
    """
    text = (text or "").strip()
    if not text:
        return []

    sections = []
    current_title = None
    buffer = []

    def flush():
        body = "\n".join(buffer).strip()
        if current_title is None:
            if body:
                sections.append(("Summary", body))
        else:
            sections.append((current_title, body))

    for line in text.splitlines():
        title = _section_title_for(line)
        if title:
            flush()
            current_title = title
            buffer = []
        else:
            buffer.append(line)
    flush()

    if not sections:
        return [("Summary", text)]
    return sections


def count_summary_items(body: str) -> int:
    """Number of discrete entries in a section body - numbered lines, bullets,
    or ``Title:`` blocks (the action-item format). 0 when the body is prose."""
    fields = 0
    items = 0
    for line in (body or "").splitlines():
        field = _FIELD_LINE_RE.match(line)
        if field and field.group(1).lower() == "title":
            fields += 1
        elif _NUMBERED_LINE_RE.match(line) or _BULLET_LINE_RE.match(line):
            items += 1
    return fields or items


def format_summary_body_html(body: str) -> str:
    """Render a section body as the Qt rich-text subset: numbered entries get a
    cream ordinal, ``Label:`` fields get a cream bold label, and each new
    action-item block is separated by a rule. Font size is left to the widget so
    the view can be scaled."""
    body = (body or "").strip()
    if not body:
        return '<div style="color:#8EA7D8;">Not specified.</div>'

    parts = []
    first_card = True
    # (html template with one {} slot, lines). A plain line continues the open
    # block rather than starting a new one, so text the model hard-wrapped mid
    # sentence reflows as a single paragraph / entry instead of breaking apart.
    pending = None

    def flush():
        nonlocal pending
        if pending is None:
            return
        template, lines = pending
        pending = None
        text = " ".join(part.strip() for part in lines).strip()
        if text:
            parts.append(template.format(html_escape.escape(text)))

    for raw in body.splitlines():
        line = raw.strip()
        if not line:
            flush()
            continue

        field = _FIELD_LINE_RE.match(line)
        if field:
            flush()
            label, value = field.group(1), field.group(2).strip()
            if label.lower() == "title":
                if not first_card:
                    parts.append('<hr width="100%">')
                first_card = False
                pending = (
                    '<div style="margin:6px 0 2px 0;">'
                    '<b style="color:#FFE9A8;">{}</b></div>',
                    [value],
                )
            else:
                pending = (
                    '<div style="margin:0 0 2px 0;">'
                    f'<span style="color:#8EA7D8;">{html_escape.escape(label)}:</span> '
                    '{}</div>',
                    [value],
                )
            continue

        numbered = _NUMBERED_LINE_RE.match(line)
        if numbered:
            flush()
            pending = (
                '<div style="margin:0 0 8px 0;">'
                f'<b style="color:#FFE9A8;">{numbered.group(1)}.</b>&nbsp;'
                '{}</div>',
                [numbered.group(2)],
            )
            continue

        bullet = _BULLET_LINE_RE.match(line)
        if bullet:
            flush()
            pending = (
                '<div style="margin:0 0 8px 0;">'
                '<b style="color:#FFE9A8;">&#8226;</b>&nbsp;{}</div>',
                [bullet.group(1)],
            )
            continue

        if pending is not None:
            pending[1].append(line)
        else:
            pending = ('<div style="margin:0 0 8px 0;">{}</div>', [line])

    flush()
    return "".join(parts)


class _SectionHeader(QWidget):
    """Clickable pixel bar: chevron, section title and an item-count chip."""

    clicked = Signal()

    def __init__(self, title: str, count: int = 0, parent=None):
        super().__init__(parent)
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_Hover, True)
        self.setAutoFillBackground(False)
        self._hover = False
        self._expanded = True

        self.chevron = QLabel("▼")
        self.title = QLabel(title.upper())
        self.count = QLabel(str(count))
        self.count.setAlignment(Qt.AlignCenter)
        self.count.setVisible(count > 0)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 6, 12, 6)
        layout.setSpacing(10)
        layout.addWidget(self.chevron, 0)
        layout.addWidget(self.title, 1)
        layout.addWidget(self.count, 0)

    def set_expanded(self, value: bool):
        self._expanded = bool(value)
        self.chevron.setText("▼" if self._expanded else "▶")
        self.update()

    def apply_fonts(self, title_pt: float, chip_pt: float):
        # Sizes go through the widget's own stylesheet, not setFont: the
        # app-wide QSS declares a font-size for QWidget, and a stylesheet font
        # property always wins over setFont. Letter spacing has no QSS
        # equivalent, so that one stays on the QFont.
        spaced = QFont("Courier New")
        spaced.setBold(True)
        spaced.setLetterSpacing(QFont.AbsoluteSpacing, 1.5)
        self.title.setFont(spaced)

        self.chevron.setStyleSheet(
            "QLabel { color: #FFE9A8; background: transparent; border: none;"
            f" font-family: 'Courier New'; font-size: {title_pt:.1f}pt; font-weight: 700; }}"
        )
        self.title.setStyleSheet(
            "QLabel { color: #FFF0BF; background: transparent; border: none;"
            f" font-family: 'Courier New'; font-size: {title_pt:.1f}pt; font-weight: 700; }}"
        )
        self.count.setStyleSheet(
            "QLabel { color: #071846; background: #F6E0A6; border: 2px solid #FFEFC1;"
            " border-radius: 9px; padding: 1px 8px;"
            f" font-family: 'Courier New'; font-size: {chip_pt:.1f}pt; font-weight: 700; }}"
        )
        self.setMinimumHeight(round(title_pt * 2.6) + 16)

    def enterEvent(self, event):
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, False)
        rect = self.rect().adjusted(1, 1, -2, -2)
        if rect.width() <= 0 or rect.height() <= 0:
            return

        if self._hover:
            fill = BUTTON_BLUE_HOVER
        elif self._expanded:
            fill = BUTTON_BLUE
        else:
            fill = BUTTON_BLUE_PRESSED
        border = CREAM_BORDER if self._hover else BORDER_BLUE_LIGHT

        path = pixel_round_rect_path(
            rect.x(), rect.y(), rect.width(), rect.height(), 8
        )
        painter.setBrush(QBrush(fill))
        painter.setPen(QPen(border, 2))
        painter.drawPath(path)
        super().paintEvent(event)


class PixelCollapsibleSection(QWidget):
    """One summary section: a clickable pixel header over a navy body card that
    the user can deploy / undeploy. Sizes itself to its content, so a column of
    these scrolls as a single page instead of nesting scrollbars."""

    toggled = Signal(bool)

    def __init__(self, title: str, body: str, parent=None):
        super().__init__(parent)
        self.section_title = title
        self._expanded = True

        self.header = _SectionHeader(title, count_summary_items(body))
        self.header.clicked.connect(self.toggle)

        self.body_panel = PixelPanel(inner=True)
        self.body_label = QLabel(format_summary_body_html(body))
        self.body_label.setTextFormat(Qt.RichText)
        self.body_label.setWordWrap(True)
        self.body_label.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.body_label.setTextInteractionFlags(
            Qt.TextSelectableByMouse | Qt.LinksAccessibleByMouse
        )

        body_layout = QVBoxLayout(self.body_panel)
        body_layout.setContentsMargins(16, 12, 16, 12)
        body_layout.setSpacing(0)
        body_layout.addWidget(self.body_label)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(self.header)
        layout.addWidget(self.body_panel)

        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        self.set_scale(1.0)

    def is_expanded(self) -> bool:
        return self._expanded

    def set_expanded(self, value: bool):
        value = bool(value)
        if value == self._expanded:
            return
        self._expanded = value
        self.body_panel.setVisible(value)
        self.header.set_expanded(value)
        self.toggled.emit(value)

    def toggle(self):
        self.set_expanded(not self._expanded)

    def set_scale(self, scale: float):
        """Scale every font in the section. 1.0 is the base size."""
        self.header.apply_fonts(max(8.0, 11 * scale), max(7.0, 9 * scale))
        self.body_label.setStyleSheet(
            "QLabel { color: #FFF0BF; background: transparent; border: none;"
            f" font-family: 'Courier New'; font-size: {max(8.0, 10.5 * scale):.1f}pt; }}"
        )
        pad = max(8, round(12 * scale))
        self.body_panel.layout().setContentsMargins(pad + 4, pad, pad + 4, pad)
        self.body_label.adjustSize()
        self.adjustSize()
