"""Regression test: batch transcription must skip chunks live transcription
already transcribed.

Before the perf rework, stopping a session ran TranscriptionProcessor over
every audio file unconditionally, re-transcribing (and re-inserting into the
DB) chunks live transcription had already saved a row for while recording -
duplicating every line of a session that used live transcription. Database
now records which chunk each row came from (`audio_file`, with a
(source, timestamp) fallback for older rows), and TranscriptionProcessor
skips anything already covered - see TranscriptionProcessor._already_transcribed.
"""
import wave
from datetime import datetime
from pathlib import Path

import numpy as np
import pytest

from src.storage.database import Database
from src.transcription.processor import TranscriptionProcessor


class _FakeEngine:
    """Records which files it was asked to transcribe; never touches a real model."""

    def __init__(self):
        self.calls = []
        self._loaded = True

    def is_loaded(self):
        return self._loaded

    def load(self):
        self._loaded = True

    def transcribe(self, audio_path, initial_prompt=None):
        self.calls.append(str(audio_path))
        return f"text for {Path(audio_path).name}"


def _write_wav(path: Path, seconds: float = 0.2, sample_rate: int = 16000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(np.zeros(int(sample_rate * seconds), dtype=np.int16).tobytes())


@pytest.fixture
def db(tmp_path):
    database = Database(str(tmp_path / "chronicle.db"))
    database.connect()
    yield database
    database.disconnect()


def test_process_all_skips_a_chunk_already_transcribed_live(tmp_path, db):
    session_id = db.create_session("Test session", datetime(2024, 1, 1, 9, 0), status="stopped")

    mic_dir = tmp_path / "audio" / "mic"
    already_done = mic_dir / "20240101_090000_20240101_090005.wav"
    still_needed = mic_dir / "20240101_090004_20240101_090009.wav"
    _write_wav(already_done)
    _write_wav(still_needed)

    # Live transcription already wrote a row for the first chunk, tagged
    # with its audio_file key exactly as SessionManager._on_live_result does.
    db.add_transcript(
        session_id, datetime(2024, 1, 1, 9, 0, 0), "live text", "microphone",
        audio_file=TranscriptionProcessor.chunk_key(already_done),
    )

    engine = _FakeEngine()
    processor = TranscriptionProcessor(str(tmp_path), db=db, engine=engine)
    processor.process_all(session_id)

    assert len(engine.calls) == 1, "only the untranscribed chunk should reach the engine"
    assert "20240101_090004" in engine.calls[0]

    rows = db.get_transcripts(session_id)
    assert len(rows) == 2
    assert sum(r['text'] == 'live text' for r in rows) == 1, "the live row must not be duplicated"


def test_process_all_falls_back_to_source_and_timestamp_for_older_rows(tmp_path, db):
    """A transcript row written before the audio_file column existed (no
    value there) must still be recognized via its (source, timestamp)."""
    session_id = db.create_session("Test session", datetime(2024, 1, 1, 9, 0), status="stopped")

    mic_dir = tmp_path / "audio" / "mic"
    already_done = mic_dir / "20240101_090000_20240101_090005.wav"
    still_needed = mic_dir / "20240101_090004_20240101_090009.wav"
    _write_wav(already_done)
    _write_wav(still_needed)

    # No audio_file - simulates a row from before that column was added.
    db.add_transcript(session_id, datetime(2024, 1, 1, 9, 0, 0), "legacy live text", "microphone")

    engine = _FakeEngine()
    processor = TranscriptionProcessor(str(tmp_path), db=db, engine=engine)
    processor.process_all(session_id)

    assert len(engine.calls) == 1
    assert "20240101_090004" in engine.calls[0]


def test_process_all_transcribes_everything_when_nothing_was_live(tmp_path, db):
    session_id = db.create_session("Test session", datetime(2024, 1, 1, 9, 0), status="stopped")
    mic_dir = tmp_path / "audio" / "mic"
    for name in ("20240101_090000_20240101_090005.wav", "20240101_090005_20240101_090010.wav"):
        _write_wav(mic_dir / name)

    engine = _FakeEngine()
    processor = TranscriptionProcessor(str(tmp_path), db=db, engine=engine)
    processor.process_all(session_id)

    assert len(engine.calls) == 2
    assert len(db.get_transcripts(session_id)) == 2


def test_process_all_loads_no_model_when_everything_is_already_transcribed(tmp_path, db):
    """A session fully covered by live transcription should never load a
    speech model during Stop finalization."""
    session_id = db.create_session("Test session", datetime(2024, 1, 1, 9, 0), status="stopped")
    mic_dir = tmp_path / "audio" / "mic"
    chunk = mic_dir / "20240101_090000_20240101_090005.wav"
    _write_wav(chunk)
    db.add_transcript(
        session_id, datetime(2024, 1, 1, 9, 0, 0), "live text", "microphone",
        audio_file=TranscriptionProcessor.chunk_key(chunk),
    )

    engine = _FakeEngine()
    engine._loaded = False  # so load() would be observable if it were called
    load_calls = []
    engine.load = lambda: load_calls.append(1) or setattr(engine, '_loaded', True)

    processor = TranscriptionProcessor(str(tmp_path), db=db, engine=engine)
    processor.process_all(session_id)

    assert load_calls == [], "process_all() must not eagerly load the model"
    assert engine.calls == []
