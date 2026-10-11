"""BU159 - Dialogs in Boring Corporate."""
import json

from src.app import theme
from tests.theme_harness import run_in_theme

PROBE = """
import json
from PySide6.QtGui import QPalette
from src.app import settings_dialog as sd, setup_wizard as sw, pixel_widgets as pw

toggle = sd.PixelToggle(True)
preview = sd._ThemePreview(theme.BORING_CORPORATE)
preview.resize(200, 92)
image = preview.grab().toImage()
centre = image.pixelColor(4, image.height() - 6)
palette = theme.app_palette()
print(json.dumps({
    "toggle": [toggle.width(), toggle.height()],
    "slider_native": sd._SLIDER_QSS.startswith(theme.NATIVE_QSS),
    "nav_checked": "QPushButton#SettingsNav:checked { background: #212121" in sd._NAV_QSS,
    "wizard": "border-radius: 15px" in sw.WIZARD_QSS,
    "primary": pw.PRIMARY_BUTTON_QSS.startswith(theme.NATIVE_QSS),
    "preview_corner": [centre.red(), centre.green(), centre.blue()],
    "window": palette.color(QPalette.Window).name(),
    "text": palette.color(QPalette.WindowText).name(),
    "app_qss": "QMessageBox QPushButton" in theme.app_wide_qss(),
}))
"""


def _probe(name):
    return json.loads(run_in_theme(name, PROBE).strip().splitlines()[-1])


def test_corporate_dialog_styles():
    got = _probe(theme.BORING_CORPORATE)
    assert got["toggle"] == [40, 22]
    assert got["slider_native"] and got["nav_checked"]
    assert got["wizard"] and got["primary"]
    assert got["preview_corner"] == [0, 0, 0]  # the thumbnail is a black canvas
    assert got["window"] == "#171717" and got["text"] == "#ececec"
    assert got["app_qss"]


def test_classic_dialog_styles_unchanged():
    got = _probe(theme.CLASSIC)
    assert got["toggle"] == [60, 28]
    assert not got["slider_native"] and not got["nav_checked"]
    assert not got["wizard"] and not got["primary"]
    assert theme.app_wide_qss() == ""  # nothing installed app-wide for pixel themes
