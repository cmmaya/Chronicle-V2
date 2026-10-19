"""BU155 - Line icon set."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

import pytest
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication

from src import paths
from src.app import theme

PIXEL = Path(paths.asset_path("pixel"))
CORP = Path(paths.asset_path("themes", theme.BORING_CORPORATE, "icons"))


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def test_every_pixel_icon_has_a_line_icon():
    missing = sorted(p.name for p in PIXEL.glob("*.svg") if not (CORP / p.name).is_file())
    assert missing == []


def test_line_icons_are_valid_thin_strokes(app):
    for path in CORP.glob("*.svg"):
        text = path.read_text(encoding="utf-8")
        assert "crispEdges" not in text, path.name
        assert 'stroke-linecap="round"' in text, path.name
        assert QSvgRenderer(str(path)).isValid(), path.name


def test_send_icon_and_licence_ship():
    assert (CORP / "icon_send.svg").is_file()
    assert "ISC License" in (CORP / "LICENSE-lucide.txt").read_text(encoding="utf-8")


def test_icon_override_follows_the_theme(monkeypatch):
    monkeypatch.setattr(theme, "_active", theme.BORING_CORPORATE)
    assert Path(theme.icon_override("icon_mic_on.svg")) == CORP / "icon_mic_on.svg"
    assert theme.icon_override("no_such_icon.svg") is None
    monkeypatch.setattr(theme, "_active", theme.CLASSIC)
    assert theme.icon_override("icon_mic_on.svg") is None


def test_asset_path_prefers_the_theme_icon(monkeypatch):
    from src.app import pixel_theme

    monkeypatch.setattr(theme, "_active", theme.BORING_CORPORATE)
    assert Path(pixel_theme.asset_path("icon_play.svg")) == CORP / "icon_play.svg"
    monkeypatch.setattr(theme, "_active", theme.CLASSIC)
    assert Path(pixel_theme.asset_path("icon_play.svg")) == PIXEL / "icon_play.svg"
