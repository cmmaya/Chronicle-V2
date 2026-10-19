"""BU124 - first-run setup wizard before the main window."""
import json
import os
import tempfile
import threading
import unittest
from collections import namedtuple
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QDialog

from src import model_manager as mm
from src import paths
from src.app import setup_wizard as sw

_app = QApplication.instance() or QApplication([])

Usage = namedtuple("Usage", "total used free")


def _status(**overrides):
    values = dict(data_dir_ok=True, has_api_key=True, models_installed=True,
                  location_done=True, api_key_done=True)
    values.update(overrides)
    return sw.SetupStatus(**values)


class NeededPagesTests(unittest.TestCase):
    def test_complete_setup_needs_nothing(self):
        self.assertEqual(sw.needed_pages(_status()), [])

    def test_set_up_source_checkout_without_state_file_needs_nothing(self):
        status = _status(location_done=False, api_key_done=False)
        self.assertEqual(sw.needed_pages(status), [])

    def test_first_run(self):
        status = sw.SetupStatus(True, False, False, False, False)
        self.assertEqual(sw.needed_pages(status), [sw.LOCATION, sw.API_KEY, sw.DOWNLOAD])

    def test_models_deleted_reopens_only_download(self):
        self.assertEqual(sw.needed_pages(_status(models_installed=False)), [sw.DOWNLOAD])

    def test_closed_mid_download_resumes_on_download(self):
        status = _status(has_api_key=False, models_installed=False)
        self.assertEqual(sw.needed_pages(status), [sw.DOWNLOAD])

    def test_skipped_key_does_not_block(self):
        self.assertEqual(sw.needed_pages(_status(has_api_key=False)), [])

    def test_key_missing_and_never_asked(self):
        status = _status(has_api_key=False, api_key_done=False)
        self.assertEqual(sw.needed_pages(status), [sw.API_KEY])

    def test_disconnected_data_drive(self):
        status = _status(data_dir_ok=False, models_installed=False)
        self.assertEqual(sw.needed_pages(status), [sw.LOCATION, sw.DOWNLOAD])

    def test_force_shows_every_page(self):
        self.assertEqual(sw.needed_pages(_status(), force=True), sw.ALL_PAGES)


class RunIfNeededTests(unittest.TestCase):
    def test_no_wizard_when_complete(self):
        with mock.patch.object(sw, "check_setup", return_value=_status()), \
                mock.patch.object(sw, "SetupWizard") as wizard:
            self.assertTrue(sw.run_if_needed())
        wizard.assert_not_called()

    def test_wizard_result_decides(self):
        for result, expected in ((QDialog.Accepted, True), (QDialog.Rejected, False)):
            with self.subTest(result=result), \
                    mock.patch.object(sw, "check_setup", return_value=_status(models_installed=False)), \
                    mock.patch.object(sw, "SetupWizard") as wizard:
                wizard.return_value.exec.return_value = result
                self.assertEqual(sw.run_if_needed(), expected)
                wizard.assert_called_once_with([sw.DOWNLOAD])


class MainGateTests(unittest.TestCase):
    def test_main_window_never_built_when_setup_closed(self):
        import src.main as main_module
        with mock.patch.object(sw, "run_if_needed", return_value=False) as gate, \
                mock.patch.object(main_module, "_configure_models") as configure, \
                mock.patch.object(main_module, "notify_running_instance", return_value=False), \
                mock.patch.object(main_module, "start_instance_server"), \
                mock.patch.object(main_module, "hold_app_mutex"), \
                mock.patch.object(main_module.CrashReporter, "install"), \
                mock.patch("src.app.pixel_widgets.install_pixel_window_chrome"):
            self.assertEqual(main_module.main(["--setup"]), 0)
        gate.assert_called_once_with(force=True)
        configure.assert_not_called()


class _TmpData(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.data = self.tmp / "data"
        self._env = mock.patch.dict(os.environ, {
            "APPDATA": str(self.tmp / "Roaming"),
            paths.DATA_DIR_ENV: str(self.data),
        })
        self._env.start()

    def tearDown(self):
        self._env.stop()
        self._tmp.cleanup()


class LocationTests(_TmpData):
    def test_writable_folder_accepted(self):
        self.assertIsNone(sw.location_problem(self.tmp / "new" / "folder", 0))
        self.assertTrue((self.tmp / "new" / "folder").is_dir())

    def test_unwritable_folder_rejected(self):
        blocker = self.tmp / "file.txt"
        blocker.write_text("x")
        problem = sw.location_problem(blocker / "sub", 0)
        self.assertIn("cannot write", problem)

    def test_too_small_folder_rejected(self):
        with mock.patch.object(sw.shutil, "disk_usage", return_value=Usage(10, 5, 5 * 10**9)):
            problem = sw.location_problem(self.tmp, 8 * 10**9)
        self.assertIn("Not enough free space", problem)
        self.assertIn("8.0 GB", problem)

    def test_required_bytes_uses_the_folders_model_cache(self):
        with mock.patch.object(sw, "models_cache", return_value=self.tmp / "hub") as cache, \
                mock.patch.object(sw.model_manager, "bytes_to_download", return_value=1000):
            self.assertEqual(sw.required_bytes(self.tmp), 1000 + mm.DISK_MARGIN_BYTES)
        cache.assert_called_once_with(self.tmp)

    def test_page_blocks_next_on_problem(self):
        page = sw.LocationPage()
        with mock.patch.object(sw, "required_bytes", return_value=10**15):
            page.set_folder(self.tmp)
        self.assertFalse(page.isComplete())
        with mock.patch.object(sw, "required_bytes", return_value=0):
            page.set_folder(self.tmp)
        self.assertTrue(page.isComplete())

    def test_page_saves_pointer_and_state(self):
        os.environ.pop(paths.DATA_DIR_ENV)
        chosen = self.tmp / "D-drive" / "Chronicle"
        page = sw.LocationPage()
        with mock.patch.object(sw, "required_bytes", return_value=0):
            page.set_folder(chosen)
        self.assertTrue(page.validatePage())
        self.assertEqual(paths.resolved_data_dir(), chosen)
        state = json.loads((chosen / "setup_state.json").read_text(encoding="utf-8"))
        self.assertEqual(state, {sw.LOCATION: True})


class ModelsCacheTests(_TmpData):
    def test_frozen_uses_data_folder(self):
        with mock.patch.object(paths, "is_frozen", return_value=True):
            self.assertEqual(sw.models_cache(), self.data / "models" / "hub")
            self.assertEqual(sw.models_cache(self.tmp / "x"), self.tmp / "x" / "models" / "hub")

    def test_source_keeps_installed_default_cache(self):
        with mock.patch.object(paths, "is_frozen", return_value=False), \
                mock.patch.object(sw, "_USER_HF_HOME", None), \
                mock.patch.object(sw.model_manager, "is_installed", return_value=True):
            self.assertEqual(sw.models_cache(), sw._ORIGINAL_CACHE)

    def test_source_without_models_uses_data_folder(self):
        with mock.patch.object(paths, "is_frozen", return_value=False), \
                mock.patch.object(sw, "_USER_HF_HOME", None), \
                mock.patch.object(sw.model_manager, "is_installed", return_value=False):
            self.assertEqual(sw.models_cache(), self.data / "models" / "hub")

    def test_apply_sets_hf_home(self):
        with mock.patch.dict(os.environ), \
                mock.patch.object(paths, "is_frozen", return_value=True):
            cache = sw.apply_models_cache()
            self.assertEqual(os.environ["HF_HOME"], str(self.data / "models"))
        self.assertEqual(cache, self.data / "models" / "hub")


class CheckSetupTests(_TmpData):
    def test_reads_state_and_key(self):
        paths.setup_state_path().write_text(json.dumps({sw.API_KEY: True}), encoding="utf-8")
        with mock.patch.object(sw.secrets, "get_api_key", return_value=None), \
                mock.patch.object(sw, "models_cache", return_value=self.tmp / "hub"), \
                mock.patch.object(sw.model_manager, "is_installed", return_value=True):
            status = sw.check_setup()
        self.assertEqual(status, sw.SetupStatus(True, False, True, False, True))

    def test_unusable_data_folder(self):
        blocker = self.tmp / "file.txt"
        blocker.write_text("x")
        os.environ[paths.DATA_DIR_ENV] = str(blocker / "data")
        with mock.patch.object(sw.secrets, "get_api_key", return_value="k"):
            status = sw.check_setup()
        self.assertFalse(status.data_dir_ok)
        self.assertFalse(status.models_installed)


class ApiKeyPageTests(_TmpData):
    def test_next_only_after_success_or_skip(self):
        page = sw.ApiKeyPage()
        self.assertFalse(page.isComplete())
        page.on_result(False, "rejected")
        self.assertFalse(page.isComplete())
        page.on_result(True, "The API key works.")
        self.assertTrue(page.isComplete())
        page.key_edit.setText("changed")
        self.assertFalse(page.isComplete())
        page.skip()
        self.assertTrue(page.isComplete())
        self.assertIn("Settings", page.status_label.text())

    def test_saves_tested_key(self):
        page = sw.ApiKeyPage()
        page.key_edit.setText("sk-test")
        page.on_result(True, "ok")
        with mock.patch.object(sw.secrets, "set_api_key") as save:
            self.assertTrue(page.validatePage())
        save.assert_called_once_with("sk-test")
        self.assertTrue(sw._read_state()[sw.API_KEY])

    def test_skip_saves_nothing(self):
        page = sw.ApiKeyPage()
        page.skip()
        with mock.patch.object(sw.secrets, "set_api_key") as save:
            self.assertTrue(page.validatePage())
        save.assert_not_called()


class DownloadPageTests(_TmpData):
    def setUp(self):
        super().setUp()
        self.cache = self.tmp / "hub"
        self._patches = [
            mock.patch.object(sw, "apply_models_cache", return_value=self.cache),
            mock.patch.object(sw.model_manager, "is_installed", return_value=False),
        ]
        for p in self._patches:
            p.start()
        self.page = sw.DownloadPage()

    def tearDown(self):
        for p in reversed(self._patches):
            p.stop()
        super().tearDown()

    def _run(self, behaviour):
        with mock.patch.object(sw.model_manager, "download", side_effect=behaviour):
            self.page.initializePage()
            self.page._worker.wait(5000)
        _app.processEvents()

    def test_error_offers_retry(self):
        self._run(mm.OfflineError("Could not reach Hugging Face."))
        self.assertFalse(self.page.isComplete())
        self.assertFalse(self.page.retry_button.isHidden())
        self.assertIn("Could not reach Hugging Face", self.page.status_label.text())

    def test_cancel_keeps_page_open_with_retry(self):
        def download(progress_cb, cancel_event, cache_dir):
            progress_cb(mm.PARAKEET, 5, 10)
            self.assertTrue(cancel_event.wait(5))
            raise mm.DownloadCancelled("Download cancelled.")

        with mock.patch.object(sw.model_manager, "download", side_effect=download):
            self.page.initializePage()
            self.page.cancel()
            self.page._worker.wait(5000)
        _app.processEvents()
        self.assertFalse(self.page.isComplete())
        self.assertIn("Paused", self.page.status_label.text())
        self.assertFalse(self.page.retry_button.isHidden())
        self.assertEqual(self.page._bars[mm.PARAKEET].value(), 500)

    def test_success_completes(self):
        def download(progress_cb, cancel_event, cache_dir):
            self.assertEqual(cache_dir, self.cache)
            sw.model_manager.is_installed.return_value = True

        self._run(download)
        self.assertTrue(self.page.isComplete())

    def test_already_installed_skips_download(self):
        sw.model_manager.is_installed.return_value = True
        with mock.patch.object(sw.model_manager, "download") as download:
            self.page.initializePage()
        download.assert_not_called()
        self.assertTrue(self.page.isComplete())

    def test_shutdown_cancels_running_download(self):
        started = threading.Event()

        def download(progress_cb, cancel_event, cache_dir):
            started.set()
            cancel_event.wait(5)
            raise mm.DownloadCancelled("Download cancelled.")

        with mock.patch.object(sw.model_manager, "download", side_effect=download):
            self.page.initializePage()
            self.assertTrue(started.wait(5))
            self.page.shutdown()
        self.assertTrue(self.page._cancel.is_set())
        self.assertFalse(self.page._worker.isRunning())


class WizardTests(_TmpData):
    def test_first_run_is_framed(self):
        with mock.patch.object(sw.model_manager, "bytes_to_download", return_value=0):
            wizard = sw.SetupWizard([sw.LOCATION, sw.API_KEY, sw.DOWNLOAD])
        self.assertEqual(len(wizard.pageIds()), 5)

    def test_repair_shows_only_needed_page(self):
        wizard = sw.SetupWizard([sw.DOWNLOAD])
        self.assertEqual(len(wizard.pageIds()), 1)


if __name__ == "__main__":
    unittest.main()
