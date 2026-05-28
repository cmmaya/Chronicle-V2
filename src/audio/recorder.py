import sounddevice as sd
import numpy as np
import wave
from pathlib import Path
from datetime import datetime
import logging
import subprocess
from typing import List, Dict

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

    def start_recording(self, device_index: int = None, monitor: bool = False, monitor_source: str = None):
        if self.is_recording:
            return

        self.device_index = device_index
        self.frames = []
        self.is_recording = True
        self.start_time = datetime.now()

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
            logger.info(f'System audio recording started using parec (source: {monitor_source})')
            return

        def callback(indata, frames, time, status):
            if self.is_recording:
                self.frames.append(indata.copy())

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
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        output_path = self.audio_path / f'{timestamp}_{label}.wav'

        if self.process:
            # Handle parec recording: raw_audio is already bytes (s16le)
            self.process.terminate()
            raw_audio = self.process.communicate()[0]
            bytes_data = raw_audio
            self.process = None
        else:
            # Handle sounddevice recording: convert float32 numpy array -> int16 bytes
            self.stream.stop()
            self.stream.close()
            audio_data = np.concatenate(self.frames, axis=0)
            # If audio_data is 2D (frames, channels), flatten to interleaved int16
            int16 = (audio_data * 32767).astype(np.int16)
            bytes_data = int16.tobytes()

        try:
            # Ensure path is a string for wave.open (wave.open may treat Path as file-like)
            output_path_str = str(output_path)
            with wave.open(output_path_str, 'wb') as wf:
                wf.setnchannels(self.channels)
                wf.setsampwidth(2)  # 16-bit
                wf.setframerate(self.sample_rate)
                wf.writeframes(bytes_data)

            duration = (datetime.now() - self.start_time).total_seconds()
            logger.info(f'Saved {duration:.2f}s recording to {output_path}')
        except Exception as e:
            logger.error(f'Failed to save recording: {str(e)}')
            raise