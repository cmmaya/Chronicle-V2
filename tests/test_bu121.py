"""BU121 - app paths: install folder separate from user data."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from src import paths

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class _Env(unittest.TestCase):
    """A temp APPDATA / LOCALAPPDATA and no CHRONICLE_DATA_DIR by default."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.roaming = self.tmp / "Roaming"
        env = {"APPDATA": str(self.roaming)}
        self._env = mock.patch.dict(os.environ, env)
        self._env.start()
        os.environ.pop(paths.DATA_DIR_ENV, None)

    def tearDown(self):
        self._env.stop()
        self._tmp.cleanup()

    def write_pointer(self, content):
        pointer = paths.pointer_file()
        pointer.parent.mkdir(parents=True, exist_ok=True)
        pointer.write_text(content, encoding="utf-8")


class ResolutionOrderTests(_Env):
    def test_environment_variable_wins_over_pointer(self):
        env_dir = self.tmp / "from-env"
        self.write_pointer(json.dumps({"data_dir": str(self.tmp / "from-pointer")}))
        with mock.patch.dict(os.environ, {paths.DATA_DIR_ENV: str(env_dir)}):
            self.assertEqual(paths.data_dir(), env_dir)

    def test_pointer_wins_over_defaults(self):
        pointed = self.tmp / "from-pointer"
        self.write_pointer(json.dumps({"data_dir": str(pointed)}))
        self.assertEqual(paths.data_dir(), pointed)
        with mock.patch.object(sys, "frozen", True, create=True):
            self.assertEqual(paths.data_dir(), pointed)

    def test_dev_default_is_project_root(self):
        self.assertFalse(paths.is_frozen())
        self.assertEqual(paths.data_dir(), PROJECT_ROOT)

    def test_frozen_default_is_local_appdata_chronicle(self):
        local = self.tmp / "Local"
        with mock.patch.object(sys, "frozen", True, create=True), \
                mock.patch.object(paths, "_frozen_default", return_value=local / "Chronicle"):
            self.assertEqual(paths.data_dir(), local / "Chronicle")

    def test_frozen_default_uses_generic_data_location(self):
        from PySide6.QtCore import QStandardPaths
        base = QStandardPaths.writableLocation(QStandardPaths.GenericDataLocation)
        self.assertEqual(paths._frozen_default(), Path(base) / "Chronicle")

    def test_malformed_pointer_falls_back_without_raising(self):
        for content in ("{not json", json.dumps({"data_dir": 5}), json.dumps({}), json.dumps([1])):
            with self.subTest(content=content):
                self.write_pointer(content)
                with self.assertLogs("src.paths", level="WARNING"):
                    self.assertEqual(paths.data_dir(), PROJECT_ROOT)


class DerivedPathTests(_Env):
    def test_derived_paths_live_under_data_dir_and_are_created(self):
        root = self.tmp / "data"
        with mock.patch.dict(os.environ, {paths.DATA_DIR_ENV: str(root)}):
            self.assertFalse(root.exists())
            self.assertEqual(paths.db_path(), root / "chronicle.db")
            self.assertEqual(paths.preferences_path(), root / "preferences.json")
            for fn, name in ((paths.sessions_dir, "sessions"), (paths.models_dir, "models"),
                             (paths.logs_dir, "logs")):
                self.assertEqual(fn(), root / name)
                self.assertTrue((root / name).is_dir())

    def test_nothing_depends_on_working_directory(self):
        root = self.tmp / "data"
        with mock.patch.dict(os.environ, {paths.DATA_DIR_ENV: str(root)}):
            before = paths.db_path()
            cwd = os.getcwd()
            try:
                os.chdir(self.tmp)
                self.assertEqual(paths.db_path(), before)
                self.assertEqual(paths.resource_dir(), PROJECT_ROOT)
            finally:
                os.chdir(cwd)

    def test_database_and_session_manager_default_to_data_dir(self):
        from src.storage.database import Database
        root = self.tmp / "data"
        with mock.patch.dict(os.environ, {paths.DATA_DIR_ENV: str(root)}):
            self.assertEqual(Database().db_path, str(root / "chronicle.db"))
            self.assertEqual(Database(":memory:").db_path, ":memory:")


class ResourceDirTests(unittest.TestCase):
    def test_icons_found_from_source(self):
        from src.app.pixel_theme import asset_path
        self.assertTrue(os.path.isfile(asset_path("icon_mic_on.svg")))
        self.assertEqual(paths.resource_dir(), PROJECT_ROOT)

    def test_icons_found_in_frozen_bundle(self):
        with tempfile.TemporaryDirectory() as bundle:
            icon = Path(bundle) / "assets" / "pixel" / "icon_mic_on.svg"
            icon.parent.mkdir(parents=True)
            icon.write_text("<svg/>")
            with mock.patch.object(sys, "frozen", True, create=True), \
                    mock.patch.object(sys, "_MEIPASS", bundle, create=True):
                from src.app.pixel_theme import asset_path
                self.assertEqual(paths.resource_dir(), Path(bundle))
                self.assertEqual(Path(asset_path("icon_mic_on.svg")), icon)


if __name__ == "__main__":
    unittest.main()
