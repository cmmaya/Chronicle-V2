"""BU128 - the Settings pop-up replaces the hidden menu bar."""
import os
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMainWindow, QMenu

from src import secrets
from src.app import settings_dialog as sd
from src.app.window import MainWindow

_app = QApplication.instance() or QApplication([])


class SettingsHarness(MainWindow):
    """A MainWindow with only its settings state built (see detached_harness)."""

    def __init__(self):
        QMainWindow.__init__(self)  # deliberately not MainWindow.__init__
        self.session_manager = None
        self._vad_threshold = 30
        self._vad_aggressiveness = 2
        self.status = []
        self.saved = []
        self._create_settings_actions()

    def _on_status_update(self, message, is_error=False):
        self.status.append(message)

    def _save_preferences(self):
        self.saved.append("save")

    def _update_preferences(self, updates):
        self.saved.append(updates)

    def _on_reindex_all_clicked(self):
        self.status.append("reindex")


def _no_key():
    return mock.patch.multiple(
        secrets, key_source=mock.Mock(return_value=None),
        get_api_key=mock.Mock(return_value=None),
        has_saved_key=mock.Mock(return_value=False))


class SettingsPopupTests(unittest.TestCase):
    def setUp(self):
        self.win = SettingsHarness()
        with _no_key():
            self.dialog = sd.SettingsDialog(self.win)

    def tearDown(self):
        self.dialog.deleteLater()
        self.win.deleteLater()

    def test_no_menu_bar(self):
        self.assertIsNone(self.win.menuWidget())
        self.assertEqual(self.win.findChildren(QMenu), [])

    def test_opens_on_general_with_all_pages(self):
        self.assertEqual(self.dialog.current_page(), "General")
        self.assertEqual(self.dialog.stack.count(), len(sd.SettingsDialog.PAGES))
        self.dialog.select_page("Model")
        self.assertEqual(self.dialog.current_page(), "Model")
        model_index = sd.SettingsDialog.PAGES.index("Model")
        self.assertTrue(self.dialog.nav_buttons[model_index].text().startswith("▶"))

    def test_toggles_reflect_and_write_actions(self):
        self.win.auto_summary_action.setChecked(False)
        with _no_key():
            dialog = sd.SettingsDialog(self.win)
        self.assertFalse(dialog.summary_toggle.isChecked())
        self.assertTrue(dialog.live_toggle.isChecked())

        dialog.live_toggle.toggle()
        self.assertFalse(self.win.live_transcription_action.isChecked())
        self.assertIn("save", self.win.saved)

        dialog.logs_toggle.toggle()
        self.assertFalse(self.win.show_app_logs_action.isChecked())
        self.assertIn({"show_app_logs": False}, self.win.saved)
        dialog.deleteLater()

    def test_vad_applies_after_debounce_and_on_close(self):
        self.dialog.threshold_slider.setValue(55)
        self.assertEqual(self.dialog.threshold_chip.text(), "55%")
        self.assertEqual(self.win._vad_threshold, 30)  # still debouncing
        self.dialog.agg_slider.setValue(3)
        self.dialog.done(0)
        self.assertEqual((self.win._vad_threshold, self.win._vad_aggressiveness), (55, 3))
        self.assertEqual(len([m for m in self.win.status if m.startswith("VAD")]), 1)

    def test_model_change_persists(self):
        combo = self.dialog.model_combo
        if combo.count() < 2:
            self.skipTest("needs two allowed models")
        target = combo.itemData((combo.currentIndex() + 1) % combo.count())
        with mock.patch("src.app.window.set_selected_model", return_value=True) as setter:
            combo.setCurrentIndex(combo.findData(target))
        setter.assert_called_once_with(target)
        self.assertIn({"selected_model": target}, self.win.saved)

    def test_reindex_button_follows_action(self):
        self.win._reindex_action.setEnabled(False)
        self.assertFalse(self.dialog.reindex_button.isEnabled())
        self.win._reindex_action.setEnabled(True)
        self.assertTrue(self.dialog.reindex_button.isEnabled())
        self.dialog.reindex_button.click()
        self.assertIn("reindex", self.win.status)

    def test_open_is_single_instance(self):
        with _no_key():
            self.win._open_settings_dialog()
            first = self.win._settings_dialog
            self.win._open_settings_dialog()
        self.assertIs(self.win._settings_dialog, first)
        first.reject()
        self.assertIsNone(self.win._settings_dialog)


class PixelToggleTests(unittest.TestCase):
    def test_click_and_space_flip(self):
        toggle = sd.PixelToggle(False)
        seen = []
        toggle.toggled.connect(seen.append)
        QTest.mouseClick(toggle, Qt.LeftButton, pos=QPoint(10, 10))
        QTest.keyClick(toggle, Qt.Key_Space)
        self.assertEqual(seen, [True, False])

    def test_set_checked_is_silent(self):
        toggle = sd.PixelToggle(False)
        seen = []
        toggle.toggled.connect(seen.append)
        toggle.setChecked(True)
        self.assertTrue(toggle.isChecked())
        self.assertEqual(seen, [])


if __name__ == "__main__":
    unittest.main()
