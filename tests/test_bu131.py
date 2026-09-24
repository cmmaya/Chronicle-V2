"""BU131 - Calendar page in the Settings pop-up (auth layer mocked)."""
import os
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox

from src.app import settings_dialog as sd
from src.calendar_sync import google_auth as ga
from tests.test_bu128 import SettingsHarness, _no_key

_app = QApplication.instance() or QApplication([])

CLIENT = ga.OAuthClient(
    client_id="1234567890-abcdefghijklmnop.apps.googleusercontent.com",
    client_secret="shh", auth_uri="https://a", token_uri="https://t",
    source=ga.SOURCE_CHRONICLE)


class FakeAuth:
    """Stands in for google_auth's state: what is connected and which clients exist."""

    def __init__(self, connected=False, email=None, source=None, chronicle=True, custom=False):
        self.state = (connected, email, source)
        self.clients = {ga.SOURCE_CHRONICLE: CLIENT if chronicle else None,
                        ga.SOURCE_CUSTOM: CLIENT if custom else None}
        self.disconnect = mock.Mock(side_effect=self._disconnect)

    def _disconnect(self):
        self.state = (False, None, None)

    def patches(self):
        return mock.patch.multiple(
            ga, connection_state=lambda: self.state,
            load_client=lambda source: self.clients[source],
            disconnect=self.disconnect)


def _flow(result=None, error=None, auth=None):
    """A SignInFlow class whose run() returns ``result`` or raises ``error``."""
    class Flow:
        def __init__(self, client):
            self.cancel = mock.Mock()

        def run(self):
            if error is not None:
                raise error
            auth.state = (True, result, ga.SOURCE_CHRONICLE)
            return result
    return Flow


class CalendarPageTests(unittest.TestCase):
    def open(self, auth):
        self.win = SettingsHarness()
        self.auth = auth
        self._patch = auth.patches()
        self._patch.start()
        with _no_key():
            self.dialog = sd.SettingsDialog(self.win)
        self.dialog.select_page("Calendar")

    def tearDown(self):
        if not hasattr(self, "dialog"):
            return
        self.dialog.done(0)
        self._patch.stop()
        self.dialog.deleteLater()
        self.win.deleteLater()

    def finish_thread(self):
        thread = self.dialog._google_thread
        self.assertIsNotNone(thread)
        thread.wait(5000)
        for _ in range(20):
            _app.processEvents()
            if self.dialog._google_thread is None:
                break

    def test_page_sits_between_api_key_and_maintenance(self):
        pages = sd.SettingsDialog.PAGES
        self.assertEqual(pages.index("Calendar"), pages.index("API Key") + 1)
        self.assertEqual(pages.index("Maintenance"), pages.index("Calendar") + 1)

    def test_reflects_connection_state_on_open(self):
        self.open(FakeAuth(True, "me@example.com", ga.SOURCE_CHRONICLE))
        self.assertEqual(self.dialog.current_page(), "Calendar")
        self.assertEqual(self.dialog.google_status_label.text(), "Connected as me@example.com")
        self.assertEqual(self.dialog.google_connect_button.text(), "Disconnect")
        self.assertIn("Primary calendar", self.dialog.google_calendar_label.text())

    def test_not_connected(self):
        self.open(FakeAuth())
        self.assertEqual(self.dialog.google_status_label.text(), "Not connected")
        self.assertEqual(self.dialog.google_connect_button.text(), "Connect")
        self.assertTrue(self.dialog.google_connect_button.isEnabled())

    def test_connect_success_updates_card(self):
        self.open(FakeAuth())
        with mock.patch.object(ga, "SignInFlow", _flow("me@example.com", auth=self.auth)):
            self.dialog.google_connect_button.click()
            self.assertEqual(self.dialog.google_connect_button.text(), "Waiting for browser...")
            self.assertFalse(self.dialog.google_cancel_button.isHidden())
            self.finish_thread()
        self.assertEqual(self.dialog.google_status_label.text(), "Connected as me@example.com")
        self.assertEqual(self.dialog.google_error_label.text(), "")
        self.assertTrue(self.dialog.google_cancel_button.isHidden())
        self.assertIn("Google account connected", self.win.status)

    def test_connect_failure_shows_error(self):
        self.open(FakeAuth())
        error = ga.GoogleAuthError("Calendar permission was not granted.")
        with mock.patch.object(ga, "SignInFlow", _flow(error=error)):
            self.dialog.google_connect_button.click()
            self.finish_thread()
        self.assertEqual(self.dialog.google_status_label.text(), "Not connected")
        self.assertEqual(self.dialog.google_error_label.text(), "Calendar permission was not granted.")
        self.assertEqual(self.dialog.google_connect_button.text(), "Connect")

    def test_disconnect_only_after_confirmation(self):
        self.open(FakeAuth(True, "me@example.com", ga.SOURCE_CHRONICLE))
        with mock.patch.object(sd.QMessageBox, "question", return_value=QMessageBox.No):
            self.dialog.google_connect_button.click()
        self.assertIsNone(self.dialog._google_thread)
        self.auth.disconnect.assert_not_called()

        with mock.patch.object(sd.QMessageBox, "question", return_value=QMessageBox.Yes):
            self.dialog.google_connect_button.click()
            self.finish_thread()
        self.auth.disconnect.assert_called_once()
        self.assertEqual(self.dialog.google_status_label.text(), "Not connected")

    def test_client_choice_disabled_while_connected(self):
        self.open(FakeAuth(True, "me@example.com", ga.SOURCE_CUSTOM, custom=True))
        self.assertFalse(self.dialog.google_source_combo.isEnabled())
        self.assertEqual(self.dialog.google_source_combo.currentData(), ga.SOURCE_CUSTOM)
        self.assertIn("Disconnect first", self.dialog.google_source_hint.text())
        self.assertFalse(self.dialog.google_import_button.isEnabled())

    def test_chronicle_unavailable_defaults_to_custom(self):
        self.open(FakeAuth(chronicle=False))
        combo = self.dialog.google_source_combo
        self.assertEqual(combo.currentData(), ga.SOURCE_CUSTOM)
        self.assertFalse(combo.model().item(0).isEnabled())
        self.assertIn("Not available in this build", self.dialog.google_source_hint.text())
        self.assertFalse(self.dialog.google_connect_button.isEnabled())  # nothing imported yet

    def test_invalid_import_shows_error(self):
        self.open(FakeAuth(chronicle=False))
        bad = mock.Mock(side_effect=ga.GoogleAuthError("This is a \"Web application\" client."))
        with mock.patch.object(sd.QFileDialog, "getOpenFileName", return_value=("x.json", "")), \
                mock.patch.object(ga, "import_custom_client", bad):
            self.dialog.google_import_button.click()
        self.assertIn("Web application", self.dialog.google_error_label.text())
        self.assertEqual(self.dialog.google_client_label.text(), "No client imported.")

    def test_closing_mid_sign_in_cancels_flow(self):
        self.open(FakeAuth())
        flow = mock.Mock()
        flow.run.side_effect = lambda: flow.cancel.called or None
        with mock.patch.object(ga, "SignInFlow", return_value=flow):
            self.dialog.google_connect_button.click()
            self.dialog.done(0)
        flow.cancel.assert_called()
        self.assertIsNone(self.dialog._google_thread)


class OpenSettingsPageTests(unittest.TestCase):
    def test_open_settings_selects_calendar(self):
        win = SettingsHarness()
        with FakeAuth().patches(), _no_key():
            win.open_settings(page="Calendar")
            dialog = win._settings_dialog
            self.assertEqual(dialog.current_page(), "Calendar")
            dialog.select_page("General")
            win.open_settings(page="Calendar")  # already open: switch page
            self.assertEqual(dialog.current_page(), "Calendar")
            dialog.done(0)
        win.deleteLater()


if __name__ == "__main__":
    unittest.main()
