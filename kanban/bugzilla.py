"""Read one Bugzilla bug as the original REST JSON response."""
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


def read_bug(url, bug_id):
    if not url:
        raise BugzillaError('Администратор ещё не указал адрес Bugzilla.')
    parts = parse_url(url)
    number = bug_number(bug_id)
    path = quote(parts.path.rstrip('/'), safe='/%') + f'/rest/bug/{number}'
    connection_type = HTTPSConnection if parts.scheme == 'https' else HTTPConnection
    limit = 4 * 1024 * 1024
    try:
        with closing(connection_type(parts.hostname, parts.port, timeout=10)) as connection:
            connection.request('GET', path, headers={'Accept': 'application/json'})
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
    bugs = payload.get('bugs') if isinstance(payload, dict) and not payload.get('error') else None
    if not isinstance(bugs, list) or not bugs:
        raise BugzillaError(f'Bugzilla не вернула данные бага #{number}.')
    return text
