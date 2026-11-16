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


class AccessDenied(ValueError):
    pass


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
                if version > 5:
                    raise RuntimeError('База создана более новой версией программы')
                c.executescript(SCHEMA)
                c.execute('BEGIN IMMEDIATE')
                c.execute('INSERT OR IGNORE INTO schema_migrations VALUES(1,?)', (now(),))
                if version < 2:
                    c.execute('CREATE TABLE IF NOT EXISTS team_messages(id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id), body TEXT NOT NULL, created_at TEXT NOT NULL)')
                    c.execute('INSERT OR IGNORE INTO schema_migrations VALUES(2,?)', (now(),))
                    c.execute('PRAGMA user_version=2')
                if version < 3 and 'role' not in {r[1] for r in c.execute('PRAGMA table_info(users)')}:
                    c.execute("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'employee' CHECK(role IN ('admin','manager','employee'))")
                    c.execute('ALTER TABLE boards ADD COLUMN owner_id INTEGER REFERENCES users(id)')
                    c.execute("CREATE UNIQUE INDEX one_admin ON users(role) WHERE role='admin'")
                    c.execute('CREATE TABLE access_history(id INTEGER PRIMARY KEY, actor TEXT NOT NULL, created_at TEXT NOT NULL, operation TEXT NOT NULL, payload TEXT NOT NULL)')
                    c.execute('INSERT OR IGNORE INTO schema_migrations VALUES(3,?)', (now(),))
                    c.execute('PRAGMA user_version=3')
                if 'kind' not in {r[1] for r in c.execute('PRAGMA table_info(team_messages)')}:
                    c.execute("ALTER TABLE team_messages ADD COLUMN kind TEXT NOT NULL DEFAULT 'text' CHECK(kind IN ('text','sticker'))")
                    c.execute('CREATE TABLE IF NOT EXISTS team_stickers(message_id INTEGER PRIMARY KEY REFERENCES team_messages(id), stored_name TEXT NOT NULL UNIQUE, sha256 TEXT NOT NULL, size INTEGER NOT NULL, width INTEGER NOT NULL, height INTEGER NOT NULL)')
                c.execute('INSERT OR IGNORE INTO schema_migrations VALUES(4,?)', (now(),))
                if 'board_id' not in {r[1] for r in c.execute('PRAGMA table_info(team_messages)')}:
                    c.execute('ALTER TABLE team_messages ADD COLUMN board_id INTEGER REFERENCES boards(id)')
                    if c.execute('SELECT 1 FROM team_messages LIMIT 1').fetchone():
                        matches = c.execute('SELECT id FROM boards WHERE name=? AND archived=0', ('С-А',)).fetchall()
                        if len(matches) > 1:
                            raise ValueError('Несколько досок «С-А»: оставьте это название только у одной перед обновлением')
                        if matches:
                            target = matches[0][0]
                        else:
                            admin = c.execute("SELECT id FROM users WHERE role='admin'").fetchone()
                            target = c.execute('INSERT INTO boards(name,created_at,owner_id) VALUES(?,?,?)', ('С-А', now(), admin[0] if admin else None)).lastrowid
                            for position, title in enumerate(['Бэклог', 'В работе', 'На проверке', 'Готово']):
                                c.execute('INSERT INTO columns(id,board_id,name,position,is_done) VALUES(?,?,?,?,?)', (self.next_column_id(c), target, title, position, int(position == 3)))
                        c.execute('UPDATE team_messages SET board_id=?', (target,))
                        for setting in c.execute("SELECT key,value FROM settings WHERE key LIKE 'team_read:%'").fetchall():
                            key = 'board_read:' + json.dumps([setting['key'][len('team_read:'):], target])
                            c.execute('INSERT OR IGNORE INTO settings VALUES(?,?)', (key, setting['value']))
                c.execute('CREATE INDEX IF NOT EXISTS team_messages_board ON team_messages(board_id,id)')
                c.execute('CREATE TABLE IF NOT EXISTS retired_labels(board_id INTEGER NOT NULL REFERENCES boards(id),name TEXT NOT NULL,PRIMARY KEY(board_id,name))')
                c.execute('INSERT OR IGNORE INTO schema_migrations VALUES(5,?)', (now(),))
                c.execute('PRAGMA user_version=5')
                self.user(c, self.login)
                c.commit()

    def actor(self, c):
        if not c.execute("SELECT 1 FROM users WHERE role='admin'").fetchone():
            raise AccessDenied('Администратор не назначен. Выполните tools/init_admin.py на сервере.')
        row = c.execute('SELECT * FROM users WHERE login=?', (self.login,)).fetchone()
        if not row:
            raise AccessDenied('Пользователь не зарегистрирован')
        return row

    def require(self, c, action='read', board_id=None):
        actor = self.actor(c)
        if actor['role'] == 'admin':
            return actor
        allowed = action == 'read' and board_id is None
        if board_id is not None:
            board = c.execute('SELECT * FROM boards WHERE id=?', (board_id,)).fetchone()
            member = c.execute('SELECT 1 FROM board_members WHERE board_id=? AND user_id=?', (board_id, actor['id'])).fetchone()
            owner = board and board['owner_id'] == actor['id']
            allowed = bool(board and (member or owner))
            if action == 'manage':
                allowed = bool(owner and actor['role'] == 'manager')
            elif action in ('create_task', 'archive_task'):
                allowed = allowed and actor['role'] == 'manager'
        elif action == 'create_board':
            allowed = actor['role'] == 'manager'
        if not allowed:
            raise AccessDenied('Недостаточно прав или доступ к доске отозван')
        return actor

    def profile(self):
        with self.connection() as c:
            return dict(self.actor(c))

    def audit_access(self, c, operation, payload):
        c.execute('INSERT INTO access_history(actor,created_at,operation,payload) VALUES(?,?,?,?)',
                  (self.login, now(), operation, json.dumps(payload, ensure_ascii=False)))

    def initialize_admin(self, login):
        """Offline bootstrap only. Existing administrator cannot be replaced here."""
        with self.exclusive(), self.connection() as c:
            c.execute('BEGIN IMMEDIATE')
            if c.execute("SELECT 1 FROM users WHERE role='admin'").fetchone():
                raise ValueError('Администратор уже назначен. Передайте права из приложения.')
            uid = self.user(c, login)
            c.execute("UPDATE users SET role='admin' WHERE id=?", (uid,))
            c.execute('UPDATE boards SET owner_id=? WHERE owner_id IS NULL', (uid,))
            self.audit_access(c, 'initialize_admin', {'login': login})
            c.commit()
        return uid

    def access_users(self):
        with self.connection() as c:
            self.require(c, 'admin')
            return [dict(r) for r in c.execute('SELECT * FROM users ORDER BY display_name')]

    def set_role(self, user_id, role, request_id=None):
        if role not in ('admin', 'manager', 'employee'):
            raise ValueError('Неизвестная роль')
        def apply(c, uid):
            target = c.execute('SELECT * FROM users WHERE id=?', (user_id,)).fetchone()
            if not target:
                raise ValueError('Сотрудник не найден')
            if target['role'] == 'admin' and role != 'admin':
                raise ValueError('Передайте роль администратора другому сотруднику')
            if role == 'employee' and c.execute('SELECT 1 FROM boards WHERE owner_id=? AND archived=0', (user_id,)).fetchone():
                raise ValueError('Сначала передайте доски другому менеджеру')
            if role == 'admin' and target['role'] != 'admin':
                c.execute("UPDATE users SET role='manager' WHERE role='admin'")
            c.execute('UPDATE users SET role=? WHERE id=?', (role, user_id))
            self.audit_access(c, 'role', [user_id, role])
            return True
        return self.write('role', [user_id, role], apply, request_id)

    def board_access(self, board_id):
        with self.connection() as c:
            self.require(c, 'manage', board_id)
            owner = c.execute('SELECT owner_id FROM boards WHERE id=?', (board_id,)).fetchone()[0]
            rows = c.execute('SELECT u.*, EXISTS(SELECT 1 FROM board_members m WHERE m.board_id=? AND m.user_id=u.id) AS member FROM users u ORDER BY display_name', (board_id,))
            return {'owner_id': owner, 'can_transfer': self.actor(c)['role'] == 'admin', 'users': [dict(r) for r in rows]}

    def set_board_access(self, board_id, members, owner_id, request_id=None):
        members = sorted(set(members))
        def apply(c, uid):
            self.require(c, 'manage', board_id)
            board = c.execute('SELECT * FROM boards WHERE id=?', (board_id,)).fetchone()
            if owner_id != board['owner_id']:
                self.require(c, 'admin')
            owner = c.execute('SELECT role FROM users WHERE id=?', (owner_id,)).fetchone()
            if not owner or owner['role'] not in ('admin', 'manager'):
                raise ValueError('Владелец должен быть менеджером или администратором')
            for member in members:
                if not c.execute('SELECT 1 FROM users WHERE id=?', (member,)).fetchone():
                    raise ValueError('Сотрудник не найден')
            c.execute('UPDATE boards SET owner_id=? WHERE id=?', (owner_id, board_id))
            c.execute('DELETE FROM board_members WHERE board_id=?', (board_id,))
            c.executemany('INSERT INTO board_members(board_id,user_id) VALUES(?,?)', [(board_id, x) for x in members])
            self.audit_access(c, 'board_access', [board_id, members, owner_id])
            return True
        return self.write('access', [board_id, members, owner_id], apply, request_id)

    def authorize_write(self, c, operation, payload):
        self.actor(c)
        if operation in ('role', 'user'):
            self.require(c, 'admin')
        elif operation == 'board':
            self.require(c, 'create_board' if payload[1] is None else 'manage', payload[1])
        elif operation in ('columns', 'label', 'label_change', 'access'):
            self.require(c, 'manage', payload[0])
        elif operation in ('team_message', 'team_sticker', 'team_read'):
            self.chat_board(c, payload[0])
        elif operation == 'task':
            old = self.detail_in(c, payload['id']) if payload.get('id') else None
            self.require(c, 'edit' if old else 'create_task', payload['board_id'])
            if old and old['board_id'] != payload['board_id']:
                raise AccessDenied('Перенос между досками не поддерживается')
            if old and old['archived'] != bool(payload.get('archived', False)):
                self.require(c, 'archive_task', old['board_id'])
        elif operation in ('comment', 'attachment', 'read'):
            self.detail_in(c, payload[0])

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
        fingerprint = hashlib.sha256(json.dumps([self.login, payload], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        with self.exclusive(), self.connection() as c:
            c.execute('BEGIN IMMEDIATE')
            try:
                self.authorize_write(c, operation, payload)
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

    def catalogs(self, board_id):
        with self.connection() as c:
            c.execute('BEGIN')
            self.require(c, 'read', board_id)
            users = [dict(r) for r in c.execute('SELECT id,login,display_name FROM users WHERE ? IS NULL OR id IN (SELECT user_id FROM board_members WHERE board_id=?) OR id=(SELECT owner_id FROM boards WHERE id=?) OR role="admin" ORDER BY display_name COLLATE NOCASE,login', (board_id, board_id, board_id))]
            labels = [dict(r) for r in c.execute('SELECT l.id,l.name,COUNT(t.task_id) AS task_count FROM labels l LEFT JOIN task_labels t ON t.label_id=l.id WHERE l.board_id=? GROUP BY l.id ORDER BY l.name COLLATE NOCASE', (board_id,))]
            actor = self.actor(c)
            board = c.execute('SELECT owner_id FROM boards WHERE id=?', (board_id,)).fetchone()
            permissions = {'admin': actor['role'] == 'admin', 'create': actor['role'] in ('admin', 'manager'),
                           'manage': actor['role'] == 'admin' or bool(board and board[0] == actor['id'] and actor['role'] == 'manager')}
            return {'users': users, 'labels': labels, 'permissions': permissions}

    def save_user(self, login, display_name, user_id=None, request_id=None):
        login, display_name = login.strip(), display_name.strip()
        if not login or not display_name:
            raise ValueError('Укажите имя пользователя Windows и имя для отображения')
        def apply(c, uid):
            existing = c.execute('SELECT * FROM users WHERE login=?', (login,)).fetchone()
            if user_id is None:
                if existing:
                    raise ValueError('Пользователь с таким именем Windows уже есть в справочнике')
                return c.execute('INSERT INTO users(login,display_name) VALUES(?,?)', (login, display_name)).lastrowid
            if not existing or existing['id'] != user_id:
                raise ValueError('Имя пользователя Windows нельзя менять; создайте отдельного пользователя')
            c.execute('UPDATE users SET display_name=? WHERE id=?', (display_name, user_id))
            return user_id
        return self.write('user', [login, display_name, user_id], apply, request_id)

    def next_label_id(self, c):
        maximum = c.execute('SELECT COALESCE(MAX(id),0) FROM labels').fetchone()[0]
        row = c.execute("SELECT value FROM settings WHERE key='label_sequence'").fetchone()
        value = max(maximum, int(row[0]) if row else 0) + 1
        c.execute("INSERT OR REPLACE INTO settings VALUES('label_sequence',?)", (str(value),))
        return value

    def add_label(self, board_id, name, request_id=None):
        name = name.strip()
        if not name:
            raise ValueError('Введите название метки')
        def apply(c, uid):
            self.active_board(c, board_id)
            existing = c.execute('SELECT id FROM labels WHERE board_id=? AND name=?', (board_id, name)).fetchone()
            if existing:
                return existing[0]
            c.execute('DELETE FROM retired_labels WHERE board_id=? AND name=?', (board_id, name))
            return c.execute('INSERT INTO labels(id,board_id,name) VALUES(?,?,?)', (self.next_label_id(c), board_id, name)).lastrowid
        return self.write('label', [board_id, name], apply, request_id)

    def edit_label(self, board_id, label_id, name, request_id=None, expected_name=None):
        """None deletes; renaming preserves ID. Invalidate all affected task forms."""
        if name is not None:
            name = name.strip()
            if not name:
                raise ValueError('Введите название метки')
        def apply(c, uid):
            self.active_board(c, board_id)
            old = c.execute('SELECT * FROM labels WHERE id=? AND board_id=?', (label_id, board_id)).fetchone()
            if not old:
                raise ValueError('Метка удалена. Обновите справочник.')
            if expected_name is not None and old['name'] != expected_name:
                raise ValueError('Метка уже переименована. Обновите справочник.')
            if name == old['name']:
                return label_id
            if name is not None and c.execute('SELECT 1 FROM labels WHERE board_id=? AND name=? AND id<>?', (board_id, name, label_id)).fetchone():
                raise ValueError('Метка с таким названием уже существует')
            affected = [self.detail_in(c, r[0]) for r in c.execute('SELECT task_id FROM task_labels WHERE label_id=?', (label_id,)).fetchall()]
            if name is None:
                self.next_label_id(c)
                c.execute('DELETE FROM task_labels WHERE label_id=?', (label_id,))
                c.execute('DELETE FROM labels WHERE id=?', (label_id,))
            else:
                c.execute('UPDATE labels SET name=? WHERE id=?', (name, label_id))
            c.execute('INSERT OR IGNORE INTO retired_labels VALUES(?,?)', (board_id, old['name']))
            for task in affected:
                current = self.detail_in(c, task['id'])
                c.execute('UPDATE tasks SET version=version+1,updated_at=? WHERE id=?', (now(), task['id']))
                self.history(c, task['id'], uid, 'Удаление метки' if name is None else 'Переименование метки', {'labels': [task['labels'], current['labels']]})
            return label_id
        return self.write('label_change', [board_id, label_id, name, expected_name], apply, request_id)

    def chat_board(self, c, board_id):
        if board_id is None:
            actor = self.actor(c)
            available = c.execute('SELECT id FROM boards b WHERE b.archived=0 AND (?="admin" OR b.owner_id=? OR EXISTS(SELECT 1 FROM board_members m WHERE m.board_id=b.id AND m.user_id=?))', (actor["role"], actor["id"], actor["id"])).fetchall()
            if len(available) != 1:
                raise ValueError('Выберите доску для чата')
            board_id = available[0]['id']
        self.require(c, 'read', board_id)
        self.active_board(c, board_id)
        return board_id

    def chat_updates(self, after=0):
        with self.connection() as c:
            c.execute('BEGIN')
            actor = self.actor(c)
            sql = 'SELECT m.*,u.login,u.display_name AS author,b.name AS board_name FROM team_messages m JOIN users u ON u.id=m.user_id JOIN boards b ON b.id=m.board_id WHERE m.id>? AND b.archived=0 AND (?="admin" OR b.owner_id=? OR EXISTS(SELECT 1 FROM board_members x WHERE x.board_id=b.id AND x.user_id=?)) ORDER BY m.id'
            return [dict(r) for r in c.execute(sql, (after, actor['role'], actor['id'], actor['id']))]

    def team_chat(self, after=0, board_id=None):
        with self.connection() as c:
            c.execute('BEGIN')
            board_id = self.chat_board(c, board_id)
            key = 'board_read:' + json.dumps([self.login, board_id])
            row = c.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
            last = int(row[0]) if row else 0
            unread = c.execute('SELECT COUNT(*) FROM team_messages m JOIN users u ON u.id=m.user_id WHERE m.board_id=? AND m.id>? AND u.login<>?', (board_id, last, self.login)).fetchone()[0]
            messages = [dict(r) for r in c.execute('SELECT m.*,s.width,s.height,u.login,u.display_name AS author FROM team_messages m JOIN users u ON u.id=m.user_id LEFT JOIN team_stickers s ON s.message_id=m.id WHERE m.board_id=? AND m.id>? ORDER BY m.id', (board_id, after))]
            return {'messages': messages, 'unread': unread}

    def team_send(self, text, request_id=None, board_id=None):
        if not text.strip():
            raise ValueError('Введите сообщение')
        with self.connection() as c:
            board_id = self.chat_board(c, board_id)
        def apply(c, uid):
            return c.execute('INSERT INTO team_messages(user_id,body,created_at,board_id) VALUES(?,?,?,?)', (uid, text.strip(), now(), board_id)).lastrowid
        return self.write('team_message', [board_id, text], apply, request_id)

    def team_send_sticker(self, png, request_id=None, board_id=None):
        from .stickers import prepare_sticker
        with self.connection() as c:
            board_id = self.chat_board(c, board_id)
        png, width, height = prepare_sticker(png)
        digest = hashlib.sha256(png).hexdigest()
        request_id = request_id or uuid.uuid4().hex
        name = 'team-' + hashlib.sha256((self.login + ':' + request_id).encode()).hexdigest() + '.png'
        def apply(c, uid):
            target = self.folder / 'attachments' / name
            temporary = target.with_suffix('.partial')
            try:
                with temporary.open('wb') as dst:
                    dst.write(png)
                    dst.flush()
                    os.fsync(dst.fileno())
                os.replace(str(temporary), str(target))
                mid = c.execute("INSERT INTO team_messages(user_id,body,created_at,kind,board_id) VALUES(?,'',?,'sticker',?)", (uid, now(), board_id)).lastrowid
                c.execute('INSERT INTO team_stickers VALUES(?,?,?,?,?,?)', (mid, name, digest, len(png), width, height))
                return mid
            finally:
                if temporary.exists():
                    temporary.unlink()
        return self.write('team_sticker', [board_id, digest], apply, request_id)

    def team_sticker(self, message_id):
        with self.connection() as c:
            row = c.execute('SELECT s.*,m.board_id FROM team_stickers s JOIN team_messages m ON m.id=s.message_id WHERE message_id=?', (message_id,)).fetchone()
            if not row:
                raise ValueError('Стикер не найден')
            self.chat_board(c, row['board_id'])
            path = self.folder / 'attachments' / row['stored_name']
            with path.open('rb') as src:
                data = src.read(2 * 1024 * 1024 + 1)
            if len(data) != row['size'] or hashlib.sha256(data).hexdigest() != row['sha256']:
                raise IOError('Контрольная сумма стикера не совпадает')
            return data

    def team_mark_read(self, message_id, board_id=None):
        with self.connection() as c:
            board_id = self.chat_board(c, board_id)
        def apply(c, uid):
            if not c.execute('SELECT 1 FROM team_messages WHERE id=? AND board_id=?', (message_id, board_id)).fetchone():
                raise ValueError('Сообщение не принадлежит доске')
            key = 'board_read:' + json.dumps([self.login, board_id])
            old = c.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
            value = max(message_id, int(old[0]) if old else 0)
            c.execute('INSERT OR REPLACE INTO settings VALUES(?,?)', (key, str(value)))
            return value
        return self.write('team_read', [board_id, message_id], apply)

    def boards(self):
        with self.connection() as c:
            actor = self.actor(c)
            return [dict(r) for r in c.execute('SELECT * FROM boards WHERE archived=0 AND (?="admin" OR owner_id=? OR id IN (SELECT board_id FROM board_members WHERE user_id=?)) ORDER BY id', (actor['role'], actor['id'], actor['id']))]

    def next_column_id(self, c):
        maximum = c.execute('SELECT COALESCE(MAX(id),0) FROM columns').fetchone()[0]
        saved = c.execute("SELECT value FROM settings WHERE key='column_sequence'").fetchone()
        value = max(maximum, int(saved[0]) if saved else 0) + 1
        c.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('column_sequence',?)", (str(value),))
        return value

    def board_action(self, name, board_id=None, archive=False, request_id=None, column_names=None):
        if not name.strip() and not archive:
            raise ValueError('Введите название доски')
        if column_names is not None and (not column_names or any(not x.strip() for x in column_names)):
            raise ValueError('Укажите названия колонок')
        def apply(c, uid):
            if board_id is None:
                bid = c.execute('INSERT INTO boards(name,created_at) VALUES(?,?)', (name.strip(), now())).lastrowid
                c.execute('UPDATE boards SET owner_id=? WHERE id=?', (uid, bid))
                names = column_names if column_names is not None else ['Входящие', 'Запланировано', 'В работе', 'На проверке', 'Завершено']
                for i, title in enumerate(names):
                    c.execute('INSERT INTO columns(id,board_id,name,position,is_done) VALUES(?,?,?,?,?)', (self.next_column_id(c), bid, title, i, int(i == len(names)-1)))
                return bid
            if not c.execute('SELECT id FROM boards WHERE id=? AND archived=0', (board_id,)).fetchone():
                raise ValueError('Доска недоступна')
            c.execute('UPDATE boards SET name=?,archived=? WHERE id=?', (name.strip(), int(archive), board_id))
            return board_id
        return self.write('board', [name, board_id, archive] + ([column_names] if column_names is not None else []), apply, request_id)

    def columns(self, board_id):
        with self.connection() as c:
            self.require(c, 'read', board_id)
            return [dict(r) for r in c.execute('SELECT * FROM columns WHERE board_id=? ORDER BY position,id', (board_id,))]

    def save_columns(self, board_id, columns, request_id=None, removed=None):
        removed = {int(k): int(v) for k, v in (removed or {}).items()}
        if not columns or any(not x['name'].strip() for x in columns):
            raise ValueError('Укажите названия колонок')
        if sum(bool(x['is_done']) for x in columns) > 1:
            raise ValueError('Выберите одну колонку завершения')
        def apply(c, uid):
            self.active_board(c, board_id)
            existing = {r['id'] for r in c.execute('SELECT id FROM columns WHERE board_id=?', (board_id,))}
            supplied = [x['id'] for x in columns if x.get('id')]
            if (set(supplied) | set(removed)) != existing or len(supplied) != len(set(supplied)) or set(supplied) & set(removed):
                raise ValueError('Набор колонок изменился. Откройте настройки заново.')
            if any(target not in supplied for target in removed.values()):
                raise ValueError('Выберите оставшуюся колонку для переноса задач')
            if removed:
                # Preserve the high-water mark before deleting IDs; stale forms must
                # never point to a different newly created column with a reused ID.
                self.next_column_id(c)
            for source, target in removed.items():
                source_name = c.execute('SELECT name FROM columns WHERE id=?', (source,)).fetchone()[0]
                target_name = next(x['name'] for x in columns if x.get('id') == target)
                position = c.execute('SELECT COALESCE(MAX(position),0) FROM tasks WHERE column_id=?', (target,)).fetchone()[0]
                for row in c.execute('SELECT id FROM tasks WHERE column_id=? ORDER BY position,id', (source,)).fetchall():
                    position += 1
                    c.execute('UPDATE tasks SET column_id=?,position=?,updated_at=?,version=version+1 WHERE id=?', (target, position, now(), row['id']))
                    self.history(c, row['id'], uid, 'Удаление колонки и перенос задач', {'column_name': [source_name, target_name]})
                c.execute('DELETE FROM columns WHERE id=?', (source,))
            for i, x in enumerate(columns):
                if x.get('id'):
                    c.execute('UPDATE columns SET name=?,position=?,is_done=?,wip_limit=? WHERE id=?', (x['name'].strip(), i, int(x['is_done']), x.get('wip_limit'), x['id']))
                else:
                    c.execute('INSERT INTO columns(id,board_id,name,position,is_done,wip_limit) VALUES(?,?,?,?,?,?)', (self.next_column_id(c), board_id, x['name'].strip(), i, int(x['is_done']), x.get('wip_limit')))
            # Completion semantics also apply when the completion column is reconfigured.
            for row in c.execute('SELECT t.*, col.is_done FROM tasks t JOIN columns col ON col.id=t.column_id WHERE t.board_id=?', (board_id,)).fetchall():
                completed = row['completed_at'] or now() if row['is_done'] else None
                if completed != row['completed_at']:
                    c.execute('UPDATE tasks SET completed_at=?,updated_at=?,version=version+1 WHERE id=?', (completed, now(), row['id']))
                    self.history(c, row['id'], uid, 'Изменение колонки завершения', {'completed_at': [row['completed_at'], completed]})
            return True
        return self.write('columns', [board_id, columns, removed], apply, request_id)

    def active_board(self, c, board_id):
        if not c.execute('SELECT 1 FROM boards WHERE id=? AND archived=0', (board_id,)).fetchone():
            raise ValueError('Доска архивирована или отсутствует')

    def detail_in(self, c, task_id):
        row = c.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone()
        if not row:
            raise ValueError('Задача не найдена')
        self.require(c, 'read', row['board_id'])
        d = dict(row)
        d['assignees'] = [r[0] for r in c.execute('SELECT u.login FROM task_assignees a JOIN users u ON u.id=a.user_id WHERE a.task_id=? ORDER BY u.login', (task_id,))]
        d['assignee_names'] = [r[0] for r in c.execute('SELECT u.display_name FROM task_assignees a JOIN users u ON u.id=a.user_id WHERE a.task_id=? ORDER BY u.login', (task_id,))]
        d['labels'] = [r[0] for r in c.execute('SELECT l.name FROM task_labels a JOIN labels l ON l.id=a.label_id WHERE a.task_id=? ORDER BY l.name', (task_id,))]
        d['checklist'] = [dict(r) for r in c.execute('SELECT text,done FROM checklist_items WHERE task_id=? ORDER BY position', (task_id,))]
        for key, query in [('comments', 'SELECT x.*,u.login AS login,u.display_name AS author FROM comments x JOIN users u ON u.id=x.user_id WHERE task_id=? ORDER BY x.id'), ('history', 'SELECT x.*,u.display_name AS author FROM task_history x JOIN users u ON u.id=x.user_id WHERE task_id=? ORDER BY x.id DESC'), ('attachments', 'SELECT * FROM attachments WHERE task_id=? ORDER BY id')]:
            d[key] = [dict(r) for r in c.execute(query, (task_id,))]
        key = 'read:' + json.dumps([self.login, task_id], ensure_ascii=False)
        cursor = c.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
        last = int(cursor[0]) if cursor else 0
        d['unread_count'] = sum(x['id'] > last and x['login'] != self.login for x in d['comments'])
        return d

    def mark_read(self, task_id, comment_id):
        def apply(c, uid):
            if not c.execute('SELECT 1 FROM comments WHERE task_id=? AND id=?', (task_id, comment_id)).fetchone():
                raise ValueError('Сообщение не принадлежит задаче')
            key = 'read:' + json.dumps([self.login, task_id], ensure_ascii=False)
            old = c.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
            value = max(int(old[0]) if old else 0, comment_id)
            c.execute('INSERT OR REPLACE INTO settings VALUES(?,?)', (key, str(value)))
            return value
        return self.write('read', [task_id, comment_id], apply)

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
            self.require(c, 'read', board_id)
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
                person = c.execute('SELECT * FROM users WHERE login=?', (name,)).fetchone()
                if not person:
                    raise ValueError('Сначала добавьте исполнителя в справочник сотрудников')
                if person['role'] != 'admin' and not c.execute('SELECT 1 FROM board_members WHERE board_id=? AND user_id=?', (bid, person['id'])).fetchone() and c.execute('SELECT owner_id FROM boards WHERE id=?', (bid,)).fetchone()[0] != person['id']:
                    raise ValueError('Исполнителю необходимо выдать доступ к доске')
                c.execute('INSERT INTO task_assignees VALUES(?,?)', (tid, person['id']))
            c.execute('DELETE FROM task_labels WHERE task_id=?', (tid,))
            for name in sorted(set(data.get('labels', []))):
                if name.strip():
                    if not c.execute('SELECT 1 FROM labels WHERE board_id=? AND name=?', (bid, name.strip())).fetchone():
                        if c.execute('SELECT 1 FROM retired_labels WHERE board_id=? AND name=?', (bid, name.strip())).fetchone():
                            raise ValueError('Метка удалена или переименована. Обновите форму задачи.')
                        self.require(c, 'manage', bid)
                    c.execute('INSERT OR IGNORE INTO labels(id,board_id,name) VALUES(?,?,?)', (self.next_label_id(c), bid, name.strip()))
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
            self.detail_in(c, r['task_id'])
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
            self.require(c, 'admin')
            target.mkdir()
            with sqlite3.connect(str(target / 'kanban.db')) as dest:
                c.backup(dest)
                if dest.execute('PRAGMA integrity_check').fetchone()[0] != 'ok' or dest.execute('PRAGMA foreign_key_check').fetchall():
                    raise IOError('Проверка резервной базы не пройдена')
            (target / 'attachments').mkdir()
            manifest = {}
            for r in c.execute('SELECT stored_name,sha256 FROM attachments UNION ALL SELECT stored_name,sha256 FROM team_stickers'):
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
