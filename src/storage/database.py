from pathlib import Path
import sqlite3
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
            self.connection = sqlite3.connect(self.db_path)
            self.connection.row_factory = sqlite3.Row
            self._initialize_schema()
            return self.connection
        except sqlite3.Error as e:
            raise DatabaseError(f'Database connection failed: {str(e)}')

    def disconnect(self):
        if self.connection:
            self.connection.close()
            self.connection = None

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
