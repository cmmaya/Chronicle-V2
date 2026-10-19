"""BU163 - diarization engine: offset map, clustering, turns, cancel."""
import tempfile
import threading
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import numpy as np
import soundfile as sf

from src.diarization import engine
from src.diarization.engine import DiarizationCancelled

SR = engine.SAMPLE_RATE


def _write_chunk(folder: Path, start: datetime, seconds: float, value: float = 0.1, rate: int = SR) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    end = datetime.fromtimestamp(start.timestamp() + seconds)
    path = folder / f"{start:%Y%m%d_%H%M%S}_{end:%Y%m%d_%H%M%S}.wav"
    sf.write(str(path), np.full(int(seconds * rate), value, dtype=np.float32), rate, subtype="PCM_16")
    return path


class _Tmp(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.session = Path(self._tmp.name) / "session_001"

    def tearDown(self):
        self._tmp.cleanup()


class OffsetMapTests(_Tmp):
    def test_round_trip(self):
        mic = self.session / "audio" / "mic"
        # Written out of order, with a recording gap between them.
        b = _write_chunk(mic, datetime(2026, 10, 1, 10, 0, 30), 3.0)
        a = _write_chunk(mic, datetime(2026, 10, 1, 10, 0, 0), 5.0)
        offsets = engine.build_offset_map(mic)
        t0 = datetime(2026, 10, 1, 10, 0, 0).timestamp()
        self.assertEqual([c.key for c in offsets.chunks], [f"mic/{a.name}", f"mic/{b.name}"])
        self.assertAlmostEqual(offsets.duration, 8.0)

        self.assertEqual(offsets.locate(2.0), (f"mic/{a.name}", 2.0, t0 + 2.0))
        key, inside, absolute = offsets.locate(6.5)
        self.assertEqual((key, inside), (f"mic/{b.name}", 1.5))
        self.assertAlmostEqual(absolute, t0 + 31.5)
        # A boundary starts the next chunk, but ends the previous one.
        self.assertEqual(offsets.locate(5.0)[0], f"mic/{b.name}")
        self.assertEqual(offsets.locate(5.0, end=True)[:2], (f"mic/{a.name}", 5.0))

        self.assertEqual(offsets.spans(4.0, 6.0), (
            engine.ChunkSpan(f"mic/{a.name}", 4.0, 5.0),
            engine.ChunkSpan(f"mic/{b.name}", 0.0, 1.0),
        ))
        self.assertEqual(offsets.spans(1.0, 5.0), (engine.ChunkSpan(f"mic/{a.name}", 1.0, 5.0),))

    def test_other_rates_become_16k(self):
        mic = self.session / "audio" / "mic"
        _write_chunk(mic, datetime(2026, 10, 1, 10, 0, 0), 2.0, rate=44100)
        [chunk] = engine.build_offset_map(mic).chunks
        self.assertEqual(len(engine._read_chunk(chunk)), 2 * SR)


def _group(center, n, rng, spread=0.02):
    return [center + rng.normal(scale=spread, size=len(center)) for _ in range(n)]


class ClusteringTests(unittest.TestCase):
    def setUp(self):
        self.rng = np.random.default_rng(1)
        self.a, self.b, self.c = np.eye(8)[0], np.eye(8)[1], np.eye(8)[2]

    def test_two_tight_groups_are_two_speakers(self):
        vectors = _group(self.a, 10, self.rng) + _group(self.b, 10, self.rng)
        segments = [(i * 3.0, i * 3.0 + 2.0) for i in range(20)]
        labels = engine.assign_speakers(segments, vectors)
        self.assertEqual(len(set(labels)), 2)
        self.assertEqual(len(set(labels[:10])), 1)
        self.assertEqual(len(set(labels[10:])), 1)

    def test_small_group_is_absorbed(self):
        # Its own cluster (cosine distance 0.65 from a, 1.0 from b), but only
        # 2 s of 82 s of talk: under 5%, so it joins a.
        near_a = self.a * 0.35 + self.c * 0.94
        vectors = _group(self.a, 20, self.rng) + _group(self.b, 20, self.rng) + [near_a]
        segments = [(i * 3.0, i * 3.0 + 2.0) for i in range(41)]
        self.assertEqual(len(set(engine.cluster(np.array(vectors)).tolist())), 3)
        labels = engine.assign_speakers(segments, vectors)
        self.assertEqual(len(set(labels)), 2)
        self.assertEqual(labels[40], labels[0])

    def test_short_segment_takes_closer_neighbour(self):
        vectors = [self.a, None, self.b, None]
        segments = [(0.0, 2.0), (2.2, 2.6), (5.0, 7.0), (9.5, 9.9)]
        with mock.patch.object(engine, "MIN_SPEAKER_SHARE", 0.0):
            labels = engine.assign_speakers(segments, vectors)
        self.assertEqual(labels[1], labels[0])
        self.assertEqual(labels[3], labels[2])
        self.assertNotEqual(labels[0], labels[2])

    def test_adjacent_same_speaker_turns_merge(self):
        turns = engine.merge_turns([(0.0, 2.0, 0), (2.5, 4.0, 0), (6.0, 7.0, 0), (7.2, 8.0, 1), (8.1, 9.0, 1)])
        self.assertEqual(turns, [(0.0, 4.0, 0), (6.0, 7.0, 0), (7.2, 9.0, 1)])


class _FakeDiarizer:
    """One segment per run of equal sample values (each chunk is a constant).

    Takes no ``callback``: sherpa's progress callback crashed the app (it is
    called from native code), so the engine must never pass one.
    """

    def process(self, samples):
        edges = [0] + [i for i in range(1, len(samples)) if abs(samples[i] - samples[i - 1]) > 1e-3] + [len(samples)]
        segs = [SimpleNamespace(start=lo / SR, end=hi / SR) for lo, hi in zip(edges, edges[1:])]
        return SimpleNamespace(sort_by_start_time=lambda: segs)


class _FakeExtractor:
    """Embeds by sign: positive audio is one voice, negative another."""

    def create_stream(self):
        return SimpleNamespace(accept_waveform=lambda sr, x: setattr(self, "_x", x), input_finished=lambda: None)

    def compute(self, stream):
        return [1.0, 0.0] if float(np.mean(self._x)) > 0 else [0.0, 1.0]


class DiarizeSessionTests(_Tmp):
    def setUp(self):
        super().setUp()
        self.t0 = datetime(2026, 10, 1, 9, 0, 0)
        mic = self.session / "audio" / "mic"
        system = self.session / "audio" / "system"
        _write_chunk(mic, self.t0, 4.0, 0.3)
        _write_chunk(mic, datetime(2026, 10, 1, 9, 0, 4), 4.0, 0.3)
        _write_chunk(mic, datetime(2026, 10, 1, 9, 0, 8), 4.0, -0.3)
        _write_chunk(system, datetime(2026, 10, 1, 9, 0, 2), 6.0, 0.2)
        patcher = mock.patch.object(engine, "_load_models", return_value=(_FakeDiarizer(), _FakeExtractor()))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_turns_and_numbering(self):
        events = []
        turns = engine.diarize_session(self.session, lambda *e: events.append(e))
        t0 = self.t0.timestamp()
        self.assertEqual([(t.source, t.speaker) for t in turns],
                         [("microphone", 1), ("system", 2), ("microphone", 3)])
        first = turns[0]
        self.assertAlmostEqual(first.start_abs, t0)
        self.assertAlmostEqual(first.end_abs, t0 + 8.0)  # two chunks, one merged turn
        self.assertEqual(len(first.spans), 2)
        self.assertAlmostEqual(turns[1].start_abs, t0 + 2.0)
        self.assertEqual(turns[2].spans[0].start, 0.0)
        self.assertEqual(events[-1], ("clustering", 1, 1))
        seconds = [e[1] for e in events if e[0] != "clustering"]
        self.assertEqual(seconds, sorted(seconds))
        self.assertEqual(seconds[-1], 18)

    def test_cancel_raises(self):
        cancel = threading.Event()

        def progress(stage, done, total):
            cancel.set()

        with self.assertRaises(DiarizationCancelled):
            engine.diarize_session(self.session, progress, cancel)

    def test_cancel_before_start(self):
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(DiarizationCancelled):
            engine.diarize_session(self.session, None, cancel)

    def test_no_audio(self):
        self.assertEqual(engine.diarize_session(Path(self._tmp.name) / "empty"), [])


if __name__ == "__main__":
    unittest.main()
