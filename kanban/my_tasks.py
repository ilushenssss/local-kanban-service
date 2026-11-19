"""Personal tasks across all accessible active boards."""
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QLineEdit, QLabel, QTableWidget, QTableWidgetItem, QHeaderView, QHBoxLayout, QComboBox, QPushButton, QScrollArea, QStackedWidget
from PyQt5.QtGui import QFont
from .presentation import date_text
from .cards import Cards, set_card_notice, update_cards


class MyTasksPage(QWidget):
    opened = pyqtSignal(object)
    details_requested = pyqtSignal(object)

    def __init__(self):
        super().__init__()
        self.tasks = []
        self.card_lists = {}
        self.notices = {}
        self.expanded_ids = set()
        self.board_structure = ()
        layout = QVBoxLayout(self)
        self.search = QLineEdit()
        self.search.setPlaceholderText('Поиск среди моих задач на всех досках')
        self.search.setClearButtonEnabled(False)
        self.search.textChanged.connect(self.render)
        search_row = QHBoxLayout()
        search_row.addWidget(self.search, 1)
        clear_search = QPushButton('Очистить поиск')
        clear_search.clicked.connect(self.search.clear)
        search_row.addWidget(clear_search)
        layout.addLayout(search_row)
        filters = QHBoxLayout()
        self.board_filter = QComboBox()
        self.board_filter.addItem('Все доски', None)
        self.state_filter = QComboBox()
        self.state_filter.addItems(['Все состояния', 'Открытые', 'Завершённые'])
        self.priority_filter = QComboBox()
        self.priority_filter.addItems(['Все приоритеты', 'Низкий', 'Обычный', 'Высокий', 'Срочный'])
        for combo in (self.board_filter, self.state_filter, self.priority_filter):
            filters.addWidget(combo)
            combo.currentIndexChanged.connect(self.render)
        self.view_mode = QComboBox()
        self.view_mode.addItems(['Список', 'Доска'])
        self.view_mode.setAccessibleName('Вид моих задач')
        filters.addWidget(self.view_mode)
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
        self.board = QScrollArea()
        self.board.setWidgetResizable(True)
        self.views = QStackedWidget()
        self.views.addWidget(self.table)
        self.views.addWidget(self.board)
        layout.addWidget(self.views)
        self.view_mode.currentIndexChanged.connect(self.views.setCurrentIndex)
        self.view_mode.currentIndexChanged.connect(self.render)

    def update_notices(self, counts):
        self.notices = counts
        for cards in self.card_lists.values():
            for row in range(cards.count()):
                item = cards.item(row)
                set_card_notice(item, counts.get(item.data(Qt.UserRole)['id'], 0))

    def render_board(self, tasks):
        grouped = {}
        for task in tasks:
            grouped.setdefault(task['column_name'], []).append(task)
        names = tuple(sorted(grouped, key=lambda name: (
            min(t['column_position'] for t in grouped[name]), name.casefold())))
        if names != self.board_structure:
            self.board_structure = names
            canvas = QWidget()
            canvas.setObjectName('BoardCanvas')
            layout = QHBoxLayout(canvas)
            self.card_lists = {}
            for index, name in enumerate(names):
                panel = QWidget()
                panel.setObjectName('ColumnPanel')
                panel.setProperty('tone', index % 5)
                panel.setAttribute(Qt.WA_StyledBackground, True)
                panel.setFixedWidth(316)
                column = QVBoxLayout(panel)
                label = QLabel(name)
                label.setTextFormat(Qt.PlainText)
                label.setObjectName('ColumnTitle')
                label.setWordWrap(True)
                label.setProperty('tone', index % 5)
                column.addWidget(label)
                cards = Cards(name, self.expanded_ids)
                cards.setDragDropMode(Cards.NoDragDrop)
                cards.itemClicked.connect(lambda item: self.opened.emit(item.data(Qt.UserRole)))
                cards.details_requested.connect(self.details_requested)
                column.addWidget(cards)
                layout.addWidget(panel, 0, Qt.AlignTop)
                self.card_lists[name] = cards
            layout.addStretch()
            self.board.setWidget(canvas)
        update_cards(self.card_lists, grouped, self.notices, self.expanded_ids)

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
        self.count.setText('Мои задачи: {}'.format(len(tasks)) if tasks else 'Нет подходящих назначенных задач')
        if self.view_mode.currentIndex():
            self.render_board(tasks)
            return
        scroll = self.table.verticalScrollBar().value()
        current = self.table.item(self.table.currentRow(), 0)
        selected = current.data(Qt.UserRole)['id'] if current else None
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
