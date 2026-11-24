import json
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
                                                            body=None, headers={'Accept': 'application/json'})
        connect.return_value.close.assert_called_once_with()

    @patch('kanban.bugzilla.HTTPSConnection')
    def test_authenticated_read_sends_credentials_only_in_request_body(self, connect):
        response = connect.return_value.getresponse.return_value.__enter__.return_value
        response.status = 200
        raw = '{"result":{"bugs":[{"id":42,"summary":"Тест"}]},"error":null,"id":1}\n'
        response.read.return_value = raw.encode('utf-8')
        url = 'https://bugs.example.test/bugzilla/'
        self.assertEqual(bugzilla.read_bug(url, 42, login='user@example.test', password='sample&password'), raw)

        request = connect.return_value.request.call_args
        self.assertEqual(request.args, ('POST', '/bugzilla/jsonrpc.cgi'))
        self.assertEqual(request.kwargs['headers']['Content-Type'], 'application/json')
        self.assertEqual(json.loads(request.kwargs['body']), {
            'jsonrpc': '2.0', 'id': 1, 'method': 'Bug.get',
            'params': {'ids': ['42'], 'Bugzilla_login': 'user@example.test', 'Bugzilla_password': 'sample&password'},
        })
        response.read.return_value = b'{"error":{"code":300,"message":"server error detail"},"id":1}'
        with self.assertRaisesRegex(bugzilla.BugzillaError, 'не приняла логин или пароль') as error:
            bugzilla.read_bug(url, 42, login='user@example.test', password='sample&password')
        self.assertNotIn('server error detail', str(error.exception))

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
