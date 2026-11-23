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
            # Пробелы (включая переносы строк) и запятые разделяют элементы.
            references[key] = re.findall(r'[^\s,]+', value)
    return references


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
