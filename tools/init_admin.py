"""Offline initialization of the sole administrator in an existing database."""
import argparse
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from kanban.store import Store


def main():
    parser = argparse.ArgumentParser(description='Назначение первого администратора. Закройте все клиенты и сохраните резервную копию.')
    parser.add_argument('--data', required=True, help='Папка с существующим kanban.db')
    parser.add_argument('--account', required=True, help='Точный login Windows, который возвращает getpass.getuser() у администратора')
    args = parser.parse_args()
    if not (Path(args.data) / 'kanban.db').is_file():
        parser.error('В указанной папке нет kanban.db; существующие данные не найдены')
    try:
        store = Store(args.data)
        uid = store.initialize_admin(args.account.strip())
    except (ValueError, RuntimeError) as error:
        parser.exit(1, str(error) + '\n')
    print('Администратор назначен: {} (id={}). Данные сохранены.'.format(args.account, uid))
    print('Существующие доски переданы администратору. Настройте роли и доступ в приложении.')


if __name__ == '__main__':
    main()
