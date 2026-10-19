"""BU156 - Core widgets in Boring Corporate."""
import json

from src.app import theme
from src.app.pixel_widgets import _sentence_case, corporate_button_qss
from tests.theme_harness import run_in_theme

PROBE = """
import json
from PySide6.QtCore import QSize
from PySide6.QtGui import QPainterPath
from src.app import pixel_widgets as pw

title = pw.PixelSectionTitle("PAST CONVERSATIONS")
title.setText("TRANSCRIPTS WINDOW")
plain = pw.PixelBubble("hello", variant="plain")
user = pw.PixelBubble("hello", variant="cream")
tool = pw.PixelToolButton()
tool.setIconSize(QSize(28, 28))
nav = pw.PixelButton("New Chat", sidebar=True)
button = pw.PixelButton("Done")
button.show()  # a hidden widget gets its resize event only when shown
button.resize(120, 36)
app.processEvents()
path = pw.pixel_round_rect_path(0, 0, 100, 40, 7)
curved = any(path.elementAt(i).type == QPainterPath.ElementType.CurveToElement
             for i in range(path.elementCount()))
print(json.dumps({
    "title": title.text(),
    "plain_variant": plain.variant,
    "tail": plain.tail_size,
    "user_tail": user.tail_size,
    "icon": [tool.iconSize().width(), tool.iconSize().height()],
    "nav_flat": "background: transparent" in nav.styleSheet(),
    "pill_radius": "border-radius: 18px" in button.styleSheet(),
    "curved": curved,
    "find": list(pw.FIND_TERM_ON_BLUE),
}))
"""


def _probe(name):
    return json.loads(run_in_theme(name, PROBE).strip().splitlines()[-1])


def test_sentence_case():
    assert _sentence_case("PAST CONVERSATIONS") == "Past conversations"
    assert _sentence_case("Key Points") == "Key Points"
    assert _sentence_case("") == ""


def test_button_qss_radius_is_half_the_height():
    qss = corporate_button_qss("primary", "QToolButton", 32)
    assert qss.startswith(theme.NATIVE_QSS)
    assert "border-radius: 16px" in qss
    assert "#F9F9F9" in qss and "#0D0D0D" in qss
    assert "transparent" in corporate_button_qss("ghost", "QToolButton", 30)


def test_corporate_widgets():
    got = _probe(theme.BORING_CORPORATE)
    assert got["title"] == "Transcripts window"
    assert got["plain_variant"] == "plain"
    assert got["tail"] == 0 and got["user_tail"] == 0
    assert got["icon"] == [20, 20]
    assert got["nav_flat"] is True
    assert got["pill_radius"] is True
    assert got["curved"] is True
    assert got["find"] == [theme.ROLES[theme.BORING_CORPORATE]["find_fill"],
                           theme.ROLES[theme.BORING_CORPORATE]["find_text"]]


def test_classic_widgets_unchanged():
    got = _probe(theme.CLASSIC)
    assert got["title"] == "TRANSCRIPTS WINDOW"
    assert got["plain_variant"] == "blue"  # pixel themes always draw the bubble
    assert got["tail"] == 13
    assert got["icon"] == [28, 28]
    assert got["nav_flat"] is False
    assert got["curved"] is False
