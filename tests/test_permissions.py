from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from kanban.store import Store, SCHEMA, AccessDenied, AccessRestricted


class PermissionsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.admin = Store(self.root / 'data', 'admin')
        self.admin.initialize_admin('admin')
        self.mid = self.admin.save_user('manager', 'Менеджер')
        self.eid = self.admin.save_user('employee', 'Сотрудник')
        self.admin.set_role(self.mid, 'manager')
        self.manager = Store(self.admin.folder, 'manager')
        self.employee = Store(self.admin.folder, 'employee')
        self.bid = self.manager.board_action('Доска менеджера')
        self.manager.set_board_access(self.bid, [self.eid], self.mid)
        self.task = self.manager.save_task({'board_id': self.bid, 'column_id': self.manager.columns(self.bid)[0]['id'], 'title': 'Задача'})

    def test_restrict_and_restore_app_access(self):
        tid = self.task['id']
        admin_id = self.admin.profile()['id']
        for client in (self.employee, self.manager):
            with self.assertRaises(AccessDenied):
                client.set_user_restricted(self.eid, True)
        with self.assertRaises(ValueError):
            self.admin.set_user_restricted(admin_id, True)
        self.assertTrue(self.admin.set_user_restricted(self.eid, True, 'restrict'))
        self.assertTrue(self.admin.set_user_restricted(self.eid, True, 'restrict'))
        with self.assertRaises(AccessRestricted):
            Store(self.admin.folder, 'employee')
        for action in (self.employee.boards, lambda: self.employee.detail(tid),
                       lambda: self.employee.notification_snapshot(0), self.employee.timezone,
                       lambda: self.employee.comment(tid, 'Запись после блокировки')):
            with self.assertRaises(AccessRestricted):
                action()
        with self.assertRaises(ValueError):
            self.admin.set_role(self.eid, 'admin')
        self.assertFalse(self.admin.set_user_restricted(self.eid, False))
        self.assertEqual(self.employee.profile()['role'], 'employee')
        self.assertEqual(self.employee.detail(tid)['title'], 'Задача')
        self.assertEqual(Store(self.admin.folder, 'employee').boards()[0]['id'], self.bid)
        with self.admin.connection() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM access_history WHERE operation='user_restricted'").fetchone()[0], 2)

    def test_employee_can_edit_but_not_create_archive_or_manage(self):
        task = self.employee.detail(self.task['id'])
        task['title'] = 'Правка сотрудника'
        self.employee.save_task(task)
        for operation in (lambda: self.employee.board_action('Новая'),
                          lambda: self.employee.save_task({'board_id': self.bid, 'column_id': task['column_id'], 'title': 'Новая'}),
                          lambda: self.employee.set_board_access(self.bid, [], self.mid),
                          lambda: self.employee.add_label(self.bid, 'Новая'),
                          lambda: self.employee.save_user('other', 'Другой'),
                          lambda: self.employee.backup(self.root / 'backups')):
            with self.assertRaises(AccessDenied):
                operation()
        task = self.employee.detail(self.task['id'])
        task['archived'] = True
        with self.assertRaises(AccessDenied):
            self.employee.save_task(task)
        task['archived'] = False
        task['labels'] = ['Новая']
        with self.assertRaises(AccessDenied):
            self.employee.save_task(task)

    def test_revoke_covers_read_write_attachment_and_retries(self):
        source = self.root / 'source.txt'
        source.write_text('secret')
        aid = self.manager.attach(self.task['id'], source)
        stale = self.employee.detail(self.task['id'])
        self.employee.comment(self.task['id'], 'Сообщение', 'same-request')
        self.manager.set_board_access(self.bid, [], self.mid)
        self.assertEqual(self.employee.boards(), [])
        for operation in (lambda: self.employee.detail(self.task['id']),
                          lambda: self.employee.tasks(self.bid),
                          lambda: self.employee.columns(self.bid),
                          lambda: self.employee.catalogs(self.bid),
                          lambda: self.employee.download(aid, self.root / 'leak'),
                          lambda: self.employee.save_task(stale),
                          lambda: self.employee.comment(self.task['id'], 'Сообщение', 'same-request')):
            with self.assertRaises(AccessDenied):
                operation()
        self.assertFalse((self.root / 'leak').exists())

    def test_manager_only_owns_own_boards_and_cannot_promote(self):
        other = self.admin.board_action('Административная')
        with self.assertRaises(AccessDenied):
            self.manager.board_access(other)
        self.admin.set_board_access(other, [self.mid], self.admin.profile()['id'])
        self.manager.save_task({'board_id': other, 'column_id': self.admin.columns(other)[0]['id'], 'title': 'Доступная'})
        with self.assertRaises(AccessDenied):
            self.manager.board_action('Чужая', other)
        with self.assertRaises(AccessDenied):
            self.manager.set_role(self.eid, 'manager')
        with self.assertRaises(AccessDenied):
            self.manager.set_board_access(self.bid, [], self.admin.profile()['id'])

    def test_single_admin_transfer_and_no_reinitialization(self):
        with self.assertRaises(ValueError):
            self.admin.initialize_admin('employee')
        with self.assertRaises(ValueError):
            self.admin.set_role(self.admin.profile()['id'], 'employee')
        self.admin.set_role(self.eid, 'admin')
        self.assertEqual(self.admin.profile()['role'], 'manager')
        self.assertEqual(self.employee.profile()['role'], 'admin')
        with self.assertRaises(AccessDenied):
            self.admin.set_role(self.mid, 'employee')
        with self.employee.connection() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM users WHERE role='admin'").fetchone()[0], 1)
            with self.assertRaises(sqlite3.IntegrityError):
                c.execute("UPDATE users SET role='admin' WHERE id=?", (self.mid,))

    def test_requests_are_bound_to_actor(self):
        self.manager.comment(self.task['id'], 'Текст', 'request')
        with self.assertRaises(ValueError):
            self.employee.comment(self.task['id'], 'Текст', 'request')

    def test_bootstrap_cli_keeps_existing_data_and_refuses_second_admin(self):
        script = Path(__file__).resolve().parent.parent / 'tools/init_admin.py'
        missing = subprocess.run([sys.executable, str(script), '--data', str(self.root / 'missing'), '--account', 'someone'], capture_output=True)
        self.assertNotEqual(missing.returncode, 0)
        self.assertFalse((self.root / 'missing').exists())
        folder = self.root / 'fresh'
        Store(folder, 'old-user')
        result = subprocess.run([sys.executable, str(script), '--data', str(folder), '--account', 'old-user'], capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        result = subprocess.run([sys.executable, str(script), '--data', str(folder), '--account', 'intruder'], capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(Store(folder, 'old-user').profile()['role'], 'admin')

    def test_v2_migration_preserves_data_and_requires_explicit_admin(self):
        folder = self.root / 'legacy'
        folder.mkdir()
        with sqlite3.connect(folder / 'kanban.db') as c:
            c.executescript(SCHEMA)
            c.execute("INSERT INTO users VALUES(7,'legacy','Прежний сотрудник')")
            c.execute("INSERT INTO boards VALUES(9,'Старая доска',0,'2026-01-01')")
            c.execute('CREATE TABLE team_messages(id INTEGER PRIMARY KEY,user_id INTEGER,body TEXT,created_at TEXT)')
            c.execute("INSERT INTO team_messages VALUES(1,7,'Переписка','2026-01-01')")
            c.execute('PRAGMA user_version=2')
        migrated = Store(folder, 'legacy')
        with self.assertRaises(AccessDenied):
            migrated.boards()
        migrated.initialize_admin('legacy')
        self.assertEqual(migrated.profile()['id'], 7)
        self.assertEqual(migrated.boards()[0]['id'], 9)
        self.assertEqual(migrated.team_chat(board_id=next(b['id'] for b in migrated.boards() if b['name'] == 'С-А'))['messages'][0]['body'], 'Переписка')
        with migrated.connection() as c:
            self.assertEqual(c.execute('PRAGMA user_version').fetchone()[0], 14)
            self.assertFalse(c.execute('PRAGMA foreign_key_check').fetchall())
