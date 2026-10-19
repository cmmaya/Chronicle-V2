"""Resilience tests for the supervised system-audio (loopback) recorder.

These cover fix 1: the loopback capture thread must not die on a stream fault -
it must tear the stream down, re-resolve the default speaker, and rebuild. The
real `soundcard` library is not installed in CI, so a fake is injected.
"""

import sys
import time
import types
import threading

import numpy as np
import pytest


# --------------------------------------------------------------------------
# Fake `soundcard` module
# --------------------------------------------------------------------------
class _FakeRecorderCtx:
    def __init__(self, owner):
        self._owner = owner

    def __enter__(self):
        self._owner.open_count += 1
        return self

    def __exit__(self, *exc):
        return False

    def record(self, numframes):
        o = self._owner
        o.record_calls += 1
        # Fault window: raise for a burst of calls, then recover.
        if o.fault_start is not None and o.fault_start <= o.record_calls <= o.fault_end:
            raise RuntimeError("simulated device lost")
        return np.zeros((numframes, 1), dtype="float32")


class _FakeMic:
    def __init__(self, owner):
        self._owner = owner

    def recorder(self, samplerate=48000, channels=1):
        return _FakeRecorderCtx(self._owner)


class _FakeSpeaker:
    id = "spk-default"


class _FakeSoundcard:
    """Stand-in for the `soundcard` module, with fault injection knobs."""

    def __init__(self):
        self.open_count = 0
        self.record_calls = 0
        self.resolve_calls = 0
        self.resolve_fail_until = 0
        self.fault_start = None
        self.fault_end = -1

    # module-level API used by SystemAudioRecorder
    def default_speaker(self):
        self.resolve_calls += 1
        if self.resolve_calls <= self.resolve_fail_until:
            raise RuntimeError("no default speaker")
        return _FakeSpeaker()

    def get_microphone(self, id, include_loopback=False):
        return _FakeMic(self)


@pytest.fixture(autouse=True)
def _stub_optional_audio_deps(monkeypatch):
    """Importing src.audio_capture pulls in webrtcvad / sounddevice, which are
    not installed in CI. They are irrelevant to the loopback supervisor."""
    if "webrtcvad" not in sys.modules:
        wv = types.ModuleType("webrtcvad")
        wv.Vad = lambda *a, **k: types.SimpleNamespace(
            is_speech=lambda *a, **k: True
        )
        monkeypatch.setitem(sys.modules, "webrtcvad", wv)
    if "sounddevice" not in sys.modules:
        monkeypatch.setitem(sys.modules, "sounddevice", types.ModuleType("sounddevice"))


@pytest.fixture
def fake_sc(monkeypatch):
    fake = _FakeSoundcard()
    mod = types.ModuleType("soundcard")
    mod.default_speaker = fake.default_speaker
    mod.get_microphone = fake.get_microphone
    monkeypatch.setitem(sys.modules, "soundcard", mod)
    # keep a handle to the stateful object for assertions
    mod._state = fake
    return fake


def _make_recorder(tmp_path):
    from src.audio_capture.system_recorder import SystemAudioRecorder

    rec = SystemAudioRecorder(session_path=str(tmp_path), source="system", channels=1)
    # make backoff and error thresholds fast for tests
    rec.BACKOFF_INITIAL = 0.02
    rec.BACKOFF_MAX = 0.05
    rec.MAX_CONSECUTIVE_ERRORS = 3
    rec.SILENT_STALL_SECONDS = 0.5
    return rec


def _wait_for(predicate, timeout=5.0, interval=0.02):
    end = time.time() + timeout
    while time.time() < end:
        if predicate():
            return True
        time.sleep(interval)
    return False


def test_supervisor_rebuilds_after_stream_fault(fake_sc, tmp_path):
    fake_sc.fault_start = 4
    fake_sc.fault_end = 10  # raise on calls 4..10, then recover

    rec = _make_recorder(tmp_path)
    rec.start()
    try:
        # It should rebuild the stream at least once and keep running.
        assert _wait_for(lambda: rec.restart_count >= 1), "stream was never rebuilt"
        # After the fault window, capture resumes and frames flow again.
        assert _wait_for(
            lambda: rec.is_stream_active and rec.seconds_since_last_data() < 1.0
        ), "capture did not recover after the fault"
        assert rec.is_thread_alive, "supervisor thread died"
        assert fake_sc.open_count >= 2, "stream context was not re-opened"
        # default speaker is re-resolved on every rebuild
        assert fake_sc.resolve_calls >= 2
    finally:
        rec.stop()
    assert not rec.is_thread_alive


def test_supervisor_survives_device_resolution_failure(fake_sc, tmp_path):
    fake_sc.resolve_fail_until = 3  # first 3 default_speaker() calls raise

    rec = _make_recorder(tmp_path)
    rec.start()
    try:
        assert _wait_for(lambda: rec.is_stream_active), "never recovered from resolve failure"
        assert rec.seconds_since_last_data() < 1.0
    finally:
        rec.stop()


def test_supervisor_rebuilds_on_silent_stall(fake_sc, tmp_path):
    # record() returns an empty array -> stream considered stalled.
    class _EmptyCtx(_FakeRecorderCtx):
        def record(self, numframes):
            self._owner.record_calls += 1
            return np.zeros((0, 1), dtype="float32")

    original = _FakeMic.recorder
    _FakeMic.recorder = lambda self, samplerate=48000, channels=1: _EmptyCtx(self._owner)
    try:
        rec = _make_recorder(tmp_path)
        rec.start()
        try:
            assert _wait_for(lambda: rec.restart_count >= 1, timeout=5.0), \
                "silent stall did not trigger a rebuild"
        finally:
            rec.stop()
    finally:
        _FakeMic.recorder = original


def test_stop_is_prompt_and_thread_exits(fake_sc, tmp_path):
    rec = _make_recorder(tmp_path)
    rec.start()
    assert _wait_for(lambda: rec.is_stream_active)
    t0 = time.time()
    rec.stop()
    assert time.time() - t0 < 6.0
    assert not rec.is_thread_alive
    assert rec.is_recording is False


def test_get_audio_data_none_when_not_recording(fake_sc, tmp_path):
    rec = _make_recorder(tmp_path)
    assert rec.get_audio_data() is None


# --------------------------------------------------------------------------
# Fix 2: DualSourceChunkedRecorder watchdog
# --------------------------------------------------------------------------
class _FakeChunkRec:
    def __init__(self, healthy=True):
        self._healthy = healthy
        self.restart_calls = 0
        self.started = False
        self.stopped = False

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True
        return []

    def check_health(self, stall_seconds=None):
        return (self._healthy, "ok" if self._healthy else "no system audio frames for 99s")

    def restart(self):
        self.restart_calls += 1
        self._healthy = True
        return True


def _make_dual(tmp_path, **overrides):
    from src.audio_capture.core import DualSourceChunkedRecorder

    events = []
    dsr = DualSourceChunkedRecorder(
        session_path=str(tmp_path),
        on_status=lambda s, m, e: events.append((s, m, e)),
    )
    dsr._watchdog_interval = 0.05
    dsr._watchdog_restart_cooldown = 0.0
    for k, v in overrides.items():
        setattr(dsr, k, v)
    return dsr, events


def test_watchdog_restarts_unhealthy_recorder(fake_sc, tmp_path):
    dsr, events = _make_dual(tmp_path)
    sysrec = _FakeChunkRec(healthy=False)
    dsr.system_recorder = sysrec
    dsr.mic_recorder = _FakeChunkRec(healthy=True)

    dsr.start()
    try:
        assert _wait_for(lambda: sysrec.restart_calls >= 1), "watchdog never restarted"
    finally:
        dsr.stop()

    assert any(is_err for _, _, is_err in events), "no error event emitted"
    assert any("restored" in msg for _, msg, _ in events), "no recovery event emitted"
    assert sysrec.stopped


def test_watchdog_leaves_healthy_recorders_alone(fake_sc, tmp_path):
    dsr, events = _make_dual(tmp_path)
    sysrec = _FakeChunkRec(healthy=True)
    micrec = _FakeChunkRec(healthy=True)
    dsr.system_recorder = sysrec
    dsr.mic_recorder = micrec

    dsr.start()
    time.sleep(0.3)
    dsr.stop()

    assert sysrec.restart_calls == 0
    assert micrec.restart_calls == 0
    assert events == []


def test_watchdog_gives_up_after_max_restarts(fake_sc, tmp_path):
    dsr, events = _make_dual(tmp_path, _watchdog_max_restarts=2)

    class _NeverHealthy(_FakeChunkRec):
        def restart(self):
            self.restart_calls += 1
            return True  # but check_health still reports unhealthy

        def check_health(self, stall_seconds=None):
            return (False, "still broken")

    sysrec = _NeverHealthy(healthy=False)
    dsr.system_recorder = sysrec
    dsr.mic_recorder = _FakeChunkRec(healthy=True)

    dsr.start()
    try:
        assert _wait_for(
            lambda: any("giving up" in msg for _, msg, _ in events), timeout=5.0
        ), "watchdog never gave up"
    finally:
        dsr.stop()

    assert sysrec.restart_calls <= 2
