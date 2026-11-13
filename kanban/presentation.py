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
    for entry in sorted(entries, key=lambda e: (e.get('created_at', ''), e.get('id', 0))):
        heading = '{} · {}'.format(entry['author'], entry['operation'])
        result.append('<p style="margin-top:18px; margin-bottom:4px"><b>{}</b><br><span style="color:{muted}">{}</span></p>'.format(html.escape(heading), html.escape(date_text(entry['created_at'], True)), muted=TOKENS['muted']))
        for line in history_text(entry, columns, users):
            result.append('<p style="margin-top:4px; margin-bottom:7px">{}</p>'.format(html.escape(line).replace('\n', '<br>')))
        result.append('<hr>')
    return ''.join(result)


def comments_html(entries, login):
    if not entries:
        return '<p style="color:{}">Пока нет комментариев.</p>'.format(TOKENS['muted'])
    result = []
    for entry in entries:
        own = entry.get('login') == login
        author = 'Вы' if own else entry['author']
        body = html.escape(entry['body']).replace('\n', '<br>')
        bubble = '<td width="78%" bgcolor="{bg}" style="padding:10px"><b>{author}</b><br>{body}<br><span style="color:{muted};font-size:9pt">{date}</span></td>'.format(
            bg=TOKENS['selection'] if own else TOKENS['hover'], author=html.escape(author),
            body=body, muted=TOKENS['muted'], date=html.escape(date_text(entry['created_at'], True)))
        spacer = '<td width="22%"></td>'
        result.append('<table width="100%" cellspacing="0" cellpadding="9"><tr>{}</tr></table><br>'.format(spacer+bubble if own else bubble+spacer))
    return ''.join(result)


def conversation_html(comments, events, login, columns=()):
    timeline = [(x['created_at'], 1, x['id'], x) for x in comments]
    timeline += [(x['created_at'], 0, x['id'], x) for x in events if x['operation'] != 'Комментарий']
    result = []
    for date, kind, identifier, entry in sorted(timeline, key=lambda item: item[:3]):
        if kind:
            result.append(comments_html([entry], login))
        else:
            text = entry['author'] + ' · ' + entry['operation']
            changes = history_text(entry, columns)
            if entry['operation'] != 'Создание':
                text += ' · ' + '; '.join(changes)
            result.append('<p align="center" style="color:{color};font-size:9pt">{text}<br>{date}</p>'.format(
                color=TOKENS['muted'], text=html.escape(text), date=html.escape(date_text(date, True))))
    return ''.join(result) or comments_html([], login)
