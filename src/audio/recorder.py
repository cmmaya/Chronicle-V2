import sounddevice as sd
import numpy as np
import wave
from pathlib import Path
from datetime import datetime
import logging
import subprocess
from typing import List, Dict
import threading
import time
from collections import deque

logger = logging.getLogger(__name__)


class AudioRecorder:
    def __init__(self, session_path: str = '/tmp/sessions/session_001', source: str = 'mic'):
        """Initialize AudioRecorder for a specific audio source.
        
        Args:
            session_path: Base path for the session
            source: Audio source ('mic' or 'system')
        """
        self.is_recording = False
        self.frames = []
        self.sample_rate = 44100
        self.channels = 2
        self.device_index = None  # System default
        self.start_time = None
        self.process = None
        
        # Track which source this recorder handles
        self.source = source
        
        self.session_path = Path(session_path)
        self.audio_path = self.session_path / 'audio' / source
        self.audio_path.mkdir(parents=True, exist_ok=True)
        
        # Overlapping chunk configuration
        self.chunk_duration = 10  # seconds
        self.overlap_duration = 1  # seconds
        self.chunk_save_interval = self.chunk_duration - self.overlap_duration  # 9 seconds
        self.chunk_buffer_size = self.chunk_duration + self.overlap_duration  # 11 seconds buffer
        
        # Buffer for overlapping chunks (circular buffer using deque)
        self.audio_buffer = deque()
        self.buffer_lock = threading.Lock()
        
        # Chunk saving thread
        self.chunk_thread = None
        self.chunk_stop_event = threading.Event()
        
        # Parec reader thread for system audio
        self.parec_thread = None
        
        # Chunk tracking
        self.chunk_count = 0

    @staticmethod
    def list_monitor_sources() -> List[Dict[str, str]]:
        """List available PulseAudio monitor sources.
        Returns:
            List of dicts with 'name' and 'description' of each monitor source
        """
        try:
            result = subprocess.run(
                ['pactl', 'list', 'sources'],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            if result.returncode != 0:
                raise RuntimeError(f'pactl failed: {result.stderr}')

            sources = []
            current_source = {}
            for line in result.stdout.splitlines():
                if line.startswith('Source #'):
                    if current_source:
                        sources.append(current_source)
                    current_source = {}
                elif line.strip().startswith('Name: '):
                    current_source['name'] = line.split('Name: ')[1].strip()
                elif line.strip().startswith('Description: '):
                    current_source['description'] = line.split('Description: ')[1].strip()

            if current_source:
                sources.append(current_source)

            # Filter for monitor sources
            return [
                s for s in sources
                if s.get('description', '').lower().startswith('monitor of')
            ]

        except Exception as e:
            logger.error(f'Failed to list monitor sources: {str(e)}')
            return []

    def get_start_time(self) -> datetime:
        """Get the recording start time for timestamp synchronization.
        
        Returns:
            datetime object representing when recording started,
            or None if recording has not been started.
        """
        return self.start_time

    def get_elapsed_time(self) -> float:
        """Get elapsed time since recording started in seconds.
        
        Returns:
            Float representing seconds since recording started,
            or 0.0 if not currently recording.
        """
        if not self.is_recording or not self.start_time:
            return 0.0
        return (datetime.now() - self.start_time).total_seconds()

    def _validate_audio_data(self):
        if not self.frames:
            raise ValueError('No audio frames captured')
        total_samples = sum(len(frame) for frame in self.frames)
        if total_samples == 0:
            raise ValueError('Empty audio frames captured')

    def _chunk_saving_loop(self):
        """Background thread that saves overlapping audio chunks at regular intervals."""
        while not self.chunk_stop_event.is_set():
            time.sleep(self.chunk_save_interval)
            if self.is_recording:
                self._save_audio_chunk()

    def _save_audio_chunk(self):
        """Save the last chunk_duration seconds from the buffer as a WAV file."""
        with self.buffer_lock:
            if not self.audio_buffer:
                return
            
            # Calculate how many samples we need for chunk_duration
            needed_samples = int(self.chunk_duration * self.sample_rate * self.channels)
            
            # Collect frames from buffer to get needed samples
            collected_frames = []
            collected_samples = 0
            
            # Iterate through buffer from oldest to newest
            for frame in self.audio_buffer:
                if collected_samples >= needed_samples:
                    break
                collected_frames.append(frame)
                collected_samples += frame.size
            
            if collected_samples == 0:
                return
            
            # Concatenate and trim to exact needed samples
            audio_data = np.concatenate(collected_frames, axis=0)
            if audio_data.size > needed_samples:
                audio_data = audio_data[:needed_samples]
        
        # Convert to int16 and save
        int16 = (audio_data * 32767).astype(np.int16)
        bytes_data = int16.tobytes()
        
        # Generate chunk filename with timestamp and index
        chunk_index = self.chunk_count
        self.chunk_count += 1
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        output_path = self.audio_path / f'{timestamp}_chunk_{chunk_index:04d}.wav'
        
        try:
            output_path_str = str(output_path)
            with wave.open(output_path_str, 'wb') as wf:
                wf.setnchannels(self.channels)
                wf.setsampwidth(2)  # 16-bit
                wf.setframerate(self.sample_rate)
                wf.writeframes(bytes_data)
            logger.info(f'Saved {self.chunk_duration}s chunk to {output_path}')
        except Exception as e:
            logger.error(f'Failed to save chunk: {str(e)}')

    def start_recording(self, device_index: int = None, monitor: bool = False, monitor_source: str = None):
        if self.is_recording:
            return

        self.device_index = device_index
        self.frames = []
        self.is_recording = True
        self.start_time = datetime.now()
        
        # Reset buffer and chunk count
        self.audio_buffer.clear()
        self.chunk_count = 0
        self.chunk_stop_event.clear()

        # Start chunk saving thread
        self.chunk_thread = threading.Thread(target=self._chunk_saving_loop, daemon=True)
        self.chunk_thread.start()

        if monitor:
            # Verify parec is available
            try:
                subprocess.run(['parec', '--version'], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            except Exception as e:
                raise RuntimeError('parec not found. Install pulseaudio-utils') from e

            # Use specified monitor source or default
            if monitor_source is None:
                sources = self.list_monitor_sources()
                if not sources:
                    raise RuntimeError('No monitor sources found')
                monitor_source = sources[0]['name']

            self.process = subprocess.Popen(
                ['parec', '--format=s16le', '--rate=44100', '--channels=2', f'--device={monitor_source}'],
                stdout=subprocess.PIPE
            )
            
            # Start a thread to read parec output and populate buffer for chunking
            def parec_reader():
                buffer = bytearray()
                while self.is_recording and self.process:
                    try:
                        chunk = self.process.stdout.read(4096)
                        if not chunk:
                            break
                        buffer.extend(chunk)
                        
                        # When we have enough bytes for ~0.1s of audio, convert and add to buffer
                        # 44100 Hz * 2 channels * 2 bytes/sample = 176400 bytes/second
                        # So ~17640 bytes = 0.1 seconds
                        if len(buffer) >= 17640:
                            audio_bytes = bytes(buffer)
                            buffer.clear()
                            
                            # Convert s16le to float32 numpy array for buffer consistency
                            audio_array = np.frombuffer(audio_bytes, dtype=np.int16)
                            audio_float = audio_array.astype(np.float32) / 32767.0
                            
                            # Reshape to (frames, channels)
                            audio_float = audio_float.reshape(-1, self.channels)
                            
                            with self.buffer_lock:
                                self.audio_buffer.append(audio_float)
                                # Trim buffer to keep only last (chunk_duration + overlap_duration) seconds
                                max_samples = int((self.chunk_buffer_size) * self.sample_rate * self.channels)
                                total_samples = sum(frame.size for frame in self.audio_buffer)
                                while total_samples > max_samples and self.audio_buffer:
                                    removed = self.audio_buffer.popleft()
                                    total_samples -= removed.size
                    except Exception as e:
                        logger.error(f'Error reading parec output: {e}')
                        break
            
            self.parec_thread = threading.Thread(target=parec_reader, daemon=True)
            self.parec_thread.start()
            
            logger.info(f'System audio recording started using parec (source: {monitor_source})')
            return

        def callback(indata, frames, time, status):
            if self.is_recording:
                # Add to legacy frames list (kept for backward compatibility)
                self.frames.append(indata.copy())
                # Add to circular buffer for chunking
                with self.buffer_lock:
                    self.audio_buffer.append(indata.copy())
                    # Trim buffer to keep only last (chunk_duration + overlap_duration) seconds
                    max_samples = int((self.chunk_buffer_size) * self.sample_rate * self.channels)
                    total_samples = sum(frame.size for frame in self.audio_buffer)
                    while total_samples > max_samples and self.audio_buffer:
                        removed = self.audio_buffer.popleft()
                        total_samples -= removed.size

        try:
            self.stream = sd.InputStream(
                samplerate=self.sample_rate,
                channels=self.channels,
                device=self.device_index,
                dtype='float32',
                callback=callback
            )
            self.stream.start()
            logger.info(f'Recording started on device {self.device_index}')
        except Exception as e:
            logger.error(f'Failed to start recording: {str(e)}')
            raise

    def stop_recording(self, label: str = 'recording'):
        if not self.is_recording:
            return

        self.is_recording = False
        
        # Stop chunk saving thread
        self.chunk_stop_event.set()
        if self.chunk_thread:
            self.chunk_thread.join(timeout=2)
            self.chunk_thread = None
        
        # Stop parec process if running (system audio)
        if self.process:
            self.process.terminate()
            self.process = None
            if self.parec_thread:
                self.parec_thread.join(timeout=1)
                self.parec_thread = None
        
        # Save final chunk (remaining buffer content)
        self._save_audio_chunk()

        # Legacy support: save complete recording only if using sounddevice (not for chunks)
        # This preserves the old behavior for backward compatibility with non-chunked code
        # Note: When using overlapping chunks, the complete recording is reconstructed from chunks
        if self.process is None and self.frames:
            # Only save legacy file if we have frames from sounddevice callback
            # (monitor/parec mode uses different mechanism)
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            output_path = self.audio_path / f'{timestamp}_{label}.wav'
            
            self.stream.stop()
            self.stream.close()
            audio_data = np.concatenate(self.frames, axis=0)
            int16 = (audio_data * 32767).astype(np.int16)
            bytes_data = int16.tobytes()

            try:
                output_path_str = str(output_path)
                with wave.open(output_path_str, 'wb') as wf:
                    wf.setnchannels(self.channels)
                    wf.setsampwidth(2)
                    wf.setframerate(self.sample_rate)
                    wf.writeframes(bytes_data)
                duration = (datetime.now() - self.start_time).total_seconds()
                logger.info(f'Saved {duration:.2f}s complete recording to {output_path}')
            except Exception as e:
                logger.error(f'Failed to save recording: {str(e)}')
        
        logger.info(f'Total chunks saved: {self.chunk_count}')