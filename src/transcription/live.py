"""Live transcription functionality for real-time audio chunk processing.

This module provides functions for transcribing audio chunks in real-time
during a recording session.
"""

import logging
from pathlib import Path
from typing import Optional, Dict, Any

from ..audio_capture.chunk import AudioChunk
from .parakeet import ParakeetEngine, ModelLoadError, TranscriptionError

logger = logging.getLogger(__name__)


class LiveTranscriber:
    """Handles real-time transcription of audio chunks.
    
    This class wraps the ParakeetEngine for live transcription use cases,
    maintaining state for context and deduplication.
    """
    
    def __init__(self, model_path: Optional[str] = None):
        """Initialize the live transcriber.
        
        Args:
            model_path: Optional path to Parakeet model.
        """
        self.engine = ParakeetEngine(model_path=model_path)
        self._is_loaded = False
        
        # Context for improved transcription
        self._context: list = []
        self._last_text: Optional[str] = None
    
    def load_model(self) -> None:
        """Load the transcription model.
        
        Raises:
            ModelLoadError: If model cannot be loaded
        """
        if self._is_loaded:
            return
        
        self.engine.load()
        self._is_loaded = True
        logger.info("LiveTranscriber model loaded")
    
    def transcribe_chunk(self, chunk: AudioChunk) -> Optional[Dict[str, Any]]:
        """Transcribe a single audio chunk.
        
        Args:
            chunk: AudioChunk object containing audio file path and metadata
            
        Returns:
            Dict with transcription result, or None if transcription fails
        """
        if not self._is_loaded:
            self.load_model()
        
        audio_path = chunk.file_path
        
        if not audio_path or not Path(audio_path).exists():
            logger.warning(f"Audio file not found: {audio_path}")
            return None
        
        try:
            # Build initial_prompt from context for improved accuracy
            initial_prompt = ' '.join(self._context) if self._context else None
            
            # Transcribe the audio chunk
            text = self.engine.transcribe(audio_path, initial_prompt=initial_prompt)
            
            if not text:
                logger.debug(f"No text transcribed from chunk: {chunk.chunk_id}")
                return None
            
            # Apply deduplication if we have previous text
            if self._last_text:
                text = self._deduplicate(text)
            
            # Update context
            self._context.append(text)
            if len(self._context) > 5:
                self._context = self._context[-5:]
            
            self._last_text = text
            
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
    
    def _deduplicate(self, new_text: str) -> str:
        """Remove overlapping text from new transcription.
        
        Args:
            new_text: Newly transcribed text
            
        Returns:
            Deduplicated text
        """
        if not self._last_text or not new_text:
            return new_text
        
        prev_words = self._last_text.lower().split()
        new_words = new_text.lower().split()
        
        if len(prev_words) < 2 or len(new_words) < 2:
            return new_text
        
        # Try to find overlapping phrases at the start
        for num_words in [8, 6, 4, 3, 2]:
            if num_words > len(prev_words) or num_words > len(new_words):
                continue
            
            prev_phrase = ' '.join(prev_words[-num_words:])
            new_phrase_start = ' '.join(new_words[:num_words])
            
            if prev_phrase == new_phrase_start:
                # Found overlap - remove it
                result = ' '.join(new_words[num_words:])
                logger.debug(f"Deduplicated {num_words} words from live transcription")
                return result
        
        return new_text
    
    def reset(self) -> None:
        """Reset context and state for a new session."""
        self._context.clear()
        self._last_text = None
        logger.info("LiveTranscriber state reset")


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
