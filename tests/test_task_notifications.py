import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from kanban.store import Store, Conflict, AccessDenied
from kanban.presentation import task_notification_text


class TaskNotificationsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.creator = Store(self.root / 'data', 'creator')
        self.creator.initialize_admin('creator')
        self.aid = self.creator.save_user('alice', 'Алиса')
        self.bid = self.creator.save_user('bob', 'Борис')
        self.oid = self.creator.save_user('observer', 'Наблюдатель')
        self.board = self.creator.board_action('Рабочая доска')
        self.creator.set_board_access(self.board, [self.aid, self.bid, self.oid], self.creator.profile()['id'])
        self.alice = Store(self.creator.folder, 'alice')
        self.bob = Store(self.creator.folder, 'bob')
        self.observer = Store(self.creator.folder, 'observer')
        self.column = self.creator.columns(self.board)[0]['id']

    def create(self):
        return self.creator.save_task({'board_id': self.board, 'column_id': self.column, 'title': 'Договор', 'assignees': ['alice']}, 'create')['id']

    def test_mention_subscription_deduplication_and_access(self):
        task = self.create()
        self.alice.subscribe(task, True)
        self.bob.subscribe(task, True)
        self.assertTrue(Store(self.creator.folder, 'bob').detail(task)['subscribed'])
        outsider_id = self.creator.save_user('hidden', 'Скрытый')
        outsider = Store(self.creator.folder, 'hidden')
        with self.assertRaises(AccessDenied):
            outsider.subscribe(task, True)
        before = self.alice.task_updates()['cursor']
        for _ in range(2):
            self.creator.comment(task, '@alice @alice @[observer] @hidden', 'mentions')
        alice = self.alice.task_updates(before)['events']
        self.assertEqual(len(alice), 1)
        self.assertIn('Вас упомянули', task_notification_text(alice[0], 'alice'))
        self.assertEqual(len(self.bob.task_updates(before)['events']), 1)
        self.assertEqual(len(self.observer.notification_snapshot(0)['tasks']['events']), 1)
        with self.creator.connection() as c:
            self.assertIsNone(c.execute('SELECT 1 FROM task_notifications WHERE user_id=?', (outsider_id,)).fetchone())
        self.alice.mark_notification_read('task', alice[0]['id'])
        self.assertEqual(self.alice.task_updates(before)['events'], [])
        self.assertEqual(self.alice.board_badges()[self.board]['tasks'], 1)
        bob_notice = self.bob.task_updates(before)['events'][0]['id']
        self.creator.set_board_access(self.board, [self.aid, self.oid], self.creator.profile()['id'])
        self.assertFalse(self.bob.notification_snapshot(0)['tasks']['events'])
        with self.assertRaises(AccessDenied):
            self.bob.mark_notification_read('task', bob_notice)
        self.creator.comment(task, '@bob Больше нет доступа')
        self.creator.set_board_access(self.board, [self.aid, self.bid, self.oid], self.creator.profile()['id'])
        self.assertEqual(len(self.bob.task_updates(before)['events']), 1)
        self.bob.subscribe(task, False)
        cursor = self.bob.task_updates()['cursor']
        current = self.creator.detail(task)
        self.creator.save_task(dict(current, title='Новое название'))
        self.assertFalse(self.bob.task_updates(cursor)['events'])

    def test_assignment_creator_and_removed_assignee_recipients(self):
        baseline = self.alice.task_updates()['cursor']
        task = self.create()
        assigned = self.alice.task_updates(baseline)
        self.assertEqual(len(assigned['events']), 1)
        self.assertIn('Вам назначена задача', task_notification_text(assigned['events'][0], 'alice'))
        self.assertNotIn('восстановлена', task_notification_text(assigned['events'][0], 'alice'))
        self.assertEqual(self.creator.task_updates(0)['events'], [])
        self.assertEqual(self.observer.task_updates(0)['events'], [])
        self.assertEqual(self.bob.task_updates(0)['events'], [])
        current = self.alice.detail(task)
        current['priority'] = 'Высокий'
        self.alice.save_task(current, 'priority')
        created = self.creator.task_updates(0)['events']
        self.assertEqual(len(created), 1)
        self.assertIn('приоритет', task_notification_text(created[0], 'creator'))
        current = self.creator.detail(task)
        current['assignees'] = ['bob']
        self.creator.save_task(current, 'reassign')
        removed = self.alice.task_updates(assigned['cursor'])['events']
        self.assertEqual(len(removed), 1)
        self.assertIn('Вы сняты с задачи', task_notification_text(removed[0], 'alice'))
        self.assertIn('Вам назначена задача', task_notification_text(self.bob.task_updates(0)['events'][0], 'bob'))
        self.assertEqual(self.observer.task_updates(0)['events'], [])

    def test_parameter_status_comment_attachment_and_no_duplicate(self):
        task = self.create()
        baseline = self.alice.task_updates()['cursor']
        current = self.creator.detail(task)
        current.update(column_id=self.creator.columns(self.board)[-1]['id'], due_date='2027-01-15', description='Описание', checklist=[{'text': 'Проверить', 'done': 1}])
        self.creator.save_task(current, 'changes')
        self.creator.save_task(current, 'changes')
        self.creator.comment(task, 'Комментарий', 'comment')
        source = self.root / 'file.txt'
        source.write_text('file')
        self.creator.attach(task, source, 'attachment')
        events = self.alice.task_updates(baseline)['events']
        self.assertEqual(len(events), 3)
        text = task_notification_text(events[0], 'alice')
        self.assertIn('статус', text)
        self.assertIn(self.creator.columns(self.board)[-1]['name'], text)
        self.assertIn('срок', text)
        self.assertIn('чек-лист', text)
        self.assertIn('комментарий', task_notification_text(events[1], 'alice'))
        self.assertIn('вложение', task_notification_text(events[2], 'alice'))
        cursor = self.alice.task_updates()['cursor']
        unchanged = self.creator.detail(task)
        self.creator.save_task(unchanged)
        unchanged = self.creator.detail(task)
        unchanged['position'] += 10
        self.creator.save_task(unchanged)
        with self.assertRaises(Conflict):
            self.creator.save_task(unchanged)
        self.assertEqual(self.alice.task_updates(cursor)['events'], [])
        self.assertEqual(self.alice.task_updates()['events'], [])

    def test_access_revocation_archive_and_personal_privacy(self):
        self.create()
        self.creator.set_board_access(self.board, [self.bid], self.creator.profile()['id'])
        self.assertEqual(self.alice.task_updates(0)['events'], [])
        # Archived boards do not generate visible notifications.
        self.creator.set_board_access(self.board, [self.aid, self.bid], self.creator.profile()['id'])
        self.creator.board_action('Рабочая доска', self.board, archive=True)
        self.assertEqual(self.alice.task_updates(0)['events'], [])
        private = self.alice.board_action('Личное', personal=True)
        tid = self.alice.save_task({'board_id': private, 'column_id': self.alice.columns(private)[0]['id'], 'title': 'Личная', 'assignees': ['alice']})['id']
        # Even a malformed historical recipient cannot bypass owner-only visibility.
        with self.creator.connection() as c:
            hid = c.execute('SELECT id FROM task_history WHERE task_id=?', (tid,)).fetchone()[0]
            c.execute('INSERT INTO task_notifications(history_id,user_id) VALUES(?,?)', (hid, self.creator.profile()['id']))
            c.commit()
        self.assertEqual(self.creator.task_updates(0)['events'], [])
        self.assertEqual(self.bob.task_updates(0)['events'], [])

    def test_failed_transaction_rolls_back_notification_and_retry_is_once(self):
        task = self.create()
        cursor = self.alice.task_updates()['cursor']
        current = self.creator.detail(task)
        current['priority'] = 'Высокий'
        original = self.creator.history
        def fail(*args):
            original(*args)
            raise OSError('Connection failed before commit')
        with patch.object(self.creator, 'history', side_effect=fail):
            with self.assertRaises(OSError):
                self.creator.save_task(current, 'transaction')
        self.assertEqual(self.alice.task_updates(cursor)['events'], [])
        self.assertEqual(self.creator.detail(task)['priority'], 'Обычный')
        self.creator.save_task(current, 'transaction')
        self.creator.save_task(current, 'transaction')
        self.assertEqual(len(self.alice.task_updates(cursor)['events']), 1)

    def test_v6_upgrade_keeps_history_without_replaying_old_events(self):
        task = self.create()
        with self.creator.connection() as c:
            count = c.execute('SELECT COUNT(*) FROM task_history').fetchone()[0]
            c.execute('DROP TABLE task_notifications')
            c.execute('DELETE FROM schema_migrations WHERE version=7')
            c.execute('PRAGMA user_version=6')
            c.commit()
        upgraded = Store(self.creator.folder, 'alice')
        self.assertEqual(upgraded.task_updates(0)['events'], [])
        with upgraded.connection() as c:
            self.assertEqual(c.execute('PRAGMA user_version').fetchone()[0], 13)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM task_history').fetchone()[0], count)
            self.assertFalse(c.execute('PRAGMA foreign_key_check').fetchall())
        current = self.creator.detail(task)
        current['priority'] = 'Высокий'
        self.creator.save_task(current)
        self.assertEqual(len(upgraded.task_updates(0)['events']), 1)

    def test_label_changes_notify_once(self):
        task = self.create()
        lid = self.creator.add_label(self.board, 'Метка')
        current = self.creator.detail(task)
        current['labels'] = ['Метка']
        self.creator.save_task(current)
        cursor = self.alice.task_updates()['cursor']
        self.creator.edit_label(self.board, lid, 'Новая', 'rename')
        self.creator.edit_label(self.board, lid, 'Новая', 'rename')
        self.assertEqual(len(self.alice.task_updates(cursor)['events']), 1)
        self.creator.edit_label(self.board, lid, None)
        self.assertEqual(len(self.alice.task_updates(cursor)['events']), 2)
