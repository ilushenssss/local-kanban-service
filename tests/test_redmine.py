from importlib.util import find_spec
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from kanban import redmine


class RedmineTest(unittest.TestCase):
    def test_heading_matches_exact_issue_id(self):
        parser = redmine.IssueHeadingParser(725648)
        parser.feed('<h2>Другая задача #7256480</h2>'
                    '<h2><span>Редакция к ТИ-1157р4</span>&nbsp;<a>#725648</a></h2>'
                    '<h2>Похожая задача #725648</h2>')
        self.assertEqual(parser.heading, 'Редакция к ТИ-1157р4')

    @unittest.skipUnless(find_spec('redminelib'), 'python-redmine не установлен')
    def test_read_issue_prefers_page_heading_and_keeps_it_when_page_is_unavailable(self):
        client = Mock(url='http://redmine.example.test/redmine')
        client.issue.get.return_value.raw.return_value = {
            'subject': 'Заголовок задачи',
            'status': {'name': 'Проект ТИ'},
            'tracker': {'name': 'Редакция к ТИ'},
        }
        session = client.engine.session
        session.get.return_value = SimpleNamespace(
            status_code=200, text='<h2>Редакция к ТИ-1157р4 #725648</h2>')
        fields = redmine.read_issue(client, 725648)
        self.assertEqual(fields['tracker_name'], 'Редакция к ТИ-1157р4')
        session.get.assert_called_once_with(
            'http://redmine.example.test/redmine/issues/725648',
            headers={'Accept': 'text/html'}, timeout=(5, 10), allow_redirects=False)
        session.get.return_value = SimpleNamespace(status_code=403)
        self.assertEqual(redmine.read_issue(client, 725648)['tracker_name'], 'Редакция к ТИ')
        self.assertEqual(redmine.read_issue(client, 725648, previous_label=fields['tracker_name']), fields)
