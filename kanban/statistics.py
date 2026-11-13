"""Statistics for the full active board, never the visible filtered subset."""
import datetime as dt
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QLabel, QTableWidget,
                            QTableWidgetItem, QHeaderView, QProgressBar, QTabWidget, QScrollArea)
from .charts import Chart, COLORS


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
        self.progress.hide()
        tabs = QTabWidget()
        layout.addWidget(tabs, 1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        charts = QWidget()
        charts_layout = QVBoxLayout(charts)
        charts_layout.setContentsMargins(0, 12, 12, 12)
        charts_layout.setSpacing(16)
        self.completion_chart = Chart('Завершение задач', ring=True)
        self.column_chart = Chart('Распределение по колонкам')
        self.member_chart = Chart('Нагрузка сотрудников')
        for chart in (self.completion_chart, self.column_chart, self.member_chart):
            charts_layout.addWidget(chart)
        legend = QLabel('Нагрузка: зелёный — завершено; красный — просрочено; синий — остальные открытые задачи.\nПодробные значения и полные названия — во вкладке «Таблицы».')
        legend.setWordWrap(True)
        legend.setObjectName('Muted')
        charts_layout.addWidget(legend)
        charts_layout.addStretch()
        scroll.setWidget(charts)
        tabs.addTab(scroll, 'Графики')
        tables = QWidget()
        table_layout = QVBoxLayout(tables)
        tabs.addTab(tables, 'Таблицы')
        self.columns = self.table(['Колонка', 'Задач', 'Лимит'])
        self.members = self.table(['Исполнитель', 'Всего', 'Завершено', 'Просрочено'])
        table_layout.addWidget(self.columns, 1)
        table_layout.addWidget(self.members, 2)

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
        completed, overdue = stats['completed'], stats['overdue']
        opened = stats['total'] - completed - overdue
        percent = round(100 * completed / stats['total']) if stats['total'] else 0
        self.completion_chart.set_rows([
            ('Завершено', [(completed, '#57a882')], str(completed)),
            ('Открыто в срок / без срока', [(opened, '#5789da')], str(opened)),
            ('Просрочено', [(overdue, '#d97670')], str(overdue)),
        ], str(percent) + '%')
        self.column_chart.set_rows([
            (name, [(count, COLORS[index % len(COLORS)])], 'Задач: {}{}'.format(count, ' / лимит ' + str(limit) if limit != '—' else ''))
            for index, (name, count, limit) in enumerate(stats['columns'])])
        self.member_chart.set_rows([
            (name, [(done, '#57a882'), (late, '#d97670'), (total-done-late, '#5789da')], '{} / ✓ {} / ! {}'.format(total, done, late))
            for name, total, done, late in stats['members']])
        for table, rows in [(self.columns, stats['columns']), (self.members, stats['members'])]:
            scroll = table.verticalScrollBar().value()
            table.setRowCount(len(rows))
            for r, values in enumerate(rows):
                for col, value in enumerate(values):
                    item = QTableWidgetItem(str(value))
                    item.setToolTip(str(value))
                    table.setItem(r, col, item)
            table.verticalScrollBar().setValue(scroll)
