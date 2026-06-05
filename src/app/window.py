from PySide6.QtWidgets import (QMainWindow, QMenuBar, QWidget, QVBoxLayout, 
                                QHBoxLayout, QPushButton, QLabel, QStatusBar,
                                QMessageBox, QApplication, QListWidget, QGroupBox,
                                QListWidgetItem, QMenu, QTableWidget, QTableWidgetItem,
                                QHeaderView, QComboBox, QDialog, QTextBrowser, QScrollArea, 
                                QGridLayout, QSlider, QDialogButtonBox, QTextEdit, QCheckBox,
                                QFrame, QAbstractItemView, QSplitter, QLineEdit, QCompleter,
                                QToolButton)
from PySide6.QtCore import Qt, QTimer, QMetaObject, Slot, Q_ARG, QThread, Signal, QStringListModel
from PySide6.QtGui import QAction, QPixmap, QColor
from typing import Optional
import logging

from .session_manager import SessionManager
from .session import Session
from ..summarization import SummaryGenerator
from ..config import ASSISTANT_AGENTS, SESSION, ALLOWED_MODELS, get_selected_model, set_selected_model
from ..assistant.service import AssistantAnswerService
from ..screenshots.context_generator import ScreenshotContextGenerator

logger = logging.getLogger(__name__)


class ScreenshotContextThread(QThread):
    """Thread for running screenshot context generation asynchronously."""
    
    # Signals to communicate with the main thread
    finished_signal = Signal(object)  # Emits the context dict
    error_signal = Signal(str)  # Emits error message
    
    def __init__(self, screenshot_path, summary, transcript_excerpt, db):
        super().__init__()
        self.screenshot_path = screenshot_path
        self.summary = summary
        self.transcript_excerpt = transcript_excerpt
        self.db = db
    
    def run(self):
        """Run the screenshot context generation in a separate thread."""
        try:
            context_gen = ScreenshotContextGenerator(database=self.db)
            context = context_gen.generate_context(
                screenshot_path=self.screenshot_path,
                summary=self.summary,
                transcript_excerpt=self.transcript_excerpt,
                store=True
            )
            self.finished_signal.emit(context)
        except Exception as e:
            self.error_signal.emit(str(e))


class ScreenshotContextBatchThread(QThread):
    """Thread for running screenshot context generation for multiple screenshots sequentially."""
    
    # Signals to communicate with the main thread
    progress_signal = Signal(int, int, object, str)  # current, total, context/None, filepath
    finished_signal = Signal(list)  # Emits list of (context, filepath) tuples
    
    def __init__(self, screenshots_data, summary, db):
        """
        Args:
            screenshots_data: List of dicts with 'filepath' and 'timestamp' keys
            summary: Session summary string
            db: Database instance
        """
        super().__init__()
        self.screenshots_data = screenshots_data
        self.summary = summary
        self.db = db
    
    def run(self):
        """Run the screenshot context generation for all screenshots sequentially."""
        results = []
        
        try:
            context_gen = ScreenshotContextGenerator(database=self.db)
            total = len(self.screenshots_data)
            
            for idx, screenshot in enumerate(self.screenshots_data):
                filepath = screenshot.get('filepath', '')
                screenshot_timestamp = screenshot.get('timestamp', 0)
                
                if not filepath:
                    self.progress_signal.emit(idx + 1, total, None, filepath)
                    continue
                
                # Get transcript excerpt for this screenshot
                transcript_excerpt = ""
                all_transcripts = self.db.get_transcripts(screenshot.get('session_id', 0))
                if all_transcripts:
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
                        summary=self.summary if self.summary else None,
                        transcript_excerpt=transcript_excerpt if transcript_excerpt else None,
                        store=True
                    )
                    results.append((context, filepath))
                    self.progress_signal.emit(idx + 1, total, context, filepath)
                except Exception as e:
                    logger.warning(f"Failed to generate context for {filepath}: {e}")
                    self.progress_signal.emit(idx + 1, total, None, filepath)
            
            self.finished_signal.emit(results)
            
        except Exception as e:
            logger.error(f"Batch screenshot context generation failed: {e}")
            self.finished_signal.emit(results)


class AssistantQueryThread(QThread):
    """Thread for running assistant queries asynchronously."""
    
    # Signals to communicate with the main thread
    finished_signal = Signal(object)  # Emits the AnswerResponse
    error_signal = Signal(str)  # Emits error message
    
    def __init__(self, assistant_service, question, agent_id, explicit_scope, active_session_id, selected_session_id, conversation_id):
        super().__init__()
        self.assistant_service = assistant_service
        self.question = question
        self.agent_id = agent_id
        self.explicit_scope = explicit_scope
        self.active_session_id = active_session_id
        self.selected_session_id = selected_session_id
        self.conversation_id = conversation_id
    
    def run(self):
        """Run the async assistant query in a separate thread."""
        import asyncio
        
        async def run_query():
            return await self.assistant_service.ask_async(
                question=self.question,
                agent_id=self.agent_id,
                explicit_scope=self.explicit_scope,
                active_session_id=self.active_session_id,
                selected_session_id=self.selected_session_id,
                conversation_id=self.conversation_id
            )
        
        try:
            # Run the async function in a new event loop
            response = asyncio.run(run_query())
            self.finished_signal.emit(response)
        except Exception as e:
            self.error_signal.emit(str(e))


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
        self._thinking_message_widget = None  # Track "Thinking..." message for replacement
        self._assistant_thread = None  # Track active assistant query thread
        
        # Session search components
        self._recent_sessions = []  # Store sessions for dropdown
        
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
            self._load_past_conversations()
            self._refresh_session_completer()
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
    
    def _load_past_conversations(self):
        """Load and display past conversations from the database."""
        try:
            conversations = self.session_manager.db.list_conversations()
            
            self.conversations_list.clear()
            
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
                    display_text += " (Session-specific)"
                else:
                    display_text += " (All sessions)"
                
                item = QListWidgetItem(display_text)
                item.setData(Qt.UserRole, conv_id)
                self.conversations_list.addItem(item)
            
        except Exception as e:
            logger.warning(f"Failed to load past conversations: {str(e)}")
    
    def _on_conversation_selected(self, item):
        """Handle the selection of a conversation from the list."""
        conv_id = item.data(Qt.UserRole)
        if conv_id is None:
            return
        
        # Load this conversation in the assistant panel
        # First, get the conversation messages
        try:
            messages = self.session_manager.db.get_messages(conv_id)
            
            # Clear the current answer display
            self._answer_container = QWidget()
            self._answer_layout = QVBoxLayout(self._answer_container)
            self._answer_layout.setSpacing(10)
            self._answer_layout.setContentsMargins(5, 5, 5, 5)
            self._answer_layout.addStretch()
            
            # Display messages in the conversation view using the same method as new messages
            for msg in messages:
                role = msg.get('role', 'unknown')
                content = msg.get('content', '')
                
                # Use the same method as new messages to ensure proper formatting
                self._add_message_to_conversation(role, content)
            
            # Update the scroll area
            self._answer_scroll_area.setWidget(self._answer_container)
            
            # Store conversation ID for follow-up questions
            self._current_conversation_id = conv_id
            
            self._on_status_update(f"Loaded conversation #{conv_id}")
            
        except Exception as e:
            logger.error(f"Failed to load conversation: {str(e)}")
            self._on_status_update(f"Error loading conversation: {str(e)}", is_error=True)
    
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
            self._refresh_session_completer()
    
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
            self._refresh_session_completer()
            
        except Exception as e:
            logger.error(f"Transcription failed: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            self._on_status_update(f"Transcription failed: {str(e)}", is_error=True)
            QMessageBox.warning(self, 'Transcription Failed', str(e))
            # Reload to reset button state
            self._refresh_session_completer()
    
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
            self._refresh_session_completer()
    
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
                self._refresh_session_completer()
                return
            
            # Combine all transcript text
            full_transcript = ' '.join(
                t.get('text', '') for t in transcripts if t.get('text')
            )
            
            if not full_transcript.strip():
                self._on_status_update('No transcript text found')
                QMessageBox.warning(self, 'No Transcript Text', 'Transcripts are empty.')
                self._refresh_session_completer()
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
            
            # Update summary icon state to reflect the new summary
            self._update_summary_icon_state()
            
            # Reload the sessions list to update UI
            self._refresh_session_completer()
            
        except Exception as e:
            logger.error(f"Summarization failed: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            self._on_status_update(f"Summarization failed: {str(e)}", is_error=True)
            QMessageBox.warning(self, 'Summarization Failed', str(e))
            # Reload to reset button state
            self._refresh_session_completer()
    
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
            self._on_status_update(f"Error deleting session: {str(e)}", is_error=True)
    
    def _delete_session_by_id(self, session_id: int):
        """Delete a session by its ID.
        
        Args:
            session_id: The session ID to delete
        """
        try:
            # Get session name first
            sessions = self.session_manager.db.list_sessions()
            session_name = None
            for s in sessions:
                if s['id'] == session_id:
                    session_name = s['name']
                    break
            
            if session_name is None:
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
                
                logger.info(f"Deleted session {session_id} ('{session_name}')")
                self._on_status_update(f"Session '{session_name}' deleted.")
                
                # Refresh completer
                self._refresh_session_completer()
                
        except Exception as e:
            logger.error(f"Failed to delete session: {str(e)}")
            self._on_status_update(f"Error deleting session: {str(e)}", is_error=True)
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
    
    def _show_summary_by_session_id(self, session_id: int, session_name: str):
        """Show the summary for a session by session_id.
        
        Args:
            session_id: The session ID
            session_name: The session name
        """
        try:
            # Check if session has a summary
            summaries = self.session_manager.db.get_summaries(session_id)
            
            if not summaries:
                QMessageBox.information(
                    self,
                    'No Summary',
                    f"Session '{session_name}' does not have a summary yet.\n\n"
                    "Please transcribe and summarize the session first."
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
            QMessageBox.critical(
                self,
                'Error',
                f"Failed to load summary: {str(e)}"
            )
    
    def _show_screenshots_by_session_id(self, session_id: int, session_name: str):
        """Show screenshots for a session by session_id.
        
        Args:
            session_id: The session ID
            session_name: The session name
        """
        try:
            # Reuse the same rich screenshot dialog (with context generator)
            screenshots = self.session_manager.db.get_screenshots(session_id)

            if not screenshots:
                QMessageBox.information(
                    self,
                    'No Screenshots',
                    f"Session '{session_name}' does not have any screenshots."
                )
                return

            dialog = QDialog(self)
            dialog.setWindowTitle(f"Screenshots - {session_name}")
            dialog.setMinimumSize(800, 600)

            layout = QVBoxLayout(dialog)

            header_label = QLabel(f"Screenshots for: {session_name}")
            header_font = header_label.font()
            header_font.setPointSize(14)
            header_font.setBold(True)
            header_label.setFont(header_font)
            layout.addWidget(header_label)

            scroll_area = QScrollArea()
            scroll_area.setWidgetResizable(True)

            grid_widget = QWidget()
            grid_layout = QGridLayout(grid_widget)
            grid_layout.setSpacing(10)

            screenshot_labels = []
            time_labels = []
            selected_screenshot_index = [None]

            def get_context_for_screenshot(idx):
                if idx is None or idx >= len(screenshots):
                    return ""
                screenshot = screenshots[idx]
                ai_summary = screenshot.get('ai_summary', '')
                visible_text = screenshot.get('visible_text', '')
                keywords = screenshot.get('keywords', '')

                if not ai_summary:
                    return ""

                import json, os
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
                filename = os.path.basename(screenshot.get('filepath', ''))

                return f"""Screenshot: {filename}
Summary: {ai_summary}
Visible Text: {visible_text_str}
Keywords: {keywords_str}"""

            def update_selection(new_index):
                old_index = selected_screenshot_index[0]
                if old_index is not None and old_index < len(screenshot_labels):
                    screenshot_labels[old_index].setStyleSheet("")
                    time_labels[old_index].setStyleSheet("")

                selected_screenshot_index[0] = new_index

                if new_index is not None:
                    screenshot_labels[new_index].setStyleSheet("border: 3px solid #0078d4;")
                    time_labels[new_index].setStyleSheet("border: 3px solid #0078d4; border-top: none;")
                    context = get_context_for_screenshot(new_index)
                    if context:
                        context_text.setPlainText(context)
                    else:
                        context_text.setPlainText("No context available for this screenshot. Click 'Give Context to Selected' to generate it.")
                else:
                    context_text.setPlainText("")

            def on_screenshot_clicked(idx):
                update_selection(idx)
                if has_summary and idx is not None:
                    give_context_selected_button.setEnabled(True)
                # Enable delete button when a screenshot is selected
                delete_button.setEnabled(idx is not None)

            from datetime import datetime
            for idx, screenshot in enumerate(screenshots):
                filepath = screenshot.get('filepath', '')
                timestamp = screenshot.get('timestamp', 0)

                try:
                    dt = datetime.fromtimestamp(timestamp) if timestamp else None
                    time_str = dt.strftime('%H:%M:%S') if dt else ''
                except Exception:
                    time_str = ''

                container = QWidget()
                container_layout = QVBoxLayout(container)
                container_layout.setContentsMargins(0, 0, 0, 0)
                container_layout.setSpacing(2)

                image_label = QLabel()
                pixmap = QPixmap(filepath)
                if not pixmap.isNull():
                    scaled_pixmap = pixmap.scaled(300, 200, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                    image_label.setPixmap(scaled_pixmap)
                else:
                    image_label.setText(f"Failed to load image\n{filepath}")

                image_label.setAlignment(Qt.AlignCenter)
                image_label.setCursor(Qt.PointingHandCursor)

                screenshot_labels.append(image_label)

                image_label.mousePressEvent = lambda event, fp=filepath, ts=timestamp, i=idx: (
                    event.accept(),
                    on_screenshot_clicked(i)
                )

                image_label.mouseDoubleClickEvent = lambda event, fp=filepath, ts=timestamp: (
                    event.accept(),
                    self._show_full_image(fp, ts)
                )

                time_label = QLabel(f"Captured at: {time_str}")
                time_label.setAlignment(Qt.AlignCenter)
                time_labels.append(time_label)

                container_layout.addWidget(image_label)
                container_layout.addWidget(time_label)

                grid_layout.addWidget(container, idx // 2, idx % 2)

            scroll_area.setWidget(grid_widget)
            layout.addWidget(scroll_area)

            selection_hint = QLabel("Click on a screenshot to select it and view its context")
            selection_hint.setStyleSheet("color: gray; font-style: italic;")
            layout.addWidget(selection_hint)

            context_label = QLabel("Screenshot Context:")
            context_label.setFont(header_font)
            layout.addWidget(context_label)

            context_text = QTextEdit()
            context_text.setReadOnly(True)
            context_text.setMaximumHeight(100)

            summaries = self.session_manager.db.get_summaries(session_id)
            has_summary = bool(summaries and len(summaries) > 0)
            
            if has_summary:
                context_text.setPlaceholderText("Select a screenshot and click 'Give Context to Selected' to generate context, or 'Give Context to All' for all screenshots...")
            else:
                context_text.setPlaceholderText("Generate a summary first before generating screenshot context.")

            layout.addWidget(context_text)

            button_layout = QHBoxLayout()

            give_context_selected_button = QPushButton("Give Context to Selected")
            give_context_selected_button.setEnabled(False)
            if not has_summary:
                give_context_selected_button.setToolTip("Generate a summary first before generating screenshot context")
            else:
                give_context_selected_button.setToolTip("Generate context for the currently selected screenshot")

            give_context_all_button = QPushButton("Give Context to All")
            give_context_all_button.setEnabled(has_summary and len(screenshots) > 0)
            if not has_summary:
                give_context_all_button.setToolTip("Generate a summary first before generating screenshot context")

            close_button = QPushButton("Close")
            close_button.clicked.connect(dialog.close)

            delete_button = QPushButton("Delete Selected")
            delete_button.setEnabled(False)
            delete_button.setStyleSheet("color: red;")

            button_layout.addWidget(give_context_selected_button)
            button_layout.addWidget(give_context_all_button)
            button_layout.addStretch()
            button_layout.addWidget(delete_button)
            button_layout.addWidget(close_button)

            layout.addLayout(button_layout)

            # Handlers (copied from session-row implementation)
            # Store thread references for cleanup
            context_thread = [None]  # Use list to allow mutation in closure
            all_context_threads = [None]  # For "Give Context to All"

            def on_give_context_selected():
                selected_idx = selected_screenshot_index[0]
                if selected_idx is None:
                    QMessageBox.information(self, 'No Selection', 'Please select a screenshot first.')
                    return

                # Check if a thread is already running
                if context_thread[0] is not None and context_thread[0].isRunning():
                    return

                give_context_selected_button.setEnabled(False)
                give_context_selected_button.setText("Generating...")
                context_text.setPlainText("Generating context for selected screenshot...")
                QApplication.processEvents()

                # Get the screenshot data
                screenshot = screenshots[selected_idx]
                filepath = screenshot.get('filepath', '')
                screenshot_timestamp = screenshot.get('timestamp', 0)

                if not filepath:
                    context_text.setPlainText("Invalid screenshot filepath.")
                    give_context_selected_button.setEnabled(True)
                    give_context_selected_button.setText("Give Context to Selected")
                    return

                # Prepare parameters for the thread
                summary_content = ""
                if has_summary:
                    summary = summaries[0]
                    summary_content = summary.get('content', '')

                all_transcripts = self.session_manager.db.get_transcripts(session_id)

                transcript_excerpt = ""
                if all_transcripts:
                    sorted_transcripts = sorted(all_transcripts, key=lambda t: abs(t.get('timestamp', 0) - screenshot_timestamp))
                    nearest_2 = sorted_transcripts[:2]
                    transcript_excerpt = " | ".join(t.get('text', '')[:200] for t in nearest_2 if t.get('text'))

                # Create and configure the thread
                thread = ScreenshotContextThread(
                    screenshot_path=filepath,
                    summary=summary_content if summary_content else None,
                    transcript_excerpt=transcript_excerpt if transcript_excerpt else None,
                    db=self.session_manager.db
                )

                def on_context_finished(context):
                    try:
                        ai_summary = context.get('summary', 'N/A')
                        visible_text = context.get('visible_text', [])
                        keywords = context.get('keywords', [])

                        if isinstance(visible_text, list):
                            visible_text_str = ", ".join(visible_text) if visible_text else "None"
                        else:
                            visible_text_str = str(visible_text) if visible_text else "None"

                        if isinstance(keywords, list):
                            keywords_str = ", ".join(keywords) if keywords else "None"
                        else:
                            keywords_str = str(keywords) if keywords else "None"

                        import os
                        filename = os.path.basename(filepath)

                        context_text.setPlainText(f"""Screenshot: {filename}
Summary: {ai_summary}
Visible Text: {visible_text_str}
Keywords: {keywords_str}""")

                        self._on_status_update(f"Generated context for selected screenshot")
                    except Exception as e:
                        logger.error(f"Error processing context result: {e}")
                        context_text.setPlainText(f"Error processing result: {str(e)}")
                    finally:
                        give_context_selected_button.setEnabled(True)
                        give_context_selected_button.setText("Give Context to Selected")
                        context_thread[0] = None

                def on_context_error(error_msg):
                    logger.error(f"Failed to generate screenshot context: {error_msg}")
                    context_text.setPlainText(f"Error generating context: {error_msg}")
                    QMessageBox.warning(self, 'Context Generation Failed', error_msg)
                    give_context_selected_button.setEnabled(True)
                    give_context_selected_button.setText("Give Context to Selected")
                    context_thread[0] = None

                # Connect signals and start thread
                thread.finished_signal.connect(on_context_finished)
                thread.error_signal.connect(on_context_error)
                context_thread[0] = thread
                thread.start()

            def on_give_context_all():
                # Check if a thread is already running
                if all_context_threads[0] is not None and all_context_threads[0].isRunning():
                    return

                give_context_all_button.setEnabled(False)
                give_context_all_button.setText("Generating...")
                context_text.setPlainText("Generating context for all screenshots...")
                QApplication.processEvents()

                # Prepare common data
                summary_content = ""
                if has_summary:
                    summary = summaries[0]
                    summary_content = summary.get('content', '')

                # Prepare screenshots data for batch thread
                screenshots_data = [
                    {
                        'filepath': s.get('filepath', ''),
                        'timestamp': s.get('timestamp', 0),
                        'session_id': session_id
                    }
                    for s in screenshots
                ]

                # Create batch thread (processes sequentially)
                batch_thread = ScreenshotContextBatchThread(
                    screenshots_data=screenshots_data,
                    summary=summary_content,
                    db=self.session_manager.db
                )

                context_display_parts = []

                def on_progress(current, total, context, filepath):
                    if context is not None:
                        try:
                            ai_summary = context.get('summary', 'N/A')
                            visible_text = context.get('visible_text', [])
                            keywords = context.get('keywords', [])

                            if isinstance(visible_text, list):
                                visible_text_str = ", ".join(visible_text) if visible_text else "None"
                            else:
                                visible_text_str = str(visible_text) if visible_text else "None"

                            if isinstance(keywords, list):
                                keywords_str = ", ".join(keywords) if keywords else "None"
                            else:
                                keywords_str = str(keywords) if keywords else "None"

                            import os
                            filename = os.path.basename(filepath)

                            screenshot_entry = f"""Screenshot: {filename}
Summary: {ai_summary}
Visible Text: {visible_text_str}
Keywords: {keywords_str}"""

                            context_display_parts.append(screenshot_entry)
                        except Exception as e:
                            logger.error(f"Error processing context result: {e}")
                            import os
                            context_display_parts.append(f"[Error for {os.path.basename(filepath)}: {str(e)}]")
                    else:
                        import os
                        context_display_parts.append(f"[Error for {os.path.basename(filepath)}: Failed to generate]")

                    # Update UI with current progress
                    context_text.setPlainText(f"Processed {current}/{total} screenshots...\n\n" + "\n\n".join(context_display_parts))
                    QApplication.processEvents()

                def on_batch_finished(results):
                    if context_display_parts:
                        context_text.setPlainText("\n\n".join(context_display_parts))
                        self._on_status_update(f"Generated context for {len(context_display_parts)} screenshot(s)")
                    else:
                        context_text.setPlainText("No context could be generated.")
                    give_context_all_button.setEnabled(True)
                    give_context_all_button.setText("Give Context to All")
                    all_context_threads[0] = None

                batch_thread.progress_signal.connect(on_progress)
                batch_thread.finished_signal.connect(on_batch_finished)
                all_context_threads[0] = batch_thread
                batch_thread.start()

            def on_delete_screenshot():
                """Delete the selected screenshot from database and file system."""
                selected_idx = selected_screenshot_index[0]
                if selected_idx is None:
                    return

                screenshot = screenshots[selected_idx]
                filepath = screenshot.get('filepath', '')
                screenshot_id = screenshot.get('id')

                if not filepath:
                    return

                reply = QMessageBox.question(
                    self,
                    'Delete Screenshot',
                    f"Are you sure you want to delete this screenshot?\n\n{filepath}\n\nThis will remove it from the database and delete the file.",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No
                )

                if reply != QMessageBox.Yes:
                    return

                try:
                    # Delete from database
                    if screenshot_id:
                        self.session_manager.db.delete_screenshot(screenshot_id)
                    else:
                        self.session_manager.db.delete_screenshot_by_filepath(filepath)

                    # Delete file from filesystem
                    import os
                    if os.path.exists(filepath):
                        os.remove(filepath)
                        logger.info(f"Deleted screenshot file: {filepath}")
                    else:
                        logger.warning(f"Screenshot file not found: {filepath}")

                    self._on_status_update(f"Screenshot deleted")

                    # Close the dialog and refresh
                    dialog.close()

                    # Reopen the screenshots dialog to refresh the list
                    self._show_screenshots_by_session_id(session_id, session_name)

                except Exception as e:
                    logger.error(f"Failed to delete screenshot: {e}")
                    import traceback
                    logger.error(traceback.format_exc())
                    QMessageBox.warning(self, 'Delete Failed', f"Failed to delete screenshot: {str(e)}")

            delete_button.clicked.connect(on_delete_screenshot)
            give_context_selected_button.clicked.connect(on_give_context_selected)
            give_context_all_button.clicked.connect(on_give_context_all)

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

    def _show_full_image(self, filepath: str, timestamp):
        """Show a full-size image in a dialog for the given filepath and timestamp.

        Args:
            filepath: Path to the image file
            timestamp: Capture timestamp (int/float epoch or ISO string)
        """
        try:
            from datetime import datetime
            from PySide6.QtWidgets import QApplication, QScrollArea

            # Create dialog with window controls
            dialog = QDialog(self)
            dialog.setWindowTitle("Screenshot")
            dialog.setWindowFlags(dialog.windowFlags() | Qt.WindowMinMaxButtonsHint)

            # Get screen size (fall back to sensible defaults)
            screen = QApplication.primaryScreen()
            if screen:
                screen_geometry = screen.availableGeometry()
            else:
                # Use a default size object if primaryScreen not available
                class _G:
                    def width(self):
                        return 1280
                    def height(self):
                        return 800
                screen_geometry = _G()

            # Default to 50% of screen
            try:
                width = int(screen_geometry.width() * 0.5)
                height = int(screen_geometry.height() * 0.5)
            except Exception:
                width, height = 800, 600

            dialog.resize(width, height)

            # Center the window
            try:
                dialog.move(int((screen_geometry.width() - width) / 2),
                            int((screen_geometry.height() - height) / 2))
            except Exception:
                pass

            layout = QVBoxLayout(dialog)
            layout.setContentsMargins(5, 5, 5, 5)

            # Timestamp label (if available)
            time_str = ''
            if timestamp:
                try:
                    if isinstance(timestamp, (int, float)):
                        dt = datetime.fromtimestamp(timestamp)
                    elif isinstance(timestamp, str):
                        try:
                            dt = datetime.fromisoformat(timestamp)
                        except Exception:
                            # Try numeric string
                            dt = datetime.fromtimestamp(float(timestamp))
                    else:
                        dt = None

                    if dt:
                        time_str = dt.strftime('%Y-%m-%d %H:%M:%S')
                except Exception:
                    try:
                        time_str = str(timestamp)
                    except Exception:
                        time_str = ''

            if time_str:
                time_label = QLabel(f"Captured at: {time_str}")
                time_label.setAlignment(Qt.AlignCenter)
                layout.addWidget(time_label)

            # Image with scroll area for zooming/panning
            scroll_area = QScrollArea()
            scroll_area.setWidgetResizable(True)
            try:
                scroll_area.setAlignment(Qt.AlignCenter)
            except Exception:
                # Older Qt versions may not support setAlignment on QScrollArea
                pass

            image_label = QLabel()
            image_label.setAlignment(Qt.AlignCenter)
            pixmap = QPixmap(filepath)

            if not pixmap.isNull():
                # Scale image to fit in the scroll area
                try:
                    image_label.setPixmap(pixmap.scaled(
                        screen_geometry.width(),
                        screen_geometry.height(),
                        Qt.KeepAspectRatio,
                        Qt.SmoothTransformation
                    ))
                except Exception:
                    image_label.setPixmap(pixmap)
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
            
            # Store references to screenshot widgets for selection highlighting
            screenshot_labels = []
            time_labels = []
            selected_screenshot_index = [None]  # Use list to allow mutation in closure
            
            def get_context_for_screenshot(idx):
                """Get formatted context string for a specific screenshot."""
                if idx is None or idx >= len(screenshots):
                    return ""
                screenshot = screenshots[idx]
                ai_summary = screenshot.get('ai_summary', '')
                visible_text = screenshot.get('visible_text', '')
                keywords = screenshot.get('keywords', '')
                
                if not ai_summary:
                    return ""
                
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
                
                # Get just the filename for display
                import os
                filename = os.path.basename(screenshot.get('filepath', ''))
                
                return f"""Screenshot: {filename}
Summary: {ai_summary}
Visible Text: {visible_text_str}
Keywords: {keywords_str}"""
            
            def update_selection(new_index):
                """Update the selected screenshot and refresh the display."""
                # Remove highlight from previous selection
                old_index = selected_screenshot_index[0]
                if old_index is not None and old_index < len(screenshot_labels):
                    screenshot_labels[old_index].setStyleSheet("")
                    time_labels[old_index].setStyleSheet("")
                
                # Set new selection
                selected_screenshot_index[0] = new_index
                
                if new_index is not None:
                    # Add highlight to new selection
                    screenshot_labels[new_index].setStyleSheet("border: 3px solid #0078d4;")
                    time_labels[new_index].setStyleSheet("border: 3px solid #0078d4; border-top: none;")
                    
                    # Show context for selected screenshot
                    context = get_context_for_screenshot(new_index)
                    if context:
                        context_text.setPlainText(context)
                    else:
                        context_text.setPlainText("No context available for this screenshot. Click 'Give Context to Selected' to generate it.")
                else:
                    context_text.setPlainText("")
            
            def on_screenshot_clicked(idx):
                """Handle screenshot click - select it and show context."""
                update_selection(idx)
                # Enable the "Give Context to Selected" button when a screenshot is selected
                if has_summary and idx is not None:
                    give_context_selected_button.setEnabled(True)
                # Enable delete button when a screenshot is selected
                delete_button.setEnabled(idx is not None)
            
            # Add screenshots to the grid
            from datetime import datetime
            for idx, screenshot in enumerate(screenshots):
                filepath = screenshot.get('filepath', '')
                timestamp = screenshot.get('timestamp', 0)
                
                # Convert timestamp to readable format
                dt = datetime.fromtimestamp(timestamp)
                time_str = dt.strftime('%H:%M:%S')
                
                # Create container widget for screenshot and timestamp
                container = QWidget()
                container_layout = QVBoxLayout(container)
                container_layout.setContentsMargins(0, 0, 0, 0)
                container_layout.setSpacing(2)
                
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
                
                # Store reference for selection highlighting
                screenshot_labels.append(image_label)
                
                # Click to select (not fullscreen)
                image_label.mousePressEvent = lambda event, fp=filepath, ts=timestamp, i=idx: (
                    event.accept(),
                    on_screenshot_clicked(i)
                )
                
                # Double-click for fullscreen
                image_label.mouseDoubleClickEvent = lambda event, fp=filepath, ts=timestamp: (
                    event.accept(),
                    self._show_full_image(fp, ts)
                )
                
                # Timestamp label
                time_label = QLabel(f"Captured at: {time_str}")
                time_label.setAlignment(Qt.AlignCenter)
                time_labels.append(time_label)
                
                container_layout.addWidget(image_label)
                container_layout.addWidget(time_label)
                
                # Add to grid (2 columns)
                grid_layout.addWidget(container, idx // 2, idx % 2)
            
            scroll_area.setWidget(grid_widget)
            layout.addWidget(scroll_area)
            
            # Selection hint
            selection_hint = QLabel("Click on a screenshot to select it and view its context")
            selection_hint.setStyleSheet("color: gray; font-style: italic;")
            layout.addWidget(selection_hint)
            
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
            has_summary = bool(summaries and len(summaries) > 0)
            
            # Initial message based on summary availability
            if has_summary:
                context_text.setPlaceholderText("Select a screenshot and click 'Give Context to Selected' to generate context, or 'Give Context to All' for all screenshots...")
            else:
                context_text.setPlaceholderText("Generate a summary first before generating screenshot context.")
            
            layout.addWidget(context_text)
            
            # Buttons
            button_layout = QHBoxLayout()
            
            # Give Context to Selected button - requires a screenshot to be selected
            give_context_selected_button = QPushButton("Give Context to Selected")
            give_context_selected_button.setEnabled(False)  # Disabled until a screenshot is selected
            
            if not has_summary:
                give_context_selected_button.setToolTip("Generate a summary first before generating screenshot context")
            else:
                give_context_selected_button.setToolTip("Generate context for the currently selected screenshot")
            
            # Give Context to All button - generates context for all screenshots
            give_context_all_button = QPushButton("Give Context to All")
            give_context_all_button.setEnabled(has_summary and len(screenshots) > 0)
            
            if not has_summary:
                give_context_all_button.setToolTip("Generate a summary first before generating screenshot context")

            # Delete button
            delete_button = QPushButton("Delete Selected")
            delete_button.setEnabled(False)
            delete_button.setStyleSheet("color: red;")

            # Close button
            close_button = QPushButton("Close")
            close_button.clicked.connect(dialog.close)

            button_layout.addWidget(give_context_selected_button)
            button_layout.addWidget(give_context_all_button)
            button_layout.addStretch()
            button_layout.addWidget(delete_button)
            button_layout.addWidget(close_button)
            
            layout.addLayout(button_layout)
            
            # Store references for the callbacks
            # Store thread references for cleanup
            context_thread = [None]  # Use list to allow mutation in closure
            all_context_threads = [None]  # For "Give Context to All"

            def on_give_context_selected():
                """Generate context for the selected screenshot."""
                selected_idx = selected_screenshot_index[0]
                if selected_idx is None:
                    QMessageBox.information(self, 'No Selection', 'Please select a screenshot first.')
                    return

                # Check if a thread is already running
                if context_thread[0] is not None and context_thread[0].isRunning():
                    return

                give_context_selected_button.setEnabled(False)
                give_context_selected_button.setText("Generating...")
                context_text.setPlainText("Generating context for selected screenshot...")
                QApplication.processEvents()

                # Get the screenshot data
                screenshot = screenshots[selected_idx]
                filepath = screenshot.get('filepath', '')
                screenshot_timestamp = screenshot.get('timestamp', 0)

                if not filepath:
                    context_text.setPlainText("Invalid screenshot filepath.")
                    give_context_selected_button.setEnabled(True)
                    give_context_selected_button.setText("Give Context to Selected")
                    return

                # Prepare parameters for the thread
                summary_content = ""
                if has_summary:
                    summary = summaries[0]
                    summary_content = summary.get('content', '')

                all_transcripts = self.session_manager.db.get_transcripts(session_id)

                transcript_excerpt = ""
                if all_transcripts:
                    sorted_transcripts = sorted(
                        all_transcripts,
                        key=lambda t: abs(t.get('timestamp', 0) - screenshot_timestamp)
                    )
                    nearest_2 = sorted_transcripts[:2]
                    transcript_excerpt = " | ".join(
                        t.get('text', '')[:200] for t in nearest_2 if t.get('text')
                    )

                # Create and configure the thread
                thread = ScreenshotContextThread(
                    screenshot_path=filepath,
                    summary=summary_content if summary_content else None,
                    transcript_excerpt=transcript_excerpt if transcript_excerpt else None,
                    db=self.session_manager.db
                )

                def on_context_finished(context):
                    try:
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

                        # Get just the filename for display
                        import os
                        filename = os.path.basename(filepath)

                        context_text.setPlainText(f"""Screenshot: {filename}
Summary: {ai_summary}
Visible Text: {visible_text_str}
Keywords: {keywords_str}""")

                        self._on_status_update(f"Generated context for selected screenshot")
                    except Exception as e:
                        logger.error(f"Error processing context result: {e}")
                        context_text.setPlainText(f"Error processing result: {str(e)}")
                    finally:
                        give_context_selected_button.setEnabled(True)
                        give_context_selected_button.setText("Give Context to Selected")
                        context_thread[0] = None

                def on_context_error(error_msg):
                    logger.error(f"Failed to generate screenshot context: {error_msg}")
                    context_text.setPlainText(f"Error generating context: {error_msg}")
                    QMessageBox.warning(self, 'Context Generation Failed', error_msg)
                    give_context_selected_button.setEnabled(True)
                    give_context_selected_button.setText("Give Context to Selected")
                    context_thread[0] = None

                # Connect signals and start thread
                thread.finished_signal.connect(on_context_finished)
                thread.error_signal.connect(on_context_error)
                context_thread[0] = thread
                thread.start()

            def on_give_context_all():
                """Generate context for all screenshots."""
                # Check if a thread is already running
                if all_context_threads[0] is not None and all_context_threads[0].isRunning():
                    return

                give_context_all_button.setEnabled(False)
                give_context_all_button.setText("Generating...")
                context_text.setPlainText("Generating context for all screenshots...")
                QApplication.processEvents()

                # Prepare common data
                summary_content = ""
                if has_summary:
                    summary = summaries[0]
                    summary_content = summary.get('content', '')

                # Prepare screenshots data for batch thread
                screenshots_data = [
                    {
                        'filepath': s.get('filepath', ''),
                        'timestamp': s.get('timestamp', 0),
                        'session_id': session_id
                    }
                    for s in screenshots
                ]

                # Create batch thread (processes sequentially)
                batch_thread = ScreenshotContextBatchThread(
                    screenshots_data=screenshots_data,
                    summary=summary_content,
                    db=self.session_manager.db
                )

                context_display_parts = []

                def on_progress(current, total, context, filepath):
                    if context is not None:
                        try:
                            ai_summary = context.get('summary', 'N/A')
                            visible_text = context.get('visible_text', [])
                            keywords = context.get('keywords', [])

                            if isinstance(visible_text, list):
                                visible_text_str = ", ".join(visible_text) if visible_text else "None"
                            else:
                                visible_text_str = str(visible_text) if visible_text else "None"

                            if isinstance(keywords, list):
                                keywords_str = ", ".join(keywords) if keywords else "None"
                            else:
                                keywords_str = str(keywords) if keywords else "None"

                            import os
                            filename = os.path.basename(filepath)

                            screenshot_entry = f"""Screenshot: {filename}
Summary: {ai_summary}
Visible Text: {visible_text_str}
Keywords: {keywords_str}"""

                            context_display_parts.append(screenshot_entry)
                        except Exception as e:
                            logger.error(f"Error processing context result: {e}")
                            import os
                            context_display_parts.append(f"[Error for {os.path.basename(filepath)}: {str(e)}]")
                    else:
                        import os
                        context_display_parts.append(f"[Error for {os.path.basename(filepath)}: Failed to generate]")

                    # Update UI with current progress
                    context_text.setPlainText(f"Processed {current}/{total} screenshots...\n\n" + "\n\n".join(context_display_parts))
                    QApplication.processEvents()

                def on_batch_finished(results):
                    if context_display_parts:
                        context_text.setPlainText("\n\n".join(context_display_parts))
                        self._on_status_update(f"Generated context for {len(context_display_parts)} screenshot(s)")
                    else:
                        context_text.setPlainText("No context could be generated.")
                    give_context_all_button.setEnabled(True)
                    give_context_all_button.setText("Give Context to All")
                    all_context_threads[0] = None

                batch_thread.progress_signal.connect(on_progress)
                batch_thread.finished_signal.connect(on_batch_finished)
                all_context_threads[0] = batch_thread
                batch_thread.start()

            def on_delete_screenshot():
                """Delete the selected screenshot from database and file system."""
                selected_idx = selected_screenshot_index[0]
                if selected_idx is None:
                    return

                screenshot = screenshots[selected_idx]
                filepath = screenshot.get('filepath', '')
                screenshot_id = screenshot.get('id')

                if not filepath:
                    return

                reply = QMessageBox.question(
                    self,
                    'Delete Screenshot',
                    f"Are you sure you want to delete this screenshot?\n\n{filepath}\n\nThis will remove it from the database and delete the file.",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No
                )

                if reply != QMessageBox.Yes:
                    return

                try:
                    # Delete from database
                    if screenshot_id:
                        self.session_manager.db.delete_screenshot(screenshot_id)
                    else:
                        self.session_manager.db.delete_screenshot_by_filepath(filepath)

                    # Delete file from filesystem
                    import os
                    if os.path.exists(filepath):
                        os.remove(filepath)
                        logger.info(f"Deleted screenshot file: {filepath}")
                    else:
                        logger.warning(f"Screenshot file not found: {filepath}")

                    self._on_status_update(f"Screenshot deleted")

                    # Close the dialog and refresh
                    dialog.close()

                    # Reopen the screenshots dialog to refresh the list
                    self._show_session_screenshots(row)

                except Exception as e:
                    logger.error(f"Failed to delete screenshot: {e}")
                    import traceback
                    logger.error(traceback.format_exc())
                    QMessageBox.warning(self, 'Delete Failed', f"Failed to delete screenshot: {str(e)}")

            # Connect buttons to handlers
            delete_button.clicked.connect(on_delete_screenshot)
            give_context_selected_button.clicked.connect(on_give_context_selected)
            give_context_all_button.clicked.connect(on_give_context_all)
            
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
        
        # Main layout - use QSplitter for resizable panels
        # Three column split: left (history), center (chat/session), right (live transcription)
        main_splitter = QSplitter(Qt.Horizontal)
        main_splitter.setContentsMargins(10, 10, 10, 10)
        
        # Set initial stretch factors via QSplitter
        # Left panel: Past Conversations
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setSpacing(10)
        left_layout.setContentsMargins(0, 0, 0, 0)
        
        # Session action icon row - above Past Conversations
        icon_row_layout = QHBoxLayout()
        icon_row_layout.setSpacing(5)
        icon_row_layout.setContentsMargins(0, 0, 0, 5)
        
        # Play/Stop toggle button
        self.play_stop_button = QToolButton()
        self.play_stop_button.setText("▶")
        self.play_stop_button.setToolTip("Start/Stop Session")
        self.play_stop_button.setMinimumSize(40, 40)
        self.play_stop_button.clicked.connect(self._on_play_stop_clicked)
        icon_row_layout.addWidget(self.play_stop_button)
        
        # Pause/Resume button (only visible when session is active)
        self.pause_icon_button = QToolButton()
        self.pause_icon_button.setText("⏸")
        self.pause_icon_button.setToolTip("Pause/Resume Session")
        self.pause_icon_button.setMinimumSize(40, 40)
        self.pause_icon_button.clicked.connect(self._on_pause_resume_session)
        self.pause_icon_button.setVisible(False)
        icon_row_layout.addWidget(self.pause_icon_button)
        
        # Screenshot button
        self.screenshot_icon_button = QToolButton()
        self.screenshot_icon_button.setText("📷")
        self.screenshot_icon_button.setToolTip("Take Screenshot")
        self.screenshot_icon_button.setMinimumSize(40, 40)
        self.screenshot_icon_button.clicked.connect(self._on_take_screenshot)
        icon_row_layout.addWidget(self.screenshot_icon_button)
        
        # View Screenshots button
        self.view_screenshots_icon_button = QToolButton()
        self.view_screenshots_icon_button.setText("🖼")
        self.view_screenshots_icon_button.setToolTip("View Screenshots")
        self.view_screenshots_icon_button.setMinimumSize(40, 40)
        self.view_screenshots_icon_button.clicked.connect(self._on_view_screenshots_icon_clicked)
        icon_row_layout.addWidget(self.view_screenshots_icon_button)
        
        # View Summary button
        self.view_summary_icon_button = QToolButton()
        self.view_summary_icon_button.setText("📝")
        self.view_summary_icon_button.setToolTip("View Summary")
        self.view_summary_icon_button.setMinimumSize(40, 40)
        self.view_summary_icon_button.clicked.connect(self._on_view_summary_icon_clicked)
        icon_row_layout.addWidget(self.view_summary_icon_button)
        
        icon_row_layout.addStretch()
        left_layout.addLayout(icon_row_layout)
        
        # Past Conversations list
        conversations_group = QGroupBox('Past Conversations')
        conversations_layout = QVBoxLayout()
        
        self.conversations_list = QListWidget()
        self.conversations_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.conversations_list.itemClicked.connect(self._on_conversation_selected)
        conversations_layout.addWidget(self.conversations_list)
        
        # Refresh button
        refresh_conv_button = QPushButton("Refresh")
        refresh_conv_button.clicked.connect(self._load_past_conversations)
        conversations_layout.addWidget(refresh_conv_button)
        
        conversations_group.setLayout(conversations_layout)
        left_layout.addWidget(conversations_group)
        
        # Center panel: Current Chat/Current Session Area
        center_panel = QWidget()
        center_layout = QVBoxLayout(center_panel)
        center_layout.setSpacing(15)
        center_layout.setContentsMargins(0, 0, 0, 0)
        
        # Title
        title_label = QLabel('Chronicle')
        title_label.setAlignment(Qt.AlignCenter)
        title_font = title_label.font()
        title_font.setPointSize(24)
        title_font.setBold(True)
        title_label.setFont(title_font)
        center_layout.addWidget(title_label)
        
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
        center_layout.addLayout(session_layout)
        
        # Spacer
        center_layout.addStretch()
        
        # Live transcription checkbox
        self.live_transcription_checkbox = QCheckBox('Enable live transcription')
        self.live_transcription_checkbox.setChecked(True)
        center_layout.addWidget(self.live_transcription_checkbox)
        
        # Auto summary checkbox
        self.auto_summary_checkbox = QCheckBox('Auto-generate summary after session stop')
        self.auto_summary_checkbox.setChecked(SESSION.get('auto_summary_after_stop', False))
        self.auto_summary_checkbox.toggled.connect(lambda checked: SESSION.__setitem__('auto_summary_after_stop', checked))
        center_layout.addWidget(self.auto_summary_checkbox)
        
        # Spacer
        center_layout.addStretch()
        
        # Status display
        self.status_label = QLabel('Ready')
        self.status_label.setAlignment(Qt.AlignCenter)
        status_font = self.status_label.font()
        status_font.setPointSize(16)
        self.status_label.setFont(status_font)
        center_layout.addWidget(self.status_label)
        
        session_search_layout = QHBoxLayout()
        session_search_label = QLabel("Search Session:")
        session_search_layout.addWidget(session_search_label)
        
        # Session search with completer (autocomplete)
        self.session_search_input = QLineEdit()
        self.session_search_input.setPlaceholderText("Type to search...")
        self.session_search_input.setMinimumWidth(150)
        session_search_layout.addWidget(self.session_search_input)
        
        # Setup completer for session search
        self._session_completer = QCompleter()
        self._session_completer.setFilterMode(Qt.MatchContains)
        self._session_completer.setCaseSensitivity(Qt.CaseInsensitive)
        self._session_completer.setMaxVisibleItems(5)
        self._session_completer.activated.connect(self._on_session_completer_selected)
        self.session_search_input.setCompleter(self._session_completer)
        
        # Button to show all sessions in detached window
        self.show_all_sessions_button = QPushButton("All Sessions")
        self.show_all_sessions_button.clicked.connect(self._show_all_sessions_window)
        session_search_layout.addWidget(self.show_all_sessions_button)
        
        # Load sessions for completer
        self._refresh_session_completer()
        
        center_layout.addLayout(session_search_layout)
        
        # Scope label - shows selected session name
        self._scope_label = QLabel("Scope: (none)")
        self._scope_label.setStyleSheet("color: gray; font-style: italic;")
        center_layout.addWidget(self._scope_label)
        
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
        
        # Answer display (read-only) - shown at the top, scrollable for conversation history
        answer_label = QLabel("Answer:")
        assistant_layout.addWidget(answer_label)
        
        # Create scrollable conversation view
        self._answer_scroll_area = QScrollArea()
        self._answer_scroll_area.setWidgetResizable(True)
        self._answer_scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        
        # Container for conversation messages
        self._answer_container = QWidget()
        self._answer_layout = QVBoxLayout(self._answer_container)
        self._answer_layout.setSpacing(10)
        self._answer_layout.setContentsMargins(5, 5, 5, 5)
        self._answer_layout.addStretch()  # Push content to top
        
        self._answer_scroll_area.setWidget(self._answer_container)
        self._answer_scroll_area.setMaximumHeight(500)
        assistant_layout.addWidget(self._answer_scroll_area)
        
        # For backward compatibility, keep a reference (but we use the scroll area now)
        self.answer_display = None  # Will be replaced by conversation view
        
        # Question input - shown at the bottom
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
        
        assistant_group.setLayout(assistant_layout)
        center_layout.addWidget(assistant_group)
        
        # ========== RIGHT PANEL: Live Transcriptions (unchanged) ==========
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        
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
        
        # Add all three panels to the splitter
        main_splitter.addWidget(left_panel)
        main_splitter.addWidget(center_panel)
        main_splitter.addWidget(right_panel)
        
        # Set stretch factors: left=1, center=2, right=1
        main_splitter.setStretchFactor(0, 1)
        main_splitter.setStretchFactor(1, 2)
        main_splitter.setStretchFactor(2, 1)
        
        # Set the splitter as the central widget's layout
        central_layout = QVBoxLayout(central_widget)
        central_layout.setContentsMargins(0, 0, 0, 0)
        central_layout.addWidget(main_splitter)
    
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
    
    def _add_message_to_conversation(self, role: str, text: str):
        """Add a message to the conversation view.
        
        Args:
            role: 'user' or 'assistant'
            text: The message text
        
        Returns:
            The bubble_frame widget that was added (can be used to replace later)
        """
        if not hasattr(self, '_answer_layout') or not self._answer_layout:
            return None
        
        # If this is a "Thinking..." message and we already have one, remove the old one
        if text == "Thinking..." and self._thinking_message_widget:
            self._thinking_message_widget.deleteLater()
            self._thinking_message_widget = None
        
        # Create a bubble frame
        bubble_frame = QFrame()
        bubble_frame.setFrameShape(QFrame.StyledPanel)
        bubble_frame.setFrameShadow(QFrame.Raised)
        
        # Store reference if this is "Thinking..." message
        if text == "Thinking...":
            self._thinking_message_widget = bubble_frame
        
        # Store role as property for later retrieval
        bubble_frame.setProperty('role', role)
        # Store text as property for later retrieval
        bubble_frame.setProperty('message_text', text)
        
        # Set layout for the bubble
        bubble_layout = QVBoxLayout(bubble_frame)
        bubble_layout.setContentsMargins(10, 8, 10, 8)
        bubble_layout.setSpacing(4)
        
        # Header with role label
        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        
        role_label = QLabel(f"{'You' if role == 'user' else 'Assistant'}")
        role_font = role_label.font()
        role_font.setPointSize(10)
        role_font.setBold(True)
        role_label.setFont(role_font)
        
        header_layout.addWidget(role_label)
        header_layout.addStretch()
        bubble_layout.addLayout(header_layout)
        
        # Message text
        text_label = QLabel(text)
        text_label.setWordWrap(True)
        text_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        bubble_layout.addWidget(text_label)
        
        # Style based on role
        if role == 'user':
            # User - right aligned with blue-ish background
            bubble_frame.setStyleSheet("""
                QFrame {
                    background-color: #E3F2FD;
                    border-radius: 10px;
                    border: 1px solid #90CAF9;
                }
            """)
        else:
            # Assistant - left aligned with green-ish background
            bubble_frame.setStyleSheet("""
                QFrame {
                    background-color: #E8F5E9;
                    border-radius: 10px;
                    border: 1px solid #A5D6A7;
                }
            """)
        
        # Add to layout (before the stretch)
        self._answer_layout.insertWidget(
            self._answer_layout.count() - 1,  # Insert before stretch
            bubble_frame
        )
        
        # Auto-scroll to bottom to show new message
        self._answer_scroll_area.verticalScrollBar().setValue(
            self._answer_scroll_area.verticalScrollBar().maximum()
        )
        
        # Also update detached window if it exists
        if hasattr(self, '_detached_answer_layout') and self._detached_answer_layout:
            self._add_message_to_detached_conversation(role, text)
        
        return bubble_frame
    
    def _clear_conversation_view(self):
        """Clear all messages from the conversation view."""
        if hasattr(self, '_answer_layout') and self._answer_layout:
            # Remove all widgets except the stretch (last item)
            while self._answer_layout.count() > 1:
                item = self._answer_layout.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()
        # Reset thinking message tracker
        self._thinking_message_widget = None
    
    def _replace_thinking_message(self, new_text: str):
        """Replace the 'Thinking...' message with the actual response.
        
        Args:
            new_text: The new text to replace Thinking... with
        """
        if not hasattr(self, '_answer_layout') or not self._answer_layout:
            return
        
        # If there's a Thinking... message, replace it
        if self._thinking_message_widget:
            # Find the index of the Thinking... widget
            index = self._answer_layout.indexOf(self._thinking_message_widget)
            if index >= 0:
                # Remove the old widget
                self._answer_layout.takeAt(index)
                self._thinking_message_widget.deleteLater()
        
        # Create a new bubble with the response text
        bubble_frame = QFrame()
        bubble_frame.setFrameShape(QFrame.StyledPanel)
        bubble_frame.setFrameShadow(QFrame.Raised)
        
        bubble_layout = QVBoxLayout(bubble_frame)
        bubble_layout.setContentsMargins(10, 8, 10, 8)
        bubble_layout.setSpacing(4)
        
        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        
        role_label = QLabel("Assistant")
        role_font = role_label.font()
        role_font.setPointSize(10)
        role_font.setBold(True)
        role_label.setFont(role_font)
        
        header_layout.addWidget(role_label)
        header_layout.addStretch()
        bubble_layout.addLayout(header_layout)
        
        text_label = QLabel(new_text)
        text_label.setWordWrap(True)
        text_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        bubble_layout.addWidget(text_label)
        
        bubble_frame.setStyleSheet("""
            QFrame {
                background-color: #E8F5E9;
                border-radius: 10px;
                border: 1px solid #A5D6A7;
            }
        """)
        
        # Insert at the same position where Thinking was
        self._answer_layout.insertWidget(
            self._answer_layout.count() - 1,
            bubble_frame
        )
        
        # Store role and text as properties for later retrieval (for copying to detached)
        bubble_frame.setProperty('role', 'assistant')
        bubble_frame.setProperty('message_text', new_text)
        
        # Reset thinking tracker
        self._thinking_message_widget = None
        
        # Auto-scroll to bottom
        self._answer_scroll_area.verticalScrollBar().setValue(
            self._answer_scroll_area.verticalScrollBar().maximum()
        )
        
        # Also update detached window if it exists
        if hasattr(self, '_detached_answer_layout') and self._detached_answer_layout:
            self._add_message_to_detached_conversation('assistant', new_text)
    
    def _add_message_to_detached_conversation(self, role: str, text: str):
        """Add a message to the detached window's conversation view.
        
        Args:
            role: 'user' or 'assistant'
            text: The message text
        """
        if not hasattr(self, '_detached_answer_layout') or not self._detached_answer_layout:
            return
        
        # Create a bubble frame
        bubble_frame = QFrame()
        bubble_frame.setFrameShape(QFrame.StyledPanel)
        bubble_frame.setFrameShadow(QFrame.Raised)
        
        # Set layout for the bubble
        bubble_layout = QVBoxLayout(bubble_frame)
        bubble_layout.setContentsMargins(10, 8, 10, 8)
        bubble_layout.setSpacing(4)
        
        # Header with role label
        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        
        role_label = QLabel(f"{'You' if role == 'user' else 'Assistant'}")
        role_font = role_label.font()
        role_font.setPointSize(10)
        role_font.setBold(True)
        role_label.setFont(role_font)
        
        header_layout.addWidget(role_label)
        header_layout.addStretch()
        bubble_layout.addLayout(header_layout)
        
        # Message text
        text_label = QLabel(text)
        text_label.setWordWrap(True)
        text_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        bubble_layout.addWidget(text_label)
        
        # Style based on role
        if role == 'user':
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
        
        # Add to layout (before the stretch)
        self._detached_answer_layout.insertWidget(
            self._detached_answer_layout.count() - 1,
            bubble_frame
        )
        
        # Store role and text as properties for later retrieval
        bubble_frame.setProperty('role', role)
        bubble_frame.setProperty('message_text', text)
        
        # Auto-scroll to bottom
        self._detached_answer_scroll.verticalScrollBar().setValue(
            self._detached_answer_scroll.verticalScrollBar().maximum()
        )

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
        
        if active_session and active_session.status == Session.STATUS_ACTIVE:
            self._is_recording = True
            self.session_name_input.setText(active_session.name)
            
            # Update icon row
            self.play_stop_button.setText("⏹")
            self.play_stop_button.setToolTip("Stop Session")
            self.pause_icon_button.setVisible(True)
            self.pause_icon_button.setText("⏸")
            self.pause_icon_button.setToolTip("Pause Session")
            self.screenshot_icon_button.setEnabled(True)
            
        elif active_session and active_session.status == Session.STATUS_PAUSED:
            self._is_recording = False
            self.session_name_input.setText(active_session.name)
            
            # Update icon row
            self.play_stop_button.setText("⏹")
            self.play_stop_button.setToolTip("Stop Session")
            self.pause_icon_button.setVisible(True)
            self.pause_icon_button.setText("▶")
            self.pause_icon_button.setToolTip("Resume Session")
            self.screenshot_icon_button.setEnabled(True)
            
        elif active_session and active_session.status == Session.STATUS_PROCESSING:
            # Update icon row
            self.play_stop_button.setText("▶")
            self.play_stop_button.setToolTip("Start Session")
            self.pause_icon_button.setVisible(False)
            self.screenshot_icon_button.setEnabled(False)
            
        else:
            # Enable View Screenshots button if there are past sessions
            sessions = self.session_manager.db.list_sessions()
            self._is_recording = False
            
            # Update icon row
            self.play_stop_button.setText("▶")
            self.play_stop_button.setToolTip("Start Session")
            self.pause_icon_button.setVisible(False)
            self.screenshot_icon_button.setEnabled(False)
            self.screenshot_icon_button.setEnabled(False)
        
        # Update summary icon button based on selected session
        self._update_summary_icon_state()
    
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
            
            # Update UI via icon buttons
            self._update_ui_state()
            
            # Just show status - no automatic transcription/summarization
            if session:
                self._on_status_update(f"Session '{session.name}' saved. Use Transcribe button to process.")
            
            # Reload sessions to show the new session
            self._refresh_session_completer()
            self._refresh_session_completer()
            
        except Exception as e:
            self._on_status_update(f'Failed to stop session: {str(e)}', is_error=True)
            QMessageBox.critical(self, 'Error', f'Failed to stop session: {str(e)}')
            self._update_ui_state()
    
    def _on_pause_resume_session(self):
        """Handle pause/resume button click."""
        try:
            active_session = self.session_manager.get_active_session()
            if not active_session:
                return
            
            if active_session.status == Session.STATUS_ACTIVE:
                self.session_manager.pause_session()
                self._on_status_update(f"Session '{active_session.name}' paused")
            elif active_session.status == Session.STATUS_PAUSED:
                self.session_manager.resume_session()
                self._on_status_update(f"Session '{active_session.name}' resumed")
            
            # Update UI state
            self._update_ui_state()
            
            self._update_ui_state()
        except Exception as e:
            self._on_status_update(f'Failed to pause/resume: {str(e)}', is_error=True)
    
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
    
    def _on_play_stop_clicked(self):
        """Handle play/stop icon button click."""
        active_session = self.session_manager.get_active_session() if self.session_manager else None
        if active_session and active_session.status in (Session.STATUS_ACTIVE, Session.STATUS_PAUSED):
            # Session is running - stop it
            self._on_stop_session()
        else:
            # No session running - start one
            self._on_start_session()
    
    def _on_view_screenshots_icon_clicked(self):
        """Handle view screenshots icon button click - uses selected session."""
        # Delegate to the complete handler
        self._on_view_screenshots()
    
    def _on_view_summary_icon_clicked(self):
        """Handle view summary icon button click - uses selected session."""
        try:
            session_id = None
            session_name = None
            
            # First check for selected session
            if self._selected_session_id:
                session_id = self._selected_session_id
                # Get session name from database
                sessions = self.session_manager.db.list_sessions()
                for s in sessions:
                    if s['id'] == session_id:
                        session_name = s['name']
                        break
                
                # Check summary status
                if session_id:
                    summaries = self.session_manager.db.get_summaries(session_id)
                    if not summaries:
                        QMessageBox.information(
                            self,
                            'No Summary',
                            f"Session '{session_name}' does not have a summary yet.\n\n"
                            "Please transcribe and summarize the session first."
                        )
                        return
            else:
                # Fall back to active session or most recent
                active_session = self.session_manager.get_active_session()
                if active_session:
                    session_id = active_session.id
                    session_name = active_session.name
                else:
                    sessions = self.session_manager.db.list_sessions()
                    if sessions:
                        session_id = sessions[0]['id']
                        session_name = sessions[0]['name']
            
            if not session_id:
                QMessageBox.information(
                    self,
                    'No Sessions',
                    'There are no sessions with summaries.'
                )
                return
            
            # Fetch summary from database
            summaries = self.session_manager.db.get_summaries(session_id)
            
            if not summaries:
                QMessageBox.information(
                    self,
                    'No Summary',
                    f"Session '{session_name}' does not have a summary yet.\n\n"
                    "Please transcribe and summarize the session first."
                )
                return
            
            # Get the first summary
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
            layout.addWidget(info_label)
            
            # Summary content
            summary_browser = QTextBrowser()
            summary_browser.setPlainText(summary_content)
            layout.addWidget(summary_browser)
            
            # Close button
            close_button = QPushButton("Close")
            close_button.clicked.connect(dialog.close)
            layout.addWidget(close_button)
            
            dialog.exec()
            
        except Exception as e:
            logger.error(f"Failed to view summary: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            QMessageBox.critical(self, 'Error', f'Failed to view summary: {str(e)}')
    
    def _on_view_screenshots(self):
        """Handle view screenshots button click."""
        try:
            session_id = None
            session_name = None

            # First check for selected session (scope)
            if self._selected_session_id:
                session_id = self._selected_session_id
                # Get session name from database
                sessions = self.session_manager.db.list_sessions()
                for s in sessions:
                    if s['id'] == session_id:
                        session_name = s['name']
                        break
            # Then check if there's an active session
            elif not session_id:
                active_session = self.session_manager.get_active_session()
                if active_session:
                    session_id = active_session.id
                    session_name = active_session.name
            
            # Fall back to most recent session
            if not session_id:
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

            # Use the complete screenshots window with context generator
            self._show_screenshots_by_session_id(session_id, session_name)
            
        except Exception as e:
            logger.error(f"Failed to view screenshots: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            QMessageBox.critical(
                self,
                'Error',
                f'Failed to view screenshots: {str(e)}'
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
            self._refresh_session_completer()
            
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
        self._clear_conversation_view()
        self._clear_candidates()
        self._on_status_update("New conversation started")
    
    def _on_ask_clicked(self):
        """Handle the Ask button click - wires to AssistantAnswerService."""
        # Get the question from the input
        question = self.question_input.toPlainText().strip()
        
        if not question:
            self._add_message_to_conversation('assistant', "Please enter a question.")
            return
        
        # Add user's question to conversation view
        self._add_message_to_conversation('user', question)
        
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
        
        # Get selected session ID from the sessions table (if any session is selected)
        # Falls back to getting session from conversation if conversation is selected
        selected_session_id = self._get_selected_session_id()
        
        # If no session selected but we have a conversation, get session from conversation
        if selected_session_id is None and self._current_conversation_id is not None:
            conv = self.session_manager.db.get_conversation(self._current_conversation_id)
            if conv:
                selected_session_id = conv.get('session_id')
        
        # Disable the Ask button while processing
        self.ask_button.setEnabled(False)
        self._add_message_to_conversation('assistant', "Thinking...")
        
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
        """Execute the assistant query in a background thread."""
        # Wait for any previous thread to finish before starting a new one
        if self._assistant_thread is not None and self._assistant_thread.isRunning():
            self._assistant_thread.wait()
        
        # Create and configure the thread
        self._assistant_thread = AssistantQueryThread(
            assistant_service=self.assistant_service,
            question=question,
            agent_id=agent_id,
            explicit_scope=explicit_scope,
            active_session_id=active_session_id,
            selected_session_id=selected_session_id,
            conversation_id=self._current_conversation_id
        )
        
        # Connect signals to handlers
        self._assistant_thread.finished_signal.connect(self._on_assistant_query_finished)
        self._assistant_thread.error_signal.connect(self._on_assistant_query_error)
        
        # Start the thread
        self._assistant_thread.start()
    
    def _on_assistant_query_finished(self, response):
        """Handle the assistant query response."""
        # Clear thread reference
        self._assistant_thread = None
        
        # Re-enable the Ask button
        self.ask_button.setEnabled(True)
        
        # Handle the response
        if response.success:
            # Clear candidates on successful answer
            self._clear_candidates()
            # Replace "Thinking..." with the actual response
            self._replace_thinking_message(response.answer or "")
            # Save conversation_id for follow-up questions
            if response.conversation_id:
                self._current_conversation_id = response.conversation_id
            self._on_status_update("Answer received")
        elif response.needs_clarification:
            # Show clarification question and candidates
            clarification_text = response.clarification_question or ""
            # Replace "Thinking..." with clarification
            self._replace_thinking_message(clarification_text)
            
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
            self._add_message_to_conversation('assistant', f"Error: {error_text}")
            self._on_status_update(f"Assistant error: {error_text}", is_error=True)
            # Clear candidates on error
            self._clear_candidates()
    
    def _on_assistant_query_error(self, error_message: str):
        """Handle assistant query errors."""
        # Clear thread reference
        self._assistant_thread = None
        
        # Re-enable the Ask button
        self.ask_button.setEnabled(True)
        
        logger.error(f"Assistant query failed: {error_message}")
        import traceback
        logger.error(traceback.format_exc())
        self._add_message_to_conversation('assistant', f"Error: {error_message}")
        self._on_status_update(f"Assistant error: {error_message}", is_error=True)
        # Clear candidates on exception
        self._clear_candidates()
    
    def _get_selected_session_id(self) -> Optional[int]:
        """Get the currently selected session ID from the sessions table.
        
        Returns None since sessions are no longer shown in a table.
        Session can still be inferred from the selected conversation.
        """
        return None
    
    def _load_recent_sessions(self, limit: int = 5):
        """Load the most recent sessions for the search dropdown.
        
        Args:
            limit: Maximum number of sessions to load (default 5)
        """
        # Store recent sessions in memory for dropdown
        self._recent_sessions = self._get_filtered_sessions("", limit)
    
    def _filter_sessions_by_name(self, filter_text: str):
        """Filter sessions by name matching the filter text.
        
        Args:
            filter_text: Text to filter session names by
        """
        # Store filtered sessions in memory for dropdown
        self._recent_sessions = self._get_filtered_sessions(filter_text, 5)
    
    def _on_session_search_text_changed(self, text: str):
        """Handle text changes in the session search input.
        
        Args:
            text: The current text in the search field
        """
        # Filter and show dropdown automatically
        self._show_session_search_dropdown()
    
    def _refresh_session_completer(self):
        """Refresh the completer with current session list."""
        # Skip if session_manager not initialized yet
        if not self.session_manager:
            return
        
        try:
            sessions = self.session_manager.db.list_sessions()
            
            # Create list of display strings
            from datetime import datetime
            session_list = []
            session_map = {}  # Maps display text to session_id
            
            for session in sessions:
                session_id = session.get('id')
                session_name = session.get('name', 'Unnamed')
                start_time = session.get('start_time', 0)
                
                # Format display text
                if start_time:
                    try:
                        dt = datetime.fromtimestamp(start_time)
                        display_text = f"{session_name} - {dt.strftime('%Y-%m-%d %H:%M')}"
                    except:
                        display_text = f"{session_name} (ID: {session_id})"
                else:
                    display_text = f"{session_name} (ID: {session_id})"
                
                session_list.append(display_text)
                session_map[display_text] = session_id
            
            # Sort by most recent
            session_list.sort()
            
            # Update completer
            self._session_completer_model = QStringListModel(session_list)
            self._session_completer.setModel(self._session_completer_model)
            self._session_completer_map = session_map
            
        except Exception as e:
            logger.error(f"Failed to refresh session completer: {e}")
    
    def _update_scope_label(self):
        """Update the scope label to show the currently selected session name."""
        if not hasattr(self, '_scope_label'):
            return
            
        if self._selected_session_id is None:
            self._scope_label.setText("Scope: (none)")
            self._scope_label.setStyleSheet("color: gray; font-style: italic;")
            return
        
        try:
            # Get session name from database
            sessions = self.session_manager.db.list_sessions()
            session_name = None
            for session in sessions:
                if session.get('id') == self._selected_session_id:
                    session_name = session.get('name', 'Unnamed')
                    break
            
            if session_name:
                self._scope_label.setText(f"Scope: {session_name}")
                self._scope_label.setStyleSheet("color: #0078d4; font-weight: bold;")
            else:
                self._scope_label.setText("Scope: (not found)")
                self._scope_label.setStyleSheet("color: gray; font-style: italic;")
        except Exception as e:
            logger.error(f"Failed to update scope label: {e}")
            self._scope_label.setText("Scope: (error)")
            self._scope_label.setStyleSheet("color: gray; font-style: italic;")
        
        # Also update summary icon state
        self._update_summary_icon_state()
    
    def _update_summary_icon_state(self):
        """Update the summary icon button based on selected session."""
        if not hasattr(self, 'view_summary_icon_button'):
            return
        
        # Check if selected session has a summary
        if self._selected_session_id:
            try:
                summaries = self.session_manager.db.get_summaries(self._selected_session_id)
                self.view_summary_icon_button.setEnabled(len(summaries) > 0)
                self.view_summary_icon_button.setToolTip("View Summary" if summaries else "No summary available")
            except Exception:
                self.view_summary_icon_button.setEnabled(False)
        else:
            # Check active session
            active_session = self.session_manager.get_active_session() if self.session_manager else None
            if active_session:
                try:
                    summaries = self.session_manager.db.get_summaries(active_session.id)
                    self.view_summary_icon_button.setEnabled(len(summaries) > 0)
                    self.view_summary_icon_button.setToolTip("View Summary" if summaries else "No summary available")
                except Exception:
                    self.view_summary_icon_button.setEnabled(False)
            else:
                self.view_summary_icon_button.setEnabled(False)
                self.view_summary_icon_button.setToolTip("No session selected")
    
    def _on_session_completer_selected(self, text: str):
        """Handle session selection from completer.
        
        Args:
            text: The selected text
        """
        session_id = self._session_completer_map.get(text)
        if session_id is not None:
            self._selected_session_id = session_id
            self._update_scope_label()
            logger.info(f"Selected session for assistant: {session_id}")
    
    def _show_all_sessions_window(self):
        """Show all sessions in a detached window."""
        dialog = QDialog(self)
        dialog.setWindowTitle("All Sessions")
        dialog.resize(700, 500)
        
        layout = QVBoxLayout(dialog)
        
        # Create table
        table = QTableWidget()
        table.setColumnCount(5)
        table.setHorizontalHeaderLabels(["Session Name", "Transcription", "Summary", "Date", "Actions"])
        table.horizontalHeader().setStretchLastSection(True)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        
        # Load sessions
        sessions = self.session_manager.db.list_sessions()
        
        # Verify and fix status
        from datetime import datetime
        for session in sessions:
            session_id = session['id']
            trans_status = session.get('transcription_status', 'none')
            sum_status = session.get('summary_status', 'none')
            
            # Verify transcription status
            if trans_status == 'none':
                actual_transcripts = self.session_manager.db.get_transcripts(session_id)
                if actual_transcripts:
                    trans_status = 'transcribed'
            
            # Verify summary status
            if sum_status == 'none':
                actual_summaries = self.session_manager.db.get_summaries(session_id)
                if actual_summaries:
                    sum_status = 'summarized'
            
            session['transcription_status'] = trans_status
            session['summary_status'] = sum_status
        
        table.setRowCount(len(sessions))
        
        # Store sessions data for access in handlers
        sessions_data = sessions
        
        for row, session in enumerate(sessions):
            session_id = session['id']
            trans_status = session.get('transcription_status', 'none')
            sum_status = session.get('summary_status', 'none')
            
            # Session name (editable)
            name_item = QTableWidgetItem(session['name'])
            name_item.setData(Qt.UserRole, session_id)
            name_item.setFlags(name_item.flags() | Qt.ItemIsEditable)
            table.setItem(row, 0, name_item)
            
            # Transcription status (read-only)
            trans_item = QTableWidgetItem(trans_status)
            trans_item.setFlags(trans_item.flags() & ~Qt.ItemIsEditable)
            table.setItem(row, 1, trans_item)
            
            # Summary status (read-only)
            sum_item = QTableWidgetItem(sum_status)
            sum_item.setFlags(sum_item.flags() & ~Qt.ItemIsEditable)
            table.setItem(row, 2, sum_item)
            
            # Date
            start_time = session.get('start_time', 0)
            if start_time:
                try:
                    dt = datetime.fromtimestamp(start_time)
                    date_str = dt.strftime('%Y-%m-%d %H:%M')
                except:
                    date_str = ''
            else:
                date_str = ''
            date_item = QTableWidgetItem(date_str)
            date_item.setFlags(date_item.flags() & ~Qt.ItemIsEditable)
            table.setItem(row, 3, date_item)
            
            # Actions dropdown
            action_combo = QComboBox()
            # Placeholder to ensure selecting the same action twice emits a change
            action_combo.addItem("Select Action", "none")
            
            # Determine available actions
            if trans_status != 'transcribed':
                action_combo.addItem("Transcribe", "transcribe")
            
            if trans_status == 'transcribed':
                if sum_status != 'summarized':
                    action_combo.addItem("Summarize", "summarize")
                else:
                    action_combo.addItem("View Summary", "view_summary")
            
            # Always add View Screenshots
            action_combo.addItem("View Screenshots", "view_screenshots")
            
            # Add Delete option
            action_combo.addItem("Delete", "delete")
            
            action_combo.currentIndexChanged.connect(
                lambda idx, sid=session_id, sname=session['name'], tstat=trans_status, sstat=sum_status, c=action_combo, d=dialog: 
                self._on_all_sessions_action_selected(sid, sname, tstat, sstat, idx, c, d)
            )
            
            table.setCellWidget(row, 4, action_combo)
        
        # Connect cellChanged for name editing
        table.cellChanged.connect(lambda row, col: self._on_all_sessions_cell_changed(row, col, table, sessions_data))
        
        # Connect double-click to select session (except for name column which is for renaming)
        def on_cell_double_clicked(row, col):
            # Skip if double-clicking on name column (reserved for renaming)
            if col == 0:
                return
            
            # Get session_id from the row
            if row < len(sessions_data):
                session = sessions_data[row]
                session_id = session.get('id')
                if session_id is not None:
                    self._selected_session_id = session_id
                    self._update_scope_label()
                    logger.info(f"Selected session from All Sessions window: {session_id}")
                    dialog.close()
        
        table.cellDoubleClicked.connect(on_cell_double_clicked)
        
        layout.addWidget(table)
        
        # Refresh button
        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(lambda: self._refresh_all_sessions_window(dialog))
        layout.addWidget(refresh_btn)
        
        # Close button
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(dialog.close)
        layout.addWidget(close_btn)
        
        dialog.exec()
    
    def _on_all_sessions_cell_changed(self, row, col, table, sessions_data):
        """Handle cell changes in the all sessions table.
        
        Args:
            row: The changed row
            col: The changed column
            table: The table widget
            sessions_data: List of session dictionaries
        """
        if col != 0:  # Only handle name column
            return
        
        try:
            item = table.item(row, 0)
            if not item:
                return
            
            session_id = item.data(Qt.UserRole)
            new_name = item.text()
            
            if not new_name:
                return
            
            # Find the old name from sessions_data
            old_name = None
            for session in sessions_data:
                if session['id'] == session_id:
                    old_name = session['name']
                    break
            
            if old_name is None or new_name == old_name:
                return
            
            # Update in database
            self.session_manager.db.update_session(session_id, name=new_name)
            logger.info(f"Renamed session {session_id} to '{new_name}'")
            self._on_status_update(f"Session renamed to '{new_name}'")
            
            # Update the local data
            for session in sessions_data:
                if session['id'] == session_id:
                    session['name'] = new_name
                    break
            
            # Refresh completer
            self._refresh_session_completer()
            
        except Exception as e:
            logger.error(f"Failed to rename session: {e}")
            self._on_status_update(f"Error renaming session: {e}", is_error=True)
    
    def _refresh_all_sessions_window(self, dialog):
        """Refresh the all sessions window."""
        # Simply recreate the window
        dialog.close()
        self._show_all_sessions_window()
    
    def _on_all_sessions_action_selected(self, session_id, session_name, trans_status, sum_status, index, combo, dialog):
        """Handle action selection from all sessions window."""
        # Debug/logging to trace UI actions
        try:
            logger.debug(f"_on_all_sessions_action_selected called: session_id={session_id}, index={index}, text={combo.currentText()}, data={combo.currentData()}, trans_status={trans_status}, sum_status={sum_status}")
        except Exception:
            logger.debug(f"_on_all_sessions_action_selected called: session_id={session_id}, index={index}")

        # Determine selected action; ignore placeholder/none
        action = combo.currentData()
        if not action or action == 'none':
            return
        # Show immediate status so user sees action was registered
        try:
            self._on_status_update(f"Action selected: {action} for session {session_id}")
        except Exception:
            pass
        
        if action == "transcribe":
            combo.setEnabled(False)
            self._on_transcribe_clicked(session_id, combo)
            # Reset the combo so the same action can be chosen again
            try:
                combo.blockSignals(True)
                combo.setCurrentIndex(0)
                combo.blockSignals(False)
            except Exception:
                pass
        elif action == "summarize":
            combo.setEnabled(False)
            self._on_summarize_clicked(session_id, combo)
            # Reset the combo so the same action can be chosen again
            try:
                combo.blockSignals(True)
                combo.setCurrentIndex(0)
                combo.blockSignals(False)
            except Exception:
                pass
        elif action == "view_summary":
            # Show summary directly from database
            self._show_summary_by_session_id(session_id, session_name)
            # Reset the combo so the same action can be chosen again
            try:
                combo.blockSignals(True)
                combo.setCurrentIndex(0)
                combo.blockSignals(False)
            except Exception:
                pass
        elif action == "view_screenshots":
            self._show_screenshots_by_session_id(session_id, session_name)
            # Reset the combo so the same action can be chosen again
            try:
                combo.blockSignals(True)
                combo.setCurrentIndex(0)
                combo.blockSignals(False)
            except Exception:
                pass
        elif action == "delete":
            self._delete_session_by_id(session_id)
            # Refresh the dialog
            self._refresh_all_sessions_window(dialog)
    
    def _on_session_search_selected(self, index: int):
        """Handle session selection from the combobox.
        
        Args:
            index: The selected index
        """
        session_id = self.session_search_combo.currentData()
        if session_id is not None:
            self._selected_session_id = session_id
            self._update_scope_label()
            logger.info(f"Selected session for assistant: {session_id}")
        else:
            self._selected_session_id = None
            self._update_scope_label()
    
    def _on_session_search_dropdown_opened(self, index: int):
        """Handle when the dropdown is opened - load recent sessions.
        
        Args:
            index: The selected index (can be -1 if no selection)
        """
        # Load recent sessions when dropdown is opened
        current_text = self.session_search_input.text()
        if not current_text:
            self._load_recent_sessions(5)
    
    def _show_session_search_dropdown(self):
        """Show the session search dropdown popup."""
        # Load recent sessions first
        current_text = self.session_search_input.text()
        if not current_text:
            self._load_recent_sessions(5)
        
        # Get current sessions from the internal list
        sessions_to_show = self._get_filtered_sessions(current_text)
        
        # Create a popup list
        popup = QDialog(self)
        popup.setWindowFlags(Qt.Popup)
        popup.setAttribute(Qt.WA_DeleteOnClose)
        
        layout = QVBoxLayout(popup)
        layout.setContentsMargins(0, 0, 0, 0)
        
        # Create list widget
        list_widget = QListWidget()
        list_widget.setMaximumHeight(200)
        
        # Populate with sessions
        from datetime import datetime
        for session in sessions_to_show:
            session_id = session.get('id')
            session_name = session.get('name', 'Unnamed')
            start_time = session.get('start_time', 0)
            
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
            list_widget.addItem(item)
        
        layout.addWidget(list_widget)
        
        # Handle selection
        def on_item_clicked(item):
            session_id = item.data(Qt.UserRole)
            if session_id is not None:
                self._selected_session_id = session_id
                self._update_scope_label()
                # Also update the input text
                self.session_search_input.setText(item.text())
                logger.info(f"Selected session for assistant: {session_id}")
            popup.close()
        
        list_widget.itemClicked.connect(on_item_clicked)
        
        # Position popup below the button
        pos = self.session_search_button.mapToGlobal(self.session_search_button.rect().bottomLeft())
        popup.move(pos)
        popup.exec()
    
    def _get_filtered_sessions(self, filter_text: str = "", limit: int = 5):
        """Get sessions filtered by text.
        
        Args:
            filter_text: Text to filter session names by
            limit: Maximum number of sessions to return
        Returns:
            List of session dictionaries
        """
        try:
            all_sessions = self.session_manager.db.list_sessions()
            
            # Filter by name (case-insensitive)
            if filter_text:
                filtered = [s for s in all_sessions if filter_text.lower() in s.get('name', '').lower()]
            else:
                filtered = all_sessions
            
            # Sort by start_time descending (newest first) and take limit
            sorted_sessions = sorted(filtered, key=lambda s: s.get('start_time', 0), reverse=True)[:limit]
            return sorted_sessions
            
        except Exception as e:
            logger.error(f"Failed to get filtered sessions: {e}")
            return []
    
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
        # Preserve the current selection from main window
        self._detached_scope_combo.setCurrentIndex(self.scope_combo.currentIndex())
        # Sync scope changes back to main window
        self._detached_scope_combo.currentIndexChanged.connect(
            lambda idx: self.scope_combo.setCurrentIndex(idx)
        )
        agent_layout.addWidget(self._detached_scope_combo)
        
        agent_layout.addStretch()
        main_layout.addLayout(agent_layout)
        
        # Answer display (scrollable conversation on top)
        answer_label = QLabel("Answer:")
        main_layout.addWidget(answer_label)
        
        # Create scrollable conversation view for detached window
        self._detached_answer_scroll = QScrollArea()
        self._detached_answer_scroll.setWidgetResizable(True)
        self._detached_answer_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        
        # Container for conversation messages
        self._detached_answer_container = QWidget()
        self._detached_answer_layout = QVBoxLayout(self._detached_answer_container)
        self._detached_answer_layout.setSpacing(10)
        self._detached_answer_layout.setContentsMargins(5, 5, 5, 5)
        self._detached_answer_layout.addStretch()  # Push content to top
        
        self._detached_answer_scroll.setWidget(self._detached_answer_container)
        self._detached_answer_scroll.setMinimumHeight(300)
        self._detached_answer_scroll.setMaximumHeight(500)
        main_layout.addWidget(self._detached_answer_scroll)
        
        # Question input (on bottom)
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
        
        # Copy existing conversation from main window to detached window
        self._copy_conversation_to_detached()
        
        # Connect main window's answer display to also update detached window
        # This keeps both windows in sync when main window gets an answer
        
        self._detached_assistant_window.show()
    
    def _copy_conversation_to_detached(self):
        """Copy all existing conversation messages from main window to detached window."""
        if not hasattr(self, '_answer_layout') or not self._answer_layout:
            return
        
        if not hasattr(self, '_detached_answer_layout') or not self._detached_answer_layout:
            return
        
        # Iterate through all message widgets in the main conversation (excluding stretch)
        for i in range(self._answer_layout.count() - 1):
            item = self._answer_layout.itemAt(i)
            if item and item.widget():
                original_frame = item.widget()
                
                # Get the role from the property we stored earlier
                role = original_frame.property('role')
                if not role:
                    continue
                
                # Get the text from the property we stored
                text = original_frame.property('message_text')
                if not text:
                    continue
                
                if text:
                    # Add to detached window
                    self._add_message_to_detached_conversation(role, text)
    
    def _on_detached_ask_clicked(self):
        """Handle the Ask button click in the detached assistant window."""
        # Get the question from the detached input
        question = self._detached_question_input.toPlainText().strip()
        
        if not question:
            self._add_message_to_conversation('assistant', "Please enter a question.")
            return
        
        # Add user's question to conversation view
        self._add_message_to_conversation('user', question)
        
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
        
        # Get selected session ID from the sessions table (if any session is selected)
        # Falls back to getting session from conversation if conversation is selected
        selected_session_id = self._get_selected_session_id()
        
        # If no session selected but we have a conversation, get session from conversation
        if selected_session_id is None and self._current_conversation_id is not None:
            conv = self.session_manager.db.get_conversation(self._current_conversation_id)
            if conv:
                selected_session_id = conv.get('session_id')
        
        # Disable the Ask button while processing
        self._detached_ask_button.setEnabled(False)
        self._add_message_to_conversation('assistant', "Thinking...")
        
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
                # Add assistant's response to conversation view
                self._add_message_to_conversation('assistant', response.answer or "")
                # Save conversation_id for follow-up questions
                if response.conversation_id:
                    self._current_conversation_id = response.conversation_id
                self._on_status_update("Answer received")
            elif response.needs_clarification:
                # Show clarification question and candidates
                clarification_text = response.clarification_question or ""
                self._add_message_to_conversation('assistant', clarification_text)
                
                # Display candidates in detached window
                if response.candidates:
                    self._display_detached_candidates(response.candidates)
                else:
                    self._detached_candidate_group.setVisible(False)
                    
                self._on_status_update("Clarification needed")
            else:
                # Show error
                error_text = response.error or "Unknown error occurred"
                self._add_message_to_conversation('assistant', f"Error: {error_text}")
                self._on_status_update(f"Assistant error: {error_text}", is_error=True)
                # Clear candidates on error
                self._detached_candidate_group.setVisible(False)
                
        except Exception as e:
            logger.error(f"Assistant query failed (detached): {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            self._add_message_to_conversation('assistant', f"Error: {str(e)}")
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
                self._add_message_to_conversation('assistant', "Thinking...")
                
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
        # Wait for any active assistant thread to finish
        if self._assistant_thread is not None and self._assistant_thread.isRunning():
            self._assistant_thread.wait()
        
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
