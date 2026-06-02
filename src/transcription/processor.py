"""Transcription processor for handling audio transcription workflow."""
import logging
import os
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict, Any, Set
import glob as glob_module
import threading

from .parakeet import ParakeetEngine, ParakeetError, ModelLoadError

logger = logging.getLogger(__name__)


class TranscriptionProcessor:
    """Process audio files for transcription using Parakeet.
    
    Handles transcription of both microphone and system audio recordings,
    stores results in database with timestamps for synchronization.
    """
    
    SOURCE_MICROPHONE = 'microphone'
    SOURCE_SYSTEM = 'system'
    
    # Maximum characters for context prompt (context limit)
    MAX_CONTEXT_LENGTH = 2000
    
    def __init__(self, 
                 session_path: str,
                 db=None,
                 model_path: Optional[str] = None,
                 scorer_path: Optional[str] = None,
                 language: str = 'en'):

        """Initialize transcription processor.
        
        Args:
            session_path: Path to session directory containing audio files
            db: Database instance for storing transcripts (optional)
            model_path: Parakeet ONNX model name/path. If None, ParakeetEngine defaults to
                        'nemo-parakeet-tdt-0.6b-v3'.
            scorer_path: Not used for Parakeet (kept for API compatibility)
            language: Optional language code kept for API compatibility. Parakeet v3 can
                      auto-detect supported languages.
        """
        self.session_path = Path(session_path)
        self.audio_path = self.session_path / 'audio'
        # Separate paths for mic and system audio
        self.mic_audio_path = self.audio_path / 'mic'
        self.system_audio_path = self.audio_path / 'system'
        self.db = db
        self.whisper = ParakeetEngine(
            model_path=model_path,
            scorer_path=scorer_path,
            language=language,
        )
        self._is_loaded = False
        
        # Track processed chunks to avoid re-transcription
        self._processed_chunks: Set[str] = set()
        
        # Polling state for continuous chunk processing
        self._is_polling = False
        self._polling_thread: Optional[threading.Thread] = None
        self._stop_polling_event = threading.Event()
        
        # Context for deduplication and improved transcription
        self._transcription_context: List[str] = []
        self._last_transcript: Optional[str] = None  # Original text for deduplication
        self._last_transcript_dedup: Optional[str] = None  # Deduplicated text for context
    
    def load_model(self) -> None:
        """Load Parakeet model.
        
        Raises:
            ModelLoadError: If model cannot be loaded
        """
        if self._is_loaded:
            return
        
        try:
            self.whisper.load()
            self._is_loaded = True
            logger.info("TranscriptionProcessor model loaded")
        except ModelLoadError as e:
            logger.error(f"Failed to load transcription model: {str(e)}")
            raise
    
    def _get_audio_files(self, source: Optional[str] = None) -> List[Path]:
        """Get list of audio files in session directory.
        
        Args:
            source: Filter by source ('microphone' or 'system'), or None for all
            
        Returns:
            List of audio file paths sorted by modification time
        """
        if not self.audio_path.exists():
            return []
        
        # Determine which directory(s) to scan
        if source == self.SOURCE_MICROPHONE:
            dirs_to_scan = [self.mic_audio_path] if self.mic_audio_path.exists() else []
        elif source == self.SOURCE_SYSTEM:
            dirs_to_scan = [self.system_audio_path] if self.system_audio_path.exists() else []
        else:
            dirs_to_scan = [d for d in [self.mic_audio_path, self.system_audio_path] if d.exists()]
        
        # Collect all WAV files from directories
        files = []
        for audio_dir in dirs_to_scan:
            pattern = str(audio_dir / '*.wav')
            files.extend(glob_module.glob(pattern))
        
        # Sort by timestamp in filename (YYYYMMDD_HHMMSS), not modification time
        # This ensures chunks are processed in chronological order for deduplication
        def get_timestamp_from_file(filepath: str) -> datetime:
            try:
                name = Path(filepath).stem
                if len(name) >= 15:
                    date_str = name[:15]
                    return datetime.strptime(date_str, '%Y%m%d_%H%M%S')
            except ValueError:
                pass
            # Fallback to modification time
            return datetime.fromtimestamp(Path(filepath).stat().st_mtime)
        
        files.sort(key=get_timestamp_from_file)

        return [Path(f) for f in files]
    
    def _get_source_from_filename(self, filepath: Path) -> str:
        """Determine audio source from filename.
        
        Args:
            filepath: Path to audio file
            
        Returns:
            'system' if filename contains '_system', else 'microphone'
        """
        if '_system' in filepath.stem:
            return self.SOURCE_SYSTEM
        return self.SOURCE_MICROPHONE
    
    def _extract_timestamp(self, filepath: Path) -> datetime:
        """Extract timestamp from audio filename.
        
        Filename format: YYYYMMDD_HHMMSS_label.wav
        
        Args:
            filepath: Path to audio file
            
        Returns:
            datetime object extracted from filename
        """
        try:
            # Extract timestamp from filename (first 15 characters: YYYYMMDD_HHMMSS)
            name = filepath.stem
            if len(name) >= 15:
                date_str = name[:15]  # YYYYMMDD_HHMMSS
                return datetime.strptime(date_str, '%Y%m%d_%H%M%S')
        except ValueError:
            pass
        
        # Fallback: use file modification time
        return datetime.fromtimestamp(filepath.stat().st_mtime)
    
    def transcribe_audio(self, audio_path: str, initial_prompt: Optional[str] = None) -> str:
        """Transcribe a single audio file.
        
        Args:
            audio_path: Path to audio file
            initial_prompt: Optional context argument kept for API compatibility
            
        Returns:
            Transcribed text string
            
        Raises:
            ParakeetError: If transcription fails
            ModelLoadError: If model cannot be loaded
        """
        if not self._is_loaded:
            self.load_model()
        
        return self.whisper.transcribe(audio_path, initial_prompt=initial_prompt)
    
    def process_microphone_audio(self, session_id: int) -> List[Dict[str, Any]]:
        """Process all microphone audio files for a session.
        
        Args:
            session_id: Database session ID
            
        Returns:
            List of transcription results with metadata
        """
        return self._process_audio_by_source(session_id, self.SOURCE_MICROPHONE)
    
    def process_system_audio(self, session_id: int) -> List[Dict[str, Any]]:
        """Process all system audio files for a session.
        
        Args:
            session_id: Database session ID
            
        Returns:
            List of transcription results with metadata
        """
        return self._process_audio_by_source(session_id, self.SOURCE_SYSTEM)
    
    def _process_audio_by_source(self, session_id: int, source: str) -> List[Dict[str, Any]]:
        """Process all audio files of a specific source type.
        
        Args:
            session_id: Database session ID
            source: Audio source ('microphone' or 'system')
            
        Returns:
            List of transcription results
        """
        audio_files = self._get_audio_files(source=source)
        results = []
        
        for audio_file in audio_files:
            try:
                result = self._transcribe_and_store(audio_file, session_id, source)
                if result:
                    results.append(result)
            except ModelLoadError as e:
                logger.error(f"Failed to load transcription model for {audio_file}: {str(e)}")
                raise
            except ParakeetError as e:
                logger.error(f"Failed to transcribe {audio_file}: {str(e)}")
            except Exception as e:
                logger.error(f"Unexpected error processing {audio_file}: {str(e)}")
        
        logger.info(f"Processed {len(results)} {source} audio files")
        return results
    
    def _get_context_prompt(self) -> Optional[str]:
        """Build context prompt from recent transcriptions.
        
        Returns:
            Context string for Whisper initial_prompt, or None if no context
        """
        if not self._transcription_context:
            return None
        
        # Join recent transcriptions, limiting to MAX_CONTEXT_LENGTH
        context = ' '.join(self._transcription_context)
        if len(context) > self.MAX_CONTEXT_LENGTH:
            # Truncate to last MAX_CONTEXT_LENGTH characters
            context = context[-self.MAX_CONTEXT_LENGTH:]
        
        return context
    
    def _deduplicate_transcription(self, new_text: str) -> str:
        """Remove overlapping text from new transcription based on previous transcript.
        
        Uses a simpler algorithm to find and remove repeated phrases at the
        beginning of the new text that appear at the end of the previous transcript.
        
        Args:
            new_text: The newly transcribed text
            
        Returns:
            Deduplicated text with overlapping content removed
        """
        if not self._last_transcript or not new_text:
            return new_text
        
        # Get words from previous transcript
        prev_words = self._last_transcript.split()
        new_words = new_text.split()
        
        if len(prev_words) < 3 or len(new_words) < 3:
            logger.debug(f"Deduplication skipped: prev_words={len(prev_words)}, new_words={len(new_words)}")
            return new_text
        
        # Try to find overlap starting from longer phrases to shorter
        # Check last 8, 6, 4, 3 words of previous transcript
        for num_words in [min(8, len(prev_words)), min(6, len(prev_words)), 
                          min(4, len(prev_words)), 3]:
            # Get last N words from previous
            phrase = ' '.join(prev_words[-num_words:]).lower()
            
            # Check if new text starts with similar phrase
            new_lower = new_text.lower().strip()
            
            # Find this phrase in the beginning of new text
            idx = new_lower.find(phrase)
            if idx == 0:
                # Found exact match at start - remove it
                result_words = new_words[num_words:]
                result = ' '.join(result_words)
                logger.info(f"Deduplication: removed {num_words} words overlap. Before: {len(new_words)} words, After: {len(result_words)} words")
                return result
            
            # Check for partial match (whitespace variations)
            phrase_words = phrase.split()
            if len(phrase_words) >= 3:
                # Check if first 3+ words match
                new_start = ' '.join(new_words[:num_words]).lower()
                if self._words_similar(phrase, new_start):
                    result_words = new_words[num_words:]
                    result = ' '.join(result_words)
                    logger.info(f"Deduplication: removed {num_words} words (partial match). Before: {len(new_words)} words, After: {len(result_words)} words")
                    return result
        
        logger.debug(f"No overlap detected between transcripts")
        return new_text
    
    def _words_similar(self, phrase1: str, phrase2: str, threshold: float = 0.7) -> bool:
        """Check if two phrases are similar (70%+ word overlap).
        
        Args:
            phrase1: First phrase
            phrase2: Second phrase
            threshold: Minimum similarity ratio (0-1)
            
        Returns:
            True if phrases are similar
        """
        words1 = set(phrase1.split())
        words2 = set(phrase2.split())
        
        if not words1 or not words2:
            return False
        
        intersection = words1 & words2
        max_len = max(len(words1), len(words2))
        
        return len(intersection) / max_len >= threshold
    
    def _update_context(self, text: str, original_text: str = None) -> None:
        """Update transcription context with new text.
        
        Args:
            text: The deduplicated text to add to context
            original_text: The original text before deduplication (for next chunk comparison)
        """
        if not text:
            return
        
        # Add deduplicated text to context for Whisper prompt
        self._transcription_context.append(text)
        
        # Keep only last 5 transcriptions in context window
        max_context_size = 5
        if len(self._transcription_context) > max_context_size:
            self._transcription_context = self._transcription_context[-max_context_size:]
        
        # Store original text (before deduplication) for next chunk's deduplication
        # This is what we compare against to find overlaps
        self._last_transcript = original_text if original_text else text
        # Also store deduplicated version for reference
        self._last_transcript_dedup = text
    
    def _transcribe_and_store(self, 
                               audio_file: Path, 
                               session_id: int, 
                               source: str) -> Optional[Dict[str, Any]]:
        """Transcribe a single file and store in database.
        
        Args:
            audio_file: Path to audio file
            session_id: Database session ID
            source: Audio source type
            
        Returns:
            Transcription result dict or None if failed
        """
        timestamp = self._extract_timestamp(audio_file)
        
        # Get context prompt for improved transcription
        context_prompt = self._get_context_prompt()
        
        if context_prompt:
            logger.debug(f"Using context prompt ({len(context_prompt)} chars) for {audio_file.name}")
        
        try:
            # Transcribe audio. Parakeet does not consume initial_prompt; the engine keeps this argument for API compatibility.
            text = self.transcribe_audio(str(audio_file), initial_prompt=context_prompt)
            
            # Store original text before deduplication
            original_text = text
            
            # Deduplicate based on previous transcript
            deduplicated_text = self._deduplicate_transcription(text)
            
            # Update context for next transcription (pass original for comparison)
            self._update_context(deduplicated_text, original_text)
            
            # Store in database if available
            if self.db:
                self.db.add_transcript(
                    session_id=session_id,
                    timestamp=timestamp,
                    text=deduplicated_text,
                    source=source
                )
                logger.info(f"Stored transcript for {audio_file.name}")
            
            return {
                'filepath': str(audio_file),
                'timestamp': timestamp,
                'text': deduplicated_text,
                'source': source,
                'session_id': session_id
            }
            
        except ParakeetError as e:
            logger.error(f"Transcription failed for {audio_file}: {str(e)}")
            return None
    
    def _mark_chunk_processed(self, filepath: str) -> None:
        """Mark a chunk as processed to avoid re-transcription.
        
        Args:
            filepath: Path to the audio file that was processed
        """
        self._processed_chunks.add(filepath)
        logger.debug(f"Marked chunk as processed: {filepath}")
    
    def _is_chunk_processed(self, filepath: str) -> bool:
        """Check if a chunk has already been processed.
        
        Args:
            filepath: Path to the audio file
            
        Returns:
            True if already processed, False otherwise
        """
        return filepath in self._processed_chunks
    
    def get_new_chunks(self, source: str) -> List[Path]:
        """Get list of new audio chunks that haven't been transcribed.
        
        Args:
            source: Audio source ('microphone' or 'system')
            
        Returns:
            List of unprocessed audio file paths
        """
        all_chunks = self._get_audio_files(source)
        new_chunks = [
            chunk for chunk in all_chunks
            if not self._is_chunk_processed(str(chunk))
        ]
        
        if new_chunks:
            logger.info(f"Found {len(new_chunks)} new chunks for {source}")
        
        return new_chunks
    
    def get_new_chunks_all(self) -> Dict[str, List[Path]]:
        """Get new chunks from both sources.
        
        Returns:
            Dictionary with 'microphone' and 'system' lists of new chunks
        """
        return {
            'microphone': self.get_new_chunks(self.SOURCE_MICROPHONE),
            'system': self.get_new_chunks(self.SOURCE_SYSTEM)
        }
    
    def process_new_chunks(self, session_id: int, source: str) -> List[Dict[str, Any]]:
        """Process only new chunks from a specific source.
        
        This method is designed to be called repeatedly during a session
        to transcribe chunks as they appear.
        
        Args:
            session_id: Database session ID
            source: Audio source ('microphone' or 'system')
            
        Returns:
            List of transcription results for new chunks
        """
        if not self._is_loaded:
            self.load_model()
        
        new_chunks = self.get_new_chunks(source)
        results = []
        
        for audio_file in new_chunks:
            filepath_str = str(audio_file)
            
            # Skip if already marked as processed (race condition protection)
            if self._is_chunk_processed(filepath_str):
                continue
            
            try:
                result = self._transcribe_and_store(audio_file, session_id, source)
                if result:
                    # Mark as processed after successful transcription
                    self._mark_chunk_processed(filepath_str)
                    results.append(result)
                    logger.info(f"Transcribed new chunk: {audio_file.name}")
            except ModelLoadError as e:
                logger.error(f"Failed to load transcription model for {audio_file}: {str(e)}")
                raise
            except ParakeetError as e:
                logger.error(f"Failed to transcribe {audio_file}: {str(e)}")
            except Exception as e:
                logger.error(f"Unexpected error processing {audio_file}: {str(e)}")
        
        return results
    
    def process_new_chunks_all(self, session_id: int) -> Dict[str, List[Dict[str, Any]]]:
        """Process new chunks from both sources.
        
        Args:
            session_id: Database session ID
            
        Returns:
            Dictionary with 'microphone' and 'system' lists of results
        """
        mic_results = self.process_new_chunks(session_id, self.SOURCE_MICROPHONE)
        sys_results = self.process_new_chunks(session_id, self.SOURCE_SYSTEM)
        
        return {
            'microphone': mic_results,
            'system': sys_results
        }
    
    def process_all(self, session_id: int) -> Dict[str, List[Dict[str, Any]]]:
        """Process all audio files (microphone and system) for a session.
        
        Args:
            session_id: Database session ID
            
        Returns:
            Dictionary with 'microphone' and 'system' lists of results
        """
        if not self._is_loaded:
            self.load_model()
        
        microphone_results = self.process_microphone_audio(session_id)
        system_results = self.process_system_audio(session_id)
        
        return {
            'microphone': microphone_results,
            'system': system_results
        }
    
    def get_unprocessed_files(self) -> Dict[str, List[Path]]:
        """Get list of audio files that haven't been transcribed.
        
        Returns:
            Dictionary with 'microphone' and 'system' lists of unprocessed files
        """
        if not self.db:
            # No database - return all files as unprocessed
            return {
                'microphone': self._get_audio_files(self.SOURCE_MICROPHONE),
                'system': self._get_audio_files(self.SOURCE_SYSTEM)
            }
        
        # Query database for already processed files
        # This is a simplified check - in production you'd want more robust tracking
        all_files = {
            'microphone': self._get_audio_files(self.SOURCE_MICROPHONE),
            'system': self._get_audio_files(self.SOURCE_SYSTEM)
        }
        
        return all_files
    
    def get_model_info(self) -> Dict[str, Any]:
        """Get information about the transcription model.
        
        Returns:
            Dictionary with model metadata
        """
        return self.whisper.get_model_info()
    
    def unload_model(self) -> None:
        """Unload model to free memory."""
        self.whisper.unload()
        self._is_loaded = False
        logger.info("TranscriptionProcessor model unloaded")
    
    def reset_context(self) -> None:
        """Reset transcription context and deduplication state.
        
        Useful when starting a new transcription session or processing
        a different session's audio.
        """
        self._transcription_context.clear()
        self._last_transcript = None
        self._last_transcript_dedup = None
        logger.info("Reset transcription context")
    
    def _polling_loop(self, session_id: int, poll_interval: float = 2.0) -> None:
        """Background polling loop for continuous chunk transcription.
        
        Args:
            session_id: Database session ID
            poll_interval: Seconds between polls (default 2.0)
        """
        import time
        
        logger.info(f"Started polling loop (interval={poll_interval}s)")
        
        while not self._stop_polling_event.is_set():
            try:
                # Process new chunks from both sources
                results = self.process_new_chunks_all(session_id)
                
                total_processed = len(results.get('microphone', [])) + len(results.get('system', []))
                if total_processed > 0:
                    logger.info(f"Polling: processed {total_processed} new chunks")
                    
            except Exception as e:
                logger.error(f"Polling error: {str(e)}")
            
            # Wait for next poll cycle
            self._stop_polling_event.wait(poll_interval)
        
        logger.info("Stopped polling loop")
    
    def start_polling(self, session_id: int, poll_interval: float = 2.0) -> None:
        """Start continuous chunk polling for real-time transcription.
        
        Args:
            session_id: Database session ID
            poll_interval: Seconds between polls (default 2.0)
        """
        if self._is_polling:
            logger.warning("Polling already running")
            return
        
        if not self._is_loaded:
            self.load_model()
        
        self._stop_polling_event.clear()
        self._is_polling = True
        
        self._polling_thread = threading.Thread(
            target=self._polling_loop,
            args=(session_id, poll_interval),
            daemon=True
        )
        self._polling_thread.start()
        
        logger.info(f"Started chunk polling (session_id={session_id}, interval={poll_interval}s)")
    
    def stop_polling(self) -> None:
        """Stop continuous chunk polling."""
        if not self._is_polling:
            return
        
        logger.info("Stopping chunk polling...")
        self._stop_polling_event.set()
        self._is_polling = False
        
        if self._polling_thread:
            self._polling_thread.join(timeout=5.0)
            self._polling_thread = None
        
        logger.info("Stopped chunk polling")
    
    @property
    def is_polling(self) -> bool:
        """Check if polling is active.
        
        Returns:
            True if polling is running, False otherwise
        """
        return self._is_polling
    
    def clear_processed_chunks(self) -> None:
        """Clear the processed chunks tracking.
        
        Useful when starting a new transcription session or for testing.
        """
        self._processed_chunks.clear()
        logger.info("Cleared processed chunks tracking")
