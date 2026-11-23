"""Configuration files stored next to the TeamSync launcher."""
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


def load_redmine_project_id(base, project):
    path = base / 'all_project.json'
    try:
        projects = json.loads(path.read_text(encoding='utf-8-sig'))
    except FileNotFoundError:
        raise ValueError('Для поиска в Redmine положите all_project.json рядом с main.pyw.') from None
    except (OSError, ValueError):
        raise ValueError('Не удалось прочитать all_project.json. Проверьте файл и его формат.') from None
    if not isinstance(projects, dict):
        raise ValueError('В all_project.json ожидается словарь «название проекта: ID».')
    if project not in projects:
        raise ValueError('В all_project.json нет проекта «{}».'.format(project))
    project_id = projects[project]
    if type(project_id) is not int or project_id <= 0:
        raise ValueError('В all_project.json укажите положительный числовой ID для проекта «{}».'.format(project))
    return project_id


def load_redmine_employees(base):
    try:
        employees = json.loads((base / 'employees.json').read_text(encoding='utf-8-sig'))
    except (OSError, ValueError):
        raise ValueError('Проверьте файл employees.json рядом с main.pyw.') from None
    if not isinstance(employees, list) or any(not isinstance(name, str) or not name.strip() for name in employees):
        raise ValueError('В employees.json ожидается список имён или логинов Redmine.')
    return employees


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
