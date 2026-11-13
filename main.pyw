"""Direct pythonw entry point; report startup errors even without a console."""
from pathlib import Path
import logging
import sys
import tempfile


def launch():
    log = Path(tempfile.gettempdir()) / 'kanban-errors.log'
    logging.basicConfig(filename=str(log), level=logging.ERROR)
    try:
        # Do not rely on the working directory or a system Python installation.
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from kanban.__main__ import main
        return main()
    except Exception:
        logging.exception('TeamSync startup failed')
        if sys.platform == 'win32':
            import ctypes
            ctypes.windll.user32.MessageBoxW(None,
                'Не удалось запустить TeamSync. Проверьте комплектность папки приложения.\nЖурнал ошибок: ' + str(log),
                'TeamSync — ошибка запуска', 0x10)
        elif sys.stderr is not None:
            import traceback
            traceback.print_exc()
        return 1


if __name__ == '__main__':
    raise SystemExit(launch())
