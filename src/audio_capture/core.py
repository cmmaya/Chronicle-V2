"""Core audio capture functionality with chunked recording."""

import threading
import time
from pathlib import Path
from datetime import datetime
from typing import List, Optional, Callable
import logging
import numpy as np
import wave

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
    metadata for each chunk.
    
    Attributes:
        CHUNK_DURATION: Default duration of each chunk in seconds.
    """
    
    CHUNK_DURATION = 10  # seconds
    
    def __init__(
        self,
        session_path: str = '/tmp/sessions/session_001',
        source: str = 'mic',
        chunk_duration: int = 10,
        callback: Optional[Callable[[AudioChunk], None]] = None
    ):
        """Initialize the chunked audio recorder.

        Args:
            session_path: Base path for the session.
            source: Audio source ('mic' or 'system').
            chunk_duration: Duration of each chunk in seconds.
            callback: Optional callback called after each chunk is saved.
        """
        self.session_path = Path(session_path)
        self.source = source
        self.chunk_duration = chunk_duration
        self.callback = callback

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

        # Setup audio path
        self.audio_path = self.session_path / 'audio' / source
        self.audio_path.mkdir(parents=True, exist_ok=True)
    
    def _save_chunk(self, frames: List, start_time: datetime) -> AudioChunk:
        """Save audio frames as a chunk file with metadata.
        
        Args:
            frames: List of audio frames to save.
            start_time: Start time of the chunk.
            
        Returns:
            AudioChunk with metadata.
        """
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
    
    def _recording_loop(self) -> None:
        """Main recording loop that saves chunks periodically."""
        import sounddevice as sd
        
        chunk_frames = []
        chunk_start_time = None
        samples_per_chunk = self.sample_rate * self.chunk_duration
        
        def callback(indata, frames, time_info, status):
            nonlocal chunk_frames, chunk_start_time
            
            if not self._is_running:
                return
            
            # Initialize chunk start time on first frame
            if chunk_start_time is None:
                chunk_start_time = datetime.now()
            
            # Append audio data to current chunk
            chunk_frames.append(indata.copy())
            
            # Calculate total samples in chunk
            total_samples = sum(len(f) for f in chunk_frames)
            
            # Check if chunk is full
            if total_samples >= samples_per_chunk:
                # Save the chunk
                chunk = self._save_chunk(chunk_frames, chunk_start_time)
                self.chunks.append(chunk)
                
                # Reset for next chunk
                chunk_frames = []
                chunk_start_time = None
                
                # Call callback if provided
                if self.callback:
                    self.callback(chunk)
        
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
        if chunk_frames and chunk_start_time:
            chunk = self._save_chunk(chunk_frames, chunk_start_time)
            self.chunks.append(chunk)
            if self.callback:
                self.callback(chunk)
    
    def _system_recording_loop(self) -> None:
        """Recording loop for system audio using SystemAudioRecorder."""
        # Use the SystemAudioRecorder buffer-based capture
        if self._system_recorder is None:
            logger.error("System recorder not initialized")
            return
        
        chunk_frames = []
        chunk_start_time = None
        samples_per_chunk = self.sample_rate * self.chunk_duration
        
        # Start the system recorder
        self._system_recorder.start()
        poll_interval = 0.5  # Poll every 500ms for new data
        
        while not self._stop_event.is_set():
            time.sleep(poll_interval)
            
            if not self._is_running:
                break
            
            # Get accumulated audio data
            audio_data_list = self._system_recorder.get_audio_data()
            logger.info(f"Retrieved {len(audio_data_list) if audio_data_list else 0} buffers from system recorder.")
            
            if audio_data_list:
                # Initialize chunk start time on first data
                if chunk_start_time is None:
                    chunk_start_time = datetime.now()
                
                # Add all new data to chunk
                for data in audio_data_list:
                    chunk_frames.append(data)
                
                # Clear the buffer after reading
                self._system_recorder.clear_buffer()
                
                # Calculate total samples
                total_samples = sum(len(f) for f in chunk_frames)
                
                # Check if chunk is full
                if total_samples >= samples_per_chunk:
                    chunk = self._save_chunk(chunk_frames, chunk_start_time)
                    self.chunks.append(chunk)
                    
                    chunk_frames = []
                    chunk_start_time = None
                    
                    if self.callback:
                        self.callback(chunk)
        
        # Stop the system recorder
        self._system_recorder.stop()
        
        # Save remaining frames as final chunk
        if chunk_frames and chunk_start_time:
            chunk = self._save_chunk(chunk_frames, chunk_start_time)
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
        callback: Optional[Callable[[str, AudioChunk], None]] = None
    ):
        """Initialize the dual source recorder.
        
        Args:
            session_path: Base path for the session.
            chunk_duration: Duration of each chunk in seconds.
            callback: Optional callback called after each chunk is saved.
                      Receives (source, chunk) as arguments.
        """
        self.session_path = Path(session_path)
        self.chunk_duration = chunk_duration
        
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
            callback=make_callback('mic')
        )
        
        self.system_recorder = ChunkedAudioRecorder(
            session_path=str(session_path),
            source='system',
            chunk_duration=chunk_duration,
            callback=make_callback('system')
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
    chunk_duration: int = 10
) -> ChunkedAudioRecorder:
    """Factory function to create a chunked audio recorder.
    
    Args:
        session_path: Base path for the session.
        source: Audio source ('mic' or 'system').
        chunk_duration: Duration of each chunk in seconds.
        
    Returns:
        ChunkedAudioRecorder instance.
    """
    return ChunkedAudioRecorder(
        session_path=session_path,
        source=source,
        chunk_duration=chunk_duration
    )


def create_dual_source_recorder(
    session_path: str,
    chunk_duration: int = 10
) -> DualSourceChunkedRecorder:
    """Factory function to create a dual-source chunked recorder.
    
    Args:
        session_path: Base path for the session.
        chunk_duration: Duration of each chunk in seconds.
        
    Returns:
        DualSourceChunkedRecorder instance.
    """
    return DualSourceChunkedRecorder(
        session_path=session_path,
        chunk_duration=chunk_duration
    )