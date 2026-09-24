"""First-run setup wizard shown before the main window (BU124).

``main.py`` calls :func:`run_if_needed` before it imports ``MainWindow``.
Setup is complete when the data folder is writable, an API key is available
(or its step was skipped once), and every model is installed. Anything
missing reopens only the pages that fix it.

This module must not import huggingface_hub or onnx_asr at import time: they
read ``HF_HOME`` once, and :func:`apply_models_cache` has to set it first.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QDialog, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QProgressBar,
    QPushButton, QVBoxLayout, QWizard, QWizardPage,
)

from .. import model_manager, paths, secrets
from ..config import APP_VERSION
from .pixel_theme import app_qss

logger = logging.getLogger(__name__)

LOCATION = "location"
API_KEY = "api_key"
DOWNLOAD = "download"
ALL_PAGES = [LOCATION, API_KEY, DOWNLOAD]

# The cache the process started with: an explicit HF_HOME, or the default
# Hugging Face cache. Captured before this module ever sets HF_HOME.
_USER_HF_HOME = os.environ.get("HF_HOME")
_ORIGINAL_CACHE = model_manager.hub_cache()


# -- where the models go ---------------------------------------------------------

def models_cache(data_folder: Optional[Path] = None) -> Path:
    """The hub cache the app loads models from, for ``data_folder`` (default: the current one).

    Frozen builds always use ``<data>/models``. From source, an explicit
    ``HF_HOME`` or models already in the default cache are kept (BU121 dev
    exception) so a set-up machine never downloads again.
    """
    if not paths.is_frozen() and (_USER_HF_HOME or model_manager.is_installed(_ORIGINAL_CACHE)):
        return _ORIGINAL_CACHE
    root = Path(data_folder) if data_folder else paths.data_dir()
    return root / "models" / "hub"


def apply_models_cache() -> Path:
    """Point ``HF_HOME`` at :func:`models_cache` for this process and return the cache."""
    cache = models_cache()
    if cache != _ORIGINAL_CACHE:
        os.environ["HF_HOME"] = str(cache.parent)
    return cache


def required_bytes(data_folder: Path) -> int:
    """Free space the models still need in ``data_folder`` (0 when installed)."""
    missing = model_manager.bytes_to_download(models_cache(data_folder))
    return missing + model_manager.DISK_MARGIN_BYTES if missing else 0


def location_problem(folder: Path, needed_bytes: int) -> Optional[str]:
    """Why ``folder`` cannot hold Chronicle's data, or None if it can."""
    folder = Path(folder)
    try:
        folder.mkdir(parents=True, exist_ok=True)
        probe = folder / ".chronicle_write_test"
        probe.write_bytes(b"")
        probe.unlink()
    except OSError:
        return "Chronicle cannot write to this folder. Choose another one."
    free = shutil.disk_usage(folder).free
    if free < needed_bytes:
        return (f"Not enough free space: {_gb(needed_bytes)} needed, {_gb(free)} free. "
                "Choose a folder on a larger drive.")
    return None


def _gb(n: int) -> str:
    return f"{n / 1e9:.1f} GB"


# -- which steps are done --------------------------------------------------------

def _read_state() -> Dict[str, bool]:
    try:
        with paths.setup_state_path().open("r", encoding="utf-8") as f:
            state = json.load(f)
        return state if isinstance(state, dict) else {}
    except (OSError, ValueError):
        return {}


def _mark_done(step: str) -> None:
    state = _read_state()
    state[step] = True
    paths.setup_state_path().write_text(json.dumps(state), encoding="utf-8")


@dataclass(frozen=True)
class SetupStatus:
    data_dir_ok: bool
    has_api_key: bool
    models_installed: bool
    location_done: bool
    api_key_done: bool


def check_setup() -> SetupStatus:
    """What is in place right now. Disk and environment only - no network."""
    try:
        data_ok = location_problem(paths.data_dir(), 0) is None
    except OSError:
        data_ok = False
    state = _read_state() if data_ok else {}
    return SetupStatus(
        data_dir_ok=data_ok,
        has_api_key=secrets.get_api_key() is not None,
        models_installed=data_ok and model_manager.is_installed(models_cache()),
        location_done=bool(state.get(LOCATION)),
        api_key_done=bool(state.get(API_KEY)),
    )


def needed_pages(status: SetupStatus, force: bool = False) -> List[str]:
    """The wizard pages to show, in order; empty when the app can start."""
    if force:
        return list(ALL_PAGES)
    key_ok = status.has_api_key or status.api_key_done
    if status.data_dir_ok and status.models_installed and key_ok:
        return []
    pages = []
    if not status.data_dir_ok or not status.location_done:
        pages.append(LOCATION)
    if not key_ok:
        pages.append(API_KEY)
    if not status.models_installed:
        pages.append(DOWNLOAD)
    return pages


def run_if_needed(force: bool = False) -> bool:
    """Show the wizard if anything is missing. False when the user closed it."""
    pages = needed_pages(check_setup(), force)
    if not pages:
        return True
    logger.info("Setup needed: %s", ", ".join(pages))
    return SetupWizard(pages).exec() == QDialog.Accepted


# -- pages ----------------------------------------------------------------------

WIZARD_QSS = """
QWizard, QWizardPage { background: #061946; }
QLabel#SetupNote { color: #FFE9A8; font-size: 13px; }
QLabel#SetupError { color: #FF9A8A; font-size: 13px; }
QLabel#SetupOk { color: #9BE59B; font-size: 13px; }
QLineEdit {
    background: #F6E0A6; color: #061946;
    border: 2px solid #254D9C; border-radius: 7px; padding: 6px;
}
QLineEdit:disabled { background: #B9A878; }
QPushButton {
    color: #FFF0BF; background: #274F9B;
    border: 2px solid #3A67C7; border-radius: 7px; padding: 6px 14px;
}
QPushButton:hover { background: #315DB1; }
QPushButton:disabled { color: #8FA2C9; background: #1B3A78; border-color: #254D9C; }
QProgressBar {
    background: #071D52; color: #FFF0BF; text-align: center;
    border: 2px solid #254D9C; border-radius: 7px; min-height: 22px;
}
QProgressBar::chunk { background: #3A67C7; border-radius: 5px; }
"""


def _label(text: str = "", name: str = "") -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    if name:
        label.setObjectName(name)
    return label


def _set_message(label: QLabel, text: str, kind: str = "SetupNote") -> None:
    label.setObjectName(kind)
    label.setText(text)
    label.style().unpolish(label)
    label.style().polish(label)


class WelcomePage(QWizardPage):
    def __init__(self):
        super().__init__()
        self.setTitle("Welcome")
        try:
            size = model_manager.bytes_to_download(models_cache())
        except OSError:
            size = sum(m.total_bytes for m in model_manager.required_models())
        layout = QVBoxLayout(self)
        layout.addWidget(_label("Data folder, API key, models."))
        if size:
            layout.addWidget(_label(f"One-time download: {_gb(size)}.", "SetupNote"))
        layout.addWidget(_label(
            "Audio is transcribed locally. Chat and summaries go to OpenRouter.", "SetupNote"))
        layout.addStretch(1)


class LocationPage(QWizardPage):
    def __init__(self):
        super().__init__()
        self.setTitle("Data folder")
        self.setCommitPage(True)
        self._problem: Optional[str] = "not checked"

        self.path_edit = QLineEdit()
        self.path_edit.setReadOnly(True)
        self.browse_button = QPushButton("Browse…")
        self.browse_button.clicked.connect(self._browse)
        row = QHBoxLayout()
        row.addWidget(self.path_edit, 1)
        row.addWidget(self.browse_button)

        self.space_label = _label(name="SetupNote")
        self.error_label = _label(name="SetupError")
        layout = QVBoxLayout(self)
        layout.addLayout(row)
        layout.addWidget(self.space_label)
        layout.addWidget(self.error_label)
        if paths.data_dir_from_environment():
            self.browse_button.setEnabled(False)
            layout.addWidget(_label(f"Set by {paths.DATA_DIR_ENV}.", "SetupNote"))
        layout.addStretch(1)

    def initializePage(self):
        self.set_folder(paths.resolved_data_dir())

    def _browse(self):
        chosen = QFileDialog.getExistingDirectory(self, "Choose the data folder", self.path_edit.text())
        if chosen:
            self.set_folder(Path(chosen))

    def set_folder(self, folder: Path) -> None:
        self.path_edit.setText(str(folder))
        needed = 0
        self._problem = location_problem(folder, 0)
        if self._problem is None:
            needed = required_bytes(folder)
            self._problem = location_problem(folder, needed)
        if self._problem is None:
            free = shutil.disk_usage(folder).free
            self.space_label.setText(f"Free: {_gb(free)}   Needed: {_gb(needed)}")
        else:
            self.space_label.setText("")
        self.error_label.setText(self._problem or "")
        self.completeChanged.emit()

    def isComplete(self) -> bool:
        return self._problem is None

    def validatePage(self) -> bool:
        folder = Path(self.path_edit.text())
        try:
            if not paths.data_dir_from_environment():
                paths.save_data_location(folder)
            _mark_done(LOCATION)
        except OSError as exc:
            self.error_label.setText(f"Could not save the data location: {exc}")
            return False
        return True


class _KeyCheckThread(QThread):
    result_signal = Signal(bool, str)

    def __init__(self, key: str):
        super().__init__()
        self._key = key

    def run(self):
        try:
            ok, message = secrets.check_api_key(self._key)
        except Exception as exc:  # noqa: BLE001
            ok, message = False, f"Could not test the key: {secrets.redact(exc, self._key)}"
        self.result_signal.emit(ok, message)


class ApiKeyPage(QWizardPage):
    def __init__(self):
        super().__init__()
        self.setTitle("OpenRouter key")
        self.setCommitPage(True)
        self._ok = False
        self._skipped = False
        self._thread: Optional[_KeyCheckThread] = None

        self.key_edit = QLineEdit()
        self.key_edit.setEchoMode(QLineEdit.Password)
        self.key_edit.setPlaceholderText("sk-or-v1-…")
        self.key_edit.textChanged.connect(self._key_changed)
        self.test_button = QPushButton("Test")
        self.test_button.clicked.connect(self._test)
        self.skip_button = QPushButton("Skip for now")
        self.skip_button.clicked.connect(self.skip)
        row = QHBoxLayout()
        row.addWidget(self.key_edit, 1)
        row.addWidget(self.test_button)

        self.status_label = _label(name="SetupNote")
        layout = QVBoxLayout(self)
        layout.addLayout(row)
        layout.addWidget(self.status_label)
        layout.addWidget(self.skip_button)
        layout.addStretch(1)

    def _key_changed(self):
        self._ok = False
        self.completeChanged.emit()

    def _test(self):
        self.test_button.setEnabled(False)
        _set_message(self.status_label, "Testing…")
        self._thread = _KeyCheckThread(self.key_edit.text())
        self._thread.result_signal.connect(self.on_result)
        self._thread.start()

    def on_result(self, ok: bool, message: str) -> None:
        self.test_button.setEnabled(True)
        self._ok = ok
        _set_message(self.status_label, message, "SetupOk" if ok else "SetupError")
        self.completeChanged.emit()

    def skip(self):
        self._skipped = True
        _set_message(self.status_label,
                     "Skipped. Chat and summaries need a key (Settings > API Key).",
                     "SetupError")
        self.completeChanged.emit()

    def isComplete(self) -> bool:
        return self._ok or self._skipped

    def validatePage(self) -> bool:
        try:
            if self._ok:
                secrets.set_api_key(self.key_edit.text())
            _mark_done(API_KEY)
        except Exception as exc:  # noqa: BLE001 - no credential store backend, etc.
            _set_message(self.status_label,
                         f"Could not save the key: {type(exc).__name__}", "SetupError")
            return False
        return True

    def shutdown(self) -> None:
        if self._thread is not None:
            self._thread.wait()


class _DownloadThread(QThread):
    progress = Signal(str, object, object)  # (model, bytes_done, bytes_total)
    succeeded = Signal()
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, cache: Path, cancel_event: threading.Event):
        super().__init__()
        self._cache = cache
        self._cancel = cancel_event

    def run(self):
        try:
            model_manager.download(self.progress.emit, self._cancel, cache_dir=self._cache)
        except model_manager.DownloadCancelled:
            self.cancelled.emit()
        except model_manager.ModelManagerError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(f"Model download failed: {type(exc).__name__}: {exc}")
        else:
            self.succeeded.emit()


_MODEL_LABELS = {
    model_manager.PARAKEET: "Speech-to-text (Parakeet)",
    model_manager.EMBEDDINGS: "Search embeddings (e5)",
}


class DownloadPage(QWizardPage):
    def __init__(self):
        super().__init__()
        self.setTitle("Models")
        self._cache: Optional[Path] = None
        self._complete = False
        self._worker: Optional[_DownloadThread] = None
        self._cancel: Optional[threading.Event] = None
        self._done: Dict[str, int] = {}
        self._total: Dict[str, int] = {}
        self._bars: Dict[str, QProgressBar] = {}
        self._speed_start = (0.0, 0)
        self._last_speed_update = 0.0

        layout = QVBoxLayout(self)
        for model in model_manager.required_models():
            bar = QProgressBar()
            bar.setRange(0, 1000)
            bar.setFormat(f"{_MODEL_LABELS.get(model.name, model.name)}  %p%")
            self._bars[model.name] = bar
            self._total[model.name] = model.total_bytes
            layout.addWidget(bar)
        self.speed_label = _label(name="SetupNote")
        self.status_label = _label(name="SetupNote")
        layout.addWidget(self.speed_label)
        layout.addWidget(self.status_label)

        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self.cancel)
        self.retry_button = QPushButton("Retry")
        self.retry_button.clicked.connect(self.start)
        self.close_button = QPushButton("Resume later")
        self.close_button.clicked.connect(self._close_later)
        row = QHBoxLayout()
        row.addWidget(self.cancel_button)
        row.addWidget(self.retry_button)
        row.addStretch(1)
        row.addWidget(self.close_button)
        layout.addLayout(row)
        layout.addStretch(1)
        self.retry_button.hide()

    def initializePage(self):
        self._cache = apply_models_cache()
        for st in model_manager.status(self._cache):
            self._set_progress(st.name, self._total[st.name] - st.bytes_missing)
        if model_manager.is_installed(self._cache):
            self.on_succeeded()
        else:
            self.start()

    def start(self):
        self._cancel = threading.Event()
        self._worker = _DownloadThread(self._cache, self._cancel)
        self._worker.progress.connect(self.on_progress)
        self._worker.succeeded.connect(self.on_succeeded)
        self._worker.failed.connect(self.on_failed)
        self._worker.cancelled.connect(self.on_cancelled)
        self._speed_start = (time.monotonic(), sum(self._done.values()))
        self.cancel_button.setEnabled(True)
        self.retry_button.hide()
        self.speed_label.setText("")
        _set_message(self.status_label, "Downloading…")
        self._worker.start()

    def _set_progress(self, name: str, done: int) -> None:
        self._done[name] = done
        total = self._total.get(name) or 1
        self._bars[name].setValue(int(done * 1000 / total))

    def on_progress(self, name: str, done: int, total: int) -> None:
        self._total[name] = total
        self._set_progress(name, done)
        now = time.monotonic()
        started, done0 = self._speed_start
        elapsed = now - started
        if elapsed < 1 or now - self._last_speed_update < 0.5:
            return
        self._last_speed_update = now
        overall = sum(self._done.values())
        rate = (overall - done0) / elapsed
        if rate <= 0:
            return
        left = (sum(self._total.values()) - overall) / rate
        self.speed_label.setText(f"{rate / 1e6:.1f} MB/s  ·  about {_duration(left)} left")

    def on_succeeded(self) -> None:
        self._complete = model_manager.is_installed(self._cache)
        if not self._complete:
            self.on_failed("Some model files are still missing. Retry the download.")
            return
        for name in self._bars:
            self._set_progress(name, self._total[name])
        self.cancel_button.setEnabled(False)
        self.retry_button.hide()
        self.close_button.hide()
        self.speed_label.setText("")
        _set_message(self.status_label, "Installed.", "SetupOk")
        self.completeChanged.emit()

    def on_failed(self, message: str) -> None:
        self.cancel_button.setEnabled(False)
        self.retry_button.show()
        self.speed_label.setText("")
        _set_message(self.status_label, message, "SetupError")

    def on_cancelled(self) -> None:
        self.cancel_button.setEnabled(False)
        self.retry_button.show()
        self.speed_label.setText("")
        _set_message(self.status_label, "Paused.")

    def cancel(self) -> None:
        if self._cancel is not None:
            self._cancel.set()
        self.cancel_button.setEnabled(False)
        _set_message(self.status_label, "Stopping…")

    def _close_later(self) -> None:
        self.wizard().reject()

    def isComplete(self) -> bool:
        return self._complete

    def shutdown(self) -> None:
        """Stop a running download, keeping the partial files for next time."""
        if self._worker is not None and self._worker.isRunning():
            self._cancel.set()
            self._worker.wait()


def _duration(seconds: float) -> str:
    minutes = int(seconds // 60)
    if minutes >= 60:
        return f"{minutes // 60} h {minutes % 60} min"
    if minutes:
        return f"{minutes} min"
    return f"{int(seconds)} s"


class DonePage(QWizardPage):
    def __init__(self):
        super().__init__()
        self.setTitle("All set")


_PAGE_CLASSES = {LOCATION: LocationPage, API_KEY: ApiKeyPage, DOWNLOAD: DownloadPage}


class SetupWizard(QWizard):
    """Only the ``pages`` that are needed; Welcome and Done frame a first run."""

    def __init__(self, pages: List[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Chronicle {APP_VERSION} Setup")
        self.setWizardStyle(QWizard.ClassicStyle)
        # Frameless up front: QWizard's native window already exists by the
        # time PixelWindowChrome polishes it, so its flag change alone would
        # leave the Windows title bar above the pixel one.
        self.setWindowFlags(self.windowFlags() | Qt.FramelessWindowHint)
        self.setStyleSheet(app_qss() + WIZARD_QSS)
        self.setButtonText(QWizard.CommitButton, self.buttonText(QWizard.NextButton))
        self.setButtonText(QWizard.FinishButton, "Open Chronicle")
        self.setButtonText(QWizard.CancelButton, "Close")
        self.resize(640, 440)
        framed = LOCATION in pages
        self.pages: Dict[str, QWizardPage] = {}
        if framed:
            self.addPage(WelcomePage())
        for name in pages:
            self.pages[name] = _PAGE_CLASSES[name]()
            self.addPage(self.pages[name])
        if framed:
            self.addPage(DonePage())
        # The pixel title bar covers the page header, so it carries the step name.
        self.currentIdChanged.connect(self._show_step)

    def _show_step(self, page_id: int) -> None:
        page = self.page(page_id)
        title = f"Chronicle {APP_VERSION} Setup"
        self.setWindowTitle(f"{title} · {page.title()}" if page else title)

    def done(self, result: int) -> None:
        for page in self.pages.values():
            shutdown = getattr(page, "shutdown", None)
            if shutdown:
                shutdown()
        super().done(result)
