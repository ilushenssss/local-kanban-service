from pathlib import Path
import sqlite3
import tempfile
import unittest
from kanban.store import Store, SCHEMA, AccessDenied
from test_stickers import image_bytes


class PersonalBoardsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.admin = Store(self.root / 'data', 'admin')
        self.admin.initialize_admin('admin')
        self.uid = self.admin.save_user('alice', 'Алиса')
        self.bob_id = self.admin.save_user('bob', 'Борис')
        self.alice = Store(self.admin.folder, 'alice')
        self.bob = Store(self.admin.folder, 'bob')
        self.bid = self.alice.board_action('Личные планы', personal=True)

    def test_employee_manages_personal_board_without_work_permissions(self):
        self.assertEqual(self.alice.profile()['role'], 'employee')
        with self.assertRaises(AccessDenied):
            self.alice.board_action('Рабочая')
        board = self.alice.boards()[0]
        self.assertEqual((board['owner_id'], board['personal']), (self.uid, 1))
        self.alice.board_action('Мои планы', self.bid)
        columns = self.alice.columns(self.bid)
        columns[0]['name'] = 'Скоро'
        self.alice.save_columns(self.bid, columns)
        label = self.alice.add_label(self.bid, 'Важное')
        task = self.alice.save_task({'board_id': self.bid, 'column_id': columns[0]['id'], 'title': 'Личное дело', 'assignees': ['alice'], 'labels': ['Важное']})
        current = self.alice.detail(task['id'])
        current['archived'] = True
        self.alice.save_task(current)
        self.alice.edit_label(self.bid, label, 'Для себя')
        self.assertEqual(self.alice.detail(task['id'])['labels'], ['Для себя'])
        catalogs = self.alice.catalogs(self.bid)
        self.assertEqual([u['login'] for u in catalogs['users']], ['alice'])
        self.assertTrue(catalogs['permissions']['create'])
        self.assertTrue(catalogs['permissions']['manage'])
        self.assertFalse(catalogs['permissions']['share'])
        self.assertFalse(catalogs['permissions']['create_board'])

    def test_private_data_hidden_from_everyone_including_admin(self):
        columns = self.alice.columns(self.bid)
        task = self.alice.save_task({'board_id': self.bid, 'column_id': columns[0]['id'], 'title': 'Секрет'})['id']
        source = self.root / 'secret.txt'
        source.write_text('private attachment')
        attachment = self.alice.attach(task, source)
        self.alice.team_send('Личная заметка', board_id=self.bid)
        sticker = self.alice.team_send_sticker(image_bytes(), board_id=self.bid)
        for other in (self.bob, self.admin):
            self.assertEqual(other.boards(), [])
            self.assertEqual(other.chat_updates(), [])
            operations = (
                lambda: other.columns(self.bid), lambda: other.tasks(self.bid),
                lambda: other.detail(task), lambda: other.catalogs(self.bid),
                lambda: other.team_chat(board_id=self.bid), lambda: other.team_sticker(sticker),
                lambda: other.comment(task, 'Чужой'), lambda: other.team_send('Чужой', board_id=self.bid),
                lambda: other.board_action('Чужая правка', self.bid),
                lambda: other.download(attachment, self.root / 'leak.txt'),
                lambda: other.board_access(self.bid),
                lambda: other.set_board_access(self.bid, [self.bob_id], self.bob_id),
            )
            for operation in operations:
                with self.assertRaises(AccessDenied):
                    operation()
        self.assertFalse((self.root / 'leak.txt').exists())

    def test_cannot_share_assign_other_or_gain_access_via_membership(self):
        for action in (lambda: self.alice.board_access(self.bid),
                       lambda: self.alice.set_board_access(self.bid, [self.bob_id], self.uid),
                       lambda: self.alice.save_task({'board_id': self.bid, 'column_id': self.alice.columns(self.bid)[0]['id'], 'title': 'Обход', 'assignees': ['admin']})):
            with self.assertRaises(AccessDenied):
                action()
        self.assertEqual(self.alice.tasks(self.bid), [])
        # Even a stale/incorrect membership row cannot make a private board visible.
        with self.admin.connection() as c:
            c.execute('INSERT INTO board_members(board_id,user_id) VALUES(?,?)', (self.bid, self.bob_id))
            c.commit()
        self.assertEqual(self.bob.boards(), [])
        with self.assertRaises(AccessDenied):
            self.bob.tasks(self.bid)

    def test_archive_restore_and_role_changes_preserve_privacy(self):
        self.alice.board_action('Личные планы', self.bid, archive=True)
        self.assertEqual(self.alice.boards(), [])
        self.assertEqual(self.alice.archived_personal_boards()[0]['id'], self.bid)
        self.assertEqual(self.admin.archived_personal_boards(), [])
        with self.assertRaises(AccessDenied):
            self.admin.restore_personal_board(self.bid)
        self.alice.restore_personal_board(self.bid, 'restore')
        self.alice.restore_personal_board(self.bid, 'restore')
        self.admin.set_role(self.uid, 'manager')
        self.admin.set_role(self.uid, 'employee')
        self.assertEqual(self.alice.boards()[0]['owner_id'], self.uid)
        self.assertEqual(self.admin.boards(), [])
        self.assertEqual(self.alice.board_action('Повтор', personal=True, request_id='create'), self.alice.board_action('Повтор', personal=True, request_id='create'))

    def test_v5_migration_keeps_all_existing_boards_shared(self):
        folder = self.root / 'legacy'
        folder.mkdir()
        with sqlite3.connect(folder / 'kanban.db') as c:
            c.executescript(SCHEMA)
            c.execute("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'employee'")
            c.execute('ALTER TABLE boards ADD COLUMN owner_id INTEGER REFERENCES users(id)')
            c.execute("INSERT INTO users VALUES(1,'admin','Администратор','admin')")
            c.execute("INSERT INTO boards VALUES(10,'Рабочая',0,'2026-01-01',1)")
            c.execute('CREATE TABLE team_messages(id INTEGER PRIMARY KEY,user_id INTEGER REFERENCES users(id),body TEXT,created_at TEXT,kind TEXT,board_id INTEGER REFERENCES boards(id))')
            c.execute('PRAGMA user_version=5')
        migrated = Store(folder, 'admin')
        self.assertEqual(migrated.boards()[0]['personal'], 0)
        self.assertEqual(migrated.boards()[0]['name'], 'Рабочая')
        with migrated.connection() as c:
            self.assertEqual(c.execute('PRAGMA user_version').fetchone()[0], 11)
            self.assertFalse(c.execute('PRAGMA foreign_key_check').fetchall())
