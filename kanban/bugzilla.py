"""Read one Bugzilla bug and return the original JSON response."""
from contextlib import closing
from http.client import HTTPConnection, HTTPException, HTTPSConnection
import json
from urllib.parse import quote, urlsplit


class BugzillaError(ValueError):
    pass


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


def read_bug(url, bug_id, *, login=None, password=None):
    if not url:
        raise BugzillaError('Администратор ещё не указал адрес Bugzilla.')
    parts = parse_url(url)
    number = bug_number(bug_id)
    base_path = quote(parts.path.rstrip('/'), safe='/%')
    headers = {'Accept': 'application/json'}
    authenticated = login is not None or password is not None
    if authenticated:
        if not login or not password:
            raise BugzillaError('Введите логин и пароль Bugzilla.')
        # Bugzilla принимает учётные данные в теле JSON-RPC вызова Bug.get.
        method, path = 'POST', base_path + '/jsonrpc.cgi'
        headers['Content-Type'] = 'application/json'
        body = json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'Bug.get', 'params': {
            'ids': [number], 'Bugzilla_login': login, 'Bugzilla_password': password,
        }}).encode('utf-8')
    else:
        method, path = 'GET', base_path + f'/rest/bug/{number}'
        body = None
    connection_type = HTTPSConnection if parts.scheme == 'https' else HTTPConnection
    limit = 4 * 1024 * 1024
    try:
        with closing(connection_type(parts.hostname, parts.port, timeout=10)) as connection:
            connection.request(method, path, body=body, headers=headers)
            with connection.getresponse() as response:
                if response.status in (401, 403):
                    raise BugzillaError('Bugzilla требует авторизацию или ограничивает доступ к этому багу.')
                if response.status == 404:
                    raise BugzillaError(f'Баг #{number} или REST API не найден в Bugzilla.')
                if response.status != 200:
                    raise BugzillaError(f'Bugzilla вернула HTTP {response.status}. Проверьте базовый адрес сервера.')
                raw = response.read(limit + 1)
    except (OSError, HTTPException):
        raise BugzillaError('Не удалось получить ответ от Bugzilla. Проверьте соединение и адрес сервера.') from None
    if len(raw) > limit:
        raise BugzillaError('Ответ Bugzilla превышает 4 МБ.')
    try:
        payload = json.loads(raw)
        text = raw.decode('utf-8')
    except (ValueError, RecursionError):
        raise BugzillaError('Bugzilla вернула некорректный JSON. Проверьте базовый адрес сервера.') from None
    if authenticated and isinstance(payload, dict):
        error = payload.get('error')
        if isinstance(error, dict) and error.get('code') in (300, 301, 410):
            raise BugzillaError('Bugzilla не приняла логин или пароль. Проверьте учётные данные и доступ к API.')
        if isinstance(error, dict) and error.get('code') == 102:
            raise BugzillaError(f'Нет доступа к багу #{number} в Bugzilla.')
        payload = payload.get('result') if not error else None
    bugs = payload.get('bugs') if isinstance(payload, dict) and not payload.get('error') else None
    if not isinstance(bugs, list) or not bugs:
        raise BugzillaError(f'Bugzilla не вернула данные бага #{number}.')
    return text
