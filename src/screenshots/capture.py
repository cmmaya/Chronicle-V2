import logging
from pathlib import Path
from datetime import datetime
from typing import Optional, Callable, Tuple

from PySide6.QtGui import QShortcut, QKeySequence, QGuiApplication, QScreen, QCursor
from PySide6.QtWidgets import QWidget, QInputDialog, QLineEdit
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
        self.session_name = None  # Nombre de la sesión para el nombre del archivo

    def get_start_time(self) -> datetime:
        return self.start_time

    def capture_fullscreen(self, session_name: str = 'session', session_id: int = 1) -> str:
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

            # Generar nombre de archivo con session_name + timestamp
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            # Limpiar nombre de sesión para usar en nombre de archivo
            safe_name = session_name.replace(' ', '_')
            for char in ['/', '\\', ':', '-', '.', ',']:
                safe_name = safe_name.replace(char, '_')
            filename = f'{safe_name}_{timestamp}.png'
            output_path = self.screenshots_path / filename

            # Guardar imagen
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

    def capture_interactive_region(self, session_name: str = 'session', session_id: int = 1) -> Optional[str]:
        """
        Inicia una captura interactiva de región con selección visual.
        Ejecuta el dialog de forma modal y retorna la ruta de la imagen capturada.
        """
        result = {}

        def on_region_selected(image_rect: QRect, full_image: 'QImage', output_dir: Path):
            """Callback que se ejecuta cuando el usuario selecciona una región."""
            try:
                # Recortar la región seleccionada de la imagen original
                cropped = full_image.copy(image_rect)

                # Generar nombre de archivo con session_name + timestamp
                timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                # Limpiar nombre de sesión para usar en nombre de archivo
                # Reemplazar caracteres no válidos en nombres de archivo
                safe_name = session_name.replace(' ', '_')
                for char in ['/', '\\', ':', '-', '.', ',']:
                    safe_name = safe_name.replace(char, '_')
                filename = f'{safe_name}_{timestamp}.png'
                output_path = self.screenshots_path / filename

                # Guardar imagen
                cropped.save(str(output_path), "PNG")

                logger.info(f'Interactive region screenshot saved to {output_path}')
                self._store_metadata(output_path, session_id)

                # Guardar el resultado (el filename se usará para la descripción después)
                result['path'] = str(output_path)
                result['filename'] = filename

            except Exception as e:
                logger.error(f'Failed to save interactive region capture: {str(e)}')
                result['error'] = str(e)

        # Crear y mostrar el widget de selección de forma modal
        dialog = SnippingOverlay(
            on_region_selected=on_region_selected,
            output_dir=self.screenshots_path
        )
        dialog.exec()

        # Después de que el diálogo de selección se cierre, pedir la descripción
        if 'filename' in result:
            try:
                from PySide6.QtWidgets import QApplication
                QApplication.processEvents()  # Procesar eventos pendientes

                description = self._prompt_for_description(result['filename'])
                if description:
                    # Quitar la extensión .png del filename para el archivo de descripción
                    desc_filename = result['filename'].replace('.png', '')
                    desc_path = self.screenshots_path / f'description_{desc_filename}.txt'
                    desc_path.write_text(description, encoding='utf-8')
                    logger.info(f'Screenshot description saved to {desc_path}')

                    # Actualizar la descripción en la base de datos
                    self.update_screenshot_description(result['path'], description)
            except Exception as e:
                logger.error(f'Failed to save screenshot description: {str(e)}')

        # Retornar el resultado
        if 'path' in result:
            return result['path']
        elif 'error' in result:
            raise RuntimeError(result['error'])
        return None

    def _prompt_for_description(self, filename: str) -> Optional[str]:
        """
        Muestra un diálogo para que el usuario ingrese una descripción del screenshot.
        """
        from PySide6.QtWidgets import QApplication

        # Necesitamos una ventana padre para el diálogo
        parent = None
        widget = QApplication.topLevelWidgets()
        if widget:
            for w in widget:
                if w.isWindow() and w.isVisible():
                    parent = w
                    break

        text, ok = QInputDialog.getText(
            parent,
            'Screenshot Description',
            'Enter a description for this screenshot:',
            QLineEdit.Normal,
            ''
        )

        if ok and text:
            return text.strip()
        return None

    def register_shortcuts(self, parent: QWidget):
        """Registra los atajos de teclado para capturar pantallas."""
        # Usar lambda con valores por defecto para evitar problemas de scoping
        QShortcut(QKeySequence('Ctrl+Shift+S'), parent,
                  lambda: self.capture_fullscreen(session_name='screenshot'))
        QShortcut(QKeySequence('Ctrl+Shift+R'), parent,
                  lambda: self.capture_interactive_region(session_name='screenshot'))

    def _get_output_path(self, label: str) -> Path:
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        return self.screenshots_path / f'{timestamp}_{label}.png'

    def _store_metadata(self, filepath: Path, session_id: int, description: str = None):
        if self.db is None:
            logger.warning(f'Database not available, skipping metadata store for {filepath}')
            return
        try:
            cursor = self.db.connection.cursor()
            cursor.execute(
                'INSERT INTO screenshots (session_id, timestamp, filepath, description) VALUES (?, ?, ?, ?)',
                (session_id, int(datetime.now().timestamp()), str(filepath), description)
            )
            self.db.connection.commit()
            logger.debug(f'Screenshot metadata stored for {filepath}')
        except Exception as e:
            logger.error(f'Failed to store screenshot metadata: {str(e)}')

    def update_screenshot_description(self, filepath: str, description: str):
        """Actualiza la descripción de un screenshot existente."""
        if self.db is None:
            logger.warning(f'Database not available, skipping description update for {filepath}')
            return
        try:
            cursor = self.db.connection.cursor()
            cursor.execute(
                'UPDATE screenshots SET description = ? WHERE filepath = ?',
                (description, filepath)
            )
            self.db.connection.commit()
            logger.debug(f'Screenshot description updated for {filepath}')
        except Exception as e:
            logger.error(f'Failed to update screenshot description: {str(e)}')
