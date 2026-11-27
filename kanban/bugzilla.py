"""Read one Bugzilla bug and return its data as JSON."""
from base64 import b64encode
from contextlib import closing
from datetime import date, datetime
from gzip import GzipFile
from http.client import HTTPConnection, HTTPException, HTTPSConnection
import json
import re
from socket import timeout as SocketTimeout
from urllib.parse import quote, urlsplit
from xml.parsers.expat import ExpatError
from xmlrpc import client
import zlib


class BugzillaError(ValueError):
    pass


class BugzillaAuthError(BugzillaError):
    pass


class BugzillaFault(BugzillaError):
    def __init__(self, code):
        self.code = code
        super().__init__(f'Bugzilla отклонила запрос XML-RPC (код {code}).')


CONNECT_TIMEOUT = 10
BULK_RESPONSE_TIMEOUT = 60


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


def reference_ids(value):
    return {number.lstrip('0') for number in re.findall(r'[0-9]+', value)}


def is_closed(bug):
    return str(bug.get('status') or '').strip().casefold() in {'закрыто', 'closed'}


def release_ti_keys(bug):
    action = bug.get('cf_event_1')
    actions = action if isinstance(action, list) else [action]
    if not any(' '.join(str(value).casefold().split()) in {'выпуск ти', 'выпуск редакции ти'}
               for value in actions):
        return set()
    return {(int(match[1]), int(match[2]) if match[2] else None) for match in re.finditer(
        r'(?:\bти\s*|(?<!\w))[-‑–—]\s*([0-9]+)(?:\s*[рp]\.?\s*([0-9]+))?(?!\w)',
        str(bug.get('cf_cr_num') or ''), re.IGNORECASE)}


def employee_names(employees):
    return tuple(name.strip().casefold() for name in employees if name.strip())


def matches_employee(bug, names):
    assignee = bug.get('assigned_to')
    assignee = assignee.casefold() if isinstance(assignee, str) else ''
    return any(name in assignee for name in names)


def bugzilla_login(login, domain):
    username = login.strip().rsplit('\\', 1)[-1].partition('@')[0]
    return f'{username}@{domain}' if username and domain else username


def is_assigned_to(bug, login):
    assignee = str(bug.get('assigned_to') or '').strip().casefold()
    login = login.strip().casefold()
    if not assignee or not login:
        return False
    if '@' not in assignee:
        assignee = bugzilla_login(assignee, login.partition('@')[2])
    return assignee == login


def assignee_initials(bug):
    name = bug.get('assigned_to_name') or ''
    if not name:
        name = bugzilla_login(bug.get('assigned_to') or '', '')
    return ''.join(part[0].upper() for part in re.findall(r'[^\W\d_]+', name)[:2])


def card_fields(bug):
    card = {key: bug.get(key) for key in ('id', 'summary', 'cf_event_1', 'cf_cr_num')}
    detail = bug.get('assigned_to_detail')
    if not isinstance(detail, dict):
        detail = {}
    assignee = bug.get('assigned_to') or detail.get('name')
    name = detail.get('real_name') or bug.get('assigned_to_name')
    if isinstance(assignee, str) and assignee.strip():
        card['assigned_to'] = assignee.strip()
    if isinstance(name, str) and name.strip():
        card['assigned_to_name'] = name.strip()
    if bug.get('cf_time_limit'):
        card['cf_time_limit'] = deadline_value(bug['cf_time_limit'])
    return card


def board_card(raw, number, employees):
    names = employee_names(employees)
    for bug in json.loads(raw)['bugs']:
        if (isinstance(bug, dict) and str(bug.get('id')) == number
                and not is_closed(bug) and matches_employee(bug, names)):
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
            and str(card.get('id')).lstrip('0') == number and not is_closed(card)}


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
        raise BugzillaFault(error.faultCode) from None
    except (client.ResponseError, ExpatError, ValueError, TypeError, LookupError):
        raise BugzillaError('Bugzilla вернула некорректный ответ XML-RPC. Проверьте базовый адрес сервера.') from None
    if not isinstance(payload, dict):
        raise BugzillaError('Bugzilla вернула некорректный ответ XML-RPC.')
    return payload


def request(parts, method, suffix, headers, body=None, *, operation=None, response_timeout=CONNECT_TIMEOUT):
    path = quote(parts.path.rstrip('/'), safe='/%') + suffix
    connection_type = HTTPSConnection if parts.scheme == 'https' else HTTPConnection
    limit = 4 * 1024 * 1024
    phase = 'подключение'
    timeout = CONNECT_TIMEOUT
    try:
        with closing(connection_type(parts.hostname, parts.port, timeout=CONNECT_TIMEOUT)) as connection:
            connection.connect()
            phase = 'отправка запроса'
            connection.request(method, path, body=body, headers=headers)
            phase = 'ожидание ответа'
            timeout = response_timeout
            if timeout != CONNECT_TIMEOUT:
                connection.sock.settimeout(timeout)
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
    except (TimeoutError, SocketTimeout):
        name = operation or f'{method} {suffix}'
        raise BugzillaError(f'Превышено время ожидания Bugzilla ({timeout} с). Запрос {name}, этап «{phase}».') from None
    except (OSError, HTTPException, EOFError, zlib.error):
        raise BugzillaError('Не удалось получить ответ от Bugzilla. Проверьте соединение и адрес сервера.') from None
    if len(raw) > limit:
        raise BugzillaError('Ответ Bugzilla превышает 4 МБ.')
    return raw


def xml_request(parts, method, params, login, password, number=None, *, response_timeout=CONNECT_TIMEOUT):
    if not login or not password:
        raise BugzillaError('Введите логин и пароль Bugzilla.')
    headers = {'Accept': 'text/xml', 'Content-Type': 'text/xml', 'Accept-Encoding': 'gzip'}
    body = client.dumps((dict(params, login=login, password=password),), methodname=method, encoding='utf-8')
    raw = request(parts, 'POST', '/xmlrpc.cgi', headers, body.encode('utf-8'), operation=method,
                  response_timeout=response_timeout)
    return read_xml_response(raw, number)


def response_list(payload, key):
    values = payload.get(key)
    if not isinstance(values, list):
        raise BugzillaError(f'В ответе Bugzilla отсутствует список «{key}».')
    return values


def bug_page_url(url, bug_id):
    parts = parse_url(url)
    number = bug_number(bug_id)
    return parts._replace(path=parts.path.rstrip('/') + '/show_bug.cgi', query='id=' + number).geturl()


def add_bug_comment(url, bug_id, comment, *, login, password):
    number = bug_number(bug_id)
    comment = comment.strip()
    if not comment:
        raise BugzillaError('Введите комментарий.')
    result = xml_request(parse_url(url), 'Bug.add_comment', {'id': int(number), 'comment': comment},
                         login, password, number)
    if not result.get('id'):
        raise BugzillaError('Bugzilla не подтвердила отправку. Проверьте комментарии на странице бага.')
    return result['id']


EDITABLE_FIELDS = ('cf_event_1', 'deadline', 'cf_cr_num', 'assigned_to')


def read_bug_details(url, bug_id, *, login, password):
    number = bug_number(bug_id)
    result = xml_request(parse_url(url), 'Bug.get', {'ids': [number]}, login, password, number)
    for bug in response_list(result, 'bugs'):
        if str(bug.get('id')) == number:
            return bug
    raise BugzillaError(f'Bugzilla не вернула данные бага #{number}.')


def bug_edit_choices(url, bug, employees, *, login, password):
    parts = parse_url(url)
    warnings, actions, assignees = [], None, None
    try:
        result = xml_request(parts, 'Bug.fields', {'names': ['cf_event_1']}, login, password)
        fields = response_list(result, 'fields')
        field = next((item for item in fields if item.get('name') == 'cf_event_1'), None)
        if field is None or not isinstance(field.get('values'), list):
            raise BugzillaError('Список значений не найден.')
        controller = bug.get(field.get('value_field') or field.get('visibility_field'))
        controllers = controller if isinstance(controller, list) else [controller]
        actions = [item['name'] for item in field['values'] if item.get('name') and item.get('is_active', True)
                   and (not item.get('visibility_values') or any(value in item['visibility_values'] for value in controllers))]
    except BugzillaAuthError:
        raise
    except BugzillaError:
        warnings.append('Bugzilla не вернула варианты дальнейшего действия.')
    try:
        names = employee_names(employees)
        if not names:
            raise BugzillaError('Список сотрудников пуст.')
        result = xml_request(parts, 'User.get', {'match': list(names),
                                                'include_fields': ['name', 'email', 'real_name', 'can_login']},
                             login, password)
        assignees = {}
        for user in response_list(result, 'users'):
            account = user.get('name') or user.get('email')
            full_name = user.get('real_name') or ''
            if account and user.get('can_login', True) and any(
                    match in value.casefold() for match in names for value in (account, full_name)):
                assignees[account] = f'{full_name} · {account}' if full_name else account
    except BugzillaAuthError:
        raise
    except BugzillaError:
        warnings.append('Не удалось загрузить исполнителей из Bugzilla по списку employees.json.')
    return actions, assignees, warnings


def update_bug_fields(url, bug_id, fields, *, login, password):
    number = bug_number(bug_id)
    if not fields or fields.keys() - set(EDITABLE_FIELDS):
        raise BugzillaError('Выберите поля для изменения.')
    if fields.get('deadline'):
        try:
            fields = dict(fields, deadline=date.fromisoformat(fields['deadline']).isoformat())
        except (TypeError, ValueError):
            raise BugzillaError('Укажите срок в формате ГГГГ-ММ-ДД.') from None
    try:
        result = xml_request(parse_url(url), 'Bug.update', dict(fields, ids=[int(number)]),
                             login, password, number)
    except BugzillaFault as error:
        if fields.keys() == {'deadline'}:
            reason = {
                50: 'Поле срока нельзя оставить пустым.',
                56: 'Сервер отклонил дату в поле срока.',
                104: 'Сервер не принимает поле deadline.',
                108: 'Сервер не распознал поле deadline.',
                109: 'У этой учётной записи нет права редактировать баг.',
                115: 'У этой учётной записи нет права менять срок. Проверьте её доступ к учёту времени в Bugzilla.',
            }.get(error.code)
            if reason:
                raise BugzillaError(f'{reason} Код Bugzilla {error.code}.') from None
        raise
    for bug in response_list(result, 'bugs'):
        if str(bug.get('id')) == number:
            return bug
    raise BugzillaError('Bugzilla не подтвердила сохранение бага.')


def deadline_value(value):
    return str(value)[:10] if value else ''


def require_deadline_confirmation(expected, response, current):
    if 'deadline' in current:
        if deadline_value(current['deadline']) == expected:
            return
        raise BugzillaError('Bugzilla вернула другой срок после сохранения. Проверьте поле «Срок» на странице бага.')
    change = (response.get('changes') or {}).get('deadline') or {}
    if 'added' not in change or deadline_value(change['added']) != expected:
        raise BugzillaError('Bugzilla не подтвердила новый срок. Проверьте дату на странице бага перед повтором.')


def search_board_bugs(url, project, board, employees, *, login, password):
    names = employee_names(employees)
    if not names:
        raise BugzillaError('В employees.json нет сотрудников для выгрузки багов.')
    parts = parse_url(url)
    project_key, board_key = project.casefold(), board.casefold()
    accessible = xml_request(parts, 'Product.get_accessible_products', {}, login, password,
                             response_timeout=BULK_RESPONSE_TIMEOUT)
    ids = response_list(accessible, 'ids')
    if not ids:
        return {}
    catalog = xml_request(parts, 'Product.get', {'ids': ids, 'include_fields': ['name', 'versions.name']}, login, password,
                          response_timeout=BULK_RESPONSE_TIMEOUT)
    cards = {}
    for item in response_list(catalog, 'products'):
        if project_key not in item['name'].casefold():
            continue
        for release in response_list(item, 'versions'):
            version = release['name']
            if board_key not in version.casefold():
                continue
            params = {'product': item['name'], 'version': version, 'limit': 0,
                      'include_fields': ['id', 'product', 'version', 'assigned_to', 'assigned_to.real_name', 'status',
                                         'summary', 'cf_event_1', 'cf_cr_num', 'cf_time_limit']}
            result = xml_request(parts, 'Bug.search', params, login, password, response_timeout=BULK_RESPONSE_TIMEOUT)
            for bug in response_list(result, 'bugs'):
                if (not is_closed(bug) and project_key in str(bug.get('product') or '').casefold()
                        and board_key in str(bug.get('version') or '').casefold() and matches_employee(bug, names)):
                    cards[bug_number(bug['id'])] = card_fields(bug)
    return dict(sorted(cards.items(), key=lambda item: (len(item[0]), item[0])))


def refresh_board_bugs(url, numbers, employees, *, login, password, keep_unavailable=None):
    parts = parse_url(url)
    names = employee_names(employees)
    numbers = list(dict.fromkeys(bug_number(number) for number in numbers))
    cards = {}
    for offset in range(0, len(numbers), 100):
        batch = numbers[offset:offset + 100]
        params = {'ids': batch, 'permissive': True,
                  'include_fields': ['id', 'assigned_to', 'assigned_to.real_name', 'status',
                                     'summary', 'cf_event_1', 'cf_cr_num', 'cf_time_limit']}
        response = xml_request(parts, 'Bug.get', params, login, password)
        for fault in response.get('faults', []):
            code = fault.get('faultCode')
            if code not in (100, 101, 102):
                raise BugzillaError('Bugzilla не смогла обновить часть сохранённых багов.')
            number = str(fault.get('id'))
            if code == 102 and keep_unavailable and number in keep_unavailable and not is_closed(keep_unavailable[number]):
                cards[number] = keep_unavailable[number]
        requested = set(batch)
        for bug in response_list(response, 'bugs'):
            number = bug_number(bug['id'])
            if number in requested and not is_closed(bug) and matches_employee(bug, names):
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
