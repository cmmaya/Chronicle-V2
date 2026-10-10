"""Transcription processor for handling audio transcription workflow."""
import logging
import os
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict, Any, Set, Callable
import glob as glob_module
import threading

from .parakeet import ParakeetEngine, ParakeetError, ModelLoadError, get_shared_engine
from ..audio.importer import IMPORTED_CHUNK_SUFFIX

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
                 language: str = 'en',
                 engine: Optional[ParakeetEngine] = None):

        """Initialize transcription processor.

        Args:
            session_path: Path to session directory containing audio files
            db: Database instance for storing transcripts (optional)
            model_path: Parakeet ONNX model name/path. If given, the processor
                        loads its own engine for it; otherwise it uses the
                        app-wide shared engine (one model copy in memory).
            scorer_path: Not used for Parakeet (kept for API compatibility)
            language: Optional language code kept for API compatibility. Parakeet v3 can
                      auto-detect supported languages.
            engine: Engine to use instead of the shared one.
        """
        self.session_path = Path(session_path)
        self.audio_path = self.session_path / 'audio'
        # Separate paths for mic and system audio
        self.mic_audio_path = self.audio_path / 'mic'
        self.system_audio_path = self.audio_path / 'system'
        self.db = db
        if engine is not None:
            self.whisper = engine
        elif model_path:
            self.whisper = ParakeetEngine(
                model_path=model_path,
                scorer_path=scorer_path,
                language=language,
            )
        else:
            self.whisper = get_shared_engine()
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

    @staticmethod
    def _extract_end_timestamp(filepath: Path) -> Optional[datetime]:
        """End time from a ``<start>_<end>[_suffix].wav`` chunk name, if present."""
        try:
            return datetime.strptime(filepath.stem[16:31], '%Y%m%d_%H%M%S')
        except ValueError:
            return None

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
    
    @staticmethod
    def chunk_key(audio_file: Path) -> str:
        """How a chunk is recorded in ``transcripts.audio_file``."""
        return f'{audio_file.parent.name}/{audio_file.name}'

    def _already_transcribed(self, session_id: int) -> Callable[[Path, str], bool]:
        """Predicate telling whether a chunk already has a transcript row.

        Live transcription writes a row per chunk while recording, so a batch
        pass over the same session must skip those chunks - running it used
        to insert every line a second time.
        """
        if not self.db or not hasattr(self.db, 'get_transcribed_chunk_keys'):
            return lambda audio_file, source: False
        audio_files, legacy_keys = self.db.get_transcribed_chunk_keys(session_id)

        def check(audio_file: Path, source: str) -> bool:
            if self.chunk_key(audio_file) in audio_files:
                return True
            # Older rows don't name their file; a chunk's file name starts
            # with its start time, which those rows do carry.
            stamp = int(self._extract_timestamp(audio_file).timestamp())
            return (source, stamp) in legacy_keys

        return check

    def _pending_audio_files(self, session_id: int, source: str) -> List[Path]:
        """Audio files of one source that have no transcript yet."""
        done = self._already_transcribed(session_id)
        return [f for f in self._get_audio_files(source=source) if not done(f, source)]

    def _process_audio_by_source(self, session_id: int, source: str,
                                 on_chunk: Optional[Callable[[], None]] = None) -> List[Dict[str, Any]]:
        """Transcribe every audio file of one source that has no transcript yet.

        Args:
            session_id: Database session ID
            source: Audio source ('microphone' or 'system')
            on_chunk: Called after each file is attempted (success or not)

        Returns:
            List of transcription results
        """
        audio_files = self._pending_audio_files(session_id, source)
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
            if on_chunk:
                on_chunk()
        
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
        
        # Require at least 2 words in both for meaningful deduplication
        if len(prev_words) < 2 or len(new_words) < 2:
            logger.debug(f"Deduplication skipped: prev_words={len(prev_words)}, new_words={len(new_words)}")
            return new_text
        
        # Common small words that might be added at chunk boundaries
        prefix_words = {'and', 'but', 'so', 'the', 'a', 'an', 'to', 'of', 'in', 'for', 'is', 'it', 'that', 'this', 'with', 'as'}
        
        new_lower = new_text.lower().strip()
        
        # Strategy 1: Direct match at start (no prefix)
        result = self._try_match_at_start(prev_words, new_words, new_lower, allow_prefix=False)
        if result is not None:
            return result
        
        # Strategy 2: Try with potential prefix words (e.g., "and good enough")
        if new_words[0].lower() in prefix_words:
            result = self._try_match_at_start(prev_words, new_words, new_lower, allow_prefix=True)
            if result is not None:
                return result
        
        # Strategy 3: Fuzzy match anywhere in the text (for non-contiguous overlaps)
        result = self._try_fuzzy_match(prev_words, new_words)
        if result is not None:
            return result
        
        logger.debug(f"No overlap detected between transcripts")
        return new_text
    
    def _try_match_at_start(self, prev_words: list, new_words: list, new_lower: str, allow_prefix: bool) -> Optional[str]:
        """Try to find overlap at the start of new text.
        
        Args:
            prev_words: Words from previous transcript
            new_words: Words from new transcript  
            new_lower: Lowercased new text
            allow_prefix: If True, skip first word of new if it's a small word
            
        Returns:
            Deduplicated text if overlap found, None otherwise
        """
        prefix_words = {'and', 'but', 'so', 'the', 'a', 'an', 'to', 'of', 'in', 'for', 'is', 'it', 'that', 'this', 'with', 'as'}
        
        # Check from longer phrases down to 2 words
        for num_words in [min(8, len(prev_words)), min(6, len(prev_words)), 
                          min(4, len(prev_words)), 3, 2]:
            # Get last N words from previous
            prev_phrase = ' '.join(prev_words[-num_words:]).lower()
            prev_phrase = prev_phrase.rstrip('.,!?')
            
            # Direct match
            idx = new_lower.find(prev_phrase)
            if idx == 0:
                result_words = new_words[num_words:]
                result = ' '.join(result_words)
                logger.info(f"Deduplication: removed {num_words} words (exact). Before: {len(new_words)} words, After: {len(result_words)} words")
                return result
            
            # If allow_prefix, try skipping first word
            if allow_prefix and len(new_words) > 1 and new_words[0].lower() in prefix_words:
                new_without_prefix = ' '.join(new_words[1:]).lower()
                idx = new_without_prefix.find(prev_phrase)
                if idx == 0:
                    result_words = new_words[1 + num_words:]
                    result = ' '.join(result_words)
                    logger.info(f"Deduplication: removed {num_words} words (with prefix). Before: {len(new_words)} words, After: {len(result_words)} words")
                    return result
        
        return None
    
    def _try_fuzzy_match(self, prev_words: list, new_words: list) -> Optional[str]:
        """Try to find overlapping text anywhere in the new transcript.
        
        Looks for any sequence of 2+ consecutive words from previous transcript
        that appears anywhere in the new transcript.
        
        Args:
            prev_words: Words from previous transcript
            new_words: Words from new transcript
            
        Returns:
            Deduplicated text if overlap found, None otherwise
        """
        prev_str = ' '.join(prev_words).lower()
        new_str = ' '.join(new_words).lower()
        
        # Try phrases of different lengths (longer first)
        for num_words in [min(6, len(prev_words)), min(5, len(prev_words)), 
                          min(4, len(prev_words)), 3, 2]:
            if num_words < 2:
                continue
                
            # Check each position in prev_words
            for start_idx in range(len(prev_words) - num_words + 1):
                phrase = ' '.join(prev_words[start_idx:start_idx + num_words]).lower()
                phrase = phrase.rstrip('.,!?')
                
                if not phrase:
                    continue
                    
                # Find this phrase in new text
                idx = new_str.find(phrase)
                if idx >= 0:
                    # Found overlap - remove it
                    # Count words before the match
                    words_before = new_str[:idx].split()
                    
                    # Remove the overlapping phrase
                    phrase_word_count = len(phrase.split())
                    result_words = words_before + new_words[len(words_before) + phrase_word_count:]
                    result = ' '.join(result_words)
                    
                    logger.info(f"Deduplication: removed fuzzy overlap ({num_words} words). Before: {len(new_words)} words, After: {len(result_words)} words")
                    return result
        
        return None
    
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
            
            # Deduplicate based on previous transcript. Uploaded audio (BU104)
            # is cut at pauses with no overlap, so there is nothing to strip -
            # and the fuzzy matcher would delete genuinely repeated phrases.
            if audio_file.stem.endswith(IMPORTED_CHUNK_SUFFIX):
                deduplicated_text = text
            else:
                deduplicated_text = self._deduplicate_transcription(text)
            
            # Update context for next transcription (pass original for comparison)
            self._update_context(deduplicated_text, original_text)
            
            # Store in database if available
            if self.db:
                self.db.add_transcript(
                    session_id=session_id,
                    timestamp=timestamp,
                    text=deduplicated_text,
                    source=source,
                    end_timestamp=self._extract_end_timestamp(audio_file),
                    audio_file=self.chunk_key(audio_file),
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

        done = self._already_transcribed(session_id)
        new_chunks = self.get_new_chunks(source)
        results = []

        for audio_file in new_chunks:
            filepath_str = str(audio_file)

            # Skip if already marked as processed (race condition protection)
            # or transcribed live
            if self._is_chunk_processed(filepath_str) or done(audio_file, source):
                self._mark_chunk_processed(filepath_str)
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
    
    def process_all(self, session_id: int,
                    progress: Optional[Callable[[int, int], None]] = None) -> Dict[str, List[Dict[str, Any]]]:
        """Process all audio files (microphone and system) for a session.
        
        Args:
            session_id: Database session ID
            progress: Optional ``progress(done, total)`` callback, called once
                with ``done=0`` and again after each chunk. ``total`` counts
                the chunks that still need a transcript.
            
        Returns:
            Dictionary with 'microphone' and 'system' lists of results
        """
        # No eager load_model(): transcribe_audio() loads on the first file
        # that actually needs it, so a session whose chunks were all
        # transcribed live never pulls the model into memory.
        on_chunk = None
        if progress:
            total = sum(len(self._pending_audio_files(session_id, source))
                        for source in (self.SOURCE_MICROPHONE, self.SOURCE_SYSTEM))
            finished = 0
            progress(0, total)

            def on_chunk():
                nonlocal finished
                finished += 1
                progress(finished, total)

        microphone_results = self._process_audio_by_source(
            session_id, self.SOURCE_MICROPHONE, on_chunk)
        system_results = self._process_audio_by_source(
            session_id, self.SOURCE_SYSTEM, on_chunk)
        
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
        """Unload model to free memory (never the shared engine, which live
        transcription may be using; the app unloads that when idle)."""
        if self.whisper is not get_shared_engine():
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
