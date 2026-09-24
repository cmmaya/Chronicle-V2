"""BU123 - model manager: check, download and verify models."""
import errno
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import httpx

from src import model_manager as mm

TINY = [
    mm.RequiredModel("parakeet", "acme/asr", "a" * 40, {"config.json": 4, "enc.onnx": 10}),
    mm.RequiredModel("embeddings", "acme/emb", "b" * 40, {"onnx/model.onnx": 6}),
]


def _write(cache, model, filename, size):
    path = mm.snapshot_dir(model, cache) / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    return path


def _install(cache, models=TINY):
    for model in models:
        for filename, size in model.files.items():
            _write(cache, model, filename, size)


class _Tmp(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.cache = Path(self._tmp.name) / "hub"

    def tearDown(self):
        self._tmp.cleanup()


class RequiredModelsTests(unittest.TestCase):
    def test_pinned_revisions(self):
        for model in mm.REQUIRED_MODELS:
            self.assertRegex(model.revision, r"^[0-9a-f]{40}$")

    def test_e5_pin_matches_embedding_service(self):
        from src.rag import embeddings
        e5 = next(m for m in mm.REQUIRED_MODELS if m.name == mm.EMBEDDINGS)
        self.assertEqual((e5.repo_id, e5.revision), (embeddings.EMBED_REPO_ID, embeddings.EMBED_REVISION))

    def test_parakeet_repo_matches_onnx_asr(self):
        from onnx_asr.resolver import model_repos
        self.assertEqual(model_repos["nemo-parakeet-tdt-0.6b-v3"], mm.PARAKEET_REPO_ID)

    def test_parakeet_files_are_what_onnx_asr_loads(self):
        import fnmatch
        from onnx_asr.models.nemo import NemoConformerTdt
        for quantization in (None, "int8"):
            with self.subTest(quantization=quantization):
                files = next(m for m in mm.required_models(quantization) if m.name == mm.PARAKEET).files
                for pattern in NemoConformerTdt._get_model_files(quantization).values():
                    self.assertEqual(len(fnmatch.filter(files, pattern)), 1, pattern)
                self.assertIn("config.json", files)

    def test_quantization_follows_config(self):
        with mock.patch.dict(mm.TRANSCRIPTION, {"quantization": "int8"}):
            files = mm.required_models()[0].files
        self.assertIn("encoder-model.int8.onnx", files)
        self.assertNotIn("encoder-model.onnx.data", files)

    def test_unknown_quantization_rejected(self):
        with self.assertRaises(mm.ModelManagerError):
            mm.required_models("fp4")


class StatusTests(_Tmp):
    def test_missing(self):
        states = mm.status(self.cache, TINY)
        self.assertEqual([s.state for s in states], ["missing", "missing"])
        self.assertEqual(mm.bytes_to_download(self.cache, TINY), 20)
        self.assertFalse(mm.is_installed(self.cache, TINY))

    def test_installed(self):
        _install(self.cache)
        self.assertTrue(all(s.installed for s in mm.status(self.cache, TINY)))
        self.assertEqual(mm.bytes_to_download(self.cache, TINY), 0)

    def test_partial_files_are_incomplete(self):
        _write(self.cache, TINY[0], "config.json", 4)
        _write(self.cache, TINY[0], "enc.onnx", 3)  # truncated
        [asr, emb] = mm.status(self.cache, TINY)
        self.assertEqual(asr.state, "incomplete")
        self.assertEqual(asr.missing_files, ["enc.onnx"])
        self.assertEqual(asr.bytes_missing, 10)
        self.assertEqual(emb.state, "missing")

    def test_interrupted_blob_is_incomplete(self):
        blobs = mm.snapshot_dir(TINY[1], self.cache).parent.parent / "blobs"
        blobs.mkdir(parents=True)
        (blobs / "abc.incomplete").write_bytes(b"xx")
        self.assertEqual(mm.status(self.cache, TINY)[1].state, "incomplete")

    def test_status_never_touches_the_network(self):
        with mock.patch.dict(sys.modules, {"huggingface_hub": None}), \
                mock.patch("httpx.Client.send", side_effect=AssertionError("network")):
            mm.status(self.cache, TINY)

    def test_local_path(self):
        with mock.patch.object(mm, "required_models", return_value=TINY):
            with self.assertRaises(mm.ModelNotInstalledError) as ctx:
                mm.local_path("embeddings", cache_dir=self.cache)
            self.assertIn("setup", str(ctx.exception))
            _install(self.cache)
            self.assertEqual(
                mm.local_path("embeddings", "onnx/model.onnx", cache_dir=self.cache),
                mm.snapshot_dir(TINY[1], self.cache) / "onnx/model.onnx",
            )


def _fake_hf(cache, chunk=3, fail_on=None, before=None):
    """An hf_hub_download stand-in writing files in chunks through tqdm_class."""
    calls = []

    def download(repo_id, filename, *, revision, cache_dir, force_download, tqdm_class):
        calls.append((repo_id, filename, force_download))
        if before:
            before(repo_id, filename)
        if fail_on and filename == fail_on[0]:
            raise fail_on[1]
        model = next(m for m in TINY if m.repo_id == repo_id)
        size = model.files[filename]
        with tqdm_class(total=size, initial=0, desc=filename) as bar:
            for _ in range(0, size, chunk):
                bar.update(min(chunk, size - bar.done))
        return str(_write(Path(cache_dir), model, filename, size))

    return download, calls


class DownloadTests(_Tmp):
    def run_download(self, fake, **kwargs):
        events = []
        with mock.patch("huggingface_hub.hf_hub_download", side_effect=fake):
            mm.download(lambda *e: events.append(e), cache_dir=self.cache, models=TINY, **kwargs)
        return events

    def test_progress_reaches_total_then_installed(self):
        fake, calls = _fake_hf(self.cache)
        events = self.run_download(fake)
        self.assertTrue(mm.is_installed(self.cache, TINY))
        self.assertEqual(len(calls), 3)
        for name, total in (("parakeet", 14), ("embeddings", 6)):
            mine = [e for e in events if e[0] == name]
            self.assertEqual(mine[-1], (name, total, total))
            done = [e[1] for e in mine]
            self.assertEqual(done, sorted(done))  # never goes backwards

    def test_only_missing_files_fetched(self):
        _write(self.cache, TINY[0], "config.json", 4)
        _write(self.cache, TINY[1], "onnx/model.onnx", 6)
        fake, calls = _fake_hf(self.cache)
        events = self.run_download(fake)
        self.assertEqual([c[1] for c in calls], ["enc.onnx"])
        self.assertEqual(events[0], ("parakeet", 4, 14))

    def test_wrong_size_file_is_forced(self):
        _write(self.cache, TINY[1], "onnx/model.onnx", 2)
        fake, calls = _fake_hf(self.cache)
        self.run_download(fake)
        self.assertIn(("acme/emb", "onnx/model.onnx", True), calls)

    def test_resume_starts_from_initial(self):
        events = []
        bar = mm._Progress(lambda done: events.append(done), None)
        with bar(total=10, initial=7):
            bar.update(3)
        self.assertEqual(events, [7, 10])

    def test_cancel_mid_file(self):
        cancel = threading.Event()

        def cancel_when_embeddings_start(repo_id, filename):
            if repo_id == "acme/emb":
                cancel.set()

        fake, calls = _fake_hf(self.cache, before=cancel_when_embeddings_start)
        with self.assertRaises(mm.DownloadCancelled):
            self.run_download(fake, cancel_event=cancel)
        self.assertEqual(mm.status(self.cache, TINY)[0].state, "installed")
        self.assertFalse(mm.is_installed(self.cache, TINY))

    def test_cancel_before_start(self):
        cancel = threading.Event()
        cancel.set()
        fake, calls = _fake_hf(self.cache)
        with self.assertRaises(mm.DownloadCancelled):
            self.run_download(fake, cancel_event=cancel)
        self.assertEqual(calls, [])

    def test_error_mapping(self):
        from huggingface_hub.errors import LocalEntryNotFoundError
        cases = [
            (httpx.ConnectError("no route"), mm.OfflineError),
            (httpx.ReadTimeout("slow"), mm.OfflineError),
            (LocalEntryNotFoundError("offline"), mm.OfflineError),
            (OSError(errno.ENOSPC, "No space left on device"), mm.DiskFullError),
            (ValueError("boom"), mm.DownloadError),
        ]
        for exc, expected in cases:
            with self.subTest(exc=type(exc).__name__):
                fake, _ = _fake_hf(self.cache, fail_on=("config.json", exc))
                with mock.patch.object(mm, "_hub_reachable", return_value=True),                         self.assertRaises(expected) as ctx:
                    self.run_download(fake)
                self.assertTrue(str(ctx.exception))

    def test_unrecognised_error_while_unreachable_is_offline(self):
        # huggingface_hub's retry surfaces a dropped connection like this.
        exc = RuntimeError("Cannot send a request, as the client has been closed.")
        fake, _ = _fake_hf(self.cache, fail_on=("config.json", exc))
        with mock.patch("httpx.head", side_effect=httpx.ConnectError("down")),                 self.assertRaises(mm.OfflineError):
            self.run_download(fake)

    def test_xet_disabled_only_while_downloading(self):
        import huggingface_hub.constants as constants
        before = constants.HF_HUB_DISABLE_XET
        seen = []
        fake, _ = _fake_hf(self.cache, before=lambda *a: seen.append(constants.HF_HUB_DISABLE_XET))
        self.run_download(fake)
        self.assertTrue(all(seen))
        self.assertEqual(constants.HF_HUB_DISABLE_XET, before)

    def test_disk_space_refused_before_downloading(self):
        fake, calls = _fake_hf(self.cache)
        usage = mock.Mock(free=mm.DISK_MARGIN_BYTES)  # less than margin + 20 bytes
        with mock.patch("shutil.disk_usage", return_value=usage):
            with self.assertRaises(mm.DiskFullError) as ctx:
                self.run_download(fake)
        self.assertIn("GB needed", str(ctx.exception))
        self.assertEqual(calls, [])

    def test_disk_space_checked_on_existing_ancestor(self):
        deep = self.cache / "not" / "yet" / "created"
        with mock.patch("shutil.disk_usage", return_value=mock.Mock(free=10**12)) as du:
            mm.check_disk_space(100, deep)
        self.assertTrue(Path(du.call_args.args[0]).exists())


class OfflineModeTests(_Tmp):
    def setUp(self):
        super().setUp()
        self._env = mock.patch.dict(os.environ, {})
        self._env.start()
        os.environ.pop("HF_HUB_OFFLINE", None)
        import huggingface_hub.constants as constants
        self._constants = mock.patch.object(constants, "HF_HUB_OFFLINE", False)
        self._constants.start()

    def tearDown(self):
        self._constants.stop()
        self._env.stop()
        super().tearDown()

    def test_not_set_when_something_is_missing(self):
        with mock.patch.object(mm, "required_models", return_value=TINY):
            self.assertFalse(mm.enable_offline_if_installed(self.cache))
        self.assertNotIn("HF_HUB_OFFLINE", os.environ)

    def test_set_when_everything_is_installed(self):
        import huggingface_hub.constants as constants
        _install(self.cache)
        with mock.patch.object(mm, "required_models", return_value=TINY):
            self.assertTrue(mm.enable_offline_if_installed(self.cache))
        self.assertEqual(os.environ["HF_HUB_OFFLINE"], "1")
        self.assertTrue(constants.HF_HUB_OFFLINE)


class LoaderTests(_Tmp):
    def test_parakeet_missing_says_run_setup_and_never_downloads(self):
        from src.transcription.parakeet import ModelLoadError, ParakeetEngine
        engine = ParakeetEngine(quantization=None)
        fake_onnx_asr = mock.Mock()
        with mock.patch.dict(os.environ, {"HF_HUB_CACHE": str(self.cache)}), \
                mock.patch.dict(sys.modules, {"onnx_asr": fake_onnx_asr}):
            with self.assertRaises(ModelLoadError) as ctx:
                engine.load()
        self.assertIn("not installed", str(ctx.exception))
        self.assertIn("setup", str(ctx.exception))
        fake_onnx_asr.load_model.assert_not_called()

    def test_parakeet_loads_exact_variant_from_local_dir(self):
        from src.transcription.parakeet import ParakeetEngine
        with mock.patch.dict(mm._PARAKEET_FILES["int8"], {k: 1 for k in mm._PARAKEET_FILES["int8"]}):
            parakeet = mm.required_models("int8")[0]
            _install(self.cache, [parakeet])
            engine = ParakeetEngine(quantization="int8")
            fake_onnx_asr = mock.Mock()
            with mock.patch.dict(os.environ, {"HF_HUB_CACHE": str(self.cache)}), \
                    mock.patch.dict(sys.modules, {"onnx_asr": fake_onnx_asr}):
                engine.load()
        args, kwargs = fake_onnx_asr.load_model.call_args
        self.assertEqual(args, ("nemo-parakeet-tdt-0.6b-v3", str(mm.snapshot_dir(parakeet, self.cache))))
        self.assertEqual(kwargs["quantization"], "int8")
        self.assertEqual(fake_onnx_asr.load_model.call_count, 1)  # no fallback variant

    def test_embedder_missing_degrades_without_download(self):
        from src.rag.embeddings import EmbeddingService
        with mock.patch.dict(os.environ, {"HF_HUB_CACHE": str(self.cache)}), \
                mock.patch("huggingface_hub.hf_hub_download", side_effect=AssertionError("download")), \
                self.assertLogs("src.rag.embeddings", level="WARNING") as logs:
            svc = EmbeddingService()
            self.assertFalse(svc.is_available)
        self.assertTrue(any("setup" in line for line in logs.output))


if __name__ == "__main__":
    unittest.main()
