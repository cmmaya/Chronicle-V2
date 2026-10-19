"""BU167 - diarization progress bar and Speakers chip on the session card."""
import json
import os
from unittest.mock import MagicMock

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from src.app import theme
from src.app.pixel_widgets import PixelCardProgress, PixelSessionCard
from src.app.window import MainWindow
from tests.theme_harness import run_in_theme


@pytest.fixture(scope='module')
def qapp():
    return QApplication.instance() or QApplication([])


def _card():
    return PixelSessionCard('Lecture', '10:00 – 11:00', True, True)


def _host(progress=None, diarizing=False, error=None):
    host = MagicMock()
    host._diarization_progress = dict(progress or {})
    host._all_sessions_dialog = None
    host.session_manager.is_diarizing.return_value = diarizing
    host.session_manager.diarization_error.return_value = error
    return host


def _session(**extra):
    session = {'id': 5, 'diarization_status': 'none', 'diarization_enabled': False, 'speaker_count': 0}
    session.update(extra)
    return session


def test_strip_hidden_idle_shown_running_hidden_after_100(qapp):
    card = _card()
    strip = card.progress
    assert strip.isHidden()
    strip.set_progress(40, 'Finding speakers…')
    assert not strip.isHidden()
    assert strip.text_label.text() == 'Finding speakers…'
    QTest.qWait(800)
    assert strip.value() == pytest.approx(40)
    assert strip.percent_label.text() == '40%'

    done = []
    strip.done.connect(lambda: done.append(True))
    strip.set_progress(100, 'Done')
    QTest.qWait(100)
    assert not strip.isHidden()  # holding at 100
    QTest.qWait(PixelCardProgress.HOLD_MS + 400)
    assert strip.isHidden() and done == [True]


def test_bar_eases_instead_of_jumping(qapp):
    card = _card()
    card.progress.set_progress(10, 'Finding speakers…', animate=False)
    card.progress.set_progress(80, 'Finding speakers…')
    QTest.qWait(120)
    assert 10 < card.progress.value() < 80


def test_progress_survives_reload_and_reopen(qapp):
    host = _host()
    # Reported while no All Sessions window is open: kept on the main window.
    MainWindow._apply_diarization_progress(host, 5, 62, 'Transcribing speakers 3/9…')
    assert host._diarization_progress == {5: (62, 'Transcribing speakers 3/9…')}
    for _ in range(2):  # a reload, then the window reopened: fresh cards each time
        card = _card()
        MainWindow._apply_diarization_card_state(host, card, _session())
        assert not card.progress.isHidden()
        assert card.progress.value() == 62
        assert card.progress.text_label.text() == 'Transcribing speakers 3/9…'


def test_queued_session_shows_strip(qapp):
    card = _card()
    MainWindow._apply_diarization_card_state(_host(diarizing=True), card, _session())
    assert not card.progress.isHidden() and card.progress.text_label.text() == 'Queued…'


def test_finished_card_fills_then_reloads(qapp):
    host = _host({5: (90, 'Saving…')})
    card = _card()
    card.progress.set_progress(90, 'Saving…', animate=False)
    host._diarization_card.return_value = card
    MainWindow._apply_diarization_finished(host, 5, True, None)
    assert host._diarization_progress[5] == (100, 'Done')
    MainWindow._on_diarization_strip_done(host, 5)
    assert 5 not in host._diarization_progress


def test_speakers_chip(qapp):
    card = _card()
    MainWindow._apply_diarization_card_state(_host(), card, _session(speaker_count=3))
    assert card.speakers_chip.isHidden()  # not enabled
    MainWindow._apply_diarization_card_state(
        _host(), card, _session(speaker_count=3, diarization_enabled=True, diarization_status='ready'))
    assert not card.speakers_chip.isHidden()
    assert card.speakers_chip.count_label.text() == '3'
    assert card.speakers_chip.toolTip().startswith('3 speakers')

    MainWindow._apply_diarization_card_state(
        _host(error='No speech was found'), card, _session(diarization_status='failed'))
    assert not card.speakers_chip.isHidden() and card.speakers_chip.has_error()
    assert 'No speech was found' in card.speakers_chip.toolTip()


PROBE = """
import json
from src.app import pixel_widgets as pw
card = pw.PixelSessionCard("Lecture", "10:00", True, True)
card.progress.set_progress(30, "FINDING SPEAKERS…")
print(json.dumps({
    "text": card.progress.text_label.text(),
    "track_height": card.progress.track.height(),
    "chunk": theme.progress_color("chunk"),
}))
"""


def _probe(name):
    return json.loads(run_in_theme(name, PROBE).strip().splitlines()[-1])


def test_corporate_strip_sentence_case_rounded_track():
    got = _probe(theme.BORING_CORPORATE)
    assert got == {"text": "Finding speakers…", "track_height": 4, "chunk": "#F9F9F9"}


def test_classic_strip_pixel_track():
    got = _probe(theme.CLASSIC)
    assert got == {"text": "FINDING SPEAKERS…", "track_height": 6, "chunk": "#E37C25"}
