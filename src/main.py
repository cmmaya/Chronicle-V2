import logging

# Configure logging - keep important info, reduce noise
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# Reduce noise from buffer retrieval logs
logging.getLogger('src.audio_capture.core').setLevel(logging.WARNING)

from PySide6.QtWidgets import QApplication
from src.app.window import MainWindow


def main():
    app = QApplication([])
    window = MainWindow()
    window.show()
    app.exec()


if __name__ == '__main__':
    main()
