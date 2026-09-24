"""Session manager for coordinating session lifecycle and components."""
import logging
import queue
import shutil
import threading
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any, Callable

from .. import paths
from ..storage.database import Database
from ..audio.importer import decode_audio_file, write_chunks, SAMPLE_RATE as IMPORT_SAMPLE_RATE
from ..audio_capture.chunk import AudioChunk
from ..audio_capture.core import DualSourceChunkedRecorder
from ..screenshots.capture import ScreenshotCapture
from ..transcription.processor import TranscriptionProcessor
from ..transcription.live import LiveTranscriber
from ..transcription.parakeet import get_shared_engine
from ..transcription.worker import TranscriptionWorker
from ..summarization import SummaryGenerator
from ..rag.indexer import index_session_content

from .session import Session
from .timeline import Timeline
from ..config import SESSION, TRANSCRIPTION

logger = logging.getLogger(__name__)

# How long closing the app waits for queued live transcriptions. Whatever is
# left is transcribed from its audio file when the session is finalized on
# the next start.
CLOSE_DRAIN_SECONDS = 8.0

JobCallback = Callable[[Any, Optional[BaseException]], None]


class _JobRunner:
    """One daemon thread that runs background jobs in submission order.

    Post-processing (transcribing, indexing, summarizing) runs here instead
    of on the UI thread, one job at a time so two jobs never compete for the
    CPU or hold two models' worth of work at once. The thread is a daemon: a
    job still waiting on the network never keeps the app from exiting, and
    interrupted finalization is picked up again on the next start.
    """

    def __init__(self, db: Database):
        self._db = db
        self._queue: "queue.Queue[Any]" = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._cond = threading.Condition()
        self._busy = 0

    @property
    def busy(self) -> bool:
        with self._cond:
            return self._busy > 0

    def submit(self, label: str, fn: Callable[[], Any], on_done: Optional[JobCallback] = None) -> None:
        with self._cond:
            self._busy += 1
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._run, name='session-jobs', daemon=True)
                self._thread.start()
        self._queue.put((label, fn, on_done))

    def wait(self, timeout: Optional[float] = None) -> bool:
        """Block until no job is running or queued. False on timeout.

        Used by SessionManager.close() so it never disconnects the database
        while a job's ``fn()`` (which owns this thread's DB connection) is
        still running - that raced and crashed the job with 'NoneType has no
        attribute cursor' before this existed.
        """
        with self._cond:
            return self._cond.wait_for(lambda: self._busy == 0, timeout=timeout)

    def _run(self) -> None:
        while True:
            label, fn, on_done = self._queue.get()
            result, error = None, None
            try:
                result = fn()
            except Exception as exc:  # noqa: BLE001 - report, keep the runner alive
                error = exc
                logger.exception(f'Background job failed: {label}')
            finally:
                # Signal "done" before on_done/release_thread_connection: a
                # waiter (close()) only needs fn() - the part that touches
                # the database - to have finished, not the callback after it.
                with self._cond:
                    self._busy -= 1
                    self._cond.notify_all()
            if on_done is not None:
                try:
                    on_done(result, error)
                except Exception:  # noqa: BLE001
                    logger.exception(f'Callback of background job failed: {label}')
            self._db.release_thread_connection()


def _parse_iso(value) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(value) if value else None
    except (TypeError, ValueError):
        return None


class SessionManager:
    """Manages session lifecycle and coordinates all components.

    Handles session creation, component initialization, and coordinates
    the workflow between audio recording, screenshots, and transcription.

    Threading: recorders hand chunks to a single live-transcription worker
    and never wait for speech-to-text; post-processing runs on one background
    job thread. ``status_callback``, ``live_transcription_ui_callback`` and
    ``session_finalized_callback`` are therefore called from those threads
    too - a UI must marshal them onto its own thread.
    """

    def __init__(self,
                 base_path: Optional[str] = None,
                 db_path: Optional[str] = None,
                 status_callback: Optional[callable] = None,
                 live_transcription_ui_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
                 session_finalized_callback: Optional[Callable[[int, Dict[str, Any]], None]] = None):
        """Initialize SessionManager.

        Args:
            base_path: Base directory for session data (default: paths.sessions_dir())
            db_path: Path to SQLite database (default: paths.db_path())
            status_callback: Optional callable for status updates
            live_transcription_ui_callback: Optional callback for live transcription results
            session_finalized_callback: Optional ``(session_id, outcome)`` callback
                fired when a stopped session's post-processing has finished
        """
        self.base_path = Path(base_path) if base_path is not None else paths.sessions_dir()
        self.base_path.mkdir(parents=True, exist_ok=True)
        self.status_callback = status_callback
        self.live_transcription_ui_callback = live_transcription_ui_callback
        self.session_finalized_callback = session_finalized_callback

        # Initialize database
        self.db = Database(db_path if db_path is not None else str(paths.db_path()))
        self.db.connect()
        self._recover_after_restart()

        # Active session
        self.current_session: Optional[Session] = None
        self.current_timeline: Optional[Timeline] = None

        # BU118: which sources the user wants muted. Held here rather than on
        # the session so the choice can be made before a session exists - every
        # recorder this manager creates is handed this state at creation.
        self._muted_sources = {'mic': False, 'system': False}

        # Component factories (can be overridden for testing)
        self.dual_recorder_factory = DualSourceChunkedRecorder
        self.screenshot_capture_factory = ScreenshotCapture
        self.transcription_processor_factory = TranscriptionProcessor

        # VAD settings
        self.vad_threshold = 0.30  # 30% of frames must have speech
        self.vad_aggressiveness = 2  # VAD mode (0-3)

        # Background work: one thread for live transcription, one for jobs.
        self._idle_unload_seconds = TRANSCRIPTION.get('idle_unload_seconds')
        self._jobs = _JobRunner(self.db)
        self._worker = TranscriptionWorker(
            LiveTranscriber(engine=get_shared_engine()),
            on_result=self._on_live_result,
            on_status=self._update_status,
            on_idle=self._unload_idle_models,
        )
        self._worker.start()

    def _recover_after_restart(self) -> None:
        """Close out sessions the last run didn't stop and drop leftovers of
        deleted sessions. Must run before any session starts."""
        try:
            repaired = self.db.repair_interrupted_sessions()
            if repaired:
                logger.info(f'Closed sessions left open by the last run: {repaired}')
            removed = self.db.purge_orphans()
            if any(removed.values()):
                logger.info(f'Removed rows left behind by deleted sessions: {removed}')
        except Exception as e:  # noqa: BLE001 - never block startup on housekeeping
            logger.error(f'Startup database housekeeping failed: {e}')

    def _on_audio_status(self, source: str, message: str, is_error: bool = False):
        """Surface an audio capture-health event (stream lost / restored)."""
        label = 'System audio' if source == 'system' else 'Microphone'
        self._update_status(f'{label}: {message}', is_error=is_error)

    def _update_status(self, message: str, is_error: bool = False):
        """Update status via callback and log."""
        if is_error:
            logger.error(message)
        else:
            logger.info(message)

        if self.status_callback:
            try:
                self.status_callback(message, is_error)
            except Exception as e:  # noqa: BLE001 - a UI hiccup must not break capture
                logger.warning(f'status callback raised: {e}')

    # -- live transcription ------------------------------------------------

    def _live_callback_for(self, session_id: int) -> Callable[[str, AudioChunk], None]:
        """Recorder callback that queues chunks for ``session_id``.

        The session id is bound when the recorder is created, so a chunk
        flushed while the session is stopping is still filed under it.
        """
        def queue_chunk(source: str, chunk: AudioChunk) -> None:
            self._worker.submit(session_id, source, chunk)
        return queue_chunk

    def handle_live_transcription(self, source_or_chunk, chunk=None) -> None:
        """Queue an audio chunk of the current session for live transcription.

        Returns immediately; the transcription worker transcribes it, saves
        the line and passes the result to ``live_transcription_ui_callback``.

        Args:
            source_or_chunk: Either the audio source string ('mic'/'system') or AudioChunk object
            chunk: AudioChunk object (if first arg is source string)
        """
        # Handle both calling conventions:
        # - DualSourceChunkedRecorder passes (source, chunk)
        # - ChunkedAudioRecorder passes just (chunk,)
        if chunk is not None:
            source, audio_chunk = source_or_chunk, chunk
        else:
            audio_chunk = source_or_chunk
            source = audio_chunk.source
        session = self.current_session
        if session is None:
            logger.debug(f'No current session for chunk {audio_chunk.chunk_id}; dropped')
            return
        self._worker.submit(session.id, source, audio_chunk)

    def _on_live_result(self, session_id: int, source: str, chunk: AudioChunk,
                        result: Dict[str, Any]) -> None:
        """Save a live transcription and pass it to the UI (worker thread)."""
        text = (result.get('text') or '').strip()
        if not text:
            return
        timestamp = _parse_iso(chunk.timestamp_start) or datetime.now()
        try:
            self.db.add_transcript(
                session_id=session_id,
                timestamp=timestamp,
                text=text,
                source='microphone' if source == 'mic' else 'system',
                end_timestamp=_parse_iso(chunk.timestamp_end),
                audio_file=TranscriptionProcessor.chunk_key(Path(chunk.file_path)),
            )
        except Exception as e:  # noqa: BLE001
            logger.error(f'Failed to save live transcript: {e}')

        result['source'] = source
        result['session_id'] = session_id
        if self.live_transcription_ui_callback:
            try:
                self.live_transcription_ui_callback(result)
            except Exception as e:  # noqa: BLE001
                logger.warning(f'live transcription UI callback raised: {e}')

    # -- model memory ----------------------------------------------------------

    def _models_in_use(self) -> bool:
        session = self.current_session
        if session is not None and session.status in (Session.STATUS_ACTIVE, Session.STATUS_PAUSED):
            return True
        return self._jobs.busy or self._worker.pending() > 0

    def _unload_idle_models(self) -> None:
        """Free the speech / embedding models once nothing has needed them for
        ``TRANSCRIPTION['idle_unload_seconds']`` (runs on the worker thread).
        They reload on demand: a session start preloads the speech model."""
        idle = self._idle_unload_seconds
        if not idle or self._models_in_use():
            return
        engine = get_shared_engine()
        if engine.is_loaded() and engine.idle_seconds() >= idle:
            engine.unload()
            logger.info('Speech model unloaded after %ss idle', idle)
        try:
            from ..rag.embeddings import get_embedding_service
            embedder = get_embedding_service()
            if getattr(embedder, 'is_loaded', False) and embedder.idle_seconds() >= idle:
                embedder.unload()
                logger.info('Embedding model unloaded after %ss idle', idle)
        except Exception as e:  # noqa: BLE001
            logger.debug(f'Embedding idle check skipped: {e}')

    # -- sessions ------------------------------------------------------------------

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
        live_callback = self._live_callback_for(session_id) if enable_live_transcription else None
        session.dual_recorder = self.dual_recorder_factory(
            str(session_path),
            vad_aggressiveness=self.vad_aggressiveness,
            vad_threshold=self.vad_threshold,
            live_transcription_callback=live_callback,
            on_status=self._on_audio_status
        )
        self._apply_muted_sources(session)
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

    def import_audio_file(self, file_path: str) -> Session:
        """Create a stopped session from an uploaded recording (BU104).

        Decodes the file (WAV, iPhone .m4a, ...) into 16 kHz mono chunks in
        the new session's ``audio/mic`` folder, where the batch transcription
        pipeline picks them up. The session is named after the file and spans
        the recording's own time: its embedded creation time when present,
        else the file's modified time minus its duration. Nothing is created
        if the file can't be decoded.

        Args:
            file_path: Path to the audio file

        Returns:
            The new Session, ready for ``process_transcriptions``
        """
        path = Path(file_path)
        samples, recorded_at = decode_audio_file(path)
        if not len(samples):
            raise ValueError(f'{path.name} contains no audio')

        duration = timedelta(seconds=len(samples) / IMPORT_SAMPLE_RATE)
        start = recorded_at or datetime.fromtimestamp(path.stat().st_mtime) - duration
        end = start + duration
        name = path.stem.strip() or 'Uploaded audio'

        session_id = self.db.create_session(name, start, status=Session.STATUS_STOPPED)
        session_path = self._get_session_path(session_id)
        try:
            self.db.update_session(session_id, end_time=int(end.timestamp()))
            session = Session(session_id, name, str(session_path), db=self.db)
            session.start_time, session.end_time = start, end
            chunk_count = write_chunks(samples, session.session_path / 'audio' / 'mic', start)
        except Exception:
            self.db.delete_session(session_id)
            shutil.rmtree(session_path, ignore_errors=True)
            raise
        session.transcription_processor = self.transcription_processor_factory(
            str(session_path),
            db=self.db
        )

        self._update_status(f'Imported {path.name} as session {session_id} ({chunk_count} chunks)')
        return session

    def load_session(self, session_id: int, with_capture: bool = True) -> Session:
        """Load an existing session from database.

        Args:
            session_id: Database session ID
            with_capture: Also build its recorder and screenshot capture (only
                needed to record into it again)

        Returns:
            Loaded Session instance
        """
        # Get session from database
        db_session = self.db.get_session(session_id)
        if db_session is None:
            raise ValueError(f'Session {session_id} not found')

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
        if with_capture:
            session.dual_recorder = self.dual_recorder_factory(
                str(session_path),
                vad_aggressiveness=self.vad_aggressiveness,
                vad_threshold=self.vad_threshold,
                live_transcription_callback=self._live_callback_for(session_id),
                on_status=self._on_audio_status
            )
            self._apply_muted_sources(session)
            session.screenshot_capture = self.screenshot_capture_factory(
                str(session_path),
                db=self.db
            )
            # Create timeline with session start time
            self.current_timeline = Timeline(session_id, session.start_time)
        session.transcription_processor = self.transcription_processor_factory(
            str(session_path),
            db=self.db
        )

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

        # Load the speech model while the first chunk is being recorded.
        if enable_live_transcription:
            self._worker.preload()

        # Auto-start recording if enabled
        if auto_record:
            self.start_recording(label='main')

        self._update_status(f'Started session {session.id}: {session.name}')
        return session

    def set_source_muted(self, source: str, muted: bool) -> bool:
        """Mute or unmute 'mic' or 'system' (BU118).

        Valid before a session exists: the choice is remembered and applied to
        the recorder as soon as one is created, so the user can silence a
        source and then hit record. Returns False only for an unknown source.
        """
        if source not in self._muted_sources:
            self._update_status(f'Unknown audio source: {source}', is_error=True)
            return False

        self._muted_sources[source] = bool(muted)
        if self.current_session:
            self.current_session.set_source_muted(source, muted)
        return True

    def is_source_muted(self, source: str) -> bool:
        """True if that source is muted, session or no session."""
        return self._muted_sources.get(source, False)

    def _apply_muted_sources(self, session: Session) -> None:
        """Hand a freshly built recorder the mute state the user already chose."""
        for source, muted in self._muted_sources.items():
            if muted:
                session.set_source_muted(source, muted)

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
        self._worker.preload()
        self.start_recording(label='main')

        self._update_status(f'Session {self.current_session.id} resumed')
        return True

    def resume_stopped_session(self, session_id: int) -> Optional[Session]:
        """Resume a previously stopped (or paused) session by its database id.

        Loads the session back in as the current session, preserving its
        original start_time, name and session_id, then continues recording
        into the same session folder. No new session row is created and
        existing transcripts/screenshots/summaries stay associated with the
        same session_id.

        Args:
            session_id: Database session ID to resume

        Returns:
            The resumed Session instance, or None if it could not be resumed
        """
        if self.current_session and self.current_session.status in (
            Session.STATUS_ACTIVE, Session.STATUS_PAUSED
        ):
            self._update_status(
                'Stop or pause the current session before resuming another', is_error=True
            )
            return None

        db_session = self.db.get_session(session_id)
        if not db_session:
            self._update_status(f'Session {session_id} not found', is_error=True)
            return None

        if db_session['status'] not in (Session.STATUS_STOPPED, Session.STATUS_PAUSED,
                                        Session.STATUS_COMPLETED, Session.STATUS_PROCESSING):
            self._update_status(
                f"Session {session_id} can't be resumed (status: {db_session['status']})", is_error=True
            )
            return None

        session = self.load_session(session_id)
        session.status = Session.STATUS_ACTIVE
        self.db.update_session(session_id, status=Session.STATUS_ACTIVE)
        self.current_session = session

        self._worker.preload()
        self.start_recording(label='main')

        self._update_status(f'Resumed session {session_id}: {session.name}')
        return session

    def stop_session(self, auto_transcribe: bool = True, background: bool = False,
                     finalize: bool = True) -> Optional[Session]:
        """Stop the current session.

        Stopping capture is quick. Everything after it - waiting for queued
        live transcriptions, transcribing chunks that have no transcript yet,
        RAG indexing, the automatic summary - is *finalization*
        (:meth:`finalize_session`). Until it completes the session stays
        flagged ``needs_finalize``, so an interrupted run is finished on the
        next start.

        Args:
            auto_transcribe: During finalization, transcribe audio chunks that
                have no transcript yet. Never re-transcribes a chunk, so it is
                cheap when live transcription kept up.
            background: Finalize on the background job thread and return
                immediately (``session_finalized_callback`` reports the end).
                Use this from a UI.
            finalize: False skips finalization for now (app shutdown).

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
        self.db.update_session(self.current_session.id, needs_finalize=1)

        # Update timeline
        if self.current_timeline:
            self.current_timeline.set_session_end(datetime.now())

        session = self.current_session
        self.current_session = None

        if finalize:
            if background:
                self.finalize_in_background(session.id, auto_transcribe=auto_transcribe)
            else:
                self.finalize_session(session.id, auto_transcribe=auto_transcribe)

        self._update_status(f'Stopped session {session.id}: {session.name}')
        return session

    def finalize_session(self, session_id: int, auto_transcribe: bool = True) -> Dict[str, Any]:
        """Post-process a stopped session. Blocking: run it off the UI thread.

        Returns an outcome dict: ``transcribed`` (chunks transcribed now),
        ``indexed``, ``summarized`` and ``error``.
        """
        outcome: Dict[str, Any] = {
            'session_id': session_id, 'transcribed': 0,
            'indexed': False, 'summarized': False, 'error': None,
        }
        try:
            db_session = self.db.get_session(session_id)
            if not db_session:
                return outcome  # deleted meanwhile
            name = db_session['name']

            # Chunks recorded just before Stop may still be queued.
            if self._worker.pending(session_id):
                self._update_status(f"Finishing live transcription of '{name}'…")
                self._worker.wait_idle(session_id)
            self._worker.forget_session(session_id)

            if auto_transcribe:
                outcome['transcribed'] = self._transcribe_missing(session_id)

            if not self.db.get_session(session_id):
                return outcome
            has_transcripts = self.db.has_transcripts(session_id)
            if has_transcripts:
                self.db.update_session(session_id, transcription_status='transcribed')

            self._update_status(f"Indexing '{name}' for search…")
            outcome['indexed'] = index_session_content(self.db, session_id)

            if (SESSION.get('auto_summary_after_stop', False) and has_transcripts
                    and not self.db.get_summaries(session_id)):
                self._update_status(f"Summarizing '{name}'…")
                try:
                    self._summarize(session_id)
                    outcome['summarized'] = True
                except Exception as e:  # noqa: BLE001 - summary is optional
                    outcome['error'] = f'Auto summary failed: {e}'
                    self._update_status(f'Auto summary failed: {e}', is_error=True)

            if self.db.get_session(session_id):
                self.db.update_session(session_id, needs_finalize=0)
        except Exception as e:  # noqa: BLE001
            outcome['error'] = str(e)
            logger.exception(f'Finalizing session {session_id} failed')
            self._update_status(f'Finishing session {session_id} failed: {e}', is_error=True)
        return outcome

    def finalize_in_background(self, session_id: int, auto_transcribe: bool = True) -> None:
        """Queue :meth:`finalize_session`; ``session_finalized_callback``
        receives ``(session_id, outcome)`` when it completes."""
        def done(outcome, error):
            if outcome is None:
                outcome = {'session_id': session_id, 'error': str(error)}
            if self.session_finalized_callback:
                self.session_finalized_callback(session_id, outcome)

        self._jobs.submit(
            f'finalize session {session_id}',
            lambda: self.finalize_session(session_id, auto_transcribe=auto_transcribe),
            done,
        )

    def finalize_pending_sessions(self) -> List[int]:
        """Queue finalization of sessions a previous run left unfinished."""
        pending = self.db.list_sessions_needing_finalize()
        for session_id in pending:
            self.finalize_in_background(session_id)
        return pending

    def submit_job(self, label: str, fn: Callable[[], Any],
                   on_done: Optional[JobCallback] = None) -> None:
        """Run ``fn`` on the background job thread (after queued jobs);
        ``on_done(result, error)`` is called on that thread."""
        self._jobs.submit(label, fn, on_done)

    def _transcribe_missing(self, session_id: int) -> int:
        """Batch-transcribe the session's chunks that have no transcript yet."""
        processor = self.transcription_processor_factory(
            str(self._get_session_path(session_id)), db=self.db
        )
        results = processor.process_all(session_id)
        return len(results.get('microphone', [])) + len(results.get('system', []))

    def transcribe_session(self, session_id: int) -> Dict[str, Any]:
        """Transcribe a stored session's untranscribed audio and index it.
        Blocking: run it with :meth:`submit_job`."""
        db_session = self.db.get_session(session_id)
        if not db_session:
            raise ValueError(f'Session {session_id} not found')
        self._update_status(f"Transcribing '{db_session['name']}'…")
        count = self._transcribe_missing(session_id)
        if self.db.has_transcripts(session_id):
            self.db.update_session(session_id, transcription_status='transcribed')
            index_session_content(self.db, session_id)
        return {'session_id': session_id, 'transcribed': count,
                'has_transcripts': self.db.has_transcripts(session_id)}

    def summarize_session(self, session_id: int) -> Dict[str, Any]:
        """Generate and store a summary, then re-index. Blocking: run it with
        :meth:`submit_job`. Raises when the session has no transcript text."""
        db_session = self.db.get_session(session_id)
        if not db_session:
            raise ValueError(f'Session {session_id} not found')
        self._update_status(f"Summarizing '{db_session['name']}'…")
        return self._summarize(session_id)

    def _summarize(self, session_id: int) -> Dict[str, Any]:
        transcripts = self.db.get_transcripts(session_id)
        full_transcript = ' '.join(t.get('text', '') for t in transcripts if t.get('text'))
        if not full_transcript.strip():
            raise ValueError('No transcript text to summarize')
        summary = SummaryGenerator(db=self.db).generate_and_store(
            transcript=full_transcript,
            session_id=session_id,
            summary_type='full'
        )
        self.db.update_session(session_id, summary_status='summarized')
        try:
            index_session_content(self.db, session_id)
        except Exception as e:  # noqa: BLE001
            self._update_status(f'RAG indexing failed for session {session_id}: {str(e)}', is_error=True)
        return summary

    def _auto_generate_summary(self, session: Session) -> None:
        """Automatically generate a summary for a session if conditions are met.

        Args:
            session: The session to generate a summary for
        """
        session_id = session.id
        if self.db.get_summaries(session_id):
            self._update_status(f'Summary already exists for session {session_id}, skipping auto-summary')
            return
        if not self.db.has_transcripts(session_id):
            self._update_status(f'No transcripts found for session {session_id}, cannot auto-summarize')
            return
        self._update_status(f'Auto-generating summary for session {session_id}...')
        self._summarize(session_id)
        self._update_status(f'Auto-summary completed for session {session_id}')

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

    def import_clipboard_screenshot(self, image) -> str:
        """Save an image taken from the clipboard (e.g. a Win+Shift+S snip)
        as a screenshot of the current session (BU110).

        Returns:
            Path to saved screenshot
        """
        if not self.current_session:
            raise RuntimeError('No active session')

        output_path = self.current_session.screenshot_capture.import_image(
            image,
            session_name=self.current_session.name,
            session_id=self.current_session.id
        )

        if self.current_timeline:
            self.current_timeline.add_screenshot(output_path, 'clipboard')

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
        """Process transcriptions for a session (blocking).

        Transcribes only chunks that have no transcript yet, then updates the
        transcription status and RAG index.

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
        if self.db.has_transcripts(session.id):
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
            session = self.load_session(session_id, with_capture=False)
            return session.get_summary()
        except Exception as e:
            self._update_status(f'Failed to get session summary: {str(e)}', is_error=True)
            return None

    def close(self) -> None:
        """Clean up resources.

        A session still recording or paused is stopped without finalizing
        (it stays flagged and is finalized on the next start), so closing
        never waits for indexing or the summary request.
        """
        if self.current_session and self.current_session.status in (
            Session.STATUS_ACTIVE, Session.STATUS_PAUSED
        ):
            try:
                self.stop_session(auto_transcribe=False, finalize=False)
            except Exception as e:  # noqa: BLE001
                logger.error(f'Failed to stop session on close: {e}')

        # Let the last queued chunks finish; leftovers are handled next start.
        self._worker.stop(drain_timeout=CLOSE_DRAIN_SECONDS)

        # A background job (Stop's background finalize, or a Transcribe /
        # Summarize / Upload Audio submitted from the UI) may still be
        # mid-query on its own DB connection; disconnecting under it raised
        # "'NoneType' object has no attribute 'cursor'" in that thread. Give
        # it the same bounded grace period, and only disconnect once it's
        # actually done - a job still running past that keeps the database
        # open, which is safe (its daemon thread and connection are reclaimed
        # by the OS at process exit) and rare (the drain above already lets
        # queued transcription finish first).
        if not self._jobs.wait(timeout=CLOSE_DRAIN_SECONDS):
            logger.warning(
                'A background job is still running at close() - leaving the database connected'
            )
        elif self.db:
            self.db.disconnect()

        self._update_status('SessionManager closed')

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
