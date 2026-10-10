"""BU138: inserted transcript view in the main Transcripts panel."""
import os
from datetime import datetime
from unittest.mock import MagicMock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout

from src.app import window as window_module
from src.app.pixel_widgets import PixelToolButton
from src.app.window import MainWindow
from src.storage.database import Database, SESSION_ORIGIN_TEXT, TRANSCRIPT_SOURCE_INSERTED

PARAGRAPHS = ['First paragraph of the meeting.', 'Second paragraph with more detail.']


@pytest.fixture(scope='module')
def qapp():
    return QApplication.instance() or QApplication([])


def _bind(host, *names):
    for name in names:
        setattr(host, name, getattr(MainWindow, name).__get__(host))


@pytest.fixture
def host(qapp):
    h = MagicMock()
    db = Database(':memory:')
    db.connect()
    h.session_manager.db = db
    h._transcript_records = []
    h._displaying_inserted_transcript = False
    h._transcription_filter = 'all'
    h._transcript_groups = {}
    h._detached_window = None
    h._detached_layout = None
    h._inserted_transcript_caption = QLabel('Inserted Transcript')
    h._inserted_transcript_caption.setVisible(False)
    h.transcript_filter_button = PixelToolButton(compact=True)
    h._transcription_layout = QVBoxLayout()
    h._transcription_layout.addStretch()
    _bind(h, '_sync_inserted_transcript_chrome', '_add_inserted_paragraph_to_view',
          '_clear_transcription_view', '_load_transcripts_for_session',
          'add_transcription_to_view')
    # Real property semantics for the shim the download path reads.
    type(h)._transcription_history = MainWindow._transcription_history
    yield h
    db.disconnect()


def _inserted_session(db):
    sid = db.create_session('meeting', datetime(2026, 1, 1, 10, 0), status='stopped',
                            origin=SESSION_ORIGIN_TEXT)
    for i, text in enumerate(PARAGRAPHS):
        db.add_transcript(sid, datetime(2026, 1, 1, 10, 0, i), text, TRANSCRIPT_SOURCE_INSERTED)
    return sid


def _recorded_session(db):
    sid = db.create_session('call', datetime(2026, 1, 1, 11, 0), status='stopped')
    db.add_transcript(sid, datetime(2026, 1, 1, 11, 0, 0), 'hello there', 'microphone')
    return sid


def _row_texts(host):
    layout = host._transcription_layout
    return [layout.itemAt(i).widget() for i in range(layout.count() - 1)]


def test_inserted_session_renders_plain_bubbles_and_caption(host):
    sid = _inserted_session(host.session_manager.db)

    host._load_transcripts_for_session(sid)

    rows = _row_texts(host)
    assert [r.property('find_text') for r in rows] == PARAGRAPHS
    assert all('Mic:' not in r.property('find_text') and 'System:' not in r.property('find_text')
               for r in rows)
    assert not host._inserted_transcript_caption.isHidden()
    assert [r.display_text for r in host._transcript_records] == PARAGRAPHS
    assert {r.source for r in host._transcript_records} == {'inserted'}


def test_filter_button_disabled_only_for_inserted(host):
    db = host.session_manager.db

    host._load_transcripts_for_session(_inserted_session(db))
    assert not host.transcript_filter_button.isEnabled()
    assert 'Not available' in host.transcript_filter_button.toolTip()

    host._clear_transcription_view()
    host._load_transcripts_for_session(_recorded_session(db))
    assert host.transcript_filter_button.isEnabled()
    assert host._inserted_transcript_caption.isHidden()
    assert host.transcript_filter_button.toolTip() == 'Cycle transcript filter'


def test_clear_hides_caption(host):
    host._load_transcripts_for_session(_inserted_session(host.session_manager.db))
    assert not host._inserted_transcript_caption.isHidden()

    host._clear_transcription_view()

    assert host._inserted_transcript_caption.isHidden()
    assert host.transcript_filter_button.isEnabled()
    assert host._displaying_inserted_transcript is False


def test_live_chunks_are_ignored_while_inserted_is_displayed(host):
    host._load_transcripts_for_session(_inserted_session(host.session_manager.db))
    _bind(host, '_append_transcription')

    host._append_transcription('live words', 'mic', '', '')

    assert len(host._transcript_records) == len(PARAGRAPHS)
    assert len(_row_texts(host)) == len(PARAGRAPHS)


def test_download_joins_paragraphs_with_blank_lines(host, tmp_path):
    host._load_transcripts_for_session(_inserted_session(host.session_manager.db))
    host._selected_session_id = None
    host._resolve_transcript_session_name = lambda: 'meeting'
    host._on_status_update = MagicMock()
    _bind(host, '_on_download_transcripts')

    with patch.object(window_module.QStandardPaths, 'writableLocation',
                      return_value=str(tmp_path)):
        host._on_download_transcripts()

    files = list(tmp_path.glob('chronicle_transcript_meeting_*.txt'))
    assert len(files) == 1
    assert files[0].read_text(encoding='utf-8') == '\n\n'.join(PARAGRAPHS)
