import copy
from pathlib import Path
import tempfile
import unittest
from kanban.store import Store, AccessDenied, Conflict
from test_stickers import image_bytes


class BoardChatLabelsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.admin = Store(Path(self.tmp.name) / 'data', 'admin')
        self.admin.initialize_admin('admin')
        self.a = self.admin.board_action('A')
        self.b = self.admin.board_action('B')
        self.uid = self.admin.save_user('employee', 'Сотрудник')
        self.admin.set_board_access(self.a, [self.uid], self.admin.profile()['id'])
        self.employee = Store(self.admin.folder, 'employee')

    def test_chat_isolation_and_sticker_access(self):
        one = self.admin.team_send('Только A', board_id=self.a)
        two = self.admin.team_send('Только B', board_id=self.b)
        photo = self.admin.team_send_sticker(image_bytes(), board_id=self.b)
        self.assertEqual([m['id'] for m in self.employee.team_chat(board_id=self.a)['messages']], [one])
        self.assertEqual([m['id'] for m in self.employee.chat_updates()], [one])
        for action in (lambda: self.employee.team_chat(board_id=self.b),
                       lambda: self.employee.team_send('Нет', board_id=self.b),
                       lambda: self.employee.team_send_sticker(image_bytes(), board_id=self.b),
                       lambda: self.employee.team_sticker(photo)):
            with self.assertRaises(AccessDenied):
                action()
        with self.assertRaises(ValueError):
            self.employee.team_mark_read(two, board_id=self.a)
        self.admin.team_mark_read(one, board_id=self.a)
        self.assertEqual(self.employee.team_chat(board_id=self.a)['unread'], 1)
        self.employee.team_mark_read(one, board_id=self.a)
        self.assertEqual(self.employee.team_chat(board_id=self.a)['unread'], 0)
        self.admin.set_board_access(self.a, [], self.admin.profile()['id'])
        self.assertEqual(self.employee.chat_updates(), [])
        with self.assertRaises(AccessDenied):
            self.employee.team_chat(board_id=self.a)

    def test_retries_are_bound_to_board(self):
        mid = self.admin.team_send('Текст', 'same', board_id=self.a)
        self.assertEqual(mid, self.admin.team_send('Текст', 'same', board_id=self.a))
        with self.assertRaises(ValueError):
            self.admin.team_send('Текст', 'same', board_id=self.b)

    def test_label_rename_delete_updates_tasks_history_and_conflicts(self):
        lid = self.admin.add_label(self.a, 'Старая')
        tid = self.admin.save_task({'board_id': self.a, 'column_id': self.admin.columns(self.a)[0]['id'], 'title': 'Задача', 'labels': ['Старая']})['id']
        stale = self.admin.detail(tid)
        self.admin.edit_label(self.a, lid, 'Новая', 'rename', 'Старая')
        self.admin.edit_label(self.a, lid, 'Новая', 'rename', 'Старая')
        self.assertEqual(self.admin.detail(tid)['labels'], ['Новая'])
        self.assertEqual(self.admin.detail(tid)['version'], stale['version'] + 1)
        self.assertEqual(self.admin.catalogs(self.a)['labels'][0]['id'], lid)
        with self.assertRaises(Conflict):
            self.admin.save_task(stale)
        self.admin.edit_label(self.a, lid, None, 'delete', 'Новая')
        self.admin.edit_label(self.a, lid, None, 'delete', 'Новая')
        self.assertEqual(self.admin.detail(tid)['labels'], [])
        self.assertEqual(len(self.admin.detail(tid)['history']), 3)
        self.assertEqual(self.admin.catalogs(self.a)['labels'], [])
        with self.assertRaises(ValueError):
            self.admin.save_task({'board_id': self.a, 'column_id': stale['column_id'], 'title': 'Старая форма', 'labels': ['Новая']})
        replacement = self.admin.add_label(self.a, 'Новая')
        self.assertGreater(replacement, lid)
        with self.admin.connection() as c:
            self.assertFalse(c.execute('PRAGMA foreign_key_check').fetchall())

    def test_label_changes_require_owner_and_validate_scope(self):
        lid = self.admin.add_label(self.a, 'Одна')
        self.admin.add_label(self.a, 'Другая')
        with self.assertRaises(AccessDenied):
            self.employee.edit_label(self.a, lid, None)
        for action in (lambda: self.admin.edit_label(self.b, lid, None),
                       lambda: self.admin.edit_label(self.a, lid, 'Другая'),
                       lambda: self.admin.edit_label(self.a, lid, '   '),
                       lambda: self.admin.edit_label(self.a, lid, 'Новая', expected_name='Устаревшая')):
            with self.assertRaises(ValueError):
                action()
        self.assertEqual(self.admin.catalogs(self.a)['labels'][0]['task_count'], 0)
