"""Live transcription functionality for real-time audio chunk processing.

This module provides functions for transcribing audio chunks in real-time
during a recording session.
"""

import difflib
import logging
from pathlib import Path
from typing import Optional, Dict, Any, Hashable, List

from ..audio_capture.chunk import AudioChunk
from .parakeet import ParakeetEngine, ModelLoadError, TranscriptionError, get_shared_engine

logger = logging.getLogger(__name__)


class LiveTranscriber:
    """Handles real-time transcription of audio chunks.

    This class wraps the ParakeetEngine for live transcription use cases,
    maintaining state for context and deduplication. That state is kept per
    *stream* - one per (session, source) - because overlap only exists
    between consecutive chunks of the same recorder: comparing a mic chunk
    with the last system chunk (or with the previous session) never finds the
    real overlap and can strip words that merely look alike.
    """

    def __init__(self, model_path: Optional[str] = None, engine: Optional[ParakeetEngine] = None):
        """Initialize the live transcriber.

        Args:
            model_path: Optional Parakeet model; loads a dedicated engine.
            engine: Engine to use. Defaults to the app-wide shared engine.
        """
        if engine is not None:
            self.engine = engine
        elif model_path:
            self.engine = ParakeetEngine(model_path=model_path)
        else:
            self.engine = get_shared_engine()

        # Context / previous text for improved transcription, per stream
        self._context: Dict[Hashable, List[str]] = {}
        self._last_text: Dict[Hashable, str] = {}

    def load_model(self) -> None:
        """Load the transcription model.

        Raises:
            ModelLoadError: If model cannot be loaded
        """
        if self.engine.is_loaded():
            return
        self.engine.load()
        logger.info("LiveTranscriber model loaded")

    def transcribe_chunk(self, chunk: AudioChunk, stream: Hashable = None) -> Optional[Dict[str, Any]]:
        """Transcribe a single audio chunk.

        Args:
            chunk: AudioChunk object containing audio file path and metadata
            stream: Key of the recording stream the chunk belongs to (e.g.
                ``(session_id, source)``); defaults to the chunk's source

        Returns:
            Dict with transcription result, or None if transcription fails
        """
        if stream is None:
            stream = chunk.source
        audio_path = chunk.file_path

        if not audio_path or not Path(audio_path).exists():
            logger.warning(f"Audio file not found: {audio_path}")
            return None

        try:
            context = self._context.setdefault(stream, [])
            # Build initial_prompt from context for improved accuracy
            initial_prompt = ' '.join(context) if context else None

            # Transcribe the audio chunk
            text = self.engine.transcribe(audio_path, initial_prompt=initial_prompt)

            if not text:
                logger.debug(f"No text transcribed from chunk: {chunk.chunk_id}")
                return None

            # Apply deduplication against the previous chunk of this stream
            previous = self._last_text.get(stream)
            if previous:
                text = self._deduplicate(text, previous)
                if not text:
                    return None

            # Update context
            context.append(text)
            del context[:-5]

            self._last_text[stream] = text

            result = {
                'text': text,
                'source': chunk.source,
                'chunk_id': chunk.chunk_id,
                'timestamp_start': chunk.timestamp_start,
                'timestamp_end': chunk.timestamp_end,
                'file_path': audio_path,
                'context': initial_prompt
            }
            
            logger.info(f"Live transcription result for {chunk.chunk_id}: {text[:50]}...")
            return result
            
        except ModelLoadError as e:
            logger.error(f"Failed to load model for live transcription: {e}")
            return None
        except TranscriptionError as e:
            logger.error(f"Live transcription failed for {chunk.chunk_id}: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error in live transcription: {e}")
            return None
    
    # Similarity bar for treating a phrase at the end of the previous chunk
    # and a phrase at the start of the new one as "the same overlapping
    # audio", for phrases long enough that a high character-similarity ratio
    # can't be a coincidence (more characters already have to agree).
    _DEDUP_FUZZY_THRESHOLDS = {8: 0.75, 6: 0.80, 4: 0.85}
    # Short phrases (2-3 words) only dedup on an exact match: fuzzy-matching
    # them risked false positives - "we can do" / "we can also" matched at
    # ratio 0.80 in testing, which would have silently deleted real,
    # non-duplicate text just because both started with "we can".
    _DEDUP_EXACT_ONLY_LENGTHS = (3, 2)

    def _deduplicate(self, new_text: str, previous_text: Optional[str]) -> str:
        """Remove text at the start of ``new_text`` that duplicates the tail
        of ``previous_text`` - the previous chunk of the same stream.

        The chunk-to-chunk audio overlap means the same few seconds of audio
        get transcribed twice; this strips the second copy. For phrases of 4+
        words, matching is done by character-similarity ratio rather than
        requiring exact equality, because the model doesn't reliably
        transcribe the same overlapping audio identically both times - a
        boundary word coming out slightly different each time (e.g. "its" vs
        "it's", or a garbled attempt on one side and the correct word on the
        other) previously slipped past the exact match and showed up as a
        leftover invented/duplicate word. Shorter phrases (2-3 words) still
        require an exact match, since fuzzy-matching them risks deleting real
        text that just happens to start the same way.

        Args:
            new_text: Newly transcribed text
            previous_text: The stream's previous (already deduplicated) text

        Returns:
            Deduplicated text
        """
        if not previous_text or not new_text:
            return new_text

        prev_words_lower = previous_text.lower().split()
        new_words = new_text.split()  # original casing, used for the result
        new_words_lower = new_text.lower().split()

        if len(prev_words_lower) < 2 or len(new_words_lower) < 2:
            return new_text

        def _strip(num_words: int, ratio: float) -> str:
            result = ' '.join(new_words[num_words:])
            logger.debug(
                f"Deduplicated {num_words} words from live transcription "
                f"(similarity={ratio:.2f})"
            )
            return result

        for num_words, min_ratio in self._DEDUP_FUZZY_THRESHOLDS.items():
            if num_words > len(prev_words_lower) or num_words > len(new_words_lower):
                continue
            prev_phrase = ' '.join(prev_words_lower[-num_words:])
            new_phrase_start = ' '.join(new_words_lower[:num_words])
            ratio = difflib.SequenceMatcher(None, prev_phrase, new_phrase_start).ratio()
            if ratio >= min_ratio:
                return _strip(num_words, ratio)

        for num_words in self._DEDUP_EXACT_ONLY_LENGTHS:
            if num_words > len(prev_words_lower) or num_words > len(new_words_lower):
                continue
            prev_phrase = ' '.join(prev_words_lower[-num_words:])
            new_phrase_start = ' '.join(new_words_lower[:num_words])
            if prev_phrase == new_phrase_start:
                return _strip(num_words, 1.0)

        return new_text
    
    def reset(self, stream: Hashable = None) -> None:
        """Forget context for one stream, or for all streams if none given."""
        if stream is None:
            self._context.clear()
            self._last_text.clear()
        else:
            self._context.pop(stream, None)
            self._last_text.pop(stream, None)
        logger.info("LiveTranscriber state reset")

    def forget_session(self, session_id: int) -> None:
        """Drop the state of every ``(session_id, source)`` stream."""
        for stream in [s for s in self._context if isinstance(s, tuple) and s[:1] == (session_id,)]:
            self.reset(stream)


def transcribe_audio_chunk(chunk: AudioChunk) -> Optional[Dict[str, Any]]:
    """Convenience function to transcribe an audio chunk.
    
    Creates a LiveTranscriber, transcribes the chunk, and returns the result.
    
    Args:
        chunk: AudioChunk object containing audio file path and metadata
        
    Returns:
        Dict with transcription result, or None if transcription fails
    """
    transcriber = LiveTranscriber()
    return transcriber.transcribe_chunk(chunk)
