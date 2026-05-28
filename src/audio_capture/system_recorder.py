"""System audio recorder using soundcard loopback functionality."""

import threading
import time
from pathlib import Path
from typing import Optional
import logging

logger = logging.getLogger(__name__)


class SystemAudioRecorder:
    """Recorder for system audio using soundcard loopback.

    This recorder captures system audio by using the soundcard library's
    loopback functionality, which captures audio played through the
    system's default speaker.

    Attributes:
        SAMPLE_RATE: Default sample rate (48000 Hz).
        CHANNELS: Number of audio channels (2 for stereo).
    """

    SAMPLE_RATE = 48000
    CHANNELS = 2

    def __init__(
        self,
        session_path: str = '/tmp/sessions/session_001',
        source: str = 'system',
        sample_rate: int = 48000,
        channels: int = 2
    ):
        """Initialize the system audio recorder.

        Args:
            session_path: Base path for the session.
            source: Audio source identifier (unused, kept for compatibility).
            sample_rate: Sample rate for recording.
            channels: Number of audio channels.
        """
        self.session_path = Path(session_path)
        self.source = source
        self.sample_rate = sample_rate if sample_rate > 0 else self.SAMPLE_RATE
        self.channels = channels

        # Validate soundcard is available
        try:
            import soundcard as sc
            self._sc = sc
        except ImportError:
            raise ImportError(
                "soundcard library is required for system audio capture. "
                "Install it with: pip install soundcard"
            )

        # Setup audio path
        self.audio_path = self.session_path / 'audio' / source
        self.audio_path.mkdir(parents=True, exist_ok=True)

        # Recording state
        self._is_recording = False
        self._stream = None
        self._data_buffer = []

        logger.info(
            f"Initialized SystemAudioRecorder: "
            f"sample_rate={self.sample_rate}, channels={self.channels}"
        )

    def _get_loopback_microphone(self):
        """Get the loopback microphone for system audio capture.

        Returns:
            A soundcard microphone object with loopback enabled.
        """
        try:
            # Get default speaker to determine loopback device
            default_speaker = self._sc.default_speaker()

            # Get microphone that includes loopback from the speaker
            loopback_mic = self._sc.get_microphone(
                id=str(default_speaker.id),
                include_loopback=True
            )

            logger.info(f"Using loopback device: {loopback_mic}")
            return loopback_mic

        except Exception as e:
            logger.error(f"Failed to get loopback microphone: {e}")
            raise RuntimeError(f"Cannot access system audio loopback: {e}")

    def _recording_thread(self, stop_event: threading.Event) -> None:
        """Background thread for continuous system audio recording.

        Args:
            stop_event: Event to signal recording stop.
        """
        import soundfile as sf

        buffer_size = self.sample_rate  # 1 second buffer

        try:
            loopback_mic = self._get_loopback_microphone()

            with loopback_mic.recorder(
                samplerate=self.sample_rate,
                channels=self.channels
            ) as recorder:
                self._stream = recorder

                logger.info("Started system audio loopback capture")

                while not stop_event.is_set():
                    # Record a chunk of audio
                    try:
                        # Using non-blocking record approach
                        data = recorder.record(numframes=buffer_size)

                        if data is not None and len(data) > 0:
                            self._data_buffer.append(data.copy())

                    except Exception as e:
                        logger.warning(f"Recording chunk error: {e}")
                        time.sleep(0.1)

                logger.info("Stopped system audio loopback capture")

        except Exception as e:
            logger.error(f"Recording thread error: {e}")
            raise

    def start(self) -> None:
        """Start system audio recording."""
        if self._is_recording:
            logger.warning("Already recording")
            return

        self._data_buffer = []
        self._stop_event = threading.Event()
        self._is_recording = True

        # Start recording in background thread
        self._thread = threading.Thread(
            target=self._recording_thread,
            args=(self._stop_event,),
            daemon=True
        )
        self._thread.start()

        logger.info("Started system audio recorder")

    def stop(self) -> list:
        """Stop recording and return accumulated audio data.

        Returns:
            List of numpy arrays containing recorded audio data.
        """
        if not self._is_recording:
            return self._data_buffer

        self._is_recording = False
        self._stop_event.set()

        if hasattr(self, '_thread') and self._thread:
            self._thread.join(timeout=2)
            self._thread = None

        logger.info(f"Stopped system audio recorder. Buffers: {len(self._data_buffer)}")

        return self._data_buffer

    def get_audio_data(self) -> Optional[list]:
        """Get accumulated audio data without stopping.

        Returns:
            List of audio buffers, or None if not recording.
        """
        if not self._is_recording:
            return None
        return self._data_buffer.copy()

    def clear_buffer(self) -> None:
        """Clear the audio data buffer."""
        self._data_buffer = []

    @property
    def is_recording(self) -> bool:
        """Check if recording is in progress."""
        return self._is_recording
