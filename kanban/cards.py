from PyQt5.QtCore import Qt, QRect, QSize
from PyQt5.QtGui import QColor, QFont, QPen
from PyQt5.QtWidgets import QStyledItemDelegate, QStyle
from .theme import TOKENS, PRIORITIES
from .presentation import date_text
import datetime as dt


class CardDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index):
        return QSize(240, 194)

    def paint(self, painter, option, index):
        task = index.data(Qt.UserRole)
        if not task:
            return super().paint(painter, option, index)
        painter.save()
        painter.setRenderHint(painter.Antialiasing)
        rect = option.rect.adjusted(2, 2, -2, -4)
        selected = bool(option.state & QStyle.State_Selected)
        hovered = bool(option.state & QStyle.State_MouseOver)
        painter.setPen(QPen(QColor(TOKENS['accent'] if selected else TOKENS['border']), 1.5 if selected else 1))
        painter.setBrush(QColor(TOKENS['selection'] if selected else TOKENS['background']))
        painter.drawRoundedRect(rect, 9, 9)
        painter.setClipRect(rect)
        x, y = rect.x() + 14, rect.y() + 12
        width = rect.width() - 28
        def font(size, bold=False):
            f = QFont('Segoe UI')
            f.setPixelSize(size)
            f.setBold(bold)
            painter.setFont(f)
        font(11)
        painter.setPen(QColor(TOKENS['muted']))
        painter.drawText(QRect(x, y, width - 88, 20), Qt.AlignVCenter, '№ {}'.format(task['number']))
        priority = task.get('priority', 'Обычный')
        color, background = PRIORITIES.get(priority, PRIORITIES['Обычный'])
        badge = QRect(rect.right() - 101, y - 2, 87, 23)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(background))
        painter.drawRoundedRect(badge, 5, 5)
        painter.setPen(QColor(color))
        font(11, True)
        painter.drawText(badge, Qt.AlignCenter, priority)
        font(14, True)
        painter.setPen(QColor(TOKENS['text']))
        painter.drawText(QRect(x, y + 32, width, 42), Qt.AlignTop | Qt.TextWordWrap, task['title'])
        font(11)
        painter.setPen(QColor(TOKENS['muted']))
        assignees = ', '.join(task.get('assignee_names') or task['assignees']) or 'Без исполнителя'
        painter.drawText(QRect(x, y + 82, width, 18), Qt.AlignVCenter, painter.fontMetrics().elidedText(assignees, Qt.ElideRight, width))
        overdue = task['due_date'] and task['due_date'] < dt.date.today().isoformat() and not task['completed_at']
        painter.setPen(QColor(TOKENS['danger'] if overdue else TOKENS['muted']))
        due = ('Просрочено · ' if overdue else 'Срок · ') + date_text(task['due_date']) if task['due_date'] else 'Срок не задан'
        painter.drawText(QRect(x, y + 106, width, 18), Qt.AlignVCenter, due)
        labels = task.get('labels', [])
        if labels:
            tag = QRect(x, y + 138, min(155, width - 55), 24)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(TOKENS['hover']))
            painter.drawRoundedRect(tag, 5, 5)
            painter.setPen(QColor(TOKENS['muted']))
            name = labels[0] + ('  +{}'.format(len(labels) - 1) if len(labels) > 1 else '')
            painter.drawText(tag.adjusted(7, 0, -7, 0), Qt.AlignVCenter, painter.fontMetrics().elidedText(name, Qt.ElideRight, tag.width() - 14))
        checks = task.get('checklist', [])
        if checks:
            painter.setPen(QColor(TOKENS['muted']))
            painter.drawText(QRect(rect.right() - 70, y + 138, 56, 24), Qt.AlignRight | Qt.AlignVCenter, '✓ {}/{}'.format(sum(bool(c['done']) for c in checks), len(checks)))
        if hovered and not selected:
            painter.setPen(QPen(QColor(TOKENS['scroll']), 1))
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(rect, 9, 9)
        painter.restore()
