"""Session manager for coordinating session lifecycle and components."""
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict, Any, Callable

from ..storage.database import Database
from ..audio_capture.core import DualSourceChunkedRecorder
from ..screenshots.capture import ScreenshotCapture
from ..transcription.processor import TranscriptionProcessor
from ..transcription.live import LiveTranscriber
from ..summarization import SummaryGenerator
from ..rag.indexer import index_session_content

from .session import Session
from .timeline import Timeline
from ..config import SESSION

logger = logging.getLogger(__name__)


class SessionManager:
    """Manages session lifecycle and coordinates all components.
    
    Handles session creation, component initialization, and coordinates
    the workflow between audio recording, screenshots, and transcription.
    """
    
    def __init__(self, 
                 base_path: str = 'sessions',
                 db_path: str = 'chronicle.db',
                 status_callback: Optional[callable] = None,
                 live_transcription_ui_callback: Optional[Callable[[Dict[str, Any]], None]] = None):
        """Initialize SessionManager.
        
        Args:
            base_path: Base directory for session data
            db_path: Path to SQLite database
            status_callback: Optional callable for status updates
            live_transcription_ui_callback: Optional callback for live transcription results
        """
        self.base_path = Path(base_path)
        self.base_path.mkdir(parents=True, exist_ok=True)
        self.status_callback = status_callback
        self.live_transcription_ui_callback = live_transcription_ui_callback
        
        # Initialize database
        self.db = Database(db_path)
        self.db.connect()
        
        # Active session
        self.current_session: Optional[Session] = None
        self.current_timeline: Optional[Timeline] = None
        
        # Component factories (can be overridden for testing)
        self.dual_recorder_factory = DualSourceChunkedRecorder
        self.screenshot_capture_factory = ScreenshotCapture
        self.transcription_processor_factory = TranscriptionProcessor
        
        # VAD settings
        self.vad_threshold = 0.30  # 30% of frames must have speech
        self.vad_aggressiveness = 2  # VAD mode (0-3)

    def _update_status(self, message: str, is_error: bool = False):
        """Update status via callback and log."""
        if is_error:
            logger.error(message)
        else:
            logger.info(message)
        
        if self.status_callback:
            self.status_callback(message, is_error)
    
    def handle_live_transcription(self, source_or_chunk, chunk=None) -> None:
        """Handle live transcription of an audio chunk.
        
        This method is called by the audio recorder's live_transcription_callback.
        It transcribes the audio chunk, saves it to the database, and passes 
        the result to the UI callback.
        
        Args:
            source_or_chunk: Either the audio source string ('mic'/'system') or AudioChunk object
            chunk: AudioChunk object (if first arg is source string)
        """
        try:
            # Handle both calling conventions:
            # - DualSourceChunkedRecorder passes (source, chunk)
            # - ChunkedAudioRecorder passes just (chunk,)
            if chunk is not None:
                # Called as (source, chunk)
                source = source_or_chunk
                audio_chunk = chunk
            else:
                # Called as (chunk,) - extract source from chunk
                audio_chunk = source_or_chunk
                source = audio_chunk.source
            
            # Import here to avoid circular imports
            from ..transcription.live import LiveTranscriber
            
            # Get or create live transcriber for this session
            if not hasattr(self, '_live_transcriber'):
                self._live_transcriber = LiveTranscriber()
            
            # Transcribe the chunk
            result = self._live_transcriber.transcribe_chunk(audio_chunk)
            
            if result:
                # Add source to result
                result['source'] = source
                
                # Get session_id from current session
                session_id = None
                if self.current_session:
                    session_id = self.current_session.id
                
                # Save to database if we have a session - use thread-safe approach
                if session_id:
                    try:
                        # Convert timestamp string to datetime
                        timestamp_str = audio_chunk.timestamp_start
                        if isinstance(timestamp_str, str):
                            from datetime import datetime
                            timestamp = datetime.fromisoformat(timestamp_str)
                        else:
                            timestamp = timestamp_str
                        
                        # Determine source for database (map 'mic' to 'microphone')
                        db_source = 'microphone' if source == 'mic' else 'system'
                        
                        # Create a new database connection in this thread (SQLite requirement)
                        from ..storage.database import Database
                        thread_db = Database(self.db.db_path)
                        thread_db.connect()
                        
                        # Save transcript to database
                        thread_db.add_transcript(
                            session_id=session_id,
                            timestamp=timestamp,
                            text=result.get('text', ''),
                            source=db_source
                        )
                        thread_db.disconnect()
                        logger.info(f"Saved live transcript for session {session_id}")
                    except Exception as e:
                        logger.error(f"Failed to save live transcript: {e}")
                
                # Print to console for debugging (per validation requirements)
                print(f"[LIVE TRANSCRIPTION] {source}: {result.get('text', '')}")
                
                # Pass result to UI callback if provided
                if self.live_transcription_ui_callback:
                    self.live_transcription_ui_callback(result)
            else:
                logger.debug(f"No transcription result for chunk {chunk.chunk_id}")
                
        except Exception as e:
            logger.error(f"Error in live transcription: {e}")
    
    def _get_session_path(self, session_id: int) -> Path:
        """Get path for a session directory.
        
        Args:
            session_id: Database session ID
            
        Returns:
            Path to session directory
        """
        return self.base_path / f'session_{session_id:03d}'
    
    def create_session(self, name: str, enable_live_transcription: bool = True) -> Session:
        """Create a new session.
        
        Args:
            name: Session name/title
            enable_live_transcription: Whether to enable live transcription
            
        Returns:
            Created Session instance
        """
        now = datetime.now()
        
        # Create session in database
        session_id = self.db.create_session(name, now, status='active')
        
        # Get session path
        session_path = self._get_session_path(session_id)
        
        # Create session object
        session = Session(
            session_id=session_id,
            name=name,
            session_path=str(session_path),
            db=self.db
        )
        
        # Initialize components
        live_callback = self.handle_live_transcription if enable_live_transcription else None
        session.dual_recorder = self.dual_recorder_factory(
            str(session_path),
            vad_aggressiveness=self.vad_aggressiveness,
            vad_threshold=self.vad_threshold,
            live_transcription_callback=live_callback
        )
        session.screenshot_capture = self.screenshot_capture_factory(
            str(session_path), 
            db=self.db
        )
        session.transcription_processor = self.transcription_processor_factory(
            str(session_path),
            db=self.db
        )
        
        # Create timeline
        self.current_timeline = Timeline(session_id, now)
        
        self._update_status(f'Created session {session_id}: {name}')
        return session
    
    def load_session(self, session_id: int) -> Session:
        """Load an existing session from database.
        
        Args:
            session_id: Database session ID
            
        Returns:
            Loaded Session instance
        """
        # Get session from database
        db_session = self.db.get_session(session_id)
        
        session_path = self._get_session_path(session_id)
        
        # Create session object
        session = Session(
            session_id=session_id,
            name=db_session['name'],
            session_path=str(session_path),
            db=self.db
        )
        
        # Restore timestamps
        session.start_time = datetime.fromtimestamp(db_session['start_time'])
        session.status = db_session['status']
        
        if db_session.get('end_time'):
            session.end_time = datetime.fromtimestamp(db_session['end_time'])
        
        # Initialize components
        session.dual_recorder = self.dual_recorder_factory(
            str(session_path),
            vad_aggressiveness=self.vad_aggressiveness,
            vad_threshold=self.vad_threshold,
            live_transcription_callback=self.handle_live_transcription
        )
        session.screenshot_capture = self.screenshot_capture_factory(
            str(session_path),
            db=self.db
        )
        session.transcription_processor = self.transcription_processor_factory(
            str(session_path),
            db=self.db
        )
        
        # Create timeline with session start time
        self.current_timeline = Timeline(session_id, session.start_time)
        
        self._update_status(f'Loaded session {session_id}: {session.name}')
        return session
    
    def start_session(self, name: str, auto_record: bool = True, enable_live_transcription: bool = True) -> Session:
        """Create and start a new session.
        
        Args:
            name: Session name/title
            auto_record: Whether to automatically start recording
            enable_live_transcription: Whether to enable live transcription
            
        Returns:
            Started Session instance
        """
        session = self.create_session(name, enable_live_transcription=enable_live_transcription)
        session.start()
        self.current_session = session
        
        # Auto-start recording if enabled
        if auto_record:
            self.start_recording(label='main')
        
        self._update_status(f'Started session {session.id}: {session.name}')
        return session
    
    def pause_session(self) -> bool:
        """Pause the current session.
        
        Pauses audio recording while keeping the session active.
        All data continues to be associated with the same session_id.
        
        Returns:
            True if paused successfully, False if no active session
        """
        if not self.current_session:
            self._update_status('No active session to pause', is_error=True)
            return False
        
        if self.current_session.status != Session.STATUS_ACTIVE:
            self._update_status(f'Session is not active (status: {self.current_session.status})', is_error=True)
            return False
        
        # Stop recording (suspends audio capture)
        if self.current_session.dual_recorder and self.current_session.dual_recorder.is_running:
            self.stop_recording(label='main')
        
        # Update session status to paused
        self.current_session.pause()
        
        self._update_status(f'Session {self.current_session.id} paused')
        return True
    
    def resume_session(self) -> bool:
        """Resume a paused session.
        
        Resumes audio recording into the same session folder.
        No new session is created.
        
        Returns:
            True if resumed successfully, False if no paused session
        """
        if not self.current_session:
            self._update_status('No active session to resume', is_error=True)
            return False
        
        if self.current_session.status != Session.STATUS_PAUSED:
            self._update_status(f'Session is not paused (status: {self.current_session.status})', is_error=True)
            return False
        
        # Update session status to active
        self.current_session.resume()
        
        # Resume recording
        self.start_recording(label='main')
        
        self._update_status(f'Session {self.current_session.id} resumed')
        return True
    
    def stop_session(self, auto_transcribe: bool = True) -> Optional[Session]:
        """Stop the current session.
        
        Args:
            auto_transcribe: Whether to automatically process transcriptions
            
        Returns:
            The stopped session, or None if no active session
        """
        if not self.current_session:
            self._update_status('No active session to stop', is_error=True)
            return None
        
        self._update_status('Stopping session...')
        # Stop recording first
        self.stop_recording(label='main')
        
        self.current_session.stop()
        
        # Update timeline
        if self.current_timeline:
            self.current_timeline.set_session_end(datetime.now())
        
        session = self.current_session
        self.current_session = None
        
        # Auto-process transcriptions if enabled
        if auto_transcribe:
            try:
                self._update_status('Processing transcriptions...')
                session.process_transcriptions()
                self._update_status('Transcription processing finished.')
            except Exception as e:
                self._update_status(f'Auto transcription failed: {str(e)}', is_error=True)
        else:
            # Even without auto-transcribe, check if live transcription already created transcripts
            # and update the status accordingly
            if self.db.get_transcripts(session.id):
                self.db.update_session(session.id, transcription_status='transcribed')
                self._update_status(f'Transcription status updated for session {session.id}')
                
                # Index the session content for RAG search
                try:
                    index_session_content(self.db, session.id)
                    self._update_status(f'RAG indexing completed for session {session.id}')
                except Exception as e:
                    self._update_status(f'RAG indexing failed for session {session.id}: {str(e)}', is_error=True)
        
        # Auto-generate summary if enabled (after transcriptions are processed)
        if SESSION.get('auto_summary_after_stop', False):
            try:
                self._auto_generate_summary(session)
            except Exception as e:
                self._update_status(f'Auto summary failed: {str(e)}', is_error=True)
        
        self._update_status(f'Stopped session {session.id}: {session.name}')
        return session
    
    def _auto_generate_summary(self, session: Session) -> None:
        """Automatically generate a summary for a session if conditions are met.
        
        Args:
            session: The session to generate a summary for
        """
        session_id = session.id
        
        # Check if summary already exists
        existing_summaries = session.db.get_summaries(session_id)
        if existing_summaries:
            self._update_status(f'Summary already exists for session {session_id}, skipping auto-summary')
            return
        
        # Get transcripts from database
        transcripts = session.db.get_transcripts(session_id)
        
        if not transcripts:
            self._update_status(f'No transcripts found for session {session_id}, cannot auto-summarize')
            return
        
        # Combine all transcript text
        full_transcript = ' '.join(
            t.get('text', '') for t in transcripts if t.get('text')
        )
        
        if not full_transcript.strip():
            self._update_status(f'No transcript text found for session {session_id}, cannot auto-summarize')
            return
        
        self._update_status(f'Auto-generating summary for session {session_id}...')
        
        # Create summary generator and generate summary
        summary_gen = SummaryGenerator(db=session.db)
        summary_gen.generate_and_store(
            transcript=full_transcript,
            session_id=session_id,
            summary_type='full'
        )
        
        # Update summary status in database
        session.db.update_session(session_id, summary_status='summarized')
        
        self._update_status(f'Auto-summary completed for session {session_id}')
        
        # Index the session content for RAG search (will include the new summary)
        try:
            index_session_content(session.db, session_id)
            self._update_status(f'RAG indexing completed for session {session_id}')
        except Exception as e:
            self._update_status(f'RAG indexing failed for session {session_id}: {str(e)}', is_error=True)
    
    def get_active_session(self) -> Optional[Session]:
        """Get the currently active session.
        
        Returns:
            Active session or None
        """
        return self.current_session
    
    def set_active_session(self, session: Session) -> None:
        """Set the active session.
        
        Args:
            session: Session to make active
        """
        self.current_session = session
        
        # Create timeline if not exists
        if not self.current_timeline:
            start_time = session.start_time or datetime.now()
            self.current_timeline = Timeline(session.id, start_time)
    
    def start_recording(self, label: str = 'recording') -> str:
        """Start recording audio in the current session.
        
        Args:
            label: Recording label
            
        Returns:
            Path to audio directory
        """
        if not self.current_session:
            raise RuntimeError('No active session')
        
        # Track in timeline
        if self.current_timeline:
            self.current_timeline.add_audio_start(label)
        
        self._update_status('Recording started...')
        return self.current_session.start_recording(label)
    
    def stop_recording(self, label: str = 'recording') -> Optional[str]:
        """Stop recording audio in the current session.
        
        Args:
            label: Recording label
            
        Returns:
            Path to saved audio file
        """
        if not self.current_session:
            return None
        
        self._update_status('Recording stopped.')
        
        output_path = self.current_session.stop_recording(label)
        
        # Track in timeline
        if self.current_timeline and output_path:
            self.current_timeline.add_audio_end(output_path, label)
        
        return output_path
    
    def capture_screenshot(self, label: str = 'screenshot') -> str:
        """Capture a screenshot in the current session.
        
        Args:
            label: Screenshot label
            
        Returns:
            Path to saved screenshot
        """
        if not self.current_session:
            raise RuntimeError('No active session')
        
        output_path = self.current_session.capture_screenshot(label)
        
        # Track in timeline
        if self.current_timeline:
            self.current_timeline.add_screenshot(output_path, label)
        
        return output_path

    def capture_interactive_region(self, label: str = 'region') -> Optional[str]:
        """Capture an interactive region screenshot in the current session.

        Args:
            label: Screenshot label

        Returns:
            Path to saved screenshot, or None if cancelled
        """
        if not self.current_session:
            raise RuntimeError('No active session')

        # Obtener el nombre de la sesión para el nombre del archivo
        session_name = self.current_session.name

        output_path = self.current_session.screenshot_capture.capture_interactive_region(
            session_name=session_name,
            session_id=self.current_session.id
        )

        # Track in timeline
        if output_path and self.current_timeline:
            self.current_timeline.add_screenshot(output_path, label)

        return output_path

    def process_transcriptions(self, session=None) -> Dict[str, List[Dict[str, Any]]]:
        """Process transcriptions for a session.
        
        Args:
            session: Session to process transcriptions for. If None, uses current_session.
            
        Returns:
            Dictionary with transcription results
        """
        if not session:
            session = self.current_session
        
        if not session:
            raise RuntimeError('No active session')
        
        result = session.process_transcriptions()
        
        # Update transcription status in database
        # Check if there are any transcripts in the database (from live transcription or batch processing)
        if self.db.get_transcripts(session.id):
            self.db.update_session(session.id, transcription_status='transcribed')
            self._update_status(f'Transcription completed for session {session.id}')
            
            # Index the session content for RAG search
            try:
                index_session_content(self.db, session.id)
                self._update_status(f'RAG indexing completed for session {session.id}')
            except Exception as e:
                self._update_status(f'RAG indexing failed for session {session.id}: {str(e)}', is_error=True)
        
        return result
    
    def get_timeline(self) -> Optional[Timeline]:
        """Get the current timeline.
        
        Returns:
            Current timeline or None
        """
        return self.current_timeline
    
    def list_sessions(self) -> List[Dict[str, Any]]:
        """List all sessions from database.
        
        Returns:
            List of session dictionaries
        """
        return self.db.list_sessions()
    
    def get_session_summary(self, session_id: int) -> Optional[Dict[str, Any]]:
        """Get summary for a session.
        
        Args:
            session_id: Database session ID
            
        Returns:
            Session summary or None if not found
        """
        try:
            session = self.load_session(session_id)
            return session.get_summary()
        except Exception as e:
            self._update_status(f'Failed to get session summary: {str(e)}', is_error=True)
            return None
    
    def close(self) -> None:
        """Clean up resources."""
        if self.current_session and self.current_session.status == Session.STATUS_ACTIVE:
            self.stop_session()
        
        if self.db:
            self.db.disconnect()
        
        self._update_status('SessionManager closed')
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
