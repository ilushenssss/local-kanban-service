"""Human readable presentation of immutable history, including legacy entries."""
import datetime as dt
import html
import json
from .theme import TOKENS


def date_text(value, with_time=False):
    if not value:
        return 'не задано'
    try:
        if with_time:
            return dt.datetime.fromisoformat(value.replace('Z', '+00:00')).strftime('%d.%m.%Y в %H:%M') + ' UTC'
        return dt.date.fromisoformat(value).strftime('%d.%m.%Y')
    except (ValueError, AttributeError):
        return str(value)


def history_text(entry, columns=(), users=()):
    col_names = {c['id']: c['name'] for c in columns}
    user_names = {u['login']: u['display_name'] for u in users}
    try:
        changes = json.loads(entry['changes'])
    except (ValueError, TypeError):
        return ['Запись истории в прежнем формате']
    lines = []
    names = {'title': 'Название', 'description': 'Описание', 'priority': 'Приоритет', 'due_date': 'Срок', 'completed_at': 'Завершение', 'column_id': 'Колонка', 'column_name': 'Колонка', 'assignees': 'Исполнители', 'labels': 'Метки', 'archived': 'Архив', 'checklist': 'Чек-лист'}
    def display(key, value):
        if key == 'archived':
            return 'да' if value else 'нет'
        if value is None or value == '' or value == []:
            return 'не задано'
        if key == 'column_id':
            return col_names.get(value, 'Колонка №' + str(value))
        if key in ['due_date', 'completed_at']:
            return date_text(value, key == 'completed_at')
        if key == 'assignees':
            return ', '.join(user_names.get(x, x) for x in value)
        if key == 'labels':
            return ', '.join(value)
        if key == 'checklist':
            return '; '.join(('✓ ' if x['done'] else '○ ') + x['text'] for x in value)
        return str(value)
    if entry['operation'] == 'Комментарий':
        return [changes.get('text', 'Комментарий добавлен')]
    if entry['operation'] == 'Вложение':
        return ['Добавлен файл «{}»'.format(changes.get('name', 'Вложение'))]
    for key, pair in changes.items():
        if key == 'position':
            if entry['operation'] != 'Создание' and 'column_id' not in changes:
                lines.append('Изменён порядок карточки в колонке')
            continue
        if key not in names or not isinstance(pair, list) or len(pair) != 2:
            continue
        old, new = pair
        if entry['operation'] == 'Создание':
            if new not in (None, '', [], False):
                lines.append('{}: {}'.format(names[key], display(key, new)))
        else:
            lines.append('{}: {} → {}'.format(names[key], display(key, old), display(key, new)))
    return lines or ['Поля задачи сохранены без изменений']


def history_html(entries, columns=(), users=()):
    if not entries:
        return '<p>История появится после сохранения задачи.</p>'
    result = []
    for entry in entries:
        heading = '{} · {}'.format(entry['author'], entry['operation'])
        result.append('<p style="margin-top:18px; margin-bottom:4px"><b>{}</b><br><span style="color:{muted}">{}</span></p>'.format(html.escape(heading), html.escape(date_text(entry['created_at'], True)), muted=TOKENS['muted']))
        for line in history_text(entry, columns, users):
            result.append('<p style="margin-top:4px; margin-bottom:7px">{}</p>'.format(html.escape(line).replace('\n', '<br>')))
        result.append('<hr>')
    return ''.join(result)
