"""Read-only Redmine access. The client and its credentials live in one TeamSync session."""
from datetime import date

import re

from urllib.parse import urlsplit


class RedmineError(ValueError):
    pass


class RedmineAuthError(RedmineError):
    pass


def validate_url(url):
    try:
        parts = urlsplit(url)
        valid = (parts.scheme in ('http', 'https') and parts.hostname and '@' not in parts.netloc
                 and not parts.query and not parts.fragment and not any(ch.isspace() for ch in url)
                 and parts.port != 0)
    except ValueError:
        valid = False
    if not valid:
        raise RedmineError('Укажите адрес Redmine с http:// или https:// без учётных данных.')


def connect(url, login, password):
    validate_url(url)
    try:
        from redminelib import Redmine
        from redminelib.engines import SyncEngine
        from redminelib.exceptions import AuthError
    except ImportError:
        raise RedmineError('Установите компоненты из requirements/redmine.txt.') from None

    class ReadEngine(SyncEngine):
        @staticmethod
        def construct_request_kwargs(method, headers, params, data):
            options = SyncEngine.construct_request_kwargs(method, headers, params, data)
            return dict(options, timeout=(5, 10), allow_redirects=False)

    try:
        client = Redmine(url.rstrip('/'), username=login, password=password,
                         engine=ReadEngine)
        client.auth()
    except AuthError:
        raise RedmineAuthError('Redmine не принял логин или пароль.') from None
    except Exception:
        raise RedmineError('Не удалось подключиться к Redmine. Проверьте адрес, соединение и доступ к API.') from None
    return client


APPROVAL_COLORS = {
    'нет': 'red',
    'на согласовании': 'red',
    'есть замечания': 'yellow',
    'да': 'green',
}
DEPARTMENT_PREFIX = re.compile(r'\bТИ\s+согласовано\s+со?(?=\s|[:–—-]|$)(?:\s+отд\.)?', re.IGNORECASE)


def department_approvals(fields):
    approvals = []
    for field in fields:
        if not isinstance(field, dict):
            continue
        name, value = field.get('name'), field.get('value')
        if not isinstance(name, str) or not isinstance(value, str):
            continue
        match = DEPARTMENT_PREFIX.search(name)
        if match and status_key(value) in APPROVAL_COLORS:
            department = name[match.end():].strip().lstrip(':–—-').strip()
            if department:
                approvals.append({'department': department, 'status': value.strip()})
    return approvals


BASIS_NUMBER = re.compile(r'ТИ\(200\)-[0-9]{4}-[0-9]{2}', re.IGNORECASE)


def basis_number(value):
    match = BASIS_NUMBER.match(value)
    return match.group().upper() if match else None


CHECK_SHEET_FIELD = 'Лист проверки оформлен?'


def issue_references(fields):
    names = {'основание для открытия ти': 'basis', '№ bz': 'bugs'}
    references = {}
    for field in fields:
        if not isinstance(field, dict):
            continue
        name, value = field.get('name'), field.get('value')
        if not isinstance(name, str):
            continue
        name = status_key(name)
        if name == CHECK_SHEET_FIELD.casefold():
            answer = {'1': 'Да', '0': 'Нет', 'да': 'Да', 'нет': 'Нет'}.get(status_key(str(value)))
            if answer:
                references['check_sheet'] = answer
            continue
        key = names.get(name)
        if key and isinstance(value, str):
            parts = re.split(r'[,\r\n]+', value)
            references[key] = [part.strip() for part in parts if part.strip()]
            if key == 'basis':
                references[key] = [basis_number(part) or part for part in references[key]]
    return references


def read_basis_statuses(client, project_id, numbers, *, cache=None):
    from redminelib.exceptions import AuthError

    statuses = dict.fromkeys(numbers)
    pending = set(statuses)
    try:
        candidates = project_issues(client, project_id, cache, status_id='*')
        for issue in candidates:
            fields = issue.raw()
            for field in fields.get('custom_fields') or []:
                if status_key(field.get('name') or '') != 'регистрационный номер_лпив':
                    continue
                linked = pending.intersection(number.upper() for number in BASIS_NUMBER.findall(str(field.get('value') or '')))
                if linked:
                    status = (fields.get('status') or {}).get('name')
                    if not status:
                        raise RedmineError('Redmine не вернул статус задачи.')
                    statuses.update(dict.fromkeys(linked, status))
                    pending.difference_update(linked)
            if not pending:
                break
    except AuthError:
        raise RedmineAuthError('Сеанс Redmine недействителен. Войдите заново.') from None
    except Exception:
        raise RedmineError('Не удалось проверить основания в проекте Redmine #{}.'.format(project_id)) from None
    return statuses


def with_basis_statuses(references, project, statuses):
    matches = {number: statuses[number] for number in references.get('basis', []) if number in statuses}
    return dict(references, basis_project=project, basis_statuses=matches) if matches else references


def custom_field_values(data):
    return {status_key(field['name']): field.get('value')
            for field in data.get('custom_fields') or [] if field.get('name')}


def resource_ids(value):
    values = value if isinstance(value, list) else [value]
    for raw in values:
        key = str(raw).strip()
        if key.isascii() and key.isdecimal() and int(key) > 0:
            yield int(key)


def version_names_for(client, value, cache):
    from redminelib.exceptions import ForbiddenError, ResourceNotFoundError

    for version_id in resource_ids(value):
        if version_id not in cache:
            try:
                cache[version_id] = client.version.get(version_id).name
            except (ForbiddenError, ResourceNotFoundError):
                cache[version_id] = None
        if cache[version_id]:
            yield cache[version_id]


def ti_is_closed(status):
    return status_key(status or '') == 'ти закрыто'


def project_issues(client, project_id, cache, **filters):
    if cache is None:
        return client.issue.filter(project_id=project_id, **filters)
    if project_id not in cache:
        cache[project_id] = list(client.issue.filter(project_id=project_id, **filters))
    return cache[project_id]


def sppi_family(client, root):
    """Yield full Redmine tasks and their parent IDs, starting at the root."""
    from redminelib.exceptions import ForbiddenError, ResourceNotFoundError

    root_id = root.id
    try:
        data = dict(root.raw(), **client.issue.get(root_id, include='children').raw())
    except (ForbiddenError, ResourceNotFoundError):
        return
    yield root_id, data, None
    seen = {root_id}
    pending = [(root_id, child) for child in reversed(data.get('children') or [])]
    while pending:
        parent_id, child = pending.pop()
        child_id = child['id']
        if child_id in seen:
            continue
        seen.add(child_id)
        try:
            child = dict(child, **client.issue.get(child_id, include='children').raw())
        except (ForbiddenError, ResourceNotFoundError):
            pending.extend((parent_id, node) for node in reversed(child.get('children') or []))
            continue
        yield child_id, child, parent_id
        pending.extend((child_id, node) for node in reversed(child.get('children') or []))


def iter_sppi_issues(client, project_id, board, employees, *, on_issue=None):
    from redminelib.exceptions import AuthError, ForbiddenError, ResourceNotFoundError

    board_key = status_key(board)
    employee_keys = {status_key(name) for name in employees}
    people, versions = {}, {}
    if not employee_keys or not board_key:
        return
    try:
        matching = set()
        for issue in client.issue.filter(project_id=project_id, status_id='*', parent_id='!*'):
            if issue.raw().get('parent'):
                continue
            family_statuses = {}
            for issue_id, data, parent_id in sppi_family(client, issue):
                status = (data.get('status') or {}).get('name') or ''
                parent_status = family_statuses.get(parent_id)
                if parent_status is not None:
                    family_statuses[issue_id] = parent_status
                if issue_id in matching or ti_is_closed(status):
                    continue
                fields = custom_field_values(data)
                responsible = list(resource_ids(fields.get('ответственный за выпуск ти')))
                version_labels = list(dict.fromkeys(
                    version_names_for(client, fields.get('приоритет реализации'), versions)))
                for user_id in responsible:
                    if user_id in people:
                        continue
                    try:
                        person = client.user.get(user_id).raw()
                    except (ForbiddenError, ResourceNotFoundError):
                        person = {}
                    first, last = person.get('firstname') or '', person.get('lastname') or ''
                    names = (person.get('login') or '', person.get('name') or '', f'{first} {last}', f'{last} {first}')
                    label = names[2].strip() or names[1] or names[0] or '#{} (имя недоступно)'.format(user_id)
                    people[user_id] = label, any(status_key(name) in employee_keys for name in names)
                if on_issue is not None:
                    owners = ', '.join(dict.fromkeys(people[user_id][0] for user_id in responsible)) or '—'
                    names = ', '.join(version_labels) or '—'
                    on_issue('#{} · Ответственный: {} · Версия: {}'.format(issue_id, owners, names))
                own_match = any(people[user_id][1] for user_id in responsible) and any(
                    board_key in status_key(name) for name in version_labels)
                if parent_status is not None or own_match:
                    matching.add(issue_id)
                    family_statuses[issue_id] = status if parent_status is None else parent_status
                    yield issue_id, data, parent_status
    except AuthError:
        raise RedmineAuthError('Сеанс Redmine недействителен. Войдите заново.') from None
    except Exception:
        raise RedmineError('Не удалось прочитать задачи СППИ в проекте Redmine #{}.'.format(project_id)) from None


def read_identification_cards(client, project_id):
    from redminelib.exceptions import AuthError

    cards = []
    version_names = {}
    try:
        for issue in client.issue.filter(project_id=project_id, status_id='*'):
            data = issue.raw()
            fields = custom_field_values(data)
            approval = fields.get('ти согласовано с отд.цз', fields.get('ти согласовано с отд. цз', 'Не требуется'))
            approval = str(approval or '').strip()
            if status_key(approval) == 'не требуется':
                continue
            registration = fields.get('регистрационный номер_лпив') or ''
            subject = data.get('subject') or ''
            number = BASIS_NUMBER.search(str(registration)) or BASIS_NUMBER.search(subject)
            if not number:
                continue
            versions = list(dict.fromkeys(version_names_for(client, fields.get('приоритет реализации_лпив'), version_names)))
            status = (data.get('status') or {}).get('name') or ''
            label = number.group().upper()
            cards.append({'number': label, 'subject': subject if subject != label else '',
                          'approval': approval, 'status': status, 'versions': versions})
    except AuthError:
        raise RedmineAuthError('Сеанс Redmine недействителен. Войдите заново.') from None
    except Exception:
        raise RedmineError('Не удалось загрузить ИД из проекта Redmine #{}.'.format(project_id)) from None
    return cards


TI_LOCATION_FIELD = 'расположение ти'


def find_kris_issues(client, issue_ids, project_id, *, cache=None):
    pending = {str(issue_id) for issue_id in issue_ids}
    matches = {}
    if not pending:
        return matches
    candidates = project_issues(client, project_id, cache, status_id='open', parent_id='!*')
    for candidate in candidates:
        for field in candidate.raw().get('custom_fields') or []:
            if status_key(field.get('name') or '') != TI_LOCATION_FIELD:
                continue
            linked = pending.intersection(re.findall(r'[0-9]+', str(field.get('value') or '')))
            for issue_id in linked:
                matches[int(issue_id)] = candidate.id
            pending.difference_update(linked)
        if not pending:
            break
    return matches


def coding_deadline(issue):
    try:
        due = date.fromisoformat(issue.get('due_date')).isoformat()
    except (TypeError, ValueError):
        due = None
    return {'subject': issue.get('subject') or '#{}'.format(issue['id']), 'due_date': due}


def kris_assignee_name(client, assignee, cache):
    from redminelib.exceptions import ForbiddenError, ResourceNotFoundError

    user_id = assignee.get('id')
    if user_id and user_id not in cache:
        try:
            user = client.user.get(user_id).raw()
        except (ForbiddenError, ResourceNotFoundError):
            user = {}
        cache[user_id] = ' '.join((user.get(key) or '').strip() for key in ('lastname', 'firstname')).strip()
    return cache.get(user_id) or assignee.get('name') or ''


def read_kris_children(client, parent_id, assignee_cache):
    parent = client.issue.get(parent_id, include='children').raw()
    subject = (parent.get('subject') or '').strip()
    parent_title = ' '.join(subject.split())
    children = parent.get('children') or []
    done = total = 0
    coding = []
    subtasks = []
    for child in children:
        status = (child.get('status') or {}).get('name')
        if status_key(status or '') == 'действие не требуется':
            continue
        missing_due_date = status_key(status or '') == 'кодирование' and 'due_date' not in child
        if not status or not child.get('subject') or 'assigned_to' not in child or missing_due_date:
            child = dict(child, **client.issue.get(child['id']).raw())
            status = (child.get('status') or {}).get('name')
        if not status:
            raise RedmineError('Redmine не вернул статус подзадачи.')
        key = status_key(status)
        if key == 'действие не требуется':
            continue
        title = ' '.join((child.get('subject') or '').split())
        if parent_title and parent_title in title:
            title = title.replace(parent_title, '', 1).strip(' :–—·-')
        assignee = kris_assignee_name(client, child.get('assigned_to') or {}, assignee_cache)
        subtasks.append({'title': title, 'assignee': assignee, 'status': status.strip()})
        total += 1
        done += 'выполнено' in key
        if key == 'кодирование':
            coding.append(coding_deadline(child))
    if not coding and status_key((parent.get('status') or {}).get('name') or '') == 'кодирование':
        coding.append(coding_deadline(parent))
    progress = {'done': done, 'total': total, 'subtasks': subtasks}
    if subject:
        progress['subject'] = subject
    if coding:
        progress['coding'] = coding
    return progress


def read_kris_progress(client, issue_ids, project_id, *, cache=None):
    from redminelib.exceptions import AuthError

    progress = dict.fromkeys(issue_ids)
    try:
        matches = find_kris_issues(client, progress, project_id, cache=cache)
    except AuthError:
        raise RedmineAuthError('Сеанс Redmine недействителен. Войдите заново.') from None
    except Exception:
        return {}, ['Не удалось найти задачи КРИС_ТИ в проекте #{}.'.format(project_id)]
    errors = []
    children = {}
    assignee_cache = cache.setdefault('kris_assignees', {}) if cache is not None else {}
    for issue_id, parent_id in matches.items():
        if parent_id not in children:
            try:
                children[parent_id] = read_kris_children(client, parent_id, assignee_cache)
            except AuthError:
                raise RedmineAuthError('Сеанс Redmine недействителен. Войдите заново.') from None
            except Exception:
                children[parent_id] = None
                errors.append('Не удалось прочитать подзадачи КРИС_ТИ #{}.'.format(parent_id))
        if children[parent_id] is None:
            del progress[issue_id]
        else:
            progress[issue_id] = children[parent_id]
    return progress, errors


def kris_percent(progress):
    return round(100 * progress['done'] / progress['total']) if progress['total'] else 0


def kris_progress_text(progress):
    if progress is None:
        return '—'
    if not progress['total']:
        return 'Подзадач нет'
    return '{}% ({}/{})'.format(kris_percent(progress), progress['done'], progress['total'])


def kris_coding_lines(progress):
    coding = (progress or {}).get('coding', [])
    if not coding:
        return []
    dates = {issue['due_date'] for issue in coding}
    if len(dates) == 1:
        due = coding[0]['due_date']
        if due:
            return ['Кодирование до ' + date.fromisoformat(due).strftime('%d.%m')]
        return ['Кодирование: срок не указан']
    lines = []
    for issue in coding:
        due = issue['due_date']
        deadline = date.fromisoformat(due).strftime('%d.%m') if due else 'срок не указан'
        lines.append('{}: {}'.format(issue['subject'], deadline))
    return lines


def kris_coding_deadline(progress):
    coding = (progress or {}).get('coding', [])
    dates = sorted({issue['due_date'] for issue in coding if issue['due_date']})
    if not dates:
        return 'Срок не указан' if coding else ''
    first = date.fromisoformat(dates[0]).strftime('%d.%m')
    if len(dates) == 1:
        return 'до ' + first
    last = date.fromisoformat(dates[-1]).strftime('%d.%m')
    return first + '–' + last


def issue_subtasks(client, fields, issue_id):
    from redminelib.exceptions import ForbiddenError, ResourceNotFoundError

    subtasks = []
    seen = {issue_id}
    pending = list(reversed(fields.get('children') or []))
    while pending:
        child = pending.pop()
        child_id = child['id']
        if child_id in seen:
            continue
        seen.add(child_id)
        if not child.get('subject') or not child.get('status'):
            try:
                child = dict(child, **client.issue.get(child_id, include='children').raw())
            except (ForbiddenError, ResourceNotFoundError):
                pass
        subtasks.append({'id': child_id, 'title': child.get('subject') or '',
                         'status': (child.get('status') or {}).get('name') or ''})
        pending.extend(reversed(child.get('children') or []))
    return subtasks


def ti_responsible(client, issue_id, fields, cache):
    from redminelib.exceptions import ForbiddenError, ResourceNotFoundError

    parent_id = (fields.get('parent') or {}).get('id')
    ids_key = ('responsible_ids', issue_id)
    cache[ids_key] = tuple(resource_ids(custom_field_values(fields).get('ответственный за выпуск ти')))
    if parent_id:
        ids_key = ('responsible_ids', parent_id)
        if ids_key not in cache:
            try:
                parent = client.issue.get(parent_id).raw()
            except (ForbiddenError, ResourceNotFoundError):
                parent = {}
            cache[ids_key] = tuple(resource_ids(custom_field_values(parent).get('ответственный за выпуск ти')))
    result = []
    for user_id in dict.fromkeys(cache[ids_key]):
        user_key = ('responsible_user', user_id)
        if user_key not in cache:
            try:
                user = client.user.get(user_id).raw()
            except (ForbiddenError, ResourceNotFoundError):
                user = {}
            parts = [(user.get(field) or '').strip() for field in ('firstname', 'lastname')]
            name = ' '.join(part for part in parts if part)
            initials = ''.join(part[0].upper() for part in parts if part)
            cache[user_key] = {'name': name, 'initials': initials} if initials else None
        if cache[user_key]:
            result.append(cache[user_key])
    return result


def read_issue(client, issue_id, *, skip_closed=False, data=None, responsible_cache=None):
    from redminelib.exceptions import AuthError, ForbiddenError, ResourceNotFoundError

    try:
        fields = client.issue.get(issue_id, include='children').raw() if data is None else data
        title = fields.get('subject')
        status = (fields.get('status') or {}).get('name')
        if skip_closed and ti_is_closed(status):
            return None
        tracker = fields.get('tracker')
        tracker_name = tracker.get('name') if isinstance(tracker, dict) else None
        custom_fields = fields.get('custom_fields') or []
        approvals = department_approvals(custom_fields)
        references = issue_references(custom_fields)
        parent_id = (fields.get('parent') or {}).get('id')
        if parent_id:
            references['parent_id'] = parent_id
        responsible = ti_responsible(client, issue_id, fields, {} if responsible_cache is None else responsible_cache)
        if responsible:
            references['ti_responsible'] = responsible
        subtasks = issue_subtasks(client, fields, issue_id)
        if subtasks:
            references['children'] = subtasks
    except AuthError:
        raise RedmineAuthError('Сеанс Redmine недействителен. Войдите заново.') from None
    except (ForbiddenError, ResourceNotFoundError):
        raise RedmineError('Задача #{} не найдена или недоступна в Redmine.'.format(issue_id)) from None
    except Exception:
        raise RedmineError('Не удалось прочитать задачу #{} из Redmine.'.format(issue_id)) from None
    if not isinstance(title, str) or not title.strip() or not isinstance(status, str) or not status.strip():
        raise RedmineError('Redmine не вернул заголовок или статус задачи #{}.'.format(issue_id))
    tracker_name = tracker_name.strip() if isinstance(tracker_name, str) else ''
    return {'title': title.strip(), 'status': status.strip(), 'tracker_name': tracker_name,
            'approvals': approvals, 'issue_references': references}


def issue_label(tracker_name, issue_id, kris_progress=None):
    return (kris_progress or {}).get('subject') or tracker_name or 'ТИ-{}'.format(issue_id)


TI_NUMBER = re.compile(r'\bти\s*[-‑–—]\s*([0-9]+)(?:\s*[рp]\s*([0-9]+))?', re.IGNORECASE)


def ti_sort_key(task):
    tracker = task['tracker_name']
    progress = task['kris_progress'] or {}
    labels = (progress.get('subject') or '', tracker, task['title'])
    numbers = [(int(match[1]), int(match[2] or 0))
               for label in labels if (match := TI_NUMBER.search(label))]
    label = status_key(issue_label(tracker, task['issue_id'], progress))
    if numbers:
        number, revision = numbers[0]
        revision = revision or next((rev for base, rev in numbers if base == number and rev), 0)
        return 0, number, revision, label, task['issue_id']
    return 1, 0, 0, label, task['issue_id']


def status_key(value):
    return ' '.join(value.casefold().split())


def column_name(status, mapping):
    return mapping.get(status_key(status), status)
