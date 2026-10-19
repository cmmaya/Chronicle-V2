"""BU139: inserted transcript mode in the detached transcripts window."""
import os
from datetime import datetime
from unittest.mock import MagicMock

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QVBoxLayout

from src.app.pixel_widgets import PixelToolButton, pixel_empty_hint, pixel_filter_chip
from src.app.window import MainWindow, TranscriptRecord


@pytest.fixture(scope='module')
def qapp():
    return QApplication.instance() or QApplication([])


def _bind(host, *names):
    for name in names:
        setattr(host, name, getattr(MainWindow, name).__get__(host))


@pytest.fixture
def host(qapp):
    h = MagicMock()
    h._displaying_inserted_transcript = False
    h._live_qa_mode = 'suggest'
    h._live_qa_answer_mode = 'general'
    h.DETACHED_ANSWERS_HINT = MainWindow.DETACHED_ANSWERS_HINT
    h.DETACHED_ANSWERS_HINT_INSERTED = MainWindow.DETACHED_ANSWERS_HINT_INSERTED
    h._detached_window = object()
    h._detached_inserted_caption = MainWindow._make_inserted_caption(Qt.AlignLeft)
    h._detached_filter_button = PixelToolButton(compact=True)
    h._live_qa_settings_button = PixelToolButton(compact=True)
    h._live_qa_mode_chips = {m: pixel_filter_chip(m) for m in ('manual', 'suggest', 'auto')}
    h._live_qa_answer_mode_chips = {m: pixel_filter_chip(m) for m in ('transcripts', 'general')}
    h._detached_answers_empty = pixel_empty_hint(MainWindow.DETACHED_ANSWERS_HINT)
    h._detached_layout = QVBoxLayout()
    h._detached_layout.addStretch()
    h._detached_transcript_groups = {}
    h._detached_last_end = None
    h._detached_filter_combo = None
    _bind(h, '_sync_detached_inserted_mode', '_sync_live_qa_mode_chips',
          '_sync_live_qa_answer_mode_chips', '_add_transcription_to_detached')
    h._sync_detached_inserted_mode()  # a recorded session is displayed
    return h


def _checked(chips):
    return {m for m, c in chips.items() if c.isChecked()}


def _controls(host):
    return [host._detached_filter_button, host._live_qa_settings_button,
            *host._live_qa_mode_chips.values(), *host._live_qa_answer_mode_chips.values()]


def test_inserted_mode_shows_caption_and_disables_controls(host):
    host._displaying_inserted_transcript = True

    host._sync_detached_inserted_mode()

    assert not host._detached_inserted_caption.isHidden()
    assert _checked(host._live_qa_mode_chips) == set()
    assert _checked(host._live_qa_answer_mode_chips) == set()
    assert not any(c.isEnabled() for c in _controls(host))
    assert 'inserted' in host._detached_answers_empty.text()


def test_inserted_bubbles_have_no_selection_filter(host):
    host._add_transcription_to_detached(
        TranscriptRecord('plain paragraph', 'inserted', None, None, 1, 'plain paragraph'), 0)

    host._install_bubble_selection.assert_not_called()
    row = host._detached_layout.itemAt(0).widget()
    assert row.property('find_text') == 'plain paragraph'
    assert row.property('source') == 'inserted'

    when = datetime(2026, 1, 1, 10, 0)
    host._add_transcription_to_detached(
        TranscriptRecord('live words', 'mic', when, when, None, 'Mic: live words'), 1)
    host._install_bubble_selection.assert_called_once()


def test_recorded_session_restores_saved_modes(host):
    host._displaying_inserted_transcript = True
    host._sync_detached_inserted_mode()

    host._displaying_inserted_transcript = False
    host._sync_detached_inserted_mode()

    assert host._detached_inserted_caption.isHidden()
    assert _checked(host._live_qa_mode_chips) == {'suggest'}
    assert _checked(host._live_qa_answer_mode_chips) == {'general'}
    assert all(c.isEnabled() for c in _controls(host))
    assert host._detached_answers_empty.text() == MainWindow.DETACHED_ANSWERS_HINT
    assert host._live_qa_mode == 'suggest' and host._live_qa_answer_mode == 'general'
