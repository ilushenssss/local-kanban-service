import contextlib
import copy
import multiprocessing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from kanban.store import Store, Conflict, Busy, AccessDenied
from unittest.mock import patch
from tools.restore import restore


def create_concurrent(folder, title):
    s = Store(folder, title)
    s.save_task({'board_id': 1, 'column_id': 1, 'title': title})


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.s = Store(self.root / 'data', 'alice')
        self.s.initialize_admin('alice')
        self.bid = self.s.board_action('Отдел')
        bob = self.s.save_user('bob', 'bob')
        self.s.set_board_access(self.bid, [bob], self.s.profile()['id'])
        self.cols = self.s.columns(self.bid)
        self.base = {'board_id': self.bid, 'column_id': self.cols[0]['id'], 'title': 'Проверить договор', 'description': 'Описание', 'assignees': ['alice', 'bob'], 'labels': ['Документы'], 'checklist': [{'text': 'Подписать', 'done': 0}]}
    def tearDown(self):
        self.temp.cleanup()
    def create(self):
        r = self.s.save_task(self.base)
        return self.s.detail(r['id'])
    def test_only_admin_can_change_shared_redmine_settings(self):
        member = Store(self.s.folder, 'bob')
        self.assertIsNone(member.redmine_settings())
        with self.assertRaises(AccessDenied):
            member.set_redmine_settings('https://redmine.example.test', {})
        with self.assertRaises(ValueError):
            self.s.set_redmine_settings('https://login:secret@redmine.example.test', {})
        settings = self.s.set_redmine_settings('https://redmine.example.test/', {' Новый ': 'Проект ТИ'})
        self.assertEqual(settings, {'url': 'https://redmine.example.test', 'statuses': {'новый': 'Проект ТИ'}})
        self.assertEqual(member.redmine_settings(), settings)

    def test_shared_redmine_cards_and_status_updates(self):
        board = self.s.board_action('Проект / ТИ', column_names=['Проект ТИ', 'ТИ открыто'])
        member = Store(self.s.folder, 'bob')
        self.s.set_board_access(board, [member.profile()['id']], self.s.profile()['id'])
        with self.assertRaises(AccessDenied):
            member.link_redmine_issue(board, 23, 'ТИ', 'Проект ТИ', 'Проект ТИ')
        with self.assertRaises(ValueError):
            self.s.link_redmine_issue(board, 23, 'ТИ', 'Неизвестный', 'Неизвестный')
        self.s.link_redmine_issue(board, 23, 'ТИ', 'Проект ТИ', 'Проект ТИ')
        with self.assertRaises(ValueError):
            self.s.link_redmine_issue(board, 23, 'ТИ', 'Проект ТИ', 'Проект ТИ')
        outsider = Store(self.s.folder, 'outsider')
        with self.assertRaises(AccessDenied):
            outsider.redmine_tasks(board)
        with self.assertRaises(AccessDenied):
            outsider.update_redmine_tasks(board, [(23, 1, 'ТИ', 'ТИ открыто', 'ТИ открыто', 'Редакция к ТИ-1157р4', [], {}, None)])
        progress = {'done': 0, 'total': 0, 'subject': 'Основная задача КРИС'}
        self.assertEqual(member.update_redmine_tasks(
            board, [(23, 1, 'Открытая ТИ', 'ТИ открыто', 'ТИ открыто', 'Редакция к ТИ-1157р4', [], {}, progress)]), [])
        self.assertTrue(self.s.update_redmine_tasks(board, [(23, 1, 'Устаревшие данные', 'Старый статус', 'Проект ТИ', '', [], {}, None)]))
        self.assertTrue(self.s.update_redmine_tasks(board, [(23, 2, 'Неизвестная ТИ', 'Неизвестный', 'Неизвестный', '', [], {}, None)]))
        _, _, columns, tasks, totals, _, _ = member.board_snapshot(board, {'query': 'ТИ-1157р4'}, 'board')
        self.assertEqual([(t['redmine_id'], t['title'], t['status'], t['column_id']) for t in tasks],
                         [(23, 'Открытая ТИ', 'ТИ открыто', columns[1]['id'])])
        self.assertEqual(totals, {columns[1]['id']: 1})
        self.assertEqual(member.board_snapshot(board, {'query': 'Основная задача КРИС'}, 'board')[3], tasks)

    def test_redmine_cards_sort_by_ti_number_and_numeric_revision_in_each_column(self):
        board = self.s.board_action('Проект / ТИ', column_names=['Проект ТИ', 'ТИ открыто'])
        for issue_id, tracker, subject in (
            (1, 'ТИ-1156р2', ''), (2, 'Редакция к ТИ-1148р10', ''),
            (3, 'ТИ-1156', ''), (4, 'ТИ-1148р2', 'ТИ-1148'), (5, 'ТИ-1148', ''),
        ):
            for index, column in enumerate(('Проект ТИ', 'ТИ открыто')):
                self.s.link_redmine_issue(board, issue_id + 10 * index, 'Заголовок', column, column, tracker_name=tracker,
                                          kris_progress={'subject': subject})
        _, _, columns, cards, _, _, _ = self.s.board_snapshot(board, {}, 'board')
        for index, column in enumerate(columns):
            self.assertEqual([card['redmine_id'] for card in cards if card['column_id'] == column['id']],
                             [issue_id + 10 * index for issue_id in (5, 4, 2, 3, 1)])

    def test_basis_project_is_admin_only_and_scoped_to_the_project_board(self):
        board = self.s.board_action('Проект / ТИ')
        other = self.s.board_action('Проект / Другая доска')
        member = Store(self.s.folder, 'bob')
        self.s.set_board_access(board, [member.profile()['id']], self.s.profile()['id'])
        with self.assertRaises(AccessDenied):
            member.set_redmine_basis_project(board, 'Внедрение')
        with self.assertRaises(ValueError):
            self.s.set_redmine_basis_project(self.bid, 'Внедрение')
        self.s.set_redmine_basis_project(board, 'Внедрение')
        self.assertEqual(member.redmine_basis_project(board), 'Внедрение')
        self.assertEqual(self.s.redmine_basis_project(other), '')

    def test_sppi_batch_requires_creation_rights_and_does_not_duplicate_cards(self):
        board = self.s.board_action('Проект / 2.0-218', column_names=['Проект ТИ'])
        other = self.s.board_action('Проект / 2.0-219', column_names=['Проект ТИ'])
        member = Store(self.s.folder, 'bob')
        self.s.set_board_access(board, [member.profile()['id']], self.s.profile()['id'])
        with self.s.connection() as c:
            c.execute("INSERT INTO settings VALUES('redmine_sppi_project','Общий СППИ')")
            c.commit()
        self.assertEqual(member.redmine_sppi_project(board), 'Общий СППИ')
        with self.assertRaises(AccessDenied):
            member.set_redmine_sppi_project(board, 'СППИ')
        with self.assertRaises(AccessDenied):
            member.redmine_sppi_project(other)
        self.s.set_redmine_sppi_project(board, 'СППИ')
        self.assertEqual(member.redmine_sppi_project(board), 'СППИ')
        self.assertEqual(self.s.redmine_sppi_project(other), 'Общий СППИ')
        self.s.set_redmine_sppi_project(other, '')
        self.assertEqual(self.s.redmine_sppi_project(other), '')
        updates = [(40, None, 'Импорт', 'Проект ТИ', 'Проект ТИ', 'ТИ-40', [], {}, None)]
        with self.assertRaises(AccessDenied):
            member.update_redmine_tasks(board, updates, sppi_project='СППИ')
        with self.assertRaisesRegex(ValueError, 'изменился'):
            self.s.update_redmine_tasks(board, updates, sppi_project='Другой проект')
        self.assertEqual(self.s.update_redmine_tasks(board, updates, sppi_project='СППИ'), [])
        self.assertEqual(self.s.update_redmine_tasks(board, updates, sppi_project='СППИ'), [])
        self.assertEqual([task['issue_id'] for task in member.redmine_tasks(board)], [40])
        self.s.set_redmine_sppi_project(board, 'Новый проект')
        stale = [(40, 1, 'Старое обновление', 'Проект ТИ', 'Проект ТИ', 'ТИ-40', [], {}, None)]
        with self.assertRaisesRegex(ValueError, 'изменился'):
            self.s.update_redmine_tasks(board, stale, sppi_project='СППИ')

    def test_remove_manual_redmine_card_from_one_board_only(self):
        board = self.s.board_action('Проект / ТИ', column_names=['Проект ТИ'])
        other = self.s.board_action('Проект / Другая доска', column_names=['Проект ТИ'])
        member = Store(self.s.folder, 'bob')
        self.s.set_board_access(board, [member.profile()['id']], self.s.profile()['id'])
        for target in (board, other):
            self.s.link_redmine_issue(target, 40, 'Ручная карточка', 'Проект ТИ', 'Проект ТИ')
        self.s.set_redmine_sppi_project(board, 'СППИ')
        self.s.update_redmine_tasks(board, [(41, None, 'СППИ', 'Проект ТИ', 'Проект ТИ', '', [], {}, None)],
                                    sppi_project='СППИ')
        with self.assertRaises(AccessDenied):
            member.unlink_redmine_issue(board, 40, 1)
        with self.assertRaisesRegex(ValueError, 'СППИ'):
            self.s.unlink_redmine_issue(board, 41, 1)
        with self.assertRaisesRegex(ValueError, 'изменились'):
            self.s.unlink_redmine_issue(board, 40, 2)
        self.s.set_role(member.profile()['id'], 'manager')
        self.assertTrue(member.unlink_redmine_issue(board, 40, 1, 'remove-40'))
        self.assertTrue(member.unlink_redmine_issue(board, 40, 1, 'remove-40'))
        self.assertEqual([task['issue_id'] for task in member.redmine_tasks(board)], [41])
        self.assertEqual([task['issue_id'] for task in self.s.redmine_tasks(other)], [40])

    def test_identification_links_only_to_visible_active_boards(self):
        board = self.s.board_action('Проект / 2.0-218')
        private = self.s.board_action('Проект / Другая доска')
        member = Store(self.s.folder, 'bob')
        self.s.set_board_access(board, [member.profile()['id']], self.s.profile()['id'])
        first, second = 'ТИ(200)-1234-01', 'ТИ(200)-1234-02'
        for bid, number, ti in ((board, first, 'ТИ-701'), (private, second, 'ТИ-702')):
            column = self.s.columns(bid)[0]['name']
            self.s.link_redmine_issue(bid, int(ti[3:]), 'Заголовок', column, column,
                                     tracker_name=ti, issue_references={'basis': [number + ' описание']})
        column = self.s.columns(board)[0]['name']
        self.s.link_redmine_issue(board, 703, 'Уточнить сроки поставки', column, column, tracker_name='Редакция к ТИ',
                                 issue_references={'basis': [first]},
                                 kris_progress={'done': 0, 'total': 0, 'subject': 'Редакция к ТИ — 1157p4'})
        self.s.link_redmine_issue(board, 704, 'Проверить комплект', column, column,
                                 tracker_name='Редакция к ТИ-1157р5', issue_references={'basis': [first]},
                                 kris_progress={'done': 0, 'total': 0, 'subject': 'Работы по ТИ-1157'})
        column = self.s.columns(private)[0]['name']
        self.s.link_redmine_issue(private, 703, 'Заголовок', column, column, tracker_name='Редакция к ТИ',
                                 issue_references={'basis': [first]})
        self.s.set_redmine_basis_project(board, 'Проверка')
        cards = [{'number': number, 'approval': 'Да', 'status': 'ТИ утверждено'} for number in (first, second)]
        with self.assertRaises(AccessDenied):
            member.save_identification_cards(private, 'Проверка', cards)
        self.s.save_identification_cards(board, 'Проверка', cards)
        self.assertIsNone(member.board_snapshot(board, {}, 'identification')[3])
        updated = [dict(card, versions=['2.0-218 И-01']) for card in cards]
        updated.append(dict(cards[0], number='ТИ(200)-1234-03', versions=['2.0-219']))
        self.s.save_identification_cards(board, 'Проверка', updated)
        self.assertEqual([card['links'] for card in member.board_snapshot(board, {}, 'identification')[3]],
                         [['ТИ-701', 'ТИ — 1157p4', 'ТИ-1157р5'], []])
        self.assertEqual([card['links'] for card in self.s.board_snapshot(board, {}, 'identification')[3]],
                         [['ТИ-701', 'ТИ — 1157p4', 'ТИ-1157р5'], ['ТИ-702']])
        self.s.set_redmine_basis_project(board, 'Другой проект')
        self.assertIsNone(member.board_snapshot(board, {}, 'identification')[3])
        with self.assertRaisesRegex(ValueError, 'изменился'):
            member.save_identification_cards(board, 'Проверка', cards)

    def test_board_cards_batch_filters_counts_and_access(self):
        original = self.create()
        bob = Store(self.s.folder, 'bob')
        cid = bob.comment(original['id'], 'Обсуждение')
        self.s.save_task(dict(self.base, checklist=[{'text': 'Готово', 'done': True}]))
        trace = []
        connection = self.s.connection

        @contextlib.contextmanager
        def traced():
            with connection() as c:
                c.set_trace_callback(trace.append)
                yield c

        with patch.object(self.s, 'connection', traced):
            filters = {'query': 'ОПИСАНИЕ', 'assignee': 'bob', 'label': 'Документы'}
            cards = self.s.board_cards(self.bid, filters)
            small = len(trace)
            self.assertEqual(len(cards), 2)
            for _ in range(12):
                self.s.save_task(self.base)
            trace.clear()
            self.assertEqual(len(self.s.board_cards(self.bid, filters)), 14)
            self.assertEqual(len(trace), small)
        self.assertFalse({'description', 'history', 'comments', 'attachments', 'checklist'} & cards[0].keys())
        self.assertEqual((cards[0]['check_total'], cards[1]['check_done'], cards[0]['unread_count']), (1, 1, 1))
        self.s.mark_read(original['id'], cid)
        self.assertEqual(self.s.board_cards(self.bid)[0]['unread_count'], 0)
        self.assertEqual(self.s.card_details(original['id'])['description'], 'Описание')
        self.s.set_board_access(self.bid, [], self.s.profile()['id'])
        with self.assertRaises(AccessDenied):
            bob.board_cards(self.bid)
        self.assertFalse(bob.board_snapshot(self.bid, {}, 'personal')[-1])

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
            self.assertEqual(c.execute('PRAGMA user_version').fetchone()[0], 15)
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
        self.assertEqual(Store(restored, 'alice').detail(d['id'])['title'], d['title'])
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
        members = [r['id'] for r in self.s.board_access(self.bid)['users'] if r['member']]
        self.s.set_board_access(self.bid, members + [uid], self.s.profile()['id'])
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
        members = []
        for i in range(4):
            uid = self.s.save_user('user' + str(i), 'User ' + str(i))
            self.s.set_role(uid, 'manager')
            members.append(uid)
        self.s.set_board_access(self.bid, members, self.s.profile()['id'])
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
