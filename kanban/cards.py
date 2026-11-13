"""Compact board cards, following the supplied YouGile reference."""
import datetime as dt
from PyQt5.QtCore import Qt, QRect, QRectF, QSize
from PyQt5.QtGui import QColor, QFont, QPen
from PyQt5.QtWidgets import QStyledItemDelegate, QStyle
from .theme import TOKENS, PRIORITIES, label_colors
from .presentation import date_text


class CardDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index):
        task = index.data(Qt.UserRole) or {}
        return QSize(280, 136 if task.get('checklist') else 116)

    def paint(self, p, option, index):
        task = index.data(Qt.UserRole)
        if not task:
            return super().paint(p, option, index)
        p.save()
        p.setRenderHint(p.Antialiasing)
        rect = option.rect.adjusted(2, 2, -2, -4)
        selected = bool(option.state & QStyle.State_Selected)
        done = bool(task['completed_at'])
        p.setPen(QPen(QColor(TOKENS['accent'] if selected else TOKENS['border']), 1))
        p.setBrush(QColor(TOKENS['selection'] if selected else TOKENS['background']))
        p.drawRoundedRect(rect, 8, 8)
        p.setClipRect(rect)
        if done:
            p.setOpacity(.52)
        x, y, width = rect.x()+10, rect.y()+9, rect.width()-20
        def font(size=9, bold=False, strike=False):
            f = QFont(option.font)
            f.setPointSizeF(size)
            f.setBold(bold)
            f.setStrikeOut(strike)
            p.setFont(f)
        font(10)
        p.setPen(QColor('#57a882' if done else TOKENS['muted']))
        p.drawText(QRect(x, y, 18, 19), Qt.AlignCenter, '●' if done else '○')
        font(9.5, False, done)
        p.setPen(QColor(TOKENS['text']))
        p.drawText(QRect(x+23, y, width-35, 34), Qt.AlignTop | Qt.TextWordWrap, task['title'])
        font(8)
        cursor = x
        def chip(text, foreground, background, max_width):
            nonlocal cursor
            w = min(max_width, p.fontMetrics().horizontalAdvance(text)+14)
            box = QRect(cursor, y+54, w, 22)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(background))
            p.drawRoundedRect(box, 5, 5)
            p.setPen(QColor(foreground))
            p.drawText(box.adjusted(6, 0, -6, 0), Qt.AlignVCenter, p.fontMetrics().elidedText(text, Qt.ElideRight, w-12))
            cursor += w+5
        priority = task.get('priority', 'Обычный')
        fg, bg = PRIORITIES.get(priority, PRIORITIES['Обычный'])
        chip('▥ '+priority, fg, bg, 94)
        if task.get('due_date'):
            overdue = task['due_date'] < dt.date.today().isoformat() and not done
            chip(('! ' if overdue else '▣ ')+date_text(task['due_date']), TOKENS['danger'] if overdue else TOKENS['muted'], TOKENS['hover'], 96)
        labels = task.get('labels', [])
        if labels and cursor < x+width-38:
            fg, bg = label_colors(labels[0])
            chip(labels[0], fg, bg, max(28, x+width-cursor-30))
        names = task.get('assignee_names') or task.get('assignees', [])
        if names:
            name = names[0]
            initials = ''.join(part[0] for part in name.split()[:2]).upper()
            avatar = QRect(rect.right()-33, y+55, 23, 23)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(TOKENS['selection']))
            p.drawEllipse(avatar)
            p.setPen(QColor(TOKENS['text']))
            font(7, True)
            p.drawText(avatar, Qt.AlignCenter, initials)
        checks = task.get('checklist', [])
        unread = task.get('unread_count', 0)
        if unread:
            font(8, True)
            p.setPen(QColor(TOKENS['accent']))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(QRect(x, y+87, 15, 10), 3, 3)
            p.drawLine(x+3, y+97, x+3, y+101)
            p.drawLine(x+3, y+101, x+7, y+97)
            p.drawText(QRect(x+22, y+82, 38, 22), Qt.AlignVCenter, str(unread))
        if checks:
            count = sum(bool(c['done']) for c in checks)
            track = QRectF(x+(62 if unread else 0), y+94, width-45-(62 if unread else 0), 6)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(TOKENS['hover']))
            p.drawRoundedRect(track, 3, 3)
            if count:
                p.setBrush(QColor('#79ad79' if done else '#9ca8bc'))
                p.drawRoundedRect(QRectF(track.x(), track.y(), track.width()*count/len(checks), 6), 3, 3)
            font(8)
            p.setPen(QColor(TOKENS['muted']))
            p.drawText(QRect(rect.right()-48, y+87, 38, 20), Qt.AlignRight | Qt.AlignVCenter, '{}/{}'.format(count, len(checks)))
        p.restore()
