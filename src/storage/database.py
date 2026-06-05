from pathlib import Path
import sqlite3
import os
from typing import Optional, List, Dict, Any
from datetime import datetime


class DatabaseError(Exception):
    pass


class Database:
    def __init__(self, db_path: str = 'chronicle.db'):
        self.db_path = db_path
        self.connection: Optional[sqlite3.Connection] = None

    def connect(self) -> sqlite3.Connection:
        try:
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
            
            self.connection = sqlite3.connect(self.db_path, check_same_thread=False)
            self.connection.row_factory = sqlite3.Row
            self._initialize_schema()
            return self.connection
        except sqlite3.Error as e:
            raise DatabaseError(f'Database connection failed: {str(e)}')

    def disconnect(self):
        if self.connection:
            self.connection.close()
            self.connection = None

    def recreate_database(self) -> None:
        """Delete and recreate the database from scratch.
        
        WARNING: This will delete ALL data. Use reset_database() to keep
        schema but delete data, or this method to start completely fresh.
        
        Raises:
            DatabaseError: If recreation fails
        """
        # Close existing connection if any
        self.disconnect()
        
        # Delete existing database file
        if os.path.exists(self.db_path):
            os.remove(self.db_path)
        
        # Reconnect (which will create fresh schema)
        self.connect()

    def _initialize_schema(self):
        try:
            cursor = self.connection.cursor()
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    start_time INTEGER NOT NULL,
                    end_time INTEGER,
                    status TEXT NOT NULL,
                    transcription_status TEXT DEFAULT 'none',
                    summary_status TEXT DEFAULT 'none'
                )
            ''')
            # Add columns to existing tables if they don't exist
            try:
                cursor.execute('ALTER TABLE sessions ADD COLUMN transcription_status TEXT DEFAULT "none"')
            except sqlite3.OperationalError:
                pass  # Column already exists
            try:
                cursor.execute('ALTER TABLE sessions ADD COLUMN summary_status TEXT DEFAULT "none"')
            except sqlite3.OperationalError:
                pass  # Column already exists
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS transcripts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id INTEGER NOT NULL,
                    timestamp INTEGER NOT NULL,
                    text TEXT NOT NULL,
                    source TEXT NOT NULL,
                    FOREIGN KEY(session_id) REFERENCES sessions(id)
                )
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS screenshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id INTEGER NOT NULL,
                    timestamp INTEGER NOT NULL,
                    filepath TEXT NOT NULL,
                    description TEXT,
                    FOREIGN KEY(session_id) REFERENCES sessions(id)
                )
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS summaries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id INTEGER NOT NULL,
                    summary_type TEXT NOT NULL,
                    content TEXT NOT NULL,
                    model_used TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    FOREIGN KEY(session_id) REFERENCES sessions(id)
                )
            ''')
            self.connection.commit()

            # Migración: agregar columna description a screenshots si no existe
            try:
                cursor.execute("ALTER TABLE screenshots ADD COLUMN description TEXT")
                self.connection.commit()
            except sqlite3.OperationalError:
                pass  # La columna ya existe

            # Migración: agregar columnas para contexto estructurado de screenshots (nuevo formato)
            try:
                cursor.execute("ALTER TABLE screenshots ADD COLUMN ai_summary TEXT")
                self.connection.commit()
            except sqlite3.OperationalError:
                pass  # La columna ya existe
            
            try:
                cursor.execute("ALTER TABLE screenshots ADD COLUMN visible_text TEXT")
                self.connection.commit()
            except sqlite3.OperationalError:
                pass  # La columna ya existe
            
            try:
                cursor.execute("ALTER TABLE screenshots ADD COLUMN keywords TEXT")
                self.connection.commit()
            except sqlite3.OperationalError:
                pass  # La columna ya existe

            # Assistant conversations and messages tables
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS assistant_conversations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id INTEGER,
                    title TEXT,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    FOREIGN KEY(session_id) REFERENCES sessions(id)
                )
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS assistant_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id INTEGER NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    timestamp INTEGER NOT NULL,
                    FOREIGN KEY(conversation_id) REFERENCES assistant_conversations(id)
                )
            ''')
            self.connection.commit()

        except sqlite3.Error as e:
            raise DatabaseError(f'Schema initialization failed: {str(e)}')

    def create_session(self, name: str, start_time: datetime, status: str = 'active', 
                       transcription_status: str = 'none', summary_status: str = 'none') -> int:
        try:
            cursor = self.connection.cursor()
            cursor.execute('''
                INSERT INTO sessions (name, start_time, status, transcription_status, summary_status)
                VALUES (?, ?, ?, ?, ?)
            ''', (name, int(start_time.timestamp()), status, transcription_status, summary_status))
            self.connection.commit()
            return cursor.lastrowid
        except sqlite3.Error as e:
            raise DatabaseError(f'Session creation failed: {str(e)}')

    def get_session(self, session_id: int) -> Dict[str, Any]:
        try:
            cursor = self.connection.cursor()
            cursor.execute('SELECT * FROM sessions WHERE id = ?', (session_id,))
            return dict(cursor.fetchone())
        except sqlite3.Error as e:
            raise DatabaseError(f'Session retrieval failed: {str(e)}')

    def update_session(self, session_id: int, **kwargs) -> None:
        try:
            cursor = self.connection.cursor()
            set_clause = ', '.join(f'{k} = ?' for k in kwargs)
            values = list(kwargs.values())
            cursor.execute(f'''
                UPDATE sessions
                SET {set_clause}
                WHERE id = ?
            ''', (*values, session_id))
            self.connection.commit()
        except sqlite3.Error as e:
            raise DatabaseError(f'Session update failed: {str(e)}')

    def delete_session(self, session_id: int) -> None:
        try:
            cursor = self.connection.cursor()
            cursor.execute('DELETE FROM sessions WHERE id = ?', (session_id,))
            self.connection.commit()
        except sqlite3.Error as e:
            raise DatabaseError(f'Session deletion failed: {str(e)}')

    def list_sessions(self) -> List[Dict[str, Any]]:
        try:
            cursor = self.connection.cursor()
            cursor.execute('SELECT * FROM sessions ORDER BY start_time DESC')
            return [dict(row) for row in cursor.fetchall()]
        except sqlite3.Error as e:
            raise DatabaseError(f'Session listing failed: {str(e)}')

    def get_screenshots(self, session_id: int) -> List[Dict[str, Any]]:
        """Get all screenshots for a session.

        Args:
            session_id: ID of the session

        Returns:
            List of screenshot dictionaries sorted by timestamp

        Raises:
            DatabaseError: If retrieval fails
        """
        try:
            cursor = self.connection.cursor()
            cursor.execute('''
                SELECT * FROM screenshots 
                WHERE session_id = ?
                ORDER BY timestamp ASC
            ''', (session_id,))
            return [dict(row) for row in cursor.fetchall()]
        except sqlite3.Error as e:
            raise DatabaseError(f'Screenshot retrieval failed: {str(e)}')

    def update_screenshot_description(self, filepath: str, description: str) -> None:
        """Update the description of a screenshot.

        Args:
            filepath: Path to the screenshot file
            description: New description text

        Raises:
            DatabaseError: If update fails
        """
        try:
            cursor = self.connection.cursor()
            cursor.execute(
                'UPDATE screenshots SET description = ? WHERE filepath = ?',
                (description, filepath)
            )
            self.connection.commit()
        except sqlite3.Error as e:
            raise DatabaseError(f'Screenshot description update failed: {str(e)}')

    def update_screenshot_ai_context(
        self,
        screenshot_id: int,
        ai_summary: str,
        visible_text: str,
        keywords: str
    ) -> None:
        """Update the AI context fields of a screenshot.

        Args:
            screenshot_id: ID of the screenshot
            ai_summary: Concise summary of the screenshot context
            visible_text: JSON array of important visible text
            keywords: JSON array of keywords for semantic search

        Raises:
            DatabaseError: If update fails
        """
        try:
            cursor = self.connection.cursor()
            cursor.execute('''
                UPDATE screenshots SET 
                    ai_summary = ?,
                    visible_text = ?,
                    keywords = ?
                WHERE id = ?
            ''', (
                ai_summary,
                visible_text,
                keywords,
                screenshot_id
            ))
            self.connection.commit()
        except sqlite3.Error as e:
            raise DatabaseError(f'Screenshot AI context update failed: {str(e)}')

    def get_screenshot(self, screenshot_id: int) -> Dict[str, Any]:
        """Get a specific screenshot by ID.

        Args:
            screenshot_id: ID of the screenshot

        Returns:
            Screenshot dictionary

        Raises:
            DatabaseError: If retrieval fails
        """
        try:
            cursor = self.connection.cursor()
            cursor.execute('SELECT * FROM screenshots WHERE id = ?', (screenshot_id,))
            row = cursor.fetchone()
            if row is None:
                raise DatabaseError(f'Screenshot {screenshot_id} not found')
            return dict(row)
        except sqlite3.Error as e:
            raise DatabaseError(f'Screenshot retrieval failed: {str(e)}')

    def get_screenshot_by_filepath(self, filepath: str) -> Optional[Dict[str, Any]]:
        """Get a screenshot by its file path.

        Args:
            filepath: Path to the screenshot file

        Returns:
            Screenshot dictionary or None if not found

        Raises:
            DatabaseError: If retrieval fails
        """
        try:
            cursor = self.connection.cursor()
            cursor.execute('SELECT * FROM screenshots WHERE filepath = ?', (filepath,))
            row = cursor.fetchone()
            return dict(row) if row else None
        except sqlite3.Error as e:
            raise DatabaseError(f'Screenshot retrieval failed: {str(e)}')

    def delete_screenshot(self, screenshot_id: int) -> bool:
        """Delete a screenshot from the database.

        Args:
            screenshot_id: ID of the screenshot to delete

        Returns:
            True if deleted, False if not found

        Raises:
            DatabaseError: If deletion fails
        """
        try:
            cursor = self.connection.cursor()
            cursor.execute('DELETE FROM screenshots WHERE id = ?', (screenshot_id,))
            self.connection.commit()
            return cursor.rowcount > 0
        except sqlite3.Error as e:
            raise DatabaseError(f'Screenshot deletion failed: {str(e)}')

    def delete_screenshot_by_filepath(self, filepath: str) -> bool:
        """Delete a screenshot from the database by its filepath.

        Args:
            filepath: Path to the screenshot file

        Returns:
            True if deleted, False if not found

        Raises:
            DatabaseError: If deletion fails
        """
        try:
            cursor = self.connection.cursor()
            cursor.execute('DELETE FROM screenshots WHERE filepath = ?', (filepath,))
            self.connection.commit()
            return cursor.rowcount > 0
        except sqlite3.Error as e:
            raise DatabaseError(f'Screenshot deletion failed: {str(e)}')


    def add_transcript(self, session_id: int, timestamp: datetime, text: str, source: str) -> int:
        """Add a transcript entry to the database.
        
        Args:
            session_id: ID of the session this transcript belongs to
            timestamp: Timestamp when the audio was recorded
            text: Transcribed text
            source: Audio source ('microphone' or 'system')
            
        Returns:
            ID of the inserted transcript
            
        Raises:
            DatabaseError: If insertion fails
        """
        try:
            cursor = self.connection.cursor()
            cursor.execute('''
                INSERT INTO transcripts (session_id, timestamp, text, source)
                VALUES (?, ?, ?, ?)
            ''', (session_id, int(timestamp.timestamp()), text, source))
            self.connection.commit()
            return cursor.lastrowid
        except sqlite3.Error as e:
            raise DatabaseError(f'Transcript insertion failed: {str(e)}')

    def get_transcripts(self, session_id: int) -> List[Dict[str, Any]]:
        """Get all transcripts for a session.
        
        Args:
            session_id: ID of the session
            
        Returns:
            List of transcript dictionaries sorted by timestamp
            
        Raises:
            DatabaseError: If retrieval fails
        """
        try:
            cursor = self.connection.cursor()
            cursor.execute('''
                SELECT * FROM transcripts 
                WHERE session_id = ?
                ORDER BY timestamp ASC
            ''', (session_id,))
            return [dict(row) for row in cursor.fetchall()]
        except sqlite3.Error as e:
            raise DatabaseError(f'Transcript retrieval failed: {str(e)}')

    def get_transcript(self, transcript_id: int) -> Dict[str, Any]:
        """Get a specific transcript by ID.
        
        Args:
            transcript_id: ID of the transcript
            
        Returns:
            Transcript dictionary
            
        Raises:
            DatabaseError: If retrieval fails
        """
        try:
            cursor = self.connection.cursor()
            cursor.execute('SELECT * FROM transcripts WHERE id = ?', (transcript_id,))
            row = cursor.fetchone()
            if row is None:
                raise DatabaseError(f'Transcript {transcript_id} not found')
            return dict(row)
        except sqlite3.Error as e:
            raise DatabaseError(f'Transcript retrieval failed: {str(e)}')

    def delete_transcript(self, transcript_id: int) -> None:
        """Delete a transcript.
        
        Args:
            transcript_id: ID of the transcript to delete
            
        Raises:
            DatabaseError: If deletion fails
        """
        try:
            cursor = self.connection.cursor()
            cursor.execute('DELETE FROM transcripts WHERE id = ?', (transcript_id,))
            self.connection.commit()
        except sqlite3.Error as e:
            raise DatabaseError(f'Transcript deletion failed: {str(e)}')

    def delete_transcripts(self, session_id: int) -> None:
        """Delete all transcripts for a session.
        
        Args:
            session_id: ID of the session
            
        Raises:
            DatabaseError: If deletion fails
        """
        try:
            cursor = self.connection.cursor()
            cursor.execute('DELETE FROM transcripts WHERE session_id = ?', (session_id,))
            self.connection.commit()
        except sqlite3.Error as e:
            raise DatabaseError(f'Transcript deletion failed: {str(e)}')

    def add_summary(self, session_id: int, summary_type: str, content: str, model_used: str) -> int:
        """Add a summary entry to the database.
        
        Args:
            session_id: ID of the session this summary belongs to
            summary_type: Type of summary (key_points, action_items, decisions, full)
            content: Generated summary content
            model_used: AI model used for generation
            
        Returns:
            ID of the inserted summary
            
        Raises:
            DatabaseError: If insertion fails
        """
        try:
            cursor = self.connection.cursor()
            cursor.execute('''
                INSERT INTO summaries (session_id, summary_type, content, model_used, created_at)
                VALUES (?, ?, ?, ?, ?)
            ''', (session_id, summary_type, content, model_used, int(datetime.now().timestamp())))
            self.connection.commit()
            return cursor.lastrowid
        except sqlite3.Error as e:
            raise DatabaseError(f'Summary insertion failed: {str(e)}')

    def get_summaries(self, session_id: int) -> List[Dict[str, Any]]:
        """Get all summaries for a session.
        
        Args:
            session_id: ID of the session
            
        Returns:
            List of summary dictionaries sorted by creation time
            
        Raises:
            DatabaseError: If retrieval fails
        """
        try:
            cursor = self.connection.cursor()
            cursor.execute('''
                SELECT * FROM summaries 
                WHERE session_id = ?
                ORDER BY created_at ASC
            ''', (session_id,))
            return [dict(row) for row in cursor.fetchall()]
        except sqlite3.Error as e:
            raise DatabaseError(f'Summary retrieval failed: {str(e)}')

    def get_summary(self, summary_id: int) -> Dict[str, Any]:
        """Get a specific summary by ID.
        
        Args:
            summary_id: ID of the summary
            
        Returns:
            Summary dictionary
            
        Raises:
            DatabaseError: If retrieval fails
        """
        try:
            cursor = self.connection.cursor()
            cursor.execute('SELECT * FROM summaries WHERE id = ?', (summary_id,))
            row = cursor.fetchone()
            if row is None:
                raise DatabaseError(f'Summary {summary_id} not found')
            return dict(row)
        except sqlite3.Error as e:
            raise DatabaseError(f'Summary retrieval failed: {str(e)}')

    def delete_summary(self, summary_id: int) -> None:
        """Delete a summary.
        
        Args:
            summary_id: ID of the summary to delete
            
        Raises:
            DatabaseError: If deletion fails
        """
        try:
            cursor = self.connection.cursor()
            cursor.execute('DELETE FROM summaries WHERE id = ?', (summary_id,))
            self.connection.commit()
        except sqlite3.Error as e:
            raise DatabaseError(f'Summary deletion failed: {str(e)}')

    def delete_summaries(self, session_id: int) -> None:
        """Delete all summaries for a session.
        
        Args:
            session_id: ID of the session
            
        Raises:
            DatabaseError: If deletion fails
        """
        try:
            cursor = self.connection.cursor()
            cursor.execute('DELETE FROM summaries WHERE session_id = ?', (session_id,))
            self.connection.commit()
        except sqlite3.Error as e:
            raise DatabaseError(f'Summary deletion failed: {str(e)}')

    # Assistant Conversation Methods

    def create_conversation(self, session_id: Optional[int] = None, title: Optional[str] = None) -> int:
        """Create a new assistant conversation.

        Args:
            session_id: ID of the session this conversation belongs to (can be None for all-session scope)
            title: Optional conversation title

        Returns:
            ID of the created conversation

        Raises:
            DatabaseError: If creation fails
        """
        try:
            cursor = self.connection.cursor()
            now = int(datetime.now().timestamp())
            cursor.execute('''
                INSERT INTO assistant_conversations (session_id, title, created_at, updated_at)
                VALUES (?, ?, ?, ?)
            ''', (session_id, title, now, now))
            self.connection.commit()
            return cursor.lastrowid
        except sqlite3.Error as e:
            raise DatabaseError(f'Conversation creation failed: {str(e)}')

    def get_conversation(self, conversation_id: int) -> Dict[str, Any]:
        """Get a specific conversation by ID.

        Args:
            conversation_id: ID of the conversation

        Returns:
            Conversation dictionary

        Raises:
            DatabaseError: If retrieval fails
        """
        try:
            cursor = self.connection.cursor()
            cursor.execute('SELECT * FROM assistant_conversations WHERE id = ?', (conversation_id,))
            row = cursor.fetchone()
            if row is None:
                raise DatabaseError(f'Conversation {conversation_id} not found')
            return dict(row)
        except sqlite3.Error as e:
            raise DatabaseError(f'Conversation retrieval failed: {str(e)}')

    def list_conversations(self, session_id: Optional[int] = None) -> List[Dict[str, Any]]:
        """List conversations, optionally filtered by session_id.

        Args:
            session_id: Optional session ID to filter by. If None, returns all conversations.

        Returns:
            List of conversation dictionaries, sorted by updated_at descending

        Raises:
            DatabaseError: If retrieval fails
        """
        try:
            cursor = self.connection.cursor()
            if session_id is not None:
                cursor.execute('''
                    SELECT * FROM assistant_conversations 
                    WHERE session_id = ?
                    ORDER BY updated_at DESC
                ''', (session_id,))
            else:
                cursor.execute('SELECT * FROM assistant_conversations ORDER BY updated_at DESC')
            return [dict(row) for row in cursor.fetchall()]
        except sqlite3.Error as e:
            raise DatabaseError(f'Conversation listing failed: {str(e)}')

    def update_conversation(self, conversation_id: int, **kwargs) -> None:
        """Update a conversation's fields.

        Args:
            conversation_id: ID of the conversation to update
            **kwargs: Fields to update (title, session_id)

        Raises:
            DatabaseError: If update fails
        """
        try:
            cursor = self.connection.cursor()
            if 'title' in kwargs or 'session_id' in kwargs:
                kwargs['updated_at'] = int(datetime.now().timestamp())
            set_clause = ', '.join(f'{k} = ?' for k in kwargs)
            values = list(kwargs.values())
            cursor.execute(f'''
                UPDATE assistant_conversations
                SET {set_clause}
                WHERE id = ?
            ''', (*values, conversation_id))
            self.connection.commit()
        except sqlite3.Error as e:
            raise DatabaseError(f'Conversation update failed: {str(e)}')

    def delete_conversation(self, conversation_id: int) -> None:
        """Delete a conversation and all its messages.

        Args:
            conversation_id: ID of the conversation to delete

        Raises:
            DatabaseError: If deletion fails
        """
        try:
            cursor = self.connection.cursor()
            cursor.execute('DELETE FROM assistant_messages WHERE conversation_id = ?', (conversation_id,))
            cursor.execute('DELETE FROM assistant_conversations WHERE id = ?', (conversation_id,))
            self.connection.commit()
        except sqlite3.Error as e:
            raise DatabaseError(f'Conversation deletion failed: {str(e)}')

    # Assistant Message Methods

    def add_message(self, conversation_id: int, role: str, content: str) -> int:
        """Add a message to a conversation.

        Args:
            conversation_id: ID of the conversation
            role: Message role ('user' or 'assistant')
            content: Message content (plain text)

        Returns:
            ID of the inserted message

        Raises:
            DatabaseError: If insertion fails
        """
        try:
            cursor = self.connection.cursor()
            now = int(datetime.now().timestamp())
            cursor.execute('''
                INSERT INTO assistant_messages (conversation_id, role, content, timestamp)
                VALUES (?, ?, ?, ?)
            ''', (conversation_id, role, content, now))
            # Update conversation's updated_at timestamp
            cursor.execute('''
                UPDATE assistant_conversations SET updated_at = ? WHERE id = ?
            ''', (now, conversation_id))
            self.connection.commit()
            return cursor.lastrowid
        except sqlite3.Error as e:
            raise DatabaseError(f'Message insertion failed: {str(e)}')

    def get_messages(self, conversation_id: int) -> List[Dict[str, Any]]:
        """Get all messages for a conversation in chronological order.

        Args:
            conversation_id: ID of the conversation

        Returns:
            List of message dictionaries, sorted by timestamp ascending

        Raises:
            DatabaseError: If retrieval fails
        """
        try:
            cursor = self.connection.cursor()
            cursor.execute('''
                SELECT * FROM assistant_messages 
                WHERE conversation_id = ?
                ORDER BY timestamp ASC
            ''', (conversation_id,))
            return [dict(row) for row in cursor.fetchall()]
        except sqlite3.Error as e:
            raise DatabaseError(f'Message retrieval failed: {str(e)}')

    def get_recent_messages(self, conversation_id: int, limit: int = 20) -> List[Dict[str, Any]]:
        """Get recent messages for a conversation in chronological order.

        Args:
            conversation_id: ID of the conversation
            limit: Maximum number of messages to return (default 20)

        Returns:
            List of message dictionaries, sorted by timestamp ascending

        Raises:
            DatabaseError: If retrieval fails
        """
        try:
            cursor = self.connection.cursor()
            cursor.execute('''
                SELECT * FROM assistant_messages 
                WHERE conversation_id = ?
                ORDER BY timestamp ASC
                LIMIT ?
            ''', (conversation_id, limit))
            return [dict(row) for row in cursor.fetchall()]
        except sqlite3.Error as e:
            raise DatabaseError(f'Message retrieval failed: {str(e)}')

    def get_conversation_for_session(self, session_id: int) -> Optional[Dict[str, Any]]:
        """Get the most recent conversation for a specific session.

        Args:
            session_id: ID of the session

        Returns:
            Conversation dictionary if exists, None otherwise

        Raises:
            DatabaseError: If retrieval fails
        """
        try:
            cursor = self.connection.cursor()
            cursor.execute('''
                SELECT * FROM assistant_conversations 
                WHERE session_id = ?
                ORDER BY updated_at DESC
                LIMIT 1
            ''', (session_id,))
            row = cursor.fetchone()
            return dict(row) if row else None
        except sqlite3.Error as e:
            raise DatabaseError(f'Conversation retrieval failed: {str(e)}')

    def get_or_create_conversation(self, session_id: Optional[int] = None, title: Optional[str] = None) -> int:
        """Get existing conversation for session or create a new one.

        If session_id is provided, finds the most recent conversation for that session
        and returns its ID. If none exists, creates a new conversation.

        Args:
            session_id: ID of the session (can be None for all-session scope)
            title: Optional title for new conversation

        Returns:
            ID of the existing or newly created conversation

        Raises:
            DatabaseError: If operation fails
        """
        try:
            if session_id is not None:
                existing = self.get_conversation_for_session(session_id)
                if existing:
                    return existing['id']
            # Create new conversation
            return self.create_conversation(session_id=session_id, title=title)
        except sqlite3.Error as e:
            raise DatabaseError(f'Get or create conversation failed: {str(e)}')

    def get_message(self, message_id: int) -> Dict[str, Any]:
        """Get a specific message by ID.

        Args:
            message_id: ID of the message

        Returns:
            Message dictionary

        Raises:
            DatabaseError: If retrieval fails
        """
        try:
            cursor = self.connection.cursor()
            cursor.execute('SELECT * FROM assistant_messages WHERE id = ?', (message_id,))
            row = cursor.fetchone()
            if row is None:
                raise DatabaseError(f'Message {message_id} not found')
            return dict(row)
        except sqlite3.Error as e:
            raise DatabaseError(f'Message retrieval failed: {str(e)}')

    def delete_message(self, message_id: int) -> None:
        """Delete a message.

        Args:
            message_id: ID of the message to delete

        Raises:
            DatabaseError: If deletion fails
        """
        try:
            cursor = self.connection.cursor()
            cursor.execute('DELETE FROM assistant_messages WHERE id = ?', (message_id,))
            self.connection.commit()
        except sqlite3.Error as e:
            raise DatabaseError(f'Message deletion failed: {str(e)}')

    def reset_database(self) -> None:
        """Delete all data from all tables but keep the schema.
        
        Useful for testing or starting fresh.
        
        Raises:
            DatabaseError: If reset fails
        """
        try:
            cursor = self.connection.cursor()
            cursor.execute('DELETE FROM assistant_messages')
            cursor.execute('DELETE FROM assistant_conversations')
            cursor.execute('DELETE FROM transcripts')
            cursor.execute('DELETE FROM screenshots')
            cursor.execute('DELETE FROM summaries')
            cursor.execute('DELETE FROM sessions')
            self.connection.commit()
        except sqlite3.Error as e:
            raise DatabaseError(f'Database reset failed: {str(e)}')

    # Search Methods (read-only, parameterized queries)

    def search_transcripts(self, query: str, limit: int = 10, session_id: Optional[int] = None) -> List[Dict[str, Any]]:
        """Search transcripts by text content using parameterized LIKE queries.

        Args:
            query: Search term to match against transcript text
            limit: Maximum number of results to return
            session_id: Optional session ID to limit search to a specific session

        Returns:
            List of transcript dictionaries with session metadata, sorted by timestamp

        Raises:
            DatabaseError: If search fails
        """
        try:
            cursor = self.connection.cursor()
            search_pattern = f'%{query}%'
            if session_id is not None:
                cursor.execute('''
                    SELECT t.*, s.name as session_name
                    FROM transcripts t
                    JOIN sessions s ON t.session_id = s.id
                    WHERE t.session_id = ? AND t.text LIKE ?
                    ORDER BY t.timestamp DESC
                    LIMIT ?
                ''', (session_id, search_pattern, limit))
            else:
                cursor.execute('''
                    SELECT t.*, s.name as session_name
                    FROM transcripts t
                    JOIN sessions s ON t.session_id = s.id
                    WHERE t.text LIKE ?
                    ORDER BY t.timestamp DESC
                    LIMIT ?
                ''', (search_pattern, limit))
            return [dict(row) for row in cursor.fetchall()]
        except sqlite3.Error as e:
            raise DatabaseError(f'Transcript search failed: {str(e)}')

    def search_summaries(self, query: str, limit: int = 10, session_id: Optional[int] = None) -> List[Dict[str, Any]]:
        """Search summaries by content using parameterized LIKE queries.

        Args:
            query: Search term to match against summary content
            limit: Maximum number of results to return
            session_id: Optional session ID to limit search to a specific session

        Returns:
            List of summary dictionaries with session metadata, sorted by created_at

        Raises:
            DatabaseError: If search fails
        """
        try:
            cursor = self.connection.cursor()
            search_pattern = f'%{query}%'
            if session_id is not None:
                cursor.execute('''
                    SELECT su.*, s.name as session_name
                    FROM summaries su
                    JOIN sessions s ON su.session_id = s.id
                    WHERE su.session_id = ? AND su.content LIKE ?
                    ORDER BY su.created_at DESC
                    LIMIT ?
                ''', (session_id, search_pattern, limit))
            else:
                cursor.execute('''
                    SELECT su.*, s.name as session_name
                    FROM summaries su
                    JOIN sessions s ON su.session_id = s.id
                    WHERE su.content LIKE ?
                    ORDER BY su.created_at DESC
                    LIMIT ?
                ''', (search_pattern, limit))
            return [dict(row) for row in cursor.fetchall()]
        except sqlite3.Error as e:
            raise DatabaseError(f'Summary search failed: {str(e)}')

    def find_sessions(self, query: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Find sessions by name, summary content, or transcript text.

        Searches across session names, summary content, and transcript text
        to find candidate sessions matching the query.

        Args:
            query: Search term to match against session names, summaries, or transcripts
            limit: Maximum number of sessions to return

        Returns:
            List of session dictionaries with matched content preview

        Raises:
            DatabaseError: If search fails
        """
        try:
            cursor = self.connection.cursor()
            search_pattern = f'%{query}%'
            # Search across session names, summaries, and transcripts
            cursor.execute('''
                SELECT DISTINCT s.*,
                       (SELECT su.content FROM summaries su WHERE su.session_id = s.id ORDER BY su.created_at DESC LIMIT 1) as matched_summary,
                       (SELECT t.text FROM transcripts t WHERE t.session_id = s.id ORDER BY t.timestamp DESC LIMIT 1) as matched_transcript
                FROM sessions s
                LEFT JOIN summaries su ON s.id = su.session_id AND su.content LIKE ?
                LEFT JOIN transcripts t ON s.id = t.session_id AND t.text LIKE ?
                WHERE s.name LIKE ? OR su.content LIKE ? OR t.text LIKE ?
                ORDER BY s.start_time DESC
                LIMIT ?
            ''', (search_pattern, search_pattern, search_pattern, search_pattern, search_pattern, limit))
            return [dict(row) for row in cursor.fetchall()]
        except sqlite3.Error as e:
            raise DatabaseError(f'Session search failed: {str(e)}')
