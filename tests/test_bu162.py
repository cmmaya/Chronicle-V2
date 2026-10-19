"""BU162 - optional diarization models: not required, downloaded on demand."""
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from src import model_manager as mm


def _write(cache, model, filename, size):
    path = mm.snapshot_dir(model, cache) / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    return path


def _fake_hf(chunk=1_000_000, before=None):
    """An hf_hub_download stand-in that writes the diarization files in chunks."""
    calls = []

    def download(repo_id, filename, *, revision, cache_dir, force_download, tqdm_class):
        calls.append((repo_id, filename, revision))
        if before:
            before(repo_id, filename)
        model = next(m for m in mm.DIARIZATION_MODELS if m.repo_id == repo_id)
        size = model.files[filename]
        with tqdm_class(total=size, initial=0) as bar:
            for _ in range(0, size, chunk):
                bar.update(min(chunk, size - bar.done))
        return str(_write(Path(cache_dir), model, filename, size))

    return download, calls


class _Tmp(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.cache = Path(self._tmp.name) / "hub"

    def tearDown(self):
        self._tmp.cleanup()


class GroupTests(unittest.TestCase):
    def test_not_in_required_set(self):
        required = {m.name for m in mm.required_models()}
        optional = {m.name for m in mm.DIARIZATION_MODELS}
        self.assertEqual(optional, {mm.SEGMENTATION, mm.SPEAKER_EMBEDDING})
        self.assertFalse(required & optional)
        self.assertFalse({m.repo_id for m in mm.REQUIRED_MODELS}
                         & {m.repo_id for m in mm.DIARIZATION_MODELS})

    def test_pinned_and_sized(self):
        for model in mm.DIARIZATION_MODELS:
            self.assertRegex(model.revision, r"^[0-9a-f]{40}$")
        self.assertEqual(sum(m.total_bytes for m in mm.DIARIZATION_MODELS), 46_250_196)


class StatusTests(_Tmp):
    def test_missing_then_installed(self):
        states = mm.status(self.cache, mm.DIARIZATION_MODELS)
        self.assertEqual([s.state for s in states], ["missing", "missing"])
        self.assertFalse(mm.diarization_installed(self.cache))
        for model in mm.DIARIZATION_MODELS:
            for filename, size in model.files.items():
                _write(self.cache, model, filename, size)
        self.assertTrue(all(s.installed for s in mm.status(self.cache, mm.DIARIZATION_MODELS)))
        self.assertTrue(mm.diarization_installed(self.cache))
        self.assertEqual(
            mm.local_path(mm.SPEAKER_EMBEDDING, "nemo_en_titanet_small.onnx", self.cache),
            mm.snapshot_dir(mm.DIARIZATION_MODELS[1], self.cache) / "nemo_en_titanet_small.onnx",
        )

    def test_required_status_ignores_the_group(self):
        self.assertEqual({s.name for s in mm.status(self.cache)},
                         {m.name for m in mm.required_models()})

    def test_local_path_when_missing_names_the_model(self):
        with self.assertRaises(mm.ModelNotInstalledError) as ctx:
            mm.local_path(mm.SEGMENTATION, cache_dir=self.cache)
        self.assertIn("pyannote", str(ctx.exception))


class DownloadTests(_Tmp):
    def run_download(self, fake, **kwargs):
        events = []
        with mock.patch("huggingface_hub.hf_hub_download", side_effect=fake), \
                mock.patch.object(mm, "check_disk_space"):
            mm.download_diarization(lambda *e: events.append(e), cache_dir=self.cache, **kwargs)
        return events

    def test_progress_reaches_total(self):
        fake, calls = _fake_hf()
        events = self.run_download(fake)
        self.assertTrue(mm.diarization_installed(self.cache))
        self.assertEqual({c[0] for c in calls}, {m.repo_id for m in mm.DIARIZATION_MODELS})
        for model in mm.DIARIZATION_MODELS:
            mine = [e for e in events if e[0] == model.name]
            self.assertEqual(mine[-1], (model.name, model.total_bytes, model.total_bytes))
            done = [e[1] for e in mine]
            self.assertEqual(done, sorted(done))

    def test_cancel_mid_download(self):
        cancel = threading.Event()

        def cancel_on_embedding(repo_id, filename):
            if repo_id == mm.SPEAKER_EMBEDDING_REPO_ID:
                cancel.set()

        fake, _ = _fake_hf(before=cancel_on_embedding)
        with self.assertRaises(mm.DownloadCancelled):
            self.run_download(fake, cancel_event=cancel)
        states = {s.name: s.state for s in mm.status(self.cache, mm.DIARIZATION_MODELS)}
        self.assertEqual(states[mm.SEGMENTATION], "installed")
        self.assertNotEqual(states[mm.SPEAKER_EMBEDDING], "installed")

    def test_works_while_offline_mode_is_on(self):
        import huggingface_hub.constants as hf_constants

        seen = []
        fake, _ = _fake_hf(before=lambda *a: seen.append(hf_constants.HF_HUB_OFFLINE))
        with mock.patch.object(hf_constants, "HF_HUB_OFFLINE", True):
            self.run_download(fake)
            self.assertTrue(hf_constants.HF_HUB_OFFLINE)
        self.assertEqual(seen, [False, False])

    def test_offline_and_disk_full_errors(self):
        import httpx

        def offline(*a, **k):
            raise httpx.ConnectError("no route")

        with mock.patch("huggingface_hub.hf_hub_download", side_effect=offline):
            with self.assertRaises(mm.OfflineError):
                mm.download_diarization(cache_dir=self.cache)
        with mock.patch("shutil.disk_usage", return_value=mock.Mock(free=1_000)):
            with self.assertRaises(mm.DiskFullError):
                mm.download_diarization(cache_dir=self.cache)


if __name__ == "__main__":
    unittest.main()
