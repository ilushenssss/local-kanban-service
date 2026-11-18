from pathlib import Path
import tempfile
import unittest
from kanban.store import Store, AccessDenied
from kanban.presentation import date_text


class BoardBadgesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.creator = Store(Path(self.tmp.name) / 'data', 'creator')
        self.creator.initialize_admin('creator')
        self.aid = self.creator.save_user('alice', 'Алиса')
        self.bid = self.creator.save_user('bob', 'Борис')
        self.a = self.creator.board_action('Первая')
        self.b = self.creator.board_action('Вторая')
        for board in (self.a, self.b):
            self.creator.set_board_access(board, [self.aid, self.bid], self.creator.profile()['id'])
        self.alice = Store(self.creator.folder, 'alice')
        self.bob = Store(self.creator.folder, 'bob')

    def create(self, board):
        return self.creator.save_task({'board_id': board, 'column_id': self.creator.columns(board)[0]['id'], 'title': 'Задача', 'assignees': ['alice']})['id']

    def test_shared_timezone_requires_admin_and_handles_daylight_saving(self):
        self.assertEqual(self.alice.timezone(), 'UTC')
        with self.assertRaises(AccessDenied):
            self.alice.set_timezone('Europe/Berlin')
        with self.assertRaises(ValueError):
            self.creator.set_timezone('invalid')
        self.creator.set_timezone('Europe/Berlin')
        zone = self.alice.timezone()
        self.assertEqual(zone, 'Europe/Berlin')
        self.assertIn('01.01.2026 в 13:00', date_text('2026-01-01T12:00:00Z', True, zone))
        self.assertIn('01.07.2026 в 14:00', date_text('2026-07-01T12:00:00Z', True, zone))
        self.assertEqual(date_text('2026-07-01', timezone=zone), '01.07.2026')

    def test_separate_chat_and_task_counts_and_snapshot_reading(self):
        first = self.creator.team_send('Раз', board_id=self.a)
        self.creator.team_send('Два', board_id=self.a)
        own = self.alice.team_send('Моё', board_id=self.a)
        self.creator.team_send('На второй', board_id=self.b)
        task = self.create(self.a)
        badges = self.alice.board_badges()
        self.assertEqual(badges[self.a], {'chat': 2, 'tasks': 1, 'total': 3})
        self.assertEqual(badges[self.b], {'chat': 1, 'tasks': 0, 'total': 1})
        self.alice.team_mark_read(own, board_id=self.a)
        self.assertEqual(self.alice.board_badges()[self.a], {'chat': 0, 'tasks': 1, 'total': 1})
        snapshot = self.alice.detail(task)
        current = self.creator.detail(task)
        current['priority'] = 'Высокий'
        self.creator.save_task(current)
        self.alice.mark_task_notifications_read(task, snapshot['notification_cursor'])
        self.alice.mark_task_notifications_read(task, snapshot['notification_cursor'])
        self.assertEqual(self.alice.board_badges()[self.a]['tasks'], 1)
        reopened = Store(self.creator.folder, 'alice')
        self.assertEqual(reopened.board_badges()[self.a]['tasks'], 1)
        self.assertEqual(len(reopened.task_updates(0)['events']), 1)
        latest = reopened.detail(task)
        reopened.mark_task_notifications_read(task, latest['notification_cursor'])
        self.assertEqual(reopened.board_badges()[self.a]['total'], 0)
        self.assertEqual(reopened.board_badges()[self.b]['total'], 1)
        self.assertEqual(reopened.detail(task)['version'], latest['version'])
        self.assertEqual(reopened.task_updates(0)['events'], [])

    def test_manual_read_is_individual_persistent_and_checks_access(self):
        first = self.creator.team_send('Первое', board_id=self.a)
        second = self.creator.team_send('Второе', board_id=self.a)
        task = self.create(self.a)
        old_event = self.alice.task_updates(0)['events'][0]['id']
        current = self.creator.detail(task)
        current['priority'] = 'Высокий'
        self.creator.save_task(current)
        new_event = self.alice.task_updates(0)['events'][-1]['id']

        with self.assertRaises(ValueError):
            self.bob.mark_notification_read('task', new_event)
        self.alice.mark_notification_read('task', new_event)
        self.alice.mark_notification_read('chat', second)
        self.alice.mark_notification_read('chat', second)
        reopened = Store(self.creator.folder, 'alice')
        self.assertEqual([m['id'] for m in reopened.chat_updates(unread_only=True)], [first])
        self.assertEqual([e['id'] for e in reopened.task_updates(0)['events']], [old_event])
        self.assertEqual(reopened.board_badges()[self.a], {'chat': 1, 'tasks': 1, 'total': 2})
        self.assertEqual(reopened.team_chat(board_id=self.a)['unread'], 1)
        self.assertEqual(self.bob.team_chat(board_id=self.a)['unread'], 2)

        self.creator.set_board_access(self.a, [self.bid], self.creator.profile()['id'])
        with self.assertRaises(AccessDenied):
            reopened.mark_notification_read('chat', first)
        with self.assertRaises(AccessDenied):
            reopened.mark_notification_read('task', old_event)

    def test_private_archived_and_revoked_boards_are_excluded(self):
        private = self.alice.board_action('Личное', personal=True)
        self.alice.team_send('Заметка', board_id=private)
        self.assertNotIn(private, self.creator.board_badges())
        self.assertNotIn(private, self.bob.board_badges())
        self.assertEqual(self.alice.board_badges()[private]['total'], 0)
        self.creator.team_send('Скрыть', board_id=self.a)
        self.create(self.a)
        self.creator.set_board_access(self.a, [self.bid], self.creator.profile()['id'])
        self.assertNotIn(self.a, self.alice.board_badges())
        self.creator.board_action('Вторая', self.b, archive=True)
        self.assertNotIn(self.b, self.alice.board_badges())

    def test_cannot_acknowledge_another_user_task_or_future_event(self):
        task = self.create(self.a)
        snapshot = self.alice.detail(task)['notification_cursor']
        other = self.create(self.a)
        for user, tid, marker in ((self.bob, task, snapshot), (self.alice, other, snapshot), (self.alice, task, snapshot + 100)):
            with self.assertRaises(ValueError):
                user.mark_task_notifications_read(tid, marker)
        self.assertEqual(self.alice.board_badges()[self.a]['tasks'], 2)
        self.creator.set_board_access(self.a, [], self.creator.profile()['id'])
        with self.assertRaises(AccessDenied):
            self.alice.mark_task_notifications_read(task, snapshot)
