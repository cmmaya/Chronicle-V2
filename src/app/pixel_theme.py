import os


def asset_path(filename: str) -> str:
    """
    Devuelve la ruta a los SVG pixel-art.
    Espera esta estructura:
        assets/pixel/<filename>
    """
    here = os.path.dirname(os.path.abspath(__file__))
    src_dir = os.path.dirname(here)
    project_dir = os.path.dirname(src_dir)
    return os.path.join(project_dir, "assets", "pixel", filename)


def app_qss() -> str:
    return """
    QMainWindow {
        background: #061946;
    }

    QWidget {
        background: transparent;
        color: #FFF0BF;
        font-family: "Courier New";
        font-size: 15px;
    }

    QLabel {
        background: transparent;
        border: none;
        color: #FFF0BF;
    }

    QLabel#BrandTitle {
        color: #FFF0BF;
        font-size: 26px;
        font-weight: 900;
        padding: 4px 0px;
    }

    QLabel#ScopeLabel {
        color: #FFE9A8;
        font-size: 13px;
    }

    QFrame,
    QGroupBox {
        background: transparent;
        border: none;
    }

    QFrame#UnifiedSearchBar,
    QFrame#ChatInputBar {
        background: #F6E0A6;
        border: 2px solid #254D9C;
        border-radius: 7px;
    }

    QFrame#TranscriptViewport {
        background: transparent;
        border: none;
    }

    QListWidget {
        background: transparent;
        border: none;
        color: #FFF0BF;
        outline: none;
        padding: 3px 8px 3px 3px;
    }

    QListWidget::item {
        background: #274F9B;
        color: #FFF0BF;
        border: 2px solid #315DB1;
        border-radius: 7px;
        padding: 8px 9px;
        margin: 3px 1px;
    }

    QListWidget::item:selected {
        background: #315DB1;
        color: #FFF0BF;
        border: 2px solid #FFE9A8;
    }

    QListWidget::item:hover {
        background: #315DB1;
    }

    QScrollArea {
        background: transparent;
        border: none;
    }

    QScrollArea > QWidget {
        background: transparent;
    }

    QScrollArea > QWidget > QWidget {
        background: transparent;
    }

    QScrollBar:vertical {
        background: #071D52;
        width: 14px;
        margin: 0px;
        border: 2px solid #254D9C;
    }

    QScrollBar::handle:vertical {
        background: #8EA7D8;
        min-height: 24px;
        border: 1px solid #FFF0BF;
    }

    QScrollBar::add-line:vertical,
    QScrollBar::sub-line:vertical {
        height: 0px;
        background: transparent;
        border: none;
    }

    QScrollBar:horizontal {
        background: #071D52;
        height: 14px;
        margin: 0px;
        border: 2px solid #254D9C;
    }

    QScrollBar::handle:horizontal {
        background: #8EA7D8;
        min-width: 24px;
        border: 1px solid #FFF0BF;
    }

    QScrollBar::add-line:horizontal,
    QScrollBar::sub-line:horizontal {
        width: 0px;
        background: transparent;
        border: none;
    }

    QTextEdit,
    QTextBrowser,
    QLineEdit {
        background: #F6E0A6;
        color: #071846;
        border: 2px solid #254D9C;
        selection-background-color: #274F9B;
        selection-color: #FFF0BF;
        padding: 8px;
    }

    QTextEdit:focus,
    QLineEdit:focus {
        border: 3px solid #FFF0BF;
    }

    QTextEdit[readOnly="true"] {
        background: #071D52;
        color: #FFF0BF;
        border: 3px solid #254D9C;
    }

    QComboBox {
        background: #F6E0A6;
        color: #071846;
        border: none;
        border-radius: 6px;
        padding: 6px 10px;
        min-height: 34px;
        font-size: 15px;
        font-weight: 700;
    }

    QComboBox:hover {
        background: #FFE7B4;
    }

    QComboBox::drop-down {
        border: none;
        width: 28px;
    }

    QComboBox::down-arrow {
        image: none;
        width: 0;
        height: 0;
        border-left: 5px solid transparent;
        border-right: 5px solid transparent;
        border-top: 6px solid #071846;
        margin-right: 8px;
    }

    QComboBox QAbstractItemView {
        background: #F6E0A6;
        color: #071846;
        border: 2px solid #254D9C;
        selection-background-color: #274F9B;
        selection-color: #FFF0BF;
    }

    QComboBox#ScopeCombo,
    QComboBox#AgentCombo,
    QLineEdit#SessionSearchInput {
        background: transparent;
        color: #071846;
        border: none;
        padding: 6px 8px;
        font-size: 15px;
        font-weight: 700;
    }

    QTextEdit#QuestionInput {
        background: transparent;
        color: #071846;
        border: none;
        padding: 8px 10px;
        font-size: 16px;
        font-weight: 700;
    }

    QTableWidget {
        background: #071D52;
        color: #FFF0BF;
        border: 3px solid #254D9C;
        gridline-color: #254D9C;
    }

    QHeaderView::section {
        background: #274F9B;
        color: #FFF0BF;
        border: 2px solid #315DB1;
        padding: 6px;
    }

    QPushButton#AppLogsButton {
        min-height: 46px;
        text-align: center;
        border-radius: 7px;
    }
    """
