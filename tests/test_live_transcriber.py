"""Tests for LiveTranscriber's per-stream context/dedup state.

Before the perf rework, LiveTranscriber kept exactly one `_last_text` /
`_context` shared across every chunk it ever saw. Mic and system audio (and
different sessions once resume-into-the-same-session-id and the shared engine
made cross-session reuse possible) are two independent, unrelated audio
streams: comparing a mic chunk against the previous *system* chunk finds no
real overlap and can strip words that merely start the same way. State is now
keyed per (session_id, source) "stream" - these tests lock that in.
"""
import tempfile
from pathlib import Path

import pytest

from src.audio_capture.chunk import AudioChunk
from src.transcription.live import LiveTranscriber


class _FakeEngine:
    """Returns canned text per call, in order; never touches a real model."""

    def __init__(self, texts):
        self._texts = list(texts)
        self.calls = 0
        self._loaded = True

    def is_loaded(self):
        return self._loaded

    def load(self):
        self._loaded = True

    def transcribe(self, audio_path, initial_prompt=None):
        text = self._texts[self.calls]
        self.calls += 1
        return text


@pytest.fixture
def audio_file(tmp_path):
    path = tmp_path / "chunk.wav"
    path.write_bytes(b"")  # transcribe_chunk only checks the file exists
    return path


def _chunk(path: Path, source: str, chunk_id: str) -> AudioChunk:
    return AudioChunk(
        source=source, chunk_id=chunk_id,
        timestamp_start="2024-01-01T09:00:00", timestamp_end="2024-01-01T09:00:05",
        file_path=str(path),
    )


class TestDeduplicatePure:
    """`_deduplicate(new_text, previous_text)` is a pure function of its two
    text arguments (no longer of `self._last_text`) - test it directly."""

    def test_strips_exact_two_word_overlap(self):
        transcriber = LiveTranscriber(engine=_FakeEngine([]))
        result = transcriber._deduplicate("more time please", "need more time")
        assert result == "please"

    def test_no_previous_text_returns_input_unchanged(self):
        transcriber = LiveTranscriber(engine=_FakeEngine([]))
        assert transcriber._deduplicate("more time please", None) == "more time please"
        assert transcriber._deduplicate("more time please", "") == "more time please"

    def test_no_overlap_returns_input_unchanged(self):
        transcriber = LiveTranscriber(engine=_FakeEngine([]))
        result = transcriber._deduplicate("completely different words", "need more time")
        assert result == "completely different words"


class TestPerStreamState:
    def test_dedup_state_is_isolated_between_streams(self, tmp_path):
        """A brand-new stream must not be affected by another stream's last
        text, even when that other stream's text happens to be a prefix
        match - this is exactly the bug shared global state had."""
        engine = _FakeEngine(["need more time", "more time please"])
        transcriber = LiveTranscriber(engine=engine)

        mic_chunk1 = _chunk(tmp_path / "mic1.wav", "mic", "mic-1")
        mic_chunk1.file_path = str(tmp_path / "mic1.wav")
        (tmp_path / "mic1.wav").write_bytes(b"")
        r1 = transcriber.transcribe_chunk(mic_chunk1, stream=(1, "mic"))
        assert r1["text"] == "need more time"

        # A different stream's very first chunk: text that WOULD be stripped
        # to "please" if state were shared with the mic stream above, since
        # "need more time" is what the mic stream's _last_text now holds.
        (tmp_path / "sys1.wav").write_bytes(b"")
        system_chunk1 = _chunk(tmp_path / "sys1.wav", "system", "sys-1")
        r2 = transcriber.transcribe_chunk(system_chunk1, stream=(1, "system"))
        assert r2["text"] == "more time please", (
            "a new stream's first chunk must not be deduplicated against "
            "another stream's previous text"
        )

    def test_dedup_still_applies_within_the_same_stream(self, tmp_path):
        engine = _FakeEngine(["need more time", "more time please"])
        transcriber = LiveTranscriber(engine=engine)

        (tmp_path / "a.wav").write_bytes(b"")
        (tmp_path / "b.wav").write_bytes(b"")
        c1 = _chunk(tmp_path / "a.wav", "mic", "a")
        c2 = _chunk(tmp_path / "b.wav", "mic", "b")

        r1 = transcriber.transcribe_chunk(c1, stream=(1, "mic"))
        r2 = transcriber.transcribe_chunk(c2, stream=(1, "mic"))

        assert r1["text"] == "need more time"
        assert r2["text"] == "please"  # the "more time" overlap was stripped

    def test_stream_defaults_to_chunk_source_when_not_given(self, tmp_path):
        """Callers that don't pass `stream` (e.g. TranscriptionProcessor's
        own call sites, if any) still get per-source isolation via the
        chunk's own `source` field."""
        engine = _FakeEngine(["need more time", "more time please"])
        transcriber = LiveTranscriber(engine=engine)

        (tmp_path / "a.wav").write_bytes(b"")
        (tmp_path / "b.wav").write_bytes(b"")
        mic = _chunk(tmp_path / "a.wav", "mic", "a")
        system = _chunk(tmp_path / "b.wav", "system", "b")

        transcriber.transcribe_chunk(mic)  # no `stream` kwarg
        r2 = transcriber.transcribe_chunk(system)

        assert r2["text"] == "more time please"  # untouched: different source

    def test_forget_session_drops_only_that_sessions_streams(self):
        transcriber = LiveTranscriber(engine=_FakeEngine([]))
        transcriber._context[(1, "mic")] = ["a"]
        transcriber._context[(1, "system")] = ["b"]
        transcriber._context[(2, "mic")] = ["c"]
        transcriber._last_text[(1, "mic")] = "a"
        transcriber._last_text[(2, "mic")] = "c"

        transcriber.forget_session(1)

        assert (1, "mic") not in transcriber._context
        assert (1, "system") not in transcriber._context
        assert (2, "mic") in transcriber._context
        assert (1, "mic") not in transcriber._last_text
        assert (2, "mic") in transcriber._last_text

    def test_reset_one_stream_leaves_others_untouched(self):
        transcriber = LiveTranscriber(engine=_FakeEngine([]))
        transcriber._context[(1, "mic")] = ["a"]
        transcriber._context[(1, "system")] = ["b"]
        transcriber._last_text[(1, "mic")] = "a"
        transcriber._last_text[(1, "system")] = "b"

        transcriber.reset((1, "mic"))

        assert (1, "mic") not in transcriber._context
        assert (1, "mic") not in transcriber._last_text
        assert (1, "system") in transcriber._context
        assert (1, "system") in transcriber._last_text

    def test_reset_with_no_stream_clears_everything(self):
        transcriber = LiveTranscriber(engine=_FakeEngine([]))
        transcriber._context[(1, "mic")] = ["a"]
        transcriber._last_text[(1, "mic")] = "a"

        transcriber.reset()

        assert transcriber._context == {}
        assert transcriber._last_text == {}
