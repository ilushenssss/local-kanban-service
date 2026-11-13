"""Personal task list across all active boards."""
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QLineEdit, QLabel, QTableWidget, QTableWidgetItem, QHeaderView, QHBoxLayout, QComboBox
from PyQt5.QtGui import QFont
from .presentation import date_text


class MyTasksPage(QWidget):
    opened = pyqtSignal(object)

    def __init__(self):
        super().__init__()
        self.tasks = []
        layout = QVBoxLayout(self)
        self.search = QLineEdit()
        self.search.setPlaceholderText('Поиск среди моих задач на всех досках')
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.render)
        layout.addWidget(self.search)
        filters = QHBoxLayout()
        self.board_filter = QComboBox()
        self.state_filter = QComboBox()
        self.state_filter.addItems(['Все состояния', 'Открытые', 'Завершённые'])
        self.priority_filter = QComboBox()
        self.priority_filter.addItems(['Все приоритеты', 'Низкий', 'Обычный', 'Высокий', 'Срочный'])
        for combo in (self.board_filter, self.state_filter, self.priority_filter):
            filters.addWidget(combo)
            combo.currentIndexChanged.connect(self.render)
        layout.addLayout(filters)
        self.count = QLabel('Нет назначенных задач')
        layout.addWidget(self.count)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(['Задача / расположение', 'Исполнители', 'Срок', 'Приоритет', 'Метки'])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        for i in range(1, 5):
            self.table.horizontalHeader().setSectionResizeMode(i, QHeaderView.ResizeToContents)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.verticalHeader().hide()
        self.table.setWordWrap(False)
        self.table.cellDoubleClicked.connect(lambda row, col: self.opened.emit(self.table.item(row, 0).data(Qt.UserRole)))
        layout.addWidget(self.table)

    def update_data(self, tasks):
        if self.tasks == tasks:
            return
        self.tasks = tasks
        current = self.board_filter.currentData()
        self.board_filter.blockSignals(True)
        self.board_filter.clear()
        self.board_filter.addItem('Все доски', None)
        for board_id, name in sorted({(t['board_id'], t['board_name']) for t in tasks}, key=lambda x: x[1]):
            self.board_filter.addItem(name, board_id)
        self.board_filter.setCurrentIndex(max(0, self.board_filter.findData(current)))
        self.board_filter.blockSignals(False)
        self.render()

    def render(self):
        query = self.search.text().strip().casefold()
        tasks = [t for t in self.tasks if query in (t['title'] + ' ' + t['description'] + ' ' + t['board_name']).casefold()]
        board_id = self.board_filter.currentData()
        if board_id is not None:
            tasks = [t for t in tasks if t['board_id'] == board_id]
        state = self.state_filter.currentIndex()
        if state:
            tasks = [t for t in tasks if bool(t['completed_at']) == (state == 2)]
        if self.priority_filter.currentIndex():
            tasks = [t for t in tasks if t['priority'] == self.priority_filter.currentText()]
        scroll = self.table.verticalScrollBar().value()
        current = self.table.item(self.table.currentRow(), 0)
        selected = current.data(Qt.UserRole)['id'] if current else None
        self.count.setText('Мои задачи: {}'.format(len(tasks)) if tasks else 'Нет подходящих назначенных задач')
        self.table.setRowCount(len(tasks))
        for row, task in enumerate(tasks):
            values = [task['title'] + '\n' + task['board_name'] + ' / ' + task['column_name'],
                      ', '.join(task['assignee_names']), date_text(task['due_date']), task['priority'], ', '.join(task['labels'])]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                if col == 0:
                    item.setData(Qt.UserRole, task)
                    font = QFont(item.font())
                    font.setStrikeOut(bool(task['completed_at']))
                    item.setFont(font)
                self.table.setItem(row, col, item)
            self.table.setRowHeight(row, 54)
            if task['id'] == selected:
                self.table.selectRow(row)
        self.table.verticalScrollBar().setValue(scroll)
