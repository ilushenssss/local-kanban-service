"""Shared board list and read-only date chart; all values come from Store."""
import datetime as dt
from PyQt5.QtCore import Qt, pyqtSignal, QRectF
from PyQt5.QtGui import QPainter, QColor, QPen
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QLabel, QTableWidget, QTableWidgetItem, QHeaderView, QStyledItemDelegate
from .theme import TOKENS
from .presentation import date_text


class Timeline(QStyledItemDelegate):
    def paint(self, p, option, index):
        item = index.data(Qt.UserRole)
        if not item or not item['due_date']:
            return super().paint(p, option, index)
        start, end, first, last = index.data(Qt.UserRole + 1)
        p.save()
        box = QRectF(option.rect).adjusted(8, 14, -8, -14)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(TOKENS['hover']))
        p.drawRoundedRect(box, 4, 4)
        span = max(1, (last-first).days)
        x = box.x() + box.width() * (start-first).days / span
        width = max(4, box.width() * max(1, (end-start).days) / span)
        p.setBrush(QColor('#57a882' if item['completed_at'] else TOKENS['accent']))
        p.drawRoundedRect(QRectF(x, box.y(), min(width, box.right()-x), box.height()), 4, 4)
        p.restore()


class BoardView(QWidget):
    opened = pyqtSignal(object)
    def __init__(self, timeline=False):
        super().__init__()
        self.timeline = timeline
        layout = QVBoxLayout(self)
        self.hint = QLabel()
        self.hint.setWordWrap(True)
        self.hint.setObjectName('Muted')
        layout.addWidget(self.hint)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(['Задача', 'Колонка', 'Исполнитель', 'Срок', 'Период' if timeline else 'Стикеры'])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.verticalHeader().hide()
        if timeline:
            self.table.setItemDelegateForColumn(4, Timeline(self.table))
        self.table.cellDoubleClicked.connect(lambda row, col: self.opened.emit(self.table.item(row, 0).data(Qt.UserRole)))
        layout.addWidget(self.table)

    def update_data(self, tasks, columns):
        names = {c['id']: c['name'] for c in columns}
        ranges = []
        for task in tasks:
            if task['due_date']:
                end = dt.date.fromisoformat(task['due_date'])
                created = dt.date.fromisoformat(task['created_at'][:10])
                start, end = min(created, end), max(created, end)
                ranges.append((start, end))
        first = min((r[0] for r in ranges), default=dt.date.today())
        last = max((r[1] for r in ranges), default=first)
        self.hint.setText('Период: {} — {}. Между созданием и сроком; без срока — без полосы. Зависимости не заданы.'.format(date_text(first.isoformat()), date_text(last.isoformat())) if self.timeline else 'Задач: {}'.format(len(tasks)))
        self.table.setRowCount(len(tasks))
        for row, task in enumerate(tasks):
            values = [task['title'], names.get(task['column_id'], ''), ', '.join(task['assignee_names']), date_text(task['due_date']), '' if self.timeline else ', '.join(task['labels'])]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                if col == 0:
                    item.setData(Qt.UserRole, task)
                if col == 4 and self.timeline and task['due_date']:
                    end = dt.date.fromisoformat(task['due_date'])
                    created = dt.date.fromisoformat(task['created_at'][:10])
                    start, end = min(created, end), max(created, end)
                    item.setData(Qt.UserRole, task)
                    item.setData(Qt.UserRole+1, (start, end, first, last))
                    item.setToolTip('Создано: {} · Срок: {}'.format(date_text(task['created_at'][:10]), date_text(task['due_date'])))
                self.table.setItem(row, col, item)
            self.table.setRowHeight(row, 48)
