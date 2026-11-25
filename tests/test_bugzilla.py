from datetime import datetime
from gzip import compress
from io import BytesIO
import json
import unittest
from unittest.mock import MagicMock, patch
from xmlrpc import client

from kanban import bugzilla


class BugzillaTest(unittest.TestCase):
    @patch('kanban.bugzilla.xml_request')
    def test_refresh_requests_only_saved_ids_and_filters_response(self, request):
        request.return_value = {'bugs': [
            {'id': 42, 'summary': 'Обновлённый заголовок', 'assigned_to': 'Anna@example.test'},
            {'id': 7, 'assigned_to': 'other@example.test'},
            {'id': 142, 'assigned_to': 'anna@example.test'},
        ], 'faults': [{'id': '9', 'faultCode': 102}]}
        cards = bugzilla.refresh_board_bugs('https://bugs.example.test', ['42', '7', '9'], ['anna'],
                                           login='user@example.test', password='sample')
        self.assertEqual(cards, {'42': {'id': 42, 'summary': 'Обновлённый заголовок',
                                        'cf_event_1': None, 'cf_cr_num': None}})
        args = request.call_args.args
        self.assertEqual(args[1], 'Bug.get')
        self.assertEqual(args[2]['ids'], ['42', '7', '9'])
        self.assertTrue(args[2]['permissive'])

    def test_board_card_matches_employee_substring_and_keeps_only_selected_fields(self):
        raw = json.dumps({'bugs': [
            {'id': 41, 'assigned_to': 'anna@example.test'},
            {'id': 42, 'assigned_to': 'ANNA.IVANOVA@example.test', 'cf_event_1': ['A', 'B'],
             'cf_cr_num': 0, 'summary': 'Проверка'},
            {'id': 43, 'assigned_to': 'other@example.test'},
            {'id': 44, 'assigned_to': None},
        ]})
        self.assertEqual(bugzilla.board_card(raw, '42', [' anna.ivanova ']), {
            'id': 42, 'cf_event_1': ['A', 'B'], 'cf_cr_num': 0, 'summary': 'Проверка',
        })
        for number in ('43', '44', '142'):
            self.assertIsNone(bugzilla.board_card(raw, number, ['anna']))
        self.assertIsNone(bugzilla.board_card(raw, '42', []))

    @patch('kanban.bugzilla.HTTPSConnection')
    def test_bulk_search_resolves_exact_products_and_versions_and_verifies_all_substrings(self, connect):
        response = connect.return_value.getresponse.return_value.__enter__.return_value
        response.status = 200
        response.getheader.return_value = ''
        bug = {'id': 42, 'product': 'Система ПРОЕКТ', 'version': '2.0-218 И-01',
               'assigned_to': 'ANNA@example.test', 'summary': 'Подходящий', 'cf_event_1': 'Проверить', 'cf_cr_num': 7}
        replies = [
            {'ids': [1, 2]},
            {'products': [{'name': 'Система ПРОЕКТ', 'versions': [{'name': '2.0-218 И-01'}, {'name': '2.0-219'},
                                                                {'name': '2.0-218 И-02'}]},
                          {'name': 'Другая система', 'versions': [{'name': '2.0-218 И-01'}]}]},
            {'bugs': [bug, dict(bug, id=43, product='Другая система'), dict(bug, id=44, version='2.0-219'),
                      dict(bug, id=45, assigned_to='other@example.test')]},
            {'bugs': [dict(bug, id=46, version='2.0-218 И-02', assigned_to='bob@example.test')]},
        ]
        response.read.side_effect = [client.dumps((reply,), methodresponse=True, encoding='utf-8').encode()
                                     for reply in replies]
        cards = bugzilla.search_board_bugs('https://bugs.example.test/bugzilla', 'Проект', '2.0-218', ['anna', 'BOB'],
                                          login='user@example.test', password='sample')
        self.assertEqual(set(cards), {'42', '46'})
        self.assertEqual(cards['42'], {'id': 42, 'summary': 'Подходящий', 'cf_event_1': 'Проверить', 'cf_cr_num': 7})
        calls = []
        for request in connect.return_value.request.call_args_list:
            self.assertEqual(request.args, ('POST', '/bugzilla/xmlrpc.cgi'))
            (params,), method = client.loads(request.kwargs['body'])
            calls.append((method, params))
        self.assertEqual([method for method, _ in calls],
                         ['Product.get_accessible_products', 'Product.get', 'Bug.search', 'Bug.search'])
        self.assertEqual(calls[1][1]['ids'], [1, 2])
        versions = []
        for _, params in calls[2:]:
            self.assertEqual(params['product'], 'Система ПРОЕКТ')
            self.assertEqual(params['limit'], 0)
            self.assertIn('cf_cr_num', params['include_fields'])
            versions.append(params['version'])
        self.assertEqual(versions, ['2.0-218 И-01', '2.0-218 И-02'])

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
