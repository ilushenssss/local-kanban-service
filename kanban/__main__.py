import configparser
import logging
from pathlib import Path
import sys
import tempfile
from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import QLocale, QTranslator, QLibraryInfo
from .ui import MainWindow
from .theme import apply_theme, theme_settings


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
    QLocale.setDefault(QLocale('ru_RU'))
    app = QApplication(sys.argv)
    translator = QTranslator(app)
    if translator.load('qtbase_ru', QLibraryInfo.location(QLibraryInfo.TranslationsPath)):
        app.installTranslator(translator)
    app.setApplicationName('Канбан')
    app.setOrganizationName('LocalKanban')
    app.setStyle('Fusion')
    apply_theme(app, theme_settings().value('dark', False, type=bool))
    window = MainWindow(str(folder))
    window.show()
    return app.exec_()


if __name__ == '__main__':
    sys.exit(main())
