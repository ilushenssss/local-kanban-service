import configparser
import logging
from pathlib import Path
import sys
import tempfile
from PyQt5.QtWidgets import QApplication
from .ui import MainWindow
from .theme import stylesheet


def main():
    logging.basicConfig(filename=str(Path(tempfile.gettempdir()) / 'kanban-errors.log'), level=logging.ERROR)
    def exception_hook(kind, value, traceback):
        logging.error('Unhandled exception', exc_info=(kind, value, traceback))
        sys.__excepthook__(kind, value, traceback)
    sys.excepthook = exception_hook
    base = Path(__file__).resolve().parent.parent
    config = configparser.ConfigParser(interpolation=None)
    config.read(str(base / 'kanban.ini'), encoding='utf-8-sig')
    folder = Path(config.get('storage', 'folder', fallback='data'))
    if not folder.is_absolute():
        folder = base / folder
    app = QApplication(sys.argv)
    app.setApplicationName('Канбан')
    app.setOrganizationName('LocalKanban')
    app.setStyle('Fusion')
    app.setStyleSheet(stylesheet())
    window = MainWindow(str(folder))
    window.show()
    return app.exec_()


if __name__ == '__main__':
    sys.exit(main())
