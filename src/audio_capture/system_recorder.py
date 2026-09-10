"""System audio recorder using soundcard loopback functionality.

The loopback stream is bound to whatever device is the default speaker at the
moment it is opened. Windows silently swaps the default output device in several
common situations - most notably when a Zoom/Meet/Teams call ends and the
"communications" device reverts to the "multimedia" default. When that happens
the loopback stream goes stale: ``record()`` either starts raising or quietly
returns nothing, and the old code would spin forever against the dead handle (or
let the thread die outright).

This recorder runs a *supervisor* loop instead: on any fault it tears the stream
down, re-resolves ``default_speaker()``, and rebuilds - with exponential backoff
and a hard cap enforced by the watchdog in ``core.py``. The recording thread
never propagates an exception; it only exits when explicitly stopped.
"""

import threading
import time
from pathlib import Path
from typing import Optional
import logging

try:
    from src.config import AUDIO_CAPTURE as _AUDIO_CAPTURE
except Exception:  # pragma: no cover - config should always import
    _AUDIO_CAPTURE = {}

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

    # Resilience tunables (overridable via src.config.AUDIO_CAPTURE).
    MAX_CONSECUTIVE_ERRORS = int(_AUDIO_CAPTURE.get("loopback_max_consecutive_errors", 5))
    BACKOFF_INITIAL = float(_AUDIO_CAPTURE.get("loopback_backoff_initial", 1.0))
    BACKOFF_MAX = float(_AUDIO_CAPTURE.get("loopback_backoff_max", 30.0))
    SILENT_STALL_SECONDS = float(_AUDIO_CAPTURE.get("loopback_silent_stall_seconds", 20.0))

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
        self._stream_active = False
        self._data_buffer = []
        self._buffer_lock = threading.Lock()

        # Health / supervision state (read by the watchdog in core.py)
        self._last_data_time = 0.0
        self._restart_count = 0
        self._last_error: Optional[str] = None
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

        logger.info(
            f"Initialized SystemAudioRecorder: "
            f"sample_rate={self.sample_rate}, channels={self.channels}"
        )

    def _get_loopback_microphone(self):
        """Resolve the loopback microphone for the *current* default speaker.

        Called on every (re)build so a default-output-device change is picked up.

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

    def _capture_once(self, stop_event: threading.Event) -> None:
        """Open a loopback stream and pump frames until it faults or we stop.

        Returns normally when the stream should be rebuilt (fault) or when
        ``stop_event`` is set. Never raises - all faults are converted to a
        return so the supervisor can decide whether to retry.
        """
        buffer_size = self.sample_rate  # ~1 second per record() call

        loopback_mic = self._get_loopback_microphone()

        with loopback_mic.recorder(
            samplerate=self.sample_rate,
            channels=self.channels
        ) as recorder:
            self._stream = recorder
            self._stream_active = True
            self._last_data_time = time.time()
            consecutive_errors = 0
            logger.info("Started system audio loopback capture")

            try:
                while not stop_event.is_set():
                    try:
                        data = recorder.record(numframes=buffer_size)
                    except Exception as e:
                        consecutive_errors += 1
                        self._last_error = str(e)
                        logger.warning(
                            f"Recording chunk error "
                            f"({consecutive_errors}/{self.MAX_CONSECUTIVE_ERRORS}): {e}"
                        )
                        if consecutive_errors >= self.MAX_CONSECUTIVE_ERRORS:
                            logger.error(
                                "Loopback stream is failing repeatedly - "
                                "tearing down for a rebuild"
                            )
                            return
                        stop_event.wait(0.2)
                        continue

                    if data is not None and len(data) > 0:
                        consecutive_errors = 0
                        self._last_data_time = time.time()
                        with self._buffer_lock:
                            self._data_buffer.append(data.copy())
                    else:
                        # Stream returned nothing - loopback normally delivers
                        # zero-frames during silence, so a real gap is a fault.
                        if time.time() - self._last_data_time > self.SILENT_STALL_SECONDS:
                            logger.error(
                                "Loopback stream delivered no frames for "
                                f"{self.SILENT_STALL_SECONDS:.0f}s - rebuilding"
                            )
                            self._last_error = "stream stalled (no frames)"
                            return
                        stop_event.wait(0.1)
            finally:
                self._stream_active = False
                self._stream = None

        logger.info("Stopped system audio loopback capture")

    def _recording_thread(self, stop_event: threading.Event) -> None:
        """Supervisor: keep a loopback capture alive until told to stop.

        Args:
            stop_event: Event to signal recording stop.
        """
        backoff = self.BACKOFF_INITIAL
        first_attempt = True
        # A capture that survived at least this long is treated as "healthy",
        # so an isolated fault much later doesn't inflate the backoff.
        healthy_run_seconds = max(self.BACKOFF_MAX, 30.0)

        while not stop_event.is_set():
            if not first_attempt:
                self._restart_count += 1
                logger.info(
                    f"Rebuilding system audio loopback "
                    f"(restart #{self._restart_count})"
                )
            first_attempt = False

            started = time.time()
            try:
                self._capture_once(stop_event)
            except Exception as e:
                # Should be rare - _capture_once swallows its own faults - but a
                # failure to even resolve the device lands here.
                self._last_error = str(e)
                logger.error(f"System audio supervisor caught: {e}")

            self._stream_active = False

            if stop_event.is_set():
                break

            ran_for = time.time() - started
            if ran_for >= healthy_run_seconds:
                backoff = self.BACKOFF_INITIAL

            logger.warning(
                f"System audio capture dropped after {ran_for:.0f}s; "
                f"re-establishing in {backoff:.1f}s"
            )
            if stop_event.wait(backoff):
                break
            backoff = min(backoff * 2, self.BACKOFF_MAX)

        self._stream_active = False
        logger.info("System audio supervisor exited")

    def start(self) -> None:
        """Start system audio recording."""
        if self._is_recording:
            logger.warning("Already recording")
            return

        with self._buffer_lock:
            self._data_buffer = []
        self._stop_event = threading.Event()
        self._is_recording = True
        self._stream_active = False
        self._last_data_time = time.time()
        self._last_error = None

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
            with self._buffer_lock:
                return list(self._data_buffer)

        self._is_recording = False
        self._stop_event.set()

        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None

        self._stream_active = False

        with self._buffer_lock:
            buffered = list(self._data_buffer)
        logger.info(f"Stopped system audio recorder. Buffers: {len(buffered)}")

        return buffered

    def get_audio_data(self) -> Optional[list]:
        """Get accumulated audio data without stopping.

        Returns:
            List of audio buffers, or None if not recording.
        """
        if not self._is_recording:
            return None
        with self._buffer_lock:
            return list(self._data_buffer)

    def clear_buffer(self) -> None:
        """Clear the audio data buffer."""
        with self._buffer_lock:
            self._data_buffer = []

    def seconds_since_last_data(self) -> float:
        """Seconds since the loopback stream last delivered frames.

        Large values mean the capture has stalled. Returns a large sentinel if
        no data has been seen at all.
        """
        if not self._last_data_time:
            return float('inf')
        return time.time() - self._last_data_time

    @property
    def is_stream_active(self) -> bool:
        """True while a loopback stream is open and pumping frames."""
        return self._stream_active

    @property
    def is_thread_alive(self) -> bool:
        """True while the supervisor thread is running."""
        return self._thread is not None and self._thread.is_alive()

    @property
    def restart_count(self) -> int:
        """How many times the loopback stream has been rebuilt this session."""
        return self._restart_count

    @property
    def last_error(self) -> Optional[str]:
        """Most recent capture error message, if any."""
        return self._last_error

    @property
    def is_recording(self) -> bool:
        """Check if recording is in progress."""
        return self._is_recording
