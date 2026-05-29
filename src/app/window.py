from PySide6.QtWidgets import (QMainWindow, QMenuBar, QWidget, QVBoxLayout, 
                                QHBoxLayout, QPushButton, QLabel, QStatusBar,
                                QMessageBox, QApplication, QListWidget, QGroupBox,
                                QListWidgetItem, QMenu, QTableWidget, QTableWidgetItem,
                                QHeaderView, QComboBox, QDialog, QTextBrowser, QScrollArea, QGridLayout)
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QPixmap
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
            
            # Disconnect signal to prevent triggering during programmatic updates
            self.sessions_list.cellChanged.disconnect()
            
            self.sessions_list.setRowCount(len(sessions))
            
            for row, session in enumerate(sessions):
                trans_status = session.get('transcription_status', 'none')
                sum_status = session.get('summary_status', 'none')
                
                # Session name (editable)
                name_item = QTableWidgetItem(session['name'])
                name_item.setData(Qt.UserRole, session['id'])
                name_item.setFlags(name_item.flags() | Qt.ItemIsEditable)
                self.sessions_list.setItem(row, 0, name_item)
                
                # Transcription status (read-only)
                trans_item = QTableWidgetItem(trans_status)
                trans_item.setFlags(trans_item.flags() & ~Qt.ItemIsEditable)
                self.sessions_list.setItem(row, 1, trans_item)
                
                # Summary status (read-only)
                sum_item = QTableWidgetItem(sum_status)
                sum_item.setFlags(sum_item.flags() & ~Qt.ItemIsEditable)
                self.sessions_list.setItem(row, 2, sum_item)
                
                # Actions column with dropdown (only enabled when transcription_status is None or 'none')
                action_combo = QComboBox()
                action_combo.addItem("Select Action", "none")
                
                # Add transcribe option only if transcription status is None or 'none'
                if trans_status in (None, 'none'):
                    action_combo.addItem("Transcribe", "transcribe")
                
                # Add summarize option only if transcription is complete and summary is None or 'none'
                if trans_status == 'transcribed' and sum_status in (None, 'none'):
                    action_combo.addItem("Summarize", "summarize")
                
                # Set current index based on status
                if trans_status == 'transcribed' and sum_status == 'summarized':
                    action_combo.setCurrentIndex(1)  # Select "Done" or adjust
                    action_combo.setEnabled(False)
                elif trans_status == 'transcribed' and sum_status in (None, 'none'):
                    # Has transcribe option at index 1, summarize at index 2
                    action_combo.setCurrentIndex(0)
                else:
                    action_combo.setCurrentIndex(0)
                
                action_combo.setProperty('session_id', session['id'])
                action_combo.currentIndexChanged.connect(lambda index, sid=session['id'], combo=action_combo: self._on_action_selected(sid, index, combo))
                self.sessions_list.setCellWidget(row, 3, action_combo)
            
            # Reconnect signal after loading
            self.sessions_list.cellChanged.connect(self._on_session_name_changed)
            
        except Exception as e:
            logger.warning(f"Failed to load past sessions: {str(e)}")
    
    def _on_session_name_changed(self, row, column):
        """Handle the renaming of a session."""
        if column != 0:
            return  # Only allow editing the name column
        
        try:
            item = self.sessions_list.item(row, 0)
            session_id = item.data(Qt.UserRole)
            new_name = item.text()
            
            if session_id is None:
                return
            
            # Update the database
            self.session_manager.db.update_session(session_id, name=new_name)
            
            logger.info(f"Renamed session {session_id} to '{new_name}'")
            self._on_status_update(f"Session renamed to '{new_name}'")
            
        except Exception as e:
            logger.error(f"Failed to rename session: {str(e)}")
            self._on_status_update(f"Error renaming session.", is_error=True)
    
    def _on_action_selected(self, session_id: int, index: int, combo: QComboBox):
        """Handle the action selection from the dropdown."""
        if index == 0:  # "Select Action" - do nothing
            return
        
        action = combo.currentData()
        
        if action == "transcribe":
            # Disable the combo to prevent multiple clicks
            combo.setEnabled(False)
            self._on_transcribe_clicked(session_id, combo)
        elif action == "summarize":
            # Disable the combo to prevent multiple clicks
            combo.setEnabled(False)
            self._on_summarize_clicked(session_id, combo)
    
    def _on_transcribe_clicked(self, session_id: int, combo: QComboBox):
        """Handle the transcribe action for a session."""
        try:
            # Update status
            self._on_status_update(f'Transcribing session {session_id}...')
            
            # Run transcription in background using timer to allow UI to update
            QTimer.singleShot(50, lambda: self._run_transcription(session_id, combo))
            
        except Exception as e:
            logger.error(f"Failed to start transcription: {str(e)}")
            self._on_status_update(f"Error: {str(e)}", is_error=True)
            # Reload to reset state
            self._load_past_sessions()
    
    def _run_transcription(self, session_id: int, combo: QComboBox):
        """Run the transcription process for a session."""
        try:
            self._on_status_update('Loading session...')
            
            # Load the session using session manager
            session = self.session_manager.load_session(session_id)
            
            self._on_status_update('Processing transcriptions...')
            QApplication.processEvents()
            
            # Process transcriptions
            results = session.process_transcriptions()
            
            # Update transcription status in database
            if results:
                self.session_manager.db.update_session(session_id, transcription_status='transcribed')
                mic_count = len(results.get('microphone', []))
                sys_count = len(results.get('system', []))
                self._on_status_update(f'Transcribed {mic_count} mic chunks, {sys_count} system chunks')
            else:
                self._on_status_update('No audio files found to transcribe')
            
            # Reload the sessions list to update UI
            self._load_past_sessions()
            
        except Exception as e:
            logger.error(f"Transcription failed: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            self._on_status_update(f"Transcription failed: {str(e)}", is_error=True)
            QMessageBox.warning(self, 'Transcription Failed', str(e))
            # Reload to reset button state
            self._load_past_sessions()
    
    def _on_summarize_clicked(self, session_id: int, combo: QComboBox):
        """Handle the summarize action for a session."""
        try:
            # Update status
            self._on_status_update(f'Summarizing session {session_id}...')
            
            # Run summarization in background using timer to allow UI to update
            QTimer.singleShot(50, lambda: self._run_summarization(session_id, combo))
            
        except Exception as e:
            logger.error(f"Failed to start summarization: {str(e)}")
            self._on_status_update(f"Error: {str(e)}", is_error=True)
            # Reload to reset state
            self._load_past_sessions()
    
    def _run_summarization(self, session_id: int, combo: QComboBox):
        """Run the summarization process for a session."""
        try:
            self._on_status_update('Loading session...')
            
            # Load the session using session manager
            session = self.session_manager.load_session(session_id)
            
            self._on_status_update('Generating summary...')
            QApplication.processEvents()
            
            # Get transcripts from database
            transcripts = self.session_manager.db.get_transcripts(session_id)
            
            if not transcripts:
                self._on_status_update('No transcripts found for summarization')
                QMessageBox.warning(self, 'No Transcripts', 'No transcripts available. Please transcribe first.')
                self._load_past_sessions()
                return
            
            # Combine all transcript text
            full_transcript = ' '.join(
                t.get('text', '') for t in transcripts if t.get('text')
            )
            
            if not full_transcript.strip():
                self._on_status_update('No transcript text found')
                QMessageBox.warning(self, 'No Transcript Text', 'Transcripts are empty.')
                self._load_past_sessions()
                return
            
            # Create summary generator (reads API key from .env)
            summary_gen = SummaryGenerator(db=self.session_manager.db)
            
            # Generate and store summary
            summary_result = summary_gen.generate_and_store(
                transcript=full_transcript,
                session_id=session_id,
                summary_type='full'
            )
            
            # Update summary status in database
            self.session_manager.db.update_session(session_id, summary_status='summarized')
            
            self._on_status_update('Summary generated successfully')
            
            # Reload the sessions list to update UI
            self._load_past_sessions()
            
        except Exception as e:
            logger.error(f"Summarization failed: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            self._on_status_update(f"Summarization failed: {str(e)}", is_error=True)
            QMessageBox.warning(self, 'Summarization Failed', str(e))
            # Reload to reset button state
            self._load_past_sessions()
    
    def _show_session_context_menu(self, position):
        """Show a context menu for the right-clicked session item."""
        # Get row from position
        row = self.sessions_list.row(self.sessions_list.itemAt(position))
        
        if row < 0:
            return
        
        menu = QMenu()
        view_summary_action = menu.addAction("View Summary")
        view_screenshots_action = menu.addAction("View Screenshots")
        menu.addSeparator()
        delete_action = menu.addAction("Delete Session")
        
        chosen_action = menu.exec(self.sessions_list.mapToGlobal(position))
        
        if chosen_action == view_summary_action:
            self._show_session_summary(row)
        elif chosen_action == view_screenshots_action:
            self._show_session_screenshots(row)
        elif chosen_action == delete_action:
            self._delete_session(row)
    
    def _delete_session(self, row):
        """Delete the selected session after confirmation."""
        try:
            session_id = self.sessions_list.item(row, 0).data(Qt.UserRole)
            session_name = self.sessions_list.item(row, 0).text()
            
            if session_id is None:
                return
            
            reply = QMessageBox.question(
                self,
                'Delete Session',
                f"Are you sure you want to permanently delete '{session_name}'?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )
            
            if reply == QMessageBox.Yes:
                # Delete related data first (summaries, transcripts)
                self.session_manager.db.delete_summaries(session_id)
                self.session_manager.db.delete_transcripts(session_id)
                # Delete the session itself
                self.session_manager.db.delete_session(session_id)
                self.sessions_list.removeRow(row)
                
                logger.info(f"Deleted session {session_id} ('{session_name}')")
                self._on_status_update(f"Session '{session_name}' deleted.")
                
        except Exception as e:
            logger.error(f"Failed to delete session: {str(e)}")
            self._on_status_update(f"Error deleting session.", is_error=True)
            QMessageBox.critical(self, 'Error', 'Could not delete the session from the database.')
    
    def _on_session_double_clicked(self, row, column):
        """Handle double-click on a session row to view summary."""
        self._show_session_summary(row)
    
    def _show_session_summary(self, row):
        """Show the summary for a session in a separate window."""
        try:
            session_id = self.sessions_list.item(row, 0).data(Qt.UserRole)
            session_name = self.sessions_list.item(row, 0).text()
            summary_status = self.sessions_list.item(row, 2).text()
            
            if session_id is None:
                return
            
            # Check if session has a summary
            if summary_status != 'summarized':
                QMessageBox.information(
                    self,
                    'No Summary',
                    f"Session '{session_name}' does not have a summary yet.\n\n"
                    "Please transcribe and summarize the session first."
                )
                return
            
            # Fetch summary from database
            summaries = self.session_manager.db.get_summaries(session_id)
            
            if not summaries:
                QMessageBox.warning(
                    self,
                    'No Summary',
                    f"No summary found for session '{session_name}'."
                )
                return
            
            # Get the first summary (or most recent)
            summary = summaries[0]
            summary_content = summary.get('content', '')
            summary_type = summary.get('summary_type', 'full')
            model_used = summary.get('model_used', 'unknown')
            
            # Create a dialog to display the summary
            dialog = QDialog(self)
            dialog.setWindowTitle(f"Summary - {session_name}")
            dialog.setMinimumSize(600, 400)
            
            layout = QVBoxLayout(dialog)
            
            # Header with session info
            header_label = QLabel(f"Session: {session_name}")
            header_font = header_label.font()
            header_font.setPointSize(14)
            header_font.setBold(True)
            header_label.setFont(header_font)
            layout.addWidget(header_label)
            
            # Summary type and model info
            info_label = QLabel(f"Type: {summary_type} | Model: {model_used}")
            info_label.setStyleSheet("color: gray;")
            layout.addWidget(info_label)
            
            # Summary content
            text_browser = QTextBrowser()
            text_browser.setPlainText(summary_content)
            text_browser.setOpenExternalLinks(True)
            layout.addWidget(text_browser)
            
            # Close button
            close_button = QPushButton("Close")
            close_button.clicked.connect(dialog.close)
            layout.addWidget(close_button)
            
            dialog.exec()
            
        except Exception as e:
            logger.error(f"Failed to show summary: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            QMessageBox.critical(
                self,
                'Error',
                f"Failed to load summary: {str(e)}"
            )
    
    def _show_full_image(self, filepath: str, timestamp: int):
        """Show a full-size image in a dialog."""
        try:
            from datetime import datetime
            from PySide6.QtWidgets import QApplication, QScrollArea, QSizePolicy
            
            # Create dialog with window controls
            dialog = QDialog(self)
            dialog.setWindowTitle("Screenshot")
            dialog.setWindowFlags(dialog.windowFlags() | Qt.WindowMinMaxButtonsHint)
            
            # Get screen size
            screen = QApplication.primaryScreen()
            screen_geometry = screen.availableGeometry()
            
            # Default to 50% of screen
            width = int(screen_geometry.width() * 0.5)
            height = int(screen_geometry.height() * 0.5)
            dialog.resize(width, height)
            
            # Center the window
            dialog.move(int((screen_geometry.width() - width) / 2), 
                       int((screen_geometry.height() - height) / 2))
            
            layout = QVBoxLayout(dialog)
            layout.setContentsMargins(5, 5, 5, 5)
            
            # Timestamp
            dt = datetime.fromtimestamp(timestamp)
            time_str = dt.strftime('%Y-%m-%d %H:%M:%S')
            time_label = QLabel(f"Captured at: {time_str}")
            time_label.setAlignment(Qt.AlignCenter)
            layout.addWidget(time_label)
            
            # Image with scroll area for zooming/panning
            scroll_area = QScrollArea()
            scroll_area.setWidgetResizable(True)
            scroll_area.setAlignment(Qt.AlignCenter)
            
            image_label = QLabel()
            image_label.setAlignment(Qt.AlignCenter)
            pixmap = QPixmap(filepath)
            
            if not pixmap.isNull():
                # Scale image to fit in the scroll area
                image_label.setPixmap(pixmap.scaled(
                    screen_geometry.width(), 
                    screen_geometry.height(), 
                    Qt.KeepAspectRatio, 
                    Qt.SmoothTransformation
                ))
            else:
                image_label.setText(f"Failed to load image\n{filepath}")
            
            scroll_area.setWidget(image_label)
            layout.addWidget(scroll_area)
            
            # Button layout for Close and Fullscreen
            button_layout = QHBoxLayout()
            button_layout.addStretch()
            
            # Fullscreen toggle button
            fullscreen_btn = QPushButton("Fullscreen")
            fullscreen_btn.clicked.connect(lambda: self._toggle_fullscreen(dialog, fullscreen_btn, image_label, pixmap, screen_geometry))
            button_layout.addWidget(fullscreen_btn)
            
            # Close button
            close_button = QPushButton("Close")
            close_button.clicked.connect(dialog.close)
            button_layout.addWidget(close_button)
            
            layout.addLayout(button_layout)
            
            dialog.exec()
            
        except Exception as e:
            logger.error(f"Failed to show full image: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            QMessageBox.critical(self, 'Error', f"Failed to show full image: {str(e)}")
    
    def _toggle_fullscreen(self, dialog: QDialog, btn: QPushButton, image_label: QLabel, pixmap: QPixmap, screen_geometry):
        """Toggle between fullscreen and normal mode."""
        if dialog.isFullScreen():
            dialog.showNormal()
            btn.setText("Fullscreen")
            # Scale to 50% when not fullscreen
            width = int(screen_geometry.width() * 0.5)
            height = int(screen_geometry.height() * 0.5)
            dialog.resize(width, height)
            dialog.move(int((screen_geometry.width() - width) / 2), 
                       int((screen_geometry.height() - height) / 2))
            # Scale image to fit in the window
            image_label.setPixmap(pixmap.scaled(
                screen_geometry.width(), 
                screen_geometry.height(), 
                Qt.KeepAspectRatio, 
                Qt.SmoothTransformation
            ))
        else:
            # Save current geometry for restore
            dialog.showFullScreen()
            btn.setText("Exit Fullscreen")
            # Scale image to fill the fullscreen
            image_label.setPixmap(pixmap.scaled(
                screen_geometry.width(), 
                screen_geometry.height(), 
                Qt.KeepAspectRatio, 
                Qt.SmoothTransformation
            ))
    
    def _show_session_screenshots(self, row):
        """Show the screenshots for a session in a separate window."""
        try:
            session_id = self.sessions_list.item(row, 0).data(Qt.UserRole)
            session_name = self.sessions_list.item(row, 0).text()
            
            if session_id is None:
                return
            
            # Fetch screenshots from database
            screenshots = self.session_manager.db.get_screenshots(session_id)
            
            if not screenshots:
                QMessageBox.information(
                    self,
                    'No Screenshots',
                    f"Session '{session_name}' does not have any screenshots yet."
                )
                return
            
            # Create a dialog to display the screenshots
            dialog = QDialog(self)
            dialog.setWindowTitle(f"Screenshots - {session_name}")
            dialog.setMinimumSize(800, 600)
            
            layout = QVBoxLayout(dialog)
            
            # Header with session info
            header_label = QLabel(f"Screenshots for: {session_name}")
            header_font = header_label.font()
            header_font.setPointSize(14)
            header_font.setBold(True)
            header_label.setFont(header_font)
            layout.addWidget(header_label)
            
            # Scroll area for screenshots
            scroll_area = QScrollArea()
            scroll_area.setWidgetResizable(True)
            
            # Grid layout for screenshots
            grid_widget = QWidget()
            grid_layout = QGridLayout(grid_widget)
            grid_layout.setSpacing(10)
            
            # Add screenshots to the grid
            from datetime import datetime
            for idx, screenshot in enumerate(screenshots):
                filepath = screenshot.get('filepath', '')
                timestamp = screenshot.get('timestamp', 0)
                
                # Convert timestamp to readable format
                dt = datetime.fromtimestamp(timestamp)
                time_str = dt.strftime('%H:%M:%S')
                
                # Create label with image
                image_label = QLabel()
                pixmap = QPixmap(filepath)
                
                if not pixmap.isNull():
                    # Scale to fit while maintaining aspect ratio
                    scaled_pixmap = pixmap.scaled(300, 200, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                    image_label.setPixmap(scaled_pixmap)
                else:
                    image_label.setText(f"Failed to load image\n{filepath}")
                
                image_label.setAlignment(Qt.AlignCenter)
                image_label.setCursor(Qt.PointingHandCursor)
                image_label.mousePressEvent = lambda event, fp=filepath, ts=timestamp: self._show_full_image(fp, ts)
                
                # Timestamp label
                time_label = QLabel(f"Captured at: {time_str}")
                time_label.setAlignment(Qt.AlignCenter)
                
                # Add to grid (2 columns)
                grid_layout.addWidget(image_label, idx // 2 * 2, idx % 2)
                grid_layout.addWidget(time_label, idx // 2 * 2 + 1, idx % 2)
            
            scroll_area.setWidget(grid_widget)
            layout.addWidget(scroll_area)
            
            # Close button
            close_button = QPushButton("Close")
            close_button.clicked.connect(dialog.close)
            layout.addWidget(close_button)
            
            dialog.exec()
            
        except Exception as e:
            logger.error(f"Failed to show screenshots: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            QMessageBox.critical(
                self,
                'Error',
                f"Failed to load screenshots: {str(e)}"
            )
    
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
        
        self.screenshot_button = QPushButton('Take Screenshot')
        self.screenshot_button.setMinimumSize(150, 50)
        self.screenshot_button.setFont(title_font)
        self.screenshot_button.clicked.connect(self._on_take_screenshot)
        self.screenshot_button.setEnabled(False)
        
        self.view_screenshots_button = QPushButton('View Screenshots')
        self.view_screenshots_button.setMinimumSize(150, 50)
        self.view_screenshots_button.setFont(title_font)
        self.view_screenshots_button.clicked.connect(self._on_view_screenshots)
        self.view_screenshots_button.setEnabled(False)
        
        button_layout.addStretch()
        button_layout.addWidget(self.start_button)
        button_layout.addWidget(self.stop_button)
        button_layout.addWidget(self.screenshot_button)
        button_layout.addWidget(self.view_screenshots_button)
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
        
        # Past Sessions list - using table for separate cells
        sessions_group = QGroupBox('Past Sessions')
        sessions_layout = QVBoxLayout()
        self.sessions_list = QTableWidget()
        self.sessions_list.setColumnCount(4)
        self.sessions_list.setHorizontalHeaderLabels(['Session Name', 'Transcription', 'Summary', 'Actions'])
        self.sessions_list.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.sessions_list.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.sessions_list.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.sessions_list.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.sessions_list.setSelectionBehavior(QTableWidget.SelectRows)
        self.sessions_list.setEditTriggers(QTableWidget.DoubleClicked | QTableWidget.EditKeyPressed)
        self.sessions_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.sessions_list.customContextMenuRequested.connect(self._show_session_context_menu)
        self.sessions_list.cellChanged.connect(self._on_session_name_changed)
        self.sessions_list.cellDoubleClicked.connect(self._on_session_double_clicked)
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
            self.screenshot_button.setEnabled(True)
            self.view_screenshots_button.setEnabled(True)
            self._is_recording = True
            self.session_name_input.setText(active_session.name)
        elif active_session and active_session.status == 'processing':
            self.start_button.setEnabled(False)
            self.stop_button.setEnabled(False)
            self.screenshot_button.setEnabled(False)
            self.view_screenshots_button.setEnabled(False)
        else:
            self.start_button.setEnabled(True)
            self.stop_button.setEnabled(False)
            self.screenshot_button.setEnabled(False)
            # Enable View Screenshots button if there are past sessions
            sessions = self.session_manager.db.list_sessions()
            self.view_screenshots_button.setEnabled(len(sessions) > 0)
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
            # Stop session without auto-transcribing (manual transcription only)
            session = self.session_manager.stop_session(auto_transcribe=False)
            
            # Update UI
            self.start_button.setEnabled(True)
            self.stop_button.setEnabled(False)
            self.screenshot_button.setEnabled(False)
            
            # Just show status - no automatic transcription/summarization
            if session:
                self._on_status_update(f"Session '{session.name}' saved. Use Transcribe button to process.")
            
            # Reload sessions to show the new session
            self._load_past_sessions()
            
        except Exception as e:
            self._on_status_update(f'Failed to stop session: {str(e)}', is_error=True)
            QMessageBox.critical(self, 'Error', f'Failed to stop session: {str(e)}')
            self._update_ui_state()
    
    def _on_take_screenshot(self):
        """Handle take screenshot button click."""
        try:
            # Minimizar la ventana antes de tomar la captura
            self.showMinimized()

            # Esperar a que la ventana se minimice completamente antes de mostrar el dialog
            # Usar QTimer.singleShot para dar tiempo al sistema de minimizar
            QTimer.singleShot(300, self._execute_screenshot_capture)

        except Exception as e:
            # Asegurar que la ventana se restaure en caso de error
            self.showNormal()
            self.activateWindow()
            self.raise_()

            logger.error(f"Failed to take screenshot: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            self._on_status_update(f'Failed to take screenshot: {str(e)}', is_error=True)
            QMessageBox.critical(self, 'Error', f'Failed to take screenshot: {str(e)}')

    def _execute_screenshot_capture(self):
        """Execute the screenshot capture after window is minimized."""
        try:
            # Capture screenshot via session manager (interactive region selection)
            screenshot_path = self.session_manager.capture_interactive_region()

            # Restaurar la ventana
            self.showNormal()
            self.activateWindow()
            self.raise_()

            if screenshot_path:
                self._on_status_update(f'Screenshot saved')
            else:
                self._on_status_update('Screenshot cancelled', is_error=False)

        except Exception as e:
            # Asegurar que la ventana se restaure en caso de error
            self.showNormal()
            self.activateWindow()
            self.raise_()

            logger.error(f"Failed to take screenshot: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            self._on_status_update(f'Failed to take screenshot: {str(e)}', is_error=True)
            QMessageBox.critical(self, 'Error', f'Failed to take screenshot: {str(e)}')
    
    def _on_view_screenshots(self):
        """Handle view screenshots button click."""
        try:
            session_id = None
            session_name = None
            
            # Check if there's an active session first
            active_session = self.session_manager.get_active_session()
            
            if active_session:
                session_id = active_session.id
                session_name = active_session.name
            else:
                # Get the most recent session from the list
                sessions = self.session_manager.db.list_sessions()
                if sessions:
                    session_id = sessions[0]['id']
                    session_name = sessions[0]['name']
            
            if not session_id:
                QMessageBox.information(
                    self,
                    'No Sessions',
                    'There are no sessions to view screenshots for.'
                )
                return
            
            # Fetch screenshots from database
            screenshots = self.session_manager.db.get_screenshots(session_id)
            
            if not screenshots:
                QMessageBox.information(
                    self,
                    'No Screenshots',
                    "No screenshots have been taken in this session yet."
                )
                return
            
            # Create a dialog to display the screenshots
            dialog = QDialog(self)
            dialog.setWindowTitle(f"Screenshots - {session_name}")
            dialog.setMinimumSize(800, 600)
            
            layout = QVBoxLayout(dialog)
            
            # Header
            header_label = QLabel(f"Screenshots for: {session_name}")
            header_font = header_label.font()
            header_font.setPointSize(14)
            header_font.setBold(True)
            header_label.setFont(header_font)
            layout.addWidget(header_label)
            
            # Scroll area for screenshots
            scroll_area = QScrollArea()
            scroll_area.setWidgetResizable(True)
            
            # Grid layout for screenshots
            grid_widget = QWidget()
            grid_layout = QGridLayout(grid_widget)
            grid_layout.setSpacing(10)
            
            # Add screenshots to the grid
            from datetime import datetime
            for idx, screenshot in enumerate(screenshots):
                filepath = screenshot.get('filepath', '')
                timestamp = screenshot.get('timestamp', 0)
                
                # Convert timestamp to readable format
                dt = datetime.fromtimestamp(timestamp)
                time_str = dt.strftime('%H:%M:%S')
                
                # Create label with image
                image_label = QLabel()
                pixmap = QPixmap(filepath)
                
                if not pixmap.isNull():
                    # Scale to fit while maintaining aspect ratio
                    scaled_pixmap = pixmap.scaled(300, 200, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                    image_label.setPixmap(scaled_pixmap)
                else:
                    image_label.setText(f"Failed to load image\n{filepath}")
                
                image_label.setAlignment(Qt.AlignCenter)
                image_label.setCursor(Qt.PointingHandCursor)
                image_label.mousePressEvent = lambda event, fp=filepath, ts=timestamp: self._show_full_image(fp, ts)
                
                # Timestamp label
                time_label = QLabel(f"Captured at: {time_str}")
                time_label.setAlignment(Qt.AlignCenter)
                
                # Add to grid (2 columns)
                grid_layout.addWidget(image_label, idx // 2 * 2, idx % 2)
                grid_layout.addWidget(time_label, idx // 2 * 2 + 1, idx % 2)
            
            scroll_area.setWidget(grid_widget)
            layout.addWidget(scroll_area)
            
            # Close button
            close_button = QPushButton("Close")
            close_button.clicked.connect(dialog.close)
            layout.addWidget(close_button)
            
            dialog.exec()
            
        except Exception as e:
            logger.error(f"Failed to view screenshots: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            QMessageBox.critical(
                self,
                'Error',
                f"Failed to view screenshots: {str(e)}"
            )
    
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
                        
                        # Update summary status in database
                        self.session_manager.db.update_session(session.id, summary_status='summarized')
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
            
            # Reload sessions to show updated status
            self._load_past_sessions()
            
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
