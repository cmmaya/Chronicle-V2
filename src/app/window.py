from PySide6.QtWidgets import (QMainWindow, QMenuBar, QWidget, QVBoxLayout, 
                               QHBoxLayout, QPushButton, QLabel, QStatusBar,
                               QMessageBox, QApplication, QListWidget, QGroupBox)
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction
import logging

from .session_manager import SessionManager
from ..summarization import SummaryGenerator

logger = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    """Main application window with session controls."""
    
    def __init__(self):
        super().__init__()
        self.setWindowTitle('Chronicle')
        self.resize(800, 600)
        
        # Get project root directory (parent of src/)
        import os
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        sessions_path = os.path.join(project_root, 'sessions')
        
        # Session manager
        self.session_manager: SessionManager = None
        
        # UI state
        self._is_recording = False
        
        # Create UI components
        self._create_menu_bar()
        self._create_central_widget()
        self._create_status_bar()
        
        # Initialize session manager
        self._init_session_manager(sessions_path)
        
    def _init_session_manager(self, sessions_path: str):
        """Initialize the session manager.
        
        Args:
            sessions_path: Path to sessions directory
        """
        try:
            self.session_manager = SessionManager(
                base_path=sessions_path,
                db_path='chronicle.db',
                status_callback=self._on_status_update
            )
            self._update_ui_state()
            self._load_past_sessions()
            self.status_label.setText('Ready')
        except Exception as e:
            self._on_status_update(f'Failed to initialize: {str(e)}', is_error=True)
            QMessageBox.critical(self, 'Error', f'Failed to initialize: {str(e)}')
    
    def _load_past_sessions(self):
        """Load and display past sessions from the database."""
        try:
            sessions = self.session_manager.db.list_sessions()
            self.sessions_list.clear()
            for session in sessions:
                # Format: Session name - Date/Time
                from datetime import datetime
                start_time = datetime.fromtimestamp(session['start_time'])
                display_text = f"{session['name']} - {start_time.strftime('%Y-%m-%d %H:%M')}"
                self.sessions_list.addItem(display_text)
        except Exception as e:
            logger.warning(f"Failed to load past sessions: {str(e)}")
    
    def _create_menu_bar(self):
        """Create the application menu bar."""
        menu_bar = QMenuBar(self)
        file_menu = menu_bar.addMenu('File')
        
        # Exit action
        exit_action = QAction('Exit', self)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)
        
        session_menu = menu_bar.addMenu('Session')
        
        # Start session action
        start_action = QAction('Start Session', self)
        start_action.triggered.connect(self._on_start_session)
        session_menu.addAction(start_action)
        
        # Stop session action
        stop_action = QAction('Stop Session', self)
        stop_action.triggered.connect(self._on_stop_session)
        session_menu.addAction(stop_action)
        
        help_menu = menu_bar.addMenu('Help')
        
        # About action
        about_action = QAction('About', self)
        about_action.triggered.connect(self._show_about)
        help_menu.addAction(about_action)
        
        self.setMenuBar(menu_bar)
    
    def _create_central_widget(self):
        """Create the central widget with session controls."""
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        # Main layout
        layout = QVBoxLayout(central_widget)
        layout.setSpacing(20)
        layout.setContentsMargins(40, 40, 40, 40)
        
        # Title
        title_label = QLabel('Chronicle')
        title_label.setAlignment(Qt.AlignCenter)
        title_font = title_label.font()
        title_font.setPointSize(24)
        title_font.setBold(True)
        title_label.setFont(title_font)
        layout.addWidget(title_label)
        
        # Session name input area
        session_layout = QHBoxLayout()
        session_layout.addStretch()
        self.session_name_label = QLabel('Session Name:')
        self.session_name_label.setFont(title_font)
        self.session_name_input = QLabel('New Session')
        self.session_name_input.setFont(title_font)
        session_layout.addWidget(self.session_name_label)
        session_layout.addWidget(self.session_name_input)
        session_layout.addStretch()
        layout.addLayout(session_layout)
        
        # Spacer
        layout.addStretch()
        
        # Control buttons
        button_layout = QHBoxLayout()
        button_layout.setSpacing(20)
        
        self.start_button = QPushButton('Start Session')
        self.start_button.setMinimumSize(150, 50)
        self.start_button.setFont(title_font)
        self.start_button.clicked.connect(self._on_start_session)
        
        self.stop_button = QPushButton('Stop Session')
        self.stop_button.setMinimumSize(150, 50)
        self.stop_button.setFont(title_font)
        self.stop_button.clicked.connect(self._on_stop_session)
        self.stop_button.setEnabled(False)
        
        button_layout.addStretch()
        button_layout.addWidget(self.start_button)
        button_layout.addWidget(self.stop_button)
        button_layout.addStretch()
        
        layout.addLayout(button_layout)
        
        # Spacer
        layout.addStretch()
        
        # Status display
        self.status_label = QLabel('Ready')
        self.status_label.setAlignment(Qt.AlignCenter)
        status_font = self.status_label.font()
        status_font.setPointSize(16)
        self.status_label.setFont(status_font)
        layout.addWidget(self.status_label)
        
        # Past Sessions list
        sessions_group = QGroupBox('Past Sessions')
        sessions_layout = QVBoxLayout()
        self.sessions_list = QListWidget()
        sessions_layout.addWidget(self.sessions_list)
        sessions_group.setLayout(sessions_layout)
        layout.addWidget(sessions_group)
        
        # Spacer at bottom
        layout.addStretch()
    
    def _create_status_bar(self):
        """Create the status bar."""
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage('Ready')

    def _on_status_update(self, message: str, is_error: bool = False):
        """Handle status updates from the session manager."""
        print(f"[DEBUG] Status update: {message}")
        self.status_label.setText(message)
        self.status_bar.showMessage(message)
        
        if is_error:
            self.status_label.setStyleSheet("color: red;")
        else:
            self.status_label.setStyleSheet("")

    def _update_ui_state(self):
        """Update UI based on current session state."""
        if self.session_manager is None:
            return
            
        active_session = self.session_manager.get_active_session()
        
        if active_session and active_session.status == 'active':
            self.start_button.setEnabled(False)
            self.stop_button.setEnabled(True)
            self._is_recording = True
            self.session_name_input.setText(active_session.name)
        elif active_session and active_session.status == 'processing':
            self.start_button.setEnabled(False)
            self.stop_button.setEnabled(False)
        else:
            self.start_button.setEnabled(True)
            self.stop_button.setEnabled(False)
            self._is_recording = False
    
    def _on_start_session(self):
        """Handle start session button click."""
        try:
            # Generate session name with timestamp
            from datetime import datetime
            now = datetime.now()
            session_name = f"Session {now.strftime('%Y-%m-%d %H:%M')}"
            
            # Start session via manager (auto-starts recording)
            self.session_manager.start_session(session_name, auto_record=True)
            
            # Update UI
            self._update_ui_state()
            
        except Exception as e:
            self._on_status_update(f'Failed to start session: {str(e)}', is_error=True)
            QMessageBox.critical(self, 'Error', f'Failed to start session: {str(e)}')
    
    def _on_stop_session(self):
        """Handle stop session button click."""
        try:
            # Stop session without auto-transcribing (we'll do it manually to show status)
            session = self.session_manager.stop_session(auto_transcribe=False)
            
            # Update UI
            self.start_button.setEnabled(False)
            self.stop_button.setEnabled(False)
            
            # Process transcriptions in background
            if session:
                # Run transcription in a timer to allow UI to update
                QTimer.singleShot(100, lambda: self._process_transcription(session))
            
        except Exception as e:
            self._on_status_update(f'Failed to stop session: {str(e)}', is_error=True)
            QMessageBox.critical(self, 'Error', f'Failed to stop session: {str(e)}')
            self._update_ui_state()
    
    def _process_transcription(self, session):
        """Process transcriptions after session stops."""
        print(f"[DEBUG] _process_transcription called with session: {session}")
        try:
            # Show status before processing
            self._on_status_update('Processing transcriptions...')
            QApplication.processEvents()
            print("[DEBUG] Set status to Processing transcriptions...")
            
            results = self.session_manager.process_transcriptions(session)
            
            # Update UI
            self._update_ui_state()
            
            # Generate summary
            self._on_status_update('Generating summary...')
            QApplication.processEvents()
            
            try:
                # Get transcripts from database
                # Ensure db is connected
                transcripts = []
                if session.db is None:
                    logger.error("session.db is None")
                elif session.db.connection is None:
                    logger.info("Database not connected, connecting...")
                    session.db.connect()
                    transcripts = session.db.get_transcripts(session.id)
                else:
                    transcripts = session.db.get_transcripts(session.id)
                
                if transcripts:
                    # Combine all transcript text
                    full_transcript = ' '.join(
                        t.get('text', '') for t in transcripts if t.get('text')
                    )
                    
                    if full_transcript.strip():
                        # Create summary generator (reads API key from .env)
                        summary_gen = SummaryGenerator(db=session.db)
                        
                        # Generate and store summary
                        summary_result = summary_gen.generate_and_store(
                            transcript=full_transcript,
                            session_id=session.id,
                            summary_type='full'
                        )
                        
                        summary_content = summary_result.get('content', '')
                        logger.info(f"Summary generated for session {session.id}")
                    else:
                        summary_content = None
                        logger.warning(f"No transcript text found for session {session.id}")
                else:
                    summary_content = None
                    logger.warning(f"No transcripts found for session {session.id}")
                    
            except Exception as e:
                logger.error(f"Summary generation failed: {str(e)}")
                import traceback
                logger.error(traceback.format_exc())
                summary_content = None
            
            # Update status to show session finished
            self._on_status_update('Session complete')
            
            # Show completion message
            mic_count = len(results.get('microphone', []))
            sys_count = len(results.get('system', []))
            
            msg = f'Session saved successfully.\n\n'
            msg += f'Microphone transcriptions: {mic_count}\n'
            msg += f'System audio transcriptions: {sys_count}\n'
            
            if summary_content:
                msg += f'\nSummary: Generated'
            else:
                msg += f'\nSummary: Not available'
            
            QMessageBox.information(
                self, 
                'Session Complete',
                msg
            )
            
        except Exception as e:
            QMessageBox.warning(self, 'Warning', f'Transcription failed: {str(e)}')
            self._update_ui_state()
    
    def _show_about(self):
        """Show the about dialog."""
        QMessageBox.about(
            self,
            'About Chronicle',
            'Chronicle\n\n'
            'Meeting capture and transcription tool.\n\n'
            'Captures audio, screenshots, and generates summaries.'
        )
    
    def closeEvent(self, event):
        """Handle window close event."""
        # Check if there's an active session
        if self.session_manager and self.session_manager.get_active_session():
            reply = QMessageBox.question(
                self,
                'Active Session',
                'There is an active session. Stop and close?',
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )
            
            if reply == QMessageBox.Yes:
                try:
                    # Stop recording if active
                    self.session_manager.stop_recording(label='main')
                    # Stop session
                    self.session_manager.stop_session()
                except Exception:
                    pass  # Ignore errors during shutdown
                event.accept()
            else:
                event.ignore()
                return
        
        # Clean up session manager
        if self.session_manager:
            try:
                self.session_manager.close()
            except Exception:
                pass
        
        event.accept()
