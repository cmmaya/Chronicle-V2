"""BU125 - windowed-app hardening: log file, crash dialog, single instance."""
import logging
import os
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QElapsedTimer, QProcess
from PySide6.QtWidgets import QApplication

from src import main as app_main

_app = QApplication.instance() or QApplication([])


class LogWriterTest(unittest.TestCase):
    def test_accepts_writes_and_forwards_lines_to_the_log(self):
        writer = app_main._LogWriter("stdout", logging.INFO)
        with self.assertLogs("stdout", level="INFO") as logs:
            self.assertEqual(writer.write("hello\npart"), len("hello\npart"))
            writer.write("ial\n")
            writer.flush()
        self.assertEqual([r.getMessage() for r in logs.records], ["hello", "partial"])
        self.assertFalse(writer.isatty())

    def test_setup_logging_replaces_missing_streams(self):
        root = logging.getLogger()
        before = list(root.handlers)
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(sys, "stdout", None), \
                mock.patch.object(sys, "stderr", None):
            log_file = app_main.setup_logging(tmp)
            try:
                self.assertIsInstance(sys.stdout, app_main._LogWriter)
                self.assertIsInstance(sys.stderr, app_main._LogWriter)
                print("printed without a console")
                sys.stdout.flush()
                for h in root.handlers:
                    h.flush()
                self.assertIn("printed without a console",
                              log_file.read_text(encoding="utf-8"))
            finally:
                for h in [h for h in root.handlers if h not in before]:
                    root.removeHandler(h)
                    h.close()


class CrashReporterTest(unittest.TestCase):
    def test_excepthook_logs_and_does_not_reraise(self):
        # Patched on the class before construction: __init__ connects the
        # signal to the bound method, so patching the instance is too late
        # and the real (modal) dialog would block the test run.
        with mock.patch.object(app_main.CrashReporter, "_show_dialog") as show, \
                self.assertLogs(app_main.logger, level="CRITICAL") as logs:
            reporter = app_main.CrashReporter()
            try:
                raise ValueError("boom")
            except ValueError:
                reporter.handle(*sys.exc_info())  # must return normally
        self.assertIn("Uncaught exception", logs.output[0])
        self.assertIn("ValueError: boom", logs.output[0])
        show.assert_called_once_with("ValueError: boom")


class SingleInstanceTest(unittest.TestCase):
    def test_second_instance_signals_the_first(self):
        name = f"chronicle-test-{uuid.uuid4().hex}"
        activated = []
        server = app_main.start_instance_server(name, lambda: activated.append(True))
        # The second launch runs in its own process, as it does for real: a
        # same-thread client blocks in its waits and the pipe never delivers.
        client = QProcess()
        client.setWorkingDirectory(str(Path(__file__).resolve().parent.parent))
        client.start(sys.executable, ["-c", (
            "from PySide6.QtCore import QCoreApplication; app = QCoreApplication([]); "
            "from src import main; "
            f"print(main.notify_running_instance({name!r}, timeout_ms=3000))")])
        try:
            self.assertTrue(server.isListening())
            timer = QElapsedTimer()
            timer.start()
            while not activated and timer.elapsed() < 15000:
                QCoreApplication.processEvents()
            client.waitForFinished(5000)
            self.assertEqual(bytes(client.readAllStandardOutput()).strip(), b"True")
            self.assertEqual(activated, [True])
        finally:
            client.kill()
            server.close()

    def test_no_running_instance(self):
        self.assertFalse(app_main.notify_running_instance(
            f"chronicle-test-{uuid.uuid4().hex}", timeout_ms=100))


if __name__ == "__main__":
    unittest.main()
