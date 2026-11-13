import copy
import datetime as dt
import getpass
import json
from pathlib import Path
import tempfile
import uuid
from PyQt5.QtCore import Qt, QTimer, QObject, QRunnable, QThreadPool, pyqtSignal, QUrl
from PyQt5.QtGui import QDesktopServices
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QListWidget, QListWidgetItem, QPushButton, QLabel, QLineEdit, QTextEdit, QComboBox,
    QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QMessageBox, QInputDialog,
    QScrollArea, QFileDialog, QTabWidget, QTableWidget, QTableWidgetItem, QHeaderView)
from .store import Store, Conflict


class Signals(QObject):
    result = pyqtSignal(object, object)


class Job(QRunnable):
    def __init__(self, fn):
        super().__init__()
        self.fn = fn
        self.signals = Signals()

    def run(self):
        try:
            result = self.fn()
        except Exception as error:
            self.signals.result.emit(None, error)
        else:
            self.signals.result.emit(result, None)


class Async:
    def init_async(self):
        self.jobs = set()

    def work(self, fn, callback):
        job = Job(fn)
        self.jobs.add(job)
        def done(result, error):
            self.jobs.discard(job)
            callback(result, error)
        job.signals.result.connect(done)
        QThreadPool.globalInstance().start(job)


def button(text, fn, parent_layout):
    b = QPushButton(text)
    b.clicked.connect(fn)
    parent_layout.addWidget(b)
    return b


def split(text):
    return sorted(set(x.strip() for x in text.split(',') if x.strip()))


def error_text(error):
    return str(error) or type(error).__name__


class DropFiles(QListWidget):
    files = pyqtSignal(list)
    def __init__(self):
        super().__init__()
        self.setAcceptDrops(True)
        self.setToolTip('Перетащите файлы из Проводника или нажмите «Добавить файлы»')
    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()
    def dragMoveEvent(self, event):
        self.dragEnterEvent(event)
    def dropEvent(self, event):
        paths = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
        if paths:
            self.files.emit(paths)
            event.acceptProposedAction()


class TaskDialog(QDialog, Async):
    changed = pyqtSignal()
    def __init__(self, store, columns, data, parent=None):
        super().__init__(parent)
        self.init_async()
        self.store, self.columns = store, columns
        self.data = copy.deepcopy(data)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setWindowTitle('Задача №' + str(data['number']) if data.get('id') else 'Новая задача')
        self.resize(840, 760)
        self.setMinimumSize(640, 480)
        self.pending = False
        self.retry = None
        self.conflict_current = None
        self.comment_retry = None
        self.upload_queue = []
        root = QVBoxLayout(self)
        self.tabs = QTabWidget()
        root.addWidget(self.tabs)
        page = QWidget()
        form = QFormLayout(page)
        self.title = QLineEdit(data.get('title', ''))
        self.description = QTextEdit(data.get('description', ''))
        self.description.setAcceptRichText(False)
        self.assignees = QLineEdit(', '.join(data.get('assignees', [])))
        self.assignees.setPlaceholderText('Имена пользователей Windows через запятую')
        self.labels = QLineEdit(', '.join(data.get('labels', [])))
        self.priority = QComboBox()
        self.priority.addItems(['Низкий', 'Обычный', 'Высокий', 'Срочный'])
        self.priority.setCurrentText(data.get('priority', 'Обычный'))
        self.due = QLineEdit(data.get('due_date') or '')
        self.due.setPlaceholderText('ГГГГ-ММ-ДД или пусто')
        self.column = QComboBox()
        for c in columns:
            self.column.addItem(c['name'], c['id'])
        self.column.setCurrentIndex(max(0, self.column.findData(data['column_id'])))
        self.archived = QCheckBox('Задача в архиве')
        self.archived.setChecked(bool(data.get('archived')))
        for label, field in [('Название', self.title), ('Описание', self.description), ('Исполнители', self.assignees), ('Приоритет', self.priority), ('Метки через запятую', self.labels), ('Срок', self.due), ('Переместить в…', self.column), ('Архивирование', self.archived)]:
            form.addRow(label, field)
        self.checklist = QTextEdit()
        self.checklist.setAcceptRichText(False)
        self.checklist.setPlaceholderText('Одна строка — один пункт. Для выполненного: [x] Текст')
        self.checklist.setPlainText('\n'.join(('[x] ' if i['done'] else '[ ] ') + i['text'] for i in data.get('checklist', [])))
        self.checklist.setMaximumHeight(130)
        form.addRow('Чек-лист', self.checklist)
        dates = QLabel('Создано: {}\nИзменено: {}\nЗавершено: {}'.format(data.get('created_at', '—'), data.get('updated_at', '—'), data.get('completed_at') or '—'))
        dates.setTextFormat(Qt.PlainText)
        self.dates = dates
        form.addRow('Даты (UTC)', dates)
        form_scroll = QScrollArea()
        form_scroll.setWidgetResizable(True)
        form_scroll.setWidget(page)
        self.tabs.addTab(form_scroll, 'Задача')
        comments_page = QWidget()
        comments_layout = QVBoxLayout(comments_page)
        self.comments = QTextEdit()
        self.comments.setReadOnly(True)
        comments_layout.addWidget(self.comments)
        self.comment_text = QTextEdit()
        self.comment_text.setAcceptRichText(False)
        self.comment_text.setMaximumHeight(120)
        self.comment_text.setPlaceholderText('Комментарий')
        comments_layout.addWidget(self.comment_text)
        self.comment_button = button('Добавить комментарий', self.add_comment, comments_layout)
        self.tabs.addTab(comments_page, 'Комментарии')
        attachments_page = QWidget()
        attach_layout = QVBoxLayout(attachments_page)
        self.attachments = DropFiles()
        self.attachments.files.connect(self.add_files)
        self.attachments.itemDoubleClicked.connect(lambda _: self.download(True))
        attach_layout.addWidget(self.attachments)
        self.file_button = button('Добавить файлы…', self.choose_files, attach_layout)
        button('Открыть', lambda: self.download(True), attach_layout)
        button('Скачать как…', lambda: self.download(False), attach_layout)
        self.tabs.addTab(attachments_page, 'Вложения')
        self.history = QTextEdit()
        self.history.setReadOnly(True)
        self.tabs.addTab(self.history, 'История')
        self.status = QLabel('')
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.PlainText)
        root.addWidget(self.status)
        conflict_bar = QHBoxLayout()
        self.compare_button = button('Сравнить версии', self.compare, conflict_bar)
        self.rebase_button = button('Использовать актуальную версию как основу', self.rebase, conflict_bar)
        self.compare_button.hide()
        self.rebase_button.hide()
        root.addLayout(conflict_bar)
        actions = QHBoxLayout()
        self.save_button = button('Сохранить', self.save, actions)
        button('Закрыть', self.close, actions)
        root.addLayout(actions)
        self.render_extras(data)
        self.initial = self.payload()
        self.refresh_timer = QTimer(self)
        self.refresh_timer.timeout.connect(self.refresh_extras)
        self.refresh_timer.start(5000)
        self.extras_pending = False

    def payload(self):
        checklist = []
        for line in self.checklist.toPlainText().splitlines():
            text = line.strip()
            if not text:
                continue
            done = text.lower().startswith('[x]')
            if text.startswith('[ ]') or done:
                text = text[3:].strip()
            if text:
                checklist.append({'text': text, 'done': int(done)})
        p = {k: self.data[k] for k in ['id', 'version', 'board_id'] if k in self.data}
        p.update(title=self.title.text(), description=self.description.toPlainText(), assignees=split(self.assignees.text()), labels=split(self.labels.text()), priority=self.priority.currentText(), due_date=self.due.text().strip() or None, column_id=self.column.currentData(), archived=self.archived.isChecked(), checklist=checklist)
        return p

    def render_extras(self, d):
        self.dates.setText('Создано: {}\nИзменено: {}\nЗавершено: {}'.format(d.get('created_at', '—'), d.get('updated_at', '—'), d.get('completed_at') or '—'))
        self.comments.setPlainText('\n\n'.join(x['created_at'] + ' · ' + x['author'] + '\n' + x['body'] for x in d.get('comments', [])))
        selected = self.attachments.currentItem()
        selected_id = selected.data(Qt.UserRole)['id'] if selected else None
        self.attachments.clear()
        for x in d.get('attachments', []):
            item = QListWidgetItem(x['original_name'] + ' · ' + str(x['size']) + ' байт')
            item.setData(Qt.UserRole, x)
            self.attachments.addItem(item)
            if x['id'] == selected_id:
                self.attachments.setCurrentItem(item)
        self.history.setPlainText('\n\n'.join(x['created_at'] + ' · ' + x['author'] + ' · ' + x['operation'] + '\n' + x['changes'] for x in d.get('history', [])))
        for b in [self.comment_button, self.file_button]:
            b.setEnabled(bool(d.get('id')) and not self.pending)

    def refresh_extras(self):
        if not self.data.get('id') or self.extras_pending or self.pending:
            return
        self.extras_pending = True
        tid = self.data['id']
        def done(d, error):
            self.extras_pending = False
            if error:
                self.status.setText('Нет обновления: ' + error_text(error) + '. Введённые данные остаются в форме.')
                return
            self.render_extras(d)
            if d['version'] != self.data['version']:
                self.status.setText('Есть изменения коллег. Ваша форма сохранена; при записи потребуется сравнить версии.')
        self.work(lambda: self.store.detail(tid), done)

    def busy(self, enabled):
        self.pending = enabled
        self.save_button.setEnabled(not enabled)
        self.comment_button.setEnabled(not enabled and bool(self.data.get('id')))
        self.file_button.setEnabled(not enabled and bool(self.data.get('id')))
        self.tabs.setEnabled(not enabled)
        self.rebase_button.setEnabled(not enabled)

    def save(self):
        p = self.payload()
        if not p['title'].strip():
            self.status.setText('Введите название задачи')
            self.title.setFocus()
            return
        try:
            if p['due_date']:
                dt.date.fromisoformat(p['due_date'])
        except ValueError:
            self.status.setText('Срок должен иметь формат ГГГГ-ММ-ДД')
            self.due.setFocus()
            return
        # Resolve an uncertain previous commit first; keep newer edits in the form.
        if not self.retry:
            self.retry = (uuid.uuid4().hex, copy.deepcopy(p))
        rid, payload = self.retry
        self.busy(True)
        self.status.setText('Сохранение…')
        def done(result, error):
            self.busy(False)
            if error:
                self.status.setText(error_text(error))
                if isinstance(error, Conflict):
                    self.conflict_current = error.current
                    self.compare_button.show()
                    self.rebase_button.show()
                    self.retry = None
                elif isinstance(error, ValueError):
                    self.retry = None
                return
            self.data.update(id=result['id'], version=result['version'])
            self.retry = None
            self.initial = dict(payload, id=result['id'], version=result['version'])
            self.status.setText('Сохранено' if self.payload() == self.initial else 'Предыдущая запись подтверждена. Новые изменения остаются в форме; сохраните их отдельно.')
            self.setWindowTitle('Задача сохранена')
            self.changed.emit()
            self.refresh_extras()
        self.work(lambda: self.store.save_task(payload, rid), done)

    def compare(self):
        if not self.conflict_current:
            return
        d = QDialog(self)
        d.setWindowTitle('Сравнение: ваши данные и текущая версия')
        d.resize(950, 620)
        layout = QHBoxLayout(d)
        for title, value in [('Ваши данные', self.payload()), ('Текущая версия', self.conflict_current)]:
            col = QVBoxLayout()
            col.addWidget(QLabel(title))
            text = QTextEdit()
            text.setReadOnly(True)
            text.setPlainText(json.dumps(value, ensure_ascii=False, indent=2))
            col.addWidget(text)
            layout.addLayout(col)
        d.exec_()

    def rebase(self):
        if not self.conflict_current:
            return
        if QMessageBox.question(self, 'Разрешение конфликта', 'После сравнения отредактируйте свою форму. Следующее сохранение заменит текущие поля задачи вашими значениями. Продолжить?', QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        self.data['version'] = self.conflict_current['version']
        self.conflict_current = None
        self.compare_button.hide()
        self.rebase_button.hide()
        self.status.setText('Ваш текст оставлен в форме. Проверьте поля и нажмите «Сохранить».')

    def add_comment(self):
        if self.pending or not self.data.get('id'):
            return
        text = self.comment_text.toPlainText()
        if not text.strip():
            self.status.setText('Введите комментарий')
            return
        if self.comment_retry and text != self.comment_retry[1]:
            self.status.setText('Для безопасного повтора верните прежний текст комментария')
            return
        if not self.comment_retry:
            self.comment_retry = (uuid.uuid4().hex, text)
        rid, text = self.comment_retry
        tid = self.data['id']
        self.busy(True)
        def done(result, error):
            self.busy(False)
            if error:
                self.status.setText(error_text(error) + '. Повторите добавление: дубликат не появится.')
                return
            self.comment_retry = None
            self.comment_text.clear()
            self.status.setText('Комментарий добавлен')
            self.refresh_extras()
        self.work(lambda: self.store.comment(tid, text, rid), done)

    def choose_files(self):
        paths, _ = QFileDialog.getOpenFileNames(self, 'Добавить вложения')
        self.add_files(paths)

    def add_files(self, paths):
        if self.pending or not self.data.get('id'):
            self.status.setText('Сначала сохраните задачу и дождитесь завершения операции')
            return
        if self.upload_queue:
            self.status.setText('Сначала повторите незавершённую загрузку')
            self.upload_next()
            return
        self.upload_queue = [(path, uuid.uuid4().hex) for path in paths]
        self.upload_next()

    def upload_next(self):
        if not self.upload_queue:
            self.refresh_extras()
            self.changed.emit()
            return
        path, rid = self.upload_queue[0]
        tid = self.data['id']
        self.busy(True)
        self.status.setText('Загрузка: ' + Path(path).name)
        def done(result, error):
            self.busy(False)
            if error:
                self.status.setText(error_text(error) + '. Нажмите «Добавить файлы» для повтора незавершённой загрузки.')
                return
            self.upload_queue.pop(0)
            self.status.setText('Вложение добавлено')
            self.upload_next()
        self.work(lambda: self.store.attach(tid, path, rid), done)

    def download(self, open_file):
        item = self.attachments.currentItem()
        if not item or self.pending:
            return
        attachment = item.data(Qt.UserRole)
        if open_file:
            folder = Path(tempfile.mkdtemp(prefix='kanban-'))
            dest = str(folder / Path(attachment['original_name']).name)
        else:
            dest, _ = QFileDialog.getSaveFileName(self, 'Скачать вложение', attachment['original_name'])
            if not dest:
                return
            if Path(dest).exists():
                self.status.setText('Файл уже существует. Выберите другое имя; замена не выполняется.')
                return
        self.busy(True)
        def done(path, error):
            self.busy(False)
            if error:
                self.status.setText('Не удалось получить вложение: ' + error_text(error))
            elif open_file:
                if not QDesktopServices.openUrl(QUrl.fromLocalFile(path)):
                    self.status.setText('Нет приложения для открытия файла. Скачайте вложение.')
            else:
                self.status.setText('Файл сохранён: ' + path)
        self.work(lambda: self.store.download(attachment['id'], dest), done)

    def reject(self):
        self.close()

    def closeEvent(self, event):
        if self.pending or self.jobs:
            self.status.setText('Дождитесь завершения операции')
            event.ignore()
            return
        dirty = self.payload() != self.initial or bool(self.comment_text.toPlainText().strip()) or bool(self.upload_queue) or bool(self.retry) or bool(self.comment_retry)
        if dirty and QMessageBox.question(self, 'Несохранённые данные', 'Закрыть карточку и потерять несохранённые изменения или данные для повтора?', QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            event.ignore()
            return
        self.refresh_timer.stop()
        event.accept()


class ColumnsDialog(QDialog):
    def __init__(self, columns, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Колонки')
        self.resize(720, 440)
        self.original = columns
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel('Порядок строк задаёт порядок колонок. Лимит 0 — без ограничения.'))
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(['Название', 'Завершение (да)', 'Лимит'])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        layout.addWidget(self.table)
        for c in columns:
            self.add_row(c)
        actions = QHBoxLayout()
        button('Добавить', lambda: self.add_row({'name': 'Новая колонка', 'is_done': 0, 'wip_limit': 0}), actions)
        button('Выше', lambda: self.move(-1), actions)
        button('Ниже', lambda: self.move(1), actions)
        layout.addLayout(actions)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText('Сохранить')
        buttons.button(QDialogButtonBox.Cancel).setText('Отмена')
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def add_row(self, c):
        row = self.table.rowCount()
        self.table.insertRow(row)
        for i, value in enumerate([c['name'], 'да' if c['is_done'] else '', str(c.get('wip_limit') or 0)]):
            item = QTableWidgetItem(value)
            if i == 0:
                item.setData(Qt.UserRole, c.get('id'))
            self.table.setItem(row, i, item)

    def move(self, delta):
        row = self.table.currentRow()
        target = row + delta
        if row < 0 or target < 0 or target >= self.table.rowCount():
            return
        for col in range(3):
            a = self.table.takeItem(row, col)
            b = self.table.takeItem(target, col)
            self.table.setItem(row, col, b)
            self.table.setItem(target, col, a)
        self.table.setCurrentCell(target, 0)

    def values(self):
        values = []
        for row in range(self.table.rowCount()):
            limit = int(self.table.item(row, 2).text())
            if limit < 0:
                raise ValueError('Лимит не может быть отрицательным')
            done = self.table.item(row, 1).text().strip().casefold()
            if done not in ['', 'да']:
                raise ValueError('В колонке завершения укажите «да» либо оставьте пусто')
            values.append({'id': self.table.item(row, 0).data(Qt.UserRole), 'name': self.table.item(row, 0).text(), 'is_done': done == 'да', 'wip_limit': limit or None})
        return values


class Cards(QListWidget):
    moved = pyqtSignal(int, int, object)
    def __init__(self, column_id):
        super().__init__()
        self.column_id = column_id
        self.setDragDropMode(QListWidget.DragDrop)
        self.setDefaultDropAction(Qt.MoveAction)
        self.setSelectionMode(QListWidget.SingleSelection)
        self.setSpacing(6)
        self.setWordWrap(True)
        self.setMinimumWidth(250)
        self.setAccessibleName('Задачи колонки')
    def dropEvent(self, event):
        source = event.source()
        if not isinstance(source, Cards) or not source.currentItem():
            event.ignore()
            return
        task = source.currentItem().data(Qt.UserRole)
        index = self.indexAt(event.pos()).row()
        before_id = self.item(index).data(Qt.UserRole)['id'] if index >= 0 else None
        self.moved.emit(task['id'], self.column_id, before_id)
        event.acceptProposedAction()


class MainWindow(QMainWindow, Async):
    def __init__(self, folder):
        super().__init__()
        self.init_async()
        self.folder = folder
        self.store = None
        self.board_id = None
        self.columns_data = []
        self.dialogs = set()
        self.loading = False
        self.mutating = False
        self.pending_action = None
        self.generation = 0
        self.setWindowTitle('Канбан')
        self.resize(1400, 850)
        self.setMinimumSize(900, 600)
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        sidebar = QWidget()
        sidebar.setObjectName('Sidebar')
        sidebar.setFixedWidth(220)
        side = QVBoxLayout(sidebar)
        heading = QLabel('Канбан')
        heading.setObjectName('Heading')
        side.addWidget(heading)
        user = QLabel('Пользователь: ' + getpass.getuser())
        user.setTextFormat(Qt.PlainText)
        user.setWordWrap(True)
        side.addWidget(user)
        self.boards_list = QListWidget()
        self.boards_list.currentItemChanged.connect(self.select_board)
        side.addWidget(self.boards_list)
        button('Создать доску', lambda: self.board_action('create'), side)
        button('Переименовать', lambda: self.board_action('rename'), side)
        button('Архивировать доску', lambda: self.board_action('archive'), side)
        button('Резервная копия…', self.backup, side)
        root.addWidget(sidebar)
        content = QVBoxLayout()
        root.addLayout(content, 1)
        top = QHBoxLayout()
        self.board_title = QLabel('Выберите или создайте доску')
        self.board_title.setTextFormat(Qt.PlainText)
        self.board_title.setObjectName('Heading')
        top.addWidget(self.board_title, 1)
        button('Колонки…', self.edit_columns, top)
        button('Новая задача', self.new_task, top)
        button('Обновить', self.refresh, top)
        button('Повторить запись', self.retry_mutation, top)
        content.addLayout(top)
        filters = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText('Поиск по названию и описанию')
        self.search.setAccessibleName('Поиск задач')
        self.search.setClearButtonEnabled(True)
        filters.addWidget(self.search, 2)
        self.quick = QComboBox()
        self.quick.addItems(['Все задачи', 'Мои задачи', 'Просроченные', 'Без исполнителя'])
        filters.addWidget(self.quick)
        self.priority_filter = QComboBox()
        self.priority_filter.addItems(['Все приоритеты', 'Низкий', 'Обычный', 'Высокий', 'Срочный'])
        filters.addWidget(self.priority_filter)
        self.archive_filter = QCheckBox('Архив')
        filters.addWidget(self.archive_filter)
        content.addLayout(filters)
        advanced = QHBoxLayout()
        self.assignee_filter = QLineEdit()
        self.assignee_filter.setPlaceholderText('Исполнитель: точное имя')
        self.label_filter = QLineEdit()
        self.label_filter.setPlaceholderText('Метка: точное название')
        self.due_from = QLineEdit()
        self.due_from.setPlaceholderText('Срок с ГГГГ-ММ-ДД')
        self.due_to = QLineEdit()
        self.due_to.setPlaceholderText('Срок по ГГГГ-ММ-ДД')
        for field in [self.assignee_filter, self.label_filter, self.due_from, self.due_to]:
            advanced.addWidget(field)
        content.addLayout(advanced)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        content.addWidget(self.scroll, 1)
        self.status = QLabel('Подключение к общей папке…')
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.PlainText)
        content.addWidget(self.status)
        bottom = QHBoxLayout()
        button('Открыть карточку', self.open_selected, bottom)
        button('Переместить в…', self.move_selected, bottom)
        button('Копировать', self.copy_selected, bottom)
        button('Архивировать / восстановить', self.archive_selected, bottom)
        content.addLayout(bottom)
        self.debounce = QTimer(self)
        self.debounce.setSingleShot(True)
        self.debounce.timeout.connect(self.refresh)
        for field in [self.search, self.assignee_filter, self.label_filter, self.due_from, self.due_to]:
            field.textChanged.connect(self.filters_changed)
        self.quick.currentIndexChanged.connect(self.filters_changed)
        self.priority_filter.currentIndexChanged.connect(self.filters_changed)
        self.archive_filter.toggled.connect(self.filters_changed)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(5000)
        self.card_lists = []
        self.connect_store()

    def connect_store(self):
        if self.loading:
            return
        self.loading = True
        def done(store, error):
            self.loading = False
            if error:
                self.status.setText('Общая папка недоступна: ' + error_text(error) + '. Проверьте kanban.ini и нажмите «Обновить».')
                return
            self.store = store
            self.refresh()
        self.work(lambda: Store(self.folder), done)

    def filters_changed(self, *args):
        self.generation += 1
        self.debounce.start(300)

    def select_board(self, current, previous):
        self.board_id = current.data(Qt.UserRole) if current else None
        self.generation += 1
        self.refresh()

    def filters(self):
        quick = self.quick.currentIndex()
        return {'query': self.search.text(), 'priority': self.priority_filter.currentText() if self.priority_filter.currentIndex() else '', 'archived': self.archive_filter.isChecked(), 'assignee': getpass.getuser() if quick == 1 else self.assignee_filter.text().strip(), 'overdue': quick == 2, 'unassigned': quick == 3, 'label': self.label_filter.text().strip(), 'due_from': self.due_from.text().strip(), 'due_to': self.due_to.text().strip()}

    def refresh(self):
        if self.loading or self.mutating or self.pending_action:
            return
        if not self.store:
            self.connect_store()
            return
        f = self.filters()
        try:
            for key in ['due_from', 'due_to']:
                if f[key]:
                    dt.date.fromisoformat(f[key])
        except ValueError:
            self.status.setText('Фильтр срока: введите дату ГГГГ-ММ-ДД')
            return
        generation = self.generation
        bid = self.board_id
        self.loading = True
        def read():
            boards = self.store.boards()
            selected = bid if any(b['id'] == bid for b in boards) else (boards[0]['id'] if boards else None)
            return boards, selected, self.store.columns(selected) if selected else [], self.store.tasks(selected, f) if selected else [], self.store.tasks(selected) if selected else []
        def done(result, error):
            self.loading = False
            if generation != self.generation:
                self.debounce.start(1)
                return
            if error:
                self.status.setText('Обновление не выполнено: ' + error_text(error) + '. Можно повторить. Открытые формы сохранены.')
                return
            boards, selected, columns, tasks, all_tasks = result
            self.board_id = selected
            self.boards_list.blockSignals(True)
            self.boards_list.clear()
            for b in boards:
                item = QListWidgetItem(b['name'])
                item.setData(Qt.UserRole, b['id'])
                self.boards_list.addItem(item)
                if b['id'] == selected:
                    self.boards_list.setCurrentItem(item)
                    self.board_title.setText(b['name'])
            self.boards_list.blockSignals(False)
            if not selected:
                self.board_title.setText('Создайте первую доску')
            self.columns_data = columns
            selected_task = self.selected_task()
            focused = any(x.hasFocus() for x in self.card_lists)
            board = QWidget()
            row = QHBoxLayout(board)
            row.setSizeConstraint(QHBoxLayout.SetMinimumSize)
            self.card_lists = []
            for column in columns:
                panel = QWidget()
                panel.setMinimumWidth(270)
                layout = QVBoxLayout(panel)
                count = sum(t['column_id'] == column['id'] for t in all_tasks)
                warning = bool(column['wip_limit'] and count > column['wip_limit'])
                label = QLabel(column['name'] + ' · ' + str(count) + (' / ' + str(column['wip_limit']) if column['wip_limit'] else '') + (' · Лимит превышен' if warning else ''))
                label.setTextFormat(Qt.PlainText)
                label.setWordWrap(True)
                layout.addWidget(label)
                cards = Cards(column['id'])
                cards.itemDoubleClicked.connect(lambda item: self.open_task(item.data(Qt.UserRole)))
                cards.itemSelectionChanged.connect(lambda cards=cards: self.exclusive_selection(cards))
                cards.moved.connect(self.move_task)
                for task in tasks:
                    if task['column_id'] != column['id']:
                        continue
                    overdue = task['due_date'] and task['due_date'] < dt.date.today().isoformat() and not task['completed_at']
                    check = task['checklist']
                    text = '№{}  {}\n{}\n{}{} · {}\n{}{}'.format(task['number'], task['title'], ', '.join(task['assignees']) or 'Без исполнителя', 'Просрочено: ' if overdue else 'Срок: ', task['due_date'] or 'не задан', task['priority'], ', '.join(task['labels']), '\nЧек-лист: {}/{}'.format(sum(bool(x['done']) for x in check), len(check)) if check else '')
                    item = QListWidgetItem(text)
                    item.setData(Qt.UserRole, task)
                    cards.addItem(item)
                    if selected_task and selected_task['id'] == task['id']:
                        cards.setCurrentItem(item)
                        if focused:
                            QTimer.singleShot(0, cards.setFocus)
                layout.addWidget(cards)
                row.addWidget(panel)
                self.card_lists.append(cards)
            if not columns:
                row.addWidget(QLabel('Создайте доску, чтобы добавить задачи.'))
            old = self.scroll.takeWidget()
            if old:
                old.deleteLater()
            self.scroll.setWidget(board)
            self.status.setText('Показано задач: {}. Обновление каждые 5 секунд. Двойной щелчок открывает карточку.'.format(len(tasks)) if tasks else 'Подходящих задач нет. Измените фильтры или создайте задачу.')
        self.work(read, done)

    def exclusive_selection(self, source):
        if source.selectedItems():
            for cards in self.card_lists:
                if cards is not source:
                    cards.clearSelection()

    def selected_task(self):
        for cards in self.card_lists:
            if cards.selectedItems():
                return cards.selectedItems()[0].data(Qt.UserRole)
        return None

    def open_selected(self):
        task = self.selected_task()
        if task:
            self.open_task(task)
        else:
            self.status.setText('Выберите задачу')

    def open_task(self, task):
        d = TaskDialog(self.store, self.columns_data, task, self)
        self.dialogs.add(d)
        d.changed.connect(self.refresh)
        d.destroyed.connect(lambda: self.dialogs.discard(d))
        d.show()

    def new_task(self):
        if not self.board_id or not self.columns_data:
            self.status.setText('Сначала создайте или выберите доску')
            return
        self.open_task({'board_id': self.board_id, 'column_id': self.columns_data[0]['id']})

    def mutate(self, fn, success='Сохранено', retry=False):
        if self.pending_action and not retry:
            self.status.setText('Результат прошлой записи неизвестен. Нажмите «Повторить запись»; новая операция пока недоступна.')
            return
        if self.mutating:
            self.status.setText('Дождитесь текущей операции')
            return
        if not retry:
            self.pending_action = (fn, success, uuid.uuid4().hex)
        rid = self.pending_action[2]
        self.mutating = True
        self.status.setText('Сохранение…')
        def done(result, error):
            self.mutating = False
            if error:
                self.status.setText(error_text(error) + '. При сетевой ошибке нажмите «Повторить запись».')
                if isinstance(error, (Conflict, ValueError)):
                    self.pending_action = None
                if isinstance(error, Conflict):
                    self.open_task(error.current)
            else:
                self.pending_action = None
                self.status.setText(success)
                self.refresh()
        self.work(lambda: fn(rid), done)

    def retry_mutation(self):
        if self.pending_action:
            self.mutate(self.pending_action[0], self.pending_action[1], retry=True)
        else:
            self.status.setText('Нет незавершённой записи')

    def board_action(self, action):
        if not self.store:
            self.status.setText('Сначала подключитесь к общей папке')
            return
        item = self.boards_list.currentItem()
        if action != 'create' and not item:
            return
        bid = item.data(Qt.UserRole) if item else None
        if action == 'archive':
            name = item.text()
            if QMessageBox.question(self, 'Архивирование доски', 'Архивировать «' + name + '»? Доска будет скрыта у всех сотрудников.', QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                return
            self.mutate(lambda rid: self.store.board_action(name, bid, True, rid))
        else:
            name, ok = QInputDialog.getText(self, 'Доска', 'Название:', text=item.text() if action == 'rename' else '')
            if ok:
                self.mutate(lambda rid: self.store.board_action(name, bid if action == 'rename' else None, request_id=rid))

    def edit_columns(self):
        if not self.board_id:
            return
        d = ColumnsDialog(self.columns_data, self)
        if d.exec_() == QDialog.Accepted:
            try:
                values = d.values()
            except ValueError as e:
                self.status.setText(str(e))
                return
            bid = self.board_id
            self.mutate(lambda rid: self.store.save_columns(bid, values, rid))

    def move_task(self, task_id, column_id, before_id=None):
        # Use the version shown on screen, so a stale drag cannot overwrite a colleague's edit.
        task = next((cards.item(i).data(Qt.UserRole) for cards in self.card_lists for i in range(cards.count()) if cards.item(i).data(Qt.UserRole)['id'] == task_id), None)
        if not task:
            return
        task = copy.deepcopy(task)
        displayed = sorted([cards.item(i).data(Qt.UserRole) for cards in self.card_lists for i in range(cards.count()) if cards.item(i).data(Qt.UserRole)['column_id'] == column_id and cards.item(i).data(Qt.UserRole)['id'] != task_id], key=lambda t: (t['position'], t['id']))
        if before_id == task_id:
            return
        index = next((i for i, x in enumerate(displayed) if x['id'] == before_id), len(displayed))
        low = displayed[index - 1]['position'] if index else 0
        high = displayed[index]['position'] if index < len(displayed) else low + 2048
        task['position'] = (low + high) / 2
        task['column_id'] = column_id
        self.mutate(lambda rid: self.store.save_task(task, rid))

    def move_selected(self):
        task = self.selected_task()
        if not task:
            self.status.setText('Выберите задачу')
            return
        names = [c['name'] for c in self.columns_data]
        name, ok = QInputDialog.getItem(self, 'Переместить в…', 'Колонка:', names, 0, False)
        if ok:
            self.move_task(task['id'], self.columns_data[names.index(name)]['id'])

    def copy_selected(self):
        task = self.selected_task()
        if not task:
            self.status.setText('Выберите задачу')
            return
        data = copy.deepcopy(task)
        for field in ['id', 'version', 'number', 'comments', 'attachments', 'history', 'created_at', 'updated_at', 'completed_at', 'position']:
            data.pop(field, None)
        data['title'] += ' (копия)'
        data['archived'] = False
        self.open_task(data)

    def archive_selected(self):
        task = self.selected_task()
        if not task:
            self.status.setText('Выберите задачу')
            return
        data = copy.deepcopy(task)
        data['archived'] = not data['archived']
        verb = 'Восстановить' if not data['archived'] else 'Архивировать'
        if QMessageBox.question(self, verb, verb + ' задачу №' + str(data['number']) + '?', QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes:
            self.mutate(lambda rid: self.store.save_task(data, rid))

    def backup(self):
        if not self.store:
            return
        folder = QFileDialog.getExistingDirectory(self, 'Каталог резервных копий (вне рабочей папки данных)')
        if folder:
            self.mutate(lambda rid: self.store.backup(folder), 'Проверенная резервная копия создана')

    def closeEvent(self, event):
        if self.jobs or self.mutating or any(d.pending for d in self.dialogs):
            self.status.setText('Дождитесь завершения операций перед выходом')
            event.ignore()
            return
        if self.pending_action and QMessageBox.question(self, 'Незавершённая запись', 'Результат записи неизвестен. Перед выходом рекомендуется повторить запись. Всё равно выйти?', QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            event.ignore()
            return
        for dialog in list(self.dialogs):
            if not dialog.close():
                event.ignore()
                return
        self.timer.stop()
        event.accept()
