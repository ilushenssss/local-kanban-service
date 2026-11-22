"""Read-only Redmine access. The client and its credentials live in one TeamSync session."""
from urllib.parse import urlsplit


class RedmineError(ValueError):
    pass


class RedmineAuthError(RedmineError):
    pass


def validate_url(url):
    try:
        parts = urlsplit(url)
        valid = (parts.scheme == 'https' and parts.hostname and '@' not in parts.netloc
                 and not parts.query and not parts.fragment and not any(ch.isspace() for ch in url)
                 and parts.port != 0)
    except ValueError:
        valid = False
    if not valid:
        raise RedmineError('Укажите HTTPS-адрес Redmine без учётных данных.')


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
        raise RedmineError('Не удалось подключиться к Redmine. Проверьте адрес, HTTPS и доступ к API.') from None
    return client


def read_issue(client, issue_id):
    from redminelib.exceptions import AuthError, ForbiddenError, ResourceNotFoundError

    try:
        fields = client.issue.get(issue_id).raw()
        title = fields.get('subject')
        status = (fields.get('status') or {}).get('name')
    except AuthError:
        raise RedmineAuthError('Сеанс Redmine недействителен. Войдите заново.') from None
    except (ForbiddenError, ResourceNotFoundError):
        raise RedmineError('Задача #{} не найдена или недоступна в Redmine.'.format(issue_id)) from None
    except Exception:
        raise RedmineError('Не удалось прочитать задачу #{} из Redmine.'.format(issue_id)) from None
    if not isinstance(title, str) or not title.strip() or not isinstance(status, str) or not status.strip():
        raise RedmineError('Redmine не вернул заголовок или статус задачи #{}.'.format(issue_id))
    return {'title': title.strip(), 'status': status.strip()}


def status_key(value):
    return ' '.join(value.casefold().split())


def column_name(status, mapping):
    return mapping.get(status_key(status), status)
