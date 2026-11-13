import copy
import multiprocessing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from kanban.store import Store, Conflict, Busy
from tools.restore import restore


def create_concurrent(folder, title):
    s = Store(folder, title)
    s.save_task({'board_id': 1, 'column_id': 1, 'title': title})


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.s = Store(self.root / 'data', 'alice')
        self.bid = self.s.board_action('Отдел')
        self.cols = self.s.columns(self.bid)
        self.base = {'board_id': self.bid, 'column_id': self.cols[0]['id'], 'title': 'Проверить договор', 'description': 'Описание', 'assignees': ['alice', 'bob'], 'labels': ['Документы'], 'checklist': [{'text': 'Подписать', 'done': 0}]}
    def tearDown(self):
        self.temp.cleanup()
    def create(self):
        r = self.s.save_task(self.base)
        return self.s.detail(r['id'])
    def test_delete_column_moves_active_and_archived_with_history_and_retry(self):
        original = self.create()
        archived = self.create()
        archived['archived'] = True
        self.s.save_task(archived)
        remaining = self.cols[1:]
        removed = {self.cols[0]['id']: self.cols[-1]['id']}
        self.s.save_columns(self.bid, remaining, 'delete-columns', removed)
        self.s.save_columns(self.bid, remaining, 'delete-columns', removed)
        self.assertEqual(len(self.s.columns(self.bid)), 4)
        for task in (original, archived):
            current = self.s.detail(task['id'])
            self.assertEqual(current['column_id'], self.cols[-1]['id'])
            self.assertIsNotNone(current['completed_at'])
            events = [h for h in current['history'] if h['operation'] == 'Удаление колонки и перенос задач']
            self.assertEqual(len(events), 1)
            self.assertIn('Входящие', events[0]['changes'])
        self.assertTrue(self.s.detail(archived['id'])['archived'])
        with self.assertRaises(Conflict):
            self.s.save_task(original)
        with self.s.connection() as c:
            self.assertFalse(c.execute('PRAGMA foreign_key_check').fetchall())

    def test_delete_done_column_reopens_tasks_and_rejects_invalid_plan(self):
        task = self.create()
        task['column_id'] = self.cols[-1]['id']
        self.s.save_task(task)
        self.s.save_columns(self.bid, self.cols[:-1], removed={self.cols[-1]['id']: self.cols[0]['id']})
        self.assertIsNone(self.s.detail(task['id'])['completed_at'])
        before = self.s.columns(self.bid)
        with self.assertRaises(ValueError):
            self.s.save_columns(self.bid, before[1:], removed={before[0]['id']: 99999})
        self.assertEqual(before, self.s.columns(self.bid))
        with self.assertRaises(ValueError):
            self.s.save_columns(self.bid, [])
        with self.assertRaises(ValueError):
            self.s.save_columns(self.bid, self.cols)

    def test_deleted_column_ids_are_not_reused_and_failed_delete_rolls_back(self):
        self.create()
        remaining = copy.deepcopy(self.cols[1:])
        remaining[0]['wip_limit'] = -1
        with self.assertRaises(sqlite3.IntegrityError):
            self.s.save_columns(self.bid, remaining, removed={self.cols[0]['id']: remaining[0]['id']})
        self.assertEqual(self.s.columns(self.bid), self.cols)
        self.assertEqual(self.s.tasks(self.bid)[0]['column_id'], self.cols[0]['id'])
        self.s.save_columns(self.bid, self.cols[:-1], removed={self.cols[-1]['id']: self.cols[0]['id']})
        updated = self.s.columns(self.bid) + [{'name': 'Новая', 'is_done': False, 'wip_limit': None}]
        self.s.save_columns(self.bid, updated)
        self.assertGreater(self.s.columns(self.bid)[-1]['id'], self.cols[-1]['id'])

    def test_unread_comments_are_per_user_and_read_cursor_is_monotonic(self):
        names = ['Бэклог', 'В работе', 'На проверке', 'Готово']
        board = self.s.board_action('Новый шаблон', column_names=names)
        self.assertEqual([c['name'] for c in self.s.columns(board)], names)
        self.assertTrue(self.s.columns(board)[-1]['is_done'])
        task = self.create()
        own = self.s.comment(task['id'], 'Свое сообщение')
        bob = Store(self.s.folder, 'bob')
        foreign = bob.comment(task['id'], 'Сообщение коллеги')
        self.assertEqual(self.s.detail(task['id'])['unread_count'], 1)
        self.assertEqual(bob.detail(task['id'])['unread_count'], 1)
        self.s.mark_read(task['id'], foreign)
        self.s.mark_read(task['id'], own)
        self.assertEqual(Store(self.s.folder, 'alice').detail(task['id'])['unread_count'], 0)
        self.assertEqual(bob.detail(task['id'])['unread_count'], 1)
        bob.comment(task['id'], 'Новое сообщение')
        self.assertEqual(self.s.detail(task['id'])['unread_count'], 1)
        another = self.create()
        with self.assertRaises(ValueError):
            self.s.mark_read(another['id'], foreign)
        self.assertEqual(self.s.detail(task['id'])['version'], task['version'])

    def test_team_chat_retry_read_cursor_and_backup(self):
        first = self.s.team_send('Первое', 'same-message')
        self.assertEqual(first, self.s.team_send('Первое', 'same-message'))
        bob = Store(self.s.folder, 'bob')
        second = bob.team_send('Ответ')
        self.assertEqual(self.s.team_chat()['unread'], 1)
        self.assertEqual(bob.team_chat()['unread'], 1)
        self.s.team_mark_read(second)
        self.s.team_mark_read(first)
        self.assertEqual(self.s.team_chat()['unread'], 0)
        self.assertEqual(len(self.s.team_chat(first)['messages']), 1)
        with self.assertRaises(ValueError):
            self.s.team_send('Другой текст', 'same-message')
        self.assertEqual(len(self.s.team_chat()['messages']), 2)
        backup = self.s.backup(self.root / 'chat-backups')
        restore(backup, self.root / 'chat-restored')
        restored = Store(self.root / 'chat-restored', 'alice')
        self.assertEqual(len(restored.team_chat()['messages']), 2)
        self.assertEqual(restored.team_chat()['unread'], 0)

    def test_schema_one_migrates_without_losing_existing_tasks(self):
        task = self.create()
        with self.s.connection() as c:
            c.execute('DROP TABLE team_messages')
            c.execute('DELETE FROM schema_migrations WHERE version=2')
            c.execute('PRAGMA user_version=1')
            c.commit()
        upgraded = Store(self.s.folder, 'alice')
        self.assertEqual(upgraded.detail(task['id'])['title'], task['title'])
        upgraded.team_send('После обновления')
        with upgraded.connection() as c:
            self.assertEqual(c.execute('PRAGMA user_version').fetchone()[0], 2)
            self.assertFalse(c.execute('PRAGMA foreign_key_check').fetchall())

    def test_create_and_history(self):
        d = self.create()
        self.assertEqual(d['number'], 1)
        self.assertEqual(d['assignees'], ['alice', 'bob'])
        self.assertEqual(d['history'][0]['author'], 'alice')
        self.assertEqual(len(d['checklist']), 1)
    def test_conflict_does_not_overwrite(self):
        original = self.create()
        other = copy.deepcopy(original)
        other['title'] = 'Правка коллеги'
        self.s.save_task(other)
        original['description'] = 'Мой текст'
        with self.assertRaises(Conflict) as error:
            self.s.save_task(original)
        self.assertEqual(error.exception.current['title'], 'Правка коллеги')
        self.assertEqual(original['description'], 'Мой текст')
        self.assertEqual(len(self.s.detail(original['id'])['history']), 2)
    def test_completion_reopen(self):
        d = self.create()
        d['column_id'] = self.cols[-1]['id']
        self.s.save_task(d)
        d = self.s.detail(d['id'])
        self.assertIsNotNone(d['completed_at'])
        d['column_id'] = self.cols[0]['id']
        self.s.save_task(d)
        self.assertIsNone(self.s.detail(d['id'])['completed_at'])
    def test_retry_task_and_comment(self):
        a = self.s.save_task(self.base, 'task-operation')
        self.assertEqual(a, self.s.save_task(self.base, 'task-operation'))
        cid = self.s.comment(a['id'], 'Текст', 'comment-operation')
        self.assertEqual(cid, self.s.comment(a['id'], 'Текст', 'comment-operation'))
        self.assertEqual(len(self.s.tasks(self.bid)), 1)
        self.assertEqual(len(self.s.detail(a['id'])['comments']), 1)
        with self.assertRaises(ValueError):
            self.s.comment(a['id'], 'Другой текст', 'comment-operation')
    def test_filters_entire_dataset_and_russian(self):
        a = self.create()
        self.assertEqual(len(self.s.tasks(self.bid, {'query': 'ДОГОВОР'})), 1)
        self.assertEqual(len(self.s.tasks(self.bid, {'assignee': 'bob', 'label': 'Документы'})), 1)
        self.assertEqual(len(self.s.tasks(self.bid, {'unassigned': True})), 0)
        a.update(due_date='2000-01-01', priority='Срочный')
        self.s.save_task(a)
        self.assertEqual(len(self.s.tasks(self.bid, {'overdue': True, 'priority': 'Срочный', 'due_to': '2001-01-01'})), 1)
        self.assertEqual(len(self.s.tasks(self.bid, {'query': 'нет'})), 0)
    def test_archive_restore(self):
        d = self.create()
        d['archived'] = True
        self.s.save_task(d)
        self.assertFalse(self.s.tasks(self.bid))
        d = self.s.tasks(self.bid, {'archived': True})[0]
        d['archived'] = False
        self.s.save_task(d)
        self.assertEqual(len(self.s.tasks(self.bid)), 1)
    def test_columns_order_and_completion(self):
        d = self.create()
        columns = self.cols[::-1]
        for c in columns:
            c['is_done'] = c['id'] == d['column_id']
        columns[0]['wip_limit'] = 2
        self.s.save_columns(self.bid, columns)
        self.assertEqual(self.s.columns(self.bid)[0]['id'], columns[0]['id'])
        self.assertIsNotNone(self.s.detail(d['id'])['completed_at'])
    def test_foreign_board_rejected(self):
        bid = self.s.board_action('Другая')
        payload = dict(self.base, column_id=self.s.columns(bid)[0]['id'])
        with self.assertRaises(ValueError):
            self.s.save_task(payload)
        self.assertFalse(self.s.tasks(self.bid))
    def test_history_immutable_and_transaction_rollback(self):
        d = self.create()
        with self.s.connection() as c:
            with self.assertRaises(sqlite3.IntegrityError):
                c.execute('DELETE FROM task_history')
        d['assignees'] = ['']
        with self.assertRaises(ValueError):
            self.s.save_task(d)
        self.assertEqual(self.s.detail(d['id'])['version'], 1)
        self.assertEqual(len(self.s.detail(d['id'])['history']), 1)
    def test_attachment_unique_retry_and_no_overwrite(self):
        d = self.create()
        path = self.root / 'договор.txt'
        path.write_text('Один', encoding='utf-8')
        aid = self.s.attach(d['id'], path, 'upload1')
        self.assertEqual(aid, self.s.attach(d['id'], path, 'upload1'))
        path.write_text('Два', encoding='utf-8')
        self.s.attach(d['id'], path, 'upload2')
        attachments = self.s.detail(d['id'])['attachments']
        self.assertEqual(len(attachments), 2)
        self.assertNotEqual(attachments[0]['stored_name'], attachments[1]['stored_name'])
        dest = self.root / 'download.txt'
        self.s.download(aid, dest)
        self.assertEqual(dest.read_text(encoding='utf-8'), 'Один')
        with self.assertRaises(FileExistsError):
            self.s.download(aid, dest)
    def test_missing_and_corrupted_attachment(self):
        d = self.create()
        source = self.root / 'file.txt'
        source.write_text('text')
        aid = self.s.attach(d['id'], source)
        attachment = self.s.detail(d['id'])['attachments'][0]
        physical = self.s.folder / 'attachments' / attachment['stored_name']
        physical.write_text('corrupt')
        with self.assertRaises(IOError):
            self.s.download(aid, self.root / 'out.txt')
        self.assertFalse((self.root / 'out.txt').exists())
        physical.unlink()
        with self.assertRaises(FileNotFoundError):
            self.s.download(aid, self.root / 'out.txt')
    def test_backup_restore_and_repeated_backup(self):
        d = self.create()
        path = self.root / 'a.txt'
        path.write_text('attachment')
        self.s.attach(d['id'], path)
        backup = self.s.backup(self.root / 'backups')
        self.assertNotEqual(backup, self.s.backup(self.root / 'backups'))
        restored = self.root / 'restored'
        restore(backup, restored)
        self.assertEqual(Store(restored).detail(d['id'])['title'], d['title'])
        with self.assertRaises(ValueError):
            restore(backup, restored)
    def test_stale_lock_not_automatically_removed(self):
        self.s.lock.write_text('{}')
        self.s.timeout = .1
        with self.assertRaises(Busy):
            self.s.save_task(self.base)
        self.assertTrue(self.s.lock.exists())
    def test_archived_board_blocks_write(self):
        self.s.board_action('Отдел', self.bid, True)
        with self.assertRaises(ValueError):
            self.s.save_task(self.base)
    def test_catalogs_preserve_existing_users_and_labels(self):
        d = self.create()
        uid = self.s.save_user('ivanov', 'Иванов Иван', request_id='add-user')
        self.assertEqual(uid, self.s.save_user('ivanov', 'Иванов Иван', request_id='add-user'))
        with self.assertRaises(ValueError):
            self.s.save_user('ivanov', 'Другой сотрудник')
        self.s.save_user('ivanov', 'Иванов И. И.', uid)
        with self.assertRaises(ValueError):
            self.s.save_user('new-login', 'Иванов', uid)
        d['assignees'] = ['ivanov']
        self.s.save_task(d)
        reopened = Store(self.s.folder, 'alice')
        self.assertEqual(reopened.detail(d['id'])['assignee_names'], ['Иванов И. И.'])
        catalog = reopened.catalogs(self.bid)
        self.assertTrue(any(u['login'] == 'bob' for u in catalog['users']))
        self.assertTrue(any(l['name'] == 'Документы' for l in catalog['labels']))
        self.assertEqual(len(reopened.detail(d['id'])['history']), 2)

    def test_labels_are_persistent_and_scoped_to_board(self):
        lid = self.s.add_label(self.bid, 'На согласование', 'label-id')
        self.assertEqual(lid, self.s.add_label(self.bid, 'На согласование', 'label-id'))
        self.assertEqual(lid, self.s.add_label(self.bid, 'На согласование'))
        second = self.s.board_action('Другая доска')
        self.assertEqual(self.s.catalogs(second)['labels'], [])
        with self.assertRaises(ValueError):
            self.s.add_label(self.bid, '   ')
        with self.assertRaises(ValueError):
            self.s.save_user('', 'Имя')
        d = self.create()
        d['labels'] = ['На согласование']
        self.s.save_task(d)
        self.assertEqual(self.s.tasks(self.bid, {'label': 'На согласование'})[0]['id'], d['id'])

    def test_concurrent_processes(self):
        ctx = multiprocessing.get_context('spawn')
        processes = [ctx.Process(target=create_concurrent, args=(str(self.s.folder), 'user' + str(i))) for i in range(4)]
        for p in processes:
            p.start()
        for p in processes:
            p.join(20)
            self.assertEqual(p.exitcode, 0)
        self.assertEqual(sorted(t['number'] for t in self.s.tasks(self.bid)), [1, 2, 3, 4])


if __name__ == '__main__':
    unittest.main()
