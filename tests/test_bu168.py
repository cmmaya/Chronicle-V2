"""BU168 - speaker-labelled transcript view (offscreen Qt)."""
import json
import os
from datetime import datetime
from unittest.mock import MagicMock

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from PySide6.QtWidgets import QApplication, QComboBox, QVBoxLayout, QWidget

from src.app import theme
from src.app.pixel_widgets import bubble_of_row
from src.app.window import MainWindow, TRANSCRIPT_FILTERS, speaker_layout
from tests.theme_harness import run_in_theme

T0 = datetime(2026, 10, 1, 10, 0).timestamp()


@pytest.fixture(scope='module')
def qapp():
    return QApplication.instance() or QApplication([])


def _rows():
    return [
        {'timestamp': T0, 'end_timestamp': T0 + 5, 'text': 'short question', 'source': 'microphone', 'speaker': 1},
        {'timestamp': T0 + 10, 'end_timestamp': T0 + 70, 'text': 'long lecture part', 'source': 'microphone',
         'speaker': 2},
        {'timestamp': T0 + 80, 'end_timestamp': T0 + 90, 'text': 'another voice', 'source': 'system', 'speaker': 3},
    ]


def test_speaker_layout_primary_is_most_talk():
    assert speaker_layout(_rows()) == {'primary': 2, 'speakers': [1, 2, 3]}
    assert speaker_layout([{'timestamp': T0, 'text': 'x', 'source': 'microphone'}]) is None


def _host(layout=None, filter_value='all'):
    host = MagicMock()
    container = QWidget()
    host._container = container
    host._transcription_layout = QVBoxLayout(container)
    host._transcription_layout.addStretch()
    host._transcript_groups = {}
    host._transcription_filter = filter_value
    host._speaker_layout = layout
    host._transcription_filter_combo = QComboBox()
    host._detached_filter_combo = None
    host._transcript_filter_options = MainWindow._transcript_filter_options.__get__(host)
    return host


def _add(host, row):
    start = datetime.fromtimestamp(row['timestamp'])
    end = datetime.fromtimestamp(row.get('end_timestamp') or row['timestamp'])
    source = 'mic' if row['source'] == 'microphone' else 'system'
    MainWindow.add_transcription_to_view(host, row['text'], source, '', start_dt=start, end_dt=end,
                                         animate=False, speaker=row.get('speaker'))


def _bubbles(host):
    layout = host._transcription_layout
    rows = [layout.itemAt(i).widget() for i in range(layout.count() - 1)]
    return [(r, bubble_of_row(r)) for r in rows]


def test_speaker_rows_render_speaker_labels(qapp):
    host = _host(speaker_layout(_rows()))
    for row in _rows():
        _add(host, row)
    found = _bubbles(host)
    assert [r.property('source') for r, _b in found] == ['speaker:1', 'speaker:2', 'speaker:3']
    assert all(f"Speaker {n}:" in b.label.text() for n, (_r, b) in zip((1, 2, 3), found))
    assert [r.property('find_text') for r, _b in found][1] == 'Speaker 2: long lecture part'
    # The most-talk speaker is on the right, in blue; everyone else left, cream.
    assert [(b.tail, b.variant) for _r, b in found] == [
        ('left', 'cream'), ('right', 'blue'), ('left', 'cream')]
    assert theme.speaker_color(2, on_blue=True) in found[1][1].label.text()
    assert theme.speaker_color(3, on_blue=False) in found[2][1].label.text()


def test_original_rows_render_mic_and_system(qapp):
    host = _host()
    for row in _rows():
        _add(host, {k: v for k, v in row.items() if k != 'speaker'})
    labels = [b.label.text() for _r, b in _bubbles(host)]
    assert labels[0].startswith('Mic:') and labels[-1].startswith('System:')
    assert all('Speaker' not in text for text in labels)


def test_consecutive_turns_merge_but_not_across_speakers(qapp):
    host = _host({'primary': 1, 'speakers': [1, 2]})
    for i, speaker in enumerate((1, 1, 2, 1)):
        _add(host, {'timestamp': T0 + 2 * i, 'end_timestamp': T0 + 2 * i + 1, 'text': f't{i}',
                    'source': 'microphone', 'speaker': speaker})
    assert [r.property('source') for r, _b in _bubbles(host)] == ['speaker:1', 'speaker:2', 'speaker:1']


def test_speaker_filter_hides_other_speakers(qapp):
    host = _host(speaker_layout(_rows()))
    for row in _rows():
        _add(host, row)
    host._transcription_filter = 'speaker:2'
    MainWindow._update_transcription_filter(host)
    assert [r.isHidden() for r, _b in _bubbles(host)] == [True, False, True]


def test_filter_menu_lists_speakers_and_resets_on_switch(qapp):
    host = _host(speaker_layout(_rows()), filter_value='mic')
    MainWindow._sync_transcript_filter_options(host)
    combo = host._transcription_filter_combo
    assert [combo.itemText(i) for i in range(combo.count())] == ['All', 'Speaker 1', 'Speaker 2', 'Speaker 3']
    assert host._transcription_filter == 'all'

    host._transcription_filter = 'speaker:2'
    host._speaker_layout = None  # diarization switched off
    MainWindow._sync_transcript_filter_options(host)
    assert [combo.itemData(i) for i in range(combo.count())] == list(TRANSCRIPT_FILTERS)
    assert host._transcription_filter == 'all'


PROBE = """
import json
from src.app.pixel_widgets import aligned_bubble_with_time, bubble_of_row
row = aligned_bubble_with_time("hello", variant="cream", align="left",
                               tag=("Speaker 2", theme.speaker_color(2, on_blue=False)))
print(json.dumps({"html": bubble_of_row(row).label.text(),
                  "colors": theme.SPEAKER_COLORS[theme.active()]}))
"""


def test_corporate_speaker_tags_use_its_tokens():
    got = json.loads(run_in_theme(theme.BORING_CORPORATE, PROBE).strip().splitlines()[-1])
    assert got["colors"] == [list(c) for c in theme.SPEAKER_COLORS[theme.BORING_CORPORATE]]
    assert theme.SPEAKER_COLORS[theme.BORING_CORPORATE][1][0] in got["html"]
    assert "Speaker 2:" in got["html"]


def _luminance(color):
    channels = [int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    channels = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def _contrast(a, b):
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


# Bubble fills per appearance: (cream, blue), after the theme's remap.
FILLS = {theme.CLASSIC: ('#F6E0A6', '#294F9D'), theme.SYNTHWAVE: ('#FDD865', '#001E66'),
         theme.BORING_CORPORATE: ('#262626', '#171718')}


@pytest.mark.parametrize('name', list(FILLS))
def test_speaker_colours_contrast_on_both_fills(name):
    cream, blue = FILLS[name]
    assert theme.REMAP[name].get('#F6E0A6', '#F6E0A6') == cream
    assert theme.REMAP[name].get('#294F9D', '#294F9D') == blue
    colors = theme.SPEAKER_COLORS[name]
    assert len(colors) == 6
    for on_cream, on_blue in colors:
        assert _contrast(on_cream, cream) >= 4.0
        assert _contrast(on_blue, blue) >= 4.0
