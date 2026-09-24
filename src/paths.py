"""Where Chronicle reads its shipped files and writes its user data (BU121).

This is the only module that knows about folders:

- :func:`resource_dir` - read-only files the app ships (``assets/``). The
  bundle folder when frozen by PyInstaller, otherwise the project root.
- :func:`data_dir` - the per-user writable root holding the database, sessions,
  preferences, models and logs. Resolved in this order:

  1. the ``CHRONICLE_DATA_DIR`` environment variable;
  2. the location saved by the setup wizard in ``%APPDATA%\\Chronicle\\location.json``;
  3. running from source: the project root, so existing data is used unchanged;
  4. frozen: ``%LOCALAPPDATA%\\Chronicle``.

Nothing here depends on the working directory.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

APP_NAME = "Chronicle"
DATA_DIR_ENV = "CHRONICLE_DATA_DIR"
POINTER_FILENAME = "location.json"


def is_frozen() -> bool:
    """True when running from a PyInstaller build."""
    return bool(getattr(sys, "frozen", False))


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def resource_dir() -> Path:
    """Folder holding the read-only files the app ships (``assets/``)."""
    if is_frozen():
        bundle = getattr(sys, "_MEIPASS", None)
        return Path(bundle) if bundle else Path(sys.executable).resolve().parent
    return _project_root()


def pointer_file() -> Path:
    """``%APPDATA%\\Chronicle\\location.json``, written by the setup wizard."""
    roaming = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    return Path(roaming) / APP_NAME / POINTER_FILENAME


def _pointer_location() -> Optional[Path]:
    """The data folder named by the pointer file, or None if absent/unusable."""
    path = pointer_file()
    if not path.is_file():
        return None
    try:
        with path.open("r", encoding="utf-8") as f:
            location = json.load(f).get("data_dir")
        if not isinstance(location, str) or not location.strip():
            raise ValueError("no 'data_dir' string")
        return Path(location)
    except Exception as exc:  # noqa: BLE001 - a bad pointer must not stop the app
        logger.warning("Ignoring unreadable data location file %s: %s", path, exc)
        return None


def _frozen_default() -> Path:
    from PySide6.QtCore import QStandardPaths

    # GenericDataLocation is %LOCALAPPDATA% whatever the application name is,
    # so this resolves the same before and after QApplication exists.
    base = QStandardPaths.writableLocation(QStandardPaths.GenericDataLocation)
    return Path(base) / APP_NAME


def data_dir_from_environment() -> bool:
    """True when ``CHRONICLE_DATA_DIR`` fixes the data folder."""
    return bool(os.environ.get(DATA_DIR_ENV, "").strip())


def resolved_data_dir() -> Path:
    """Where :func:`data_dir` points, without creating it."""
    env = os.environ.get(DATA_DIR_ENV, "").strip()
    if env:
        return Path(env)
    root = _pointer_location()
    if root is None:
        root = _frozen_default() if is_frozen() else _project_root()
    return root


def data_dir() -> Path:
    """The per-user writable root, created on first use."""
    root = resolved_data_dir()
    root.mkdir(parents=True, exist_ok=True)
    return root


def save_data_location(folder: Path) -> None:
    """Write the pointer file so later launches use ``folder`` (BU124 wizard)."""
    path = pointer_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"data_dir": str(Path(folder))}), encoding="utf-8")
    os.replace(tmp, path)
    logger.info("Data location saved: %s", folder)


def _subdir(name: str) -> Path:
    path = data_dir() / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def db_path() -> Path:
    return data_dir() / "chronicle.db"


def preferences_path() -> Path:
    return data_dir() / "preferences.json"


def sessions_dir() -> Path:
    return _subdir("sessions")


def models_dir() -> Path:
    """Hugging Face cache root (``HF_HOME``) for the app's models."""
    return _subdir("models")


def setup_state_path() -> Path:
    """Which setup-wizard steps were completed for this data folder (BU124)."""
    return data_dir() / "setup_state.json"


def bundled_google_client_path() -> Path:
    """Chronicle's own Google OAuth client, added at build time (BU130)."""
    return resource_dir() / "google_oauth_client.json"


def custom_google_client_path() -> Path:
    """A Google OAuth client the user imported (BU130)."""
    return data_dir() / "google_client.json"


def logs_dir() -> Path:
    return _subdir("logs")


def asset_path(*parts: str) -> Path:
    """A shipped file under ``resource_dir()/assets``."""
    return resource_dir().joinpath("assets", *parts)
