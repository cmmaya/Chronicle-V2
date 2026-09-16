"""Core audio capture functionality with chunked recording."""

import threading
import time
from collections import deque
from fractions import Fraction
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Optional, Callable, Tuple
import logging
import numpy as np
import wave
import webrtcvad

from src.audio.recorder import AudioRecorder
from src.audio_capture.chunk import (
    AudioChunk,
    generate_chunk_id,
    generate_filename
)

# Import SystemAudioRecorder for system audio capture
try:
    from src.audio_capture.system_recorder import SystemAudioRecorder
except ImportError:
    SystemAudioRecorder = None

try:
    from src.config import AUDIO_CAPTURE as _AUDIO_CAPTURE
except Exception:  # pragma: no cover - config should always import
    _AUDIO_CAPTURE = {}


logger = logging.getLogger(__name__)


class _StreamResampler:
    """Stateful mono float32 resampler (FFmpeg's swresample through PyAV).

    Fed the capture stream block by block, so no filter edge lands on a chunk
    boundary. One instance per recording run; ``flush()`` ends it.
    """

    _av = None

    @classmethod
    def available(cls) -> bool:
        if cls._av is None:
            try:
                import av
                cls._av = av
            except Exception:  # noqa: BLE001 - PyAV is optional
                cls._av = False
        return bool(cls._av)

    def __init__(self, in_rate: int, out_rate: int):
        if not self.available():
            raise RuntimeError("PyAV is not installed")
        self._in_rate = in_rate
        self._resampler = self._av.AudioResampler(format='flt', layout='mono', rate=out_rate)
        self._pts = 0

    def process(self, samples: np.ndarray) -> np.ndarray:
        if not len(samples):
            return np.zeros(0, dtype=np.float32)
        frame = self._av.AudioFrame.from_ndarray(
            np.ascontiguousarray(samples, dtype=np.float32).reshape(1, -1),
            format='flt', layout='mono',
        )
        frame.sample_rate = self._in_rate
        frame.pts = self._pts
        frame.time_base = Fraction(1, self._in_rate)
        self._pts += len(samples)
        return self._collect(self._resampler.resample(frame))

    def flush(self) -> np.ndarray:
        return self._collect(self._resampler.resample(None))

    @staticmethod
    def _collect(frames) -> np.ndarray:
        parts = [f.to_ndarray().reshape(-1) for f in frames]
        if not parts:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(parts).astype(np.float32, copy=False)


def _storage_rate(capture_rate: int) -> int:
    """Rate chunks are stored at: AUDIO_CAPTURE['storage_sample_rate'] when
    it is lower than the device rate and PyAV can resample, else the device rate."""
    target = _AUDIO_CAPTURE.get("storage_sample_rate", 16000)
    if not target or target >= capture_rate:
        return capture_rate
    if not _StreamResampler.available():
        logger.warning("PyAV not installed - storing audio at the device rate (%s Hz)", capture_rate)
        return capture_rate
    return int(target)


class ChunkedAudioRecorder:
    """Audio recorder that saves audio in fixed-duration chunks.
    
    This recorder captures audio continuously but saves it in discrete
    chunks of a specified duration (default 5 seconds), along with
    metadata for each chunk. Supports overlapping chunks to prevent
    word loss at chunk boundaries.

    Attributes:
        CHUNK_DURATION: Default duration of each chunk in seconds.
        OVERLAP_DURATION: Default overlap between consecutive chunks.
    """

    CHUNK_DURATION = 5  # seconds - balances live-bubble update speed against
    # transcription quality (2s chunks gave the model too little audio per call)
    OVERLAP_DURATION = 1.0  # seconds - a boundary word needs to sit fully
    # inside the overlap window (with real audio context on both sides) for
    # Parakeet to transcribe it cleanly; too little overlap here is what let
    # words get hallucinated/invented or dropped right at chunk edges. Paired
    # with LiveTranscriber._deduplicate's fuzzy matching (src/transcription/
    # live.py) so the resulting duplicated text still gets stripped.
    VAD_SAMPLE_RATE = 16000  # Required by WebRTC VAD
    VAD_FRAME_DURATION = 30  # ms (10, 20, or 30 supported)

    def __init__(
        self,
        session_path: str = '/tmp/sessions/session_001',
        source: str = 'mic',
        chunk_duration: int = CHUNK_DURATION,
        overlap_duration: float = OVERLAP_DURATION,
        callback: Optional[Callable[[AudioChunk], None]] = None,
        vad_aggressiveness: int = 2,  # 0-3, higher = more aggressive filtering
        vad_threshold: float = 0.30,  # Minimum speech ratio to save chunk
        live_transcription_callback: Optional[Callable[[AudioChunk], None]] = None
    ):
        """Initialize the chunked audio recorder.

        Args:
            session_path: Base path for the session.
            source: Audio source ('mic' or 'system').
            chunk_duration: Duration of each chunk in seconds.
            overlap_duration: Overlap duration between chunks in seconds.
            callback: Optional callback called after each chunk is saved.
            vad_aggressiveness: VAD aggressiveness mode (0=3, 3=most aggressive).
            vad_threshold: Minimum ratio of frames with speech to save chunk (0.0-1.0).
            live_transcription_callback: Optional callback triggered for live transcription
                                         of each new audio chunk.
        """
        self.session_path = Path(session_path)
        self.source = source
        self.chunk_duration = chunk_duration
        self.overlap_duration = overlap_duration
        self.callback = callback
        self.vad_aggressiveness = vad_aggressiveness
        self.vad_threshold = vad_threshold
        self.live_transcription_callback = live_transcription_callback
        
        # Initialize VAD
        self._vad = webrtcvad.Vad(vad_aggressiveness)

        # Select the appropriate recorder based on source
        if source == 'system':
            # Use SystemAudioRecorder for system audio (loopback)
            if SystemAudioRecorder is None:
                raise ImportError(
                    "SystemAudioRecorder not available. "
                    "Ensure soundcard is installed."
                )
            self._system_recorder = SystemAudioRecorder(
                session_path=str(session_path),
                source=source,
                channels=1  # Force mono recording
            )
            # Expose needed attributes from system recorder
            self._recorder = self._system_recorder
            self.capture_rate = self._system_recorder.sample_rate
            self._capture_channels = 1  # Force mono recording
        else:
            # Use AudioRecorder for microphone
            self._recorder = AudioRecorder(
                session_path=str(session_path),
                source=source
            )
            self.capture_rate = self._recorder.sample_rate
            self._capture_channels = self._recorder.channels
            self._system_recorder = None

        # Chunks are mono, at the storage rate (16 kHz when PyAV can resample:
        # what Parakeet uses, and a third of the disk space of 48 kHz).
        self.sample_rate = _storage_rate(self.capture_rate)
        self.channels = 1

        # Track recorded chunks
        self.chunks: List[AudioChunk] = []
        self._is_running = False
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

        # Health tracking (read by the DualSourceChunkedRecorder watchdog)
        self._started_at: float = 0.0
        self._last_chunk_time: float = 0.0
        self._restart_count: int = 0

        # Per-run stream state (see _reset_stream_state)
        self._raw: deque = deque()
        self._overflows = 0
        self._resampler: Optional[_StreamResampler] = None
        self._pending: List[np.ndarray] = []
        self._pending_len = 0
        self._pending_start: Optional[datetime] = None
        self._first_window = True

        # Overlap configuration
        self._overlap_samples = int(self.overlap_duration * self.sample_rate)
        self._samples_per_chunk = int(self.chunk_duration * self.sample_rate)
        self._samples_per_save = self._samples_per_chunk - self._overlap_samples

        # Setup audio path
        self.audio_path = self.session_path / 'audio' / source
        self.audio_path.mkdir(parents=True, exist_ok=True)
    
    def _check_vad(self, audio_data: np.ndarray) -> bool:
        """Check if audio contains speech using VAD.
        
        Args:
            audio_data: Audio data as float32 numpy array.
            
        Returns:
            True if speech is detected, False otherwise.
        """
        # Convert to mono if stereo
        if len(audio_data.shape) > 1 and audio_data.shape[1] > 1:
            audio_mono = np.mean(audio_data, axis=1)
        else:
            audio_mono = audio_data.flatten()
        
        # Convert float32 to int16
        int16_data = (audio_mono * 32767).astype(np.int16)
        
        # Resample to 16kHz if needed (VAD requires 16kHz)
        if self.sample_rate != self.VAD_SAMPLE_RATE:
            # Calculate resampling ratio
            num_samples = int(len(int16_data) * self.VAD_SAMPLE_RATE / self.sample_rate)
            indices = np.linspace(0, len(int16_data) - 1, num_samples)
            int16_data = np.interp(indices, np.arange(len(int16_data)), int16_data).astype(np.int16)
        
        # Split into frames and check each frame
        frame_size = int(self.VAD_SAMPLE_RATE * self.VAD_FRAME_DURATION / 1000)
        num_frames = len(int16_data) // frame_size
        
        if num_frames == 0:
            logger.warning("VAD: no frames to check")
            return False
        
        # Check if any frame contains speech
        speech_frames = 0
        for i in range(num_frames):
            start = i * frame_size
            end = start + frame_size
            frame_bytes = int16_data[start:end].tobytes()
            try:
                if self._vad.is_speech(frame_bytes, self.VAD_SAMPLE_RATE):
                    speech_frames += 1
            except Exception as e:
                logger.warning(f"VAD error on frame {i}: {e}")
        
        # Consider speech only if at least threshold% of frames have speech
        # This filters out short bursts of noise
        speech_ratio = speech_frames / num_frames if num_frames > 0 else 0
        has_speech = speech_ratio >= self.vad_threshold
        
        logger.debug(f"VAD check: {speech_frames}/{num_frames} frames ({speech_ratio:.1%}) - {'speech detected' if has_speech else 'silent'}")
        
        return has_speech
    
    def _save_chunk_from_array(self, audio_data: np.ndarray, start_time: datetime,
                               end_time: Optional[datetime] = None) -> Optional[AudioChunk]:
        """Save numpy audio array as a chunk file with metadata.

        Args:
            audio_data: Mono float32 audio at ``self.sample_rate``.
            start_time: Start time of the chunk.
            end_time: End time of the chunk (defaults to now).

        Returns:
            AudioChunk with metadata, or None if no speech detected.
        """
        if len(audio_data) == 0:
            logger.warning("Empty audio data for chunk")
            return None

        # Run VAD check - skip if no speech detected
        if not self._check_vad(audio_data):
            logger.debug("Skipping silent chunk")
            return None

        timestamp_start = start_time
        timestamp_end = end_time or datetime.now()

        chunk_id = generate_chunk_id(self.source, timestamp_start)
        filename = generate_filename(timestamp_start, timestamp_end)
        file_path = self.audio_path / filename

        # Convert float32 to int16
        int16_data = (np.clip(audio_data, -1.0, 1.0) * 32767).astype(np.int16)
        bytes_data = int16_data.tobytes()

        # Save audio file
        with wave.open(str(file_path), 'wb') as wf:
            wf.setnchannels(self.channels)
            wf.setsampwidth(2)
            wf.setframerate(self.sample_rate)
            wf.writeframes(bytes_data)

        chunk = AudioChunk(
            source=self.source,
            chunk_id=chunk_id,
            timestamp_start=timestamp_start.isoformat(),
            timestamp_end=timestamp_end.isoformat(),
            file_path=str(file_path)
        )

        chunk.save_metadata()
        self._last_chunk_time = time.time()

        logger.info(f"Saved chunk: {chunk_id} ({chunk.duration:.2f}s)")

        # Hand the chunk to live transcription. The callback only queues it
        # (see SessionManager.handle_live_transcription) and returns at once.
        if self.live_transcription_callback:
            try:
                self.live_transcription_callback(chunk)
            except Exception as e:
                logger.error(f"Error in live_transcription_callback: {e}")

        return chunk

    # -- stream -> chunks ------------------------------------------------------
    #
    # Capture delivers audio in small blocks. _feed() downmixes and resamples
    # them into self._pending, and _emit_ready_chunks() cuts CHUNK_DURATION
    # windows from its front, advancing by (chunk - overlap) each time, so
    # consecutive chunks share OVERLAP_DURATION seconds and no audio is ever
    # skipped - however late a block arrives. Timestamps come from the sample
    # count, not from when a block happened to be processed.

    def _reset_stream_state(self) -> None:
        """Fresh state for one recording run (start or restart)."""
        self._raw = deque()
        self._overflows = 0
        self._pending = []
        self._pending_len = 0
        self._pending_start = None
        self._first_window = True
        self._resampler = None
        if self.sample_rate != self.capture_rate:
            try:
                self._resampler = _StreamResampler(self.capture_rate, self.sample_rate)
            except Exception as e:
                # Can't resample after all: store at the device rate.
                logger.error(f"Resampler unavailable ({e}); storing {self.source} audio at {self.capture_rate} Hz")
                self._set_sample_rate(self.capture_rate)

    def _set_sample_rate(self, rate: int) -> None:
        self.sample_rate = rate
        self._overlap_samples = int(self.overlap_duration * rate)
        self._samples_per_chunk = int(self.chunk_duration * rate)
        self._samples_per_save = self._samples_per_chunk - self._overlap_samples

    @staticmethod
    def _to_mono(block: np.ndarray) -> np.ndarray:
        block = np.asarray(block, dtype=np.float32)
        if block.ndim > 1:
            block = block[:, 0] if block.shape[1] == 1 else block.mean(axis=1)
        return block

    def _feed(self, blocks: List[np.ndarray]) -> None:
        """Append captured blocks to the pending stream and emit full chunks."""
        if not blocks:
            return
        audio = np.concatenate([self._to_mono(b) for b in blocks])
        if self._resampler is not None:
            audio = self._resampler.process(audio)
        if not len(audio):
            return
        if self._pending_start is None:
            # Wall time of the first pending sample.
            self._pending_start = datetime.now() - timedelta(seconds=len(audio) / self.sample_rate)
        self._pending.append(audio)
        self._pending_len += len(audio)
        self._emit_ready_chunks()

    def _pending_audio(self) -> np.ndarray:
        if len(self._pending) != 1:
            self._pending = [np.concatenate(self._pending)] if self._pending else []
        return self._pending[0] if self._pending else np.zeros(0, dtype=np.float32)

    def _emit_ready_chunks(self) -> None:
        while self._pending_len >= self._samples_per_chunk:
            audio = self._pending_audio()
            self._save_window(audio[:self._samples_per_chunk], self._pending_start)
            rest = audio[self._samples_per_save:]
            self._pending = [rest]
            self._pending_len = len(rest)
            self._pending_start += timedelta(seconds=self._samples_per_save / self.sample_rate)
            self._first_window = False

    def _save_window(self, audio: np.ndarray, window_start: datetime) -> None:
        """Save one window of the stream as a chunk.

        The chunk's start timestamp is where its *new* audio begins - after
        the overlap it shares with the previous window - which is what its
        (deduplicated) transcript covers.
        """
        lead = 0.0 if self._first_window else self.overlap_duration
        chunk_start = window_start + timedelta(seconds=lead)
        chunk_end = window_start + timedelta(seconds=len(audio) / self.sample_rate)
        try:
            chunk = self._save_chunk_from_array(audio, chunk_start, chunk_end)
        except Exception as e:
            logger.error(f"Failed to save {self.source} chunk: {e}")
            return
        if chunk is not None:
            self.chunks.append(chunk)
            if self.callback:
                self.callback(chunk)

    def _flush_final(self) -> None:
        """Save what is left of the stream when a run ends."""
        if self._resampler is not None:
            try:
                tail = self._resampler.flush()
            except Exception:
                tail = np.zeros(0, dtype=np.float32)
            if len(tail) and self._pending_start is not None:
                self._pending.append(tail)
                self._pending_len += len(tail)
        if self._pending_start is None or not self._pending_len:
            return
        new_samples = self._pending_len - (0 if self._first_window else self._overlap_samples)
        if new_samples >= int(0.5 * self.sample_rate):
            self._save_window(self._pending_audio(), self._pending_start)
        self._pending = []
        self._pending_len = 0

    def _drain_raw(self) -> None:
        blocks = []
        while self._raw:
            blocks.append(self._raw.popleft())
        try:
            self._feed(blocks)
        except Exception as e:
            logger.error(f"Error processing {self.source} audio: {e}")

    def _recording_loop(self) -> None:
        """Microphone recording loop."""
        import sounddevice as sd

        self._reset_stream_state()
        raw = self._raw

        def callback(indata, frames, time_info, status):
            # PortAudio's own thread: copy the block and return. Anything slow
            # here (VAD, file writes, transcription) makes the device drop audio.
            if status:
                self._overflows += 1
            raw.append(indata.copy())

        # Create stream using instance attributes
        self._stream = sd.InputStream(
            samplerate=self.capture_rate,
            channels=self._capture_channels,
            device=self._recorder.device_index,
            dtype='float32',
            callback=callback
        )
        self._stream.start()
        try:
            # Run until stopped
            while not self._stop_event.wait(0.1):
                self._drain_raw()
        finally:
            self._stream.stop()
            self._stream.close()
            self._drain_raw()
            self._flush_final()
            if self._overflows:
                logger.warning(f"Microphone input overflowed {self._overflows} times this run")

    def _system_recording_loop(self) -> None:
        """Recording loop for system audio (loopback)."""
        if self._system_recorder is None:
            logger.error("System recorder not initialized")
            return

        self._reset_stream_state()
        self._system_recorder.start()
        try:
            while not self._stop_event.wait(0.25):
                if not self._is_running:
                    break
                try:
                    self._feed(self._system_recorder.drain_buffer())
                except Exception as e:
                    logger.error(f"Error processing system audio: {e}")
        finally:
            self._system_recorder.stop()
            try:
                self._feed(self._system_recorder.drain_buffer())
            except Exception as e:
                logger.error(f"Error processing system audio: {e}")
            self._flush_final()

    def start(self) -> None:
        """Start chunked recording."""
        if self._is_running:
            logger.warning("Recording already in progress")
            return

        self.chunks = []
        self._stop_event.clear()
        self._is_running = True
        now = time.time()
        self._started_at = now
        self._last_chunk_time = now

        # Select the appropriate recording loop based on source
        if self.source == 'system' and self._system_recorder is not None:
            target = self._system_recording_loop
        else:
            target = self._recording_loop

        # Start recording in background thread
        self._thread = threading.Thread(target=target, daemon=True)
        self._thread.start()

        logger.info(f"Started chunked recording ({self.chunk_duration}s chunks)")

    def stop(self) -> List[AudioChunk]:
        """Stop recording and return all recorded chunks.

        Returns:
            List of AudioChunk objects.
        """
        if not self._is_running:
            return self.chunks

        self._is_running = False
        self._stop_event.set()

        if self._thread:
            self._thread.join(timeout=self.chunk_duration + 1)
            self._thread = None

        logger.info(f"Stopped chunked recording. Total chunks: {len(self.chunks)}")

        return self.chunks

    @property
    def is_thread_alive(self) -> bool:
        """True while the recording-loop thread is running."""
        return self._thread is not None and self._thread.is_alive()

    def check_health(self, stall_seconds: Optional[float] = None) -> Tuple[bool, str]:
        """Assess whether capture is still working.

        Returns ``(healthy, reason)``. A recorder that isn't running is reported
        healthy - there is nothing to recover. During a short grace window after
        start/restart the recorder is always reported healthy so a slow device
        open doesn't trip the watchdog.
        """
        if not self._is_running:
            return True, "not running"

        if stall_seconds is None:
            stall_seconds = float(
                _AUDIO_CAPTURE.get("watchdog_system_stall_seconds", 40.0)
            )

        grace = max(stall_seconds, 10.0)
        if time.time() - self._started_at < grace:
            return True, "starting up"

        if not self.is_thread_alive:
            return False, "recording thread is not alive"

        if self.source == 'system' and self._system_recorder is not None:
            if not getattr(self._system_recorder, "is_thread_alive", True):
                return False, "system loopback supervisor is not alive"
            idle = self._system_recorder.seconds_since_last_data()
            if idle > stall_seconds:
                return False, f"no system audio frames for {idle:.0f}s"

        return True, "ok"

    def restart(self) -> bool:
        """Tear down and re-establish capture in place.

        Keeps ``self.chunks`` and all callbacks. For the system source this
        builds a fresh :class:`SystemAudioRecorder`, so the loopback device is
        re-resolved from the *current* default speaker.

        Returns:
            True if a new recording thread was started.
        """
        if not self._is_running:
            return False

        self._restart_count += 1
        logger.warning(
            f"Restarting {self.source} chunked recorder (restart #{self._restart_count})"
        )

        # Signal the current loop to exit and wait for it. Its own teardown
        # stops the underlying stream / system recorder.
        self._stop_event.set()
        old_thread = self._thread
        if old_thread:
            old_thread.join(timeout=self.chunk_duration + 5)
            if old_thread.is_alive():
                # A wedged loop still references self._stop_event; starting a
                # second thread now would double-write chunks. Bail and let the
                # watchdog retry after its cooldown.
                logger.error(
                    f"{self.source} recording thread will not stop; "
                    "deferring restart"
                )
                return False

        # Best-effort stop of the old underlying recorder in case the loop
        # timed out before its finally-block ran.
        if self._system_recorder is not None:
            try:
                self._system_recorder.stop()
            except Exception as e:
                logger.warning(f"Error stopping stale system recorder: {e}")

            # Fresh instance -> re-resolves default_speaker() on start.
            try:
                self._system_recorder = SystemAudioRecorder(
                    session_path=str(self.session_path),
                    source=self.source,
                    channels=1,
                )
                self._recorder = self._system_recorder
            except Exception as e:
                logger.error(f"Failed to rebuild SystemAudioRecorder: {e}")
                self._is_running = False
                return False

        # Restart the loop.
        self._stop_event = threading.Event()
        self._is_running = True
        now = time.time()
        self._started_at = now
        self._last_chunk_time = now

        if self.source == 'system' and self._system_recorder is not None:
            target = self._system_recording_loop
        else:
            target = self._recording_loop

        self._thread = threading.Thread(target=target, daemon=True)
        self._thread.start()
        logger.info(f"{self.source} chunked recorder restarted")
        return True


    def get_chunks(self) -> List[AudioChunk]:
        """Get list of recorded chunks.
        
        Returns:
            List of AudioChunk objects.
        """
        return self.chunks.copy()


class DualSourceChunkedRecorder:
    """Manages chunked recording for both microphone and system audio.
    
    This class coordinates chunked recording from two audio sources
    (microphone and system audio) simultaneously.
    """
    
    def __init__(
        self,
        session_path: str = '/tmp/sessions/session_001',
        chunk_duration: int = ChunkedAudioRecorder.CHUNK_DURATION,
        overlap_duration: float = ChunkedAudioRecorder.OVERLAP_DURATION,
        callback: Optional[Callable[[str, AudioChunk], None]] = None,
        vad_aggressiveness: int = 2,
        vad_threshold: float = 0.30,
        live_transcription_callback: Optional[Callable[[str, AudioChunk], None]] = None,
        on_status: Optional[Callable[[str, str, bool], None]] = None
    ):
        """Initialize the dual source recorder.

        Args:
            session_path: Base path for the session.
            chunk_duration: Duration of each chunk in seconds.
            overlap_duration: Overlap duration between chunks in seconds.
            callback: Optional callback called after each chunk is saved.
                      Receives (source, chunk) as arguments.
            vad_aggressiveness: VAD aggressiveness mode (0-3).
            vad_threshold: Minimum ratio of speech frames to save chunk (0.0-1.0).
            live_transcription_callback: Optional callback for live transcription.
                                        Receives (source, chunk) as arguments.
            on_status: Optional callback for capture-health events. Receives
                       (source, message, is_error) - used to surface a lost /
                       recovered audio stream to the UI.
        """
        self.session_path = Path(session_path)
        self.chunk_duration = chunk_duration
        self.overlap_duration = overlap_duration
        self.on_status = on_status
        
        # Create callbacks for each source
        def make_callback(source: str):
            def cb(chunk: AudioChunk):
                if callback:
                    callback(source, chunk)
            return cb
        
        # Create live transcription callback for each source
        def make_live_callback(source: str):
            def cb(chunk: AudioChunk):
                if live_transcription_callback:
                    live_transcription_callback(source, chunk)
            return cb
        
        # Create chunked recorders for both sources
        self.mic_recorder = ChunkedAudioRecorder(
            session_path=str(session_path),
            source='mic',
            chunk_duration=chunk_duration,
            overlap_duration=overlap_duration,
            callback=make_callback('mic'),
            vad_aggressiveness=vad_aggressiveness,
            vad_threshold=vad_threshold,
            live_transcription_callback=make_live_callback('mic')
        )
        
        self.system_recorder = ChunkedAudioRecorder(
            session_path=str(session_path),
            source='system',
            chunk_duration=chunk_duration,
            overlap_duration=overlap_duration,
            callback=make_callback('system'),
            vad_aggressiveness=vad_aggressiveness,
            vad_threshold=vad_threshold,
            live_transcription_callback=make_live_callback('system')
        )
        
        self._is_running = False

        # Watchdog state
        self._watchdog_enabled = bool(_AUDIO_CAPTURE.get("watchdog_enabled", True))
        self._watchdog_interval = float(
            _AUDIO_CAPTURE.get("watchdog_interval_seconds", 15.0)
        )
        self._watchdog_stall_seconds = float(
            _AUDIO_CAPTURE.get("watchdog_system_stall_seconds", 40.0)
        )
        self._watchdog_max_restarts = int(
            _AUDIO_CAPTURE.get("watchdog_max_restarts", 30)
        )
        self._watchdog_restart_cooldown = float(
            _AUDIO_CAPTURE.get("watchdog_restart_cooldown_seconds", 20.0)
        )
        self._watchdog_thread: Optional[threading.Thread] = None
        self._watchdog_stop = threading.Event()
        self._restart_total = 0
        self._last_restart_at: dict = {}
        self._gave_up = False

    def _emit(self, source: str, message: str, is_error: bool = False) -> None:
        """Report a capture-health event via the on_status callback + log."""
        if is_error:
            logger.error(f"[{source} audio] {message}")
        else:
            logger.info(f"[{source} audio] {message}")
        if self.on_status:
            try:
                self.on_status(source, message, is_error)
            except Exception as e:  # never let a UI callback break the watchdog
                logger.warning(f"on_status callback raised: {e}")

    def _watchdog_loop(self) -> None:
        """Periodically check both recorders and restart a broken one."""
        recorders = (('system', self.system_recorder), ('mic', self.mic_recorder))

        while not self._watchdog_stop.wait(self._watchdog_interval):
            for name, rec in recorders:
                try:
                    healthy, reason = rec.check_health(self._watchdog_stall_seconds)
                except Exception as e:
                    healthy, reason = False, f"health probe error: {e}"

                if healthy:
                    continue

                if self._gave_up:
                    continue

                now = time.time()
                since_last = now - self._last_restart_at.get(name, 0.0)
                if since_last < self._watchdog_restart_cooldown:
                    continue

                if self._restart_total >= self._watchdog_max_restarts:
                    self._gave_up = True
                    self._emit(
                        name,
                        "capture keeps failing - giving up automatic recovery; "
                        "restart the session to try again",
                        True,
                    )
                    continue

                self._last_restart_at[name] = now
                self._restart_total += 1
                self._emit(name, f"capture lost ({reason}) - restarting", True)

                try:
                    ok = rec.restart()
                except Exception as e:
                    ok = False
                    reason = str(e)

                if ok:
                    self._emit(name, "capture restored", False)
                else:
                    self._emit(name, f"restart deferred ({reason})", True)

        logger.info("Audio watchdog exited")

    def start(self) -> None:
        """Start recording from both sources."""
        self.mic_recorder.start()
        self.system_recorder.start()
        self._is_running = True

        if self._watchdog_enabled:
            self._watchdog_stop = threading.Event()
            self._restart_total = 0
            self._last_restart_at = {}
            self._gave_up = False
            self._watchdog_thread = threading.Thread(
                target=self._watchdog_loop, daemon=True
            )
            self._watchdog_thread.start()

        logger.info("Started dual-source chunked recording")

    def stop(self) -> tuple[List[AudioChunk], List[AudioChunk]]:
        """Stop recording from both sources.

        Returns:
            Tuple of (mic_chunks, system_chunks).
        """
        self._watchdog_stop.set()
        if self._watchdog_thread:
            self._watchdog_thread.join(timeout=self._watchdog_interval + 2)
            self._watchdog_thread = None

        mic_chunks = self.mic_recorder.stop()
        system_chunks = self.system_recorder.stop()
        self._is_running = False
        logger.info("Stopped dual-source chunked recording")
        return mic_chunks, system_chunks

    @property
    def is_running(self) -> bool:
        """Check if recording is in progress."""
        return self._is_running

    @property
    def is_healthy(self) -> bool:
        """True if both sources currently report healthy capture."""
        return (
            self.mic_recorder.check_health(self._watchdog_stall_seconds)[0]
            and self.system_recorder.check_health(self._watchdog_stall_seconds)[0]
        )

    @property
    def restart_count(self) -> int:
        """Total automatic recorder restarts performed this session."""
        return self._restart_total


def create_chunked_recorder(
    session_path: str,
    source: str = 'mic',
    chunk_duration: int = ChunkedAudioRecorder.CHUNK_DURATION,
    overlap_duration: float = ChunkedAudioRecorder.OVERLAP_DURATION,
    live_transcription_callback: Optional[Callable[[AudioChunk], None]] = None
) -> ChunkedAudioRecorder:
    """Factory function to create a chunked audio recorder.
    
    Args:
        session_path: Base path for the session.
        source: Audio source ('mic' or 'system').
        chunk_duration: Duration of each chunk in seconds.
        overlap_duration: Overlap duration between chunks in seconds.
        live_transcription_callback: Optional callback for live transcription.
        
    Returns:
        ChunkedAudioRecorder instance.
    """
    return ChunkedAudioRecorder(
        session_path=session_path,
        source=source,
        chunk_duration=chunk_duration,
        overlap_duration=overlap_duration,
        live_transcription_callback=live_transcription_callback
    )


def create_dual_source_recorder(
    session_path: str,
    chunk_duration: int = ChunkedAudioRecorder.CHUNK_DURATION,
    overlap_duration: float = ChunkedAudioRecorder.OVERLAP_DURATION,
    live_transcription_callback: Optional[Callable[[str, AudioChunk], None]] = None,
    on_status: Optional[Callable[[str, str, bool], None]] = None
) -> DualSourceChunkedRecorder:
    """Factory function to create a dual-source chunked recorder.

    Args:
        session_path: Base path for the session.
        chunk_duration: Duration of each chunk in seconds.
        overlap_duration: Overlap duration between chunks in seconds.
        live_transcription_callback: Optional callback for live transcription.
                                    Receives (source, chunk) as arguments.
        on_status: Optional capture-health callback (source, message, is_error).

    Returns:
        DualSourceChunkedRecorder instance.
    """
    return DualSourceChunkedRecorder(
        session_path=session_path,
        chunk_duration=chunk_duration,
        overlap_duration=overlap_duration,
        live_transcription_callback=live_transcription_callback,
        on_status=on_status
    )