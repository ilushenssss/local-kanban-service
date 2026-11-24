import unittest
from unittest.mock import MagicMock, patch

from kanban import bugzilla


class BugzillaTest(unittest.TestCase):
    @patch('kanban.bugzilla.HTTPSConnection')
    def test_read_bug_requests_specific_id_and_preserves_raw_json(self, connect):
        response = connect.return_value.getresponse.return_value.__enter__.return_value
        response.status = 200
        raw = '{ "bugs": [{"id": 42, "summary": "Тест", "custom": [1, 2]}] }\n'
        response.read.return_value = raw.encode('utf-8')

        self.assertEqual(bugzilla.read_bug('https://bugs.example.test:8443/bugzilla/', ' 0042 '), raw)
        connect.assert_called_once_with('bugs.example.test', 8443, timeout=10)
        connect.return_value.request.assert_called_once_with('GET', '/bugzilla/rest/bug/42',
                                                            headers={'Accept': 'application/json'})
        connect.return_value.close.assert_called_once_with()

    @patch('kanban.bugzilla.HTTPConnection')
    def test_read_bug_reports_http_and_data_errors(self, connect):
        response = MagicMock()
        connect.return_value.getresponse.return_value.__enter__.return_value = response
        for status, body, message in [
            (403, b'', 'авторизацию'), (404, b'', 'не найден'), (302, b'', 'HTTP 302'),
            (200, b'<html>Login</html>', 'некорректный JSON'), (200, b'{"bugs": []}', 'не вернула данные'),
        ]:
            with self.subTest(status=status, body=body):
                response.status, response.read.return_value = status, body
                with self.assertRaisesRegex(bugzilla.BugzillaError, message):
                    bugzilla.read_bug('http://bugs.example.test', 42)
        with self.assertRaisesRegex(bugzilla.BugzillaError, 'числовой ID'):
            bugzilla.read_bug('http://bugs.example.test', '42?x=1')
        with self.assertRaisesRegex(bugzilla.BugzillaError, 'Администратор'):
            bugzilla.read_bug('', 42)
