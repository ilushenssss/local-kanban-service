import unittest
from importlib.util import find_spec
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, call

from kanban.installation import load_redmine_project_id
from kanban.redmine import (APPROVAL_COLORS, department_approvals, issue_references, kris_coding_deadline, kris_coding_lines,
                            kris_progress_text, read_basis_statuses, read_identification_cards, read_issue,
                            iter_sppi_issues, read_kris_progress, status_key)


class RedmineTest(unittest.TestCase):
    @unittest.skipUnless(find_spec('redminelib'), 'python-redmine is optional')
    def test_ti_responsible_uses_initials_from_immediate_parent(self):
        client = Mock()
        fields = {'subject': 'Тема', 'status': {'name': 'Проект ТИ'}}
        responsible = 'Ответственный за выпуск ТИ'
        client.issue.get.return_value.raw.return_value = dict(
            fields, custom_fields=[{'name': responsible, 'value': ['42']}])
        client.user.get.return_value.raw.return_value = {'firstname': 'Иван', 'lastname': 'Петров'}
        child = dict(fields, parent={'id': 9}, custom_fields=[{'name': responsible, 'value': '43'}])
        cache = {}
        expected = [{'name': 'Иван Петров', 'initials': 'ИП'}]
        self.assertEqual(read_issue(client, 10, data=child, responsible_cache=cache)['issue_references']['ti_responsible'],
                         expected)
        self.assertEqual(read_issue(client, 11, data=child, responsible_cache=cache)['issue_references']['ti_responsible'],
                         expected)
        client.issue.get.assert_called_once_with(9)
        client.user.get.assert_called_once_with(42)
        self.assertEqual(read_issue(client, 9)['issue_references']['ti_responsible'], expected)

    @unittest.skipUnless(find_spec('redminelib'), 'python-redmine is optional')
    def test_sppi_filters_resolved_users_and_versions_and_reads_nested_subtasks(self):
        client = Mock()

        def candidate(issue_id, responsible, version, status='Проект ТИ'):
            issue = Mock()
            issue.id = issue_id
            issue.raw.return_value = {'subject': 'Задача {}'.format(issue_id), 'status': {'name': status}, 'custom_fields': [
                {'name': 'Ответственный за выпуск ТИ', 'value': responsible},
                {'name': 'Приоритет реализации', 'value': version},
            ]}
            return issue

        client.issue.filter.return_value = [
            candidate(1, ['42'], ['1342']), candidate(2, '43', ['1342']),
            candidate(3, '42', ['1343']), candidate(4, '42', '1342'),
            candidate(5, '99', '9999', 'ТИ закрыто'),
            candidate(6, None, None),
        ]
        root_details = {task.id: task.raw.return_value for task in client.issue.filter.return_value}
        revision = candidate(7, '42', '1342')
        revision_data = dict(revision.raw.return_value, parent={'id': 1}, tracker={'name': 'Редакция к ТИ'})
        root_details[1]['children'] = [
            {'id': 7, 'tracker': {'name': 'Редакция к ТИ'}},
            {'id': 8, 'tracker': {'name': 'Разработка'}},
        ]
        root_details[7] = revision_data
        root_details[8] = dict(candidate(8, None, None).raw.return_value, parent={'id': 1},
                               tracker={'name': 'Разработка'}, children=[{'id': 9}])
        root_details[9] = dict(candidate(9, None, None).raw.return_value, parent={'id': 8})
        revision.raw.return_value = revision_data
        client.issue.filter.return_value.append(revision)
        client.issue.get.side_effect = lambda issue_id, **params: Mock(**{'raw.return_value': root_details[issue_id]})
        client.version.get.side_effect = lambda version: SimpleNamespace(
            name={1342: '2.0-218 И-01', 1343: '2.0-219 И-01'}[version])
        client.user.get.side_effect = lambda user_id: Mock(**{'raw.return_value': {
            'firstname': 'Анна', 'lastname': 'Иванова' if user_id == 42 else 'Смирнова'}})
        progress = []
        accepted = iter_sppi_issues(client, 88, '2.0-218', ['Иванова Анна'], on_issue=progress.append)
        self.assertEqual([(issue_id, parent_status) for issue_id, _, parent_status in accepted],
                         [(1, None), (7, 'Проект ТИ'), (8, 'Проект ТИ'), (9, 'Проект ТИ'), (4, None)])
        self.assertEqual(progress, [
            '#1 · Ответственный: Анна Иванова · Версия: 2.0-218 И-01',
            '#7 · Ответственный: Анна Иванова · Версия: 2.0-218 И-01',
            '#8 · Ответственный: — · Версия: —',
            '#9 · Ответственный: — · Версия: —',
            '#2 · Ответственный: Анна Смирнова · Версия: 2.0-218 И-01',
            '#3 · Ответственный: Анна Иванова · Версия: 2.0-219 И-01',
            '#4 · Ответственный: Анна Иванова · Версия: 2.0-218 И-01',
            '#6 · Ответственный: — · Версия: —',
        ])
        client.issue.filter.assert_called_once_with(project_id=88, status_id='*', parent_id='!*')
        self.assertEqual(client.user.get.call_args_list, [call(42), call(43)])
        self.assertEqual(client.version.get.call_args_list, [call(1342), call(1343)])
        self.assertIn(call(8, include='children'), client.issue.get.call_args_list)
        self.assertIn(call(9, include='children'), client.issue.get.call_args_list)
        client.issue.get.reset_mock()

        issues = {
            1: {'subject': 'Задача', 'status': {'name': 'Проект ТИ'},
                'custom_fields': [{'name': 'Основание для открытия ТИ', 'value': 'СП-123'}],
                'children': [{'id': 2, 'subject': 'Подзадача'}]},
            2: {'status': {'name': 'В работе'},
                'children': [{'id': 3, 'subject': 'Вложенная', 'status': {'name': 'Готово'}}]},
            4: {'status': {'name': 'ТИ закрыто'}, 'children': [{'id': 99}]},
        }
        client.issue.get.side_effect = lambda issue_id, **params: Mock(**{'raw.return_value': issues[issue_id]})
        self.assertEqual(read_issue(client, 1)['issue_references'], {
            'basis': ['СП-123'], 'children': [
                {'id': 2, 'title': 'Подзадача', 'status': 'В работе'},
                {'id': 3, 'title': 'Вложенная', 'status': 'Готово'},
            ],
        })
        self.assertIsNone(read_issue(client, 4, skip_closed=True))
        self.assertEqual(client.issue.get.call_args_list,
                         [call(1, include='children'), call(2, include='children'), call(4, include='children')])

    @unittest.skipUnless(find_spec('redminelib'), 'python-redmine is optional')
    def test_identification_keeps_all_values_except_not_required(self):
        from redminelib.exceptions import ResourceNotFoundError

        client = Mock()
        versions = {1342: SimpleNamespace(name='2.0-218 И-01'), 1343: SimpleNamespace(name='2.0-219')}

        def get_version(version_id):
            if version_id not in versions:
                raise ResourceNotFoundError()
            return versions[version_id]

        client.version.get.side_effect = get_version
        for_filter = []
        for suffix, field, approval, status in (
            ('01', 'ТИ согласовано с отд.ЦЗ', 'Да', 'ТИ утверждено'),
            ('02', 'ТИ согласовано с отд. ЦЗ', 'Нет', 'В работе'),
            ('03', 'ТИ согласовано с отд.ЦЗ', 'На согласовании', 'В работе'),
            ('04', 'ТИ согласовано с отд.ЦЗ', ' Не требуется ', 'ТИ утверждено'),
            ('05', 'Другое поле', 'Да', 'ТИ утверждено'),
        ):
            data = {'subject': 'Проверка ' + suffix, 'status': {'name': status}, 'custom_fields': [
                {'name': field, 'value': approval},
                {'name': 'Регистрационный номер_ЛПИВ', 'value': 'ТИ(200)-1234-' + suffix + ' описание'},
                {'name': 'Приоритет реализации_ЛПИВ',
                 'value': ['1342'] if suffix == '01' else '1343' if suffix == '02' else ['1342', '9999']},
            ]}
            for_filter.append(Mock(**{'raw.return_value': data}))
        for_filter.append(Mock(**{'raw.return_value': {'id': 99, 'subject': 'Задача без номера', 'custom_fields': [
            {'name': 'ТИ согласовано с отд.ЦЗ', 'value': 'Да'},
        ]}}))
        client.issue.filter.return_value = for_filter
        cards = read_identification_cards(client, 88)
        self.assertEqual(cards, [
            {'number': 'ТИ(200)-1234-01', 'subject': 'Проверка 01', 'approval': 'Да',
             'status': 'ТИ утверждено', 'versions': ['2.0-218 И-01']},
            {'number': 'ТИ(200)-1234-02', 'subject': 'Проверка 02', 'approval': 'Нет',
             'status': 'В работе', 'versions': ['2.0-219']},
            {'number': 'ТИ(200)-1234-03', 'subject': 'Проверка 03', 'approval': 'На согласовании',
             'status': 'В работе', 'versions': ['2.0-218 И-01']},
        ])
        client.issue.filter.assert_called_once_with(project_id=88, status_id='*')
        self.assertEqual(client.version.get.call_args_list, [call(1342), call(1343), call(9999)])

    @unittest.skipUnless(find_spec('redminelib'), 'python-redmine is optional')
    def test_basis_lookup_uses_fixed_number_and_registration_field(self):
        approved, red, missing = 'ТИ(200)-0123-04', 'ТИ(200)-0123-05', 'ТИ(200)-0123-06'
        raw = [{'name': 'Основание для открытия ТИ', 'value': approved + 'доп. текст, СП-220\n' + red}]
        self.assertEqual(issue_references(raw)['basis'], [approved, 'СП-220', red])

        def issue(field_name, value, status):
            return Mock(**{'raw.return_value': {'custom_fields': [{'name': field_name, 'value': value}],
                                                'status': {'name': status}}})

        client = Mock()
        client.issue.filter.return_value = [
            issue('Другое поле', missing, 'ТИ утверждено'),
            issue('Регистрационный номер_ЛПИВ', 'Номер ' + approved, 'ТИ утверждено'),
            issue('Регистрационный номер_ЛПИВ', [red], 'В работе'),
        ]
        self.assertEqual(read_basis_statuses(client, 88, {approved, red, missing}),
                         {approved: 'ТИ утверждено', red: 'В работе', missing: None})
        client.issue.filter.assert_called_once_with(project_id=88, status_id='*')

    def test_kris_project_id_uses_exact_project_name(self):
        with TemporaryDirectory() as folder:
            base = Path(folder)
            path = base / 'all_project.json'
            with self.assertRaisesRegex(ValueError, 'all_project.json'):
                load_redmine_project_id(base, 'Разработка')
            path.write_text('{"Разработка": 49, "Внедрение": 73, "Некорректный": true}', encoding='utf-8-sig')
            self.assertEqual(load_redmine_project_id(base, 'Разработка'), 49)
            self.assertEqual(load_redmine_project_id(base, 'Внедрение'), 73)
            with self.assertRaisesRegex(ValueError, 'нет проекта'):
                load_redmine_project_id(base, 'разработка')
            with self.assertRaisesRegex(ValueError, 'числовой ID'):
                load_redmine_project_id(base, 'Некорректный')

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
            72: resource({'id': 72, 'subject': '  ТИ-1157 · КРИС  ', 'children': [
                {'id': 101, 'status': {'name': 'Выполнено (проверено)'}, 'children': [{'id': 999}]},
                {'id': 102}, {'id': 103, 'status': {'name': 'Кодирование'}, 'subject': 'Сервис'},
                {'id': 104, 'status': {'name': 'Действие не требуется'}},
            ]}),
            102: resource({'id': 102, 'status': {'name': 'ВЫПОЛНЕНО'}}),
            103: resource({'id': 103, 'due_date': '2027-02-03'}),
            73: resource({'id': 73, 'status': {'name': 'Кодирование'}, 'due_date': '2027-02-07',
                          'children': [{'id': 105}]}),
            105: resource({'id': 105, 'status': {'name': ' ДЕЙСТВИЕ  НЕ ТРЕБУЕТСЯ '}}),
        }
        def issue_get(issue_id, **kwargs):
            return issues[issue_id]
        client.issue.get.side_effect = issue_get
        progress, errors = read_kris_progress(client, iter([42, 43, 44, 45]), 73)
        self.assertEqual(progress, {
            42: {'done': 2, 'total': 3, 'subject': 'ТИ-1157 · КРИС',
                 'coding': [{'subject': 'Сервис', 'due_date': '2027-02-03'}]},
            43: {'done': 0, 'total': 0, 'coding': [{'subject': '#73', 'due_date': '2027-02-07'}]},
            45: None,
        })
        self.assertEqual(errors, ['Не удалось прочитать подзадачи КРИС_ТИ #74.'])
        self.assertEqual(kris_progress_text(progress[42]), '67% (2/3)')
        self.assertEqual(kris_progress_text(progress[43]), 'Подзадач нет')
        self.assertEqual(kris_progress_text(progress[45]), '—')
        client.issue.filter.assert_called_once_with(project_id=73, status_id='open', parent_id='!*')
        self.assertEqual(client.issue.get.call_args_list,
                         [call(72, include='children'), call(102), call(103), call(73, include='children'), call(105),
                          call(74, include='children')])

    def test_kris_coding_lines_group_equal_dates_and_list_different_dates(self):
        coding = [{'subject': 'Сервис', 'due_date': '2027-02-03'},
                  {'subject': 'Интерфейс', 'due_date': '2027-02-03'}]
        self.assertEqual(kris_coding_lines({'coding': coding}), ['Кодирование до 03.02'])
        self.assertEqual(kris_coding_deadline({'coding': coding}), 'до 03.02')
        coding[1]['due_date'] = '2027-02-04'
        self.assertEqual(kris_coding_lines({'coding': coding}), ['Сервис: 03.02', 'Интерфейс: 04.02'])
        self.assertEqual(kris_coding_deadline({'coding': coding}), '03.02–04.02')
        coding[1]['due_date'] = None
        self.assertEqual(kris_coding_lines({'coding': coding}), ['Сервис: 03.02', 'Интерфейс: срок не указан'])
        self.assertEqual(kris_coding_lines({'done': 2, 'total': 3}), [])
        self.assertEqual(kris_coding_deadline({'done': 2, 'total': 3}), '')

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
