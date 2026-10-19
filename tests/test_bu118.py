"""BU118 - mic and system audio mute toggles."""
import os
import tempfile
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np

from src.app.session import Session
from src.app.session_manager import SessionManager
from src.app.window import _MUTE_TOGGLES
from src.audio_capture.core import ChunkedAudioRecorder, DualSourceChunkedRecorder
from tests.detached_harness import DetachedHarness, app

_app = app()

RATE = 16000


def read_wav(path: str) -> np.ndarray:
    import wave
    with wave.open(path, 'rb') as wf:
        raw = wf.readframes(wf.getnframes())
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32767.0


def tone(seconds: float, rate: int = RATE) -> np.ndarray:
    """A loud-ish signal; VAD is disabled in these tests via vad_threshold=0."""
    t = np.arange(int(seconds * rate), dtype=np.float32) / rate
    return (0.3 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)


class RecorderMuteTest(unittest.TestCase):
    """The drop gate inside ChunkedAudioRecorder."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.saved = []
        self.live = []
        self.rec = ChunkedAudioRecorder(
            session_path=self._tmp.name,
            source='mic',
            chunk_duration=1,
            overlap_duration=0.2,
            callback=self.saved.append,
            live_transcription_callback=self.live.append,
        )
        # Capture and storage at the same rate: no resampler, and VAD off so a
        # chunk is judged by the gate alone, not by what it contains.
        self.rec.capture_rate = RATE
        self.rec._set_sample_rate(RATE)
        self.rec.vad_threshold = 0.0
        self.rec._reset_stream_state()

    def tearDown(self):
        self._tmp.cleanup()

    def test_a_recorder_starts_unmuted(self):
        self.assertFalse(self.rec.is_muted)

    def test_set_muted_toggles_and_is_idempotent(self):
        self.rec.set_muted(True)
        self.assertTrue(self.rec.is_muted)
        self.rec._mute_dirty.clear()
        self.rec.set_muted(True)
        self.assertFalse(self.rec._mute_dirty.is_set())
        self.rec.set_muted(False)
        self.assertFalse(self.rec.is_muted)

    def test_unmuted_audio_is_chunked(self):
        self.rec._feed([tone(1.5)])
        self.assertEqual(len(self.saved), 1)
        self.assertEqual(len(self.live), 1)

    def test_muted_audio_is_dropped(self):
        self.rec.set_muted(True)
        self.rec._feed([tone(5.0)])
        self.assertEqual(self.saved, [])
        self.assertEqual(self.live, [])
        self.assertEqual(self.rec.chunks, [])

    def test_muting_flushes_the_audio_captured_before_the_mute(self):
        self.rec._feed([tone(0.8)])  # short of a full chunk
        self.assertEqual(self.saved, [])
        self.rec.set_muted(True)
        self.rec._feed([])  # the next drain applies the transition
        self.assertEqual(len(self.saved), 1)

    def test_a_mute_transition_leaves_a_clean_window(self):
        self.rec._feed([tone(0.8)])
        self.rec.set_muted(True)
        self.rec._feed([])
        self.rec.set_muted(False)
        self.rec._feed([])
        self.assertIsNone(self.rec._pending_start)
        self.assertEqual(self.rec._pending_len, 0)
        self.assertTrue(self.rec._first_window)

    def test_unmuting_resumes_chunking_without_splicing(self):
        self.rec._feed([tone(0.8)])
        self.rec.set_muted(True)
        self.rec._feed([tone(3.0)])
        before = len(self.saved)

        self.rec.set_muted(False)
        marker = np.full(int(1.5 * RATE), 0.5, dtype=np.float32)
        self.rec._feed([marker])

        self.assertEqual(len(self.saved), before + 1)
        # The chunk holds post-unmute audio only: no pre-mute remainder at its
        # front, and nothing from the muted stretch.
        self.assertTrue(np.allclose(read_wav(self.saved[-1].file_path), 0.5, atol=0.01))

    def test_a_muted_recorder_reports_healthy(self):
        self.rec._is_running = True
        self.rec._started_at = time.time() - 600
        self.rec.set_muted(True)
        healthy, reason = self.rec.check_health(stall_seconds=1.0)
        self.assertTrue(healthy)
        self.assertEqual(reason, "muted")

    def test_mute_survives_a_restart(self):
        self.rec.set_muted(True)
        self.rec._is_running = True
        self.rec._recording_loop = lambda: None  # no device in a test run
        self.assertTrue(self.rec.restart())
        self.assertTrue(self.rec.is_muted)


class _StubRecorder:
    def __init__(self):
        self.muted = False

    def set_muted(self, muted):
        self.muted = bool(muted)

    @property
    def is_muted(self):
        return self.muted


class DualRecorderMuteTest(unittest.TestCase):
    """Per-source routing, without opening a device."""

    def setUp(self):
        self.dual = DualSourceChunkedRecorder.__new__(DualSourceChunkedRecorder)
        self.dual.mic_recorder = _StubRecorder()
        self.dual.system_recorder = _StubRecorder()

    def test_each_source_mutes_independently(self):
        self.dual.set_source_muted('mic', True)
        self.assertTrue(self.dual.is_source_muted('mic'))
        self.assertFalse(self.dual.is_source_muted('system'))

        self.dual.set_source_muted('system', True)
        self.dual.set_source_muted('mic', False)
        self.assertFalse(self.dual.is_source_muted('mic'))
        self.assertTrue(self.dual.is_source_muted('system'))

    def test_an_unknown_source_raises(self):
        with self.assertRaises(ValueError):
            self.dual.set_source_muted('speaker', True)
        with self.assertRaises(ValueError):
            self.dual.is_source_muted('speaker')


class SessionMuteTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.session = Session(1, 'Test', self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_without_a_recorder_it_no_ops(self):
        self.assertFalse(self.session.set_source_muted('mic', True))
        self.assertFalse(self.session.is_source_muted('mic'))

    def test_with_a_recorder_it_delegates(self):
        dual = DualSourceChunkedRecorder.__new__(DualSourceChunkedRecorder)
        dual.mic_recorder = _StubRecorder()
        dual.system_recorder = _StubRecorder()
        self.session.dual_recorder = dual

        self.assertTrue(self.session.set_source_muted('system', True))
        self.assertTrue(self.session.is_source_muted('system'))
        self.assertFalse(self.session.is_source_muted('mic'))


def stub_session(tmp: str) -> Session:
    session = Session(1, 'Test', tmp)
    dual = DualSourceChunkedRecorder.__new__(DualSourceChunkedRecorder)
    dual.mic_recorder = _StubRecorder()
    dual.system_recorder = _StubRecorder()
    session.dual_recorder = dual
    return session


class SessionManagerMuteTest(unittest.TestCase):
    def setUp(self):
        self.manager = SessionManager.__new__(SessionManager)
        self.messages = []
        self.manager.status_callback = lambda msg, is_error=False: self.messages.append((msg, is_error))
        self.manager.current_session = None
        self.manager._muted_sources = {'mic': False, 'system': False}

    def test_muting_before_a_session_exists_is_remembered(self):
        self.assertTrue(self.manager.set_source_muted('mic', True))
        self.assertTrue(self.manager.is_source_muted('mic'))
        self.assertFalse(self.manager.is_source_muted('system'))
        self.assertEqual(self.messages, [])

    def test_a_pre_session_mute_reaches_the_new_recorder(self):
        self.manager.set_source_muted('system', True)
        with tempfile.TemporaryDirectory() as tmp:
            session = stub_session(tmp)
            self.manager._apply_muted_sources(session)
            self.assertTrue(session.is_source_muted('system'))
            self.assertFalse(session.is_source_muted('mic'))

    def test_an_unknown_source_is_refused(self):
        self.assertFalse(self.manager.set_source_muted('speaker', True))
        self.assertTrue(self.messages[-1][1])

    def test_a_session_without_a_recorder_still_records_the_choice(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.manager.current_session = Session(1, 'Test', tmp)
            self.assertTrue(self.manager.set_source_muted('mic', True))
            self.assertTrue(self.manager.is_source_muted('mic'))

    def test_with_a_recorder_it_applies_immediately(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.manager.current_session = stub_session(tmp)

            self.assertTrue(self.manager.set_source_muted('mic', True))
            self.assertTrue(self.manager.is_source_muted('mic'))
            self.assertTrue(self.manager.current_session.is_source_muted('mic'))


class _StubSession:
    status = Session.STATUS_ACTIVE


class _StubManager:
    """Stands in for SessionManager, which now holds the mute state itself."""

    def __init__(self, session=None, applies=True):
        self.session = session
        self.applies = applies
        self.calls = []
        self.muted = {'mic': False, 'system': False}

    def get_active_session(self):
        return self.session

    def is_source_muted(self, source):
        return self.muted[source]

    def set_source_muted(self, source, muted):
        self.calls.append((source, muted))
        if self.applies:
            self.muted[source] = muted
        return self.applies


class MuteHarness(DetachedHarness):
    """Builds the real mute row and records which icon each toggle wears."""

    def __init__(self):
        super().__init__()
        self.icon_files = []
        self._mute_row = self._build_mute_toggle_row()

    def _make_icon(self, filename):
        self.icon_files.append(filename)
        return super()._make_icon(filename)

    def faces(self):
        """The icon filename currently on each toggle, in _MUTE_TOGGLES order."""
        return self.icon_files[-len(_MUTE_TOGGLES):]


class MuteToggleUiTest(unittest.TestCase):
    def setUp(self):
        self.harness = MuteHarness()

    def test_toggles_start_enabled_and_unmuted(self):
        for source, _label, on_icon, _off, _fallback in _MUTE_TOGGLES:
            button = self.harness._mute_buttons[source]
            self.assertTrue(button.isEnabled())
            self.assertFalse(self.harness._muted_sources[source])
        self.assertEqual(
            self.harness.faces(),
            [t[2] for t in _MUTE_TOGGLES],
        )

    def test_clicking_before_a_session_starts_is_accepted(self):
        manager = _StubManager(None)
        self.harness.session_manager = manager
        self.harness._update_mute_controls()

        self.harness._mute_buttons['mic'].click()

        self.assertEqual(manager.calls, [('mic', True)])
        self.assertTrue(self.harness._muted_sources['mic'])
        self.assertEqual(self.harness.faces()[0], 'icon_mic_off.svg')
        message, is_error = self.harness.status_messages[-1]
        self.assertFalse(is_error)
        self.assertIn('applies when recording starts', message)

    def test_a_pre_session_mute_survives_the_session_starting(self):
        manager = _StubManager(None)
        self.harness.session_manager = manager
        self.harness._mute_buttons['system'].click()

        manager.session = _StubSession()  # recording begins
        self.harness._update_mute_controls()

        self.assertTrue(self.harness._muted_sources['system'])
        self.assertEqual(self.harness.faces()[1], 'icon_audio_off.svg')

    def test_clicking_mutes_one_source_and_swaps_its_icon(self):
        session = _StubSession()
        self.harness.session_manager = _StubManager(session)
        self.harness._update_mute_controls()

        self.harness._mute_buttons['mic'].click()

        self.assertEqual(self.harness.session_manager.calls, [('mic', True)])
        self.assertTrue(self.harness._muted_sources['mic'])
        self.assertFalse(self.harness._muted_sources['system'])
        self.assertEqual(self.harness.faces(), ['icon_mic_off.svg', 'icon_audio_on.svg'])
        self.assertEqual(self.harness.status_messages[-1], ('Microphone muted', False))

        self.harness._mute_buttons['mic'].click()
        self.assertTrue(self.harness.faces()[0] == 'icon_mic_on.svg')
        self.assertEqual(self.harness.status_messages[-1], ('Microphone unmuted', False))

    def test_clicking_system_audio_reports_its_own_label(self):
        self.harness.session_manager = _StubManager(_StubSession())
        self.harness._update_mute_controls()
        self.harness._mute_buttons['system'].click()
        self.assertEqual(self.harness.status_messages[-1], ('System audio muted', False))
        self.assertEqual(self.harness.faces()[1], 'icon_audio_off.svg')

    def test_a_refused_mute_leaves_the_button_alone(self):
        session = _StubSession()
        self.harness.session_manager = _StubManager(session, applies=False)
        self.harness._update_mute_controls()

        self.harness._mute_buttons['mic'].click()

        self.assertFalse(self.harness._muted_sources['mic'])
        self.assertEqual(self.harness.faces(), ['icon_mic_on.svg', 'icon_audio_on.svg'])

    def test_the_mute_holds_when_a_session_ends(self):
        manager = _StubManager(_StubSession())
        self.harness.session_manager = manager
        self.harness._update_mute_controls()
        self.harness._mute_buttons['mic'].click()

        manager.session = None  # session stopped
        self.harness._update_mute_controls()

        # The icon says muted, so the mute has to still be true - the next
        # recording must not quietly come back with a live mic.
        self.assertTrue(self.harness._muted_sources['mic'])
        self.assertEqual(self.harness.faces()[0], 'icon_mic_off.svg')

    def test_the_toggles_stay_clickable_with_no_session(self):
        self.harness.session_manager = _StubManager(None)
        self.harness._update_mute_controls()
        for source, *_rest in _MUTE_TOGGLES:
            self.assertTrue(self.harness._mute_buttons[source].isEnabled())


class MuteIconAssetTest(unittest.TestCase):
    def test_every_toggle_icon_exists(self):
        from src.app.pixel_theme import asset_path
        for _source, _label, on_icon, off_icon, _fallback in _MUTE_TOGGLES:
            for name in (on_icon, off_icon):
                self.assertTrue(os.path.exists(asset_path(name)), name)


if __name__ == '__main__':
    unittest.main()
