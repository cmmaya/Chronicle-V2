"""UI colour themes (BU129).

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

# name -> (label, description), in the order Settings lists them.
THEMES = {
    CLASSIC: ("Classic", "Navy panels and cream controls."),
    SYNTHWAVE: ("Synthwave", "Neon magenta panels, gold controls and a retro "
                             "sunset behind the chat."),
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
}

_active = CLASSIC
_original_set_stylesheet = None


# =========================
# Selecting the theme
# =========================

def active() -> str:
    return _active


def is_classic() -> bool:
    return _active == CLASSIC


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
    """``text`` with every classic hex colour replaced by the active theme's."""
    mapping = REMAP[_active]
    if not mapping or not text:
        return text
    return _HEX_RE.sub(lambda m: mapping.get(m.group(0).upper(), m.group(0)), text)


def hex(value: str) -> str:  # noqa: A001 - reads as theme.hex("#...")
    """One colour, through the active theme."""
    return REMAP[_active].get(value.upper(), value)


def qcolor(value: str):
    from PySide6.QtGui import QColor

    return QColor(hex(value))


def role(key: str) -> str:
    return ROLES[_active].get(key, ROLES[CLASSIC][key])


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
