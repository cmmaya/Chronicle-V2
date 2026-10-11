"""BU153 - Theme engine: style tokens beyond colour."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtGui import QFont, QPainterPath
from PySide6.QtWidgets import QApplication

from src.app import theme
from tests.theme_harness import run_in_theme

SAMPLE_QSS = """
QPushButton#X {
    font-family: 'Courier New';
    font-size: 15px;
    font-weight: 900;
    letter-spacing: 2px;
    border: 2px solid #254D9C;
    border-left: 3px solid #6FD3FF;
    border-radius: 7px;
}
"""


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def active(monkeypatch):
    """Point the theme module at another theme without activate()'s
    process-wide side effects (stylesheet hook, QFont class)."""
    def use(name):
        monkeypatch.setattr(theme, "_active", name)
    return use


def test_boring_corporate_is_a_listed_theme():
    assert theme.BORING_CORPORATE == "boring_corporate"
    assert list(theme.THEMES)[-1] == theme.BORING_CORPORATE
    assert theme.THEMES[theme.BORING_CORPORATE][0] == "Boring Corporate"
    assert theme.normalize("boring_corporate") == theme.BORING_CORPORATE
    assert theme.read_preference({"ui_theme": "boring_corporate"}) == theme.BORING_CORPORATE


@pytest.mark.parametrize("name", [theme.CLASSIC, theme.SYNTHWAVE])
def test_pixel_themes_restyle_nothing(active, name):
    active(name)
    assert theme.is_pixel()
    assert theme.restyle(SAMPLE_QSS) == SAMPLE_QSS
    assert theme.antialias() is False
    assert theme.pen_width(2) == 2
    assert theme.icon_override("icon_play.svg") is None


def test_corporate_restyle_rewrites_shape_and_type(active):
    active(theme.BORING_CORPORATE)
    out = theme.restyle(SAMPLE_QSS)
    assert 'font-family: "Segoe UI"' in out
    assert "Courier" not in out
    assert "font-weight: 600" in out
    assert "letter-spacing: 0px" in out
    assert "border: 1px solid #254D9C" in out
    assert "border-left: 2px solid" in out  # selection edges stay a touch heavier
    assert "border-radius: 10px" in out
    assert "font-size: 14px" in out


def test_native_marker_keeps_the_rest_as_written(active):
    active(theme.BORING_CORPORATE)
    native = theme.NATIVE_QSS + "QLabel { border: 2px solid #111111; font-size: 18px; }"
    out = theme.restyle(SAMPLE_QSS + native)
    assert out.endswith(native)
    assert "font-weight: 600" in out  # the part before the marker is restyled


def test_remap_restyles_only_in_corporate(active):
    text = "QLabel { color: #FFF0BF; border: 2px solid #254D9C; }"
    active(theme.SYNTHWAVE)
    assert "2px" in theme.remap(text)
    active(theme.BORING_CORPORATE)
    out = theme.remap(text)
    assert "#ECECEC" in out and "1px" in out


def test_rounded_rect_path_is_curved_and_clamped(app):
    path = theme.rounded_rect_path(0, 0, 100, 20, 99)
    types = {path.elementAt(i).type for i in range(path.elementCount())}
    assert QPainterPath.ElementType.CurveToElement in types
    rect = path.boundingRect()
    assert rect.height() == pytest.approx(20)


def test_corner_radius_scale():
    assert theme.corner_radius(3) == 6
    assert theme.corner_radius(5) == 8
    assert theme.corner_radius(9) == 12


def test_font_class_maps_the_pixel_font(app):
    ThemedFont = theme._font_class(QFont, "Segoe UI")
    font = ThemedFont("Courier New")
    assert font.family() == "Segoe UI"
    font.setBold(True)
    assert font.weight() == QFont.Weight.DemiBold
    font.setWeight(QFont.Weight.Black)
    assert font.weight() == QFont.Weight.DemiBold
    font.setLetterSpacing(QFont.AbsoluteSpacing, 2)
    assert font.letterSpacing() == 0
    assert ThemedFont("Arial").family() == "Arial"
    assert ThemedFont("Courier New", 10, QFont.Weight.Black).weight() == QFont.Weight.DemiBold


def test_activate_installs_the_font_class_only_for_corporate():
    out = run_in_theme(theme.BORING_CORPORATE, """
        from PySide6.QtGui import QFont
        print(QFont("Courier New").family())
        print(bool(theme.is_corporate()), bool(theme.is_pixel()))
    """)
    assert out.split("\n")[:2] == ["Segoe UI", "True False"]

    out = run_in_theme(theme.CLASSIC, """
        from PySide6.QtGui import QFont
        print(QFont("Courier New").family())
    """)
    assert out.strip() == "Courier New"
