"""Audio capture module for chunked recording.

This module provides chunked audio recording functionality, capturing
audio in discrete time-based chunks with metadata.
"""

from src.audio_capture.core import (
    ChunkedAudioRecorder,
    DualSourceChunkedRecorder,
    create_chunked_recorder,
    create_dual_source_recorder,
)
from src.audio_capture.chunk import (
    AudioChunk,
    generate_chunk_id,
    generate_filename,
)
from src.audio_capture.system_recorder import SystemAudioRecorder

__all__ = [
    'ChunkedAudioRecorder',
    'DualSourceChunkedRecorder',
    'create_chunked_recorder',
    'create_dual_source_recorder',
    'AudioChunk',
    'generate_chunk_id',
    'generate_filename',
    'SystemAudioRecorder',
]