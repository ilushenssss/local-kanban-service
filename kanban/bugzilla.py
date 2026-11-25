"""Read one Bugzilla bug and return its data as JSON."""
from base64 import b64encode
from contextlib import closing
from datetime import datetime
from gzip import GzipFile
from http.client import HTTPConnection, HTTPException, HTTPSConnection
import json
from urllib.parse import quote, urlsplit
from xml.parsers.expat import ExpatError
from xmlrpc import client
import zlib


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


def xml_value_to_json(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, bytes):
        return b64encode(value).decode('ascii')
    raise TypeError('Unsupported XML-RPC value')


def read_xml_bug(raw, number):
    try:
        (payload,), _ = client.loads(raw.replace(b'\x1b', b''), use_builtin_types=True)
    except client.Fault as error:
        if error.faultCode in (300, 301, 410):
            raise BugzillaError('Bugzilla не приняла логин или пароль. Проверьте учётные данные и доступ к API.') from None
        if error.faultCode in (101, 102):
            raise BugzillaError(f'Баг #{number} не найден или недоступен в Bugzilla.') from None
        raise BugzillaError(f'Bugzilla отклонила запрос XML-RPC для бага #{number}.') from None
    except (client.ResponseError, ExpatError, ValueError, TypeError, LookupError):
        raise BugzillaError('Bugzilla вернула некорректный ответ XML-RPC. Проверьте базовый адрес сервера.') from None
    if not isinstance(payload, dict) or not isinstance(payload.get('bugs'), list) or not payload['bugs']:
        raise BugzillaError(f'Bugzilla не вернула данные бага #{number}.')
    return json.dumps(payload, ensure_ascii=False, indent=2, default=xml_value_to_json)


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
        method, path = 'POST', base_path + '/xmlrpc.cgi'
        headers = {'Accept': 'text/xml', 'Content-Type': 'text/xml', 'Accept-Encoding': 'gzip'}
        params = {'ids': [number], 'login': login, 'password': password}
        body = client.dumps((params,), methodname='Bug.get', encoding='utf-8').encode('utf-8')
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
                    raise BugzillaError(f'Баг #{number} или API не найден в Bugzilla.')
                if response.status != 200:
                    raise BugzillaError(f'Bugzilla вернула HTTP {response.status}. Проверьте базовый адрес сервера.')
                if authenticated and response.getheader('Content-Encoding', '').lower() == 'gzip':
                    with GzipFile(fileobj=response) as stream:
                        raw = stream.read(limit + 1)
                else:
                    raw = response.read(limit + 1)
    except (OSError, HTTPException, EOFError, zlib.error):
        raise BugzillaError('Не удалось получить ответ от Bugzilla. Проверьте соединение и адрес сервера.') from None
    if len(raw) > limit:
        raise BugzillaError('Ответ Bugzilla превышает 4 МБ.')
    if authenticated:
        return read_xml_bug(raw, number)
    try:
        payload = json.loads(raw)
        text = raw.decode('utf-8')
    except (ValueError, RecursionError):
        raise BugzillaError('Bugzilla вернула некорректный JSON. Проверьте базовый адрес сервера.') from None
    bugs = payload.get('bugs') if isinstance(payload, dict) and not payload.get('error') else None
    if not isinstance(bugs, list) or not bugs:
        raise BugzillaError(f'Bugzilla не вернула данные бага #{number}.')
    return text
