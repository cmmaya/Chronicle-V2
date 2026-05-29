import logging
from pathlib import Path
from datetime import datetime
from typing import Optional, Callable

from PySide6.QtGui import QShortcut, QKeySequence, QGuiApplication, QScreen, QCursor
from PySide6.QtWidgets import QWidget
from PySide6.QtCore import QRect

from .snipping import SnippingOverlay

logger = logging.getLogger(__name__)


class ScreenshotCapture:
    """
    Capturador de pantallas con soporte correcto para HiDPI.
    Utiliza la metodología de test_screenshots.py.
    """
    def __init__(self, session_path: str = '/tmp/sessions/session_001', db=None):
        self.session_path = Path(session_path)
        self.screenshots_path = self.session_path / 'screenshots'
        self.screenshots_path.mkdir(parents=True, exist_ok=True)
        self.db = db
        self.start_time = datetime.now()

    def get_start_time(self) -> datetime:
        return self.start_time

    def capture_fullscreen(self, label: str = 'screenshot', session_id: int = 1) -> str:
        """
        Captura la pantalla completa con soporte HiDPI.
        """
        try:
            # Obtener la pantalla actual
            screen = QGuiApplication.screenAt(QCursor.pos())

            if screen is None:
                screen = QGuiApplication.primaryScreen()

            if screen is None:
                raise RuntimeError("No se pudo acceder a la pantalla.")

            # Capturar pantalla usando Qt
            pixmap = screen.grabWindow(0)

            # Convertir a QImage y neutralizar DPR
            image = pixmap.toImage()
            image.setDevicePixelRatio(1)

            # Guardar imagen
            output_path = self._get_output_path(label)
            image.save(str(output_path), "PNG")

            logger.info(f'Full screen screenshot saved to {output_path}')
            self._store_metadata(output_path, session_id)
            return str(output_path)

        except Exception as e:
            logger.error(f'Full screen capture failed: {str(e)}')
            raise

    def capture_region(self, left: int, top: int, width: int, height: int,
                       label: str = 'region', session_id: int = 1) -> str:
        """
        Captura una región específica de la pantalla con soporte HiDPI.
        Las coordenadas deben ser las coordenadas reales de la imagen (sin escalado).
        """
        try:
            # Obtener la pantalla actual
            screen = QGuiApplication.screenAt(QCursor.pos())

            if screen is None:
                screen = QGuiApplication.primaryScreen()

            if screen is None:
                raise RuntimeError("No se pudo acceder a la pantalla.")

            # Capturar pantalla completa
            pixmap = screen.grabWindow(0)

            # Convertir a QImage y neutralizar DPR
            image = pixmap.toImage()
            image.setDevicePixelRatio(1)

            # Recortar la región especificada
            crop_rect = QRect(left, top, width, height)
            cropped = image.copy(crop_rect)

            # Guardar imagen
            output_path = self._get_output_path(label)
            cropped.save(str(output_path), "PNG")

            logger.info(f'Region screenshot saved to {output_path}')
            self._store_metadata(output_path, session_id)
            return str(output_path)

        except Exception as e:
            logger.error(f'Region capture failed: {str(e)}')
            raise

    def capture_interactive_region(self, label: str = 'region', session_id: int = 1) -> Optional[str]:
        """
        Inicia una captura interactiva de región con selección visual.
        Ejecuta el dialog de forma modal y retorna la ruta de la imagen capturada.
        """
        result = {}

        def on_region_selected(image_rect: QRect, output_dir: Path):
            """Callback que se ejecuta cuando el usuario selecciona una región."""
            try:
                # Obtener la pantalla actual
                screen = QGuiApplication.screenAt(QCursor.pos())

                if screen is None:
                    screen = QGuiApplication.primaryScreen()

                # Capturar pantalla completa
                pixmap = screen.grabWindow(0)

                # Convertir a QImage y neutralizar DPR
                full_image = pixmap.toImage()
                full_image.setDevicePixelRatio(1)

                # Recortar la región seleccionada
                cropped = full_image.copy(image_rect)

                # Guardar imagen
                timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                output_path = self.screenshots_path / f'{timestamp}_{label}.png'
                cropped.save(str(output_path), "PNG")

                logger.info(f'Interactive region screenshot saved to {output_path}')
                self._store_metadata(output_path, session_id)

                # Guardar el resultado
                result['path'] = str(output_path)

            except Exception as e:
                logger.error(f'Failed to save interactive region capture: {str(e)}')
                result['error'] = str(e)

        # Crear y mostrar el widget de selección de forma modal
        dialog = SnippingOverlay(
            on_region_selected=on_region_selected,
            output_dir=self.screenshots_path
        )
        dialog.exec()

        # Retornar el resultado
        if 'path' in result:
            return result['path']
        elif 'error' in result:
            raise RuntimeError(result['error'])
        return None

    def register_shortcuts(self, parent: QWidget):
        """Registra los atajos de teclado para capturar pantallas."""
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
