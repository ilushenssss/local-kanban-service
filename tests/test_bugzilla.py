from datetime import datetime
from gzip import compress
from io import BytesIO
import json
import unittest
from unittest.mock import MagicMock, patch
from xmlrpc import client

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
    def test_authenticated_read_uses_xmlrpc_and_preserves_bug_fields(self, connect):
        response = connect.return_value.getresponse.return_value.__enter__.return_value
        response.status = 200
        response.getheader.return_value = 'gzip'
        payload = {'bugs': [{'id': 42, 'summary': 'Тест\x1b', 'created': datetime(2026, 1, 2, 3, 4),
                             'custom': [1, 2], 'binary': b'abc'}]}
        raw = client.dumps((payload,), methodresponse=True, encoding='utf-8').encode('utf-8')
        response.read.side_effect = BytesIO(compress(raw)).read
        url = 'https://bugs.example.test/bugzilla/'
        result = bugzilla.read_bug(url, 42, login='user@example.test', password='sample&password')
        self.assertEqual(json.loads(result), {'bugs': [{'id': 42, 'summary': 'Тест', 'created': '2026-01-02T03:04:00',
                                                       'custom': [1, 2], 'binary': 'YWJj'}]})

        request = connect.return_value.request.call_args
        self.assertEqual(request.args, ('POST', '/bugzilla/xmlrpc.cgi'))
        self.assertEqual(request.kwargs['headers']['Content-Type'], 'text/xml')
        self.assertEqual(client.loads(request.kwargs['body']),
                         (({'ids': ['42'], 'login': 'user@example.test', 'password': 'sample&password'},), 'Bug.get'))

        response.getheader.return_value = ''
        response.read.side_effect = None
        response.read.return_value = client.dumps(client.Fault(300, 'server error detail')).encode('utf-8')
        with self.assertRaisesRegex(bugzilla.BugzillaError, 'не приняла логин или пароль') as error:
            bugzilla.read_bug(url, 42, login='user@example.test', password='sample&password')
        self.assertNotIn('server error detail', str(error.exception))
        response.read.return_value = b'<html>Login</html>'
        with self.assertRaisesRegex(bugzilla.BugzillaError, 'некорректный ответ XML-RPC'):
            bugzilla.read_bug(url, 42, login='user@example.test', password='sample&password')

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
