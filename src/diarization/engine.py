"""Who spoke when in a session's stored audio (BU163).

Per source (``audio/mic``, ``audio/system``) the chunk WAVs are concatenated
in time order, in windows of about :data:`WINDOW_SECONDS`, with an
:class:`OffsetMap` from concatenated time back to (chunk file, absolute time).

sherpa-onnx (pyannote segmentation-3.0) only supplies the speech segments:
its own clustering over-splits (19 speakers with WeSpeaker, 140 with TitaNet
on a 2-speaker session, test of 2026-10-11). Each segment of
:data:`MIN_EMBED_SECONDS` or more is embedded again with TitaNet (its first
:data:`EMBED_SECONDS`), the embeddings are clustered with average linkage at
cosine distance :data:`CLUSTER_DISTANCE`, clusters under
:data:`MIN_SPEAKER_SHARE` of the source's talk time are folded into the
nearest kept speaker, shorter segments take a neighbour's speaker, and
same-speaker turns closer than :data:`MERGE_GAP_SECONDS` are merged.

Speakers are numbered 1, 2, ... across the session in order of first
appearance. Mic and system speakers are never matched with each other, so a
person heard on both sources gets two numbers.
"""
from __future__ import annotations

import bisect
import logging
import threading
from dataclasses import dataclass
from datetime import datetime
from fractions import Fraction
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

from .. import model_manager

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16000
NUM_THREADS = 4  # leaves cores for live transcription

CLUSTER_DISTANCE = 0.6
MIN_SPEAKER_SHARE = 0.05
MIN_EMBED_SECONDS = 1.0
EMBED_SECONDS = 10.0
MERGE_GAP_SECONDS = 1.0
WINDOW_SECONDS = 300.0  # audio held in memory at once, per source; also the cancel granularity

# sherpa's clustering only decides where its segments split; any value that
# over-splits is fine, since the segments are re-clustered here.
SHERPA_THRESHOLD = 0.6
SEGMENTATION_FILE = "model.onnx"
EMBEDDING_FILE = "nemo_en_titanet_small.onnx"

# (transcripts.source, folder under audio/)
SOURCES = (("microphone", "mic"), ("system", "system"))

ProgressCallback = Callable[[str, int, int], None]  # (stage, done, total)


class DiarizationError(Exception):
    """Diarization could not run; the message is meant for the user."""


class DiarizationCancelled(DiarizationError):
    """The cancel event was set."""


@dataclass(frozen=True)
class ChunkSpan:
    audio_file: str  # as in transcripts.audio_file, e.g. "mic/<start>_<end>.wav"
    start: float  # seconds into that chunk file
    end: float


@dataclass(frozen=True)
class Turn:
    source: str  # "microphone" | "system"
    speaker: int  # 1, 2, ... across the session
    start_abs: float  # epoch seconds
    end_abs: float
    spans: Tuple[ChunkSpan, ...]


# -- concatenated time <-> chunk files -------------------------------------------

@dataclass(frozen=True)
class _Chunk:
    path: Path
    key: str
    start_abs: float
    offset: float  # start in the concatenated audio, seconds
    duration: float


class OffsetMap:
    """Concatenated time of one source -> (chunk file, time in it, absolute time)."""

    def __init__(self, chunks: Sequence[_Chunk]):
        self.chunks = list(chunks)
        self._offsets = [c.offset for c in self.chunks]

    @property
    def duration(self) -> float:
        last = self.chunks[-1] if self.chunks else None
        return last.offset + last.duration if last else 0.0

    def _index(self, t: float, end: bool = False) -> int:
        # An end time on a boundary belongs to the chunk it ends.
        find = bisect.bisect_left if end else bisect.bisect_right
        return min(max(find(self._offsets, t) - 1, 0), len(self.chunks) - 1)

    def locate(self, t: float, end: bool = False) -> Tuple[str, float, float]:
        chunk = self.chunks[self._index(t, end)]
        inside = min(max(t - chunk.offset, 0.0), chunk.duration)
        return chunk.key, inside, chunk.start_abs + inside

    def spans(self, start: float, end: float) -> Tuple[ChunkSpan, ...]:
        first, last = self._index(start), self._index(end, end=True)
        result = []
        for chunk in self.chunks[first:last + 1]:
            lo = max(start, chunk.offset) - chunk.offset
            hi = min(end, chunk.offset + chunk.duration) - chunk.offset
            if hi > lo:
                result.append(ChunkSpan(chunk.key, round(lo, 3), round(hi, 3)))
        return tuple(result)


def _chunk_start(path: Path) -> datetime:
    """Start time from a ``<YYYYmmdd_HHMMSS>_...`` chunk name (as the transcriber reads it)."""
    try:
        return datetime.strptime(path.stem[:15], "%Y%m%d_%H%M%S")
    except ValueError:
        return datetime.fromtimestamp(path.stat().st_mtime)


def build_offset_map(folder: Path) -> OffsetMap:
    import soundfile as sf

    files = sorted(folder.glob("*.wav"), key=lambda p: (_chunk_start(p), p.name))
    chunks, offset = [], 0.0
    for path in files:
        try:
            duration = sf.info(str(path)).duration
        except RuntimeError as exc:
            logger.warning("Skipping unreadable chunk %s: %s", path, exc)
            continue
        if duration <= 0:
            continue
        chunks.append(_Chunk(path, f"{folder.name}/{path.name}",
                             _chunk_start(path).timestamp(), offset, duration))
        offset += duration
    return OffsetMap(chunks)


def _read_chunk(chunk: _Chunk) -> np.ndarray:
    """The chunk as 16 kHz mono float32, exactly ``duration`` long."""
    import soundfile as sf

    data, rate = sf.read(str(chunk.path), dtype="float32", always_2d=True)
    samples = data.mean(axis=1)
    if rate != SAMPLE_RATE:
        samples = _resample(samples, rate)
    n = int(round(chunk.duration * SAMPLE_RATE))
    if len(samples) < n:
        samples = np.pad(samples, (0, n - len(samples)))
    return samples[:n]


def _resample(samples: np.ndarray, rate: int) -> np.ndarray:
    import av

    resampler = av.AudioResampler(format="flt", layout="mono", rate=SAMPLE_RATE)
    frame = av.AudioFrame.from_ndarray(
        np.ascontiguousarray(samples, dtype=np.float32).reshape(1, -1), format="flt", layout="mono")
    frame.sample_rate = rate
    frame.pts = 0
    frame.time_base = Fraction(1, rate)
    out = list(resampler.resample(frame)) + list(resampler.resample(None))
    return np.concatenate([f.to_ndarray().reshape(-1) for f in out]) if out else np.zeros(0, np.float32)


# -- clustering ------------------------------------------------------------------------

def cluster(embeddings: np.ndarray, threshold: float = CLUSTER_DISTANCE) -> np.ndarray:
    """Average-linkage clustering on cosine distance, cut at ``threshold``.

    Same partition as scipy's ``fcluster(linkage(x, "average", "cosine"),
    threshold, "distance")``, built with the nearest-neighbour chain (O(n^2))
    so scipy is not needed. Returns a label per row, 0-based.
    """
    n = len(embeddings)
    if n == 0:
        return np.zeros(0, dtype=int)
    x = np.asarray(embeddings, dtype=np.float64)
    x = x / np.linalg.norm(x, axis=1, keepdims=True)
    dist = 1.0 - x @ x.T
    np.fill_diagonal(dist, np.inf)
    size = np.ones(n)
    active = np.ones(n, dtype=bool)
    root = np.arange(n)

    def find(i: int) -> int:
        while root[i] != i:
            root[i] = root[root[i]]
            i = root[i]
        return i

    chain: List[int] = []
    for _ in range(n - 1):
        while True:
            if not chain:
                chain.append(int(np.flatnonzero(active)[0]))
            a = chain[-1]
            b = int(np.argmin(dist[a]))
            if len(chain) > 1 and dist[a, chain[-2]] <= dist[a, b]:
                b = chain[-2]
            if len(chain) > 1 and b == chain[-2]:
                break
            chain.append(b)
        chain.pop()
        chain.pop()
        # Average linkage is monotone: once a merge is above the threshold,
        # every later merge of that cluster is too.
        if dist[a, b] <= threshold:
            root[find(b)] = find(a)
        merged = (size[a] * dist[a] + size[b] * dist[b]) / (size[a] + size[b])
        dist[a, :] = merged
        dist[:, a] = merged
        dist[a, a] = np.inf
        dist[b, :] = np.inf
        dist[:, b] = np.inf
        size[a] += size[b]
        active[b] = False

    _, labels = np.unique([find(i) for i in range(n)], return_inverse=True)
    return labels


def assign_speakers(segments: Sequence[Tuple[float, float]],
                    embeddings: Sequence[Optional[np.ndarray]]) -> List[int]:
    """A speaker label per segment (sorted by start); ``None`` = too short to embed."""
    embedded = [i for i, e in enumerate(embeddings) if e is not None]
    if not embedded:
        return [0] * len(segments)
    vectors = np.stack([embeddings[i] for i in embedded]).astype(np.float64)
    vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
    raw = cluster(vectors)
    talk = np.array([segments[i][1] - segments[i][0] for i in embedded])
    share = {k: talk[raw == k].sum() / talk.sum() for k in set(raw.tolist())}
    kept = [k for k in sorted(share) if share[k] >= MIN_SPEAKER_SHARE] or [max(share, key=share.get)]

    centroids = np.stack([vectors[raw == k].mean(axis=0) for k in kept])
    centroids /= np.linalg.norm(centroids, axis=1, keepdims=True)
    labels: List[Optional[int]] = [None] * len(segments)
    for j, i in enumerate(embedded):
        k = raw[j]
        labels[i] = kept.index(k) if k in kept else int(np.argmax(centroids @ vectors[j]))

    # Short segments take the speaker of the closer embedded neighbour.
    for i, label in enumerate(labels):
        if label is not None:
            continue
        before = next((j for j in range(i - 1, -1, -1) if embeddings[j] is not None), None)
        after = next((j for j in range(i + 1, len(segments)) if embeddings[j] is not None), None)
        gap_before = segments[i][0] - segments[before][1] if before is not None else np.inf
        gap_after = segments[after][0] - segments[i][1] if after is not None else np.inf
        labels[i] = labels[before] if gap_before <= gap_after else labels[after]
    return labels


def merge_turns(items: Sequence[Tuple[float, float, int]],
                gap: float = MERGE_GAP_SECONDS) -> List[Tuple[float, float, int]]:
    """Join consecutive (start, end, speaker) items of one speaker less than ``gap`` apart."""
    merged: List[Tuple[float, float, int]] = []
    for start, end, speaker in sorted(items):
        if merged and merged[-1][2] == speaker and start - merged[-1][1] < gap:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end), speaker)
        else:
            merged.append((start, end, speaker))
    return merged


# -- running it ------------------------------------------------------------------------

def _load_models():
    """sherpa's diarizer (for segments) and a TitaNet extractor.

    Raises:
        model_manager.ModelNotInstalledError: the diarization models are missing.
    """
    import sherpa_onnx as so

    segmentation = model_manager.local_path(model_manager.SEGMENTATION, SEGMENTATION_FILE)
    embedding = model_manager.local_path(model_manager.SPEAKER_EMBEDDING, EMBEDDING_FILE)
    embedding_config = so.SpeakerEmbeddingExtractorConfig(model=str(embedding), num_threads=NUM_THREADS)
    config = so.OfflineSpeakerDiarizationConfig(
        segmentation=so.OfflineSpeakerSegmentationModelConfig(
            pyannote=so.OfflineSpeakerSegmentationPyannoteModelConfig(model=str(segmentation)),
            num_threads=NUM_THREADS,
        ),
        embedding=embedding_config,
        clustering=so.FastClusteringConfig(num_clusters=-1, threshold=SHERPA_THRESHOLD),
        min_duration_on=0.3,
        min_duration_off=0.5,
    )
    if not config.validate():
        raise DiarizationError("The diarization models could not be loaded.")
    return so.OfflineSpeakerDiarization(config), so.SpeakerEmbeddingExtractor(embedding_config)


def _embed(extractor, samples: np.ndarray) -> Optional[np.ndarray]:
    stream = extractor.create_stream()
    stream.accept_waveform(SAMPLE_RATE, samples)
    stream.input_finished()
    vector = np.asarray(extractor.compute(stream), dtype=np.float32)
    norm = float(np.linalg.norm(vector))
    return vector / norm if norm > 0 else None


def _windows(offsets: OffsetMap) -> List[List[_Chunk]]:
    windows: List[List[_Chunk]] = [[]]
    for chunk in offsets.chunks:
        if windows[-1] and windows[-1][-1].offset + windows[-1][-1].duration - windows[-1][0].offset >= WINDOW_SECONDS:
            windows.append([])
        windows[-1].append(chunk)
    return [w for w in windows if w]


def diarize_session(session_path, progress: Optional[ProgressCallback] = None,
                    cancel_event: Optional[threading.Event] = None) -> List[Turn]:
    """Speaker turns for every source with audio in ``session_path``, sorted by start.

    ``progress(stage, done, total)``: stage is "segmenting" or "embedding"
    (``done`` / ``total`` in seconds of audio, never going backwards) and then
    "clustering" (0 / 1, 1 / 1).

    Raises:
        DiarizationCancelled: ``cancel_event`` was set.
        model_manager.ModelNotInstalledError: the diarization models are missing.
    """
    audio = Path(session_path) / "audio"
    maps = {source: build_offset_map(audio / folder) for source, folder in SOURCES}
    maps = {source: m for source, m in maps.items() if m.chunks}
    total = sum(m.duration for m in maps.values())
    report = progress or (lambda *args: None)

    def check_cancel() -> None:
        if cancel_event is not None and cancel_event.is_set():
            raise DiarizationCancelled("Diarization cancelled.")

    if not maps:
        return []
    diarizer, extractor = _load_models()

    done = 0.0
    local_turns: Dict[str, List[Tuple[float, float, int]]] = {}
    for source, offsets in maps.items():
        segments: List[Tuple[float, float]] = []
        embeddings: List[Optional[np.ndarray]] = []
        for window in _windows(offsets):
            base = window[0].offset
            length = window[-1].offset + window[-1].duration - base
            parts = []
            for chunk in window:
                check_cancel()
                parts.append(_read_chunk(chunk))
            samples = np.concatenate(parts)

            # No progress callback: sherpa calls it from native code without
            # a safe hand-over to Python, which crashed the app (access
            # violation) as soon as the UI thread was running Python too.
            # Progress and cancel happen between windows instead.
            check_cancel()
            report("segmenting", int(done), int(total))
            found = diarizer.process(samples).sort_by_start_time()
            check_cancel()
            found = [(s.start, s.end) for s in found if s.end > s.start]
            for n, (start, end) in enumerate(found):
                check_cancel()
                vector = None
                if end - start >= MIN_EMBED_SECONDS:
                    lo = int(start * SAMPLE_RATE)
                    hi = int(min(end, start + EMBED_SECONDS) * SAMPLE_RATE)
                    vector = _embed(extractor, samples[lo:hi])
                segments.append((base + start, base + end))
                embeddings.append(vector)
                report("embedding", int(done + length / 2 * (1 + (n + 1) / len(found))), int(total))
            done += length
            report("embedding", int(done), int(total))
        labels = assign_speakers(segments, embeddings)
        local_turns[source] = merge_turns([(s, e, k) for (s, e), k in zip(segments, labels)])

    report("clustering", 0, 1)
    turns = _number_speakers(local_turns, maps)
    report("clustering", 1, 1)
    return turns


def _number_speakers(local_turns: Dict[str, List[Tuple[float, float, int]]],
                     maps: Dict[str, OffsetMap]) -> List[Turn]:
    """Absolute-time turns, speakers numbered across the session by first appearance."""
    placed = []
    for source, items in local_turns.items():
        offsets = maps[source]
        for start, end, label in items:
            start_abs = offsets.locate(start)[2]
            end_abs = max(offsets.locate(end, end=True)[2], start_abs)
            placed.append((start_abs, end_abs, source, label, offsets.spans(start, end)))
    placed.sort(key=lambda t: (t[0], t[1]))
    numbers: Dict[Tuple[str, int], int] = {}
    turns = []
    for start_abs, end_abs, source, label, spans in placed:
        speaker = numbers.setdefault((source, label), len(numbers) + 1)
        turns.append(Turn(source, speaker, start_abs, end_abs, spans))
    return turns
