import logging
import os
import sys
import threading
import traceback
from logging.handlers import RotatingFileHandler
from pathlib import Path

if __package__ in (None, "") and not getattr(sys, "frozen", False):
    # Run as a script (python path\to\src\main.py): import the project as the
    # `src` package. src/ itself must not stay on sys.path - it would shadow
    # standard-library modules such as `secrets`. A PyInstaller build (BU126)
    # bundles `src` already, and its sys.path[0] is the standard library.
    _project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path[0] = _project_root

from src import model_manager, paths

logger = logging.getLogger(__name__)

LOG_FILENAME = "chronicle.log"
LOG_MAX_BYTES = 5 * 1024 * 1024
LOG_BACKUPS = 3
_LOG_FORMAT = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
# Only processes started from a real console have one; pythonw and a
# --noconsole build start with sys.stdout / sys.stderr set to None (BU125).
_HAS_CONSOLE = sys.stderr is not None


class _LogWriter:
    """A stand-in for a missing sys.stdout / sys.stderr that writes to the log."""

    def __init__(self, name: str, level: int):
        self._logger = logging.getLogger(name)
        self._level = level
        self._buffer = ""
        self.encoding = "utf-8"

    def write(self, text) -> int:
        self._buffer += str(text)
        *lines, self._buffer = self._buffer.split("\n")
        for line in lines:
            if line.strip():
                self._logger.log(self._level, line)
        return len(text)

    def flush(self) -> None:
        if self._buffer.strip():
            self._logger.log(self._level, self._buffer)
        self._buffer = ""

    def isatty(self) -> bool:
        return False


def setup_logging(log_dir=None) -> Path:
    """Log to a rotating file in the data folder, and to the console if any.

    Safe to call again: after the setup wizard picks a data folder the log
    moves there. Returns the log file path.
    """
    log_dir = Path(log_dir) if log_dir is not None else paths.logs_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / LOG_FILENAME

    root = logging.getLogger()
    for handler in [h for h in root.handlers if getattr(h, "_chronicle", False)]:
        root.removeHandler(handler)
        handler.close()
    formatter = logging.Formatter(_LOG_FORMAT)
    handlers = [RotatingFileHandler(log_file, maxBytes=LOG_MAX_BYTES,
                                    backupCount=LOG_BACKUPS, encoding="utf-8")]
    if _HAS_CONSOLE:
        handlers.append(logging.StreamHandler(sys.__stderr__))
    for handler in handlers:
        handler._chronicle = True
        handler.setFormatter(formatter)
        root.addHandler(handler)
    root.setLevel(logging.INFO)

    # Reduce noise from specific modules
    logging.getLogger('src.audio_capture.system_recorder').setLevel(logging.WARNING)
    logging.getLogger('src.audio_capture.core').setLevel(logging.WARNING)

    if sys.stdout is None:
        sys.stdout = _LogWriter("stdout", logging.INFO)
    if sys.stderr is None:
        sys.stderr = _LogWriter("stderr", logging.ERROR)
    return log_file


def open_log_folder() -> None:
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QDesktopServices

    QDesktopServices.openUrl(QUrl.fromLocalFile(str(paths.logs_dir())))


class CrashReporter:
    """Logs uncaught exceptions and shows them; never lets them pass silently.

    Worker-thread errors reach the dialog through a queued Qt signal, so the
    dialog is always built on the GUI thread. Exceptions raised in Qt slots
    also land in ``sys.excepthook``; handling them there (without re-raising)
    keeps the event loop running.
    """

    def __init__(self):
        from PySide6.QtCore import QObject, Signal

        class _Bridge(QObject):
            show = Signal(str)

        self._bridge = _Bridge()
        self._bridge.show.connect(self._show_dialog)
        self._showing = False

    def install(self) -> None:
        sys.excepthook = self.handle
        threading.excepthook = lambda args: self.handle(
            args.exc_type, args.exc_value, args.exc_traceback, args.thread)

    def handle(self, exc_type, exc_value, exc_tb, thread=None) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        where = f" in thread {thread.name}" if thread is not None else ""
        logger.critical("Uncaught exception%s", where,
                        exc_info=(exc_type, exc_value, exc_tb))
        summary = "".join(traceback.format_exception_only(exc_type, exc_value)).strip()
        try:
            self._bridge.show.emit(summary)
        except Exception:  # noqa: BLE001 - reporting must never raise
            logger.exception("Could not show the error dialog")

    def _show_dialog(self, summary: str) -> None:
        from PySide6.QtWidgets import QApplication, QMessageBox

        if self._showing or QApplication.instance() is None:
            return  # one dialog at a time; the rest are in the log
        self._showing = True
        try:
            box = QMessageBox(QMessageBox.Critical, "Chronicle - Unexpected error",
                              f"Something went wrong:\n\n{summary[:500]}\n\n"
                              f"The details are in the log folder:\n{paths.logs_dir()}")
            open_button = box.addButton("Open log folder", QMessageBox.ActionRole)
            box.addButton(QMessageBox.Close)
            box.exec()
            if box.clickedButton() is open_button:
                open_log_folder()
        finally:
            self._showing = False


def instance_server_name() -> str:
    """Per-user name of the single-instance local socket."""
    user = os.environ.get("USERNAME") or os.environ.get("USER") or "user"
    return f"{paths.APP_NAME}-{user}"


# Held for the life of the process so the installer and uninstaller can tell
# Chronicle is running (Inno Setup AppMutex in packaging/chronicle.iss, BU127).
# The Global\ copy is visible from every Windows session.
APP_MUTEX = f"{paths.APP_NAME}-AppRunning"
_mutex_handles = []


def hold_app_mutex() -> None:
    if sys.platform != "win32":
        return
    import ctypes

    create = ctypes.windll.kernel32.CreateMutexW
    create.restype = ctypes.c_void_p
    for name in (APP_MUTEX, "Global\\" + APP_MUTEX):
        handle = create(None, False, name)
        if handle:
            _mutex_handles.append(handle)


def notify_running_instance(name: str, timeout_ms: int = 500) -> bool:
    """Ask a running instance to come to the front. True if one answered."""
    from PySide6.QtNetwork import QLocalSocket

    socket = QLocalSocket()
    socket.connectToServer(name)
    if not socket.waitForConnected(timeout_ms):
        return False
    socket.write(b"activate")
    socket.flush()
    socket.waitForBytesWritten(timeout_ms)
    socket.disconnectFromServer()
    return True


def start_instance_server(name: str, on_activate):
    """Listen for later launches; call ``on_activate`` when one signals."""
    from PySide6.QtNetwork import QLocalServer

    server = QLocalServer()
    if not server.listen(name):
        # A stale endpoint left by a crash (non-Windows); clear it and retry.
        QLocalServer.removeServer(name)
        if not server.listen(name):
            logger.warning("Single-instance server unavailable: %s", server.errorString())
            return server

    def _on_connection():
        while server.hasPendingConnections():
            socket = server.nextPendingConnection()

            def _read(socket=socket):
                if b"activate" in bytes(socket.readAll()):
                    on_activate()
            socket.readyRead.connect(_read)
            socket.disconnected.connect(socket.deleteLater)

    server.newConnection.connect(_on_connection)
    return server


def _bring_to_front(app) -> None:
    from PySide6.QtCore import Qt
    from src.app.window import MainWindow

    windows = [w for w in app.topLevelWidgets() if w.isVisible()]
    if not windows:
        return
    target = next((w for w in windows if isinstance(w, MainWindow)), windows[0])
    target.setWindowState(target.windowState() & ~Qt.WindowMinimized)
    target.show()
    target.raise_()
    target.activateWindow()


def _configure_models() -> None:
    """Point the model cache at the data folder and go offline once installed.

    Must run before anything imports huggingface_hub or onnx_asr: both read
    HF_HOME / HF_HUB_OFFLINE once, at import.
    """
    from src.app.setup_wizard import apply_models_cache

    cache = apply_models_cache()
    if model_manager.enable_offline_if_installed(cache):
        logger.info("Models installed in %s; Hugging Face offline mode on", cache)
    else:
        logger.warning(
            "Models missing in %s - transcription and RAG routing will not work. %s",
            cache, model_manager.SETUP_HINT,
        )


def activate_saved_theme() -> str:
    """Apply the UI theme saved in preferences (BU129).

    Runs before any widget module is imported: their colours are built from
    the active theme at import time.
    """
    import json
    from src.app import theme

    prefs = {}
    try:
        path = paths.preferences_path()
        if path.is_file():
            with path.open("r", encoding="utf-8") as f:
                prefs = json.load(f)
    except Exception as exc:  # noqa: BLE001 - a bad file must not stop the app
        logger.warning("Could not read the UI theme preference: %s", exc)
    return theme.activate(theme.read_preference(prefs))


def relaunch() -> bool:
    """Start a new Chronicle process with the same arguments (BU129)."""
    from PySide6.QtCore import QProcess

    if paths.is_frozen():
        program, args = sys.executable, sys.argv[1:]
    else:
        program, args = sys.executable, [os.path.abspath(sys.argv[0])] + sys.argv[1:]
    result = QProcess.startDetached(program, args, os.getcwd())
    started = result[0] if isinstance(result, tuple) else bool(result)
    if not started:
        logger.error("Could not restart Chronicle (%s %s)", program, args)
    return started


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    setup_logging()
    from PySide6.QtWidgets import QApplication
    activate_saved_theme()
    from src.app.pixel_widgets import install_pixel_window_chrome
    from src.app import setup_wizard

    app = QApplication.instance() or QApplication([])
    # One instance per user (BU125): a second launch fronts the first and exits.
    server_name = instance_server_name()
    if notify_running_instance(server_name):
        logger.info("Chronicle is already running; asked it to come to the front")
        return 0
    instance_server = start_instance_server(server_name, lambda: _bring_to_front(app))
    hold_app_mutex()
    CrashReporter().install()

    install_pixel_window_chrome(app)
    # The gate (BU124): nothing past this point runs until setup is complete.
    if not setup_wizard.run_if_needed(force='--setup' in argv):
        logger.info("Setup closed before it finished; exiting")
        return 0
    setup_logging()  # the wizard may have moved the data folder
    _configure_models()

    from src.app.window import MainWindow
    window = MainWindow()
    window.show()
    code = app.exec()
    instance_server.close()
    if window.restart_requested:
        # The instance server is closed, so the new process becomes the
        # running instance instead of handing off to this one.
        relaunch()
    return code


if __name__ == '__main__':
    sys.exit(main())
