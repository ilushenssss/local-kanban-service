# Модуль читает данные Redmine. Учётные данные клиента остаются в памяти сеанса TeamSync.
"""Read-only Redmine access. The client and its credentials live in one TeamSync session."""
# Ищет префикс поля согласования в имени, полученном из Redmine.
import re

# Разбирает URL на схему, адрес, порт и путь без сетевого запроса.
from urllib.parse import urlsplit


# Общая ошибка интеграции, которую TeamSync может показать в интерфейсе.
class RedmineError(ValueError):
    # Для этой ошибки достаточно текстового сообщения из ValueError.
    pass


# Отдельный тип для отказа в авторизации. По нему интерфейс сбрасывает сеанс.
class RedmineAuthError(RedmineError):
    # Сообщение и остальные свойства наследуются от RedmineError.
    pass


# Проверяет базовый URL, заданный администратором в «Настройки → Подключение…».
def validate_url(url):
    # Разбор URL или проверка порта могут выбросить ValueError.
    try:
        # Для установки в подпапке URL включает её, например https://server.example.org/redmine.
        parts = urlsplit(url)
        # Допускаем HTTP и HTTPS, проверяем адрес сервера. Логин и пароль передаются отдельно, поэтому символ @ в адресе запрещён.
        valid = (parts.scheme in ('http', 'https') and parts.hostname and '@' not in parts.netloc
                 # В базовом URL не нужны параметры запроса, фрагмент после # и пробельные символы.
                 and not parts.query and not parts.fragment and not any(ch.isspace() for ch in url)
                 # Порт можно опустить или указать явно. Разбор порта проверит его формат и диапазон.
                 and parts.port != 0)
    # Неверный формат адреса обрабатываем так же, как остальные недопустимые URL.
    except ValueError:
        # Сообщение для пользователя формируется ниже в одном месте.
        valid = False
    # При проверке адреса не выполняем вход или запрос к серверу.
    if not valid:
        # Не включаем введённый URL в сообщение, чтобы не раскрыть случайно добавленные учётные данные.
        raise RedmineError('Укажите адрес Redmine с http:// или https:// без учётных данных.')


# Здесь создаётся клиент. Если у вашей установки другой поддерживаемый способ входа, менять его нужно здесь.
def connect(url, login, password):
    # Проверяем схему и форму адреса до передачи пароля библиотеке.
    validate_url(url)
    # Загружаем необязательную зависимость только при попытке подключиться к Redmine.
    try:
        # Redmine — класс клиента из пакета python-redmine.
        from redminelib import Redmine
        # Синхронный HTTP-движок. TeamSync запускает операцию в фоновом потоке PyQt.
        from redminelib.engines import SyncEngine
        # Библиотека использует этот тип ошибки для HTTP 401.
        from redminelib.exceptions import AuthError
    # При отсутствии пакета обычные доски продолжают работать, а вход в Redmine сообщает, что нужно установить.
    except ImportError:
        # from None исключает исходную ошибку из отображаемой цепочки исключений.
        raise RedmineError('Установите компоненты из requirements/redmine.txt.') from None

    # Наш вариант движка добавляет общие сетевые параметры ко всем запросам этого клиента.
    class ReadEngine(SyncEngine):
        # Метод собирает параметры запроса, не обращаясь к состоянию экземпляра.
        @staticmethod
        # method — HTTP-метод, headers — заголовки, params — параметры URL, data — тело запроса.
        def construct_request_kwargs(method, headers, params, data):
            # Сохраняем параметры, рассчитанные самой библиотекой.
            options = SyncEngine.construct_request_kwargs(method, headers, params, data)
            # 5 секунд на подключение, 10 секунд ожидания данных. Перенаправления отключены, используйте конечный URL.
            return dict(options, timeout=(5, 10), allow_redirects=False)

    # Создание клиента и проверка входа могут завершиться ошибками разных типов.
    try:
        # Для входа используется HTTP Basic. Только HTTPS защищает пароль при передаче.
        client = Redmine(url.rstrip('/'), username=login, password=password,
                         # Подставляем движок с тайм-аутами и запретом перенаправлений.
                         engine=ReadEngine)
        # Библиотека проверяет учётные данные запросом текущего пользователя, GET /users/current.json.
        client.auth()
    # Отдельно сообщаем об отказе в авторизации.
    except AuthError:
        # В сообщение не попадают логин, пароль или исходный ответ сервера.
        raise RedmineAuthError('Redmine не принял логин или пароль.') from None
    # Сюда попадают, например, сбой сети, ошибка TLS, перенаправление или недоступный API.
    except Exception:
        # Сообщаем, что проверить, без вывода внутренних деталей запроса.
        raise RedmineError('Не удалось подключиться к Redmine. Проверьте адрес, соединение и доступ к API.') from None
    # Интерфейс держит этот клиент в памяти на время сеанса и использует его для следующих запросов.
    return client


# Цвета состояний, которые нужны для карточки. Прочие значения не выводятся.
APPROVAL_COLORS = {
    'нет': 'red',
    'на согласовании': 'red',
    'есть замечания': 'yellow',
    'да': 'green',
}
# После «с» или «со» также отсекаем сокращение «отд.», если оно есть.
DEPARTMENT_PREFIX = re.compile(r'\bТИ\s+согласовано\s+со?(?=\s|[:–—-]|$)(?:\s+отд\.)?', re.IGNORECASE)


# Собирает названия отделов и их текущие решения.
def department_approvals(fields):
    # issue.raw()['custom_fields'] содержит те же словари, что и issue.custom_fields._resources.
    approvals = []
    for field in fields:
        # Неожиданные форматы поля или значения пропускаем.
        if not isinstance(field, dict):
            continue
        name, value = field.get('name'), field.get('value')
        if not isinstance(name, str) or not isinstance(value, str):
            continue
        match = DEPARTMENT_PREFIX.search(name)
        if match and status_key(value) in APPROVAL_COLORS:
            # После префикса может стоять разделитель между фразой и названием отдела.
            department = name[match.end():].strip().lstrip(':–—-').strip()
            if department:
                approvals.append({'department': department, 'status': value.strip()})
    return approvals


# Собирает номера СП и багов из двух пользовательских полей Redmine.
def issue_references(fields):
    names = {'основание для открытия ти': 'basis', '№ bz': 'bugs'}
    references = {}
    for field in fields:
        if not isinstance(field, dict):
            continue
        name, value = field.get('name'), field.get('value')
        key = names.get(status_key(name)) if isinstance(name, str) else None
        if key and isinstance(value, str):
            # Запятые и переносы строк разделяют элементы. Внутренние пробелы сохраняются.
            parts = re.split(r'[,\r\n]+', value)
            references[key] = [part.strip() for part in parts if part.strip()]
    return references


# ID проекта КРИС_ТИ и имя поля, в котором хранится ссылка на исходную ТИ.
KRIS_PROJECT_ID = 49
TI_LOCATION_FIELD = 'расположение ти'


def find_kris_issues(client, issue_ids):
    # Один проход по открытым верхнеуровневым задачам для всех ТИ на доске.
    pending = {str(issue_id) for issue_id in issue_ids}
    matches = {}
    if not pending:
        return matches
    candidates = client.issue.filter(project_id=KRIS_PROJECT_ID, status_id='open', parent_id='!*')
    for candidate in candidates:
        for field in candidate.raw().get('custom_fields') or []:
            if status_key(field.get('name') or '') != TI_LOCATION_FIELD:
                continue
            # Берём целые номера, в том числе из #ID и /issues/ID. ID 42 не совпадёт с 142.
            linked = pending.intersection(re.findall(r'[0-9]+', str(field.get('value') or '')))
            for issue_id in linked:
                matches[int(issue_id)] = candidate.id
            pending.difference_update(linked)
        if not pending:
            break
    return matches


def read_kris_children(client, parent_id):
    # Вложенные потомки не учитываются. Статус прямой подзадачи может потребовать отдельного запроса.
    parent = client.issue.get(parent_id, include='children').raw()
    children = parent.get('children') or []
    done = total = 0
    for child in children:
        status = (child.get('status') or {}).get('name')
        if not status:
            issue = client.issue.get(child['id']).raw()
            status = (issue.get('status') or {}).get('name')
        if not status:
            raise RedmineError('Redmine не вернул статус подзадачи.')
        key = status_key(status)
        # Статус «Действие не требуется» исключает подзадачу из расчёта прогресса.
        if key == 'действие не требуется':
            continue
        total += 1
        done += 'выполнено' in key
    return {'done': done, 'total': total}


def read_kris_progress(client, issue_ids):
    from redminelib.exceptions import AuthError

    # None означает, что задача КРИС_ТИ не найдена. При сбое оставляем прежний снимок.
    progress = dict.fromkeys(issue_ids)
    try:
        matches = find_kris_issues(client, progress)
    except AuthError:
        raise RedmineAuthError('Сеанс Redmine недействителен. Войдите заново.') from None
    except Exception:
        return {}, ['Не удалось найти задачи КРИС_ТИ в проекте #{}.'.format(KRIS_PROJECT_ID)]
    errors = []
    children = {}
    for issue_id, parent_id in matches.items():
        if parent_id not in children:
            try:
                children[parent_id] = read_kris_children(client, parent_id)
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


# Читает поля задачи по числовому ID из её адреса в Redmine.
def read_issue(client, issue_id):
    # Импортируем типы ошибок здесь. К этому моменту клиент уже создан.
    from redminelib.exceptions import AuthError, ForbiddenError, ResourceNotFoundError

    # Запрос, разбор ответа и извлечение полей обрабатываются вместе.
    try:
        # issue.get() выполняет GET /issues/<ID>.json. raw() выдаёт словарь полей задачи внутри ответа.
        fields = client.issue.get(issue_id).raw()
        # subject — стандартный заголовок. Если нужен заголовок из пользовательского поля, ищите его в custom_fields по ID.
        title = fields.get('subject')
        # Обычно status выглядит как {"id": 1, "name": "Новая"}. Берём имя для показа и подбора колонки.
        status = (fields.get('status') or {}).get('name')
        # Redmine формирует подпись «имя трекера #ID». Берём имя целиком, включая редакцию.
        tracker = fields.get('tracker')
        # Старые или изменённые ответы API могут не содержать объект трекера.
        tracker_name = tracker.get('name') if isinstance(tracker, dict) else None
        # Читаем исходные пользовательские поля без построения дополнительных объектов Redmine.
        custom_fields = fields.get('custom_fields') or []
        approvals = department_approvals(custom_fields)
        references = issue_references(custom_fields)
    # Если Redmine вернул HTTP 401, TeamSync запросит вход повторно при следующем обращении.
    except AuthError:
        # Отдельный тип ошибки позволяет интерфейсу сбросить сохранённый в памяти клиент.
        raise RedmineAuthError('Сеанс Redmine недействителен. Войдите заново.') from None
    # HTTP 403 означает отказ в доступе, HTTP 404 означает, что сервер не показал такую задачу.
    except (ForbiddenError, ResourceNotFoundError):
        # Не обещаем, что задача существует, если Redmine возвращает 404 для скрытых задач.
        raise RedmineError('Задача #{} не найдена или недоступна в Redmine.'.format(issue_id)) from None
    # Остальные сбои, включая неожиданный формат ответа и ошибки сети, дают общее сообщение.
    except Exception:
        # ID помогает понять, какой запрос не выполнился. Тело ответа не показываем.
        raise RedmineError('Не удалось прочитать задачу #{} из Redmine.'.format(issue_id)) from None
    # Для карточки обязательны две непустые строки. Пробелы сами по себе за значение не считаются.
    if not isinstance(title, str) or not title.strip() or not isinstance(status, str) or not status.strip():
        # При таком ответе карточка не создаётся, а ранее загруженные данные не затираются.
        raise RedmineError('Redmine не вернул заголовок или статус задачи #{}.'.format(issue_id))
    # Пустое имя трекера допустимо. Для таких карточек есть подпись по ID.
    tracker_name = tracker_name.strip() if isinstance(tracker_name, str) else ''
    # ID задачи используется отдельно, для связи с Redmine.
    return {'title': title.strip(), 'status': status.strip(), 'tracker_name': tracker_name,
            'approvals': approvals, 'issue_references': references}


# Формирует подпись карточки, пока для старой записи ещё не загрузилось имя трекера.
def issue_label(tracker_name, issue_id):
    # Для записей из предыдущей схемы сохраняем привычный вид «ТИ-<ID>».
    return tracker_name or 'ТИ-{}'.format(issue_id)


# Приводит имя статуса к ключу для сравнения с настройками и названиями колонок.
def status_key(value):
    # casefold убирает различия в регистре, split и join сводят серии пробелов к одному.
    return ' '.join(value.casefold().split())


# Выбирает имя колонки по соответствиям из «Настройки → Подключение…».
def column_name(status, mapping):
    # Сначала ищем явно заданное соответствие, например «Новый = Проект ТИ».
    # Если его нет, TeamSync попытается найти колонку с именем статуса Redmine.
    return mapping.get(status_key(status), status)
