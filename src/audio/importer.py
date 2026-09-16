"""Turn an uploaded recording into chunks the batch transcriber can read (BU104).

Accepts WAV, iPhone Voice Memos recordings (.m4a - AAC, or Apple Lossless
when "Lossless" is on), WhatsApp voice notes (.opus) and the other common
formats FFmpeg decodes. Audio is decoded with PyAV, downmixed to 16 kHz mono
and written as timestamped WAV chunks in the live recorder's
``YYYYMMDD_HHMMSS_YYYYMMDD_HHMMSS`` layout, so ``TranscriptionProcessor``
picks them up unchanged.
"""
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np
import soundfile as sf

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16000  # Parakeet's native rate

# iPhone Voice Memos saves .m4a; WhatsApp voice notes are .opus (Ogg Opus,
# forwarded/exported copies sometimes carry .ogg/.oga instead); the rest come
# free with FFmpeg.
SUPPORTED_EXTENSIONS = ('.m4a', '.wav', '.mp3', '.aac', '.caf', '.flac', '.ogg', '.opus', '.oga')

# Chunks run ~CHUNK_SECONDS, each cut at the quietest moment of its last
# CUT_WINDOW_SECONDS so a word is never split between two chunks. Unlike live
# chunks they don't overlap, so the processor skips its overlap dedup for
# files carrying IMPORTED_CHUNK_SUFFIX.
CHUNK_SECONDS = 30
CUT_WINDOW_SECONDS = 5
IMPORTED_CHUNK_SUFFIX = '_import'


def decode_audio_file(path) -> Tuple[np.ndarray, Optional[datetime]]:
    """Decode the first audio track of ``path`` to 16 kHz mono int16 samples.

    Returns the samples and the recording time embedded in the file's
    metadata (as local time), or None when the file doesn't carry one.
    """
    try:
        import av
    except ImportError as exc:
        raise RuntimeError('Uploading audio needs PyAV. Install it with: pip install av') from exc

    name = Path(path).name
    try:
        with av.open(str(path)) as container:
            if not container.streams.audio:
                raise ValueError(f'{name} has no audio track')
            resampler = av.AudioResampler(format='s16', layout='mono', rate=SAMPLE_RATE)
            parts = []
            for frame in container.decode(container.streams.audio[0]):
                parts.extend(out.to_ndarray().reshape(-1) for out in resampler.resample(frame))
            parts.extend(out.to_ndarray().reshape(-1) for out in resampler.resample(None))
            recorded_at = _parse_creation_time(container.metadata.get('creation_time'))
    except av.FFmpegError as exc:
        raise ValueError(f"{name} isn't an audio file Chronicle can read ({exc.strerror})") from exc

    samples = np.concatenate(parts) if parts else np.zeros(0, dtype=np.int16)
    return samples, recorded_at


def _parse_creation_time(value) -> Optional[datetime]:
    """``creation_time`` metadata (UTC ISO-8601, as iPhone recordings carry
    it) as naive local time; None if missing or implausible."""
    if not value:
        return None
    try:
        stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return None
    if stamp.tzinfo is not None:
        stamp = stamp.astimezone().replace(tzinfo=None)
    # Files written without a clock carry the 1904 / 1970 epoch.
    if stamp.year < 2000 or stamp > datetime.now():
        return None
    return stamp


def split_at_pauses(samples: np.ndarray) -> List[Tuple[int, int]]:
    """``(start, end)`` sample ranges of roughly CHUNK_SECONDS each, cut at
    the quietest 50 ms of each chunk's final CUT_WINDOW_SECONDS."""
    frame = SAMPLE_RATE // 20
    total = len(samples)
    bounds, start = [], 0
    # Stop once the rest fits in one chunk, so the last one is never a sliver.
    while total - start > (CHUNK_SECONDS + CUT_WINDOW_SECONDS) * SAMPLE_RATE:
        lo = start + (CHUNK_SECONDS - CUT_WINDOW_SECONDS) * SAMPLE_RATE
        window = samples[lo:lo + CUT_WINDOW_SECONDS * SAMPLE_RATE].astype(np.float32)
        energy = np.square(window.reshape(-1, frame)).mean(axis=1)
        cut = lo + int(np.argmin(energy)) * frame + frame // 2
        bounds.append((start, cut))
        start = cut
    if total > start:
        bounds.append((start, total))
    return bounds


def write_chunks(samples: np.ndarray, audio_dir: Path, start_time: datetime) -> int:
    """Write ``samples`` as timestamped WAV chunks into ``audio_dir``.

    Returns:
        Number of chunks written
    """
    audio_dir.mkdir(parents=True, exist_ok=True)
    bounds = split_at_pauses(samples)
    for begin, end in bounds:
        chunk_start = start_time + timedelta(seconds=begin / SAMPLE_RATE)
        chunk_end = start_time + timedelta(seconds=end / SAMPLE_RATE)
        name = f'{chunk_start:%Y%m%d_%H%M%S}_{chunk_end:%Y%m%d_%H%M%S}{IMPORTED_CHUNK_SUFFIX}.wav'
        sf.write(str(audio_dir / name), samples[begin:end], SAMPLE_RATE, subtype='PCM_16')
    logger.info(f'Wrote {len(bounds)} imported chunks to {audio_dir}')
    return len(bounds)
