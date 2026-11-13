from PyQt5.QtCore import Qt, QRect, QSize
from PyQt5.QtGui import QColor, QFont, QPen
from PyQt5.QtWidgets import QStyledItemDelegate, QStyle
from .theme import TOKENS, PRIORITIES, label_colors
from .presentation import date_text
import datetime as dt


class CardDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index):
        return QSize(210, 154)

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
        painter.drawRoundedRect(rect, 8, 8)
        painter.setClipRect(rect)
        if task.get("completed_at"):
            painter.setOpacity(0.57)
        x, y = rect.x() + 10, rect.y() + 8
        width = rect.width() - 20
        def font(size, bold=False):
            f = QFont(option.font)
            f.setPointSizeF(8.5 if size == 11 else 9.5)
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
        title_font = painter.font()
        title_font.setStrikeOut(bool(task.get("completed_at")))
        painter.setFont(title_font)
        painter.setPen(QColor(TOKENS['text']))
        painter.drawText(QRect(x, y + 27, width, 35), Qt.AlignTop | Qt.TextWordWrap, task['title'])
        font(11)
        painter.setPen(QColor(TOKENS['muted']))
        assignees = ', '.join(task.get('assignee_names') or task['assignees']) or 'Без исполнителя'
        painter.drawText(QRect(x, y + 65, width, 18), Qt.AlignVCenter, painter.fontMetrics().elidedText(assignees, Qt.ElideRight, width))
        overdue = task['due_date'] and task['due_date'] < dt.date.today().isoformat() and not task['completed_at']
        painter.setPen(QColor(TOKENS['danger'] if overdue else TOKENS['muted']))
        due = ('Просрочено · ' if overdue else 'Срок · ') + date_text(task['due_date']) if task['due_date'] else 'Срок не задан'
        painter.drawText(QRect(x, y + 84, width, 18), Qt.AlignVCenter, due)
        labels = task.get('labels', [])
        if labels:
            available = width - (60 if task.get('checklist') else 0)
            cursor = x
            visible = labels[:2]
            for name in visible:
                chip_width = min((available - 6) // len(visible), painter.fontMetrics().horizontalAdvance(name) + 22)
                tag = QRect(cursor, y + 108, chip_width, 24)
                foreground, background = label_colors(name)
                painter.setPen(Qt.NoPen)
                painter.setBrush(QColor(background))
                painter.drawRoundedRect(tag, 5, 5)
                painter.setPen(QColor(foreground))
                text = name + (' +{}'.format(len(labels) - 2) if name == visible[-1] and len(labels) > 2 else '')
                painter.drawText(tag.adjusted(7, 0, -7, 0), Qt.AlignVCenter, painter.fontMetrics().elidedText(text, Qt.ElideRight, tag.width() - 14))
                cursor += chip_width + 6
        checks = task.get('checklist', [])
        if checks:
            painter.setPen(QColor(TOKENS['muted']))
            painter.drawText(QRect(rect.right() - 70, y + 108, 56, 24), Qt.AlignRight | Qt.AlignVCenter, '✓ {}/{}'.format(sum(bool(c['done']) for c in checks), len(checks)))
        if hovered and not selected:
            painter.setPen(QPen(QColor(TOKENS['scroll']), 1))
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(rect, 8, 8)
        painter.restore()
