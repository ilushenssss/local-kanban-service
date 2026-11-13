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
