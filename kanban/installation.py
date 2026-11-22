"""Installation-wide database location, available before SQLite can be opened."""
import configparser
import json
import os
from pathlib import Path
import tempfile


def load_folder(base):
    path = base / 'teamsync-settings.json'
    if not path.exists():
        return None
    folder = json.loads(path.read_text(encoding='utf-8'))['folder']
    if not isinstance(folder, str) or not Path(folder).is_absolute():
        raise ValueError('Путь к базе должен быть абсолютным')
    return folder


def save_folder(base, folder):
    path = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=base, delete=False) as file:
            path = Path(file.name)
            json.dump({'folder': str(folder)}, file, ensure_ascii=False)
        os.replace(str(path), str(base / 'teamsync-settings.json'))
    finally:
        if path is not None:
            path.unlink(missing_ok=True)


def legacy_settings(base):
    config = configparser.ConfigParser(interpolation=None)
    try:
        config.read(str(base / 'kanban.ini'), encoding='utf-8-sig')
    except configparser.Error as error:
        raise ValueError('Не удалось прочитать прежние настройки') from error
    folder = base / config.get('storage', 'folder', fallback='data')
    url = config.get('redmine', 'url', fallback='')
    mapping = dict(config.items('redmine.status')) if config.has_section('redmine.status') else {}
    return str(folder), url, mapping
