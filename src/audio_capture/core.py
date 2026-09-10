"""Core audio capture functionality with chunked recording."""

import threading
import time
from pathlib import Path
from datetime import datetime
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


class ChunkedAudioRecorder:
    """Audio recorder that saves audio in fixed-duration chunks.
    
    This recorder captures audio continuously but saves it in discrete
    chunks of a specified duration (default 10 seconds), along with
    metadata for each chunk. Supports overlapping chunks to prevent
    word loss at chunk boundaries.
    
    Attributes:
        CHUNK_DURATION: Default duration of each chunk in seconds.
        OVERLAP_DURATION: Default overlap between consecutive chunks.
    """
    
    CHUNK_DURATION = 10  # seconds
    OVERLAP_DURATION = 1  # seconds
    VAD_SAMPLE_RATE = 16000  # Required by WebRTC VAD
    VAD_FRAME_DURATION = 30  # ms (10, 20, or 30 supported)
    
    def __init__(
        self,
        session_path: str = '/tmp/sessions/session_001',
        source: str = 'mic',
        chunk_duration: int = 10,
        overlap_duration: int = 1,
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
            self.sample_rate = self._system_recorder.sample_rate
            self.channels = 1  # Force mono recording
        else:
            # Use AudioRecorder for microphone
            self._recorder = AudioRecorder(
                session_path=str(session_path),
                source=source
            )
            self.sample_rate = self._recorder.sample_rate
            self.channels = self._recorder.channels
            self._system_recorder = None

        # Track recorded chunks
        self.chunks: List[AudioChunk] = []
        self._is_running = False
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

        # Health tracking (read by the DualSourceChunkedRecorder watchdog)
        self._started_at: float = 0.0
        self._last_chunk_time: float = 0.0
        self._restart_count: int = 0

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
        
        logger.warning(f"VAD check: {speech_frames}/{num_frames} frames ({speech_ratio:.1%}) - {'speech detected' if has_speech else 'silent'}")
        
        return has_speech
    
    def _save_chunk(self, frames: List, start_time: datetime) -> Optional[AudioChunk]:
        """Save audio frames as a chunk file with metadata.
        
        Args:
            frames: List of audio frames to save.
            start_time: Start time of the chunk.
            
        Returns:
            AudioChunk with metadata, or None if no speech detected.
        """
        # Convert frames to audio data
        if not frames:
            logger.warning("No frames for chunk")
            return None
        
        audio_data = np.concatenate(frames, axis=0)
        
        # Run VAD check - skip if no speech detected
        if not self._check_vad(audio_data):
            logger.debug("Skipping silent chunk")
            return None
        
        # Calculate end time
        timestamp_start = start_time
        timestamp_end = datetime.now()
        
        # Generate chunk ID and filename
        chunk_id = generate_chunk_id(self.source, timestamp_start)
        filename = generate_filename(timestamp_start, timestamp_end)
        file_path = self.audio_path / filename
        
        # Convert frames to audio data
        if frames:
            audio_data = np.concatenate(frames, axis=0)
            # Convert float32 to int16
            int16_data = (audio_data * 32767).astype(np.int16)
            bytes_data = int16_data.tobytes()
        else:
            logger.warning(f"No frames for chunk {chunk_id}")
            bytes_data = b''
        
        # Save audio file
        with wave.open(str(file_path), 'wb') as wf:
            wf.setnchannels(self.channels)
            wf.setsampwidth(2)  # 16-bit
            wf.setframerate(self.sample_rate)
            wf.writeframes(bytes_data)
        
        # Create metadata
        chunk = AudioChunk(
            source=self.source,
            chunk_id=chunk_id,
            timestamp_start=timestamp_start.isoformat(),
            timestamp_end=timestamp_end.isoformat(),
            file_path=str(file_path)
        )
        
        # Save metadata file
        chunk.save_metadata()
        self._last_chunk_time = time.time()

        logger.info(f"Saved chunk: {chunk_id} ({chunk.duration:.2f}s)")
        
        # Call live transcription callback if provided
        if self.live_transcription_callback:
            print(f"[DEBUG] Live transcription callback triggered for chunk: {chunk.chunk_id} (source: {self.source})")
            try:
                self.live_transcription_callback(chunk)
            except Exception as e:
                logger.error(f"Error in live_transcription_callback: {e}")
        
        return chunk

    def _save_chunk_from_array(self, audio_data: np.ndarray, start_time: datetime) -> Optional[AudioChunk]:
        """Save numpy audio array as a chunk file with metadata.
        
        Args:
            audio_data: Numpy array of audio data.
            start_time: Start time of the chunk.
            
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
        timestamp_end = datetime.now()
        
        chunk_id = generate_chunk_id(self.source, timestamp_start)
        filename = generate_filename(timestamp_start, timestamp_end)
        file_path = self.audio_path / filename
        
        # Convert float32 to int16
        int16_data = (audio_data * 32767).astype(np.int16)
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
        
        # Call live transcription callback if provided
        if self.live_transcription_callback:
            print(f"[DEBUG] Live transcription callback triggered for chunk: {chunk.chunk_id} (source: {self.source})")
            try:
                self.live_transcription_callback(chunk)
            except Exception as e:
                logger.error(f"Error in live_transcription_callback: {e}")
        
        return chunk
    
    def _recording_loop(self) -> None:
        """Main recording loop that saves chunks with overlapping."""
        import sounddevice as sd
        
        # Use a continuous buffer approach for overlapping chunks
        all_frames = []
        chunk_start_time = None
        
        def callback(indata, frames, time_info, status):
            nonlocal all_frames, chunk_start_time
            
            if not self._is_running:
                return
            
            if chunk_start_time is None:
                chunk_start_time = datetime.now()
            
            # Append audio data to buffer
            all_frames.append(indata.copy())
            
            # Calculate total samples
            total_samples = sum(len(f) for f in all_frames)
            
            # Check if we have enough new samples to save a chunk
            # Save every (chunk_duration - overlap_duration) seconds of NEW audio
            if total_samples >= self._samples_per_save and total_samples >= self._samples_per_chunk:
                # Get the last chunk_duration seconds (includes overlap from previous)
                audio_data = np.concatenate(all_frames, axis=0)
                chunk_data = audio_data[-self._samples_per_chunk:]
                
                # Save the chunk
                chunk = self._save_chunk_from_array(chunk_data, chunk_start_time)
                if chunk is not None:
                    self.chunks.append(chunk)
                    if self.callback:
                        self.callback(chunk)
                
                # Keep only the overlap for the next chunk
                if total_samples > self._overlap_samples:
                    audio_data = np.concatenate(all_frames, axis=0)
                    overlap_data = audio_data[-self._overlap_samples:]
                    
                    # Split back into frames (sounddevice typically gives ~1024 frames per callback)
                    all_frames = []
                    frame_size = 1024
                    pos = 0
                    while pos < len(overlap_data):
                        chunk_size = min(frame_size, len(overlap_data) - pos)
                        all_frames.append(overlap_data[pos:pos + chunk_size])
                        pos += chunk_size
                else:
                    all_frames = []
                
                # Update chunk start time for next chunk
                chunk_start_time = datetime.now()
        
        # Create stream using instance attributes
        self._stream = sd.InputStream(
            samplerate=self.sample_rate,
            channels=self.channels,
            device=self._recorder.device_index,
            dtype='float32',
            callback=callback
        )
        self._stream.start()
        
        # Run until stopped
        while not self._stop_event.is_set():
            time.sleep(0.1)
        
        # Stop stream
        self._stream.stop()
        self._stream.close()
        
        # Save remaining frames as final chunk
        if all_frames and chunk_start_time:
            audio_data = np.concatenate(all_frames, axis=0)
            if len(audio_data) > 0:
                chunk = self._save_chunk_from_array(audio_data, chunk_start_time)
                if chunk is not None:
                    self.chunks.append(chunk)
                    if self.callback:
                        self.callback(chunk)
    
    def _system_recording_loop(self) -> None:
        """Recording loop for system audio with overlapping chunks."""
        if self._system_recorder is None:
            logger.error("System recorder not initialized")
            return
        
        # Use the same buffer approach as _recording_loop
        all_frames = []
        chunk_start_time = None
        
        # Start the system recorder
        self._system_recorder.start()
        poll_interval = 0.5  # Poll every 500ms for new data
        
        while not self._stop_event.is_set():
            time.sleep(poll_interval)
            
            if not self._is_running:
                break
            
            # Get accumulated audio data
            audio_data_list = self._system_recorder.get_audio_data()
            
            if audio_data_list:
                if chunk_start_time is None:
                    chunk_start_time = datetime.now()
                
                # Add all new data to buffer
                for data in audio_data_list:
                    all_frames.append(data)
                
                # Clear the buffer after reading
                self._system_recorder.clear_buffer()
                
                # Calculate total samples
                total_samples = sum(len(f) for f in all_frames)
                
                # Check if we have enough for a chunk with overlap
                if total_samples >= self._samples_per_save and total_samples >= self._samples_per_chunk:
                    # Get the last chunk_duration seconds
                    audio_data = np.concatenate(all_frames, axis=0)
                    chunk_data = audio_data[-self._samples_per_chunk:]
                    
                    # Save the chunk
                    chunk = self._save_chunk_from_array(chunk_data, chunk_start_time)
                    if chunk is not None:
                        self.chunks.append(chunk)
                        if self.callback:
                            self.callback(chunk)
                    
                    # Keep only the overlap for next chunk
                    if total_samples > self._overlap_samples:
                        audio_data = np.concatenate(all_frames, axis=0)
                        overlap_data = audio_data[-self._overlap_samples:]
                        
                        # Split back into frames
                        all_frames = []
                        frame_size = 1024
                        pos = 0
                        while pos < len(overlap_data):
                            chunk_size = min(frame_size, len(overlap_data) - pos)
                            all_frames.append(overlap_data[pos:pos + chunk_size])
                            pos += chunk_size
                    else:
                        all_frames = []
                    
                    # Update chunk start time
                    chunk_start_time = datetime.now()
        
        # Stop the system recorder
        self._system_recorder.stop()
        
        # Save remaining frames as final chunk
        if all_frames and chunk_start_time:
            audio_data = np.concatenate(all_frames, axis=0)
            if len(audio_data) > 0:
                chunk = self._save_chunk_from_array(audio_data, chunk_start_time)
                if chunk is not None:
                    self.chunks.append(chunk)
                    if self.callback:
                        self.callback(chunk)
    
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
        chunk_duration: int = 10,
        overlap_duration: int = 1,
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
    chunk_duration: int = 10,
    overlap_duration: int = 1,
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
    chunk_duration: int = 10,
    overlap_duration: int = 1,
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