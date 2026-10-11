"""UI themes (BU129, restyling added in BU153).

The pixel UI was written against one navy/cream palette, with its colours as
hex literals in stylesheets, paint code and a few HTML snippets. Rather than
thread a token through every one of them, a theme is a *recolouring* of that
palette:

- ``REMAP`` maps each classic colour to the theme's colour. Every stylesheet
  goes through it (``activate`` wraps ``QWidget.setStyleSheet``), and paint
  code asks for colours through :func:`qcolor` / :func:`hex`.
- ``ROLES`` covers the few places where one classic colour has to become
  different colours depending on what it paints (a panel border versus a
  button border, say). Classic roles are the colours the code used before.
- Pixel SVG icons are recoloured into a per-theme cache folder
  (:func:`themed_svg`).

The theme is chosen once, at startup, before any widget module is imported:
module-level colour constants are built from it. Switching theme in Settings
saves the preference and restarts the app. The classic theme installs
nothing, so it renders exactly as before.

Boring Corporate (BU153) changes more than colour: a sans-serif font, 1px
hairlines, rounded corners and antialiased painting. ``STYLE`` holds those
per-theme tokens; :func:`restyle` rewrites stylesheets (font family and
weight, border widths, radii, letter spacing) and the ``QFont`` class is
swapped for one that maps the pixel font to the theme's. Both run only for a
non-pixel theme, so Classic and Synthwave are untouched.
"""
from __future__ import annotations

import logging
import re
import sys
import tempfile
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

PREF_KEY = "ui_theme"
CLASSIC = "classic"
SYNTHWAVE = "synthwave"
BORING_CORPORATE = "boring_corporate"

# name -> (label, description), in the order Settings lists them.
THEMES = {
    CLASSIC: ("Classic", "Navy panels and cream controls."),
    SYNTHWAVE: ("Synthwave", "Neon magenta panels, gold controls and a retro "
                             "sunset behind the chat."),
    BORING_CORPORATE: ("Boring Corporate", "Black canvas, grey surfaces, thin line "
                                           "icons and rounded edges."),
}

_HEX_RE = re.compile(r"#[0-9A-Fa-f]{6}\b")

# Classic colour (upper case) -> theme colour. No value may also be a key:
# a stylesheet is remapped in one pass, but a remapped colour must never be
# mistaken for a classic one if it is remapped again.
REMAP = {
    CLASSIC: {},
    SYNTHWAVE: {
        # Surfaces
        "#061946": "#07072A",  # panel fill
        "#071D52": "#080A2E",  # inner panel / read-only fields / popups
        "#0E2A6B": "#140726",  # window backdrop, title bar, dialogs
        "#030C24": "#05030F",
        "#0B2A6B": "#0A0C3A",
        "#0A2359": "#090B33",
        # Borders
        "#254D9C": "#FF2BB0",  # panel / bar borders: neon magenta
        "#3A67C7": "#2150C8",  # button borders: electric blue
        "#4A78D8": "#FF7AD8",  # active panel / hovered button border
        "#3E6B9B": "#C01CA0",
        "#284B94": "#2A2560",
        "#3A5C9E": "#5A3E9E",
        # Buttons and cards
        "#274F9B": "#041550",
        "#315DB1": "#0A2270",
        "#1E3F82": "#030E3A",
        "#18336F": "#0B0B30",
        "#10306F": "#1B1250",
        "#10306E": "#1B1250",
        "#12306E": "#4A0445",
        "#0B2762": "#150D45",
        "#1B3A78": "#2A1560",
        # Cream controls become gold
        "#F6E0A6": "#FDD865",
        "#FFEFC1": "#FFE9A0",
        "#FFE7B4": "#FFE27F",
        "#E8CF8E": "#F0C24A",
        "#E0C079": "#F2B640",
        # Bubbles
        "#294F9D": "#001E66",
        "#3765BD": "#5A8FE8",
        "#3C6FCB": "#1F46B8",
        # Text
        "#FFF0BF": "#FFF4E6",
        "#071846": "#1A0F3C",
        "#071C4B": "#1A0F3C",
        "#FFE9A8": "#FFE36E",  # gold accents
        "#8EA7D8": "#B9A6E8",  # muted
        "#8FA2C9": "#B9A6E8",
        "#8090B8": "#6E6699",
        "#6E7CA8": "#6E6699",
        "#5E75A8": "#6E6699",
        "#6D7FB4": "#9A86C8",
        "#93A2CE": "#B8A8E0",
        "#5B6A94": "#7A5A20",
        "#BFD2FF": "#C9B8FF",
        "#A9BCE6": "#E6D6FF",
        "#D8E3FF": "#EDE3FF",
        "#C9D6F5": "#D9CCF5",
        "#7E8FC2": "#E020B0",  # timestamps: hot pink
        "#6FD3FF": "#6FF3FF",  # focus / selection: cyan
        "#7FD4FF": "#6FF3FF",
        "#BFE9FF": "#C0FBFF",
        "#0078D4": "#92F0FF",  # scope label
        # SVG assets
        "#FFE8AD": "#FFD447",  # icons: gold
        "#244A9C": "#001E66",  # blue bubble tails
        "#547BE0": "#FF2BB0",
        "#24448E": "#7A1070",
    },
    BORING_CORPORATE: {
        # Surfaces: one black canvas; panels sit flush on it.
        "#061946": "#000000",  # panel fill
        "#071D52": "#0D0D0D",  # inner panel / read-only fields / popups
        "#0E2A6B": "#000001",  # window backdrop, title bar, dialogs
        "#030C24": "#050505",  # screenshot stage
        "#0B2A6B": "#171717",
        "#0A2359": "#171717",
        # Hairlines
        "#254D9C": "#2A2A2A",  # panel / bar / field borders
        "#3A67C7": "#3A3A3A",  # button borders
        "#4A78D8": "#5C5C5C",  # active / hovered border
        "#3E6B9B": "#363636",  # menu and popup borders
        "#284B94": "#242424",  # disabled border
        "#3A5C9E": "#3B3B3B",  # dashed drop zones
        # Buttons and cards: dark grey, lifting on hover
        "#274F9B": "#212121",
        "#315DB1": "#2C2C2C",
        "#1E3F82": "#181818",
        "#18336F": "#141414",
        "#10306F": "#1A1A1A",
        "#10306E": "#1A1A1A",
        "#12306E": "#262627",  # selected card
        "#0B2762": "#141415",  # hovered card
        "#1B3A78": "#1C1C1C",
        "#14337F": "#1F1F1F",
        "#0B2260": "#141416",
        # Cream controls become grey surfaces with light text
        "#F6E0A6": "#262626",
        "#FFEFC1": "#4A4A4A",
        "#FFE7B4": "#2F2F2F",
        "#E8CF8E": "#1F1F20",
        "#E0C079": "#3A3A3B",
        # Bubbles: mic grey, system near-black
        "#294F9D": "#171718",
        "#3765BD": "#2B2B2B",
        "#3C6FCB": "#333334",
        # Text
        "#FFF0BF": "#ECECEC",
        "#071846": "#ECECED",  # dark-on-cream text turns light
        "#071C4B": "#ECECED",
        "#FFE9A8": "#F5F5F5",  # accents and headings
        "#FFFFFF": "#FAFAFA",
        "#8EA7D8": "#8F8F8F",  # muted
        "#8FA2C9": "#8F8F8F",
        "#93A2CE": "#8F8F8F",
        "#5B6A94": "#8F8F8F",
        "#7E8FC2": "#8F8F8F",
        "#A9BCE6": "#8F8F8F",
        "#6D7FB4": "#7D7D7D",
        "#8090B8": "#5C5C5C",  # disabled
        "#6E7CA8": "#5C5C5C",
        "#5E75A8": "#5C5C5C",
        "#BFD2FF": "#B4B4B4",
        "#C9D6F5": "#B4B4B4",
        "#D5DFF5": "#B4B4B4",
        "#B9C8EA": "#B4B4B4",
        "#D8E3FF": "#D4D4D4",
        "#0078D4": "#8F8F8F",  # scope label
        "#6FD3FF": "#D4D4D5",  # focus / selection
        "#7FD4FF": "#D4D4D6",  # links
        "#BFE9FF": "#FAFAFB",
        # Status: no accents except red for live / recording and errors
        "#FF6B5E": "#EF4444",
        "#7A3434": "#5C2626",
        "#3A1430": "#2A1414",
        "#FFD9D3": "#FCA5A5",
        "#F2B84B": "#B4B4B5",  # paused
        "#3A2E14": "#1A1A1B",
        "#FF8A7A": "#F87171",
        "#FF9A8A": "#F87171",
        "#FF8B7A": "#F87171",
        "#FFB4A8": "#FCA5A6",
        "#8FE39B": "#B4B4B6",  # "ready" green -> grey
        "#10362F": "#1A1A1C",
        "#3C9A6B": "#333335",
        "#A8E3B4": "#B4B4B7",
        "#123A1E": "#1A1A1D",
        "#4E9A63": "#333336",
        "#D6F5DD": "#ECECEE",
        "#0E3350": "#1A1A1E",
        "#2F8FC0": "#333337",
        "#3A3320": "#1A1A1F",
        "#B89A4E": "#333338",
        "#9BE59B": "#86C99A",
        "#E37C25": "#F9F9F9",  # orange upload buttons -> white
        "#EE8C38": "#E5E5E5",
        "#C8661A": "#D4D4D7",
        "#F4A25C": "#F9F9F8",
        "#B8323A": "#C42B1C",  # window close hover: Windows red
        "#B9A878": "#1A1A20",
        # Legacy light bubbles
        "#E3F2FD": "#262628",
        "#90CAF9": "#3A3A3C",
        "#E8F5E9": "#1A1A21",
        "#A5D6A7": "#333339",
        "#1565C0": "#D4D4D8",
        "#0066CC": "#B4B4B8",
        "#008800": "#B4B4B9",
        # SVG assets (BU155 replaces the icons; these cover any it misses)
        "#FFE8AD": "#ECECEF",
        "#244A9C": "#171719",
        "#547BE0": "#8F8F90",
        "#24448E": "#262629",
    },
}

# Colours that depend on the role, not just on the classic colour.
ROLES = {
    CLASSIC: {
        "brand": "#FFF0BF",
        "title_bar_text": "#FFE9A8",
        "section_title": "#FFE9A8",
        "section_title_alt": "#FFE9A8",
        "panel_border_inner": "#3A67C7",
        "panel_glow": "",
        "button_text": "#FFF0BF",
        "tool_border": "#3A67C7",
        "bubble_blue_text": "#FFF0BF",
        "date_selected": "#A9BCE6",
    },
    SYNTHWAVE: {
        "brand": "#7AF5FF",
        "title_bar_text": "#EB3BBD",
        "section_title": "#FFE36E",
        "section_title_alt": "#F02AB0",
        "panel_border_inner": "#FF2BB0",
        "panel_glow": "#FF2BB0",
        "button_text": "#7FF3FF",
        "tool_border": "#8A2FB0",
        "bubble_blue_text": "#A8F4FF",
        "date_selected": "#FFC7B0",
    },
    BORING_CORPORATE: {
        "brand": "#ECECEC",
        "title_bar_text": "#ECECEC",
        "section_title": "#8F8F8F",
        "section_title_alt": "#8F8F8F",
        "panel_border_inner": "#2A2A2A",
        "panel_glow": "",
        "button_text": "#ECECEC",
        "tool_border": "#2A2A2A",
        "bubble_blue_text": "#ECECEC",
        "date_selected": "#ECECEC",
        # Roles only this theme reads (BU154)
        "canvas": "#000000",
        "surface": "#262626",
        "surface_hover": "#2F2F2F",
        "nav_hover": "#1A1A1A",
        "nav_selected": "#212121",
        "hairline": "#262626",
        "hairline_strong": "#3A3A3A",
        "text": "#ECECEC",
        "text_secondary": "#B4B4B4",
        "text_muted": "#8F8F8F",
        "primary_fill": "#F9F9F9",
        "primary_hover": "#E5E5E5",
        "primary_text": "#0D0D0D",
        "find_fill": "#E8E8E8",
        "find_text": "#0D0D0D",
        "selection": "#D4D4D4",
        "live": "#EF4444",
    },
}

# Icons whose colour in a theme is not just REMAP of the classic one.
_SVG_OVERRIDES = {
    SYNTHWAVE: {
        "icon_menu.svg": {"#FFE8AD": "#F140A0"},
        "bubble_tail_cream_left.svg": {"#FFE8AD": "#FDD865"},
        "bubble_tail_cream_right.svg": {"#FFE8AD": "#FDD865"},
    },
}

# Image painted behind the chat, under assets/themes/.
_BACKGROUNDS = {SYNTHWAVE: "synthwave_bg.jpg"}

# Extra app-wide QSS, appended after the classic stylesheet.
_EXTRA_QSS = {
    SYNTHWAVE: """
    QLabel#BrandTitle {
        color: #7AF5FF;
    }

    QLabel#ScopeLabel {
        color: #92F0FF;
    }

    QListWidget::item {
        background: #011654;
        border: 2px solid #1747A0;
    }

    QListWidget::item:hover {
        background: #0A2270;
    }

    QListWidget::item:selected {
        background: #650557;
        border: 2px solid #C01CA0;
    }

    QScrollBar:vertical {
        background: #E0348F;
        width: 12px;
        border: none;
    }

    QScrollBar::handle:vertical {
        background: #FD9872;
        min-height: 24px;
        border: none;
    }

    QScrollBar:horizontal {
        background: #E0348F;
        height: 12px;
        border: none;
    }

    QScrollBar::handle:horizontal {
        background: #FD9872;
        min-width: 24px;
        border: none;
    }

    QScrollBar::add-page, QScrollBar::sub-page {
        background: transparent;
    }
    """,
    BORING_CORPORATE: """/* native */
    QWidget {
        selection-background-color: #3A3A3A;
        selection-color: #FAFAFA;
    }

    QLabel#BrandTitle {
        color: #ECECEC;
        font-size: 18px;
        font-weight: 600;
        padding: 2px 0px;
    }

    QLabel#ScopeLabel {
        color: #8F8F8F;
        font-weight: 400;
        font-size: 12px;
    }

    QListWidget::item {
        background: transparent;
        border: none;
        border-radius: 8px;
        padding: 8px 10px;
        margin: 1px 0px;
    }

    QListWidget::item:hover {
        background: #1A1A1A;
    }

    QListWidget::item:selected {
        background: #212121;
        border: none;
    }

    QScrollBar:vertical {
        background: transparent;
        width: 8px;
        margin: 2px;
        border: none;
    }

    QScrollBar::handle:vertical {
        background: #3A3A3A;
        min-height: 28px;
        border: none;
        border-radius: 2px;
    }

    QScrollBar::handle:vertical:hover {
        background: #5C5C5C;
    }

    QScrollBar:horizontal {
        background: transparent;
        height: 8px;
        margin: 2px;
        border: none;
    }

    QScrollBar::handle:horizontal {
        background: #3A3A3A;
        min-width: 28px;
        border: none;
        border-radius: 2px;
    }

    QScrollBar::add-page, QScrollBar::sub-page {
        background: transparent;
    }

    QTextEdit,
    QTextBrowser,
    QLineEdit {
        border-radius: 10px;
        padding: 8px 10px;
    }

    QTextEdit:focus,
    QLineEdit:focus {
        border: 1px solid #5C5C5C;
    }

    QTextEdit[readOnly="true"] {
        border: 1px solid #262626;
    }

    QComboBox {
        font-weight: 400;
        border-radius: 10px;
    }

    QComboBox::down-arrow {
        image: url(__CHEVRON__);
        width: 14px;
        height: 14px;
        border: none;
        margin-right: 6px;
    }

    QComboBox QAbstractItemView,
    QCompleter QAbstractItemView {
        background: #1F1F1F;
        color: #ECECEC;
        border: 1px solid #333333;
        padding: 4px;
        selection-background-color: #2F2F2F;
        selection-color: #FAFAFA;
        outline: none;
    }

    QMenu {
        background: #1F1F1F;
        color: #ECECEC;
        border: 1px solid #333333;
        border-radius: 10px;
        padding: 6px;
    }

    QMenu::item {
        background: transparent;
        padding: 7px 14px;
        border-radius: 6px;
    }

    QMenu::item:selected {
        background: #2F2F2F;
    }

    QMenu::separator {
        height: 1px;
        background: #333333;
        margin: 4px 6px;
    }

    QToolTip {
        background: #1F1F1F;
        color: #ECECEC;
        border: 1px solid #333333;
        padding: 5px 8px;
    }

    QHeaderView::section {
        background: #141414;
        color: #B4B4B4;
        border: none;
        border-bottom: 1px solid #262626;
        padding: 6px;
    }

    QTableWidget {
        border: 1px solid #262626;
        border-radius: 10px;
        gridline-color: #262626;
    }

    /* Main window (BU157): a hairline search pill at the top, a grey input
       pill at the bottom. Radii are half the fixed heights window.py sets. */
    QFrame#UnifiedSearchBar {
        background: transparent;
        border: 1px solid #2A2A2A;
        border-radius: 23px;
    }

    QFrame#ChatInputBar {
        background: #262626;
        border: 1px solid #333333;
        border-radius: 30px;
    }

    QComboBox#ScopeCombo,
    QLineEdit#SessionSearchInput {
        color: #ECECEC;
        font-size: 14px;
        font-weight: 500;
        border-radius: 0px;
        padding: 4px 6px;
    }

    QLineEdit#SessionSearchInput:focus {
        border: none;
    }


    QComboBox#AgentCombo {
        color: #B4B4B4;
        font-size: 13px;
        font-weight: 400;
    }

    QComboBox#AgentCombo:hover,
    QComboBox#AgentCombo:on {
        color: #ECECEC;
    }

    QComboBox#AgentCombo::down-arrow {
        width: 14px;
        height: 14px;
        border: none;
    }

    QTextEdit#QuestionInput {
        color: #ECECEC;
        font-size: 15px;
        font-weight: 400;
        border: none;
        padding: 6px 2px;
    }

    QTextEdit#QuestionInput:focus {
        border: none;
    }

    /* Message boxes are children of the window, so they take its sheet. */
    QMessageBox {
        background: #0D0D0D;
    }

    QMessageBox QLabel {
        color: #ECECEC;
        font-size: 14px;
    }

    QMessageBox QPushButton,
    QDialogButtonBox QPushButton {
        color: #ECECEC;
        background: #212121;
        border: 1px solid #424242;
        border-radius: 15px;
        padding: 6px 18px;
        min-width: 64px;
        min-height: 18px;
        font-size: 13px;
    }

    QMessageBox QPushButton:hover,
    QDialogButtonBox QPushButton:hover {
        background: #2C2C2C;
    }

    QMessageBox QPushButton:default {
        color: #0D0D0D;
        background: #F9F9F9;
        border-color: #F9F9F9;
    }
    """,
}

# Shape and type per theme (BU153). The pixel themes share one set: the code
# they were written for already paints that way.
_PIXEL_STYLE = {
    "pixel": True,           # pixel-cut corners, no antialiasing, bubble tails
    "font": "Courier New",
    "display_font": "Courier New",
}
STYLE = {
    CLASSIC: _PIXEL_STYLE,
    SYNTHWAVE: _PIXEL_STYLE,
    BORING_CORPORATE: {
        "pixel": False,
        # What the reference renders in on Windows. Plain "Segoe UI" has a
        # real Semibold face for weight 600; the Variable family only has
        # Regular and Bold to Qt.
        "font": "Segoe UI",
        "display_font": "Segoe UI Variable Display",
    },
}

# A stylesheet written in the active theme's own units carries this marker:
# restyle() leaves everything from the marker on as it is.
NATIVE_QSS = "/* native */"

_active = CLASSIC
_original_set_stylesheet = None


# =========================
# Selecting the theme
# =========================

def active() -> str:
    return _active


def is_classic() -> bool:
    return _active == CLASSIC


def is_corporate() -> bool:
    return _active == BORING_CORPORATE


def is_pixel() -> bool:
    """True for the pixel-art themes (Classic, Synthwave)."""
    return STYLE[_active]["pixel"]


def normalize(name) -> str:
    return name if name in THEMES else CLASSIC


def read_preference(prefs: Optional[dict]) -> str:
    """The theme saved in a preferences dict (classic when absent or unknown)."""
    if not isinstance(prefs, dict):
        return CLASSIC
    return normalize(prefs.get(PREF_KEY))


def activate(name: str) -> str:
    """Make ``name`` the theme for this run. Call before importing any widget
    module: their colour constants are computed at import."""
    global _active
    name = normalize(name)
    widget_modules = [m for m in ("src.app.pixel_widgets", "src.app.window") if m in sys.modules]
    if widget_modules and name != _active:
        logger.warning("Theme %r activated after %s was imported; some colours "
                       "keep the previous theme", name, ", ".join(widget_modules))
    _active = name
    if name != CLASSIC:
        _install_stylesheet_hook()
    if not STYLE[name]["pixel"]:
        _install_font_class()
    logger.info("UI theme: %s", name)
    return name


def _install_stylesheet_hook():
    global _original_set_stylesheet
    if _original_set_stylesheet is not None:
        return
    from PySide6.QtWidgets import QWidget

    original = QWidget.setStyleSheet
    _original_set_stylesheet = original

    def setStyleSheet(self, styleSheet):  # noqa: N802 - Qt signature
        original(self, remap(styleSheet))

    QWidget.setStyleSheet = setStyleSheet


# =========================
# Colours
# =========================

def remap(text):
    """``text`` with every classic hex colour replaced by the active theme's,
    and (non-pixel themes) restyled - see :func:`restyle`."""
    mapping = REMAP[_active]
    if not mapping or not text:
        return text
    text = _HEX_RE.sub(lambda m: mapping.get(m.group(0).upper(), m.group(0)), text)
    return restyle(text)


def hex(value: str) -> str:  # noqa: A001 - reads as theme.hex("#...")
    """One colour, through the active theme."""
    return REMAP[_active].get(value.upper(), value)


def qcolor(value: str):
    from PySide6.QtGui import QColor

    return QColor(hex(value))


def role(key: str) -> str:
    return ROLES[_active].get(key, ROLES[CLASSIC].get(key, ""))


def role_color(key: str):
    from PySide6.QtGui import QColor

    return QColor(role(key))


def extra_qss() -> str:
    return _EXTRA_QSS.get(_active, "")


def background_image() -> Optional[str]:
    """Path of the image painted behind the chat, or None for a plain fill."""
    filename = _BACKGROUNDS.get(_active)
    if not filename:
        return None
    from .. import paths

    path = paths.asset_path("themes", filename)
    return str(path) if path.is_file() else None


# =========================
# Icons
# =========================

def _svg_cache_dir(name: str) -> Path:
    return Path(tempfile.gettempdir()) / "chronicle-theme" / name


def recolor_svg(filename: str, text: str, name: Optional[str] = None) -> str:
    """SVG source ``text`` recoloured for theme ``name`` (default: active)."""
    name = normalize(name or _active)
    mapping = dict(REMAP[name])
    mapping.update(_SVG_OVERRIDES.get(name, {}).get(filename, {}))
    if not mapping:
        return text
    return _HEX_RE.sub(lambda m: mapping.get(m.group(0).upper(), m.group(0)), text)


def themed_svg(path) -> str:
    """Path to ``path`` recoloured for the active theme.

    Classic returns ``path`` itself. Otherwise the recoloured copy is written
    to a per-theme temp folder; any failure falls back to the original.
    """
    path = Path(path)
    if _active == CLASSIC or path.suffix.lower() != ".svg" or not path.is_file():
        return str(path)
    try:
        text = path.read_text(encoding="utf-8")
        themed = recolor_svg(path.name, text)
        if themed == text:
            return str(path)
        target = _svg_cache_dir(_active) / path.name
        if not target.is_file() or target.read_text(encoding="utf-8") != themed:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(themed, encoding="utf-8")
        return str(target)
    except OSError as exc:
        logger.warning("Could not theme icon %s: %s", path.name, exc)
        return str(path)


# =========================
# Shape and type (BU153)
# =========================

_FONT_FAMILY_RE = re.compile(
    r"""(font-family\s*:\s*)(?:"Courier New"|'Courier New'|Courier New)""", re.IGNORECASE)
_FONT_WEIGHT_RE = re.compile(r"(font-weight\s*:\s*)(?:bold(?:er)?|[6-9]00)\b", re.IGNORECASE)
_LETTER_SPACING_RE = re.compile(r"(letter-spacing\s*:\s*)[^;\"}]+")
_BORDER_WIDTH_RE = re.compile(r"(border(?:-(top|bottom|left|right))?(?:-width)?\s*:\s*)([2-9])px")
_RADIUS_RE = re.compile(r"(border(?:-(?:top|bottom)-(?:left|right))?-radius\s*:\s*)(\d+)px")
_FONT_SIZE_PX_RE = re.compile(r"(font-size\s*:\s*)(\d+)px")


def font_family() -> str:
    return STYLE[_active]["font"]


def display_font_family() -> str:
    """Family for large headings (the empty-state title)."""
    return STYLE[_active]["display_font"]


def _radius(px: int) -> int:
    # The pixel UI's 3-7px corners read as square once antialiased; the
    # corporate scale is 6 (chips), 10 (fields, buttons) and up.
    if 5 <= px <= 7:
        return 10
    if 3 <= px <= 4:
        return 6
    return px


def _border_width(match) -> str:
    side = match.group(2)
    # A left edge marks a selection or a quote; keep it a little heavier.
    return f"{match.group(1)}{2 if side == 'left' else 1}px"


def _font_size(match) -> str:
    # Courier New sets small for its size; the sans-serif reads a step larger.
    px = int(match.group(2))
    return f"{match.group(1)}{px - 1 if px >= 13 else px}px"


def _restyle(text: str) -> str:
    family = STYLE[_active]["font"]
    text = _FONT_FAMILY_RE.sub(lambda m: f'{m.group(1)}"{family}"', text)
    text = _FONT_WEIGHT_RE.sub(lambda m: f"{m.group(1)}600", text)
    text = _LETTER_SPACING_RE.sub(lambda m: f"{m.group(1)}0px", text)
    text = _BORDER_WIDTH_RE.sub(_border_width, text)
    text = _RADIUS_RE.sub(lambda m: f"{m.group(1)}{_radius(int(m.group(2)))}px", text)
    return _FONT_SIZE_PX_RE.sub(_font_size, text)


def restyle(text):
    """``text`` (a stylesheet or HTML) in the active theme's shape and type.

    Pixel themes return it unchanged. Otherwise the pixel font becomes the
    theme's, bold becomes semibold, letter spacing goes, 2-3px borders become
    1px hairlines and small corner radii grow. Anything after
    :data:`NATIVE_QSS` is already written for the theme and is kept as is.
    """
    if not text or STYLE[_active]["pixel"]:
        return text
    head, marker, tail = text.partition(NATIVE_QSS)
    return _restyle(head) + marker + tail


def antialias() -> bool:
    """Whether paint code should antialias: off for crisp pixel art."""
    return not STYLE[_active]["pixel"]


def pen_width(pixel_width: float) -> float:
    """A border width from the pixel UI, in the active theme (hairlines)."""
    return pixel_width if STYLE[_active]["pixel"] else 1


def corner_radius(cut: float) -> float:
    """Rounded-corner radius standing in for a pixel corner ``cut``."""
    if cut <= 3:
        return 6
    if cut <= 5:
        return 8
    return 12


def rounded_rect_path(x, y, w, h, radius):
    """A rounded rectangle on half-pixel coordinates, so a 1px antialiased
    pen lands on whole pixels. ``radius`` is clamped to a pill."""
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QPainterPath

    path = QPainterPath()
    radius = max(0.0, min(float(radius), w / 2, h / 2))
    path.addRoundedRect(QRectF(x + 0.5, y + 0.5, w, h), radius, radius)
    return path


def font(point_size: float, bold: bool = False):
    """A QFont in the theme's family."""
    from PySide6.QtGui import QFont

    result = QFont(STYLE[_active]["font"])
    result.setPointSizeF(point_size)
    result.setBold(bold)
    return result


def _font_class(base, family):
    """``QFont`` whose pixel-font family, bold and letter spacing follow the
    theme. Installed in place of ``PySide6.QtGui.QFont`` before any widget
    module imports it, so the ~100 ``QFont("Courier New")`` call sites need
    no per-theme branch."""

    def themed_family(name):
        return family if isinstance(name, str) and name.lower() == "courier new" else name

    def themed_weight(weight):
        value = getattr(weight, "value", weight)
        if isinstance(value, int) and value > 600:
            return base.Weight.DemiBold
        return weight

    class ThemedFont(base):
        _chronicle_family = family

        def __init__(self, *args, **kwargs):
            if args and isinstance(args[0], str):
                args = (themed_family(args[0]),) + args[1:]
                if len(args) >= 3:
                    args = args[:2] + (themed_weight(args[2]),) + args[3:]
            super().__init__(*args, **kwargs)

        def setFamily(self, name):  # noqa: N802 - Qt signature
            super().setFamily(themed_family(name))

        def setBold(self, enable):  # noqa: N802
            super().setWeight(base.Weight.DemiBold if enable else base.Weight.Normal)

        def setWeight(self, weight):  # noqa: N802
            super().setWeight(themed_weight(weight))

        def setLetterSpacing(self, kind, spacing):  # noqa: N802
            super().setLetterSpacing(kind, 0)

    ThemedFont.__name__ = ThemedFont.__qualname__ = "QFont"
    return ThemedFont


def _install_font_class():
    from PySide6 import QtGui

    if getattr(QtGui.QFont, "_chronicle_family", None):
        return
    QtGui.QFont = _font_class(QtGui.QFont, STYLE[_active]["font"])


def app_wide_qss() -> str:
    """Application-level stylesheet for a non-pixel theme: the default type,
    and the pop-ups (tooltips, menus) that are their own top-level windows."""
    if STYLE[_active]["pixel"]:
        return ""
    family = STYLE[_active]["font"]
    return f"""
    QWidget {{
        font-family: "{family}";
    }}

    QToolTip {{
        background: #1F1F1F;
        color: #ECECEC;
        border: 1px solid #333333;
        padding: 5px 8px;
    }}

    QMenu {{
        background: #1F1F1F;
        color: #ECECEC;
        border: 1px solid #333333;
        padding: 6px;
    }}

    QMenu::item {{
        background: transparent;
        padding: 7px 14px;
        border-radius: 6px;
    }}

    QMenu::item:selected {{
        background: #2F2F2F;
    }}

    QMenu::item:disabled {{
        color: #5C5C5C;
    }}

    QMenu::separator {{
        height: 1px;
        background: #333333;
        margin: 4px 6px;
    }}

    QMessageBox {{
        background: #0D0D0D;
    }}

    QMessageBox QLabel {{
        color: #ECECEC;
        font-size: 14px;
    }}

    QMessageBox QPushButton,
    QDialogButtonBox QPushButton {{
        color: #ECECEC;
        background: #212121;
        border: 1px solid #424242;
        border-radius: 15px;
        padding: 6px 18px;
        min-width: 64px;
        min-height: 18px;
        font-size: 13px;
    }}

    QMessageBox QPushButton:hover,
    QDialogButtonBox QPushButton:hover {{
        background: #2C2C2C;
    }}

    QMessageBox QPushButton:default {{
        color: #0D0D0D;
        background: #F9F9F9;
        border-color: #F9F9F9;
    }}
    """


def icon_override(filename: str) -> Optional[str]:
    """The active theme's own drawing of icon ``filename`` (BU155), if it has
    one: ``assets/themes/<theme>/icons/<filename>``."""
    if STYLE[_active]["pixel"]:
        return None
    from .. import paths

    path = paths.asset_path("themes", _active, "icons", filename)
    return str(path) if path.is_file() else None


def scrollbar_qss() -> str:
    """The non-pixel theme's thin rounded scrollbars, for a widget whose own
    stylesheet has to restate them (BU158)."""
    return NATIVE_QSS + """
    QScrollBar:vertical { background: transparent; width: 8px; margin: 2px; border: none; }
    QScrollBar::handle:vertical {
        background: #3A3A3A; min-height: 28px; border: none; border-radius: 2px;
    }
    QScrollBar::handle:vertical:hover { background: #5C5C5C; }
    QScrollBar::add-line, QScrollBar::sub-line { height: 0px; width: 0px; border: none; }
    QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
    """


def app_palette():
    """Dark palette for a non-pixel theme: anything that paints from the
    palette rather than a stylesheet (message boxes, a dialog's own
    background, native-styled controls) lands on the theme's greys."""
    from PySide6.QtGui import QColor, QPalette

    palette = QPalette()
    colors = {
        QPalette.Window: "#171717",
        QPalette.WindowText: "#ECECEC",
        QPalette.Base: "#0D0D0D",
        QPalette.AlternateBase: "#171717",
        QPalette.Text: "#ECECEC",
        QPalette.PlaceholderText: "#8F8F8F",
        QPalette.Button: "#212121",
        QPalette.ButtonText: "#ECECEC",
        QPalette.BrightText: "#FAFAFA",
        QPalette.Highlight: "#3A3A3A",
        QPalette.HighlightedText: "#FAFAFA",
        QPalette.ToolTipBase: "#1F1F1F",
        QPalette.ToolTipText: "#ECECEC",
        QPalette.Link: "#D4D4D4",
        QPalette.Mid: "#333333",
        QPalette.Dark: "#0D0D0D",
        QPalette.Light: "#3A3A3A",
    }
    for role_, value in colors.items():
        palette.setColor(role_, QColor(value))
    for role_ in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        palette.setColor(QPalette.Disabled, role_, QColor("#5C5C5C"))
    return palette
