"""Transcription processor for handling audio transcription workflow."""
import logging
import os
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict, Any, Set
import glob as glob_module
import threading

from .parakeet import ParakeetV3, ParakeetError, ModelLoadError

logger = logging.getLogger(__name__)


class TranscriptionProcessor:
    """Process audio files for transcription using Parakeet V3.
    
    Handles transcription of both microphone and system audio recordings,
    stores results in database with timestamps for synchronization.
    """
    
    SOURCE_MICROPHONE = 'microphone'
    SOURCE_SYSTEM = 'system'
    
    def __init__(self, 
                 session_path: str,
                 db=None,
                 model_path: Optional[str] = None,
                 scorer_path: Optional[str] = None):
        """Initialize transcription processor.
        
        Args:
            session_path: Path to session directory containing audio files
            db: Database instance for storing transcripts (optional)
            model_path: Path to Parakeet model file (optional)
            scorer_path: Path to language model scorer (optional)
        """
        self.session_path = Path(session_path)
        self.audio_path = self.session_path / 'audio'
        # Separate paths for mic and system audio
        self.mic_audio_path = self.audio_path / 'mic'
        self.system_audio_path = self.audio_path / 'system'
        self.db = db
        self.parakeet = ParakeetV3(model_path=model_path, scorer_path=scorer_path)
        self._is_loaded = False
        
        # Track processed chunks to avoid re-transcription
        self._processed_chunks: Set[str] = set()
        
        # Polling state for continuous chunk processing
        self._is_polling = False
        self._polling_thread: Optional[threading.Thread] = None
        self._stop_polling_event = threading.Event()
    
    def load_model(self) -> None:
        """Load Parakeet model.
        
        Raises:
            ModelLoadError: If model cannot be loaded
        """
        if self._is_loaded:
            return
        
        try:
            self.parakeet.load()
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
        
        # Sort by modification time (oldest first)
        files.sort(key=lambda f: Path(f).stat().st_mtime)
        
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
    
    def transcribe_audio(self, audio_path: str) -> str:
        """Transcribe a single audio file.
        
        Args:
            audio_path: Path to audio file
            
        Returns:
            Transcribed text string
            
        Raises:
            ParakeetError: If transcription fails
            ModelLoadError: If model cannot be loaded
        """
        if not self._is_loaded:
            self.load_model()
        
        return self.parakeet.transcribe(audio_path)
    
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
        
        try:
            # Transcribe audio
            text = self.transcribe_audio(str(audio_file))
            
            # Store in database if available
            if self.db:
                self.db.add_transcript(
                    session_id=session_id,
                    timestamp=timestamp,
                    text=text,
                    source=source
                )
                logger.info(f"Stored transcript for {audio_file.name}")
            
            return {
                'filepath': str(audio_file),
                'timestamp': timestamp,
                'text': text,
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
        return self.parakeet.get_model_info()
    
    def unload_model(self) -> None:
        """Unload model to free memory."""
        self.parakeet.unload()
        self._is_loaded = False
        logger.info("TranscriptionProcessor model unloaded")
    
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
