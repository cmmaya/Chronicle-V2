from PySide6.QtWidgets import (QMainWindow, QMenuBar, QWidget, QVBoxLayout, 
                                QHBoxLayout, QPushButton, QLabel, QStatusBar,
                                QMessageBox, QApplication, QListWidget, QGroupBox,
                                QListWidgetItem, QMenu, QTableWidget, QTableWidgetItem,
                                QHeaderView, QComboBox, QDialog, QTextBrowser, QScrollArea, 
                                QGridLayout, QSlider, QDialogButtonBox, QTextEdit, QCheckBox,
                                QFrame, QAbstractItemView, QSplitter)
from PySide6.QtCore import Qt, QTimer, QMetaObject, Slot, Q_ARG
from PySide6.QtGui import QAction, QPixmap, QColor
from typing import Optional
import logging

from .session_manager import SessionManager
from ..summarization import SummaryGenerator
from ..config import ASSISTANT_AGENTS, SESSION, ALLOWED_MODELS, get_selected_model, set_selected_model
from ..assistant.service import AssistantAnswerService
from ..screenshots.context_generator import ScreenshotContextGenerator

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
        
        # Detached transcription window
        self._detached_window = None
        self._detached_display = None
        
        # Detached assistant window
        self._detached_assistant_window = None
        
        # VAD settings
        self._vad_threshold = 30  # Default 30%
        self._vad_aggressiveness = 2  # Default mode 2
        
        # Transcription view components (for chat-like display)
        self._transcription_scroll_area = None
        self._transcription_container = None
        self._transcription_layout = None
        self._transcription_history = []  # Store transcriptions for detached window
        self._transcription_filter = 'all'  # Filter state: 'all', 'mic', or 'system'
        
        # Assistant panel state
        self._current_question = None  # Store original question for retry
        self._current_candidates = []  # Store current candidates for selection
        self._candidate_list_widget = None  # List widget for candidate selection
        self._current_conversation_id = None  # Store conversation ID for follow-up questions
        
        # Create UI components
        self._create_menu_bar()
        self._create_central_widget()
        self._create_status_bar()
        
        # Initialize session manager
        self._init_session_manager(sessions_path)

    def eventFilter(self, obj, event):
        """Filter events for editing screenshot descriptions."""
        if event.type() == event.Type.MouseButtonDblClick:
            # Check if it's a description label
            if obj.objectName().startswith('desc_label_'):
                filepath = obj.property('filepath')
                session_id = obj.property('session_id')
                if filepath and session_id:
                    self._edit_screenshot_description(obj, filepath, session_id)
                    return True
        return super().eventFilter(obj, event)

    def _edit_screenshot_description(self, label, filepath, session_id):
        """Edit screenshot description with a dialog."""
        from PySide6.QtWidgets import QInputDialog, QLineEdit

        # Get current description
        current_desc = label.property('description') or ''

        text, ok = QInputDialog.getText(
            self,
            'Edit Description',
            'Enter a description for this screenshot:',
            QLineEdit.Normal,
            current_desc
        )

        if ok and text is not None:
            new_description = text.strip()
            # Update in database
            self.session_manager.db.update_screenshot_description(filepath, new_description)
            # Update label
            if new_description:
                label.setText(f"Description: {new_description}")
                label.setStyleSheet("color: black;")
            else:
                label.setText("<i>Double-click to add description</i>")
                label.setStyleSheet("color: gray;")
            label.setProperty('description', new_description)
            self._on_status_update('Screenshot description updated')
        
    def _init_session_manager(self, sessions_path: str):
        """Initialize the session manager.
        
        Args:
            sessions_path: Path to sessions directory
        """
        try:
            self.session_manager = SessionManager(
                base_path=sessions_path,
                db_path='chronicle.db',
                status_callback=self._on_status_update,
                live_transcription_ui_callback=self._on_live_transcription
            )
            
            # Initialize Assistant Answer Service
            self.assistant_service = AssistantAnswerService(
                db=self.session_manager.db
            )
            
            # Track active/selected session for assistant
            self._active_session_id = None
            self._selected_session_id = None
            
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
                
                # FIX: Verify transcription status against actual transcripts in database
                # This corrects cases where transcription_status is 'none' but transcripts exist
                session_id = session['id']
                if trans_status == 'none':
                    actual_transcripts = self.session_manager.db.get_transcripts(session_id)
                    if actual_transcripts:
                        # Fix the inconsistent state in database
                        self.session_manager.db.update_session(session_id, transcription_status='transcribed')
                        trans_status = 'transcribed'
                
                # FIX: Verify summary status against actual summaries in database
                # This corrects cases where summary_status is 'none' but summaries exist
                if sum_status == 'none':
                    actual_summaries = self.session_manager.db.get_summaries(session_id)
                    if actual_summaries:
                        # Fix the inconsistent state in database
                        self.session_manager.db.update_session(session_id, summary_status='summarized')
                        sum_status = 'summarized'
                
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
                
                # Add view summary option only if summary exists
                if sum_status == 'summarized':
                    action_combo.addItem("View Summary", "view_summary")
                
                # Set current index based on status
                if trans_status == 'transcribed' and sum_status == 'summarized':
                    action_combo.setCurrentIndex(0)  # Keep "Select Action" visible, show options on dropdown
                    action_combo.setEnabled(True)
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
        elif action == "view_summary":
            # Find the row for this session_id and show summary
            for r in range(self.sessions_list.rowCount()):
                item = self.sessions_list.item(r, 0)
                if item and item.data(Qt.UserRole) == session_id:
                    self._show_session_summary(r)
                    break
    
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
            
            # Context generation section
            context_label = QLabel("Screenshot Context:")
            context_label.setFont(header_font)
            layout.addWidget(context_label)
            
            # Text area for context display
            context_text = QTextEdit()
            context_text.setReadOnly(True)
            context_text.setMaximumHeight(100)
            
            # Check if summary exists - required for context generation
            summaries = self.session_manager.db.get_summaries(session_id)
            has_summary = summaries and len(summaries) > 0
            
            # Load existing context from database if any screenshot has it
            existing_context_parts = []
            for screenshot in screenshots:
                ai_summary = screenshot.get('ai_summary', '')
                visible_text = screenshot.get('visible_text', '')
                keywords = screenshot.get('keywords', '')
                
                if ai_summary:  # If there's existing context
                    import json
                    try:
                        visible_text_list = json.loads(visible_text) if visible_text else []
                    except:
                        visible_text_list = []
                    
                    try:
                        keywords_list = json.loads(keywords) if keywords else []
                    except:
                        keywords_list = []
                    
                    visible_text_str = ", ".join(visible_text_list) if visible_text_list else "None"
                    keywords_str = ", ".join(keywords_list) if keywords_list else "None"
                    
                    existing_context_parts.append(f"""Screenshot: {screenshot.get('filepath', '')}
Summary: {ai_summary}
Visible Text: {visible_text_str}
Keywords: {keywords_str}""")
            
            if has_summary:
                context_text.setPlaceholderText("Click 'Give Context' to generate AI context for screenshots...")
            else:
                context_text.setPlaceholderText("Generate a summary first before generating screenshot context.")
            
            # If there's existing context, display it
            if existing_context_parts:
                context_text.setPlainText("\n\n".join(existing_context_parts))
            
            layout.addWidget(context_text)
            
            # Buttons
            button_layout = QHBoxLayout()
            
            # Give Context button - enabled only if there are screenshots AND summary exists
            give_context_button = QPushButton("Give Context")
            give_context_button.setEnabled(has_summary and len(screenshots) > 0)
            
            if not has_summary:
                give_context_button.setToolTip("Generate a summary first before generating screenshot context")
            
            # Close button
            close_button = QPushButton("Close")
            close_button.clicked.connect(dialog.close)
            
            button_layout.addWidget(give_context_button)
            button_layout.addStretch()
            button_layout.addWidget(close_button)
            
            layout.addLayout(button_layout)
            
            # Store references for the callback
            def on_give_context():
                """Generate context for screenshots."""
                give_context_button.setEnabled(False)
                give_context_button.setText("Generating...")
                context_text.setPlainText("Generating context...")
                QApplication.processEvents()
                
                try:
                    # Create context generator
                    context_gen = ScreenshotContextGenerator(database=self.session_manager.db)
                    
                    # Get summary for context generation
                    summary_content = ""
                    if has_summary:
                        summary = summaries[0]
                        summary_content = summary.get('content', '')
                    
                    # Get all transcripts for finding nearest ones
                    all_transcripts = self.session_manager.db.get_transcripts(session_id)
                    
                    # Generate context for each screenshot
                    context_display_parts = []
                    for screenshot in screenshots:
                        filepath = screenshot.get('filepath', '')
                        screenshot_timestamp = screenshot.get('timestamp', 0)
                        
                        if filepath:
                            # Find 2 nearest transcripts to this screenshot timestamp
                            transcript_excerpt = ""
                            if all_transcripts:
                                # Sort by absolute time difference to find nearest
                                sorted_transcripts = sorted(
                                    all_transcripts,
                                    key=lambda t: abs(t.get('timestamp', 0) - screenshot_timestamp)
                                )
                                nearest_2 = sorted_transcripts[:2]
                                transcript_excerpt = " | ".join(
                                    t.get('text', '')[:200] for t in nearest_2 if t.get('text')
                                )
                            
                            try:
                                context = context_gen.generate_context(
                                    screenshot_path=filepath,
                                    summary=summary_content if summary_content else None,
                                    transcript_excerpt=transcript_excerpt if transcript_excerpt else None,
                                    store=True
                                )
                                
                                # Format the context for display
                                ai_summary = context.get('summary', 'N/A')
                                visible_text = context.get('visible_text', [])
                                keywords = context.get('keywords', [])
                                
                                # Format visible text as string
                                if isinstance(visible_text, list):
                                    visible_text_str = ", ".join(visible_text) if visible_text else "None"
                                else:
                                    visible_text_str = str(visible_text) if visible_text else "None"
                                
                                # Format keywords as string
                                if isinstance(keywords, list):
                                    keywords_str = ", ".join(keywords) if keywords else "None"
                                else:
                                    keywords_str = str(keywords) if keywords else "None"
                                
                                screenshot_entry = f"""Screenshot: {filepath}
Summary: {ai_summary}
Visible Text: {visible_text_str}
Keywords: {keywords_str}"""
                                
                                context_display_parts.append(screenshot_entry)
                            except Exception as e:
                                logger.warning(f"Failed to generate context for {filepath}: {e}")
                                context_display_parts.append(f"[Error for {filepath}: {str(e)}]")
                    
                    # Display all contexts
                    if context_display_parts:
                        context_text.setPlainText("\n\n".join(context_display_parts))
                        self._on_status_update(f"Generated context for {len(context_display_parts)} screenshot(s)")
                    else:
                        context_text.setPlainText("No context could be generated.")
                        
                except Exception as e:
                    logger.error(f"Failed to generate screenshot context: {e}")
                    import traceback
                    logger.error(traceback.format_exc())
                    context_text.setPlainText(f"Error generating context: {str(e)}")
                    QMessageBox.warning(self, 'Context Generation Failed', str(e))
                finally:
                    give_context_button.setEnabled(True)
                    give_context_button.setText("Give Context")
            
            give_context_button.clicked.connect(on_give_context)
            
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
        
        settings_menu = menu_bar.addMenu('Settings')
        
        # VAD settings action
        vad_action = QAction('VAD Settings...', self)
        vad_action.triggered.connect(self._show_vad_settings)
        settings_menu.addAction(vad_action)
        
        # Model settings action
        model_action = QAction('Model Settings...', self)
        model_action.triggered.connect(self._show_model_settings)
        settings_menu.addAction(model_action)
        
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
        
        # Main layout - horizontal split
        main_layout = QHBoxLayout(central_widget)
        main_layout.setSpacing(20)
        main_layout.setContentsMargins(20, 20, 20, 20)
        
        # Left side widget - existing controls
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setSpacing(15)
        
        # Title
        title_label = QLabel('Chronicle')
        title_label.setAlignment(Qt.AlignCenter)
        title_font = title_label.font()
        title_font.setPointSize(24)
        title_font.setBold(True)
        title_label.setFont(title_font)
        left_layout.addWidget(title_label)
        
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
        left_layout.addLayout(session_layout)
        
        # Spacer
        left_layout.addStretch()
        
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
        
        left_layout.addLayout(button_layout)
        
        # Live transcription checkbox
        self.live_transcription_checkbox = QCheckBox('Enable live transcription')
        self.live_transcription_checkbox.setChecked(True)
        left_layout.addWidget(self.live_transcription_checkbox)
        
        # Auto summary checkbox
        self.auto_summary_checkbox = QCheckBox('Auto-generate summary after session stop')
        self.auto_summary_checkbox.setChecked(SESSION.get('auto_summary_after_stop', False))
        self.auto_summary_checkbox.toggled.connect(lambda checked: SESSION.__setitem__('auto_summary_after_stop', checked))
        left_layout.addWidget(self.auto_summary_checkbox)
        
        # Spacer
        left_layout.addStretch()
        
        # Status display
        self.status_label = QLabel('Ready')
        self.status_label.setAlignment(Qt.AlignCenter)
        status_font = self.status_label.font()
        status_font.setPointSize(16)
        self.status_label.setFont(status_font)
        left_layout.addWidget(self.status_label)
        
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
        sessions_layout.addWidget(self.sessions_list)
        sessions_group.setLayout(sessions_layout)
        left_layout.addWidget(sessions_group)
        
        # Assistant panel
        assistant_group = QGroupBox('Assistant')
        assistant_layout = QVBoxLayout()
        
        # Agent selection row
        agent_layout = QHBoxLayout()
        agent_label = QLabel("Agent:")
        agent_layout.addWidget(agent_label)
        
        self.agent_combo = QComboBox()
        # Populate with agents from config
        agents = ASSISTANT_AGENTS.get('agents', {})
        default_agent_id = ASSISTANT_AGENTS.get('default', '')
        for agent_id, agent_info in agents.items():
            self.agent_combo.addItem(agent_info.get('label', agent_id), agent_id)
        # Set default selection
        default_index = self.agent_combo.findData(default_agent_id)
        if default_index >= 0:
            self.agent_combo.setCurrentIndex(default_index)
        agent_layout.addWidget(self.agent_combo)
        assistant_layout.addLayout(agent_layout)
        
        # Scope selection row
        scope_layout = QHBoxLayout()
        scope_label = QLabel("Scope:")
        scope_layout.addWidget(scope_label)
        
        self.scope_combo = QComboBox()
        self.scope_combo.addItem("Current Session", "current")
        self.scope_combo.addItem("Any Session", "any")
        # Set default to "Any Session"
        self.scope_combo.setCurrentIndex(1)
        scope_layout.addWidget(self.scope_combo)
        scope_layout.addStretch()
        assistant_layout.addLayout(scope_layout)
        
        # Question input
        question_label = QLabel("Question:")
        assistant_layout.addWidget(question_label)
        
        self.question_input = QTextEdit()
        self.question_input.setPlaceholderText("Ask a question about your sessions...")
        self.question_input.setMaximumHeight(80)
        assistant_layout.addWidget(self.question_input)
        
        # Ask and Detach buttons
        button_layout = QHBoxLayout()
        
        self.ask_button = QPushButton("Ask")
        self.ask_button.clicked.connect(self._on_ask_clicked)
        button_layout.addWidget(self.ask_button)
        
        self.new_chat_button = QPushButton("New Chat")
        self.new_chat_button.clicked.connect(self._on_new_chat_clicked)
        button_layout.addWidget(self.new_chat_button)
        
        self.detach_assistant_button = QPushButton("Detach")
        self.detach_assistant_button.clicked.connect(self._on_detach_assistant)
        button_layout.addWidget(self.detach_assistant_button)
        
        button_layout.addStretch()
        assistant_layout.addLayout(button_layout)
        
        # Answer display (read-only)
        answer_label = QLabel("Answer:")
        assistant_layout.addWidget(answer_label)
        
        self.answer_display = QTextEdit()
        self.answer_display.setReadOnly(True)
        self.answer_display.setPlaceholderText("Assistant responses will appear here...")
        self.answer_display.setMaximumHeight(150)
        assistant_layout.addWidget(self.answer_display)
        
        # Candidate session selection (initially hidden)
        self.candidate_group = QGroupBox("Select a Session:")
        candidate_layout = QVBoxLayout()
        
        self._candidate_list_widget = QListWidget()
        self._candidate_list_widget.setSelectionMode(QAbstractItemView.SingleSelection)
        self._candidate_list_widget.setMaximumHeight(100)
        self._candidate_list_widget.itemClicked.connect(self._on_candidate_selected)
        candidate_layout.addWidget(self._candidate_list_widget)
        
        # Use Selected Session button
        self.use_candidate_button = QPushButton("Use Selected Session")
        self.use_candidate_button.clicked.connect(self._on_use_candidate_clicked)
        self.use_candidate_button.setEnabled(False)
        candidate_layout.addWidget(self.use_candidate_button)
        
        self.candidate_group.setLayout(candidate_layout)
        self.candidate_group.setVisible(False)  # Hidden by default
        assistant_layout.addWidget(self.candidate_group)
        
        # Past conversations button
        self.past_conversations_button = QPushButton("Past Conversations")
        self.past_conversations_button.clicked.connect(self._on_past_conversations_clicked)
        assistant_layout.addWidget(self.past_conversations_button)
        
        # Past conversations dialog (created on demand)
        self._past_conversations_dialog = None
        
        assistant_group.setLayout(assistant_layout)
        left_layout.addWidget(assistant_group)
        
        # Right side widget - placeholder for live transcription
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        
        # Live transcription display area - using chat-like view
        live_transcription_group = QGroupBox('Live Transcriptions')
        live_transcription_layout = QVBoxLayout()
        
        # Create the chat-like transcription view
        transcription_view = self._create_transcription_view()
        live_transcription_layout.addWidget(transcription_view)
        
        # Detach button
        self.detach_transcription_button = QPushButton('Detach Window')
        self.detach_transcription_button.clicked.connect(self._on_detach_transcription)
        live_transcription_layout.addWidget(self.detach_transcription_button)
        
        live_transcription_group.setLayout(live_transcription_layout)
        right_layout.addWidget(live_transcription_group)
        
        # Add both sides to main layout
        main_layout.addWidget(left_widget, 1)  # Stretch factor 1
        main_layout.addWidget(right_widget, 1)  # Stretch factor 1
    
    def _create_status_bar(self):
        """Create the status bar."""
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage('Ready')
    
    def _create_transcription_view(self) -> QWidget:
        """Create a scrollable chat-like view for displaying live transcriptions.
        
        Returns:
            QWidget: The widget containing the transcription view
        """
        # Create wrapper widget to return
        wrapper = QWidget()
        wrapper_layout = QVBoxLayout(wrapper)
        wrapper_layout.setContentsMargins(0, 0, 0, 0)
        wrapper_layout.setSpacing(5)
        
        # Filter controls row
        filter_layout = QHBoxLayout()
        filter_layout.setContentsMargins(0, 0, 0, 0)
        
        filter_label = QLabel("Filter:")
        filter_label.setFont(self.status_label.font())
        filter_layout.addWidget(filter_label)
        
        # Filter combo box
        self._transcription_filter_combo = QComboBox()
        self._transcription_filter_combo.addItem("All", "all")
        self._transcription_filter_combo.addItem("You (mic)", "mic")
        self._transcription_filter_combo.addItem("Them (system)", "system")
        self._transcription_filter_combo.currentIndexChanged.connect(self._on_transcription_filter_changed)
        filter_layout.addWidget(self._transcription_filter_combo)
        
        filter_layout.addStretch()
        wrapper_layout.addLayout(filter_layout)
        
        # Create scroll area
        self._transcription_scroll_area = QScrollArea()
        self._transcription_scroll_area.setWidgetResizable(True)
        self._transcription_scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        
        # Create container widget
        self._transcription_container = QWidget()
        self._transcription_scroll_area.setWidget(self._transcription_container)
        
        # Create vertical layout for the container
        self._transcription_layout = QVBoxLayout(self._transcription_container)
        self._transcription_layout.setSpacing(10)
        self._transcription_layout.setContentsMargins(10, 10, 10, 10)
        self._transcription_layout.addStretch()  # Push content to top
        
        wrapper_layout.addWidget(self._transcription_scroll_area)
        
        return wrapper
    
    def add_transcription_to_view(self, text: str, source: str, timestamp: str = ''):
        """Add a transcription to the chat-like view.
        
        Args:
            text: The transcription text
            source: The source ('mic' or 'system')
            timestamp: Optional timestamp string
        """
        # Create a frame for each transcription bubble
        bubble_frame = QFrame()
        bubble_frame.setFrameShape(QFrame.StyledPanel)
        bubble_frame.setFrameShadow(QFrame.Raised)
        
        # Store the source as a property for filtering
        bubble_frame.setProperty('source', source)
        
        # Check if this bubble should be visible based on current filter
        if self._transcription_filter == 'mic' and source != 'mic':
            bubble_frame.hide()
        elif self._transcription_filter == 'system' and source != 'system':
            bubble_frame.hide()
        
        # Set layout for the bubble
        bubble_layout = QVBoxLayout(bubble_frame)
        bubble_layout.setContentsMargins(10, 8, 10, 8)
        bubble_layout.setSpacing(4)
        
        # Header with source and timestamp
        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        
        # Source label
        source_label = QLabel(f"{'🎤 Mic' if source == 'mic' else '🔊 System'}")
        source_font = source_label.font()
        source_font.setPointSize(10)
        source_font.setBold(True)
        source_label.setFont(source_font)
        
        # Timestamp label
        time_label = QLabel(timestamp)
        time_label.setStyleSheet("color: gray;")
        time_font = time_label.font()
        time_font.setPointSize(9)
        time_label.setFont(time_font)
        
        header_layout.addWidget(source_label)
        header_layout.addStretch()
        header_layout.addWidget(time_label)
        
        bubble_layout.addLayout(header_layout)
        
        # Transcription text
        text_label = QLabel(text)
        text_label.setWordWrap(True)
        text_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        
        bubble_layout.addWidget(text_label)
        
        # Style based on source
        if source == 'mic':
            # Mic - left aligned with blue-ish background
            bubble_frame.setStyleSheet("""
                QFrame {
                    background-color: #E3F2FD;
                    border-radius: 10px;
                    border: 1px solid #90CAF9;
                }
            """)
            header_layout.insertStretch(0, 0)  # Left align
        else:
            # System - left aligned with green-ish background
            bubble_frame.setStyleSheet("""
                QFrame {
                    background-color: #E8F5E9;
                    border-radius: 10px;
                    border: 1px solid #A5D6A7;
                }
            """)
            header_layout.insertStretch(0, 0)  # Left align
        
        # Add to layout (before the stretch)
        self._transcription_layout.insertWidget(
            self._transcription_layout.count() - 1,  # Insert before stretch
            bubble_frame
        )
        
        # Auto-scroll to bottom
        self._transcription_scroll_area.verticalScrollBar().setValue(
            self._transcription_scroll_area.verticalScrollBar().maximum()
        )
        
        # Also update detached window if it exists
        if hasattr(self, '_detached_display') and self._detached_display:
            # For detached window, we use the simpler QTextEdit approach
            pass  # Detached window uses different display method
    
    def _clear_transcription_view(self):
        """Clear all transcriptions from the view."""
        # Clear history
        self._transcription_history = []
        
        if hasattr(self, '_transcription_layout') and self._transcription_layout:
            # Remove all widgets except the stretch (last item)
            while self._transcription_layout.count() > 1:
                item = self._transcription_layout.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()

    def _on_transcription_filter_changed(self, index: int):
        """Handle the transcription filter selection change.
        
        Args:
            index: The index of the selected filter option
        """
        # Get the selected filter value from combo box
        filter_value = self._transcription_filter_combo.currentData()
        self._transcription_filter = filter_value
        
        # Update visibility of all transcription bubbles
        self._update_transcription_filter()
    
    def _update_transcription_filter(self):
        """Update the visibility of transcriptions based on the current filter."""
        if not hasattr(self, '_transcription_layout') or not self._transcription_layout:
            return
        
        # Iterate through all transcription bubble widgets
        # The last item is the stretch, so we iterate up to count - 1
        for i in range(self._transcription_layout.count() - 1):
            item = self._transcription_layout.itemAt(i)
            if item and item.widget():
                bubble_frame = item.widget()
                # Get the source stored in the bubble
                source = bubble_frame.property('source')
                
                # Show or hide based on filter
                if self._transcription_filter == 'all':
                    bubble_frame.show()
                elif self._transcription_filter == 'mic':
                    if source == 'mic':
                        bubble_frame.show()
                    else:
                        bubble_frame.hide()
                elif self._transcription_filter == 'system':
                    if source == 'system':
                        bubble_frame.show()
                    else:
                        bubble_frame.hide()

    def _on_status_update(self, message: str, is_error: bool = False):
        """Handle status updates from the session manager."""
        print(f"[DEBUG] Status update: {message}")
        self.status_label.setText(message)
        self.status_bar.showMessage(message)
        
        if is_error:
            self.status_label.setStyleSheet("color: red;")
        else:
            self.status_label.setStyleSheet("")
    
    def _on_live_transcription(self, result: dict):
        """Handle live transcription results from the session manager.
        
        Args:
            result: Dictionary with keys: text, source, timestamp_start
        
        Thread-safe: Uses QTimer.singleShot to update UI from any thread.
        """
        try:
            text = result.get('text', '')
            source = result.get('source', 'unknown')
            timestamp = result.get('timestamp_start', result.get('timestamp', ''))
            
            if not text:
                return
            
            # Format timestamp
            time_str = ''
            if timestamp:
                if isinstance(timestamp, str):
                    try:
                        from datetime import datetime
                        dt = datetime.fromisoformat(timestamp)
                        time_str = dt.strftime('%H:%M:%S')
                    except:
                        time_str = str(timestamp)
                else:
                    time_str = str(timestamp)
            
            # Format display text
            source_label = 'Mic' if source == 'mic' else 'System'
            if time_str:
                display_text = f"[{time_str}] {source_label}: {text}"
            else:
                display_text = f"{source_label}: {text}"
            
            # Use thread-safe UI update via QMetaObject.invokeMethod
            # This works correctly when called from any thread (Python threading.Thread)
            QMetaObject.invokeMethod(
                self,
                "_append_transcription",
                Qt.QueuedConnection,
                Q_ARG(str, display_text)
            )
            
        except Exception as e:
            logger.error(f"Failed to display live transcription: {e}")
    
    @Slot(str)
    def _append_transcription(self, text: str):
        """Append transcription text to the display.
        
        This method is designed to be called via QMetaObject.invokeMethod
        from any thread for thread-safe UI updates.
        
        Args:
            text: The transcription text to append (format: "[HH:MM:SS] Source: text")
        """
        # Store in history for detached window
        self._transcription_history.append(text)
        
        # Parse the formatted text to extract components
        # Format: "[HH:MM:SS] Source: text" or "Source: text"
        timestamp = ''
        source = 'mic'  # Default to mic
        clean_text = text
        
        import re
        match = re.match(r'\[(\d{2}:\d{2}:\d{2})\]\s+(Mic|System):\s+(.+)', text)
        if match:
            timestamp = match.group(1)
            source_match = match.group(2).lower()
            source = 'mic' if source_match == 'mic' else 'system'
            clean_text = match.group(3)
        
        # Use the new chat-like view method
        self.add_transcription_to_view(clean_text, source, timestamp)
        
        # Also update detached window if it exists
        if hasattr(self, '_detached_window') and self._detached_window:
            self._add_transcription_to_detached(text)
    
    def _on_detach_transcription(self):
        """Create a detached window for live transcriptions."""
        if self._detached_window:
            # Window already exists, just bring it to front
            self._detached_window.show()
            self._detached_window.activateWindow()
            self._detached_window.raise_()
            return
        
        # Create detached window with no parent (standalone window)
        # This ensures it doesn't minimize when main window minimizes
        self._detached_window = QDialog(None)  # No parent - standalone window
        self._detached_window.setWindowTitle('Live Transcriptions')
        self._detached_window.resize(400, 500)
        
        # Set window flags: stay on top but not as modal
        self._detached_window.setWindowFlags(
            Qt.Window | 
            Qt.WindowStaysOnTopHint | 
            Qt.WindowCloseButtonHint | 
            Qt.WindowMinimizeButtonHint
        )
        
        # Prevent the detached window from activating the main window when minimized
        self._detached_window.setAttribute(Qt.WA_QuitOnClose, False)
        
        layout = QVBoxLayout(self._detached_window)
        
        # Label
        label = QLabel('Live Transcriptions')
        label_font = label.font()
        label_font.setPointSize(16)
        label_font.setBold(True)
        label.setFont(label_font)
        layout.addWidget(label)
        
        # Filter controls row
        filter_layout = QHBoxLayout()
        
        filter_label = QLabel("Filter:")
        filter_layout.addWidget(filter_label)
        
        # Filter combo box for detached window
        self._detached_filter_combo = QComboBox()
        self._detached_filter_combo.addItem("All", "all")
        self._detached_filter_combo.addItem("You (mic)", "mic")
        self._detached_filter_combo.addItem("Them (system)", "system")
        self._detached_filter_combo.currentIndexChanged.connect(self._on_detached_filter_changed)
        filter_layout.addWidget(self._detached_filter_combo)
        
        filter_layout.addStretch()
        layout.addLayout(filter_layout)
        
        # Use same chat-like view as main window
        self._detached_scroll_area = QScrollArea()
        self._detached_scroll_area.setWidgetResizable(True)
        self._detached_scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        
        self._detached_container = QWidget()
        self._detached_scroll_area.setWidget(self._detached_container)
        
        self._detached_layout = QVBoxLayout(self._detached_container)
        self._detached_layout.setSpacing(10)
        self._detached_layout.setContentsMargins(10, 10, 10, 10)
        self._detached_layout.addStretch()
        
        layout.addWidget(self._detached_scroll_area)
        
        # Close button
        close_button = QPushButton('Close')
        close_button.clicked.connect(self._on_close_detached_window)
        layout.addWidget(close_button)
        
        # Copy existing transcriptions from history
        for text in self._transcription_history:
            self._add_transcription_to_detached(text)
        
        self._detached_window.show()
    
    def _on_detached_filter_changed(self, index: int):
        """Handle the filter selection change in the detached window.
        
        Args:
            index: The index of the selected filter option
        """
        self._update_detached_filter()
    
    def _update_detached_filter(self):
        """Update the visibility of transcriptions in the detached window based on current filter."""
        if not hasattr(self, '_detached_layout') or not self._detached_layout:
            return
        
        filter_value = self._detached_filter_combo.currentData()
        
        # Iterate through all transcription bubble widgets
        for i in range(self._detached_layout.count() - 1):
            item = self._detached_layout.itemAt(i)
            if item and item.widget():
                bubble_frame = item.widget()
                # Get the source stored in the bubble
                source = bubble_frame.property('source')
                
                # Show or hide based on filter
                if filter_value == 'all':
                    bubble_frame.show()
                elif filter_value == 'mic':
                    if source == 'mic':
                        bubble_frame.show()
                    else:
                        bubble_frame.hide()
                elif filter_value == 'system':
                    if source == 'system':
                        bubble_frame.show()
                    else:
                        bubble_frame.hide()
    
    def _add_transcription_to_detached(self, text: str):
        """Add a transcription to the detached window using chat-like bubbles.
        
        Args:
            text: The transcription text (format: "[HH:MM:SS] Source: text")
        """
        # Parse the formatted text
        import re
        timestamp = ''
        source = 'mic'
        clean_text = text
        
        match = re.match(r'\[(\d{2}:\d{2}:\d{2})\]\s+(Mic|System):\s+(.+)', text)
        if match:
            timestamp = match.group(1)
            source_match = match.group(2).lower()
            source = 'mic' if source_match == 'mic' else 'system'
            clean_text = match.group(3)
        
        # Create bubble frame
        bubble_frame = QFrame()
        bubble_frame.setFrameShape(QFrame.StyledPanel)
        bubble_frame.setFrameShadow(QFrame.Raised)
        
        # Store the source as a property for filtering
        bubble_frame.setProperty('source', source)
        
        # Check if this bubble should be visible based on current filter
        filter_value = self._detached_filter_combo.currentData() if hasattr(self, '_detached_filter_combo') else 'all'
        if filter_value == 'mic' and source != 'mic':
            bubble_frame.hide()
        elif filter_value == 'system' and source != 'system':
            bubble_frame.hide()
        
        bubble_layout = QVBoxLayout(bubble_frame)
        bubble_layout.setContentsMargins(10, 8, 10, 8)
        bubble_layout.setSpacing(4)
        
        # Header
        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        
        source_label = QLabel(f"{'🎤 Mic' if source == 'mic' else '🔊 System'}")
        source_font = source_label.font()
        source_font.setPointSize(10)
        source_font.setBold(True)
        source_label.setFont(source_font)
        
        time_label = QLabel(timestamp)
        time_label.setStyleSheet("color: gray;")
        time_font = time_label.font()
        time_font.setPointSize(9)
        time_label.setFont(time_font)
        
        header_layout.addWidget(source_label)
        header_layout.addStretch()
        header_layout.addWidget(time_label)
        
        bubble_layout.addLayout(header_layout)
        
        # Text
        text_label = QLabel(clean_text)
        text_label.setWordWrap(True)
        text_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        bubble_layout.addWidget(text_label)
        
        # Style based on source
        if source == 'mic':
            bubble_frame.setStyleSheet("""
                QFrame {
                    background-color: #E3F2FD;
                    border-radius: 10px;
                    border: 1px solid #90CAF9;
                }
            """)
        else:
            bubble_frame.setStyleSheet("""
                QFrame {
                    background-color: #E8F5E9;
                    border-radius: 10px;
                    border: 1px solid #A5D6A7;
                }
            """)
        
        self._detached_layout.insertWidget(
            self._detached_layout.count() - 1,
            bubble_frame
        )
        
        # Auto-scroll
        self._detached_scroll_area.verticalScrollBar().setValue(
            self._detached_scroll_area.verticalScrollBar().maximum()
        )
    
    def _on_close_detached_window(self):
        """Close the detached transcription window."""
        if self._detached_window:
            self._detached_window.close()
            self._detached_window = None
            self._detached_display = None
            self._detached_scroll_area = None
            self._detached_container = None
            self._detached_layout = None
            self._detached_filter_combo = None

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
            # Clear live transcription display for new session
            self._clear_transcription_view()
            
            # Generate session name with timestamp
            from datetime import datetime
            now = datetime.now()
            session_name = f"Session {now.strftime('%Y-%m-%d %H:%M')}"
            
            # Start session via manager (auto-starts recording)
            enable_live = self.live_transcription_checkbox.isChecked()
            self.session_manager.start_session(session_name, auto_record=True, enable_live_transcription=enable_live)
            
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
                description = screenshot.get('description', '') or ''

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

                # Description label (editable with double click)
                desc_label = QLabel()
                if description:
                    desc_label.setText(f"Description: {description}")
                else:
                    desc_label.setText("<i>Double-click to add description</i>")
                    desc_label.setStyleSheet("color: gray;")
                desc_label.setAlignment(Qt.AlignCenter)
                desc_label.setTextInteractionFlags(Qt.NoTextInteraction)
                desc_label.setCursor(Qt.PointingHandCursor)

                # Store filepath for editing
                desc_label.setProperty('filepath', filepath)
                desc_label.setProperty('session_id', session_id)

                # Install event filter for double-click
                desc_label.installEventFilter(self)
                desc_label.setObjectName(f"desc_label_{idx}")

                # Add to grid (2 columns)
                grid_layout.addWidget(image_label, idx // 2 * 3, idx % 2)
                grid_layout.addWidget(time_label, idx // 2 * 3 + 1, idx % 2)
                grid_layout.addWidget(desc_label, idx // 2 * 3 + 2, idx % 2)

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
    
    def _show_vad_settings(self):
        """Show VAD settings dialog."""
        dialog = QDialog(self)
        dialog.setWindowTitle('VAD Settings')
        dialog.setMinimumWidth(400)
        
        layout = QVBoxLayout(dialog)
        
        # Threshold slider
        threshold_label = QLabel('Speech Threshold:')
        layout.addWidget(threshold_label)
        
        threshold_layout = QHBoxLayout()
        self.threshold_slider = QSlider(Qt.Horizontal)
        self.threshold_slider.setMinimum(5)
        self.threshold_slider.setMaximum(100)
        self.threshold_slider.setValue(self._vad_threshold)
        self.threshold_value_label = QLabel(f'{self._vad_threshold}%')
        threshold_layout.addWidget(self.threshold_slider)
        threshold_layout.addWidget(self.threshold_value_label)
        layout.addLayout(threshold_layout)
        
        # Update label when slider moves
        self.threshold_slider.valueChanged.connect(
            lambda v: self.threshold_value_label.setText(f'{v}%')
        )
        
        # Description
        desc_label = QLabel(
            'Minimum percentage of audio frames that must contain\n'
            'speech for the chunk to be saved. Lower values = more\n'
            'sensitive to short utterances.'
        )
        desc_label.setStyleSheet('color: gray; font-size: 10pt;')
        layout.addWidget(desc_label)
        
        # Aggressiveness slider
        agg_label = QLabel('Aggressiveness:')
        layout.addWidget(agg_label)
        
        agg_layout = QHBoxLayout()
        self.agg_slider = QSlider(Qt.Horizontal)
        self.agg_slider.setMinimum(0)
        self.agg_slider.setMaximum(3)
        self.agg_slider.setValue(self._vad_aggressiveness)
        self.agg_value_label = QLabel(f'Mode {self._vad_aggressiveness}')
        agg_layout.addWidget(self.agg_slider)
        agg_layout.addWidget(self.agg_value_label)
        layout.addLayout(agg_layout)
        
        # Update label when slider moves
        self.agg_slider.valueChanged.connect(
            lambda v: self.agg_value_label.setText(f'Mode {v}')
        )
        
        # Description
        agg_desc_label = QLabel(
            'VAD aggressiveness: 0=least filtering, 3=most aggressive.\n'
            'Use higher modes in noisy environments.'
        )
        agg_desc_label.setStyleSheet('color: gray; font-size: 10pt;')
        layout.addWidget(agg_desc_label)
        
        # Buttons
        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        
        if dialog.exec():
            self._vad_threshold = self.threshold_slider.value()
            self._vad_aggressiveness = self.agg_slider.value()
            
            # Update SessionManager with new VAD settings
            if self.session_manager:
                self.session_manager.vad_threshold = self._vad_threshold / 100.0
                self.session_manager.vad_aggressiveness = self._vad_aggressiveness
            
            self._on_status_update(f'VAD settings updated: {self._vad_threshold}% threshold, Mode {self._vad_aggressiveness}')
    
    def _show_model_settings(self):
        """Show model settings dialog."""
        dialog = QDialog(self)
        dialog.setWindowTitle('Model Settings')
        dialog.setMinimumWidth(450)
        
        layout = QVBoxLayout(dialog)
        
        # Model selection
        model_label = QLabel('Select Model:')
        layout.addWidget(model_label)
        
        model_layout = QHBoxLayout()
        self.model_combo = QComboBox()
        
        # Populate with allowed models from config
        for model_id in ALLOWED_MODELS:
            # Use the model ID as both display text and data
            self.model_combo.addItem(model_id, model_id)
        
        # Set current selection to persisted model
        current_model = get_selected_model()
        current_index = self.model_combo.findData(current_model)
        if current_index >= 0:
            self.model_combo.setCurrentIndex(current_index)
        
        model_layout.addWidget(self.model_combo)
        layout.addLayout(model_layout)
        
        # Description
        desc_label = QLabel(
            'Select the model used for summarization and assistant features.\n'
            'Changes will persist across application restarts.'
        )
        desc_label.setStyleSheet('color: gray; font-size: 10pt;')
        layout.addWidget(desc_label)
        
        # Buttons
        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        
        if dialog.exec():
            selected_model = self.model_combo.currentData()
            if selected_model:
                set_selected_model(selected_model)
                self._on_status_update(f'Model changed to: {selected_model}')
    
    def _on_new_chat_clicked(self):
        """Handle the New Chat button click - resets conversation context."""
        self._current_conversation_id = None
        self.question_input.clear()
        self.answer_display.setPlainText("")
        self._clear_candidates()
        self._on_status_update("New conversation started")
    
    def _on_ask_clicked(self):
        """Handle the Ask button click - wires to AssistantAnswerService."""
        # Get the question from the input
        question = self.question_input.toPlainText().strip()
        
        if not question:
            self.answer_display.setPlainText("Please enter a question.")
            return
        
        # Clear previous candidates when asking a new question
        self._clear_candidates()
        
        # Store the original question for potential retry
        self._current_question = question
        
        # Get selected agent
        agent_id = self.agent_combo.currentData()
        
        # Get scope from combo
        scope_value = self.scope_combo.currentData()  # "current" or "any"
        explicit_scope = "current_session" if scope_value == "current" else "any_session"
        
        # Get active session ID if there's an active session
        active_session = self.session_manager.get_active_session()
        active_session_id = active_session.id if active_session else None
        
        # Get selected session ID from the sessions table
        selected_session_id = self._get_selected_session_id()
        
        # Disable the Ask button while processing
        self.ask_button.setEnabled(False)
        self.answer_display.setPlainText("Thinking...")
        
        # Run the assistant service call in the background using QTimer to keep UI responsive
        QTimer.singleShot(50, lambda: self._run_assistant_query(
            question=question,
            agent_id=agent_id,
            explicit_scope=explicit_scope,
            active_session_id=active_session_id,
            selected_session_id=selected_session_id
        ))
    
    def _run_assistant_query(
        self,
        question: str,
        agent_id: str,
        explicit_scope: str,
        active_session_id: Optional[int],
        selected_session_id: Optional[int]
    ):
        """Execute the assistant query in the background."""
        try:
            # Call the assistant service
            response = self.assistant_service.ask(
                question=question,
                agent_id=agent_id,
                explicit_scope=explicit_scope,
                active_session_id=active_session_id,
                selected_session_id=selected_session_id,
                conversation_id=self._current_conversation_id
            )
            
            # Handle the response
            if response.success:
                # Clear candidates on successful answer
                self._clear_candidates()
                self.answer_display.setPlainText(response.answer or "")
                # Save conversation_id for follow-up questions
                if response.conversation_id:
                    self._current_conversation_id = response.conversation_id
                self._on_status_update("Answer received")
            elif response.needs_clarification:
                # Show clarification question and candidates
                clarification_text = response.clarification_question or ""
                self.answer_display.setPlainText(clarification_text)
                
                # Display candidates if available
                if response.candidates:
                    self._display_candidates(response.candidates)
                else:
                    # Hide candidate UI if no candidates
                    self._clear_candidates()
                    
                self._on_status_update("Clarification needed")
            else:
                # Show error
                error_text = response.error or "Unknown error occurred"
                self.answer_display.setPlainText(f"Error: {error_text}")
                self._on_status_update(f"Assistant error: {error_text}", is_error=True)
                # Clear candidates on error
                self._clear_candidates()
                
        except Exception as e:
            logger.error(f"Assistant query failed: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            self.answer_display.setPlainText(f"Error: {str(e)}")
            self._on_status_update(f"Assistant error: {str(e)}", is_error=True)
            # Clear candidates on exception
            self._clear_candidates()
        
        finally:
            # Re-enable the Ask button
            self.ask_button.setEnabled(True)
    
    def _get_selected_session_id(self) -> Optional[int]:
        """Get the currently selected session ID from the sessions table."""
        selected_indexes = self.sessions_list.selectedIndexes()
        if selected_indexes:
            # Get the row of the first selected item
            row = selected_indexes[0].row()
            item = self.sessions_list.item(row, 0)
            if item:
                return item.data(Qt.UserRole)
        return None
    
    def _display_candidates(self, candidates: list):
        """Display candidate sessions for user selection.
        
        Args:
            candidates: List of candidate session dictionaries with session_id,
                       session_name, and start_time keys.
        """
        from datetime import datetime
        
        self._current_candidates = candidates
        self._candidate_list_widget.clear()
        
        for candidate in candidates:
            session_name = candidate.get('session_name', 'Unknown')
            session_id = candidate.get('session_id', 0)
            start_time = candidate.get('start_time', 0)
            
            # Format display text
            if start_time:
                try:
                    dt = datetime.fromtimestamp(start_time)
                    display_text = f"{session_name} - {dt.strftime('%Y-%m-%d %H:%M')}"
                except:
                    display_text = f"{session_name} (ID: {session_id})"
            else:
                display_text = f"{session_name} (ID: {session_id})"
            
            item = QListWidgetItem(display_text)
            item.setData(Qt.UserRole, session_id)
            self._candidate_list_widget.addItem(item)
        
        self.candidate_group.setVisible(True)
        self.use_candidate_button.setEnabled(False)
    
    def _clear_candidates(self):
        """Clear candidate selection UI."""
        self._current_candidates = []
        self._current_question = None
        self._candidate_list_widget.clear()
        self.candidate_group.setVisible(False)
        self.use_candidate_button.setEnabled(False)
    
    def _on_candidate_selected(self, item: QListWidgetItem):
        """Handle candidate selection - enable the use button.
        
        Args:
            item: The selected list widget item
        """
        self.use_candidate_button.setEnabled(item is not None)
    
    def _on_use_candidate_clicked(self):
        """Handle the Use Selected Session button click - retry with selected session."""
        selected_items = self._candidate_list_widget.selectedItems()
        if not selected_items:
            return
        
        item = selected_items[0]
        selected_session_id = item.data(Qt.UserRole)
        
        if selected_session_id and self._current_question:
            # Disable button during processing
            self.use_candidate_button.setEnabled(False)
            
            # Get current settings
            agent_id = self.agent_combo.currentData()
            
            # Run query with selected session (force current_session scope)
            QTimer.singleShot(50, lambda: self._run_assistant_query(
                question=self._current_question,
                agent_id=agent_id,
                explicit_scope="current_session",
                active_session_id=None,
                selected_session_id=selected_session_id
            ))
    
    def _on_past_conversations_clicked(self):
        """Show a dialog with past assistant conversations."""
        # Get database instance
        db = self.session_manager.db
        
        # Get all conversations
        try:
            conversations = db.list_conversations()
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to load conversations: {str(e)}")
            return
        
        # Create dialog
        dialog = QDialog(self)
        dialog.setWindowTitle("Past Conversations")
        dialog.resize(600, 500)
        
        main_layout = QVBoxLayout(dialog)
        
        if not conversations:
            # No conversations yet
            no_conv_label = QLabel("No past conversations yet.\nStart a new conversation with the Assistant!")
            no_conv_label.setAlignment(Qt.AlignCenter)
            main_layout.addWidget(no_conv_label)
            
            close_button = QPushButton("Close")
            close_button.clicked.connect(dialog.close)
            main_layout.addWidget(close_button)
            
            dialog.exec_()
            return
        
        # Create split layout: list on left, messages on right
        split_layout = QSplitter(Qt.Horizontal)
        
        # Left side: conversation list
        list_widget = QListWidget()
        list_widget.setMaximumWidth(200)
        
        # Right side: messages display
        messages_scroll = QScrollArea()
        messages_widget = QWidget()
        messages_layout = QVBoxLayout(messages_widget)
        messages_scroll.setWidget(messages_widget)
        messages_scroll.setWidgetResizable(True)
        
        # Store conversation data
        conversation_data = {}
        
        for conv in conversations:
            conv_id = conv['id']
            session_id = conv.get('session_id')
            title = conv.get('title')
            created_at = conv.get('created_at', 0)
            updated_at = conv.get('updated_at', 0)
            
            # Format display text
            from datetime import datetime
            date_str = datetime.fromtimestamp(updated_at).strftime("%Y-%m-%d %H:%M")
            
            if title:
                display_text = f"{title}\n{date_str}"
            else:
                display_text = f"Conversation #{conv_id}\n{date_str}"
            
            # Add scope indicator
            if session_id:
                display_text += "\n(Session-specific)"
            else:
                display_text += "\n(All sessions)"
            
            item = QListWidgetItem(display_text)
            item.setData(Qt.UserRole, conv_id)
            list_widget.addItem(item)
            
            conversation_data[conv_id] = conv
        
        split_layout.addWidget(list_widget)
        split_layout.addWidget(messages_scroll)
        split_layout.setStretchFactor(0, 1)
        split_layout.setStretchFactor(1, 2)
        
        main_layout.addWidget(split_layout)
        
        # Close button
        close_button = QPushButton("Close")
        close_button.clicked.connect(dialog.close)
        main_layout.addWidget(close_button)
        
        # Handle selection
        def on_selection_changed():
            selected_items = list_widget.selectedItems()
            if not selected_items:
                return
            
            conv_id = selected_items[0].data(Qt.UserRole)
            
            # Clear previous messages
            while messages_layout.count():
                item = messages_layout.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()
            
            # Get messages for this conversation
            try:
                messages = db.get_messages(conv_id)
            except Exception as e:
                messages_label = QLabel(f"Error loading messages: {str(e)}")
                messages_layout.addWidget(messages_label)
                return
            
            if not messages:
                no_msg_label = QLabel("No messages in this conversation.")
                messages_layout.addWidget(no_msg_label)
                return
            
            # Display messages
            for msg in messages:
                role = msg.get('role', 'unknown')
                content = msg.get('content', '')
                timestamp = msg.get('timestamp', 0)
                time_str = datetime.fromtimestamp(timestamp).strftime("%H:%M")
                
                # Create message bubble
                msg_frame = QFrame()
                msg_frame.setFrameShape(QFrame.StyledPanel)
                msg_layout = QVBoxLayout(msg_frame)
                
                # Role label
                role_label = QLabel(f"{role.capitalize()} - {time_str}")
                role_font = role_label.font()
                role_font.setBold(True)
                role_label.setFont(role_font)
                
                if role == 'user':
                    role_label.setStyleSheet("color: #0066cc;")
                elif role == 'assistant':
                    role_label.setStyleSheet("color: #008800;")
                
                msg_layout.addWidget(role_label)
                
                # Content
                content_label = QLabel(content)
                content_label.setWordWrap(True)
                msg_layout.addWidget(content_label)
                
                messages_layout.addWidget(msg_frame)
            
            messages_layout.addStretch()
        
        list_widget.itemClicked.connect(on_selection_changed)
        
        dialog.exec_()
    
    def _on_detach_assistant(self):
        """Create a detached window for the assistant chat panel."""
        if self._detached_assistant_window:
            # Window already exists, just bring it to front
            self._detached_assistant_window.show()
            self._detached_assistant_window.activateWindow()
            self._detached_assistant_window.raise_()
            return
        
        # Create detached window with no parent (standalone window)
        # This ensures it doesn't minimize when main window minimizes
        self._detached_assistant_window = QDialog(None)  # No parent - standalone window
        self._detached_assistant_window.setWindowTitle('Assistant Chat')
        self._detached_assistant_window.resize(450, 600)
        
        # Set window flags: stay on top but not as modal
        self._detached_assistant_window.setWindowFlags(
            Qt.Window | 
            Qt.WindowStaysOnTopHint | 
            Qt.WindowCloseButtonHint | 
            Qt.WindowMinimizeButtonHint
        )
        
        # Prevent the detached window from activating the main window when minimized
        self._detached_assistant_window.setAttribute(Qt.WA_QuitOnClose, False)
        
        # Create main layout
        main_layout = QVBoxLayout(self._detached_assistant_window)
        
        # Title
        title_label = QLabel('Assistant Chat')
        title_font = title_label.font()
        title_font.setPointSize(16)
        title_font.setBold(True)
        title_label.setFont(title_font)
        main_layout.addWidget(title_label)
        
        # Agent selector
        agent_layout = QHBoxLayout()
        agent_label = QLabel("Agent:")
        agent_layout.addWidget(agent_label)
        
        # Create agent combo for detached window (copies main window's agents)
        self._detached_agent_combo = QComboBox()
        for i in range(self.agent_combo.count()):
            self._detached_agent_combo.addItem(
                self.agent_combo.itemText(i),
                self.agent_combo.itemData(i)
            )
        agent_layout.addWidget(self._detached_agent_combo)
        
        # Scope selector
        scope_label = QLabel("Scope:")
        agent_layout.addWidget(scope_label)
        
        self._detached_scope_combo = QComboBox()
        for i in range(self.scope_combo.count()):
            self._detached_scope_combo.addItem(
                self.scope_combo.itemText(i),
                self.scope_combo.itemData(i)
            )
        agent_layout.addWidget(self._detached_scope_combo)
        
        agent_layout.addStretch()
        main_layout.addLayout(agent_layout)
        
        # Question input
        question_label = QLabel("Question:")
        main_layout.addWidget(question_label)
        
        self._detached_question_input = QTextEdit()
        self._detached_question_input.setPlaceholderText("Ask a question about your sessions...")
        self._detached_question_input.setMaximumHeight(80)
        main_layout.addWidget(self._detached_question_input)
        
        # Ask button
        button_layout = QHBoxLayout()
        
        self._detached_ask_button = QPushButton("Ask")
        self._detached_ask_button.clicked.connect(self._on_detached_ask_clicked)
        button_layout.addWidget(self._detached_ask_button)
        
        self._detached_new_chat_button = QPushButton("New Chat")
        self._detached_new_chat_button.clicked.connect(self._on_new_chat_clicked)
        button_layout.addWidget(self._detached_new_chat_button)
        
        button_layout.addStretch()
        main_layout.addLayout(button_layout)
        
        # Answer display
        answer_label = QLabel("Answer:")
        main_layout.addWidget(answer_label)
        
        self._detached_answer_display = QTextEdit()
        self._detached_answer_display.setReadOnly(True)
        self._detached_answer_display.setPlaceholderText("Assistant responses will appear here...")
        self._detached_answer_display.setMinimumHeight(150)
        main_layout.addWidget(self._detached_answer_display)
        
        # Candidate session selection (initially hidden) - same as main window
        self._detached_candidate_group = QGroupBox("Select a Session:")
        candidate_layout = QVBoxLayout()
        
        self._detached_candidate_list = QListWidget()
        self._detached_candidate_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self._detached_candidate_list.setMaximumHeight(100)
        self._detached_candidate_list.itemClicked.connect(self._on_detached_candidate_selected)
        candidate_layout.addWidget(self._detached_candidate_list)
        
        self._detached_use_candidate_button = QPushButton("Use Selected Session")
        self._detached_use_candidate_button.clicked.connect(self._on_detached_use_candidate)
        self._detached_use_candidate_button.setEnabled(False)
        candidate_layout.addWidget(self._detached_use_candidate_button)
        
        self._detached_candidate_group.setLayout(candidate_layout)
        self._detached_candidate_group.setVisible(False)
        main_layout.addWidget(self._detached_candidate_group)
        
        # Connect main window's answer display to also update detached window
        # This keeps both windows in sync when main window gets an answer
        
        self._detached_assistant_window.show()
    
    def _on_detached_ask_clicked(self):
        """Handle the Ask button click in the detached assistant window."""
        # Get the question from the detached input
        question = self._detached_question_input.toPlainText().strip()
        
        if not question:
            self._detached_answer_display.setPlainText("Please enter a question.")
            return
        
        # Clear previous candidates when asking a new question
        self._detached_candidate_group.setVisible(False)
        
        # Store the original question for potential retry
        self._current_question = question
        
        # Get selected agent
        agent_id = self._detached_agent_combo.currentData()
        
        # Get scope from combo
        scope_value = self._detached_scope_combo.currentData()
        explicit_scope = "current_session" if scope_value == "current" else "any_session"
        
        # Get active session ID if there's an active session
        active_session = self.session_manager.get_active_session()
        active_session_id = active_session.id if active_session else None
        
        # Get selected session ID from the sessions table
        selected_session_id = self._get_selected_session_id()
        
        # Disable the Ask button while processing
        self._detached_ask_button.setEnabled(False)
        self._detached_answer_display.setPlainText("Thinking...")
        
        # Run the assistant service call in the background
        QTimer.singleShot(50, lambda: self._run_detached_assistant_query(
            question=question,
            agent_id=agent_id,
            explicit_scope=explicit_scope,
            active_session_id=active_session_id,
            selected_session_id=selected_session_id,
            conversation_id=self._current_conversation_id
        ))
    
    def _run_detached_assistant_query(
        self,
        question: str,
        agent_id: str,
        explicit_scope: str,
        active_session_id: Optional[int],
        selected_session_id: Optional[int],
        conversation_id: Optional[int]
    ):
        """Execute the assistant query in the background for detached window."""
        try:
            # Call the assistant service
            response = self.assistant_service.ask(
                question=question,
                agent_id=agent_id,
                explicit_scope=explicit_scope,
                active_session_id=active_session_id,
                selected_session_id=selected_session_id,
                conversation_id=conversation_id
            )
            
            # Handle the response
            if response.success:
                # Clear candidates on successful answer
                self._detached_candidate_group.setVisible(False)
                self._detached_answer_display.setPlainText(response.answer or "")
                # Also update main window's answer display to keep them in sync
                self.answer_display.setPlainText(response.answer or "")
                # Save conversation_id for follow-up questions
                if response.conversation_id:
                    self._current_conversation_id = response.conversation_id
                self._on_status_update("Answer received")
            elif response.needs_clarification:
                # Show clarification question and candidates
                clarification_text = response.clarification_question or ""
                self._detached_answer_display.setPlainText(clarification_text)
                # Also show in main window
                self.answer_display.setPlainText(clarification_text)
                
                # Display candidates in detached window
                if response.candidates:
                    self._display_detached_candidates(response.candidates)
                else:
                    self._detached_candidate_group.setVisible(False)
                    
                self._on_status_update("Clarification needed")
            else:
                # Show error
                error_text = response.error or "Unknown error occurred"
                self._detached_answer_display.setPlainText(f"Error: {error_text}")
                self.answer_display.setPlainText(f"Error: {error_text}")
                self._on_status_update(f"Assistant error: {error_text}", is_error=True)
                # Clear candidates on error
                self._detached_candidate_group.setVisible(False)
                
        except Exception as e:
            logger.error(f"Assistant query failed (detached): {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            error_text = f"Error: {str(e)}"
            self._detached_answer_display.setPlainText(error_text)
            self.answer_display.setPlainText(error_text)
            self._on_status_update(f"Assistant error: {str(e)}", is_error=True)
            # Clear candidates on exception
            self._detached_candidate_group.setVisible(False)
        finally:
            # Re-enable the Ask button
            self._detached_ask_button.setEnabled(True)
    
    def _display_detached_candidates(self, candidates: list):
        """Display candidate sessions in the detached window for selection."""
        self._detached_candidate_list.clear()
        
        for candidate in candidates:
            # Format: "Session name - YYYY-MM-DD HH:MM"
            session_name = candidate.get('session_name', 'Unknown Session')
            start_time = candidate.get('start_time', '')
            display_text = f"{session_name} - {start_time}"
            self._detached_candidate_list.addItem(display_text)
        
        # Store candidates for later use
        self._current_candidates = candidates
        
        # Show the candidate group
        self._detached_candidate_group.setVisible(True)
    
    def _on_detached_candidate_selected(self, item):
        """Handle candidate selection in the detached window."""
        self._detached_use_candidate_button.setEnabled(True)
    
    def _on_detached_use_candidate(self):
        """Handle the Use Selected Session button in the detached window."""
        selected_index = self._detached_candidate_list.currentRow()
        
        if selected_index >= 0 and selected_index < len(self._current_candidates):
            selected_candidate = self._current_candidates[selected_index]
            selected_session_id = selected_candidate.get('session_id')
            
            # Retry the question with the selected session
            if selected_session_id and self._current_question:
                # Disable button during processing
                self._detached_use_candidate_button.setEnabled(False)
                self._detached_answer_display.setPlainText("Thinking...")
                
                agent_id = self._detached_agent_combo.currentData()
                scope_value = self._detached_scope_combo.currentData()
                explicit_scope = "current_session" if scope_value == "current" else "any_session"
                
                QTimer.singleShot(50, lambda: self._run_detached_assistant_query(
                    question=self._current_question,
                    agent_id=agent_id,
                    explicit_scope=explicit_scope,
                    active_session_id=None,  # Explicit session selected
                    selected_session_id=selected_session_id
                ))
    
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
