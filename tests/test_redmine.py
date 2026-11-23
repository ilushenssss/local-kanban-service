import unittest
from importlib.util import find_spec
from unittest.mock import Mock, call

from kanban.redmine import (APPROVAL_COLORS, department_approvals, issue_references, kris_progress_text,
                            read_kris_progress, status_key)


class RedmineTest(unittest.TestCase):
    @unittest.skipUnless(find_spec('redminelib'), 'python-redmine is optional')
    def test_kris_progress_reads_exact_ti_links_and_direct_children(self):
        def resource(data):
            issue = Mock()
            issue.id = data['id']
            issue.raw.return_value = data
            return issue

        def kris(issue_id, location):
            return resource({'id': issue_id, 'custom_fields': [{'name': 'Расположение ТИ', 'value': location}]})

        client = Mock()
        client.issue.filter.return_value = [kris(71, '#142'), kris(72, 'https://example.test/issues/42'),
                                            kris(73, 'ТИ #43'), kris(74, '44')]
        issues = {
            72: resource({'id': 72, 'children': [
                {'id': 101, 'status': {'name': 'Выполнено (проверено)'}, 'children': [{'id': 999}]},
                {'id': 102}, {'id': 103, 'status': {'name': 'В работе'}},
                {'id': 104, 'status': {'name': 'Действие не требуется'}},
            ]}),
            102: resource({'id': 102, 'status': {'name': 'ВЫПОЛНЕНО'}}),
            73: resource({'id': 73, 'children': [{'id': 105}]}),
            105: resource({'id': 105, 'status': {'name': ' ДЕЙСТВИЕ  НЕ ТРЕБУЕТСЯ '}}),
        }
        def issue_get(issue_id, **kwargs):
            return issues[issue_id]
        client.issue.get.side_effect = issue_get
        progress, errors = read_kris_progress(client, iter([42, 43, 44, 45]))
        self.assertEqual(progress, {42: {'done': 2, 'total': 3}, 43: {'done': 0, 'total': 0}, 45: None})
        self.assertEqual(errors, ['Не удалось прочитать подзадачи КРИС_ТИ #74.'])
        self.assertEqual(kris_progress_text(progress[42]), '67% (2/3)')
        self.assertEqual(kris_progress_text(progress[43]), 'Подзадач нет')
        self.assertEqual(kris_progress_text(progress[45]), '—')
        client.issue.filter.assert_called_once_with(project_id=49, status_id='open', parent_id='!*')
        self.assertEqual(client.issue.get.call_args_list,
                         [call(72, include='children'), call(102), call(73, include='children'), call(105),
                          call(74, include='children')])

    def test_issue_references_from_custom_fields(self):
        fields = [
            {'name': 'Основание для открытия ТИ', 'value': ' СП-23578,СП-71042  СП-65243\r\nСП-49618, '},
            {'name': '№ bz', 'value': ', 48316, 70245\n61428\t89537'},
            {'name': 'Другое основание', 'value': 'СП-1'},
        ]
        self.assertEqual(issue_references(fields), {
            'basis': ['СП-23578', 'СП-71042  СП-65243', 'СП-49618'],
            'bugs': ['48316', '70245', '61428\t89537'],
        })
        self.assertEqual(issue_references([{'name': '№ bz', 'value': ' ,\n '}]), {'bugs': []})

    def test_department_approvals_from_custom_fields(self):
        fields = [
            {'name': 'ТИ согласовано с ОСП', 'value': 'Нет'},
            {'name': 'ТИ согласовано с отд. 201', 'value': 'На согласовании'},
            {'name': 'Редакция: ТИ согласовано со смежным отделом', 'value': 'Есть замечания'},
            {'name': 'ТИ согласовано с ОТД.55', 'value': 'Да'},
            {'name': 'ТИ согласовано с ОСЗ', 'value': 'Не требуется'},
            {'name': 'Согласовано с ОСП', 'value': 'Да'},
            {'name': 'ТИ согласовано с', 'value': 'Да'},
            {'name': 'ТИ согласовано с отд.', 'value': 'Да'},
            {'name': 'ТИ согласовано с ОК', 'value': ''},
        ]
        approvals = department_approvals(fields)
        self.assertEqual(
            [(item['department'], item['status'], APPROVAL_COLORS[status_key(item['status'])])
             for item in approvals],
            [('ОСП', 'Нет', 'red'), ('201', 'На согласовании', 'red'),
             ('смежным отделом', 'Есть замечания', 'yellow'), ('55', 'Да', 'green')],
        )
