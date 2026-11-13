import contextlib
import datetime as dt
import getpass
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import time
import uuid


def now():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')


class Conflict(Exception):
    def __init__(self, current):
        super().__init__('Задача изменена другим сотрудником. Ваши изменения сохранены в форме.')
        self.current = current


class Busy(Exception):
    pass


SCHEMA = '''
CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, login TEXT NOT NULL UNIQUE, display_name TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS boards(id INTEGER PRIMARY KEY, name TEXT NOT NULL, archived INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS board_members(board_id INTEGER REFERENCES boards(id), user_id INTEGER REFERENCES users(id), role TEXT NOT NULL DEFAULT 'editor', PRIMARY KEY(board_id,user_id));
CREATE TABLE IF NOT EXISTS columns(id INTEGER PRIMARY KEY, board_id INTEGER NOT NULL REFERENCES boards(id), name TEXT NOT NULL, position INTEGER NOT NULL, is_done INTEGER NOT NULL DEFAULT 0, wip_limit INTEGER CHECK(wip_limit IS NULL OR wip_limit>0), UNIQUE(id,board_id));
CREATE TABLE IF NOT EXISTS tasks(id INTEGER PRIMARY KEY, board_id INTEGER NOT NULL REFERENCES boards(id), column_id INTEGER NOT NULL, number INTEGER NOT NULL, title TEXT NOT NULL CHECK(length(trim(title))>0), description TEXT NOT NULL DEFAULT '', priority TEXT NOT NULL DEFAULT 'Обычный', due_date TEXT, position INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, completed_at TEXT, archived INTEGER NOT NULL DEFAULT 0, version INTEGER NOT NULL DEFAULT 1, FOREIGN KEY(column_id,board_id) REFERENCES columns(id,board_id), UNIQUE(board_id,number));
CREATE TABLE IF NOT EXISTS task_assignees(task_id INTEGER REFERENCES tasks(id), user_id INTEGER REFERENCES users(id), PRIMARY KEY(task_id,user_id));
CREATE TABLE IF NOT EXISTS labels(id INTEGER PRIMARY KEY, board_id INTEGER REFERENCES boards(id), name TEXT NOT NULL, UNIQUE(board_id,name));
CREATE TABLE IF NOT EXISTS task_labels(task_id INTEGER REFERENCES tasks(id), label_id INTEGER REFERENCES labels(id), PRIMARY KEY(task_id,label_id));
CREATE TABLE IF NOT EXISTS checklist_items(id INTEGER PRIMARY KEY, task_id INTEGER REFERENCES tasks(id), text TEXT NOT NULL, done INTEGER NOT NULL DEFAULT 0, position INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS comments(id INTEGER PRIMARY KEY, task_id INTEGER REFERENCES tasks(id), user_id INTEGER REFERENCES users(id), body TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS attachments(id INTEGER PRIMARY KEY, task_id INTEGER REFERENCES tasks(id), user_id INTEGER REFERENCES users(id), original_name TEXT NOT NULL, stored_name TEXT NOT NULL UNIQUE, size INTEGER NOT NULL, sha256 TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS task_history(id INTEGER PRIMARY KEY, task_id INTEGER REFERENCES tasks(id), user_id INTEGER REFERENCES users(id), created_at TEXT NOT NULL, operation TEXT NOT NULL, changes TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS request_deduplication(id TEXT PRIMARY KEY, operation TEXT NOT NULL, fingerprint TEXT NOT NULL, result TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS task_board_column ON tasks(board_id,archived,column_id,position);
CREATE INDEX IF NOT EXISTS task_due ON tasks(due_date);
CREATE INDEX IF NOT EXISTS history_task ON task_history(task_id,id);
CREATE INDEX IF NOT EXISTS comments_task ON comments(task_id,id);
CREATE INDEX IF NOT EXISTS attachments_task ON attachments(task_id);
CREATE INDEX IF NOT EXISTS assignees_user ON task_assignees(user_id,task_id);
CREATE INDEX IF NOT EXISTS task_labels_label ON task_labels(label_id,task_id);
CREATE TRIGGER IF NOT EXISTS immutable_history_update BEFORE UPDATE ON task_history BEGIN SELECT RAISE(ABORT,'history is immutable'); END;
CREATE TRIGGER IF NOT EXISTS immutable_history_delete BEFORE DELETE ON task_history BEGIN SELECT RAISE(ABORT,'history is immutable'); END;
'''


class Store:
    def __init__(self, folder, login=None, timeout=10):
        self.folder = Path(folder).absolute()
        self.login = login or getpass.getuser()
        self.timeout = timeout
        self.folder.mkdir(parents=True, exist_ok=True)
        (self.folder / 'attachments').mkdir(exist_ok=True)
        self.db = self.folder / 'kanban.db'
        self.lock = self.folder / 'kanban.lock'
        with self.exclusive():
            with self.connection() as c:
                version = c.execute('PRAGMA user_version').fetchone()[0]
                if version > 1:
                    raise RuntimeError('База создана более новой версией программы')
                c.executescript(SCHEMA)
                c.execute('INSERT OR IGNORE INTO schema_migrations VALUES(1,?)', (now(),))
                c.execute('PRAGMA user_version=1')
                self.user(c, self.login)
                c.commit()

    def connection(self):
        c = sqlite3.connect(str(self.db), timeout=self.timeout)
        c.row_factory = sqlite3.Row
        c.execute('PRAGMA foreign_keys=ON')
        c.execute('PRAGMA journal_mode=DELETE')
        c.execute('PRAGMA synchronous=FULL')
        return contextlib.closing(c)

    @contextlib.contextmanager
    def exclusive(self):
        started = time.monotonic()
        token = uuid.uuid4().hex
        while True:
            try:
                fd = os.open(str(self.lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                with os.fdopen(fd, 'w', encoding='utf-8') as f:
                    json.dump({'token': token, 'user': self.login, 'host': socket.gethostname(), 'pid': os.getpid(), 'time': now()}, f)
                break
            except FileExistsError:
                if time.monotonic() - started >= self.timeout:
                    raise Busy('Общая база занята. Повторите позже. После аварии блокировку снимает администратор при закрытых клиентах.')
                time.sleep(.1)
        try:
            yield
        finally:
            # Never remove another writer's lock.
            if json.loads(self.lock.read_text(encoding='utf-8')).get('token') == token:
                self.lock.unlink()

    def user(self, c, name):
        name = name.strip()
        if not name:
            raise ValueError('Пустое имя исполнителя')
        c.execute('INSERT OR IGNORE INTO users(login,display_name) VALUES(?,?)', (name, name))
        return c.execute('SELECT id FROM users WHERE login=?', (name,)).fetchone()[0]

    def write(self, operation, payload, fn, request_id=None):
        request_id = request_id or uuid.uuid4().hex
        fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        with self.exclusive(), self.connection() as c:
            c.execute('BEGIN IMMEDIATE')
            try:
                old = c.execute('SELECT * FROM request_deduplication WHERE id=?', (request_id,)).fetchone()
                if old:
                    if old['operation'] != operation or old['fingerprint'] != fingerprint:
                        raise ValueError('Идентификатор повтора использован для другой операции')
                    return json.loads(old['result'])
                uid = self.user(c, self.login)
                result = fn(c, uid)
                c.execute('INSERT INTO request_deduplication VALUES(?,?,?,?)', (request_id, operation, fingerprint, json.dumps(result, ensure_ascii=False)))
                c.commit()
                return result
            except BaseException:
                c.rollback()
                raise

    def history(self, c, task_id, uid, op, changes):
        c.execute('INSERT INTO task_history(task_id,user_id,created_at,operation,changes) VALUES(?,?,?,?,?)', (task_id, uid, now(), op, json.dumps(changes, ensure_ascii=False)))

    def boards(self):
        with self.connection() as c:
            return [dict(r) for r in c.execute('SELECT * FROM boards WHERE archived=0 ORDER BY id')]

    def board_action(self, name, board_id=None, archive=False, request_id=None):
        if not name.strip() and not archive:
            raise ValueError('Введите название доски')
        def apply(c, uid):
            if board_id is None:
                bid = c.execute('INSERT INTO boards(name,created_at) VALUES(?,?)', (name.strip(), now())).lastrowid
                for i, title in enumerate(['Входящие', 'Запланировано', 'В работе', 'На проверке', 'Завершено']):
                    c.execute('INSERT INTO columns(board_id,name,position,is_done) VALUES(?,?,?,?)', (bid, title, i, int(i == 4)))
                return bid
            if not c.execute('SELECT id FROM boards WHERE id=? AND archived=0', (board_id,)).fetchone():
                raise ValueError('Доска недоступна')
            c.execute('UPDATE boards SET name=?,archived=? WHERE id=?', (name.strip(), int(archive), board_id))
            return board_id
        return self.write('board', [name, board_id, archive], apply, request_id)

    def columns(self, board_id):
        with self.connection() as c:
            return [dict(r) for r in c.execute('SELECT * FROM columns WHERE board_id=? ORDER BY position,id', (board_id,))]

    def save_columns(self, board_id, columns, request_id=None):
        if not columns or any(not x['name'].strip() for x in columns):
            raise ValueError('Укажите названия колонок')
        if sum(bool(x['is_done']) for x in columns) > 1:
            raise ValueError('Выберите одну колонку завершения')
        def apply(c, uid):
            self.active_board(c, board_id)
            existing = {r['id'] for r in c.execute('SELECT id FROM columns WHERE board_id=?', (board_id,))}
            supplied = [x['id'] for x in columns if x.get('id')]
            if set(supplied) != existing or len(supplied) != len(existing):
                raise ValueError('Набор колонок изменился. Откройте настройки заново. Удаление колонок не поддерживается.')
            for i, x in enumerate(columns):
                if x.get('id'):
                    c.execute('UPDATE columns SET name=?,position=?,is_done=?,wip_limit=? WHERE id=?', (x['name'].strip(), i, int(x['is_done']), x.get('wip_limit'), x['id']))
                else:
                    c.execute('INSERT INTO columns(board_id,name,position,is_done,wip_limit) VALUES(?,?,?,?,?)', (board_id, x['name'].strip(), i, int(x['is_done']), x.get('wip_limit')))
            # Completion semantics also apply when the completion column is reconfigured.
            for row in c.execute('SELECT t.*, col.is_done FROM tasks t JOIN columns col ON col.id=t.column_id WHERE t.board_id=?', (board_id,)).fetchall():
                completed = row['completed_at'] or now() if row['is_done'] else None
                if completed != row['completed_at']:
                    c.execute('UPDATE tasks SET completed_at=?,updated_at=?,version=version+1 WHERE id=?', (completed, now(), row['id']))
                    self.history(c, row['id'], uid, 'Изменение колонки завершения', {'completed_at': [row['completed_at'], completed]})
            return True
        return self.write('columns', [board_id, columns], apply, request_id)

    def active_board(self, c, board_id):
        if not c.execute('SELECT 1 FROM boards WHERE id=? AND archived=0', (board_id,)).fetchone():
            raise ValueError('Доска архивирована или отсутствует')

    def detail_in(self, c, task_id):
        row = c.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone()
        if not row:
            raise ValueError('Задача не найдена')
        d = dict(row)
        d['assignees'] = [r[0] for r in c.execute('SELECT u.login FROM task_assignees a JOIN users u ON u.id=a.user_id WHERE a.task_id=? ORDER BY u.login', (task_id,))]
        d['labels'] = [r[0] for r in c.execute('SELECT l.name FROM task_labels a JOIN labels l ON l.id=a.label_id WHERE a.task_id=? ORDER BY l.name', (task_id,))]
        d['checklist'] = [dict(r) for r in c.execute('SELECT text,done FROM checklist_items WHERE task_id=? ORDER BY position', (task_id,))]
        for key, query in [('comments', 'SELECT x.*,u.display_name AS author FROM comments x JOIN users u ON u.id=x.user_id WHERE task_id=? ORDER BY x.id'), ('history', 'SELECT x.*,u.display_name AS author FROM task_history x JOIN users u ON u.id=x.user_id WHERE task_id=? ORDER BY x.id DESC'), ('attachments', 'SELECT * FROM attachments WHERE task_id=? ORDER BY id')]:
            d[key] = [dict(r) for r in c.execute(query, (task_id,))]
        return d

    def detail(self, task_id):
        with self.connection() as c:
            c.execute('BEGIN')
            return self.detail_in(c, task_id)

    def tasks(self, board_id, filters=None):
        f = filters or {}
        where = ['t.board_id=?', 't.archived=?']
        args = [board_id, int(f.get('archived', False))]
        if f.get('query'):
            where.append('(instr(lower(t.title),lower(?))>0 OR instr(lower(t.description),lower(?))>0)')
            args += [f['query'], f['query']]
        if f.get('assignee'):
            where.append('EXISTS(SELECT 1 FROM task_assignees a JOIN users u ON u.id=a.user_id WHERE a.task_id=t.id AND u.login=?)')
            args.append(f['assignee'])
        if f.get('label'):
            where.append('EXISTS(SELECT 1 FROM task_labels a JOIN labels l ON l.id=a.label_id WHERE a.task_id=t.id AND l.name=?)')
            args.append(f['label'])
        if f.get('priority'):
            where.append('t.priority=?')
            args.append(f['priority'])
        if f.get('unassigned'):
            where.append('NOT EXISTS(SELECT 1 FROM task_assignees a WHERE a.task_id=t.id)')
        if f.get('overdue'):
            where.append('t.due_date < ? AND t.completed_at IS NULL')
            args.append(dt.date.today().isoformat())
        for key, operator in [('due_from', '>='), ('due_to', '<=')]:
            if f.get(key):
                where.append('t.due_date ' + operator + ' ?')
                args.append(f[key])
        with self.connection() as c:
            c.execute('BEGIN')
            c.create_function('lower', 1, lambda s: s.casefold() if s else '')
            return [self.detail_in(c, r[0]) for r in c.execute('SELECT t.id FROM tasks t WHERE ' + ' AND '.join(where) + ' ORDER BY t.position,t.id', args).fetchall()]

    def save_task(self, data, request_id=None):
        data = dict(data)
        if not data.get('title', '').strip():
            raise ValueError('Введите название задачи')
        if data.get('due_date'):
            dt.date.fromisoformat(data['due_date'])
        if data.get('priority', 'Обычный') not in ['Низкий', 'Обычный', 'Высокий', 'Срочный']:
            raise ValueError('Неизвестный приоритет')
        def apply(c, uid):
            bid = data['board_id']
            self.active_board(c, bid)
            old = self.detail_in(c, data['id']) if data.get('id') else None
            if old and (old['board_id'] != bid or old['version'] != data.get('version')):
                raise Conflict(old)
            col = c.execute('SELECT * FROM columns WHERE id=? AND board_id=?', (data['column_id'], bid)).fetchone()
            if not col:
                raise ValueError('Колонка не принадлежит доске')
            position = data.get('position')
            if position is None:
                position = old['position'] if old and old['column_id'] == col['id'] else c.execute('SELECT COALESCE(MAX(position),0)+1024 FROM tasks WHERE column_id=?', (col['id'],)).fetchone()[0]
            completed = (old['completed_at'] if old else None) or now() if col['is_done'] else None
            values = (data['column_id'], data['title'].strip(), data.get('description', ''), data.get('priority', 'Обычный'), data.get('due_date') or None, position, now(), completed, int(data.get('archived', False)))
            if old:
                tid = old['id']
                c.execute('UPDATE tasks SET column_id=?,title=?,description=?,priority=?,due_date=?,position=?,updated_at=?,completed_at=?,archived=?,version=version+1 WHERE id=?', values + (tid,))
            else:
                number = c.execute('SELECT COALESCE(MAX(number),0)+1 FROM tasks WHERE board_id=?', (bid,)).fetchone()[0]
                tid = c.execute('INSERT INTO tasks(column_id,title,description,priority,due_date,position,updated_at,completed_at,archived,board_id,number,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)', values + (bid, number, now())).lastrowid
            c.execute('DELETE FROM task_assignees WHERE task_id=?', (tid,))
            for name in sorted(set(data.get('assignees', []))):
                c.execute('INSERT INTO task_assignees VALUES(?,?)', (tid, self.user(c, name)))
            c.execute('DELETE FROM task_labels WHERE task_id=?', (tid,))
            for name in sorted(set(data.get('labels', []))):
                if name.strip():
                    c.execute('INSERT OR IGNORE INTO labels(board_id,name) VALUES(?,?)', (bid, name.strip()))
                    lid = c.execute('SELECT id FROM labels WHERE board_id=? AND name=?', (bid, name.strip())).fetchone()[0]
                    c.execute('INSERT INTO task_labels VALUES(?,?)', (tid, lid))
            c.execute('DELETE FROM checklist_items WHERE task_id=?', (tid,))
            for i, item in enumerate(data.get('checklist', [])):
                if item['text'].strip():
                    c.execute('INSERT INTO checklist_items(task_id,text,done,position) VALUES(?,?,?,?)', (tid, item['text'], int(item['done']), i))
            current = self.detail_in(c, tid)
            fields = ['title', 'description', 'column_id', 'position', 'priority', 'due_date', 'completed_at', 'archived', 'assignees', 'labels', 'checklist']
            changes = {k: [old[k] if old else None, current[k]] for k in fields if not old or old[k] != current[k]}
            self.history(c, tid, uid, 'Изменение' if old else 'Создание', changes)
            return {'id': tid, 'version': current['version']}
        return self.write('task', data, apply, request_id)

    def comment(self, task_id, text, request_id=None):
        if not text.strip():
            raise ValueError('Введите комментарий')
        def apply(c, uid):
            d = self.detail_in(c, task_id)
            self.active_board(c, d['board_id'])
            cid = c.execute('INSERT INTO comments(task_id,user_id,body,created_at) VALUES(?,?,?,?)', (task_id, uid, text.strip(), now())).lastrowid
            self.history(c, task_id, uid, 'Комментарий', {'text': text.strip()})
            return cid
        return self.write('comment', [task_id, text], apply, request_id)

    def attach(self, task_id, source, request_id=None):
        source = Path(source)
        request_id = request_id or uuid.uuid4().hex
        # A stable request ID also names the immutable destination; interrupted uploads can be retried.
        internal = hashlib.sha256(request_id.encode()).hexdigest()
        def apply(c, uid):
            d = self.detail_in(c, task_id)
            self.active_board(c, d['board_id'])
            target = self.folder / 'attachments' / internal
            temp = target.with_suffix('.partial')
            try:
                digest = hashlib.sha256()
                size = 0
                with source.open('rb') as src, temp.open('xb') as dst:
                    while True:
                        chunk = src.read(1024 * 1024)
                        if not chunk:
                            break
                        dst.write(chunk)
                        digest.update(chunk)
                        size += len(chunk)
                    dst.flush()
                    os.fsync(dst.fileno())
                os.replace(str(temp), str(target))
                aid = c.execute('INSERT INTO attachments(task_id,user_id,original_name,stored_name,size,sha256,created_at) VALUES(?,?,?,?,?,?,?)', (task_id, uid, source.name, internal, size, digest.hexdigest(), now())).lastrowid
                self.history(c, task_id, uid, 'Вложение', {'name': source.name, 'sha256': digest.hexdigest()})
                return aid
            finally:
                if temp.exists():
                    temp.unlink()
        return self.write('attachment', [task_id, str(source)], apply, request_id)

    def download(self, attachment_id, destination):
        with self.connection() as c:
            r = c.execute('SELECT * FROM attachments WHERE id=?', (attachment_id,)).fetchone()
            if not r:
                raise FileNotFoundError('Вложение отсутствует в базе')
            source = self.folder / 'attachments' / r['stored_name']
            digest = hashlib.sha256()
            dest = Path(destination)
            created = False
            try:
                with source.open('rb') as src, dest.open('xb') as dst:
                    created = True
                    while True:
                        chunk = src.read(1024 * 1024)
                        if not chunk:
                            break
                        digest.update(chunk)
                        dst.write(chunk)
                if digest.hexdigest() != r['sha256']:
                    raise IOError('Контрольная сумма вложения не совпадает')
            except BaseException:
                if created:
                    dest.unlink(missing_ok=True)
                raise
        return str(dest)

    def backup(self, destination, retention=14):
        if retention < 1:
            raise ValueError('Срок хранения должен быть положительным')
        root = Path(destination).absolute()
        if root == self.folder or self.folder in root.parents:
            raise ValueError('Резервные копии должны находиться вне рабочей папки данных')
        root.mkdir(parents=True, exist_ok=True)
        target = root / ('kanban-' + dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S') + '-' + uuid.uuid4().hex[:8])
        with self.exclusive(), self.connection() as c:
            target.mkdir()
            with sqlite3.connect(str(target / 'kanban.db')) as dest:
                c.backup(dest)
                if dest.execute('PRAGMA integrity_check').fetchone()[0] != 'ok' or dest.execute('PRAGMA foreign_key_check').fetchall():
                    raise IOError('Проверка резервной базы не пройдена')
            (target / 'attachments').mkdir()
            manifest = {}
            for r in c.execute('SELECT stored_name,sha256 FROM attachments'):
                name = r['stored_name']
                shutil.copyfile(str(self.folder / 'attachments' / name), str(target / 'attachments' / name))
                digest = hashlib.sha256((target / 'attachments' / name).read_bytes()).hexdigest()
                if digest != r['sha256']:
                    raise IOError('Повреждено вложение: ' + name)
                manifest[name] = digest
            (target / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
            (target / 'VERIFIED').write_text(now(), encoding='utf-8')
        cutoff = time.time() - retention * 86400
        for old in root.glob('kanban-*'):
            if old.is_dir() and (old / 'VERIFIED').is_file() and old.stat().st_mtime < cutoff:
                shutil.rmtree(str(old))
        return str(target)
