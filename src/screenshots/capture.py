import logging
from pathlib import Path
from datetime import datetime
from typing import Optional

import mss
import mss.tools
from PySide6.QtGui import QShortcut, QKeySequence
from PySide6.QtWidgets import QWidget

from .snipping import SnippingOverlay

logger = logging.getLogger(__name__)


class ScreenshotCapture:
    def __init__(self, session_path: str = '/tmp/sessions/session_001', db=None):
        self.session_path = Path(session_path)
        self.screenshots_path = self.session_path / 'screenshots'
        self.screenshots_path.mkdir(parents=True, exist_ok=True)
        self.db = db
        self.start_time = datetime.now()

    def get_start_time(self) -> datetime:
        return self.start_time

    def capture_fullscreen(self, label: str = 'screenshot', session_id: int = 1) -> str:
        try:
            with mss.mss() as sct:
                monitor = sct.monitors[1]
                screenshot = sct.grab(monitor)
                output_path = self._get_output_path(label)
                mss.tools.to_png(screenshot.rgb, screenshot.size, output=str(output_path))
                logger.info(f'Full screen screenshot saved to {output_path}')
                self._store_metadata(output_path, session_id)
                return str(output_path)
        except Exception as e:
            logger.error(f'Full screen capture failed: {str(e)}')
            raise

    def capture_region(self, left: int, top: int, width: int, height: int,
                       label: str = 'region', session_id: int = 1) -> str:
        try:
            with mss.mss() as sct:
                region = {'left': left, 'top': top, 'width': width, 'height': height}
                screenshot = sct.grab(region)
                output_path = self._get_output_path(label)
                mss.tools.to_png(screenshot.rgb, screenshot.size, output=str(output_path))
                logger.info(f'Region screenshot saved to {output_path}')
                self._store_metadata(output_path, session_id)
                return str(output_path)
        except Exception as e:
            logger.error(f'Region capture failed: {str(e)}')
            raise

    def capture_interactive_region(self, label: str = 'region', session_id: int = 1):
        def on_region_selected(left, top, width, height):
            self.capture_region(left, top, width, height, label, session_id)
        SnippingOverlay(on_region_selected)

    def register_shortcuts(self, parent: QWidget):
        QShortcut(QKeySequence('Ctrl+Shift+S'), parent, self.capture_fullscreen)
        QShortcut(QKeySequence('Ctrl+Shift+R'), parent,
                  lambda: self.capture_interactive_region())

    def _get_output_path(self, label: str) -> Path:
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        return self.screenshots_path / f'{timestamp}_{label}.png'

    def _store_metadata(self, filepath: Path, session_id: int):
        if self.db is None:
            return
        try:
            cursor = self.db.connection.cursor()
            cursor.execute(
                'INSERT INTO screenshots (session_id, timestamp, filepath) VALUES (?, ?, ?)',
                (session_id, int(datetime.now().timestamp()), str(filepath))
            )
            self.db.connection.commit()
            logger.debug(f'Screenshot metadata stored for {filepath}')
        except Exception as e:
            logger.error(f'Failed to store screenshot metadata: {str(e)}')
