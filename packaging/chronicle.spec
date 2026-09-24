# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for the standalone app folder dist\Chronicle\ (BU126).
#
# Build it with packaging\build.ps1, which uses a clean venv made from
# requirements.txt: a dev venv drags every extra package it holds into the
# bundle. Only src/ (through imports) and assets/ are collected - never
# chronicle.db, sessions/, preferences.json, .env or recordings.
import re
from pathlib import Path

from PyInstaller.utils.hooks import (
    collect_data_files, collect_dynamic_libs, collect_submodules,
)

ROOT = Path(SPECPATH).parent
BUILD_DIR = Path(workpath)

APP_VERSION = re.search(
    r'^APP_VERSION\s*=\s*"([^"]+)"', (ROOT / "src" / "config.py").read_text(encoding="utf-8"), re.M
).group(1)
_numbers = [int(n) for n in re.findall(r"\d+", APP_VERSION)][:4]
_version_tuple = tuple(_numbers + [0] * (4 - len(_numbers)))
BUILD_DIR.mkdir(parents=True, exist_ok=True)
version_file = BUILD_DIR / "version_info.txt"
version_file.write_text(
    (ROOT / "packaging" / "version_info.txt").read_text(encoding="utf-8")
    .replace("{version_tuple}", repr(_version_tuple))
    .replace("{version}", APP_VERSION),
    encoding="utf-8",
)

datas = [(str(ROOT / "assets"), "assets")]
# Chronicle's Google OAuth client (BU130): git-ignored, bundled when present.
if (ROOT / "google_oauth_client.json").is_file():
    datas.append((str(ROOT / "google_oauth_client.json"), "."))
# soundcard reads its cffi declarations (*.h / *.py.h) at import time;
# onnx_asr ships its preprocessor ONNX graphs as package data.
datas += collect_data_files("soundcard")
datas += collect_data_files("onnx_asr")

# PortAudio and libsndfile ship in the _sounddevice_data / _soundfile_data
# packages; FFmpeg in av's own folder.
binaries = []
for package in ("onnxruntime", "_sounddevice_data", "_soundfile_data", "av", "tokenizers"):
    binaries += collect_dynamic_libs(package)

hiddenimports = (
    # onnx_asr picks model and preprocessor classes by name.
    collect_submodules("onnx_asr")
    # keyring chooses its Windows backend through an entry point.
    + collect_submodules("keyring.backends")
    + ["win32ctypes.core"]
)

excludes = [
    "tests", "pytest", "_pytest", "tkinter", "_tkinter",
    "IPython", "matplotlib", "pandas", "scipy", "torch",
    # Qt modules Chronicle never imports (it uses QtCore/Gui/Widgets/Network;
    # SVG icons load through the qsvg image-format plugin, so QtSvg stays).
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
    "PySide6.QtWebChannel", "PySide6.QtWebSockets", "PySide6.QtWebView",
    "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.Qt3DInput", "PySide6.Qt3DLogic",
    "PySide6.Qt3DAnimation", "PySide6.Qt3DExtras",
    "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets", "PySide6.QtSpatialAudio",
    "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtQuick3D", "PySide6.QtQuickWidgets",
    "PySide6.QtQuickControls2",
    "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtGraphs",
    "PySide6.QtPdf", "PySide6.QtPdfWidgets", "PySide6.QtBluetooth", "PySide6.QtNfc",
    "PySide6.QtPositioning", "PySide6.QtLocation", "PySide6.QtSensors",
    "PySide6.QtSerialPort", "PySide6.QtSerialBus", "PySide6.QtSql", "PySide6.QtTest",
    "PySide6.QtDesigner", "PySide6.QtHelp", "PySide6.QtUiTools", "PySide6.QtScxml",
    "PySide6.QtStateMachine", "PySide6.QtRemoteObjects", "PySide6.QtTextToSpeech",
    "PySide6.QtHttpServer", "PySide6.QtOpenGLWidgets", "PySide6.QtAxContainer",
]

a = Analysis(
    [str(ROOT / "src" / "main.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[str(ROOT / "packaging" / "hooks")],
    excludes=excludes,
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Chronicle",
    icon=str(ROOT / "assets" / "chronicle.ico"),
    version=str(version_file),
    console=False,
    upx=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    upx=False,
    name="Chronicle",
)
