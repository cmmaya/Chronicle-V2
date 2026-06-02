"""Core audio capture functionality with chunked recording."""

import threading
import time
from pathlib import Path
from datetime import datetime
from typing import List, Optional, Callable
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
        vad_threshold: float = 0.30  # Minimum speech ratio to save chunk
    ):
        """Initialize the chunked audio recorder.

        Args:
            session_path: Base path for the session.
            source: Audio source ('mic' or 'system').
            chunk_duration: Duration of each chunk in seconds.
            overlap_duration: Overlap duration between chunks in seconds.
            callback: Optional callback called after each chunk is saved.
            vad_aggressiveness: VAD aggressiveness mode (0=least, 3=most aggressive).
            vad_threshold: Minimum ratio of frames with speech to save chunk (0.0-1.0).
        """
        self.session_path = Path(session_path)
        self.source = source
        self.chunk_duration = chunk_duration
        self.overlap_duration = overlap_duration
        self.callback = callback
        self.vad_aggressiveness = vad_aggressiveness
        self.vad_threshold = vad_threshold
        
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
        
        logger.info(f"Saved chunk: {chunk_id} ({chunk.duration:.2f}s)")
        
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
        
        logger.info(f"Saved chunk: {chunk_id} ({chunk.duration:.2f}s)")
        
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
        vad_threshold: float = 0.30
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
        """
        self.session_path = Path(session_path)
        self.chunk_duration = chunk_duration
        self.overlap_duration = overlap_duration
        
        # Create callbacks for each source
        def make_callback(source: str):
            def cb(chunk: AudioChunk):
                if callback:
                    callback(source, chunk)
            return cb
        
        # Create chunked recorders for both sources
        self.mic_recorder = ChunkedAudioRecorder(
            session_path=str(session_path),
            source='mic',
            chunk_duration=chunk_duration,
            overlap_duration=overlap_duration,
            callback=make_callback('mic'),
            vad_aggressiveness=vad_aggressiveness,
            vad_threshold=vad_threshold
        )
        
        self.system_recorder = ChunkedAudioRecorder(
            session_path=str(session_path),
            source='system',
            chunk_duration=chunk_duration,
            overlap_duration=overlap_duration,
            callback=make_callback('system'),
            vad_aggressiveness=vad_aggressiveness,
            vad_threshold=vad_threshold
        )
        
        self._is_running = False
    
    def start(self) -> None:
        """Start recording from both sources."""
        self.mic_recorder.start()
        self.system_recorder.start()
        self._is_running = True
        logger.info("Started dual-source chunked recording")
    
    def stop(self) -> tuple[List[AudioChunk], List[AudioChunk]]:
        """Stop recording from both sources.
        
        Returns:
            Tuple of (mic_chunks, system_chunks).
        """
        mic_chunks = self.mic_recorder.stop()
        system_chunks = self.system_recorder.stop()
        self._is_running = False
        logger.info("Stopped dual-source chunked recording")
        return mic_chunks, system_chunks
    
    @property
    def is_running(self) -> bool:
        """Check if recording is in progress."""
        return self._is_running


def create_chunked_recorder(
    session_path: str,
    source: str = 'mic',
    chunk_duration: int = 10,
    overlap_duration: int = 1
) -> ChunkedAudioRecorder:
    """Factory function to create a chunked audio recorder.
    
    Args:
        session_path: Base path for the session.
        source: Audio source ('mic' or 'system').
        chunk_duration: Duration of each chunk in seconds.
        overlap_duration: Overlap duration between chunks in seconds.
        
    Returns:
        ChunkedAudioRecorder instance.
    """
    return ChunkedAudioRecorder(
        session_path=session_path,
        source=source,
        chunk_duration=chunk_duration,
        overlap_duration=overlap_duration
    )


def create_dual_source_recorder(
    session_path: str,
    chunk_duration: int = 10,
    overlap_duration: int = 1
) -> DualSourceChunkedRecorder:
    """Factory function to create a dual-source chunked recorder.
    
    Args:
        session_path: Base path for the session.
        chunk_duration: Duration of each chunk in seconds.
        overlap_duration: Overlap duration between chunks in seconds.
        
    Returns:
        DualSourceChunkedRecorder instance.
    """
    return DualSourceChunkedRecorder(
        session_path=session_path,
        chunk_duration=chunk_duration,
        overlap_duration=overlap_duration
    )