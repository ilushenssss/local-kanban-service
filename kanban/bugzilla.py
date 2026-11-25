"""Read one Bugzilla bug and return its data as JSON."""
from base64 import b64encode
from contextlib import closing
from datetime import datetime
from gzip import GzipFile
from http.client import HTTPConnection, HTTPException, HTTPSConnection
import json
import os
import re
from tempfile import NamedTemporaryFile
from urllib.parse import quote, urlsplit
from xml.parsers.expat import ExpatError
from xmlrpc import client
import zlib


class BugzillaError(ValueError):
    pass


class BugzillaAuthError(BugzillaError):
    pass


def connection_settings(url, domain):
    url = url.strip().rstrip('/')
    if url:
        parse_url(url)
    domain = domain.strip().lower()
    if domain.startswith('@'):
        domain = domain[1:]
    if domain and (len(domain) > 253 or any(
            not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', part) for part in domain.split('.'))):
        raise BugzillaError('Укажите почтовый домен Bugzilla, например example.org.')
    return {'url': url, 'domain': domain}


def parse_url(url):
    try:
        parts = urlsplit(url)
        if (parts.scheme in ('http', 'https') and parts.hostname and '@' not in parts.netloc
                and parts.port != 0 and not parts.query and not parts.fragment
                and not any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in url)):
            return parts
    except ValueError:
        pass
    raise BugzillaError('Укажите базовый адрес Bugzilla с http:// или https:// без учётных данных.')


def bug_number(value):
    number = str(value).strip().lstrip('0')
    if not number.isascii() or not number.isdecimal():
        raise BugzillaError('Введите положительный числовой ID бага Bugzilla.')
    return number


def employee_names(employees):
    return tuple(name.strip().casefold() for name in employees if name.strip())


def matches_employee(bug, names):
    assignee = bug.get('assigned_to')
    assignee = assignee.casefold() if isinstance(assignee, str) else ''
    return any(name in assignee for name in names)


def card_fields(bug):
    return {key: bug.get(key) for key in ('id', 'summary', 'cf_event_1', 'cf_cr_num')}


def board_card(raw, number, employees):
    names = employee_names(employees)
    for bug in json.loads(raw)['bugs']:
        if isinstance(bug, dict) and str(bug.get('id')) == number and matches_employee(bug, names):
            return card_fields(bug)
    return None


def load_board_cache(path):
    try:
        cards = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    if not isinstance(cards, dict):
        return {}
    return {number: card_fields(card) for number, card in cards.items()
            if number.isascii() and number.isdecimal() and isinstance(card, dict)
            and str(card.get('id')).lstrip('0') == number}


def save_board_cache(path, cards):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as stream:
            temporary = stream.name
            json.dump(cards, stream, ensure_ascii=False, default=xml_value_to_json)
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            os.unlink(temporary)


def xml_value_to_json(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, bytes):
        return b64encode(value).decode('ascii')
    raise TypeError('Unsupported XML-RPC value')


def read_xml_response(raw, number):
    try:
        (payload,), _ = client.loads(raw.replace(b'\x1b', b''), use_builtin_types=True)
    except client.Fault as error:
        if error.faultCode in (300, 301, 410):
            raise BugzillaAuthError('Bugzilla не приняла логин или пароль. Проверьте учётные данные и доступ к API.') from None
        if number is not None and error.faultCode in (101, 102):
            raise BugzillaError(f'Баг #{number} не найден или недоступен в Bugzilla.') from None
        raise BugzillaError('Bugzilla отклонила запрос XML-RPC.') from None
    except (client.ResponseError, ExpatError, ValueError, TypeError, LookupError):
        raise BugzillaError('Bugzilla вернула некорректный ответ XML-RPC. Проверьте базовый адрес сервера.') from None
    if not isinstance(payload, dict):
        raise BugzillaError('Bugzilla вернула некорректный ответ XML-RPC.')
    return payload


def request(parts, method, suffix, headers, body=None):
    path = quote(parts.path.rstrip('/'), safe='/%') + suffix
    connection_type = HTTPSConnection if parts.scheme == 'https' else HTTPConnection
    limit = 4 * 1024 * 1024
    try:
        with closing(connection_type(parts.hostname, parts.port, timeout=10)) as connection:
            connection.request(method, path, body=body, headers=headers)
            with connection.getresponse() as response:
                if response.status == 401:
                    raise BugzillaAuthError('Bugzilla не приняла логин или пароль.')
                if response.status == 403:
                    raise BugzillaError('Bugzilla требует авторизацию или ограничивает доступ к данным.')
                if response.status == 404:
                    raise BugzillaError('Запрошенный баг или API не найден в Bugzilla.')
                if response.status != 200:
                    raise BugzillaError(f'Bugzilla вернула HTTP {response.status}. Проверьте базовый адрес сервера.')
                if method == 'POST' and response.getheader('Content-Encoding', '').lower() == 'gzip':
                    with GzipFile(fileobj=response) as stream:
                        raw = stream.read(limit + 1)
                else:
                    raw = response.read(limit + 1)
    except (OSError, HTTPException, EOFError, zlib.error):
        raise BugzillaError('Не удалось получить ответ от Bugzilla. Проверьте соединение и адрес сервера.') from None
    if len(raw) > limit:
        raise BugzillaError('Ответ Bugzilla превышает 4 МБ.')
    return raw


def xml_request(parts, method, params, login, password, number=None):
    if not login or not password:
        raise BugzillaError('Введите логин и пароль Bugzilla.')
    headers = {'Accept': 'text/xml', 'Content-Type': 'text/xml', 'Accept-Encoding': 'gzip'}
    body = client.dumps((dict(params, login=login, password=password),), methodname=method, encoding='utf-8')
    raw = request(parts, 'POST', '/xmlrpc.cgi', headers, body.encode('utf-8'))
    return read_xml_response(raw, number)


def response_list(payload, key):
    values = payload.get(key)
    if not isinstance(values, list):
        raise BugzillaError(f'В ответе Bugzilla отсутствует список «{key}».')
    return values


def search_board_bugs(url, project, board, employees, *, login, password):
    names = employee_names(employees)
    if not names:
        raise BugzillaError('В employees.json нет сотрудников для выгрузки багов.')
    parts = parse_url(url)
    project_key, board_key = project.casefold(), board.casefold()
    accessible = xml_request(parts, 'Product.get_accessible_products', {}, login, password)
    ids = response_list(accessible, 'ids')
    if not ids:
        return {}
    catalog = xml_request(parts, 'Product.get', {'ids': ids, 'include_fields': ['name', 'versions.name']}, login, password)
    cards = {}
    for item in response_list(catalog, 'products'):
        if project_key not in item['name'].casefold():
            continue
        for release in response_list(item, 'versions'):
            version = release['name']
            if board_key not in version.casefold():
                continue
            params = {'product': item['name'], 'version': version, 'limit': 0,
                      'include_fields': ['id', 'product', 'version', 'assigned_to', 'summary', 'cf_event_1', 'cf_cr_num']}
            result = xml_request(parts, 'Bug.search', params, login, password)
            for bug in response_list(result, 'bugs'):
                if (project_key in str(bug.get('product') or '').casefold()
                        and board_key in str(bug.get('version') or '').casefold() and matches_employee(bug, names)):
                    cards[bug_number(bug['id'])] = card_fields(bug)
    return dict(sorted(cards.items(), key=lambda item: (len(item[0]), item[0])))


def refresh_board_bugs(url, numbers, employees, *, login, password):
    parts = parse_url(url)
    names = employee_names(employees)
    numbers = list(dict.fromkeys(bug_number(number) for number in numbers))
    cards = {}
    for offset in range(0, len(numbers), 100):
        batch = numbers[offset:offset + 100]
        params = {'ids': batch, 'permissive': True,
                  'include_fields': ['id', 'assigned_to', 'summary', 'cf_event_1', 'cf_cr_num']}
        response = xml_request(parts, 'Bug.get', params, login, password)
        for fault in response.get('faults', []):
            code = fault.get('faultCode')
            if code not in (100, 101, 102):
                raise BugzillaError('Bugzilla не смогла обновить часть сохранённых багов.')
        requested = set(batch)
        for bug in response_list(response, 'bugs'):
            number = bug_number(bug['id'])
            if number in requested and matches_employee(bug, names):
                cards[number] = card_fields(bug)
    return cards


def read_bug(url, bug_id, *, login=None, password=None):
    if not url:
        raise BugzillaError('Администратор ещё не указал адрес Bugzilla.')
    parts = parse_url(url)
    number = bug_number(bug_id)
    if login is not None or password is not None:
        payload = xml_request(parts, 'Bug.get', {'ids': [number]}, login, password, number)
        if not response_list(payload, 'bugs'):
            raise BugzillaError(f'Bugzilla не вернула данные бага #{number}.')
        return json.dumps(payload, ensure_ascii=False, indent=2, default=xml_value_to_json)
    raw = request(parts, 'GET', f'/rest/bug/{number}', {'Accept': 'application/json'})
    try:
        payload = json.loads(raw)
        text = raw.decode('utf-8')
    except (ValueError, RecursionError):
        raise BugzillaError('Bugzilla вернула некорректный JSON. Проверьте базовый адрес сервера.') from None
    bugs = payload.get('bugs') if isinstance(payload, dict) and not payload.get('error') else None
    if not isinstance(bugs, list) or not bugs:
        raise BugzillaError(f'Bugzilla не вернула данные бага #{number}.')
    return text
