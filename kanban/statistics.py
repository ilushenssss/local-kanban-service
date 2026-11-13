"""Statistics for the full active board, never the visible filtered subset."""
import datetime as dt
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QLabel, QTableWidget,
                            QTableWidgetItem, QHeaderView, QProgressBar)


def summarize(tasks, columns, today=None):
    today = today or dt.date.today().isoformat()
    completed = sum(bool(t['completed_at']) for t in tasks)
    overdue = lambda t: bool(t['due_date'] and t['due_date'] < today and not t['completed_at'])
    members = {}
    for task in tasks:
        logins = task['assignees'] or [None]
        names = task.get('assignee_names') or task['assignees']
        for index, login in enumerate(logins):
            name = names[index] if login is not None and index < len(names) else 'Без исполнителя'
            row = members.setdefault(login, [name, 0, 0, 0])
            row[1] += 1
            row[2] += bool(task['completed_at'])
            row[3] += overdue(task)
    return dict(total=len(tasks), completed=completed, overdue=sum(overdue(t) for t in tasks),
                unassigned=sum(not t['assignees'] for t in tasks),
                columns=[(c['name'], sum(t['column_id'] == c['id'] for t in tasks), c['wip_limit'] or '—') for c in columns],
                members=sorted(members.values(), key=lambda r: r[0].casefold()))


class StatisticsPage(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 20, 12, 12)
        title = QLabel('Обзор доски')
        title.setObjectName('Heading')
        layout.addWidget(title)
        hint = QLabel('Все неархивные задачи выбранной доски. Фильтры поиска здесь не применяются.\nЗадача с несколькими исполнителями учитывается у каждого из них.')
        hint.setWordWrap(True)
        hint.setObjectName('Muted')
        layout.addWidget(hint)
        self.summary = QLabel('Выберите доску')
        self.summary.setObjectName('SectionTitle')
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        self.progress = QProgressBar()
        layout.addWidget(self.progress)
        self.columns = self.table(['Колонка', 'Задач', 'Лимит'])
        self.members = self.table(['Исполнитель', 'Всего', 'Завершено', 'Просрочено'])
        layout.addWidget(self.columns, 1)
        layout.addWidget(self.members, 2)

    def table(self, headers):
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectRows)
        table.setAlternatingRowColors(True)
        table.verticalHeader().hide()
        table.setAccessibleName('Статистика: ' + headers[0])
        return table

    def update_data(self, tasks, columns):
        stats = summarize(tasks, columns)
        self.summary.setText('Всего: {total}   ·   Завершено: {completed}   ·   Просрочено: {overdue}   ·   Без исполнителя: {unassigned}'.format(**stats))
        self.progress.setValue(round(100 * stats['completed'] / stats['total']) if stats['total'] else 0)
        self.progress.setFormat('Завершено %p%' if stats['total'] else 'На доске пока нет задач')
        for table, rows in [(self.columns, stats['columns']), (self.members, stats['members'])]:
            scroll = table.verticalScrollBar().value()
            table.setRowCount(len(rows))
            for r, values in enumerate(rows):
                for col, value in enumerate(values):
                    item = QTableWidgetItem(str(value))
                    item.setToolTip(str(value))
                    table.setItem(r, col, item)
            table.verticalScrollBar().setValue(scroll)
