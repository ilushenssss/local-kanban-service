import unittest

from kanban.redmine import APPROVAL_COLORS, department_approvals, status_key


class RedmineTest(unittest.TestCase):
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
