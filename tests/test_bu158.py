"""BU158 - Secondary windows in Boring Corporate."""
import json

from src.app import theme
from tests.theme_harness import run_in_theme


def test_scrollbar_qss_is_complete_and_native():
    qss = theme.scrollbar_qss()
    assert qss.startswith(theme.NATIVE_QSS)
    for selector in ("QScrollBar:vertical", "QScrollBar::handle:vertical",
                     "QScrollBar::add-line", "QScrollBar::add-page"):
        assert selector in qss


PROBE = """
import json
from PySide6.QtWidgets import QLabel
from src.app import pixel_widgets as pw

header = pw.pixel_group_header("Yesterday", 3)
labels = [w.text() for w in header.findChildren(QLabel)]
card = pw.PixelSessionCard("DR session 2", "14:10", True, True)
gap = pw.transcript_gap_separator("12 min pause")
print(json.dumps({
    "header": labels,
    "group_label": pw.pixel_group_label("DETECT").text(),
    "caption": pw.pixel_caption("NEW SESSION", "#FFFFFF", 9, True, 2.5).text(),
    "open_primary": "#F9F9F9" in card.open_button.styleSheet(),
    "actions_ghost": "transparent" in card.actions_button.styleSheet(),
    "gap": [w.text() for w in gap.findChildren(QLabel)],
}))
"""


def _probe(name):
    return json.loads(run_in_theme(name, PROBE).strip().splitlines()[-1])


def test_corporate_secondary_widgets():
    got = _probe(theme.BORING_CORPORATE)
    assert got["header"][0] == "Yesterday"
    assert got["group_label"] == "Detect"
    assert got["caption"] == "New session"
    assert got["open_primary"] and got["actions_ghost"]
    assert got["gap"] == ["12 min pause"]


def test_classic_secondary_widgets_unchanged():
    got = _probe(theme.CLASSIC)
    assert got["header"][0] == "YESTERDAY"
    assert got["group_label"] == "DETECT"
    assert got["caption"] == "NEW SESSION"
    assert not got["open_primary"]
    assert got["gap"] == ["12 MIN PAUSE"]
