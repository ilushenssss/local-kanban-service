"""Validate and restore into a NEW directory; never overwrite live data."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3


def restore(source, destination):
    source, destination = Path(source), Path(destination)
    if destination.exists():
        raise ValueError('Выберите новый, несуществующий каталог восстановления')
    if not (source / 'VERIFIED').is_file():
        raise ValueError('Копия не помечена как проверенная')
    db = source / 'kanban.db'
    with sqlite3.connect(db.resolve().as_uri() + '?mode=ro', uri=True) as c:
        if c.execute('PRAGMA integrity_check').fetchone()[0] != 'ok' or c.execute('PRAGMA foreign_key_check').fetchall():
            raise ValueError('База повреждена')
        expected = dict(c.execute('SELECT stored_name,sha256 FROM attachments'))
    manifest = json.loads((source / 'manifest.json').read_text(encoding='utf-8'))
    if expected != manifest:
        raise ValueError('Манифест не соответствует базе')
    for name, digest in manifest.items():
        if Path(name).name != name or hashlib.sha256((source / 'attachments' / name).read_bytes()).hexdigest() != digest:
            raise ValueError('Повреждено вложение: ' + name)
    shutil.copytree(str(source), str(destination))
    print('Восстановлено в', destination)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('source')
    p.add_argument('destination')
    args = p.parse_args()
    restore(args.source, args.destination)
