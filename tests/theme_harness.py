"""Run code under a given UI theme in a fresh interpreter (BU153-BU160).

A theme is chosen once per process, before any widget module is imported:
module-level colours are built from it, and Boring Corporate also swaps the
``QFont`` class. A test that needs another theme's widgets therefore cannot
switch in-process; it runs a snippet in a subprocess instead.
"""
import os
import subprocess
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

_PRELUDE = """
import os, sys
os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, {root!r})
from src.app import theme
theme.activate({name!r})
from PySide6.QtWidgets import QApplication
app = QApplication([])
"""


def run_in_theme(name: str, code: str) -> str:
    """Run ``code`` with theme ``name`` active and a QApplication up; return
    its stdout. Fails the test with the child's stderr on a non-zero exit."""
    script = (_PRELUDE.format(root=str(ROOT), name=name) + textwrap.dedent(code)
              # Skip Qt's teardown, which can crash on exit headless.
              + "\nsys.stdout.flush()\nos._exit(0)\n")
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    result = subprocess.run([sys.executable, "-c", script], capture_output=True,
                            text=True, timeout=180, cwd=str(ROOT), env=env)
    assert result.returncode == 0, result.stderr[-4000:]
    return result.stdout
