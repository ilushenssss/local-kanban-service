"""Human readable presentation of immutable history, including legacy entries."""
import datetime as dt
import html
import json
from PyQt5.QtCore import QDateTime, QTimeZone, Qt
from .store import mention_logins
from .theme import TOKENS


def date_text(value, with_time=False, timezone='UTC'):
    if not value:
        return 'не задано'
    try:
        if with_time:
            stamp = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=dt.timezone.utc)
            zone = QTimeZone(timezone.encode('utf-8'))
            if not zone.isValid():
                zone = QTimeZone.utc()
            local = QDateTime.fromSecsSinceEpoch(int(stamp.timestamp()), Qt.UTC).toTimeZone(zone)
            return local.toString("dd.MM.yyyy 'в' HH:mm") + ' ' + local.timeZoneAbbreviation()
        return dt.date.fromisoformat(value).strftime('%d.%m.%Y')
    except (ValueError, AttributeError):
        return str(value)


def history_text(entry, columns=(), users=(), timezone='UTC'):
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
            return date_text(value, key == 'completed_at', timezone)
        if key == 'assignees':
            return ', '.join(user_names.get(x, x) for x in value)
        if key == 'labels':
            return ', '.join(value)
        if key == 'checklist':
            return '; '.join(('Выполнено: ' if x['done'] else 'Не выполнено: ') + x['text'] for x in value)
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


def history_html(entries, columns=(), users=(), timezone='UTC'):
    if not entries:
        return '<p>История появится после сохранения задачи.</p>'
    result = []
    for entry in sorted(entries, key=lambda e: (e.get('created_at', ''), e.get('id', 0))):
        heading = '{} · {}'.format(entry['author'], entry['operation'])
        result.append('<p style="margin-top:18px; margin-bottom:4px"><b>{}</b><br><span style="color:{muted}">{}</span></p>'.format(html.escape(heading), html.escape(date_text(entry['created_at'], True, timezone)), muted=TOKENS['muted']))
        for line in history_text(entry, columns, users, timezone):
            result.append('<p style="margin-top:4px; margin-bottom:7px">{}</p>'.format(html.escape(line).replace('\n', '<br>')))
        result.append('<hr>')
    return ''.join(result)


def comments_html(entries, login, sticker_html=None, timezone='UTC', *, avatar_sources=None):
    if not entries:
        return '<p style="color:{}">Пока нет комментариев.</p>'.format(TOKENS['muted'])
    result = []
    for entry in entries:
        own = entry.get('login') == login
        author = html.escape(entry['author'])
        if type(entry.get('user_id')) is int:
            author = '<a style="color:{}" href="employee:{}">{}</a>'.format(
                TOKENS['accent'], entry['user_id'], author)
        if own:
            author += ' (Вы)'
        avatar = (avatar_sources or {}).get(entry.get('login'))
        if avatar:
            author = '<img src="{}" width="22" height="22" />&nbsp;{}'.format(html.escape(avatar), author)
        body = html.escape(entry['body']).replace('\n', '<br>')
        if entry.get('kind') == 'sticker' and sticker_html:
            body = sticker_html(entry)
        background = '' if entry.get('kind') == 'sticker' else ' bgcolor="{}"'.format(TOKENS['selection'] if own else TOKENS['hover'])
        bubble = '<td width="78%"{bg} style="padding:10px"><b>{author}</b><br>{body}<br><span style="color:{muted};font-size:9pt">{date}</span></td>'.format(
            bg=background, author=author,
            body=body, muted=TOKENS['muted'], date=html.escape(date_text(entry['created_at'], True, timezone)))
        spacer = '<td width="22%"></td>'
        result.append('<table width="100%" cellspacing="0" cellpadding="9"><tr>{}</tr></table><br>'.format(spacer+bubble if own else bubble+spacer))
    return ''.join(result)


def conversation_html(comments, events, login, columns=(), timezone='UTC', *, avatar_sources=None):
    timeline = [(x['created_at'], 1, x['id'], x) for x in comments]
    timeline += [(x['created_at'], 0, x['id'], x) for x in events if x['operation'] != 'Комментарий']
    result = []
    for date, kind, identifier, entry in sorted(timeline, key=lambda item: item[:3]):
        if kind:
            result.append(comments_html([entry], login, timezone=timezone, avatar_sources=avatar_sources))
        else:
            text = entry['author'] + ' · ' + entry['operation']
            changes = history_text(entry, columns, timezone=timezone)
            if entry['operation'] != 'Создание':
                text += ' · ' + '; '.join(changes)
            result.append('<p align="center" style="color:{color};font-size:9pt">{text}<br>{date}</p>'.format(
                color=TOKENS['muted'], text=html.escape(text), date=html.escape(date_text(date, True, timezone))))
    return ''.join(result) or comments_html([], login)


def task_notification_text(event, login):
    """Short desktop summary; large descriptions/checklists stay inside the card."""
    try:
        changes = json.loads(event['changes'])
    except (ValueError, TypeError):
        changes = {}
    title = ' '.join(event['title'].split())
    if len(title) > 80:
        title = title[:77] + '…'
    author = ' '.join(event['author'].split())[:40]
    heading = '№{} «{}»\n{}: '.format(event['number'], title, author)
    assignments = changes.get('assignees', [None, None])
    previous, current = assignments[0] or [], assignments[1] or []
    if event['operation'] == 'Создание':
        summary = 'Вам назначена задача' if login in current else 'Создана задача'
        return heading + summary
    details = []
    if login in current and login not in previous:
        details.append('Вам назначена задача')
    elif login in previous and login not in current:
        details.append('Вы сняты с задачи')
    if 'column_id' in changes or 'column_name' in changes:
        status = event.get('status_name') or (changes.get('column_name') or [None, None])[1]
        details.append('Изменён статус' + (': ' + status if status else ''))
    elif 'completed_at' in changes:
        details.append('Задача завершена' if changes['completed_at'][1] else 'Задача возобновлена')
    if 'archived' in changes:
        details.append('Задача архивирована' if changes['archived'][1] else 'Задача восстановлена')
    if event['operation'] == 'Комментарий':
        details.append('Вас упомянули в комментарии' if login in mention_logins(changes.get('text', ''))
                       else 'Добавлен комментарий')
    elif event['operation'] == 'Вложение':
        details.append('Добавлено вложение')
    names = {'title': 'название', 'description': 'описание', 'priority': 'приоритет', 'due_date': 'срок', 'labels': 'метки', 'checklist': 'чек-лист'}
    changed = [title for key, title in names.items() if key in changes]
    if 'assignees' in changes and not any(login in value for value in (previous, current)):
        changed.append('исполнители')
    if changed:
        details.append('Изменены: ' + ', '.join(changed))
    summary = '; '.join(details) or event['operation']
    return heading + summary
