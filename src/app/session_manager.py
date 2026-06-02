"""Session manager for coordinating session lifecycle and components."""
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict, Any

from ..storage.database import Database
from ..audio_capture.core import DualSourceChunkedRecorder
from ..screenshots.capture import ScreenshotCapture
from ..transcription.processor import TranscriptionProcessor

from .session import Session
from .timeline import Timeline

logger = logging.getLogger(__name__)


class SessionManager:
    """Manages session lifecycle and coordinates all components.
    
    Handles session creation, component initialization, and coordinates
    the workflow between audio recording, screenshots, and transcription.
    """
    
    def __init__(self, 
                 base_path: str = 'sessions',
                 db_path: str = 'chronicle.db',
                 status_callback: Optional[callable] = None):
        """Initialize SessionManager.
        
        Args:
            base_path: Base directory for session data
            db_path: Path to SQLite database
            status_callback: Optional callable for status updates
        """
        self.base_path = Path(base_path)
        self.base_path.mkdir(parents=True, exist_ok=True)
        self.status_callback = status_callback
        
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
    
    def _get_session_path(self, session_id: int) -> Path:
        """Get path for a session directory.
        
        Args:
            session_id: Database session ID
            
        Returns:
            Path to session directory
        """
        return self.base_path / f'session_{session_id:03d}'
    
    def create_session(self, name: str) -> Session:
        """Create a new session.
        
        Args:
            name: Session name/title
            
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
        session.dual_recorder = self.dual_recorder_factory(
            str(session_path),
            vad_aggressiveness=self.vad_aggressiveness,
            vad_threshold=self.vad_threshold
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
            vad_threshold=self.vad_threshold
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
    
    def start_session(self, name: str, auto_record: bool = True) -> Session:
        """Create and start a new session.
        
        Args:
            name: Session name/title
            auto_record: Whether to automatically start recording
            
        Returns:
            Started Session instance
        """
        session = self.create_session(name)
        session.start()
        self.current_session = session
        
        # Auto-start recording if enabled
        if auto_record:
            self.start_recording(label='main')
        
        self._update_status(f'Started session {session.id}: {session.name}')
        return session
    
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
        
        self._update_status(f'Stopped session {session.id}: {session.name}')
        return session
    
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
        if result:
            self.db.update_session(session.id, transcription_status='transcribed')
            self._update_status(f'Transcription completed for session {session.id}')
        
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
