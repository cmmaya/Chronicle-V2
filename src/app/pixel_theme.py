"""Pixel-art visual theme for Chronicle's PySide6 UI.

This module is intentionally presentation-only. It provides constants, QSS, and
asset-path helpers used by window.py and pixel_widgets.py.
"""
from __future__ import annotations

from pathlib import Path

# Phase 1: global palette tuned to the second reference image.
# Deep navy surfaces, saturated mid-blue controls, warm parchment/cream accents,
# and hard shadow tones for an 8-bit/pixel-art UI rather than a flat web theme.
COLORS = {
    "bg": "#03143A",
    "bg_radial": "#071F53",
    "panel": "#061B49",
    "panel_deep": "#031237",
    "panel_2": "#183B84",
    "panel_3": "#244A9C",
    "panel_border": "#24448E",
    "panel_border_light": "#547BE0",
    "panel_highlight": "#6F91F0",
    "cream": "#FFE8AD",
    "cream_light": "#FFF1C8",
    "cream_shadow": "#D4B164",
    "cream_deep_shadow": "#A98132",
    "blue_bubble": "#244A9C",
    "blue_bubble_light": "#2B56B3",
    "blue_bubble_shadow": "#102C68",
    "text_light": "#F7EFC9",
    "text_dark": "#071C4B",
    "muted": "#93B2FF",
    "danger": "#FF8A8A",
}

FONT_FAMILY = "'Press Start 2P', 'Pixelify Sans', 'Courier New', monospace"


def asset_path(filename: str) -> str:
    """Return an absolute path to a bundled pixel UI asset."""
    return str(Path(__file__).resolve().parents[2] / "assets" / "pixel" / filename)


# Backwards-compatible alias used by some older UI code/docs.
pixel_asset_path = asset_path


def _qss_url(filename: str) -> str:
    """Return an asset URL suitable for Qt stylesheets."""
    return asset_path(filename).replace("\\", "/")


def _asset_qss_if_exists(filename: str, property_name: str = "image") -> str:
    """Return a QSS image declaration only when the optional asset exists."""
    path = Path(asset_path(filename))
    if not path.exists():
        return ""
    return f"{property_name}: url({_qss_url(filename)});"


def app_qss() -> str:
    c = COLORS
    combo_arrow_dark = _asset_qss_if_exists("icon_caret_down_dark.svg")
    combo_arrow_light = _asset_qss_if_exists("icon_caret_down_light.svg")
    return f"""
    QMainWindow, QWidget {{
        background-color: {c['bg']};
        color: {c['text_light']};
        font-family: {FONT_FAMILY};
        font-size: 14px;
    }}

    QMainWindow {{
        background-color: {c['bg']};
    }}

    QLabel {{
        background: transparent;
        color: {c['text_light']};
    }}

    QLabel#BrandTitle {{
        color: {c['cream']};
        font-size: 27px;
        font-weight: 900;
        letter-spacing: 0px;
        padding: 0 0 2px 0;
    }}

    QLabel#SectionTitle, QPushButton#TabLabel {{
        color: {c['cream']};
        font-size: 16px;
        font-weight: 900;
        letter-spacing: 2px;
        background: transparent;
        border: none;
    }}

    QLabel#ScopeLabel {{
        color: {c['muted']};
        font-size: 10px;
    }}

    QFrame#PixelPanel {{
        background-color: {c['panel']};
        border: 4px solid {c['panel_border']};
    }}

    QFrame#PixelPanelInner {{
        background-color: {c['panel_deep']};
        border: 3px solid {c['panel_border']};
    }}

    QFrame#TranscriptViewport {{
        background-color: {c['panel_deep']};
        border: 3px solid {c['panel_border']};
    }}

    QFrame#CreamBar {{
        background-color: {c['cream']};
        border: 4px solid {c['panel_border_light']};
    }}

    QFrame#UnifiedSearchBar {{
        background-color: {c['cream']};
        border: 4px solid {c['panel_border_light']};
    }}

    QFrame#ChatInputBar {{
        background-color: {c['cream']};
        border: 4px solid {c['panel_border_light']};
    }}

    QPushButton, QToolButton {{
        background-color: {c['panel_2']};
        color: {c['text_light']};
        border: 3px solid {c['panel_border_light']};
        border-right: 5px solid {c['blue_bubble_shadow']};
        border-bottom: 5px solid {c['blue_bubble_shadow']};
        padding: 9px 13px;
        min-height: 36px;
        font-weight: 900;
    }}

    QPushButton:hover, QToolButton:hover {{
        background-color: {c['panel_3']};
        border-color: {c['panel_highlight']};
    }}

    QPushButton:pressed, QToolButton:pressed {{
        background-color: {c['blue_bubble_shadow']};
        border-color: {c['cream']};
        border-right: 3px solid {c['blue_bubble_shadow']};
        border-bottom: 3px solid {c['blue_bubble_shadow']};
    }}

    QPushButton:disabled, QToolButton:disabled {{
        background-color: #10295E;
        color: #7893D0;
        border-color: #2A4387;
    }}

    QPushButton#SidebarButton {{
        text-align: left;
        min-height: 56px;
        font-size: 15px;
        padding-left: 16px;
    }}

    QPushButton#IconButton, QToolButton#IconButton {{
        min-width: 48px;
        max-width: 56px;
        min-height: 48px;
        max-height: 56px;
        padding: 5px;
        font-size: 18px;
    }}

    QPushButton#TabLabel {{
        min-height: 28px;
        padding: 0 10px;
        border: none;
        background: transparent;
        color: {c['cream']};
    }}

    QPushButton#AppLogsButton {{
        min-height: 44px;
        background-color: {c['panel_2']};
        color: {c['text_light']};
        border: 3px solid {c['panel_border_light']};
        border-right: 5px solid {c['blue_bubble_shadow']};
        border-bottom: 5px solid {c['blue_bubble_shadow']};
    }}

    QLineEdit, QTextEdit, QComboBox {{
        background-color: {c['cream']};
        color: {c['text_dark']};
        border: 3px solid {c['panel_border_light']};
        padding: 8px;
        selection-background-color: {c['panel_border_light']};
        selection-color: {c['cream']};
        font-size: 14px;
        font-weight: 800;
    }}

    QTextEdit {{
        border: none;
        background-color: transparent;
        padding: 8px 10px;
    }}

    QLineEdit#SessionSearchInput {{
        border: none;
        background-color: transparent;
        padding: 8px 6px;
        color: {c['text_dark']};
    }}

    QComboBox#ScopeCombo, QComboBox#AgentCombo {{
        border: none;
        background-color: transparent;
        color: {c['text_dark']};
        padding: 8px 8px;
    }}

    QComboBox#AgentCombo {{
        min-width: 112px;
        border-left: 3px solid {c['cream_shadow']};
    }}

    QComboBox::drop-down {{
        border-left: 3px solid {c['cream_shadow']};
        width: 28px;
    }}

    QComboBox#ScopeCombo::drop-down, QComboBox#AgentCombo::drop-down {{
        border-left: none;
        width: 26px;
    }}

    QComboBox::down-arrow {{
        width: 18px;
        height: 18px;
        {combo_arrow_dark}
    }}

    QComboBox#ScopeCombo::down-arrow, QComboBox#AgentCombo::down-arrow {{
        width: 18px;
        height: 18px;
        {combo_arrow_dark}
    }}

    QToolButton#FilterButton::menu-indicator {{
        {combo_arrow_light}
    }}

    QComboBox QAbstractItemView {{
        background-color: {c['cream']};
        color: {c['text_dark']};
        border: 3px solid {c['panel_border_light']};
        selection-background-color: {c['panel_2']};
        selection-color: {c['cream']};
    }}

    QListWidget {{
        background: transparent;
        border: none;
        outline: none;
    }}

    QListWidget::item {{
        background-color: {c['panel_2']};
        color: {c['text_light']};
        border: 3px solid {c['panel_border']};
        border-right: 5px solid {c['blue_bubble_shadow']};
        border-bottom: 5px solid {c['blue_bubble_shadow']};
        padding: 12px;
        margin: 4px 0;
        min-height: 66px;
        font-size: 13px;
    }}

    QListWidget::item:selected {{
        background-color: {c['blue_bubble']};
        border-color: {c['cream']};
        color: {c['cream']};
    }}

    QScrollArea {{
        border: none;
        background: transparent;
    }}

    QScrollArea > QWidget > QWidget {{
        background: transparent;
    }}

    QScrollBar:vertical {{
        background: {c['panel_deep']};
        width: 12px;
        margin: 0;
        border: 2px solid {c['panel_border']};
    }}

    QScrollBar::handle:vertical {{
        background: {c['panel_border_light']};
        min-height: 34px;
    }}

    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
        height: 0px;
    }}

    QScrollBar:horizontal {{
        height: 0px;
    }}

    QGroupBox {{
        border: none;
        margin: 0;
        padding: 0;
        background: transparent;
    }}

    QGroupBox::title {{
        color: transparent;
        height: 0;
    }}

    QStatusBar {{
        background-color: {c['panel']};
        color: {c['text_light']};
        border-top: 2px solid {c['panel_border']};
    }}
    """
