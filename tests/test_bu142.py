"""BU142: Specific Session evidence is chronological, timestamped, uncut,
and says when the transcript is still growing."""
from datetime import datetime
from unittest.mock import MagicMock

from src.assistant.service import AssistantAnswerService

T = int(datetime(2026, 10, 7, 21, 0, 0).timestamp())


def _service():
    return AssistantAnswerService(MagicMock())


def _t(offset, text):
    return {'session_id': 1, 'session_name': 'S', 'timestamp': T + offset,
            'source': 'system', 'text': text}


def test_transcripts_render_in_time_order_with_timestamps():
    long_text = 'x' * 700
    ev = _service()._question_evidence_prompt({'transcripts': [
        _t(600, 'later'), _t(0, long_text)]})

    lines = [l for l in ev.splitlines() if l.startswith('[')]
    assert lines[0] == f'[21:00:00] (Sys): {long_text}'
    assert lines[1] == '[21:10:00] (Sys): later'


def test_no_note_for_finished_indexed_session():
    ev = _service()._question_evidence_prompt({
        'transcripts': [_t(0, 'a')],
        'transcript_state': {'live': False, 'indexed': True, 'until': T}})
    assert 'Note:' not in ev


def test_live_note_names_cutoff_and_missing_summary():
    ev = _service()._question_evidence_prompt({
        'transcripts': [_t(0, 'a')],
        'transcript_state': {'live': True, 'indexed': False, 'until': T + 90}})
    assert 'still being recorded' in ev
    assert 'up to 21:01:30' in ev
    assert 'no summary yet' in ev


def test_just_stopped_note_omits_summary_line_when_summary_exists():
    ev = _service()._question_evidence_prompt({
        'transcripts': [_t(0, 'a')],
        'summaries': [{'summary_type': 'full', 'content': 's'}],
        'transcript_state': {'live': False, 'indexed': False, 'until': T}})
    assert 'just stopped' in ev
    assert 'no summary yet' not in ev
