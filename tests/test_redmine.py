import unittest

from kanban.redmine import APPROVAL_COLORS, department_approvals, issue_references, status_key


class RedmineTest(unittest.TestCase):
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
