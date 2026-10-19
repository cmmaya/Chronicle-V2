"""BU157 - Main window layout in Boring Corporate."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QLabel, QWidget

from src.app import theme
from src.app.window import MainWindow


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


class _EmptyState:
    """Just the attributes the empty-chat helpers read."""

    _chat_is_empty = MainWindow._chat_is_empty
    _sync_empty_chat_state = MainWindow._sync_empty_chat_state

    def __init__(self):
        # Parented to one host: showing a parentless widget opens a window.
        self.host = QWidget()
        self._answer_scroll_area = QWidget(self.host)
        self._answer_container = QWidget(self._answer_scroll_area)
        self._empty_chat_top = QWidget(self.host)
        self._empty_chat_heading = QLabel("Where should we begin?", self.host)
        self._empty_chat_bottom = QWidget(self.host)
        self._empty_chat_shown = None


def test_answers_are_plain_text_only_in_corporate(monkeypatch):
    monkeypatch.setattr(theme, "_active", theme.CLASSIC)
    assert MainWindow._answer_variant() == "blue"
    assert MainWindow._chat_bubble_width("assistant") == 400
    monkeypatch.setattr(theme, "_active", theme.BORING_CORPORATE)
    assert MainWindow._answer_variant() == "plain"
    assert MainWindow._chat_bubble_width("assistant") > MainWindow._chat_bubble_width("user")


def test_empty_chat_state_follows_the_rows(app):
    state = _EmptyState()
    state._sync_empty_chat_state()
    assert state._chat_is_empty()
    assert state._empty_chat_shown is True
    assert state._answer_scroll_area.isHidden()
    assert not state._empty_chat_heading.isHidden()

    row = QLabel("a question", state._answer_container)
    assert not state._chat_is_empty()
    state._sync_empty_chat_state()
    assert state._empty_chat_shown is False
    assert not state._answer_scroll_area.isHidden()
    assert state._empty_chat_heading.isHidden()
    assert state._empty_chat_top.isHidden() and state._empty_chat_bottom.isHidden()

    row.setParent(None)
    state._sync_empty_chat_state()
    assert state._empty_chat_shown is True


def test_send_button_tracks_the_question(app):
    class _Send:
        _sync_send_button = MainWindow._sync_send_button

    from PySide6.QtWidgets import QTextEdit, QToolButton

    holder = _Send()
    holder.send_button = QToolButton()
    holder.question_input = QTextEdit()
    holder._sync_send_button()
    assert not holder.send_button.isEnabled()
    holder.question_input.setPlainText("what was said about coffee?")
    holder._sync_send_button()
    assert holder.send_button.isEnabled()
