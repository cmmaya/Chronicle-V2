from pathlib import Path
import logging
import sqlite3
import os
import re
import threading
from typing import Optional, List, Dict, Any, Iterable, Set, Tuple
from datetime import datetime

logger = logging.getLogger(__name__)

# Bump when a step is added to Database._migrate().
SCHEMA_VERSION = 3

# Session states that only exist while the app is running; finding one at
# startup means the app was closed or crashed mid-session.
_INTERRUPTED_STATUSES = ('active', 'paused', 'processing')


class DatabaseError(Exception):
    pass


# FTS5 bare operators that must never reach the query parser as syntax.
FTS_OPERATORS = frozenset({'AND', 'OR', 'NOT', 'NEAR'})

# Terms are whatever survives as word characters; every FTS5 metacharacter
# (" * ^ : - ( ) etc.) is discarded by construction.
_FTS_TERM_PATTERN = re.compile(r'\w+', re.UNICODE)


def sanitize_fts_query(query: str) -> str:
    """Turn arbitrary user text into a safe FTS5 MATCH expression.

    Punctuation and FTS5 metacharacters are dropped, bare operators are
    removed, and the remaining terms are quoted and joined with OR so that
    partial matches still rank.

    Returns an empty string when no usable term remains.
    """
    terms = [t for t in _FTS_TERM_PATTERN.findall(query or '')
             if t.upper() not in FTS_OPERATORS]
    if not terms:
        return ''
    return ' OR '.join('"{}"'.format(t) for t in terms)


class Database:
    """SQLite access for Chronicle.

    Every thread gets its own connection, opened on first use after
    ``connect()``: the app writes from the UI thread, the live transcription
    worker and background jobs, and one connection shared between them
    interleaves their transactions. The file runs in WAL mode so readers never
    block the writer. The schema is set up and migrated once per instance, not
    once per connection.
    """

    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            from ..paths import db_path as default_db_path
            db_path = str(default_db_path())
        self.db_path = db_path
        self._local = threading.local()
        self._lock = threading.Lock()  # guards _connections
        self._schema_lock = threading.Lock()
        self._connections: List[Tuple[threading.Thread, sqlite3.Connection]] = []
        self._shared_connection: Optional[sqlite3.Connection] = None
        self._connected = False
        self._schema_ready = False

    @property
    def _in_memory(self) -> bool:
        path = str(self.db_path)
        return path == ':memory:' or path.startswith('file::memory:')

    @property
    def connection(self) -> Optional[sqlite3.Connection]:
        """The calling thread's connection, or None when disconnected."""
        if not self._connected:
            return None
        if self._in_memory:
            # Every connection to ':memory:' is its own empty database, so an
            # in-memory Database keeps a single shared connection.
            return self._shared_connection
        conn = getattr(self._local, 'connection', None)
        if conn is None:
            conn = self._open_connection()
            self._local.connection = conn
        return conn

    def _open_connection(self) -> sqlite3.Connection:
        # check_same_thread=False only so disconnect() can close connections
        # that other threads opened; each connection is used by one thread.
        conn = sqlite3.connect(self.db_path, check_same_thread=False, timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA synchronous = NORMAL')  # durable enough under WAL
        conn.execute('PRAGMA temp_store = MEMORY')
        with self._lock:
            self._close_connections_of_dead_threads()
            self._connections.append((threading.current_thread(), conn))
        return conn

    def _close_connections_of_dead_threads(self) -> None:
        """Close connections whose owning thread has exited. Caller holds _lock."""
        alive = []
        for thread, conn in self._connections:
            if thread.is_alive():
                alive.append((thread, conn))
            else:
                try:
                    conn.close()
                except sqlite3.Error:
                    pass
        self._connections = alive

    def release_thread_connection(self) -> None:
        """Close the calling thread's connection.

        Background threads call this when they finish so their connections
        don't pile up; the thread gets a fresh one if it touches the DB again.
        """
        if self._in_memory:
            return
        conn = getattr(self._local, 'connection', None)
        if conn is None:
            return
        self._local.connection = None
        with self._lock:
            self._connections = [(t, c) for t, c in self._connections if c is not conn]
        try:
            conn.close()
        except sqlite3.Error:
            pass

    def connect(self) -> sqlite3.Connection:
        try:
            if not self._in_memory:
                Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
            self._connected = True
            if self._in_memory and self._shared_connection is None:
                self._shared_connection = self._open_connection()
            conn = self.connection
            with self._schema_lock:
                if not self._schema_ready:
                    self._initialize_schema(conn)
                    self._schema_ready = True
            return conn
        except sqlite3.Error as e:
            raise DatabaseError(f'Database connection failed: {str(e)}')

    def disconnect(self):
        """Close every connection this instance opened, on any thread."""
        with self._lock:
            connections, self._connections = self._connections, []
        for _thread, conn in connections:
            try:
                conn.close()
            except sqlite3.Error:
                pass
        self._local = threading.local()
        self._shared_connection = None
        self._connected = False

    def recreate_database(self) -> None:
        """Delete and recreate the database from scratch.

        WARNING: This will delete ALL data. Use reset_database() to keep
        schema but delete data, or this method to start completely fresh.

        Raises:
            DatabaseError: If recreation fails
        """
        # Close existing connection if any
        self.disconnect()

        # Delete existing database file (and its WAL side files)
        for suffix in ('', '-wal', '-shm'):
            if os.path.exists(self.db_path + suffix):
                os.remove(self.db_path + suffix)
        self._schema_ready = False

        # Reconnect (which will create fresh schema)
        self.connect()

    def _initialize_schema(self, conn: sqlite3.Connection):
        try:
            cursor = conn.cursor()
            had_schema = cursor.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'sessions'"
            ).fetchone() is not None
            version = cursor.execute('PRAGMA user_version').fetchone()[0]
            if not had_schema:
                # Only takes effect before the first table is created; existing
                # databases are switched over (with a VACUUM) in _migrate().
                cursor.execute('PRAGMA auto_vacuum = INCREMENTAL')

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    start_time INTEGER NOT NULL,
                    end_time INTEGER,
                    status TEXT NOT NULL,
                    transcription_status TEXT DEFAULT 'none',
                    summary_status TEXT DEFAULT 'none',
                    needs_finalize INTEGER NOT NULL DEFAULT 0
                )
            ''')
            # end_timestamp / audio_file: the chunk a line came from, so a batch
            # pass can skip audio that already has a transcript.
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS transcripts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id INTEGER NOT NULL,
                    timestamp INTEGER NOT NULL,
                    text TEXT NOT NULL,
                    source TEXT NOT NULL,
                    end_timestamp INTEGER,
                    audio_file TEXT,
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
                    ai_summary TEXT,
                    visible_text TEXT,
                    keywords TEXT,
                    preview_description TEXT,
                    preview_source TEXT,
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
            conn.commit()

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
            conn.commit()

            # RAG metadata tables
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS rag_documents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_type TEXT NOT NULL,
                    source_id INTEGER NOT NULL,
                    session_id INTEGER,
                    timestamp INTEGER,
                    title TEXT,
                    content_hash TEXT,
                    metadata_json TEXT,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                )
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS rag_chunks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    document_id INTEGER NOT NULL,
                    session_id INTEGER,
                    chunk_index INTEGER NOT NULL,
                    content TEXT NOT NULL,
                    token_count INTEGER,
                    start_timestamp INTEGER,
                    end_timestamp INTEGER,
                    source TEXT,
                    metadata_json TEXT,
                    embedding_model TEXT,
                    embedding BLOB,
                    created_at INTEGER NOT NULL,
                    FOREIGN KEY(document_id) REFERENCES rag_documents(id)
                )
            ''')

            # RAG indexes
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_rag_documents_source ON rag_documents(source_type, source_id)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_rag_documents_session ON rag_documents(session_id)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_rag_chunks_document ON rag_chunks(document_id)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_rag_chunks_session ON rag_chunks(session_id)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_rag_chunks_embedding_model ON rag_chunks(embedding_model)')

            # RAG FTS5 table for full-text search
            cursor.execute('''
                CREATE VIRTUAL TABLE IF NOT EXISTS rag_fts USING fts5(
                    content,
                    source_type UNINDEXED,
                    session_id UNINDEXED,
                    document_id UNINDEXED,
                    chunk_id UNINDEXED
                )
            ''')

            # Session router (BU089): one compact profile + embedding per session.
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS session_profiles (
                    session_id INTEGER PRIMARY KEY,
                    profile_text TEXT NOT NULL,
                    keywords TEXT,
                    embedding BLOB,
                    embedding_model TEXT,
                    content_hash TEXT,
                    updated_at INTEGER NOT NULL,
                    FOREIGN KEY(session_id) REFERENCES sessions(id)
                )
            ''')
            cursor.execute('''
                CREATE VIRTUAL TABLE IF NOT EXISTS session_profiles_fts USING fts5(
                    profile_text,
                    session_id UNINDEXED
                )
            ''')
            # Calendar events sent from a session's Due Dates (BU132). Keyed on
            # the entry fingerprint, not summary_id, so links survive
            # re-summarizing. SQLite only honours the CASCADE with
            # PRAGMA foreign_keys on, so session deletes also clear it by hand.
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS calendar_events (
                    id INTEGER PRIMARY KEY,
                    session_id INTEGER NOT NULL,
                    fingerprint TEXT NOT NULL,
                    google_event_id TEXT NOT NULL,
                    html_link TEXT,
                    event_date TEXT,
                    created_at REAL,
                    UNIQUE(session_id, fingerprint),
                    FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
                )
            ''')
            conn.commit()

            if version < SCHEMA_VERSION:
                can_prune = True
                if had_schema:
                    can_prune = self._backup_before_migration(conn, version)
                self._migrate(conn, version, had_schema, can_prune)
                cursor.execute(f'PRAGMA user_version = {SCHEMA_VERSION}')
                conn.commit()

            # Lookups by session / conversation. Created after _migrate() so the
            # columns that migrations add already exist.
            for statement in (
                'CREATE INDEX IF NOT EXISTS idx_sessions_start_time ON sessions(start_time)',
                'CREATE INDEX IF NOT EXISTS idx_transcripts_session_time ON transcripts(session_id, timestamp)',
                'CREATE INDEX IF NOT EXISTS idx_screenshots_session_time ON screenshots(session_id, timestamp)',
                'CREATE INDEX IF NOT EXISTS idx_screenshots_filepath ON screenshots(filepath)',
                'CREATE INDEX IF NOT EXISTS idx_summaries_session ON summaries(session_id, created_at)',
                'CREATE INDEX IF NOT EXISTS idx_conversations_session ON assistant_conversations(session_id)',
                'CREATE INDEX IF NOT EXISTS idx_conversations_updated ON assistant_conversations(updated_at)',
                'CREATE INDEX IF NOT EXISTS idx_messages_conversation ON assistant_messages(conversation_id, timestamp)',
            ):
                cursor.execute(statement)
            conn.commit()

            if not self._in_memory:
                # Persistent: readers stop blocking the writer (and vice versa).
                cursor.execute('PRAGMA journal_mode = WAL')

        except sqlite3.Error as e:
            raise DatabaseError(f'Schema initialization failed: {str(e)}')

    @staticmethod
    def _add_column(cursor: sqlite3.Cursor, table: str, column_sql: str) -> None:
        """``ALTER TABLE <table> ADD COLUMN <column_sql>`` unless it exists."""
        column = column_sql.split()[0]
        existing = {row[1] for row in cursor.execute(f'PRAGMA table_info({table})')}
        if column not in existing:
            cursor.execute(f'ALTER TABLE {table} ADD COLUMN {column_sql}')

    def _backup_before_migration(self, conn: sqlite3.Connection, version: int) -> bool:
        """Copy the database next to itself before a migration touches it.

        Returns False when the copy failed, in which case the migration skips
        its destructive steps.
        """
        if self._in_memory:
            return True
        stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        backup_path = f'{self.db_path}.bak-v{version}-{stamp}'
        try:
            backup = sqlite3.connect(backup_path)
            try:
                conn.backup(backup)
            finally:
                backup.close()
            logger.info(f'Backed up database to {backup_path} before migrating from v{version}')
            return True
        except sqlite3.Error as e:
            logger.warning(f'Could not back up database before migrating: {e}')
            return False

    def _migrate(self, conn: sqlite3.Connection, from_version: int,
                 had_schema: bool, can_prune: bool) -> None:
        """Bring a database from ``from_version`` up to SCHEMA_VERSION."""
        cursor = conn.cursor()
        if from_version < 1:
            # Columns added before the schema was versioned.
            for table, column_sql in (
                ('sessions', "transcription_status TEXT DEFAULT 'none'"),
                ('sessions', "summary_status TEXT DEFAULT 'none'"),
                ('screenshots', 'description TEXT'),
                ('screenshots', 'ai_summary TEXT'),
                ('screenshots', 'visible_text TEXT'),
                ('screenshots', 'keywords TEXT'),
                ('rag_chunks', 'source TEXT'),
            ):
                self._add_column(cursor, table, column_sql)
            conn.commit()

        if from_version < 2:
            self._add_column(cursor, 'transcripts', 'end_timestamp INTEGER')
            self._add_column(cursor, 'transcripts', 'audio_file TEXT')
            self._add_column(cursor, 'sessions', 'needs_finalize INTEGER NOT NULL DEFAULT 0')
            conn.commit()
            if had_schema and can_prune:
                removed = self._purge_orphans(cursor)
                conn.commit()
                if any(removed.values()):
                    logger.info(f'Removed rows left behind by deleted sessions: {removed}')
            # FTS rows now use the chunk id as rowid, so one document's rows can
            # be replaced without rebuilding the whole index.
            self._rebuild_rag_fts(cursor)
            conn.commit()
            if had_schema:
                cursor.execute('PRAGMA auto_vacuum = INCREMENTAL')
                cursor.execute('VACUUM')  # applies auto_vacuum, drops free pages

        if from_version < 3:
            # BU106: short retrieval-oriented description per screenshot.
            # preview_source is 'auto' (rebuilt from transcripts) or 'ai'.
            self._add_column(cursor, 'screenshots', 'preview_description TEXT')
            self._add_column(cursor, 'screenshots', 'preview_source TEXT')
            conn.commit()

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

    def get_session(self, session_id: int) -> Optional[Dict[str, Any]]:
        """Return one session row, or None if it doesn't exist."""
        try:
            cursor = self.connection.cursor()
            cursor.execute('SELECT * FROM sessions WHERE id = ?', (session_id,))
            row = cursor.fetchone()
            return dict(row) if row else None
        except sqlite3.Error as e:
            raise DatabaseError(f'Session retrieval failed: {str(e)}')

    def list_sessions_with_flags(self) -> List[Dict[str, Any]]:
        """All sessions, newest first, each with ``has_transcripts`` and
        ``has_summary`` booleans and a ``screenshot_count``, computed in the
        same query."""
        try:
            cursor = self.connection.cursor()
            cursor.execute('''
                SELECT s.*,
                       EXISTS (SELECT 1 FROM transcripts t WHERE t.session_id = s.id) AS has_transcripts,
                       EXISTS (SELECT 1 FROM summaries su WHERE su.session_id = s.id) AS has_summary,
                       (SELECT COUNT(*) FROM screenshots sc WHERE sc.session_id = s.id) AS screenshot_count
                FROM sessions s
                ORDER BY s.start_time DESC
            ''')
            rows = []
            for row in cursor.fetchall():
                item = dict(row)
                item['has_transcripts'] = bool(item['has_transcripts'])
                item['has_summary'] = bool(item['has_summary'])
                rows.append(item)
            return rows
        except sqlite3.Error as e:
            raise DatabaseError(f'Session listing failed: {str(e)}')

    def repair_interrupted_sessions(self) -> List[int]:
        """Close out sessions the app didn't get to stop.

        Call once at startup, before any session starts: a session still
        ``active`` / ``paused`` / ``processing`` then belongs to a run that
        was closed or crashed. It becomes ``stopped`` and is flagged for
        finalization (transcribe leftovers, index, summarize).

        Returns:
            The repaired session ids
        """
        try:
            cursor = self.connection.cursor()
            placeholders = ','.join('?' * len(_INTERRUPTED_STATUSES))
            ids = [row[0] for row in cursor.execute(
                f'SELECT id FROM sessions WHERE status IN ({placeholders})', _INTERRUPTED_STATUSES
            ).fetchall()]
            if ids:
                cursor.execute(f'''
                    UPDATE sessions
                    SET status = 'stopped', needs_finalize = 1,
                        end_time = COALESCE(end_time, (
                            SELECT MAX(COALESCE(t.end_timestamp, t.timestamp))
                            FROM transcripts t WHERE t.session_id = sessions.id))
                    WHERE status IN ({placeholders})
                ''', _INTERRUPTED_STATUSES)
                self.connection.commit()
            return ids
        except sqlite3.Error as e:
            raise DatabaseError(f'Session repair failed: {str(e)}')

    def list_sessions_needing_finalize(self) -> List[int]:
        """Ids of stopped sessions whose post-processing never completed."""
        try:
            cursor = self.connection.cursor()
            cursor.execute(
                "SELECT id FROM sessions WHERE needs_finalize = 1 AND status NOT IN ('active', 'paused') "
                'ORDER BY start_time'
            )
            return [row[0] for row in cursor.fetchall()]
        except sqlite3.Error as e:
            raise DatabaseError(f'Session listing failed: {str(e)}')

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
            cursor.execute('DELETE FROM calendar_events WHERE session_id = ?', (session_id,))
            cursor.execute('DELETE FROM sessions WHERE id = ?', (session_id,))
            self.connection.commit()
        except sqlite3.Error as e:
            raise DatabaseError(f'Session deletion failed: {str(e)}')

    def delete_screenshots(self, session_id: int) -> None:
        """Delete all screenshot rows for a session (does not touch image files)."""
        try:
            cursor = self.connection.cursor()
            cursor.execute('DELETE FROM screenshots WHERE session_id = ?', (session_id,))
            self.connection.commit()
        except sqlite3.Error as e:
            raise DatabaseError(f'Screenshot deletion failed: {str(e)}')

    def purge_session(self, session_id: int) -> None:
        """Permanently delete a session and every DB row that belongs to it.

        Removes, in a single transaction: transcripts, screenshot rows,
        summaries, assistant conversations + their messages, the RAG
        documents / chunks / FTS mirror, the session-router profile, and
        finally the ``sessions`` row itself. Files on disk (audio,
        screenshot images, the session folder) are the caller's
        responsibility since this class does not own the filesystem layout.
        """
        try:
            cursor = self.connection.cursor()

            # RAG index. FTS rows share their chunk's id as rowid, so they go
            # by rowid; the session_id sweep catches rows written before that.
            doc_ids = [row[0] for row in cursor.execute(
                'SELECT id FROM rag_documents WHERE session_id = ?', (session_id,)
            ).fetchall()]
            chunk_ids = [row[0] for row in cursor.execute(
                'SELECT id FROM rag_chunks WHERE session_id = ?', (session_id,)
            ).fetchall()]
            if doc_ids:
                placeholders = ','.join('?' * len(doc_ids))
                chunk_ids += [row[0] for row in cursor.execute(
                    f'SELECT id FROM rag_chunks WHERE document_id IN ({placeholders})', doc_ids
                ).fetchall()]
                cursor.execute(f'DELETE FROM rag_chunks WHERE document_id IN ({placeholders})', doc_ids)
            self._delete_fts_rows(cursor, chunk_ids)
            cursor.execute('DELETE FROM rag_fts WHERE session_id = ?', (session_id,))
            cursor.execute('DELETE FROM rag_chunks WHERE session_id = ?', (session_id,))
            cursor.execute('DELETE FROM rag_documents WHERE session_id = ?', (session_id,))

            # Session-router profile (BU089).
            cursor.execute('DELETE FROM session_profiles_fts WHERE session_id = ?', (session_id,))
            cursor.execute('DELETE FROM session_profiles WHERE session_id = ?', (session_id,))

            # Assistant chat history scoped to this session.
            conv_ids = [row[0] for row in cursor.execute(
                'SELECT id FROM assistant_conversations WHERE session_id = ?', (session_id,)
            ).fetchall()]
            for conv_id in conv_ids:
                cursor.execute(
                    'DELETE FROM assistant_messages WHERE conversation_id = ?', (conv_id,)
                )
            cursor.execute(
                'DELETE FROM assistant_conversations WHERE session_id = ?', (session_id,)
            )

            # Core session content.
            cursor.execute('DELETE FROM summaries WHERE session_id = ?', (session_id,))
            cursor.execute('DELETE FROM transcripts WHERE session_id = ?', (session_id,))
            cursor.execute('DELETE FROM screenshots WHERE session_id = ?', (session_id,))
            cursor.execute('DELETE FROM calendar_events WHERE session_id = ?', (session_id,))
            cursor.execute('DELETE FROM sessions WHERE id = ?', (session_id,))

            self.connection.commit()
        except sqlite3.Error as e:
            self.connection.rollback()
            raise DatabaseError(f'Session purge failed: {str(e)}')
        self._reclaim_free_pages()

    def _reclaim_free_pages(self) -> None:
        """Hand freed pages back to the OS (a no-op unless auto_vacuum is
        INCREMENTAL, which new and migrated databases are)."""
        try:
            self.connection.execute('PRAGMA incremental_vacuum')
            self.connection.commit()
        except sqlite3.Error as e:
            logger.debug(f'incremental_vacuum skipped: {e}')

    @staticmethod
    def _purge_orphans(cursor: sqlite3.Cursor) -> Dict[str, int]:
        """Delete rows whose session (or parent row) no longer exists.

        Sessions deleted before ``purge_session`` existed left their search
        index, router profile, chats and screenshot rows behind - and the
        search index could still surface them. Returns rows removed per table.
        """
        orphan_docs = '''(SELECT id FROM rag_documents
                          WHERE (session_id IS NOT NULL AND session_id NOT IN (SELECT id FROM sessions))
                             OR (source_type = 'summary' AND source_id NOT IN (SELECT id FROM summaries)))'''
        orphan_convs = '''(SELECT id FROM assistant_conversations
                           WHERE session_id IS NOT NULL AND session_id NOT IN (SELECT id FROM sessions))'''
        statements = (
            ('rag_fts', f'''DELETE FROM rag_fts WHERE document_id IN {orphan_docs}
                            OR document_id NOT IN (SELECT id FROM rag_documents)'''),
            ('rag_chunks', f'''DELETE FROM rag_chunks WHERE document_id IN {orphan_docs}
                               OR document_id NOT IN (SELECT id FROM rag_documents)'''),
            ('rag_documents', f'DELETE FROM rag_documents WHERE id IN {orphan_docs}'),
            ('session_profiles', 'DELETE FROM session_profiles WHERE session_id NOT IN (SELECT id FROM sessions)'),
            ('session_profiles_fts', 'DELETE FROM session_profiles_fts WHERE session_id NOT IN (SELECT id FROM sessions)'),
            ('assistant_messages', f'''DELETE FROM assistant_messages WHERE conversation_id IN {orphan_convs}
                                       OR conversation_id NOT IN (SELECT id FROM assistant_conversations)'''),
            ('assistant_conversations', f'DELETE FROM assistant_conversations WHERE id IN {orphan_convs}'),
            ('transcripts', 'DELETE FROM transcripts WHERE session_id NOT IN (SELECT id FROM sessions)'),
            ('screenshots', 'DELETE FROM screenshots WHERE session_id NOT IN (SELECT id FROM sessions)'),
            ('summaries', 'DELETE FROM summaries WHERE session_id NOT IN (SELECT id FROM sessions)'),
            ('calendar_events', 'DELETE FROM calendar_events WHERE session_id NOT IN (SELECT id FROM sessions)'),
        )
        removed = {}
        for table, sql in statements:
            cursor.execute(sql)
            removed[table] = cursor.rowcount
        return removed

    def purge_orphans(self) -> Dict[str, int]:
        """Remove rows left behind by deleted sessions. Safe to run any time."""
        try:
            cursor = self.connection.cursor()
            removed = self._purge_orphans(cursor)
            self.connection.commit()
        except sqlite3.Error as e:
            self.connection.rollback()
            raise DatabaseError(f'Orphan cleanup failed: {str(e)}')
        if any(removed.values()):
            self._reclaim_free_pages()
        return removed

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

    def update_screenshot_preview(self, screenshot_id: int, text: str, source: str) -> None:
        """Set a screenshot's preliminary description (BU106).

        ``source`` is 'auto' (derived from transcripts) or 'ai' (vision model).
        """
        try:
            cursor = self.connection.cursor()
            cursor.execute(
                'UPDATE screenshots SET preview_description = ?, preview_source = ? WHERE id = ?',
                (text, source, screenshot_id)
            )
            self.connection.commit()
        except sqlite3.Error as e:
            raise DatabaseError(f'Screenshot preview update failed: {str(e)}')

    def get_screenshot_previews(self, session_id: int) -> List[Dict[str, Any]]:
        """Tier-1 read of a session's screenshots: the light fields only.

        Leaves out ai_summary / visible_text so retrieval can rank screenshots
        without loading their full metadata.
        """
        try:
            cursor = self.connection.cursor()
            cursor.execute('''
                SELECT id, session_id, timestamp, filepath, description,
                       preview_description, preview_source, keywords
                FROM screenshots
                WHERE session_id = ?
                ORDER BY timestamp ASC
            ''', (session_id,))
            return [dict(row) for row in cursor.fetchall()]
        except sqlite3.Error as e:
            raise DatabaseError(f'Screenshot preview retrieval failed: {str(e)}')

    def get_screenshot_details(self, ids: Iterable[int]) -> List[Dict[str, Any]]:
        """Tier-2 read: full metadata for the given screenshot ids, one query."""
        ids = [int(i) for i in ids]
        if not ids:
            return []
        try:
            cursor = self.connection.cursor()
            placeholders = ','.join('?' * len(ids))
            cursor.execute(f'''
                SELECT id, session_id, timestamp, filepath, description,
                       ai_summary, visible_text, keywords
                FROM screenshots
                WHERE id IN ({placeholders})
            ''', ids)
            return [dict(row) for row in cursor.fetchall()]
        except sqlite3.Error as e:
            raise DatabaseError(f'Screenshot detail retrieval failed: {str(e)}')

    def get_searchable_screenshots(self, session_id: Optional[int] = None) -> List[Dict[str, Any]]:
        """Screenshots that have visible text to search, newest first, with
        their session name (BU110 visible-text search).

        Matching itself is done by the caller: stored JSON escapes accents,
        so SQL LIKE can't match accent-insensitively.
        """
        where = "(sc.visible_text IS NOT NULL AND sc.visible_text NOT IN ('', '[]'))"
        params: list = []
        if session_id is not None:
            where += ' AND sc.session_id = ?'
            params.append(session_id)
        try:
            cursor = self.connection.cursor()
            cursor.execute(f'''
                SELECT sc.*, s.name AS session_name
                FROM screenshots sc
                JOIN sessions s ON s.id = sc.session_id
                WHERE {where}
                ORDER BY sc.timestamp DESC
            ''', params)
            return [dict(row) for row in cursor.fetchall()]
        except sqlite3.Error as e:
            raise DatabaseError(f'Screenshot search failed: {str(e)}')

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


    def add_transcript(self, session_id: int, timestamp: datetime, text: str, source: str,
                       end_timestamp: Optional[datetime] = None,
                       audio_file: Optional[str] = None) -> int:
        """Add a transcript entry to the database.

        Args:
            session_id: ID of the session this transcript belongs to
            timestamp: Timestamp when the audio was recorded
            text: Transcribed text
            source: Audio source ('microphone' or 'system')
            end_timestamp: When the transcribed audio ends, if known
            audio_file: The chunk the text came from, as ``<source dir>/<file name>``
                relative to the session's ``audio`` folder

        Returns:
            ID of the inserted transcript

        Raises:
            DatabaseError: If insertion fails
        """
        try:
            cursor = self.connection.cursor()
            cursor.execute('''
                INSERT INTO transcripts (session_id, timestamp, text, source, end_timestamp, audio_file)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (
                session_id,
                int(timestamp.timestamp()),
                text,
                source,
                int(end_timestamp.timestamp()) if end_timestamp else None,
                audio_file,
            ))
            self.connection.commit()
            return cursor.lastrowid
        except sqlite3.Error as e:
            raise DatabaseError(f'Transcript insertion failed: {str(e)}')

    def has_transcripts(self, session_id: int) -> bool:
        """True if the session has at least one transcript line."""
        try:
            cursor = self.connection.cursor()
            cursor.execute('SELECT 1 FROM transcripts WHERE session_id = ? LIMIT 1', (session_id,))
            return cursor.fetchone() is not None
        except sqlite3.Error as e:
            raise DatabaseError(f'Transcript lookup failed: {str(e)}')

    def get_transcribed_chunk_keys(self, session_id: int) -> Tuple[Set[str], Set[Tuple[str, int]]]:
        """What audio of a session already has transcripts.

        Returns ``(audio_files, legacy_keys)``: the ``audio_file`` of every row
        that records one, and ``(source, timestamp)`` for older rows that
        don't - a chunk's file name starts with its start time, so those still
        identify the chunk.
        """
        try:
            cursor = self.connection.cursor()
            cursor.execute(
                'SELECT source, timestamp, audio_file FROM transcripts WHERE session_id = ?',
                (session_id,),
            )
            audio_files: Set[str] = set()
            legacy_keys: Set[Tuple[str, int]] = set()
            for source, timestamp, audio_file in cursor.fetchall():
                if audio_file:
                    audio_files.add(audio_file)
                else:
                    legacy_keys.add((source, int(timestamp)))
            return audio_files, legacy_keys
        except sqlite3.Error as e:
            raise DatabaseError(f'Transcript lookup failed: {str(e)}')

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
                ORDER BY timestamp ASC, id ASC
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

    def get_calendar_link(self, session_id: int, fingerprint: str) -> Optional[Dict[str, Any]]:
        """The calendar event sent for a Due Dates entry, or None (BU132)."""
        try:
            row = self.connection.execute(
                'SELECT * FROM calendar_events WHERE session_id = ? AND fingerprint = ?',
                (session_id, fingerprint),
            ).fetchone()
            return dict(row) if row else None
        except sqlite3.Error as e:
            raise DatabaseError(f'Calendar link lookup failed: {str(e)}')

    def save_calendar_link(self, session_id: int, fingerprint: str, google_event_id: str,
                           html_link: Optional[str] = None,
                           event_date: Optional[str] = None) -> None:
        """Record (or replace) the calendar event sent for a Due Dates entry."""
        try:
            self.connection.execute('''
                INSERT INTO calendar_events
                    (session_id, fingerprint, google_event_id, html_link, event_date, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id, fingerprint) DO UPDATE SET
                    google_event_id = excluded.google_event_id,
                    html_link = excluded.html_link,
                    event_date = excluded.event_date,
                    created_at = excluded.created_at
            ''', (session_id, fingerprint, google_event_id, html_link, event_date,
                  datetime.now().timestamp()))
            self.connection.commit()
        except sqlite3.Error as e:
            raise DatabaseError(f'Calendar link save failed: {str(e)}')

    def delete_calendar_link(self, session_id: int, fingerprint: str) -> None:
        try:
            self.connection.execute(
                'DELETE FROM calendar_events WHERE session_id = ? AND fingerprint = ?',
                (session_id, fingerprint),
            )
            self.connection.commit()
        except sqlite3.Error as e:
            raise DatabaseError(f'Calendar link deletion failed: {str(e)}')

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

        Removes the ``assistant_messages`` rows then the
        ``assistant_conversations`` row in one transaction. A silent no-op if
        ``conversation_id`` does not exist (0 rows deleted, no error).

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
                ORDER BY timestamp ASC, id ASC
            ''', (conversation_id,))
            return [dict(row) for row in cursor.fetchall()]
        except sqlite3.Error as e:
            raise DatabaseError(f'Message retrieval failed: {str(e)}')

    def get_recent_messages(self, conversation_id: int, limit: int = 20) -> List[Dict[str, Any]]:
        """Get the last ``limit`` messages of a conversation, oldest first.

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
                SELECT * FROM (
                    SELECT * FROM assistant_messages
                    WHERE conversation_id = ?
                    ORDER BY timestamp DESC, id DESC
                    LIMIT ?
                ) ORDER BY timestamp ASC, id ASC
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

    @staticmethod
    def _like_terms(query: str) -> List[str]:
        """Split a search query into whitespace-separated terms and turn each into
        a literal ``LIKE`` pattern.

        Backslash, ``%`` and ``_`` inside a term are escaped with ``\\`` so the
        term matches literally instead of as a wildcard; use each pattern with
        ``LIKE ? ESCAPE '\\'``. Returns ``[]`` for an empty / whitespace-only
        query so callers can skip the database entirely.
        """
        patterns = []
        for term in query.split():
            escaped = term.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
            patterns.append(f'%{escaped}%')
        return patterns

    def search_transcripts(self, query: str, limit: int = 10, session_id: Optional[int] = None) -> List[Dict[str, Any]]:
        """Search transcripts by text content using parameterized LIKE queries.

        The query is split on whitespace into terms which are AND-matched: a
        transcript is returned only if its ``text`` contains every term
        (case-insensitive). ``%``, ``_`` and ``\\`` in a term are escaped so they
        match literally. An empty / whitespace-only query returns ``[]`` without
        touching the database. A single-word query collapses to one ``LIKE``.

        Args:
            query: One or more space-separated keywords to match against transcript text
            limit: Maximum number of results to return
            session_id: Optional session ID to limit search to a specific session

        Returns:
            List of transcript dictionaries with session metadata, sorted by timestamp DESC

        Raises:
            DatabaseError: If search fails
        """
        patterns = self._like_terms(query)
        if not patterns:
            return []
        try:
            cursor = self.connection.cursor()
            text_conditions = ' AND '.join("t.text LIKE ? ESCAPE '\\'" for _ in patterns)
            if session_id is not None:
                where = f't.session_id = ? AND {text_conditions}'
                params = [session_id, *patterns, limit]
            else:
                where = text_conditions
                params = [*patterns, limit]
            cursor.execute(f'''
                SELECT t.*, s.name as session_name
                FROM transcripts t
                JOIN sessions s ON t.session_id = s.id
                WHERE {where}
                ORDER BY t.timestamp DESC
                LIMIT ?
            ''', params)
            return [dict(row) for row in cursor.fetchall()]
        except sqlite3.Error as e:
            raise DatabaseError(f'Transcript search failed: {str(e)}')

    def search_conversations(self, query: str, limit: int = 20) -> List[Dict[str, Any]]:
        """Search assistant conversations by keyword across title and message content.

        The query is split on whitespace into terms which are AND-matched: a
        conversation is returned only when every term appears somewhere in the
        union of its ``title`` and its messages' ``content`` (case-insensitive;
        ``%``, ``_`` and ``\\`` escaped to match literally). Each conversation
        appears exactly once regardless of how many of its messages match. An
        empty / whitespace-only query returns ``[]`` without touching the database.

        Args:
            query: One or more space-separated keywords
            limit: Maximum number of conversations to return

        Returns:
            List of ``assistant_conversations`` row dicts, each with two extra
            keys: ``match_count`` (number of the conversation's messages matching
            any term) and ``snippet`` (first matching message content trimmed to
            ~160 chars, falling back to the title or ``""``). Sorted by
            ``updated_at`` DESC.

        Raises:
            DatabaseError: If search fails
        """
        patterns = self._like_terms(query)
        if not patterns:
            return []
        try:
            cursor = self.connection.cursor()
            term_clause = ' AND '.join(
                "(c.title LIKE ? ESCAPE '\\' OR EXISTS ("
                "SELECT 1 FROM assistant_messages m "
                "WHERE m.conversation_id = c.id AND m.content LIKE ? ESCAPE '\\'))"
                for _ in patterns
            )
            match_params = []
            for pattern in patterns:
                match_params.extend((pattern, pattern))
            cursor.execute(f'''
                SELECT c.* FROM assistant_conversations c
                WHERE {term_clause}
                ORDER BY c.updated_at DESC
                LIMIT ?
            ''', (*match_params, limit))
            conversations = [dict(row) for row in cursor.fetchall()]

            any_term = ' OR '.join("content LIKE ? ESCAPE '\\'" for _ in patterns)
            results = []
            for conv in conversations:
                cursor.execute(f'''
                    SELECT content FROM assistant_messages
                    WHERE conversation_id = ? AND ({any_term})
                    ORDER BY timestamp ASC, id ASC
                ''', (conv['id'], *patterns))
                matches = [row['content'] for row in cursor.fetchall()]
                conv['match_count'] = len(matches)
                snippet = (matches[0] if matches else (conv.get('title') or '')).strip()
                if len(snippet) > 160:
                    snippet = snippet[:160].rstrip() + '…'
                conv['snippet'] = snippet
                results.append(conv)
            return results
        except sqlite3.Error as e:
            raise DatabaseError(f'Conversation search failed: {str(e)}')

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

    def upsert_rag_document(self, source_type: str, source_id: int, session_id: int,
                            timestamp: int, title: str, content_hash: str,
                            metadata_json: str) -> int:
        """Insert or replace a RAG document by source identity.

        Uses source_type + source_id as the logical identity for upsert.
        
        Args:
            source_type: Type of source (e.g., 'transcript', 'summary', 'screenshot')
            source_id: ID from the source table
            session_id: ID of the associated session
            timestamp: Unix timestamp
            title: Document title
            content_hash: Hash of the content for deduplication
            metadata_json: JSON string with additional metadata
            
        Returns:
            ID of the inserted or updated document
            
        Raises:
            DatabaseError: If the operation fails
        """
        try:
            cursor = self.connection.cursor()
            now = int(datetime.now().timestamp())
            
            # First try to update existing record
            cursor.execute('''
                UPDATE rag_documents SET
                    session_id = ?,
                    timestamp = ?,
                    title = ?,
                    content_hash = ?,
                    metadata_json = ?,
                    updated_at = ?
                WHERE source_type = ? AND source_id = ?
            ''', (session_id, timestamp, title, content_hash, metadata_json, now, source_type, source_id))
            
            if cursor.rowcount == 0:
                # No existing record, insert new one
                cursor.execute('''
                    INSERT INTO rag_documents (source_type, source_id, session_id, timestamp,
                                              title, content_hash, metadata_json, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (source_type, source_id, session_id, timestamp, title, content_hash,
                     metadata_json, now, now))
            
            self.connection.commit()
            
            # Get the document ID (works for both insert and update)
            cursor.execute(
                'SELECT id FROM rag_documents WHERE source_type = ? AND source_id = ?',
                (source_type, source_id)
            )
            row = cursor.fetchone()
            return row[0] if row else cursor.lastrowid
        except sqlite3.Error as e:
            raise DatabaseError(f'RAG document upsert failed: {str(e)}')

    def replace_rag_chunks(self, document_id: int, chunks: List[Dict[str, Any]]) -> None:
        """Replace all chunks for a document atomically.

        Deletes all existing chunks for the document and inserts the provided
        chunks in a single transaction, keeping the document's FTS rows in
        step (so indexing one session never rebuilds the whole FTS index).

        Args:
            document_id: ID of the document whose chunks to replace
            chunks: List of chunk dictionaries with keys:
                    - content: str
                    - chunk_index: int
                    - token_count: int (optional)
                    - start_timestamp: int (optional)
                    - end_timestamp: int (optional)
                    - source: str (optional, e.g. 'microphone' / 'system')
                    - metadata_json: str (optional)
                    - session_id: int (optional)
                    
        Raises:
            DatabaseError: If the operation fails
        """
        try:
            cursor = self.connection.cursor()
            now = int(datetime.now().timestamp())
            
            # Get session_id from the document for chunk insertion
            cursor.execute('SELECT session_id, source_type FROM rag_documents WHERE id = ?', (document_id,))
            row = cursor.fetchone()
            if not row:
                raise DatabaseError(f'Document {document_id} not found')
            default_session_id, source_type = row[0], row[1]

            old_chunk_ids = [r[0] for r in cursor.execute(
                'SELECT id FROM rag_chunks WHERE document_id = ?', (document_id,)
            ).fetchall()]
            self._delete_fts_rows(cursor, old_chunk_ids)
            cursor.execute('DELETE FROM rag_chunks WHERE document_id = ?', (document_id,))

            for chunk in chunks:
                cursor.execute('''
                    INSERT INTO rag_chunks (document_id, session_id, chunk_index, content,
                                          token_count, start_timestamp, end_timestamp,
                                          source, metadata_json, embedding_model, embedding,
                                          created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    document_id,
                    chunk.get('session_id', default_session_id),
                    chunk.get('chunk_index', 0),
                    chunk['content'],
                    chunk.get('token_count'),
                    chunk.get('start_timestamp'),
                    chunk.get('end_timestamp'),
                    chunk.get('source'),
                    chunk.get('metadata_json'),
                    chunk.get('embedding_model'),
                    chunk.get('embedding'),
                    now
                ))

            cursor.execute('''
                INSERT INTO rag_fts (rowid, content, source_type, session_id, document_id, chunk_id)
                SELECT rc.id, rc.content, ?, rc.session_id, rc.document_id, rc.id
                FROM rag_chunks rc
                WHERE rc.document_id = ?
            ''', (source_type, document_id))

            self.connection.commit()
        except sqlite3.Error as e:
            self.connection.rollback()
            raise DatabaseError(f'Chunk replacement failed: {str(e)}')

    @staticmethod
    def _delete_fts_rows(cursor: sqlite3.Cursor, chunk_ids: Iterable[int]) -> None:
        """Delete FTS rows by rowid (= chunk id), in batches under SQLite's
        bound-parameter limit."""
        ids = list(dict.fromkeys(chunk_ids))
        for start in range(0, len(ids), 500):
            batch = ids[start:start + 500]
            cursor.execute(
                f'DELETE FROM rag_fts WHERE rowid IN ({",".join("?" * len(batch))})', batch
            )

    @staticmethod
    def _rebuild_rag_fts(cursor: sqlite3.Cursor) -> None:
        cursor.execute('DELETE FROM rag_fts')
        cursor.execute('''
            INSERT INTO rag_fts (rowid, content, source_type, session_id, document_id, chunk_id)
            SELECT rc.id, rc.content, rd.source_type, rc.session_id, rc.document_id, rc.id
            FROM rag_chunks rc
            JOIN rag_documents rd ON rc.document_id = rd.id
        ''')

    def get_rag_document(self, source_type: str, source_id: int) -> Optional[Dict[str, Any]]:
        """Return one RAG document row by source identity, or ``None``.

        Used by the indexer to decide whether a re-chunk is needed: if the stored
        ``content_hash`` matches the freshly computed one, nothing has changed.
        """
        try:
            cursor = self.connection.cursor()
            cursor.execute(
                'SELECT * FROM rag_documents WHERE source_type = ? AND source_id = ?',
                (source_type, source_id),
            )
            row = cursor.fetchone()
            return dict(row) if row else None
        except sqlite3.Error as e:
            raise DatabaseError(f'RAG document retrieval failed: {str(e)}')

    def count_chunks_missing_embedding(
        self, document_id: int, embedding_model: Optional[str]
    ) -> int:
        """How many of a document's chunks lack a vector for ``embedding_model``.

        A non-zero count means the document must be re-embedded - either it was
        never embedded, or the embedding model / revision has changed since.
        """
        try:
            cursor = self.connection.cursor()
            if embedding_model is None:
                cursor.execute(
                    'SELECT COUNT(*) FROM rag_chunks WHERE document_id = ?',
                    (document_id,),
                )
            else:
                cursor.execute(
                    'SELECT COUNT(*) FROM rag_chunks '
                    'WHERE document_id = ? '
                    'AND (embedding IS NULL OR embedding_model IS NOT ?)',
                    (document_id, embedding_model),
                )
            row = cursor.fetchone()
            return int(row[0]) if row else 0
        except sqlite3.Error as e:
            raise DatabaseError(f'Chunk embedding count failed: {str(e)}')

    def rebuild_rag_fts(self) -> None:
        """Rebuild the RAG FTS index from existing rag_chunks.

        Deletes all existing FTS entries and re-indexes all chunks from rag_chunks
        joined with rag_documents. This ensures the FTS index stays in sync
        with the source tables.

        Raises:
            DatabaseError: If the rebuild fails
        """
        try:
            cursor = self.connection.cursor()
            self._rebuild_rag_fts(cursor)
            self.connection.commit()
        except sqlite3.Error as e:
            self.connection.rollback()
            raise DatabaseError(f'FTS rebuild failed: {str(e)}')

    def search_rag_fts(self, query: str, limit: int = 20, session_id: Optional[int] = None,
                       source_types: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """Search the RAG FTS index and return source-aware results.

        Args:
            query: Search query string (required, non-empty)
            limit: Maximum number of results (default 20, capped at 50)
            session_id: Optional session ID to filter results to a specific session
            source_types: Optional list of source types to filter results

        Returns:
            List of result dictionaries with keys:
                - chunk_id: int
                - document_id: int
                - source_type: str
                - source_id: int
                - session_id: int
                - timestamp: int (chunk start time, falling back to the
                  document timestamp for chunks indexed before BU087)
                - start_timestamp: int | None (per-chunk)
                - end_timestamp: int | None (per-chunk)
                - source: str | None (per-chunk, 'microphone' / 'system')
                - title: str
                - content: str
                - rank: float (BM25 rank, lower is better)

            Results are ordered by ascending BM25 rank (most relevant first).
            Returns an empty list when the query contains no usable search
            terms after FTS5 sanitisation.

        Raises:
            DatabaseError: If search fails or query is empty
        """
        # Validate query
        if not query or not query.strip():
            raise DatabaseError('Query cannot be empty')

        # Cap limit
        limit = min(max(1, limit), 50)

        try:
            cursor = self.connection.cursor()

            # Build the FTS5 search query with bm25 ranking
            fts_query = sanitize_fts_query(query)
            if not fts_query:
                # Nothing searchable survived sanitisation; never fall back to
                # an unfiltered scan.
                return []

            # Build SQL with optional filters
            sql_parts = ['''
                SELECT 
                    f.chunk_id,
                    f.document_id,
                    f.source_type,
                    rd.source_id,
                    COALESCE(f.session_id, rd.session_id) as session_id,
                    COALESCE(rc.start_timestamp, rd.timestamp) as timestamp,
                    rc.start_timestamp,
                    rc.end_timestamp,
                    rc.source,
                    rd.title,
                    f.content,
                    bm25(rag_fts) as rank
                FROM rag_fts f
                JOIN rag_documents rd ON f.document_id = rd.id
                LEFT JOIN rag_chunks rc ON rc.id = f.chunk_id
                LEFT JOIN sessions s ON s.id = rd.session_id
            ''']

            # MATCH is mandatory: without it every chunk is returned unranked.
            # Chunks of a deleted session never surface, even if a delete
            # path left them behind.
            where_clauses = ['f.content MATCH ?', '(rd.session_id IS NULL OR s.id IS NOT NULL)']
            params = [fts_query]

            # Add session_id filter if provided
            if session_id is not None:
                where_clauses.append('f.session_id = ?')
                params.append(session_id)

            # Add source_types filter if provided
            if source_types:
                placeholders = ','.join('?' * len(source_types))
                where_clauses.append(f'f.source_type IN ({placeholders})')
                params.extend(source_types)

            sql_parts.append('WHERE ' + ' AND '.join(where_clauses))

            # Add ORDER BY and LIMIT
            sql_parts.append('ORDER BY rank')
            sql_parts.append('LIMIT ?')
            params.append(limit)

            sql = ' '.join(sql_parts)
            cursor.execute(sql, params)

            return [dict(row) for row in cursor.fetchall()]
        except sqlite3.Error as e:
            raise DatabaseError(f'FTS search failed: {str(e)}')

    # -- Session router profiles (BU089) ------------------------------------

    def upsert_session_profile(
        self,
        session_id: int,
        profile_text: str,
        keywords: Optional[str] = None,
        embedding: Optional[bytes] = None,
        embedding_model: Optional[str] = None,
        content_hash: Optional[str] = None,
    ) -> None:
        """Insert or replace the router profile for a session.

        Also refreshes the session's row in ``session_profiles_fts`` so the
        lexical half of the router stays in sync.
        """
        try:
            cursor = self.connection.cursor()
            now = int(datetime.now().timestamp())
            cursor.execute('''
                INSERT INTO session_profiles
                    (session_id, profile_text, keywords, embedding,
                     embedding_model, content_hash, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    profile_text = excluded.profile_text,
                    keywords = excluded.keywords,
                    embedding = excluded.embedding,
                    embedding_model = excluded.embedding_model,
                    content_hash = excluded.content_hash,
                    updated_at = excluded.updated_at
            ''', (session_id, profile_text, keywords, embedding,
                  embedding_model, content_hash, now))

            cursor.execute(
                'DELETE FROM session_profiles_fts WHERE session_id = ?',
                (session_id,),
            )
            cursor.execute(
                'INSERT INTO session_profiles_fts (profile_text, session_id) '
                'VALUES (?, ?)',
                (profile_text, session_id),
            )
            self.connection.commit()
        except sqlite3.Error as e:
            self.connection.rollback()
            raise DatabaseError(f'Session profile upsert failed: {str(e)}')

    def get_session_profiles(self) -> List[Dict[str, Any]]:
        """Return every session profile joined with its session name and start time."""
        try:
            cursor = self.connection.cursor()
            cursor.execute('''
                SELECT sp.session_id, sp.profile_text, sp.keywords, sp.embedding,
                       sp.embedding_model, sp.content_hash, sp.updated_at,
                       s.name AS session_name, s.start_time
                FROM session_profiles sp
                JOIN sessions s ON s.id = sp.session_id
                ORDER BY s.start_time DESC
            ''')
            return [dict(row) for row in cursor.fetchall()]
        except sqlite3.Error as e:
            raise DatabaseError(f'Session profile listing failed: {str(e)}')

    def get_session_profile(self, session_id: int) -> Optional[Dict[str, Any]]:
        """Return one session profile row, or ``None``."""
        try:
            cursor = self.connection.cursor()
            cursor.execute(
                'SELECT * FROM session_profiles WHERE session_id = ?',
                (session_id,),
            )
            row = cursor.fetchone()
            return dict(row) if row else None
        except sqlite3.Error as e:
            raise DatabaseError(f'Session profile retrieval failed: {str(e)}')

    def session_profiles_signature(self) -> tuple:
        """Cheap (count, max updated_at) signature for cache invalidation."""
        try:
            cursor = self.connection.cursor()
            cursor.execute(
                'SELECT COUNT(*), COALESCE(MAX(updated_at), 0) FROM session_profiles'
            )
            row = cursor.fetchone()
            return (row[0], row[1]) if row else (0, 0)
        except sqlite3.Error as e:
            raise DatabaseError(f'Session profile signature failed: {str(e)}')

    def search_session_profiles_fts(
        self, query: str, limit: int = 20
    ) -> List[Dict[str, Any]]:
        """BM25 search over profile text. Returns ``session_id`` + ``rank`` (lower better)."""
        if not query or not query.strip():
            return []
        fts_query = sanitize_fts_query(query)
        if not fts_query:
            return []
        limit = min(max(1, limit), 100)
        try:
            cursor = self.connection.cursor()
            cursor.execute('''
                SELECT session_id, bm25(session_profiles_fts) AS rank
                FROM session_profiles_fts
                WHERE profile_text MATCH ?
                ORDER BY rank
                LIMIT ?
            ''', (fts_query, limit))
            return [dict(row) for row in cursor.fetchall()]
        except sqlite3.Error as e:
            raise DatabaseError(f'Session profile FTS search failed: {str(e)}')
