from datetime import datetime
from gzip import compress
from io import BytesIO
import json
from socket import timeout as SocketTimeout
import unittest
from unittest.mock import MagicMock, patch
from xmlrpc import client

from kanban import bugzilla


class BugzillaTest(unittest.TestCase):
    @patch('kanban.bugzilla.xml_request')
    def test_bug_edit_fields_are_scoped_and_choices_follow_bugzilla(self, request):
        url = 'https://bugs.example.test/bugzilla'
        auth = {'login': 'user', 'password': 'sample'}
        request.side_effect = [
            {'fields': [{'name': 'cf_event_1', 'value_field': 'product', 'values': [
                {'name': 'Проверить'},
                {'name': 'Передать', 'visibility_values': ['Другой проект']},
                {'name': 'Устарело', 'is_active': False},
            ]}]},
            {'users': [{'name': 'anna@example.test', 'real_name': 'Анна'}, {'name': 'outside@example.test'}]},
            {'bugs': [{'id': 42}]},
        ]
        self.assertEqual(bugzilla.bug_edit_choices(url, {'product': 'Проект'}, ['anna'], **auth),
                         (['Проверить'], {'anna@example.test': 'Анна · anna@example.test'}, []))
        fields = {'cf_event_1': 'Проверить', 'deadline': '2026-12-25',
                  'cf_cr_num': 'ТИ-23', 'assigned_to': 'anna@example.test'}
        bugzilla.update_bug_fields(url, 42, fields, **auth)
        request.assert_called_with(bugzilla.parse_url(url), 'Bug.update', dict(fields, ids=[42]),
                                   'user', 'sample', '42')
        with self.assertRaises(bugzilla.BugzillaError):
            bugzilla.update_bug_fields(url, 42, {'status': 'Closed'}, **auth)
        self.assertEqual(request.call_count, 3)

    @patch('kanban.bugzilla.xml_request', return_value={'id': 9})
    def test_comment_targets_only_the_requested_bug(self, request):
        url = 'https://bugs.example.test/bugzilla/'
        self.assertEqual(bugzilla.bug_page_url(url, '0042'), url + 'show_bug.cgi?id=42')
        self.assertEqual(bugzilla.add_bug_comment(url, '0042', '  Текст  ', login='user', password='sample'), 9)
        request.assert_called_once_with(bugzilla.parse_url(url), 'Bug.add_comment',
                                        {'id': 42, 'comment': 'Текст'}, 'user', 'sample', '42')
        with self.assertRaises(bugzilla.BugzillaError):
            bugzilla.add_bug_comment(url, '42', ' ', login='user', password='sample')
        request.assert_called_once()

    @patch('kanban.bugzilla.xml_request')
    def test_refresh_requests_only_saved_ids_and_filters_response(self, request):
        request.return_value = {'bugs': [
            {'id': 42, 'summary': 'Обновлённый заголовок', 'assigned_to': 'Anna@example.test'},
            {'id': 7, 'assigned_to': 'other@example.test'},
            {'id': 142, 'assigned_to': 'anna@example.test'},
            {'id': 8, 'assigned_to': 'anna@example.test', 'status': 'Закрыто'},
        ], 'faults': [{'id': '9', 'faultCode': 102}]}
        cards = bugzilla.refresh_board_bugs('https://bugs.example.test', ['42', '7', '8', '9'], ['anna'],
                                           login='user@example.test', password='sample')
        self.assertEqual(cards, {'42': {'id': 42, 'summary': 'Обновлённый заголовок',
                                        'cf_event_1': None, 'cf_cr_num': None, 'assigned_to': 'Anna@example.test'}})
        args = request.call_args.args
        self.assertEqual(args[1], 'Bug.get')
        self.assertEqual(args[2]['ids'], ['42', '7', '8', '9'])
        self.assertIn('status', args[2]['include_fields'])
        self.assertTrue(args[2]['permissive'])
        old_card = {'id': 9, 'summary': 'Доступен коллеге'}
        shared = bugzilla.refresh_board_bugs('https://bugs.example.test', ['42', '7', '9'], ['anna'],
                                            login='user@example.test', password='sample', keep_unavailable={'9': old_card})
        self.assertEqual(shared, dict(cards, **{'9': old_card}))

    def test_board_card_matches_employee_substring_and_keeps_only_selected_fields(self):
        raw = json.dumps({'bugs': [
            {'id': 41, 'assigned_to': 'anna@example.test'},
            {'id': 42, 'assigned_to': 'ANNA.IVANOVA@example.test', 'cf_event_1': ['A', 'B'],
             'cf_cr_num': 0, 'summary': 'Проверка', 'assigned_to_detail': {'real_name': 'Анна Иванова'}},
            {'id': 43, 'assigned_to': 'other@example.test'},
            {'id': 44, 'assigned_to': None},
            {'id': 45, 'assigned_to': 'anna@example.test', 'status': ' ЗАКРЫТО '},
        ]})
        self.assertEqual(bugzilla.board_card(raw, '42', [' anna.ivanova ']), {
            'id': 42, 'cf_event_1': ['A', 'B'], 'cf_cr_num': 0, 'summary': 'Проверка',
            'assigned_to': 'ANNA.IVANOVA@example.test', 'assigned_to_name': 'Анна Иванова',
        })
        for number in ('43', '44', '45', '142'):
            self.assertIsNone(bugzilla.board_card(raw, number, ['anna']))
        self.assertIsNone(bugzilla.board_card(raw, '42', []))
        login = bugzilla.bugzilla_login('WORK\\Anna.Ivanova', 'example.test')
        self.assertTrue(bugzilla.is_assigned_to({'assigned_to': 'anna.ivanova'}, login))
        self.assertTrue(bugzilla.is_assigned_to({'assigned_to': 'ANNA.IVANOVA@example.test'}, login))
        self.assertFalse(bugzilla.is_assigned_to({'assigned_to': 'anna.ivanova@other.test'}, login))
        self.assertFalse(bugzilla.is_assigned_to({'assigned_to': 'new.anna.ivanova@example.test'}, login))
        self.assertEqual(bugzilla.assignee_initials({'assigned_to': login}), 'AI')

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
                      dict(bug, id=45, assigned_to='other@example.test'), dict(bug, id=47, status='закрыто')]},
            {'bugs': [dict(bug, id=46, version='2.0-218 И-02', assigned_to='bob@example.test')]},
        ]
        response.read.side_effect = [client.dumps((reply,), methodresponse=True, encoding='utf-8').encode()
                                     for reply in replies]
        cards = bugzilla.search_board_bugs('https://bugs.example.test/bugzilla', 'Проект', '2.0-218', ['anna', 'BOB'],
                                          login='user@example.test', password='sample')
        self.assertEqual(set(cards), {'42', '46'})
        self.assertEqual(cards['42'], {'id': 42, 'summary': 'Подходящий', 'cf_event_1': 'Проверить', 'cf_cr_num': 7,
                                       'assigned_to': 'ANNA@example.test'})
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
            self.assertIn('status', params['include_fields'])
            self.assertIn('assigned_to.real_name', params['include_fields'])
            versions.append(params['version'])
        self.assertEqual(versions, ['2.0-218 И-01', '2.0-218 И-02'])
        self.assertEqual(connect.return_value.connect.call_count, len(replies))
        self.assertEqual([call.args for call in connect.return_value.sock.settimeout.call_args_list], [(60,)] * len(replies))

    @patch('kanban.bugzilla.HTTPSConnection')
    def test_bulk_timeout_reports_request_and_phase(self, connect):
        connection = connect.return_value
        args = ('https://bugs.example.test', 'Проект', 'Версия', ['anna'])
        credentials = {'login': 'user@example.test', 'password': 'sample'}
        connection.connect.side_effect = SocketTimeout('timed out')
        with self.assertRaisesRegex(bugzilla.BugzillaError, r'10 с.*Product\.get_accessible_products.*подключение'):
            bugzilla.search_board_bugs(*args, **credentials)
        connection.sock.settimeout.assert_not_called()

        connection.connect.side_effect = None
        connection.getresponse.side_effect = SocketTimeout('timed out')
        with self.assertRaisesRegex(bugzilla.BugzillaError, r'60 с.*Product\.get_accessible_products.*ожидание ответа'):
            bugzilla.search_board_bugs(*args, **credentials)
        connection.sock.settimeout.assert_called_once_with(60)

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
