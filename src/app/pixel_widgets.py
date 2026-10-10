from PySide6.QtWidgets import QDialog
from PySide6.QtCore import QObject, QAbstractNativeEventFilter
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
    QTextEdit,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QApplication,
)
from PySide6.QtCore import Qt, QSize, Signal, QTimer, QEvent, QMargins
import html as html_escape
import re
from .pixel_theme import asset_path
from . import theme
from PySide6.QtGui import (
    QColor,
    QIcon,
    QPixmap,
    QPainter,
    QPainterPath,
    QPen,
    QBrush,
    QFont,
    QFontMetrics,
    QCursor,
)


# =========================
# Palette
# =========================

NAVY = theme.qcolor("#061946")
NAVY_INNER = theme.qcolor("#071D52")

BORDER_BLUE = theme.qcolor("#254D9C")
BORDER_BLUE_LIGHT = theme.qcolor("#3A67C7")
BORDER_BLUE_ACTIVE = theme.qcolor("#4A78D8")

BUTTON_BLUE = theme.qcolor("#274F9B")
BUTTON_BLUE_HOVER = theme.qcolor("#315DB1")
BUTTON_BLUE_PRESSED = theme.qcolor("#1E3F82")

CREAM = theme.qcolor("#F6E0A6")
CREAM_BORDER = theme.qcolor("#FFEFC1")

BUBBLE_BLUE = theme.qcolor("#294F9D")
BUBBLE_BLUE_BORDER = theme.qcolor("#3765BD")

TEXT_LIGHT = theme.qcolor("#FFF0BF")
TEXT_DARK = theme.qcolor("#071846")

# Role colours that a theme may split off from the palette above (BU129).
PANEL_BORDER_INNER = theme.role_color("panel_border_inner")
PANEL_GLOW = theme.role("panel_glow")  # "" = no halo around panels

# In-text find highlight (BU099 follow-up): a chip behind the matched word(s).
# Two pairs, one per bubble fill, so the chip always contrasts with the
# bubble it's drawn on (a cream-on-cream chip over a "cream" bubble would be
# invisible - CREAM bubbles use the dark-on-light BUBBLE_BLUE pair instead).
FIND_TERM_ON_CREAM = (theme.hex("#294F9D"), theme.hex("#FFF0BF"))  # over CREAM / cream-variant bubbles
FIND_TERM_ON_BLUE = (theme.hex("#FFEFC1"), theme.hex("#071846"))   # over BUBBLE_BLUE / blue-variant bubbles

# Selected transcript bubble (BU113). Deliberately not cream: the find
# highlight already owns CREAM_BORDER, and a selection has to stay legible
# next to one. A saturated cyan reads as "picked by the user" against both
# bubble fills and is nothing else in the theme.
SELECTED_BORDER = theme.qcolor("#6FD3FF")

# Fill behind a selected chunk inside a segmented bubble. One per bubble
# variant, because a single tint cannot read against both a cream and a blue
# fill. The cyan left edge is what the two have in common, so a selection
# still scans as one thing down a mixed column.
SEGMENT_SELECTED_ON_CREAM = theme.hex("#E0C079")
SEGMENT_SELECTED_ON_BLUE = theme.hex("#3C6FCB")


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


# Chat answers only ever use light markdown emphasis (bold, occasional
# italics) plus plain line breaks - not full markdown (tables, headers,
# nested lists). Qt.MarkdownText's QLabel sizeHint is unreliable for
# word-wrapped, list-shaped text (the measured height can wildly overshoot
# the rendered height, leaving a huge blank gap above/below the text once
# vertically centered) so bubbles convert the light subset to HTML by hand
# and render it as ordinary Qt.RichText, whose word-wrap/sizeHint path is
# the same well-tested one PixelCollapsibleSection already relies on.
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)
_ITALIC_RE = re.compile(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", re.DOTALL)
_CODE_RE = re.compile(r"`([^`]+)`")

# Block-level markdown the models actually emit in answers: ATX headings,
# thematic breaks, and bullet/numbered lists. They are rendered as inline
# HTML on a single line (a sized bold span, a rule, a bullet glyph plus
# non-breaking-space indent) rather than as <h1>/<ul>/<li> blocks, because
# QLabel's word-wrap sizeHint is only reliable for the flat, <br>-joined
# markup the bubbles already use.
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_RULE_RE = re.compile(r"^\s*(?:-\s*-\s*-[-\s]*|\*\s*\*\s*\*[\*\s]*|_\s*_\s*_[_\s]*)$")
_BULLET_RE = re.compile(r"^(\s*)[-*+]\s+(.*)$")
_NUMBER_RE = re.compile(r"^(\s*)(\d{1,3})[.)]\s+(.*)$")

# Base bubble/answer text is 12pt; headings step up from there and stop
# growing after level 3 so a stray "#####" cannot dwarf the answer.
_HEADING_SIZES = {1: 16, 2: 14, 3: 13, 4: 12, 5: 12, 6: 12}

# A real rule rather than a run of dashes, so the separator spans the card
# instead of sitting as a short stub at whatever the em-dash count happens to
# measure. Qt's rich-text subset renders <hr> as a full-width block, so it is
# emitted on its own and not wrapped in the surrounding <br> joins.
_RULE_HTML = '<hr>'


# Models write math as LaTeX ("$q_\pi(s, a)$"). Qt rich text has no math
# renderer, so the common subset is mapped to Greek/symbol glyphs plus
# <sub>/<sup> instead of leaving the raw TeX source on screen.
_MATH_RE = re.compile(r"\$(?!\s)([^$\n]*?[^\s$])\$(?!\d)")
_TEX_SYMBOLS = {
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ε",
    "varepsilon": "ε", "zeta": "ζ", "eta": "η", "theta": "θ", "lambda": "λ",
    "mu": "μ", "nu": "ν", "xi": "ξ", "pi": "π", "rho": "ρ", "sigma": "σ",
    "tau": "τ", "phi": "φ", "varphi": "φ", "chi": "χ", "psi": "ψ",
    "omega": "ω", "Gamma": "Γ", "Delta": "Δ", "Theta": "Θ", "Lambda": "Λ",
    "Pi": "Π", "Sigma": "Σ", "Phi": "Φ", "Psi": "Ψ", "Omega": "Ω",
    "cdot": "·", "times": "×", "leq": "≤", "le": "≤", "geq": "≥", "ge": "≥",
    "neq": "≠", "ne": "≠", "approx": "≈", "infty": "∞", "sum": "Σ",
    "prod": "Π", "int": "∫", "partial": "∂", "nabla": "∇", "in": "∈",
    "to": "→", "rightarrow": "→", "leftarrow": "←", "pm": "±",
    "sqrt": "√", "ldots": "…", "dots": "…", "mid": "|",
    "arg": "arg", "max": "max", "min": "min", "log": "log", "exp": "exp",
}
_TEX_CMD_RE = re.compile(r"\\([A-Za-z]+)")
_TEX_SCRIPT_RE = re.compile(r"([_^])(?:\{([^{}]*)\}|(\\[A-Za-z]+|.))")
_TEX_WRAP_RE = re.compile(r"\\(?:text|mathrm|mathbf|mathcal|operatorname)\{([^{}]*)\}")


def _latex_to_html(expr: str) -> str:
    """Render a TeX math fragment (already HTML-escaped) as inline HTML."""
    expr = _TEX_WRAP_RE.sub(r"\1", expr)
    expr = _TEX_CMD_RE.sub(
        lambda m: _TEX_SYMBOLS.get(m.group(1), m.group(1)), expr
    )
    expr = _TEX_SCRIPT_RE.sub(
        lambda m: "<{0}>{1}</{0}>".format(
            "sub" if m.group(1) == "_" else "sup",
            m.group(2) if m.group(2) is not None else m.group(3).lstrip("\\"),
        ),
        expr,
    )
    return f"<i>{expr.replace('{', '').replace('}', '')}</i>"


def _inline_markdown_to_html(text: str) -> str:
    """Escape ``text`` and convert the inline span markers only."""
    escaped = _MATH_RE.sub(
        lambda m: _latex_to_html(m.group(1)), html_escape.escape(text)
    )
    coded = _CODE_RE.sub(
        lambda m: ('<span style="background:rgba(0,0,0,0.12);">'
                   f'{m.group(1)}</span>'),
        escaped,
    )
    bolded = _BOLD_RE.sub(lambda m: f"<b>{m.group(1)}</b>", coded)
    return _ITALIC_RE.sub(lambda m: f"<i>{m.group(1)}</i>", bolded)


def _indent_html(spaces: str) -> str:
    """Two source spaces (or one tab) of list nesting -> one indent step."""
    width = len(spaces.expandtabs(2))
    return "&nbsp;" * (4 * (width // 2))


def _markdown_line_to_html(raw: str) -> str:
    """Convert a single markdown line to inline HTML."""
    line = raw.rstrip()
    if not line.strip():
        return ""

    if _RULE_RE.match(line):
        return _RULE_HTML

    heading = _HEADING_RE.match(line.lstrip())
    if heading is not None:
        level = len(heading.group(1))
        size = _HEADING_SIZES[level]
        body = _inline_markdown_to_html(heading.group(2))
        return f'<b><span style="font-size:{size}pt;">{body}</span></b>'

    bullet = _BULLET_RE.match(line)
    if bullet is not None:
        return (f'{_indent_html(bullet.group(1))}&#8226;&nbsp;'
                f'{_inline_markdown_to_html(bullet.group(2))}')

    numbered = _NUMBER_RE.match(line)
    if numbered is not None:
        return (f'{_indent_html(numbered.group(1))}{numbered.group(2)}.&nbsp;'
                f'{_inline_markdown_to_html(numbered.group(3))}')

    return _inline_markdown_to_html(line)


def _join_markdown_lines(parts) -> str:
    """Join rendered lines with <br>, except around a rule - <hr> is already a
    block of its own, so a <br> on either side would leave a blank line."""
    out = ""
    for index, part in enumerate(parts):
        if index and part != _RULE_HTML and not out.endswith(_RULE_HTML):
            out += "<br>"
        out += part
    return out


def simple_markdown_to_html(text: str) -> str:
    """Escape ``text`` and convert the markdown subset answers actually use
    (bold, italics, inline code, headings, rules, lists) plus newlines to
    HTML, without pulling in a full markdown parser."""
    lines = (text or "").splitlines() or [""]
    return _join_markdown_lines(
        _markdown_line_to_html(line) for line in lines
    )


# BU116/BU151: the evidence label is what tells the user how far to trust the
# answer, so it must not dissolve into the body prose. Deliberately narrow -
# the labels the answer prompts emit - so an ordinary sentence opening with
# "From the slides the answer is:" is not mistaken for one.
_ANSWER_LABEL_RE = re.compile(
    r"^(From transcripts|From documents|General knowledge)\s*:\s*",
    re.IGNORECASE,
)


def format_answer_html(text: str) -> str:
    """Render a live answer, giving each evidence label its own opening line.

    Everything else goes through ``simple_markdown_to_html`` unchanged, so an
    answer with no labels renders exactly as it did before.
    """
    rendered = []
    for raw in (text or "").splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        match = _ANSWER_LABEL_RE.match(line.lstrip())
        if match is None:
            rendered.append(_markdown_line_to_html(line))
            continue
        label = html_escape.escape(match.group(1).strip())
        body = _markdown_line_to_html(line.lstrip()[match.end():].strip())
        rendered.append(
            f'<b><span style="color:#FFE9A8;">{label}:</span></b> {body}'
        )
    return theme.remap(_join_markdown_lines(rendered))


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
        self._backdrop = None  # QPixmap painted inside the border (BU129)
        self._backdrop_sky = None
        self._backdrop_cache = None  # (size, scaled pixmap)
        self.setAttribute(Qt.WA_StyledBackground, False)
        self.setAutoFillBackground(False)

    def set_backdrop(self, image_path):
        """Paint ``image_path`` inside the panel instead of the flat fill.

        The image spans the panel's width and sits on its bottom edge; any
        height it leaves free above is filled with the image's top-row
        colour, so a tall panel reads as more sky. ``None`` clears it.
        """
        self._backdrop = None
        self._backdrop_cache = None
        if image_path:
            pixmap = QPixmap(str(image_path))
            if not pixmap.isNull():
                self._backdrop = pixmap
                self._backdrop_sky = pixmap.toImage().pixelColor(pixmap.width() // 2, 0)
        self.update()

    def _scaled_backdrop(self, width: int, height: int) -> QPixmap:
        # Slightly wider than the panel, centred, so the sun sits a little
        # right of centre as in the theme's reference.
        size = (width, height)
        if self._backdrop_cache is None or self._backdrop_cache[0] != size:
            scaled = self._backdrop.scaledToWidth(
                max(1, round(width * 1.08)), Qt.SmoothTransformation
            )
            self._backdrop_cache = (size, scaled)
        return self._backdrop_cache[1]

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
        border = PANEL_BORDER_INNER if self.inner else BORDER_BLUE
        pen_width = 2 if not self.inner else 2
        if self._active and not self.inner:
            border = BORDER_BLUE_ACTIVE
            pen_width = 3

        path = pixel_round_rect_path(rect.x(), rect.y(), rect.width(), rect.height(), cut)
        if self._backdrop is not None:
            painter.save()
            painter.setClipPath(path)
            painter.fillRect(rect, self._backdrop_sky)
            scaled = self._scaled_backdrop(rect.width(), rect.height())
            painter.drawPixmap(
                rect.x() + (rect.width() - scaled.width()) // 2,
                rect.bottom() - scaled.height() + 1,
                scaled,
            )
            painter.restore()
        else:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(fill))
            painter.drawPath(path)
        painter.setBrush(Qt.NoBrush)

        # Neon themes: a soft halo under the border line (BU129).
        if PANEL_GLOW:
            painter.save()
            painter.setRenderHint(QPainter.Antialiasing, True)
            for width, alpha in ((9, 28), (6, 55), (4, 90)):
                glow = QColor(border)
                glow.setAlpha(alpha)
                painter.setPen(QPen(glow, width))
                painter.drawPath(path)
            painter.restore()

        painter.setPen(QPen(border, pen_width))
        painter.drawPath(path)

        super().paintEvent(event)


# =========================
# Titles
# =========================

class PixelSectionTitle(QLabel):
    def __init__(self, text: str, parent=None, center: bool = False, alt: bool = False):
        """``alt`` picks the theme's secondary title colour (BU129)."""
        super().__init__(text, parent)
        self.setObjectName("PixelSectionTitle")
        self.setAlignment(Qt.AlignCenter if center else Qt.AlignLeft)

        font = QFont("Courier New")
        font.setPointSize(11)
        font.setBold(True)
        font.setLetterSpacing(QFont.AbsoluteSpacing, 2)
        self.setFont(font)

        color = theme.role("section_title_alt" if alt else "section_title")
        self.setStyleSheet(
            f"""
            QLabel#PixelSectionTitle {{
                color: {color};
                background: transparent;
                border: none;
            }}
            """
        )


# =========================
# Buttons
# =========================

# Accent variants a theme can give a button through its ``accent`` property
# (BU129). Classic has none, so the property changes nothing there.
_ACCENT_BUTTON_QSS = {
    theme.SYNTHWAVE: """
    QPushButton#PixelButton[accent="primary"] {
        color: #FFF8FF;
        background: #6E0258;
        border: 2px solid #E0149F;
    }
    QPushButton#PixelButton[accent="primary"]:hover {
        background: #850A6C;
    }
    QPushButton#PixelButton[accent="primary"]:pressed {
        background: #52003F;
    }
    """,
}

_ACCENT_TOOL_QSS = {
    theme.SYNTHWAVE: """
    QToolButton#PixelToolButton[accent="menu"] {
        background: #321054;
        border-color: #8A2A9A;
    }
    QToolButton#PixelToolButton[accent="menu"]:hover {
        background: #43186E;
        border-color: #C040C8;
    }
    """,
}


def set_accent(widget: QWidget, accent: str):
    """Give a PixelButton / PixelToolButton one of the theme's accent looks."""
    widget.setProperty("accent", accent)
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    widget.update()


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
                color: __BUTTON_TEXT__;
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
            """.replace("__BUTTON_TEXT__", theme.role("button_text"))
            + _ACCENT_BUTTON_QSS.get(theme.active(), "")
        )


class PixelToolButton(QToolButton):
    """Square icon button in the app's navy/gold palette.

    ``compact=True`` (used by the transcripts toolbar, BU104) trims the
    footprint down from the default 52px/28px icon to a slimmer 34px/18px
    button with a thinner border - the default size reads oversized in a
    ~300px-wide side panel that holds three of these side by side.
    """

    def __init__(self, parent=None, compact: bool = False):
        super().__init__(parent)
        self.setCursor(Qt.PointingHandCursor)
        self.compact = compact

        size = QSize(34, 34) if compact else QSize(52, 52)
        icon_size = QSize(18, 18) if compact else QSize(28, 28)
        border_width = 1 if compact else 2
        if not theme.is_classic():
            border_width = 2  # the neon border has to read at a glance
        tool_border = theme.role("tool_border")
        radius = 6 if compact else 7
        padding = 4 if compact else 6

        if compact:
            # Pin compact buttons so every toolbar button renders the same size.
            self.setFixedSize(size)
        else:
            self.setMinimumSize(size)
        self.setIconSize(icon_size)
        self.setObjectName("PixelToolButton")

        font = QFont("Courier New")
        font.setPointSize(11)
        font.setBold(True)
        self.setFont(font)

        self.setStyleSheet(
            f"""
            QToolButton#PixelToolButton {{
                color: #FFF0BF;
                background: #274F9B;
                border: {border_width}px solid {tool_border};
                border-radius: {radius}px;
                padding: {padding}px;
            }}

            QToolButton#PixelToolButton:hover {{
                background: #315DB1;
                border-color: #4A78D8;
            }}

            QToolButton#PixelToolButton:pressed {{
                background: #1E3F82;
            }}

            QToolButton#PixelToolButton:disabled {{
                color: #8090B8;
                background: #18336F;
                border: 2px solid #284B94;
            }}

            /* BU138: a button that is switched off but should still look
               unselected - opt in with setProperty("quietDisabled", True). */
            QToolButton#PixelToolButton[quietDisabled="true"]:disabled {{
                color: #FFF0BF;
                background: #274F9B;
                border: {border_width}px solid {tool_border};
            }}
            """
            + _ACCENT_TOOL_QSS.get(theme.active(), "")
        )


# =========================
# Chat bubbles
# =========================

# A footer such as "\n\n— via Any Session" (scope attribution) or "\n\n— 21:03"
# (BU103 grouped-bubble timestamp) appended after the bubble text. Rendered as
# a small, muted line instead of inline with the body so it reads as a quiet
# annotation, not part of the message.
_SCOPE_FOOTER_RE = re.compile(r"\n\n(— .+)$", re.DOTALL)


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
        segmented: bool = False,
    ):
        """
        Args:
            segmented: keep each appended chunk as its own label inside the
                bubble instead of merging it into one body (BU113 follow-up).
                A grouped bubble can hold a minute of speech, and the chunk -
                not the bubble - is what the user selects and asks about, so
                each one has to be a widget that can be hit-tested and
                highlighted on its own. The labels are siblings in this
                widget's single top-level layout, which is the arrangement
                Qt's word-wrap height propagation handles (see the layout
                comment below); they are not a nested layout.
        """
        super().__init__(parent)
        self.variant = variant
        self.tail = tail
        self.cut = 7
        self.tail_size = 13
        self._highlighted = False
        self._selected = False
        self._match_terms = []
        self._segmented = segmented
        self._segments = []
        self._segment_texts = []
        self._max_width = max_width
        self._fill_width = False  # set_max_width() turns this on

        self.setAttribute(Qt.WA_StyledBackground, False)
        self.setAutoFillBackground(False)
        self.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Minimum)

        footer_match = _SCOPE_FOOTER_RE.search(text)
        body_text = (text[: footer_match.start()] if footer_match else text).strip()
        footer_text = footer_match.group(1) if footer_match else None
        self._body_text = body_text

        text_color = "#071846" if variant == "cream" else theme.role("bubble_blue_text")

        self.label = QLabel(simple_markdown_to_html(body_text))
        self.label.setWordWrap(True)
        self.label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.label.setMaximumWidth(max_width)
        self.label.setTextFormat(Qt.RichText)

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
            # Scope attribution ("— via Specific Session — Session X") reads as
            # a quiet source tag under a hairline: "via Specific Session · Session X".
            is_scope = footer_text.startswith("— via ")
            if is_scope:
                footer_text = footer_text[2:].replace(" — ", " · ")
            self.footer_label = QLabel(footer_text)
            self.footer_label.setWordWrap(True)
            self.footer_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            self.footer_label.setMaximumWidth(max_width)
            self.footer_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)

            footer_font = QFont("Courier New")
            footer_font.setPointSize(9)
            footer_font.setBold(False)
            footer_font.setItalic(not is_scope)
            self.footer_label.setFont(footer_font)

            footer_color = "#5B6A94" if variant == "cream" else "#93A2CE"
            # Size lives in the stylesheet: the app-wide QWidget font-size rule
            # overrides setFont().
            if is_scope:
                rule_color = "rgba(91, 106, 148, 0.35)" if variant == "cream" else "rgba(147, 162, 206, 0.30)"
                footer_qss = f"""
                QLabel {{
                    color: {footer_color};
                    background: transparent;
                    border: none;
                    border-top: 1px solid {rule_color};
                    padding: 6px 6px 0px 0px;
                    font-size: 11px;
                    font-style: normal;
                }}
                """
            else:
                footer_qss = f"""
                QLabel {{
                    color: {footer_color};
                    background: transparent;
                    border: none;
                    padding-right: 6px;
                }}
                """
            self.footer_label.setStyleSheet(footer_qss)

        # A single top-level layout directly on the widget - no layout nested
        # inside another - is the one case Qt's word-wrap heightForWidth
        # propagation reliably supports. Nesting a QVBoxLayout inside this
        # widget's own QHBoxLayout (an earlier version of this footer split)
        # broke that propagation: the reported height stopped tracking the
        # label's actual assigned width, so the box grew or clipped text
        # depending on how far off the guess was.
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(6)
        layout.addWidget(self.label)
        if self.footer_label:
            layout.addWidget(self.footer_label)
        self._layout = layout

        if self._segmented:
            self._segments.append(self.label)
            self._segment_texts.append(body_text)
            self._style_segment(self.label, selected=False)

    def set_max_width(self, max_width: int):
        """Let the bubble's text fill up to ``max_width``, e.g. to follow a
        resizable window.

        A word-wrapped QLabel's size hint is Qt's own narrow guess, and the
        bubble's Maximum size policy treats that hint as a ceiling, so raising
        the maximum alone never widens anything. Each label also gets a
        minimum width: its text's single-line width, capped at ``max_width``.
        Long text then fills the space, while short text keeps a snug bubble.
        """
        self._max_width = int(max_width)
        self._fill_width = True
        for label, text in self._width_labels():
            self._fit_label_width(label, text)
        self.updateGeometry()

    def _width_labels(self):
        if self._segmented:
            pairs = list(zip(self._segments, self._segment_texts))
        else:
            pairs = [(self.label, self._body_text)]
        if self.footer_label:
            pairs.append((self.footer_label, self.footer_label.text()))
        return pairs

    def _fit_label_width(self, label: QLabel, text: str):
        label.setMaximumWidth(self._max_width)
        if not self._fill_width:
            return
        metrics = label.fontMetrics()
        natural = max((metrics.horizontalAdvance(line) for line in text.splitlines()),
                      default=0)
        # Room for the label's 6px right padding plus a little slack, so the
        # measured line doesn't wrap its last word.
        label.setMinimumWidth(min(natural + 16, self._max_width))

    def set_highlighted(self, value: bool):
        value = bool(value)
        if value == self._highlighted:
            return
        self._highlighted = value
        self.update()

    def set_selected(self, value: bool):
        """Mark this bubble as part of the user's selection (BU113)."""
        value = bool(value)
        if value == self._selected:
            return
        self._selected = value
        self.update()

    def is_selected(self) -> bool:
        return self._selected

    def is_highlighted(self) -> bool:
        return self._highlighted

    def set_match_terms(self, terms):
        """Wrap every occurrence of ``terms`` inside the bubble's own text in
        a cream highlight chip (BU099 find-in-pane). Empty/None restores the
        plain label text.
        """
        terms = [t for t in (terms or []) if t]
        if terms == self._match_terms:
            return
        self._match_terms = terms
        if self._segmented:
            # Every chunk re-renders: a match can be in any of them, and they
            # are separate labels with separate text.
            for label, text in zip(self._segments, self._segment_texts):
                self._render_label(label, text)
            return
        self._render_label(self.label, self._body_text)

    def append_text(self, text: str):
        """Grow this bubble's body with more text (BU103 grouped live
        transcript bubbles), re-rendering through the same path
        ``set_match_terms`` uses so active find-highlighting keeps working.

        Each appended chunk starts its own paragraph (a blank line) rather
        than running on from a single space, so a viewer can tell where the
        newly-arrived text starts inside a bubble that keeps growing. In a
        ``segmented`` bubble it becomes its own label instead, so the chunk
        can be selected on its own.
        """
        self._body_text = f"{self._body_text}\n\n{text}"

        if self._segmented:
            self._segment_texts.append(text)
            label = self._new_segment_label(text)
            # Before the footer, if this bubble has one.
            self._layout.insertWidget(len(self._segments), label)
            self._segments.append(label)
            self._fit_label_width(label, text)
            return

        self._render_label(self.label, self._body_text)
        self._fit_label_width(self.label, self._body_text)

    def _new_segment_label(self, text: str) -> QLabel:
        label = QLabel()
        label.setWordWrap(True)
        label.setMaximumWidth(self._max_width)
        label.setTextFormat(Qt.RichText)
        label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        font = QFont("Courier New")
        font.setPointSize(12)
        font.setBold(False)
        label.setFont(font)

        self._render_label(label, text)
        self._style_segment(label, selected=False)
        return label

    def _render_label(self, label: QLabel, text: str):
        """Put ``text`` in ``label``, honouring any active find terms."""
        label.setTextFormat(Qt.RichText)
        if self._match_terms:
            bg, fg = FIND_TERM_ON_CREAM if self.variant == "cream" else FIND_TERM_ON_BLUE
            label.setText(highlight_terms_html(text, self._match_terms, bg, fg))
        else:
            label.setText(simple_markdown_to_html(text))

    def _style_segment(self, label: QLabel, selected: bool):
        text_color = "#071846" if self.variant == "cream" else theme.role("bubble_blue_text")
        if selected:
            fill = (SEGMENT_SELECTED_ON_CREAM if self.variant == "cream"
                    else SEGMENT_SELECTED_ON_BLUE)
            # A fill plus a cyan left edge: the fill is what reads at a glance,
            # the edge is what the two variants have in common so a mixed
            # selection still scans as one thing.
            label.setStyleSheet(
                f"""
                QLabel {{
                    color: {text_color};
                    background: {fill};
                    border: none;
                    border-left: 3px solid {SELECTED_BORDER.name()};
                    padding: 2px 6px 2px 5px;
                }}
                """
            )
        else:
            label.setStyleSheet(
                f"""
                QLabel {{
                    color: {text_color};
                    background: transparent;
                    border: none;
                    border-left: 3px solid transparent;
                    padding: 2px 6px 2px 5px;
                }}
                """
            )

    # --- segments (BU113 follow-up) --------------------------------------

    @property
    def segment_count(self) -> int:
        return len(self._segments) if self._segmented else 0

    def segment_texts(self) -> list:
        return list(self._segment_texts)

    def set_segment_selected(self, index: int, selected: bool):
        """Highlight one chunk inside this bubble."""
        if not self._segmented or not (0 <= index < len(self._segments)):
            return
        self._style_segment(self._segments[index], bool(selected))

    def segment_at(self, pos) -> int:
        """Index of the chunk under ``pos`` (this widget's coordinates).

        Returns the nearest segment vertically rather than -1 when the point
        falls in the padding between two of them, so a drag through a bubble
        never stalls on a gap.
        """
        if not self._segmented or not self._segments:
            return -1
        y = pos.y()
        best, best_distance = -1, None
        for index, label in enumerate(self._segments):
            top = label.y()
            bottom = top + label.height()
            if top <= y <= bottom:
                return index
            distance = top - y if y < top else y - bottom
            if best_distance is None or distance < best_distance:
                best, best_distance = index, distance
        return best

    def segment_index_of(self, widget) -> int:
        """Index of the segment that is (or contains) ``widget``, else -1."""
        if not self._segmented:
            return -1
        node = widget
        while node is not None:
            for index, label in enumerate(self._segments):
                if node is label:
                    return index
            if node is self:
                break
            node = node.parent()
        return -1

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

        # Selection outranks the find highlight on the border: both are
        # transient, but only one of them is something the user is holding, and
        # a match inside a selected bubble still shows through its term chip.
        if self._selected:
            border = SELECTED_BORDER
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


def aligned_bubble(text: str, variant: str = "blue", align: str = "left", max_width: int = 520,
                   segmented: bool = False) -> QWidget:
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
    bubble = PixelBubble(text=text, variant=variant, tail=tail, max_width=max_width,
                         segmented=segmented)

    if align == "right":
        layout.addStretch(1)
        layout.addWidget(bubble, 0, Qt.AlignRight)
    else:
        layout.addWidget(bubble, 0, Qt.AlignLeft)
        layout.addStretch(1)

    return row


def aligned_bubble_with_time(
    text: str,
    variant: str = "blue",
    align: str = "left",
    max_width: int = 520,
    time_text: str = "",
    segmented: bool = False,
) -> QWidget:
    """Like ``aligned_bubble``, but with a small muted timestamp placed
    *above* the bubble (BU104) instead of buried at the end of its text -
    so the time reads before the message, the way a chat client shows it,
    rather than as a trailing footer once the bubble has already appeared.
    """
    container = QWidget()
    container.setAttribute(Qt.WA_StyledBackground, False)
    container.setAutoFillBackground(False)

    outer = QVBoxLayout(container)
    outer.setContentsMargins(0, 0, 0, 0)
    outer.setSpacing(3)

    if time_text:
        time_label = QLabel(time_text)
        time_font = QFont("Courier New")
        time_font.setPointSize(9)
        time_font.setBold(True)
        time_font.setLetterSpacing(QFont.AbsoluteSpacing, 1)
        time_label.setFont(time_font)
        time_label.setStyleSheet(
            "QLabel { color: #7E8FC2; background: transparent; border: none; }"
        )

        time_row = QHBoxLayout()
        time_row.setContentsMargins(4, 0, 4, 0)
        time_row.setSpacing(0)
        if align == "right":
            time_row.addStretch(1)
            time_row.addWidget(time_label)
        else:
            time_row.addWidget(time_label)
            time_row.addStretch(1)
        outer.addLayout(time_row)

    outer.addWidget(aligned_bubble(text, variant=variant, align=align, max_width=max_width,
                                   segmented=segmented))
    return container


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

    def set_query_text(self, text: str):
        """Fill the field without firing ``query_changed``; the caller runs
        the query itself (and can then move to a specific match)."""
        self._field.blockSignals(True)
        self._field.setText(text)
        self._field.blockSignals(False)
        self._debounce.stop()


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
    "due dates": "Due Dates",
    "deadlines": "Due Dates",
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
    r"^\s*\*{0,2}(Title|Description|Calendar date|Due date|Due|Deadline|Responsible|Owner|Assignee|Status)"
    r"\*{0,2}\s*:\s*(.*)$",
    re.IGNORECASE,
)


# Markdown emphasis the model leaves in its output. Applied after HTML escaping
# - "**" carries no HTML-special characters, so escaping first stays safe.
_MD_BOLD_RE = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)


def escape_with_markdown_bold(text: str) -> str:
    """Escape ``text`` for rich-text display, rendering ``**bold**`` as ``<b>``
    instead of leaving the asterisks visible."""
    return _MD_BOLD_RE.sub(r"<b>\1</b>", html_escape.escape(text))


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

    Text before the first recognized header is the model's lead-in ("Here's a
    comprehensive summary of the meeting:") and is dropped. Falls back to a
    single ``("Summary", text)`` section when no known header is found at all,
    so legacy or off-template content is still shown in full.
    """
    text = (text or "").strip()
    if not text:
        return []

    sections = []
    current_title = None
    buffer = []

    def flush():
        if current_title is None:
            return
        sections.append((current_title, "\n".join(buffer).strip()))

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
            parts.append(template.format(escape_with_markdown_bold(text)))

    for raw in body.splitlines():
        line = raw.strip()
        if not line:
            flush()
            continue

        field = _FIELD_LINE_RE.match(line)
        if field:
            flush()
            label, value = field.group(1), field.group(2).strip()
            if label.lower() == "calendar date":
                # Machine-readable copy of the Due date line (BU129), for the
                # app only. The empty template also swallows wrapped lines.
                pending = ("", [value])
            elif label.lower() == "title":
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

    def __init__(self, title: str, body: str, parent=None, body_widget: QWidget = None):
        """``body_widget`` replaces the rich-text body (the Due Dates cards,
        BU133); the header count still comes from ``body``."""
        super().__init__(parent)
        self.section_title = title
        self._expanded = True

        self.header = _SectionHeader(title, count_summary_items(body))
        self.header.clicked.connect(self.toggle)

        self.body_panel = PixelPanel(inner=True)
        self.body_widget = body_widget
        self.body_label = QLabel(theme.remap(format_summary_body_html(body)))
        self.body_label.setTextFormat(Qt.RichText)
        self.body_label.setWordWrap(True)
        # Paragraphs read left-aligned; the block as a whole sits centred in the
        # body card, matching how PixelBubble centres its text.
        self.body_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.body_label.setTextInteractionFlags(
            Qt.TextSelectableByMouse | Qt.LinksAccessibleByMouse
        )

        body_layout = QVBoxLayout(self.body_panel)
        body_layout.setContentsMargins(16, 12, 16, 12)
        body_layout.setSpacing(0)
        if body_widget is not None:
            self.body_label.hide()
            body_layout.addWidget(body_widget)
        else:
            body_layout.addWidget(self.body_label)
            body_layout.setAlignment(self.body_label, Qt.AlignVCenter)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(self.header)
        layout.addWidget(self.body_panel)

        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        # A word-wrapped label reports a size hint for an unwrapped width, so
        # in a narrow column its later lines were clipped. Track the height
        # the text really needs at the width it actually got.
        self.body_label.installEventFilter(self)
        self.set_scale(1.0)

    def eventFilter(self, obj, event):
        if obj is self.body_label and event.type() == QEvent.Resize:
            self._fit_body_height()
        return super().eventFilter(obj, event)

    def _fit_body_height(self):
        if self.body_widget is not None:
            return
        label = self.body_label
        width = label.width()
        if width <= 0:
            return
        # QLabel.heightForWidth never reports less than the current minimum
        # height, so measure with the minimum cleared - otherwise a value
        # taken at a narrow in-between width during layout would stick.
        previous = label.minimumHeight()
        label.setMinimumHeight(0)
        needed = label.heightForWidth(width)
        label.setMinimumHeight(needed if needed > 0 else previous)

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

    def set_body(self, body: str, match_marks=None):
        """Replace the section's content (used by the screenshot viewer, BU109).

        ``match_marks`` is an optional ``(start, end)`` pair of marker
        characters bracketing search matches in ``body``; each bracketed run
        gets the find-bar highlight chip.
        """
        count = count_summary_items(body)
        self.header.count.setText(str(count))
        self.header.count.setVisible(count > 0)
        html = theme.remap(format_summary_body_html(body))
        if match_marks:
            bg, fg = FIND_TERM_ON_BLUE
            start, end = match_marks
            html = html.replace(
                start, f'<span style="background:{bg}; color:{fg};">'
            ).replace(end, '</span>')
        self.body_label.setText(html)
        self._fit_body_height()
        self.body_label.adjustSize()
        self.adjustSize()

    def set_scale(self, scale: float):
        """Scale every font in the section. 1.0 is the base size."""
        self.header.apply_fonts(max(8.0, 11 * scale), max(7.0, 9 * scale))
        self.body_label.setStyleSheet(
            "QLabel { color: #FFF0BF; background: transparent; border: none;"
            f" font-family: 'Courier New'; font-size: {max(8.0, 10.5 * scale):.1f}pt; }}"
        )
        if self.body_widget is not None:
            self.body_widget.set_scale(scale)
        pad = max(8, round(12 * scale))
        self.body_panel.layout().setContentsMargins(pad + 4, pad, pad + 4, pad)
        self._fit_body_height()
        self.body_label.adjustSize()
        self.adjustSize()


# =========================
# Due Dates cards (BU133)
# =========================

class _FittedLabel(QLabel):
    """Word-wrapped rich-text label that keeps the height its text needs at
    the width it got (same fix as PixelCollapsibleSection._fit_body_height)."""

    def __init__(self, html: str = "", parent=None):
        super().__init__(html, parent)
        self.setTextFormat(Qt.RichText)
        self.setWordWrap(True)
        self.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.LinksAccessibleByMouse)

    def fit(self):
        width = self.width()
        if width <= 0:
            return
        previous = self.minimumHeight()
        self.setMinimumHeight(0)
        needed = self.heightForWidth(width)
        self.setMinimumHeight(needed if needed > 0 else previous)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.fit()


_DUE_BUTTON_QSS = """
QToolButton {
    color: #FFF0BF; background: #274F9B; border: 2px solid #3A67C7;
    border-radius: 5px; padding: 0px 10px;
    font-family: 'Courier New'; font-size: __PT__pt; font-weight: 700;
}
QToolButton:hover { background: #315DB1; }
QToolButton:pressed { background: #1E3F82; }
QToolButton:disabled { color: #8090B8; background: #18336F; }
QToolButton[sent="true"] { color: #071846; background: #F6E0A6; border: 2px solid #FFEFC1; }
QToolButton[sent="true"]:hover { background: #FFEFC1; }
"""


class PixelDueDateCard(QWidget):
    """One Due Dates entry: title, due text and description as the rich-text
    body draws them, with a Send to Calendar button beside the title.

    ``link`` is the stored ``calendar_events`` row, or None. Once sent, the
    button reads "✓ In Calendar" and opens a menu instead.
    """

    send_requested = Signal()    # first send
    resend_requested = Signal()  # "Send again..." on a sent entry

    SEND_TEXT = "Send to Calendar"
    SENT_TEXT = "✓ In Calendar"

    def __init__(self, entry, link=None, parent=None):
        super().__init__(parent)
        self.entry = entry
        self.link = None
        self._busy_text = None

        title = escape_with_markdown_bold(entry.title or "Untitled")
        self.title_label = _FittedLabel(theme.remap(f'<b style="color:#FFE9A8;">{title}</b>'))
        self.button = QToolButton()
        self.button.setCursor(Qt.PointingHandCursor)
        self.button.clicked.connect(self._on_clicked)

        rows = []
        for label, value in (("Due date", entry.due_text), ("Description", entry.description)):
            if value:
                rows.append(
                    f'<div style="margin:0 0 2px 0;"><span style="color:#8EA7D8;">{label}:</span> '
                    f'{escape_with_markdown_bold(value)}</div>'
                )
        self.detail_label = _FittedLabel(theme.remap("".join(rows)))
        self.detail_label.setVisible(bool(rows))

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(12)
        top.addWidget(self.title_label, 1, Qt.AlignVCenter)
        top.addWidget(self.button, 0, Qt.AlignTop)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(4)
        layout.addLayout(top)
        layout.addWidget(self.detail_label)
        self.set_link(link)
        self.set_scale(1.0)

    def is_sent(self) -> bool:
        return self.link is not None

    def set_link(self, link):
        self.link = link
        self._refresh_button()

    def set_busy(self, text=None):
        """Show ``text`` on a disabled button (e.g. "Checking..."); None restores it."""
        self._busy_text = text
        self._refresh_button()

    def _refresh_button(self):
        if self._busy_text:
            self.button.setText(self._busy_text)
        else:
            self.button.setText(self.SENT_TEXT if self.is_sent() else self.SEND_TEXT)
        self.button.setEnabled(not self._busy_text)
        self.button.setProperty("sent", "true" if self.is_sent() and not self._busy_text else "false")
        self.button.setToolTip(
            "This due date is in your Google Calendar" if self.is_sent()
            else "Create a Google Calendar event for this due date"
        )
        self.button.style().unpolish(self.button)
        self.button.style().polish(self.button)

    def sent_menu(self):
        """The menu a sent card's button opens."""
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        from PySide6.QtWidgets import QMenu

        menu = QMenu(self)
        html_link = (self.link or {}).get("html_link") or ""
        open_action = menu.addAction("Open in Google Calendar")
        open_action.setEnabled(bool(html_link))
        open_action.triggered.connect(lambda: QDesktopServices.openUrl(QUrl(html_link)))
        menu.addAction("Send again…").triggered.connect(self.resend_requested.emit)
        return menu

    def _on_clicked(self):
        if not self.is_sent():
            self.send_requested.emit()
            return
        menu = self.sent_menu()
        menu.exec(self.button.mapToGlobal(self.button.rect().bottomLeft()))
        menu.deleteLater()

    def set_scale(self, scale: float):
        text_qss = (
            "QLabel { color: #FFF0BF; background: transparent; border: none;"
            f" font-family: 'Courier New'; font-size: {max(8.0, 10.5 * scale):.1f}pt; }}"
        )
        self.title_label.setStyleSheet(text_qss)
        self.detail_label.setStyleSheet(text_qss)
        pt = max(7.5, 9.5 * scale)
        self.button.setStyleSheet(_DUE_BUTTON_QSS.replace("__PT__", f"{pt:.1f}"))
        self.button.setFixedHeight(max(26, round(30 * scale)))
        self.button.setMinimumWidth(round(170 * scale))
        self.title_label.fit()
        self.detail_label.fit()


class PixelDueDateList(QWidget):
    """The Due Dates section body: one :class:`PixelDueDateCard` per entry,
    separated by a rule like the one the rich-text body draws between entries."""

    def __init__(self, cards, parent=None):
        super().__init__(parent)
        self.cards = list(cards)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        for index, card in enumerate(self.cards):
            if index:
                rule = QWidget()
                rule.setAttribute(Qt.WA_StyledBackground, True)
                rule.setFixedHeight(2)
                rule.setStyleSheet("QWidget { background: #254D9C; }")
                layout.addWidget(rule)
            layout.addWidget(card)

    def set_scale(self, scale: float):
        for card in self.cards:
            card.set_scale(scale)


# =========================
# All Sessions browser (BU102)
# =========================

LIVE_RED = theme.qcolor("#FF6B5E")
PAUSED_AMBER = theme.qcolor("#F2B84B")
CARD_FILL_HOVER = theme.qcolor("#0B2762")
CARD_FILL_SELECTED = theme.qcolor("#12306E")


def _label_qss(color: str, pt: float, bold: bool = False, extra: str = "") -> str:
    # Font sizes must live in the widget's own stylesheet: the app-wide QSS
    # declares a font-size for QWidget, which beats setFont.
    weight = " font-weight: 700;" if bold else ""
    return (
        f"QLabel {{ color: {color}; background: transparent; border: none;"
        f" font-family: 'Courier New'; font-size: {pt:.1f}pt;{weight} {extra} }}"
    )


class PixelElidedLabel(QLabel):
    """Single-line label that elides its text with "…" to the width it gets,
    so long session names never push the rest of a card row off screen."""

    def __init__(self, text: str = "", parent=None):
        super().__init__(parent)
        self._full_text = text
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setMinimumWidth(40)
        self._elide()

    def full_text(self) -> str:
        return self._full_text

    def set_full_text(self, text: str):
        self._full_text = text
        self._elide()

    def _elide(self):
        width = max(self.width(), 40)
        super().setText(self.fontMetrics().elidedText(self._full_text, Qt.ElideRight, width))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._elide()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() in (QEvent.FontChange, QEvent.StyleChange):
            self._elide()


class PixelLiveBadge(QLabel):
    """"● LIVE" / "❚❚ PAUSED" badge for the session being captured right now.
    While recording, `pulse()` blinks the dot so the badge reads as live."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTextFormat(Qt.RichText)
        self._state = None
        self._dim = False
        self.setVisible(False)

    def state(self):
        return self._state

    def set_state(self, state):
        """`state` is "recording", "paused" or None (badge hidden)."""
        self._state = state
        self._dim = False
        self.setVisible(state is not None)
        self._restyle()

    def pulse(self):
        if self._state == "recording":
            self._dim = not self._dim
            self._restyle()

    def _restyle(self):
        if self._state == "recording":
            dot = "#7A3434" if self._dim else LIVE_RED.name()
            self.setText(f'<span style="color:{dot};">●</span>&nbsp;LIVE')
            self.setToolTip("This session is recording right now")
            self.setStyleSheet(_label_qss(
                "#FFD9D3", 8.5, bold=True,
                extra=f"background: #3A1430; border: 1px solid {LIVE_RED.name()};"
                " border-radius: 4px; padding: 2px 7px;"
            ))
        elif self._state == "paused":
            self.setText("❚❚&nbsp;PAUSED")
            self.setToolTip("This session is paused - resume it to keep recording")
            self.setStyleSheet(_label_qss(
                PAUSED_AMBER.name(), 8.5, bold=True,
                extra=f"background: #3A2E14; border: 1px solid {PAUSED_AMBER.name()};"
                " border-radius: 4px; padding: 2px 7px;"
            ))


class PixelStatusChip(QLabel):
    """Small status pill for a processing step (transcript / summary)."""

    _STYLES = {
        "ready": ("✓", "#8FE39B", "#10362F", "1px solid #3C9A6B"),
        "pending": ("○", "#8EA7D8", "transparent", "1px dashed #3A5C9E"),
        "busy": ("…", "#FFE9A8", "#3A3320", "1px solid #B89A4E"),
    }

    def __init__(self, label: str, parent=None):
        super().__init__(parent)
        self._label = label
        self.setAlignment(Qt.AlignCenter)
        self.set_state("pending")

    def set_state(self, state: str, text: str = ""):
        glyph, fg, bg, border = self._STYLES[state]
        self.setText(f"{glyph} {text or self._label}")
        self.setStyleSheet(_label_qss(
            fg, 8.5, bold=True,
            extra=f"background: {bg}; border: {border}; border-radius: 4px; padding: 3px 8px;"
        ))
        if state == "ready":
            self.setToolTip(f"{self._label} ready")
        elif state == "pending":
            self.setToolTip(f"No {self._label.lower()} yet")
        else:
            self.setToolTip(text or self._label)


def pixel_filter_chip(text: str, compact: bool = False) -> QToolButton:
    """Checkable pixel pill used as one option of a segmented filter.

    ``compact`` trims the side padding, for rows that decide how narrow a
    window can get.
    """
    btn = QToolButton()
    btn.setText(text)
    btn.setCheckable(True)
    btn.setCursor(Qt.PointingHandCursor)
    btn.setFocusPolicy(Qt.NoFocus)
    btn.setMinimumHeight(36)
    btn.setStyleSheet(
        """
        QToolButton {
            color: #FFF0BF;
            background: #274F9B;
            border: 2px solid #3A67C7;
            border-radius: 6px;
            padding: 4px %dpx;"""
        % (7 if compact else 12) + """
            font-family: 'Courier New';
            font-size: 9pt;
            font-weight: 700;
        }
        QToolButton:hover { background: #315DB1; }
        QToolButton:checked {
            color: #071846;
            background: #F6E0A6;
            border: 2px solid #FFEFC1;
        }
        """
    )
    return btn


def pixel_group_header(text: str, count: int = 0) -> QWidget:
    """Day divider for a list of cards: spaced title, a count and a rule."""
    widget = QWidget()
    layout = QHBoxLayout(widget)
    layout.setContentsMargins(2, 8, 2, 0)
    layout.setSpacing(10)

    title = QLabel(text.upper())
    spaced = QFont("Courier New")
    spaced.setBold(True)
    spaced.setLetterSpacing(QFont.AbsoluteSpacing, 1.5)
    title.setFont(spaced)
    title.setStyleSheet(_label_qss("#FFE9A8", 9, bold=True))
    layout.addWidget(title, 0)

    if count:
        count_label = QLabel(f"{count}")
        count_label.setStyleSheet(_label_qss("#8EA7D8", 9))
        layout.addWidget(count_label, 0)

    rule = QWidget()
    rule.setFixedHeight(2)
    rule.setAttribute(Qt.WA_StyledBackground, True)
    rule.setStyleSheet("background: #1E3F82;")
    layout.addWidget(rule, 1, Qt.AlignVCenter)
    return widget


class PixelShotsChip(QWidget):
    """How many screenshots a session has: the app's pixel camera icon and a
    small number. Hidden when the session has none."""

    ICON_SIZE = 18
    COUNT_PT = 7.5
    ICON_FILE = "icon_camera.svg"
    NOUN = "screenshot"

    clicked = Signal()

    def __init__(self, count: int = 0, parent=None):
        super().__init__(parent)
        self.setCursor(Qt.PointingHandCursor)
        self.setObjectName("PixelShotsChip")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(
            "QWidget#PixelShotsChip { background: #1E3F82; border: 1px solid #3A67C7;"
            " border-radius: 4px; }"
        )
        self.icon_label = QLabel()
        self.icon_label.setStyleSheet("QLabel { background: transparent; border: none; }")
        self.icon_label.setPixmap(QIcon(asset_path(self.ICON_FILE)).pixmap(
            QSize(self.ICON_SIZE, self.ICON_SIZE)
        ))
        self.count_label = QLabel()
        self.count_label.setStyleSheet(_label_qss("#FFE9A8", self.COUNT_PT, bold=True))
        # Two digits' worth of room, so the chip is the same width on every card.
        count_font = QFont("Courier New")
        count_font.setPointSizeF(self.COUNT_PT)
        count_font.setBold(True)
        self.count_label.setMinimumWidth(QFontMetrics(count_font).horizontalAdvance("99"))

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 0, 7, 0)
        layout.setSpacing(4)
        layout.addWidget(self.icon_label, 0, Qt.AlignVCenter)
        layout.addWidget(self.count_label, 0, Qt.AlignVCenter)
        self.set_count(count)

    def set_count(self, count: int):
        count = max(0, int(count or 0))
        self.count_label.setText(str(count))
        self.setToolTip(f"{count} {self.NOUN}{'s' if count != 1 else ''} - click to open")
        # Showing a chip that has no parent yet would open it as a top-level
        # window (and PixelWindowChrome would give it a title bar); unhidden,
        # it simply appears with the card it is placed in.
        if count <= 0:
            self.hide()
        elif self.parentWidget() is not None:
            self.show()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
            event.accept()  # do not also select the card underneath
            return
        super().mousePressEvent(event)


class PixelDocsChip(PixelShotsChip):
    """Like the screenshots chip, for a session's documents."""

    ICON_FILE = "icon_document.svg"
    NOUN = "document"


class PixelSessionCard(QWidget):
    """One session in the All Sessions browser: name, time, processing chips,
    an Open button and a "•••" actions button (its menu is set by the owner).

    A session that is recording (or paused) right now gets a LIVE / PAUSED
    badge, a coloured side stripe and matching border.
    """

    clicked = Signal()
    open_requested = Signal()
    screenshots_requested = Signal()
    documents_requested = Signal()

    def __init__(self, name: str, meta: str, trans_ready: bool, sum_ready: bool,
                 live_state=None, shot_count: int = 0, doc_count: int = 0,
                 parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_Hover, True)
        self.setAutoFillBackground(False)
        self.setCursor(Qt.PointingHandCursor)
        self._hover = False
        self._selected = False
        self._live_state = None

        self.live_badge = PixelLiveBadge()
        self.name_label = PixelElidedLabel(name)
        self.name_label.setToolTip(name)
        self.name_label.setStyleSheet(_label_qss("#FFF0BF", 11, bold=True))
        self.meta_label = QLabel(meta)
        self.meta_label.setStyleSheet(_label_qss("#8EA7D8", 9))

        name_row = QHBoxLayout()
        name_row.setSpacing(8)
        name_row.addWidget(self.live_badge, 0)
        name_row.addWidget(self.name_label, 1)

        text_col = QVBoxLayout()
        text_col.setSpacing(4)
        text_col.addLayout(name_row)
        text_col.addWidget(self.meta_label)

        self.transcript_chip = PixelStatusChip("Transcript")
        self.summary_chip = PixelStatusChip("Summary")
        chip_font = QFont("Courier New")
        chip_font.setPointSizeF(8.5)
        chip_font.setBold(True)
        metrics = QFontMetrics(chip_font)
        # Room for the "busy" wording so a chip doesn't jump while it works.
        self.transcript_chip.setMinimumWidth(metrics.horizontalAdvance("… Transcribing") + 18)
        self.summary_chip.setMinimumWidth(metrics.horizontalAdvance("… Summarizing") + 18)
        self.shots_chip = PixelShotsChip(shot_count)
        self.shots_chip.setFixedHeight(self.transcript_chip.sizeHint().height())
        self.shots_chip.clicked.connect(self.screenshots_requested.emit)
        self.docs_chip = PixelDocsChip(doc_count)
        self.docs_chip.setFixedHeight(self.transcript_chip.sizeHint().height())
        self.docs_chip.clicked.connect(self.documents_requested.emit)

        self.open_button = QToolButton()
        self.open_button.setText("Open")
        self.open_button.setToolTip("Load this session into the assistant")
        self.open_button.setCursor(Qt.PointingHandCursor)
        self.open_button.setFocusPolicy(Qt.NoFocus)
        self.open_button.setFixedHeight(32)
        self.open_button.setStyleSheet(
            """
            QToolButton {
                color: #071846;
                background: #F6E0A6;
                border: 2px solid #FFEFC1;
                border-radius: 5px;
                padding: 0px 14px;
                font-family: 'Courier New';
                font-size: 9.5pt;
                font-weight: 700;
            }
            QToolButton:hover { background: #FFE7B4; }
            QToolButton:pressed { background: #E8CF8E; }
            """
        )
        self.open_button.clicked.connect(self.open_requested.emit)

        self.actions_button = QToolButton()
        self.actions_button.setText("•••")
        self.actions_button.setToolTip("More actions")
        self.actions_button.setCursor(Qt.PointingHandCursor)
        self.actions_button.setFocusPolicy(Qt.NoFocus)
        self.actions_button.setPopupMode(QToolButton.InstantPopup)
        self.actions_button.setFixedSize(40, 32)
        self.actions_button.setStyleSheet(
            """
            QToolButton {
                color: #FFF0BF;
                background: #274F9B;
                border: 2px solid #3A67C7;
                border-radius: 5px;
                font-family: 'Courier New';
                font-size: 10pt;
                font-weight: 700;
            }
            QToolButton:hover { background: #315DB1; }
            QToolButton:pressed { background: #1E3F82; }
            QToolButton::menu-indicator { image: none; width: 0px; }
            """
        )

        layout = QHBoxLayout(self)
        layout.setContentsMargins(22, 10, 14, 10)
        layout.setSpacing(8)
        layout.addLayout(text_col, 1)
        layout.addWidget(self.docs_chip, 0, Qt.AlignVCenter)
        layout.addWidget(self.shots_chip, 0, Qt.AlignVCenter)
        layout.addWidget(self.transcript_chip, 0, Qt.AlignVCenter)
        layout.addWidget(self.summary_chip, 0, Qt.AlignVCenter)
        layout.addSpacing(6)
        layout.addWidget(self.open_button, 0, Qt.AlignVCenter)
        layout.addWidget(self.actions_button, 0, Qt.AlignVCenter)

        self.setMinimumHeight(68)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.set_status(trans_ready, sum_ready)
        self.set_live_state(live_state)

    # --- state -----------------------------------------------------------

    def set_status(self, trans_ready: bool, sum_ready: bool):
        self.transcript_chip.set_state("ready" if trans_ready else "pending")
        self.summary_chip.set_state("ready" if sum_ready else "pending")

    def set_shot_count(self, count: int):
        self.shots_chip.set_count(count)

    def set_doc_count(self, count: int):
        self.docs_chip.set_count(count)

    def set_meta(self, text: str):
        if self.meta_label.text() != text:
            self.meta_label.setText(text)

    def set_live_state(self, state):
        """"recording", "paused" or None."""
        self._live_state = state
        self.live_badge.set_state(state)
        self.update()

    def live_state(self):
        return self._live_state

    def set_selected(self, value: bool):
        value = bool(value)
        if value != self._selected:
            self._selected = value
            self.update()

    # --- events ----------------------------------------------------------

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

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.open_requested.emit()
        super().mouseDoubleClickEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, False)
        rect = self.rect().adjusted(1, 1, -2, -2)
        if rect.width() <= 0 or rect.height() <= 0:
            return

        live_color = None
        if self._live_state == "recording":
            live_color = LIVE_RED
        elif self._live_state == "paused":
            live_color = PAUSED_AMBER

        if self._selected:
            fill = CARD_FILL_SELECTED
        elif self._hover:
            fill = CARD_FILL_HOVER
        else:
            fill = NAVY_INNER

        if self._selected:
            border = CREAM_BORDER
        elif live_color is not None:
            border = live_color
        elif self._hover:
            border = BORDER_BLUE_ACTIVE
        else:
            border = BORDER_BLUE

        path = pixel_round_rect_path(rect.x(), rect.y(), rect.width(), rect.height(), 7)
        painter.setBrush(QBrush(fill))
        painter.setPen(QPen(border, 2))
        painter.drawPath(path)

        # Left stripe: live colour for the ongoing session, a quiet blue otherwise.
        stripe = live_color if live_color is not None else (
            CREAM if self._selected else BORDER_BLUE_LIGHT
        )
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(stripe))
        painter.drawRect(rect.x() + 8, rect.y() + 12, 4, max(0, rect.height() - 24))

        super().paintEvent(event)


def search_snippet(text: str, terms, width: int = 180) -> str:
    """A ``width``-character window of ``text`` around its first match.

    Whitespace is collapsed first. When the earliest occurrence of any term
    sits past the start, the window shifts so the hit lands about a third of
    the way in, and "…" marks each cut side - a hit deep inside a long
    transcript chunk is still visible in the result card.
    """
    flat = " ".join((text or "").split())
    if len(flat) <= width:
        return flat
    lowered = flat.lower()
    hits = [lowered.find(t) for t in (terms or []) if t and t in lowered]
    first = min(hits) if hits else 0
    start = max(0, min(first - width // 3, len(flat) - width))
    # Don't start or end in the middle of a word.
    if start > 0:
        space = flat.find(" ", start)
        if space != -1 and space < first:
            start = space + 1
    end = min(len(flat), start + width)
    if end < len(flat):
        space = flat.rfind(" ", start, end)
        if space > first:
            end = space
    snippet = flat[start:end].strip()
    return ("…" if start > 0 else "") + snippet + ("…" if end < len(flat) else "")


class PixelSearchResultCard(QWidget):
    """One hit in the Search window: a kind badge, a title, a meta line on the
    right and a snippet with the searched words highlighted.

    Click (or Enter while selected) emits ``activated``; the owner decides
    what opening a hit means for its kind.
    """

    activated = Signal()

    # kind -> (badge text, fg, bg, border)
    _KINDS = {
        "transcript": ("TRANSCRIPT", "#FFE9A8", "#1E3F82", "#3A67C7"),
        "summary": ("SUMMARY", "#8FE39B", "#10362F", "#3C9A6B"),
        "chat": ("CHAT", "#6FD3FF", "#0E3350", "#2F8FC0"),
    }

    def __init__(self, kind: str, title: str, meta: str, snippet: str, terms,
                 show_title: bool = True, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_Hover, True)
        self.setAutoFillBackground(False)
        self.setCursor(Qt.PointingHandCursor)
        self._hover = False
        self._selected = False

        badge_text, fg, bg, border = self._KINDS.get(kind, self._KINDS["transcript"])
        self.badge = QLabel(badge_text)
        self.badge.setStyleSheet(_label_qss(
            fg, 7.5, bold=True,
            extra=f"background: {bg}; border: 1px solid {border};"
            " border-radius: 4px; padding: 1px 6px;"
        ))

        self.title_label = PixelElidedLabel(title)
        self.title_label.setToolTip(title)
        self.title_label.setStyleSheet(_label_qss("#FFF0BF", 10.5, bold=True))

        self.meta_label = QLabel(meta)
        self.meta_label.setStyleSheet(_label_qss("#8EA7D8", 9))

        head = QHBoxLayout()
        head.setSpacing(8)
        head.addWidget(self.badge, 0, Qt.AlignVCenter)
        head.addWidget(self.title_label, 1, Qt.AlignVCenter)
        if not show_title:
            head.addStretch(1)
        head.addWidget(self.meta_label, 0, Qt.AlignVCenter)

        bg_hl, fg_hl = FIND_TERM_ON_BLUE
        self.snippet_label = QLabel()
        self.snippet_label.setTextFormat(Qt.RichText)
        self.snippet_label.setWordWrap(True)
        self.snippet_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.snippet_label.setText(highlight_terms_html(snippet, terms, bg_hl, fg_hl))
        self.snippet_label.setStyleSheet(_label_qss("#D5DFF5", 10, extra="line-height: 130%;"))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 9, 14, 10)
        layout.setSpacing(5)
        layout.addLayout(head)
        layout.addWidget(self.snippet_label)
        # Only hide, never show, before the card has a parent: showing a
        # parentless label opens it as a blank top-level window.
        if not show_title:
            self.title_label.hide()
        if not snippet:
            self.snippet_label.hide()
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)

    def set_selected(self, value: bool):
        value = bool(value)
        if value != self._selected:
            self._selected = value
            self.update()

    def enterEvent(self, event):
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self.rect().contains(event.position().toPoint()):
            self.activated.emit()
        super().mouseReleaseEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, False)
        rect = self.rect().adjusted(1, 1, -2, -2)
        if rect.width() <= 0 or rect.height() <= 0:
            return
        if self._selected:
            fill, border, stripe = CARD_FILL_SELECTED, CREAM_BORDER, CREAM
        elif self._hover:
            fill, border, stripe = CARD_FILL_HOVER, BORDER_BLUE_ACTIVE, BORDER_BLUE_LIGHT
        else:
            fill, border, stripe = NAVY_INNER, BORDER_BLUE, BORDER_BLUE_LIGHT
        path = pixel_round_rect_path(rect.x(), rect.y(), rect.width(), rect.height(), 7)
        painter.setBrush(QBrush(fill))
        painter.setPen(QPen(border, 2))
        painter.drawPath(path)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(stripe))
        painter.drawRect(rect.x() + 8, rect.y() + 10, 4, max(0, rect.height() - 20))
        super().paintEvent(event)


class PixelAnswerCard(QWidget):
    """One answer in the detached window's left rail (BU113).

    A card owns its own lifecycle: it is posted in the ``pending`` state as
    soon as the user asks, then either fills with the answer or shows a failure
    with a Retry. The card never talks to the assistant itself - MainWindow
    drives it through ``set_answer`` / ``set_error`` and listens to the three
    signals.
    """

    retry_requested = Signal()
    dismiss_requested = Signal()
    timestamp_clicked = Signal()

    def __init__(self, question: str, time_range: str, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setObjectName("PixelAnswerCard")
        self.setStyleSheet(
            """
            QWidget#PixelAnswerCard {
                background: #0B2A6B;
                border: 2px solid #3A67C7;
            }
            """
        )
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(8)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(6)

        self.time_button = QToolButton()
        self.time_button.setText(time_range or "--:--:--")
        self.time_button.setCursor(Qt.PointingHandCursor)
        self.time_button.setToolTip("Scroll the transcript to these chunks")
        time_font = QFont("Courier New")
        time_font.setPointSize(9)
        time_font.setBold(True)
        time_font.setLetterSpacing(QFont.AbsoluteSpacing, 1)
        self.time_button.setFont(time_font)
        self.time_button.setStyleSheet(
            """
            QToolButton {
                color: #7FD4FF;
                background: transparent;
                border: none;
                padding: 0px;
                text-align: left;
            }
            QToolButton:hover { color: #BFE9FF; }
            """
        )
        self.time_button.clicked.connect(self.timestamp_clicked)
        header.addWidget(self.time_button, 0)
        header.addStretch(1)

        self.retry_button = pixel_mini_button("\u21BB", "Ask again", width=28, height=24)
        self.retry_button.clicked.connect(self.retry_requested)
        self.retry_button.hide()
        header.addWidget(self.retry_button, 0)

        self.dismiss_button = pixel_mini_button("\u2715", "Dismiss", width=28, height=24)
        self.dismiss_button.clicked.connect(self.dismiss_requested)
        header.addWidget(self.dismiss_button, 0)
        layout.addLayout(header)

        self.question_label = QLabel(question)
        self.question_label.setWordWrap(True)
        self.question_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.question_label.setStyleSheet(
            _label_qss("#FFE9A8", 9, bold=True, extra="background: transparent;")
        )
        layout.addWidget(self.question_label)

        self.body_label = QLabel("Thinking...")
        self.body_label.setWordWrap(True)
        self.body_label.setTextFormat(Qt.RichText)
        self.body_label.setTextInteractionFlags(
            Qt.TextSelectableByMouse | Qt.LinksAccessibleByMouse
        )
        self.body_label.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        # BU116: answers are now one or two sentences. The card takes the
        # height its text needs and no more, instead of holding a paragraph's
        # worth of room open under a single line.
        self.body_label.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        self.body_label.setStyleSheet(
            _label_qss("#D8E3FF", 10, extra="background: transparent;")
        )
        layout.addWidget(self.body_label)

        # BU151: which session documents the answer used; hidden until it did.
        self.document_badge_row = QWidget()
        badge_layout = QHBoxLayout(self.document_badge_row)
        badge_layout.setContentsMargins(0, 0, 0, 0)
        self._document_badge = None
        self._document_badge_layout = badge_layout
        self.document_badge_row.hide()
        layout.addWidget(self.document_badge_row)

    def set_answer(self, html: str):
        """Fill the card with the rendered answer."""
        self.retry_button.hide()
        self.body_label.setStyleSheet(
            _label_qss("#D8E3FF", 10, extra="background: transparent;")
        )
        self.body_label.setText(html)

    def set_document_badge(self, names):
        """Badge the documents the answer used; no names clears it (BU151)."""
        if self._document_badge is not None:
            self._document_badge_layout.removeWidget(self._document_badge)
            self._document_badge.deleteLater()
            self._document_badge = None
        names = [n for n in names if n]
        if names:
            self._document_badge = PixelDocumentBadge(names)
            self._document_badge_layout.addWidget(self._document_badge, 0, Qt.AlignLeft)
            self._document_badge_layout.addStretch(1)
        self.document_badge_row.setVisible(bool(names))

    def set_error(self, message: str):
        """Show a failure the user can retry from."""
        self.retry_button.show()
        self.body_label.setStyleSheet(
            _label_qss("#FFB4A8", 10, extra="background: transparent;")
        )
        self.body_label.setText(html_escape.escape(message))

    def set_time_range(self, time_range: str):
        self.time_button.setText(time_range or "--:--:--")


class PixelCandidateCard(QWidget):
    """A detected question waiting on the user, in the answers rail (BU115).

    The second card kind in that rail. It shows what the detector heard and
    offers one click to answer it; ``to_answer_card_fields`` hands MainWindow
    what it needs to replace this card with a real ``PixelAnswerCard`` in
    place. Deliberately does not answer anything itself.
    """

    answer_requested = Signal()
    dismiss_requested = Signal()
    timestamp_clicked = Signal()

    _KIND_COLORS = {
        "genuine": "#8FE39B",
        "request": "#FFE9A8",
        "rhetorical": "#8EA7D8",
        "discourse": "#8EA7D8",
    }

    def __init__(self, question: str, time_text: str, asker: str, kind: str,
                 parent=None):
        super().__init__(parent)
        self.question_text = question
        self.time_text = time_text
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setObjectName("PixelCandidateCard")
        # A dashed border, against the answer card's solid one: this is a
        # suggestion the user has not acted on yet, and it should not look like
        # something that already happened.
        self.setStyleSheet(
            """
            QWidget#PixelCandidateCard {
                background: #0A2359;
                border: 2px dashed #4A78D8;
            }
            """
        )
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(8)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(8)

        self.time_button = QToolButton()
        self.time_button.setText(time_text or "--:--:--")
        self.time_button.setCursor(Qt.PointingHandCursor)
        self.time_button.setToolTip("Scroll the transcript to these chunks")
        time_font = QFont("Courier New")
        time_font.setPointSize(9)
        time_font.setBold(True)
        time_font.setLetterSpacing(QFont.AbsoluteSpacing, 1)
        self.time_button.setFont(time_font)
        self.time_button.setStyleSheet(
            """
            QToolButton {
                color: #7FD4FF;
                background: transparent;
                border: none;
                padding: 0px;
                text-align: left;
            }
            QToolButton:hover { color: #BFE9FF; }
            """
        )
        self.time_button.clicked.connect(self.timestamp_clicked)
        header.addWidget(self.time_button, 0)

        self.meta_label = QLabel(f"{asker}  ·  {kind}")
        self.meta_label.setStyleSheet(_label_qss(
            self._KIND_COLORS.get(kind, "#8EA7D8"), 8, bold=True,
            extra="background: transparent;",
        ))
        header.addWidget(self.meta_label, 0)
        header.addStretch(1)

        self.dismiss_button = pixel_mini_button("\u2715", "Dismiss", width=28, height=24)
        self.dismiss_button.clicked.connect(self.dismiss_requested)
        header.addWidget(self.dismiss_button, 0)
        layout.addLayout(header)

        self.question_label = QLabel(question)
        self.question_label.setWordWrap(True)
        self.question_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.question_label.setStyleSheet(
            _label_qss("#FFF0BF", 10, extra="background: transparent;")
        )
        layout.addWidget(self.question_label)

        self.answer_button = PixelButton("Answer")
        self.answer_button.setMinimumHeight(30)
        self.answer_button.clicked.connect(self.answer_requested)
        layout.addWidget(self.answer_button)


def pixel_spend_chip(text: str) -> QLabel:
    """Detector calls and estimated spend for this session (BU115)."""
    label = QLabel(text)
    label.setAlignment(Qt.AlignCenter)
    label.setStyleSheet(_label_qss(
        "#8EA7D8", 8, bold=True,
        extra="background: #10306E; border: 1px solid #3A5C9E;"
              " border-radius: 4px; padding: 3px 8px;",
    ))
    label.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
    return label


REMOVED_DOCUMENT_LABEL = "(removed document)"
BADGE_MAX_CHARS = 40


def document_badge_text(names) -> str:
    """``From: a.pdf, b.md`` for the badge, elided to ``BADGE_MAX_CHARS``."""
    text = "From: " + ", ".join(names)
    if len(text) > BADGE_MAX_CHARS:
        text = text[:BADGE_MAX_CHARS - 1].rstrip(" ,") + "…"
    return text


class PixelDocumentBadge(QWidget):
    """Under an answer: which session documents it was built from (BU150).

    A chip, not text in the bubble, so the answer reads the same with or
    without it. Same green chip vocabulary as the BU117 reference chip it replaced. Long lists are
    elided; the tooltip holds the full one.
    """

    def __init__(self, names, parent=None):
        super().__init__(parent)
        names = list(names)
        self.setObjectName("PixelDocumentBadge")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(
            "QWidget#PixelDocumentBadge { background: #123A1E;"
            " border: 1px solid #4E9A63; border-radius: 4px; }"
        )
        self.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        self.setToolTip("Documents this answer used: " + ", ".join(names))
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 3, 8, 3)
        layout.setSpacing(6)
        self.label = QLabel("📄 " + document_badge_text(names))
        self.label.setStyleSheet(_label_qss("#A8E3B4", 8, bold=True))
        layout.addWidget(self.label)
        self.names = names


class PixelDocumentsChip(QToolButton):
    """"📄 N documents" beside the scope label; opens the Documents pop-up.

    Hidden for zero - no empty placeholder, no "0 documents".
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("PixelDocumentsChip")
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("This session's documents - click to manage")
        self.setStyleSheet(
            "QToolButton { color: #A8E3B4; background: #123A1E;"
            " border: 1px solid #4E9A63; border-radius: 4px; padding: 3px 8px;"
            " font-family: 'Courier New'; font-size: 8pt; font-weight: 700; }"
            "QToolButton:hover { color: #D6F5DD; }"
        )
        self.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        self.hide()

    def set_count(self, count: int):
        if count > 0:
            self.setText(f"📄 {count} document" + ("" if count == 1 else "s"))
            self.show()
        else:
            self.setText("")
            self.hide()


class PixelDropOverlay(QWidget):
    """The themed affordance shown while a file is dragged over the window.

    Qt's own drag highlight is a thin system-coloured rectangle that looks
    like nothing else in this app, and it says only "something is being
    dragged" - not whether *this* file will be taken. This one says which, in
    the pixel vocabulary, and in two states: accepting (gold) and refusing
    (red), so a dropped .pdf is turned away visibly instead of silently.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._text = ""
        self._accepting = True
        self.hide()

    def show_state(self, text: str, accepting: bool = True):
        self._text = text
        self._accepting = accepting
        self.show()
        self.raise_()
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, False)
        rect = self.rect().adjusted(8, 8, -9, -9)
        if rect.width() <= 0 or rect.height() <= 0:
            return

        border = theme.qcolor("#F6E0A6") if self._accepting else theme.qcolor("#FF8B7A")
        fill = QColor(7, 24, 70, 210)

        path = pixel_round_rect_path(rect.x(), rect.y(), rect.width(),
                                     rect.height(), 10)
        painter.setBrush(QBrush(fill))
        pen = QPen(border)
        pen.setWidth(3)
        pen.setStyle(Qt.DashLine)
        painter.setPen(pen)
        painter.drawPath(path)

        font = QFont("Courier New")
        font.setBold(True)
        font.setPointSize(12)
        font.setLetterSpacing(QFont.AbsoluteSpacing, 2)
        painter.setFont(font)
        painter.setPen(QPen(border))
        painter.drawText(rect, Qt.AlignCenter, self._text)


def pixel_group_label(text: str) -> QLabel:
    """A small caption naming what the chips beside it control (BU116).

    The answers rail now carries two chip groups. Without a name on each, the
    five chips read as one five-way control.
    """
    label = QLabel(text.upper())
    font = QFont("Courier New")
    font.setBold(True)
    font.setPointSize(8)
    font.setLetterSpacing(QFont.AbsoluteSpacing, 1.0)
    label.setFont(font)
    label.setStyleSheet(_label_qss("#6D7FB4", 8, bold=True,
                                   extra="background: transparent;"))
    label.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
    return label


def pixel_rail_notice(text: str) -> QLabel:
    """A themed notice in the answers rail - e.g. the detection cap is hit."""
    label = QLabel(text)
    label.setWordWrap(True)
    label.setAlignment(Qt.AlignCenter)
    label.setStyleSheet(_label_qss(
        "#FFE9A8", 9, bold=True,
        extra="background: #3A3320; border: 1px solid #B89A4E;"
              " border-radius: 4px; padding: 8px 10px;",
    ))
    label.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
    return label


# =========================
# Chat input
# =========================

class PixelChatInput(QTextEdit):
    """The assistant's question box: Enter sends, Shift+Enter breaks a line.

    Its scrollbars stay hidden - the bar is only two lines tall, and Qt's
    scrollbar popped up as a blue block beside the text as soon as a newline
    overflowed it. Longer text still scrolls with the caret and the wheel.
    """

    submitted = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptRichText(False)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and not (
            event.modifiers() & (Qt.ShiftModifier | Qt.ControlModifier)
        ):
            # A bare Enter on an empty box does nothing rather than asking
            # the assistant to complain about a missing question.
            if self.toPlainText().strip():
                self.submitted.emit()
            event.accept()
            return
        super().keyPressEvent(event)


# =========================
# Past-conversation cards (sidebar history)
# =========================

class PixelConversationDelegate(QStyledItemDelegate):
    """Paints a past-conversation card: a two-line title over a small date.

    The card box itself (fill, border, hover/selected states) still comes from
    the ``QListWidget::item`` QSS; only the text is drawn here. The title is
    the item's DisplayRole, the date string its ``DATE_ROLE``.
    """

    DATE_ROLE = Qt.UserRole + 1
    TITLE_LINES = 2
    # Border + padding + margin from QListWidget::item in pixel_theme.py.
    PAD_X = 12
    PAD_Y = 11
    DATE_GAP = 7
    TITLE_COLOR = theme.qcolor("#FFF0BF")
    DATE_COLOR = theme.qcolor("#A9BCE6")
    SELECTED_DATE_COLOR = theme.role_color("date_selected")

    def _fonts(self, option):
        title_font = QFont(option.font)
        title_font.setBold(True)
        date_font = QFont(option.font)
        date_font.setBold(False)
        date_font.setPointSizeF(max(6.5, title_font.pointSizeF() - 2.5))
        return title_font, date_font

    def sizeHint(self, option, index):
        title_font, date_font = self._fonts(option)
        title_h = QFontMetrics(title_font).lineSpacing() * self.TITLE_LINES
        date_h = QFontMetrics(date_font).height()
        return QSize(0, title_h + self.DATE_GAP + date_h + 2 * self.PAD_Y)

    def _wrap(self, text: str, metrics: QFontMetrics, width: int) -> list:
        """Word-wrap into at most TITLE_LINES lines, eliding the last one."""
        words = text.split()
        lines = []
        current = ""
        while words and len(lines) < self.TITLE_LINES - 1:
            word = words[0]
            candidate = f"{current} {word}".strip()
            if metrics.horizontalAdvance(candidate) <= width:
                current = candidate
                words.pop(0)
            elif current:
                lines.append(current)
                current = ""
            else:
                # A single word wider than the line: let the last line elide it.
                break
        if current:
            lines.append(current)
        rest = " ".join(words)
        if rest:
            if len(lines) >= self.TITLE_LINES:
                lines[-1] = metrics.elidedText(f"{lines[-1]} {rest}", Qt.ElideRight, width)
            else:
                lines.append(metrics.elidedText(rest, Qt.ElideRight, width))
        return lines

    def paint(self, painter, option, index):
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        title = opt.text
        opt.text = ""
        widget = opt.widget
        style = widget.style() if widget is not None else QApplication.style()
        style.drawControl(QStyle.CE_ItemViewItem, opt, painter, widget)

        title_font, date_font = self._fonts(option)
        rect = option.rect.adjusted(self.PAD_X, self.PAD_Y, -self.PAD_X, -self.PAD_Y)
        if rect.width() <= 0:
            return

        painter.save()
        title_metrics = QFontMetrics(title_font)
        painter.setFont(title_font)
        painter.setPen(self.TITLE_COLOR)
        y = rect.top()
        for line in self._wrap(title, title_metrics, rect.width()):
            painter.drawText(rect.left(), y + title_metrics.ascent(), line)
            y += title_metrics.lineSpacing()

        date = index.data(self.DATE_ROLE) or ""
        if date:
            date_metrics = QFontMetrics(date_font)
            painter.setFont(date_font)
            selected = bool(option.state & QStyle.State_Selected)
            painter.setPen(self.SELECTED_DATE_COLOR if selected else self.DATE_COLOR)
            date_y = rect.top() + title_metrics.lineSpacing() * self.TITLE_LINES + self.DATE_GAP
            painter.drawText(
                rect.left(), date_y + date_metrics.ascent(),
                date_metrics.elidedText(date, Qt.ElideRight, rect.width()),
            )
        painter.restore()


# =========================
# Detached transcripts window (BU112)
# =========================

def transcript_gap_separator(label: str) -> QWidget:
    """A break in the transcript stream: a centred label between two rules.

    Inserted when a long silence sits between two bubbles (BU112) so the jump
    in timestamps reads as a pause in the room rather than as missing
    transcript.
    """
    widget = QWidget()
    widget.setAttribute(Qt.WA_StyledBackground, False)
    widget.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)

    layout = QHBoxLayout(widget)
    layout.setContentsMargins(2, 2, 2, 2)
    layout.setSpacing(10)

    def _rule():
        rule = QWidget()
        rule.setFixedHeight(2)
        rule.setAttribute(Qt.WA_StyledBackground, True)
        rule.setStyleSheet("background: #1E3F82;")
        return rule

    text = QLabel(label.upper())
    spaced = QFont("Courier New")
    spaced.setBold(True)
    spaced.setPointSize(8)
    spaced.setLetterSpacing(QFont.AbsoluteSpacing, 1.5)
    text.setFont(spaced)
    text.setStyleSheet(_label_qss("#7E8FC2", 8, bold=True))

    layout.addWidget(_rule(), 1, Qt.AlignVCenter)
    layout.addWidget(text, 0)
    layout.addWidget(_rule(), 1, Qt.AlignVCenter)
    return widget


def pixel_empty_hint(text: str) -> QLabel:
    """Centred muted hint for a column that is intentionally empty."""
    label = QLabel(text)
    label.setWordWrap(True)
    label.setAlignment(Qt.AlignCenter)
    label.setStyleSheet(
        _label_qss("#6D7FB4", 9, extra="padding: 18px 14px;")
    )
    label.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
    return label



# =========================
# Frameless window chrome
# =========================

class PixelWindowButton(QToolButton):
    """Minimise / maximise / close button for the custom title bar.

    The glyph is painted as chunky pixel blocks instead of a font character
    so it matches the pixel-art icons. ``kind`` is "min", "max" or "close".
    """

    # The hit area fills the title bar's height and is wider than the glyph,
    # so the button is easy to hit without a box drawn around it.
    WIDTH = 40
    PX = 2  # one "pixel" of the glyph, in screen pixels

    def __init__(self, kind: str, parent=None):
        super().__init__(parent)
        self.kind = kind
        self.maximized = False
        self.setFixedSize(self.WIDTH, PixelTitleBar.HEIGHT)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self.setAttribute(Qt.WA_Hover, True)

    def set_maximized(self, maximized: bool):
        self.maximized = maximized
        self.update()

    def _glyph(self):
        """Cells (col, row) on a 7x7 grid for the current glyph."""
        if self.kind == "min":
            return [(c, 5) for c in range(1, 6)] + [(c, 6) for c in range(1, 6)]
        if self.kind == "close":
            cells = []
            for i in range(7):
                cells += [(i, i), (6 - i, i)]
            return cells
        if self.maximized:
            # Restore: a back box peeking out behind a front box.
            back = [(c, 0) for c in range(2, 7)] + [(6, r) for r in range(1, 5)] + [(2, 1)]
            front = ([(c, 2) for c in range(0, 5)] + [(c, 3) for c in range(0, 5)]
                     + [(c, 6) for c in range(0, 5)]
                     + [(0, r) for r in range(4, 6)] + [(4, r) for r in range(4, 6)])
            return back + front
        box = [(c, 0) for c in range(7)] + [(c, 1) for c in range(7)] + [(c, 6) for c in range(7)]
        box += [(0, r) for r in range(2, 6)] + [(6, r) for r in range(2, 6)]
        return box

    def paintEvent(self, event):
        p = QPainter(self)
        hovered = self.underMouse()
        pressed = self.isDown()
        # No background at rest: just the glyph. Hover fills the whole hit
        # area, red for close.
        fg = theme.qcolor("#FFE9A8")
        if hovered or pressed:
            bg = theme.qcolor("#B8323A") if self.kind == "close" else theme.qcolor("#274F9B")
            if pressed:
                bg = bg.darker(120)
            p.fillRect(self.rect(), bg)
            fg = theme.qcolor("#FFF0BF")

        b = self.PX
        x0 = (self.width() - 7 * b) // 2
        y0 = (self.height() - 7 * b) // 2
        for col, row in self._glyph():
            p.fillRect(x0 + col * b, y0 + row * b, b, b, fg)
        p.end()


class PixelTitleBar(QWidget):
    """Custom title bar for the frameless main window: drag to move,
    double-click to maximise, pixel-art window buttons on the right."""

    HEIGHT = 30

    def __init__(self, window, title: str = "Chronicle", parent=None):
        super().__init__(parent)
        self._window = window
        self.setFixedHeight(self.HEIGHT)

        self.title_label = QLabel(title)
        self.title_label.setStyleSheet(_label_qss(theme.role("title_bar_text"), 11, bold=True))
        self.title_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)

        self.min_button = PixelWindowButton("min")
        self.min_button.setToolTip("Minimize")
        self.min_button.clicked.connect(window.showMinimized)
        self.max_button = PixelWindowButton("max")
        self.max_button.setToolTip("Maximize")
        self.max_button.clicked.connect(self.toggle_maximized)
        self.close_button = PixelWindowButton("close")
        self.close_button.setToolTip("Close")
        self.close_button.clicked.connect(window.close)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.title_label, 0, Qt.AlignVCenter)
        layout.addStretch(1)
        layout.addWidget(self.min_button)
        layout.addWidget(self.max_button)
        layout.addWidget(self.close_button)

    def toggle_maximized(self):
        if self._window.isMaximized():
            self._window.showNormal()
        else:
            self._window.showMaximized()

    def sync_state(self):
        maximized = self._window.isMaximized()
        self.max_button.set_maximized(maximized)
        self.max_button.setToolTip("Restore" if maximized else "Maximize")

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            handle = self._window.windowHandle()
            if handle is not None:
                handle.startSystemMove()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.toggle_maximized()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class PixelResizeFrame(QWidget):
    """Backdrop of the frameless window. Its outer margin doubles as the
    resize grip: pressing within ``GRIP`` px of an edge starts a native
    resize in that direction."""

    GRIP = 6

    def __init__(self, window, parent=None):
        super().__init__(parent)
        self._window = window
        self.setMouseTracking(True)

    def _edges(self, pos):
        edges = Qt.Edges()
        if self._window.isMaximized():
            return edges
        g, w, h = self.GRIP, self.width(), self.height()
        if pos.x() <= g:
            edges |= Qt.LeftEdge
        if pos.x() >= w - g:
            edges |= Qt.RightEdge
        if pos.y() <= g:
            edges |= Qt.TopEdge
        if pos.y() >= h - g:
            edges |= Qt.BottomEdge
        return edges

    def mouseMoveEvent(self, event):
        # The cursor itself is driven by PixelWindowChrome's application-wide
        # tracker, which also sees the moves that this frame's children
        # swallow - setting it here would leave it stuck on the first child
        # the pointer crossed on its way back inside.
        sync_resize_cursor(self._window, self.GRIP)
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            edges = self._edges(event.position().toPoint())
            handle = self._window.windowHandle()
            if edges and handle is not None:
                handle.startSystemResize(edges)
                event.accept()
                return
        super().mousePressEvent(event)


class PixelWindowChrome(QObject):
    """Application-wide event filter that gives every top-level window and
    dialog the frameless pixel-art chrome (title bar + edge resize).

    It hooks QEvent.Polish, which Qt sends inside show() before the window
    maps, so the window flags can still be changed without a flicker. The
    title bar is an overlay child pinned to the top edge; the window's layout
    top margin is pushed down to make room for it, and its side/bottom
    margins double as the resize grip.

    Windows that build their own title bar (the main window) set the
    ``pixelChrome`` property beforehand and are left alone.
    """

    GRIP = 6

    def eventFilter(self, obj, event):
        etype = event.type()
        if etype == QEvent.Polish:
            if isinstance(obj, QWidget) and self._wants_chrome(obj):
                self._apply(obj)
            return False

        if etype in _CURSOR_EVENTS and isinstance(obj, QWidget):
            # Mouse moves are delivered to the child under the pointer, not to
            # the window, so the window only ever hears about its own margin.
            # Tracking every widget's moves is what lets the resize cursor go
            # back off once the pointer leaves the edge.
            win = obj.window()
            if win is not None and win.property("pixelChrome") is not None:
                sync_resize_cursor(win, self.GRIP)

        bar = getattr(obj, "_pixel_title_bar", None) if isinstance(obj, QWidget) else None
        if bar is None:
            return False
        if etype == QEvent.ParentChange and not obj.isWindow():
            # Polished while parentless, then placed in a layout: it is a
            # child widget now, so the window chrome comes back off.
            self._remove(obj)
            return False
        if etype == QEvent.Resize:
            bar.setGeometry(0, 0, obj.width(), PixelTitleBar.HEIGHT)
            bar.raise_()
        elif etype == QEvent.WindowTitleChange:
            bar.title_label.setText(obj.windowTitle())
        elif etype == QEvent.WindowStateChange:
            bar.sync_state()
        elif etype == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
            edges = _edges_at(obj, event.position().toPoint(), self.GRIP)
            handle = obj.windowHandle()
            if edges and handle is not None:
                handle.startSystemResize(edges)
                return True
        return False

    @staticmethod
    def _wants_chrome(widget) -> bool:
        if not widget.isWindow() or widget.property("pixelChrome") is not None:
            return False
        return widget.windowType() in (Qt.Window, Qt.Dialog)

    def _apply(self, win):
        win.setProperty("pixelChrome", True)
        win.setWindowFlags(win.windowFlags() | Qt.FramelessWindowHint)
        win.setMouseTracking(True)

        bar = PixelTitleBar(win, win.windowTitle() or "Chronicle", parent=win)
        bar.setAttribute(Qt.WA_StyledBackground, True)
        bar.setStyleSheet("PixelTitleBar { background: #0E2A6B; }")
        if isinstance(win, QDialog):
            # Minimising a dialog on its own just loses it behind its parent.
            bar.min_button.hide()
        win._pixel_title_bar = bar

        layout = win.layout()
        if layout is not None:
            m = layout.contentsMargins()
            win._pixel_chrome_margins = m
            layout.setContentsMargins(
                max(m.left(), self.GRIP),
                m.top() + PixelTitleBar.HEIGHT,
                max(m.right(), self.GRIP),
                max(m.bottom(), self.GRIP),
            )
        bar.setGeometry(0, 0, max(win.width(), 1), PixelTitleBar.HEIGHT)
        bar.raise_()
        bar.show()

    @staticmethod
    def _remove(widget):
        bar = widget._pixel_title_bar
        widget._pixel_title_bar = None
        bar.hide()
        bar.deleteLater()
        margins = getattr(widget, "_pixel_chrome_margins", None)
        if margins is not None and widget.layout() is not None:
            widget.layout().setContentsMargins(margins)


def _edges_at(win, pos, grip):
    edges = Qt.Edges()
    if win.isMaximized():
        return edges
    w, h = win.width(), win.height()
    if pos.x() <= grip:
        edges |= Qt.LeftEdge
    if pos.x() >= w - grip:
        edges |= Qt.RightEdge
    if pos.y() <= grip:
        edges |= Qt.TopEdge
    if pos.y() >= h - grip:
        edges |= Qt.BottomEdge
    return edges


_CURSOR_EVENTS = (
    QEvent.MouseMove,
    QEvent.HoverMove,
    QEvent.Enter,
    QEvent.Leave,
    QEvent.WindowDeactivate,
)


def sync_resize_cursor(win, grip=6):
    """Match ``win``'s cursor to where the pointer actually is.

    The resize cursor is set on the window, so every child without a cursor
    of its own inherits it; if it is only ever updated from the window's own
    mouse moves it sticks - the pointer moves onto a child, the child eats
    the move, and the window keeps showing a resize cursor over its whole
    content. Re-deriving it from the global pointer position on any move in
    the window keeps it honest."""
    if win is None:
        return
    pos = win.mapFromGlobal(QCursor.pos())
    edges = _edges_at(win, pos, grip) if win.rect().contains(pos) else Qt.Edges()
    _set_resize_cursor(win, edges)


def _set_resize_cursor(widget, edges):
    if edges in (Qt.LeftEdge | Qt.TopEdge, Qt.RightEdge | Qt.BottomEdge):
        shape = Qt.SizeFDiagCursor
    elif edges in (Qt.RightEdge | Qt.TopEdge, Qt.LeftEdge | Qt.BottomEdge):
        shape = Qt.SizeBDiagCursor
    elif edges & (Qt.LeftEdge | Qt.RightEdge):
        shape = Qt.SizeHorCursor
    elif edges & (Qt.TopEdge | Qt.BottomEdge):
        shape = Qt.SizeVerCursor
    else:
        shape = None
    # Called on every mouse move: skip the no-op re-set, which would
    # otherwise reapply the cursor down the whole child tree.
    if getattr(widget, "_pixel_resize_cursor", None) == shape:
        return
    widget._pixel_resize_cursor = shape
    if shape is None:
        widget.unsetCursor()
    else:
        widget.setCursor(shape)


class _NativeSnapFilter(QAbstractNativeEventFilter):
    """Win32 side of ``enable_native_snap``: the frameless windows it tracks
    carry a real resizable caption style (so Aero Snap, Win+arrows and the
    min/max animations work), and this filter answers WM_NCCALCSIZE so that
    caption and border never get drawn - the whole window stays client area.

    Maximized, Windows still overhangs every monitor edge by the border
    width; ``snap_overhang`` reports it so the window can pad it away."""

    WM_NCCALCSIZE = 0x0083

    def __init__(self):
        super().__init__()
        self.windows = {}  # hwnd -> QWidget

    def nativeEventFilter(self, event_type, message):
        name = event_type.data() if hasattr(event_type, 'data') else bytes(event_type)
        if name != b'windows_generic_MSG':
            return False, 0
        from ctypes import wintypes
        msg = wintypes.MSG.from_address(int(message))
        if (msg.message != self.WM_NCCALCSIZE or not msg.wParam
                or (msg.hWnd or 0) not in self.windows):
            return False, 0
        # Result 0: the proposed window rect is all client area.
        return True, 0


def snap_overhang(win) -> QMargins:
    """How far a maximized snap-enabled window reaches past the screen's
    work area on each side (zero when not maximized). Pad the content by
    this so nothing is laid out off-screen."""
    screen = win.screen()
    if not win.isMaximized() or screen is None:
        return QMargins()
    avail, g = screen.availableGeometry(), win.geometry()
    return QMargins(max(0, avail.left() - g.left()), max(0, avail.top() - g.top()),
                    max(0, g.right() - avail.right()), max(0, g.bottom() - avail.bottom()))


_native_snap_filter = None


def enable_native_snap(win) -> None:
    """Let a frameless window snap like a normal Windows window: drag to the
    left/right edge for half screen, to a corner for a quarter, to the top to
    maximize. Call after every (re)creation of the native window - showEvent
    is a safe place, it is idempotent. No-op off Windows."""
    import sys
    if sys.platform != 'win32':
        return
    global _native_snap_filter
    import ctypes
    from ctypes import wintypes
    try:
        hwnd = int(win.winId())
        if _native_snap_filter is None:
            _native_snap_filter = _NativeSnapFilter()
            QApplication.instance().installNativeEventFilter(_native_snap_filter)
        _native_snap_filter.windows[hwnd] = win

        GWL_STYLE = -16
        WS_THICKFRAME, WS_CAPTION = 0x00040000, 0x00C00000
        WS_MINIMIZEBOX, WS_MAXIMIZEBOX, WS_SYSMENU = 0x00020000, 0x00010000, 0x00080000
        wanted = WS_THICKFRAME | WS_CAPTION | WS_MINIMIZEBOX | WS_MAXIMIZEBOX | WS_SYSMENU
        user32 = ctypes.windll.user32
        style = user32.GetWindowLongW(wintypes.HWND(hwnd), GWL_STYLE) & 0xFFFFFFFF
        if style & wanted != wanted:
            user32.SetWindowLongW(wintypes.HWND(hwnd), GWL_STYLE,
                                  ctypes.c_long(style | wanted))  # wraps to signed
            # SWP_NOSIZE | NOMOVE | NOZORDER | NOACTIVATE | FRAMECHANGED
            user32.SetWindowPos(wintypes.HWND(hwnd), None, 0, 0, 0, 0,
                                0x0001 | 0x0002 | 0x0004 | 0x0010 | 0x0020)
    except Exception:
        import logging
        logging.getLogger(__name__).warning("Could not enable window snapping", exc_info=True)


def install_pixel_window_chrome(app) -> PixelWindowChrome:
    """Install the frameless pixel chrome for every window the app opens."""
    chrome = PixelWindowChrome(app)
    app.installEventFilter(chrome)
    return chrome
