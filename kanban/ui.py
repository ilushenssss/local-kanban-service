import copy
import datetime as dt
import getpass
import json
from pathlib import Path
import tempfile
import uuid
from PyQt5.QtCore import Qt, QTimer, QObject, QRunnable, QThreadPool, pyqtSignal, QUrl, QPoint
from PyQt5.QtGui import QDesktopServices, QDrag
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QListWidget, QListWidgetItem, QPushButton, QLabel, QLineEdit, QTextEdit, QComboBox,
    QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QMessageBox, QInputDialog,
    QScrollArea, QFileDialog, QTabWidget, QTableWidget, QTableWidgetItem, QHeaderView, QMenu, QTabBar, QSplitter, QTreeWidget, QTreeWidgetItem, QSizePolicy)
from .store import Store, Conflict
from .widgets import RegistrySelection, DateField, ChecklistEditor, PriorityCombo
from .presentation import history_html, date_text, comments_html
from .cards import CardDelegate
from .statistics import StatisticsPage
from .my_tasks import MyTasksPage
from .board_views import BoardView
from .theme import apply_theme, theme_settings, label_icon


class ElidedLabel(QLabel):
    """Keep long board names readable without expanding the header."""
    def setText(self, text):
        self.full_text = text
        self.setToolTip(text)
        self.render_text()

    def render_text(self):
        super().setText(self.fontMetrics().elidedText(getattr(self, 'full_text', ''), Qt.ElideRight, max(20, self.width())))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.render_text()


class QuietStatus(QLabel):
    """Reserve space only while there is useful feedback to show."""
    def setText(self, text):
        super().setText(text)
        self.setVisible(bool(text))

    def clear(self):
        self.setText('')


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


def button(text, fn, parent_layout, variant=None):
    b = QPushButton(text)
    b.clicked.connect(fn)
    b.setAutoDefault(False)
    b.setCursor(Qt.PointingHandCursor)
    if variant:
        b.setProperty('variant', variant)
        if variant == 'primary':
            b.setObjectName('primary')
    parent_layout.addWidget(b)
    return b


def style_form(form):
    form.setRowWrapPolicy(QFormLayout.WrapAllRows)
    form.setVerticalSpacing(8)
    for row in range(form.rowCount()):
        item = form.itemAt(row, QFormLayout.LabelRole)
        if item and item.widget():
            item.widget().setObjectName('fieldLabel')


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


class CatalogDialog(QDialog, Async):
    changed = pyqtSignal()

    def __init__(self, store, board_id, kind, parent=None):
        super().__init__(parent)
        self.init_async()
        self.store, self.board_id, self.kind = store, board_id, kind
        self.edit_id = None
        self.retry = None
        self.setWindowTitle('Сотрудники' if kind == 'users' else 'Метки доски')
        self.resize(540, 540)
        root = QVBoxLayout(self)
        heading = QLabel(self.windowTitle())
        heading.setObjectName('Heading')
        root.addWidget(heading)
        hint = QLabel('Имя Windows связывает сотрудника с фильтром «Мои задачи».\nЭто справочник исполнителей, а не система входа.' if kind == 'users' else 'Метки доступны всем задачам этой доски.')
        hint.setObjectName('Muted')
        hint.setWordWrap(True)
        root.addWidget(hint)
        self.list = QListWidget()
        root.addWidget(self.list, 1)
        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.WrapAllRows)
        self.login = QLineEdit()
        self.login.setPlaceholderText('Например, ivanov')
        self.name = QLineEdit()
        self.name.setPlaceholderText('Иванов Иван' if kind == 'users' else 'Например, Документы')
        if kind == 'users':
            form.addRow('Пользователь Windows', self.login)
        form.addRow('Имя сотрудника' if kind == 'users' else 'Название метки', self.name)
        style_form(form)
        root.addLayout(form)
        actions = QHBoxLayout()
        self.save_button = button('Добавить', self.save_entry, actions, 'primary')
        if kind == 'users':
            self.edit_button = button('Изменить имя', self.edit_entry, actions)
        button('Закрыть', self.reject, actions)
        root.addLayout(actions)
        self.status = QLabel('Загрузка справочника…')
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.PlainText)
        root.addWidget(self.status)
        self.load()

    def load(self):
        self.save_button.setEnabled(False)
        def done(data, error):
            self.save_button.setEnabled(True)
            if error:
                self.status.setText(error_text(error) + '. Закройте и откройте справочник для повтора.')
                return
            self.list.clear()
            rows = sorted(data[self.kind], key=lambda x: (x.get('display_name') or x.get('name')).casefold())
            for row in rows:
                title = '{} · {}'.format(row['display_name'], row['login']) if self.kind == 'users' else row['name']
                item = QListWidgetItem(title)
                if self.kind == 'labels':
                    item.setIcon(label_icon(row['name']))
                item.setData(Qt.UserRole, row)
                self.list.addItem(item)
            self.status.setText('Записей: {}'.format(len(rows)))
        self.work(lambda: self.store.catalogs(self.board_id), done)

    def edit_entry(self):
        if self.jobs or self.retry or not self.list.currentItem():
            return
        row = self.list.currentItem().data(Qt.UserRole)
        self.edit_id = row['id']
        self.login.setText(row['login'])
        self.login.setReadOnly(True)
        self.name.setText(row['display_name'])
        self.save_button.setText('Сохранить имя')
        self.name.setFocus()

    def save_entry(self):
        if self.jobs:
            return
        payload = (self.login.text(), self.name.text(), self.edit_id)
        if not self.retry:
            self.retry = (uuid.uuid4().hex, payload)
        rid, payload = self.retry
        login, name, user_id = payload
        self.save_button.setEnabled(False)
        self.status.setText('Сохранение…')
        def apply():
            if self.kind == 'users':
                return self.store.save_user(login, name, user_id, rid)
            return self.store.add_label(self.board_id, name, rid)
        def done(result, error):
            self.save_button.setEnabled(True)
            if error:
                if isinstance(error, ValueError):
                    self.retry = None
                self.status.setText(error_text(error) + '. Повторное нажатие безопасно.')
                return
            self.retry = None
            if (self.login.text(), self.name.text(), self.edit_id) == payload:
                self.edit_id = None
                self.login.setReadOnly(False)
                self.login.clear()
                self.name.clear()
                self.save_button.setText('Добавить')
            self.changed.emit()
            self.load()
        self.work(apply, done)

    def reject(self):
        if self.jobs:
            self.status.setText('Дождитесь завершения операции')
            return
        if (self.name.text().strip() or self.login.text().strip() or self.retry) and QMessageBox.question(self, 'Несохранённые данные', 'Закрыть справочник без сохранения введённых данных?', QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        super().reject()

    def closeEvent(self, event):
        if self.jobs:
            event.ignore()
        else:
            event.ignore()
            self.reject()


class TaskDialog(QDialog, Async):
    changed = pyqtSignal()
    def __init__(self, store, columns, data, parent=None):
        super().__init__(parent)
        self.init_async()
        self.store, self.columns = store, columns
        self.data = copy.deepcopy(data)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setWindowTitle('Задача №' + str(data['number']) if data.get('id') else 'Новая задача')
        self.resize(1180, 740)
        self.setMinimumSize(640, 480)
        self.pending = False
        self.retry = None
        self.conflict_current = None
        self.comment_retry = None
        self.upload_queue = []
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(12)
        heading = QLabel('Задача №{}'.format(data['number']) if data.get('id') else 'Новая задача')
        heading.setObjectName('Heading')
        root.addWidget(heading)
        self.tabs = QTabWidget()
        self.task_splitter = QSplitter(Qt.Horizontal)
        root.addWidget(self.task_splitter, 1)
        self.task_splitter.addWidget(self.tabs)
        page = QWidget()
        page.setObjectName('scrollBody')
        form_columns = QVBoxLayout(page)
        form = QFormLayout()
        properties = QFormLayout()
        properties.setRowWrapPolicy(QFormLayout.WrapAllRows)
        properties.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        form_columns.addLayout(form)
        form_columns.addLayout(properties)
        form.setContentsMargins(4, 20, 12, 16)
        form.setVerticalSpacing(8)
        form.setRowWrapPolicy(QFormLayout.WrapAllRows)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        self.title = QLineEdit(data.get('title', ''))
        self.title.setPlaceholderText('Что нужно сделать?')
        self.description = QTextEdit()
        self.description.setPlaceholderText('Подробности, контекст и ожидаемый результат…')
        self.description.setMinimumHeight(110)
        self.description.setMaximumHeight(160)
        self.description.setAcceptRichText(False)
        self.description.setPlainText(data.get('description', ''))
        self.catalog_data = {'users': [], 'labels': []}
        self.catalog_pending = False
        self.catalog_ready = False
        self.assignees = RegistrySelection('Выберите исполнителей', data.get('assignees', []))
        self.labels = RegistrySelection('Выберите метки', data.get('labels', []), colored=True)
        self.priority = PriorityCombo()
        self.priority.setCurrentText(data.get('priority', 'Обычный'))
        self.due = DateField(data.get('due_date'))
        self.column = QComboBox()
        for c in columns:
            self.column.addItem(c['name'], c['id'])
        self.column.setCurrentIndex(max(0, self.column.findData(data['column_id'])))
        self.archived = QCheckBox('Задача в архиве')
        self.archived.setChecked(bool(data.get('archived')))
        form.addRow('Название', self.title)
        form.addRow('Описание', self.description)
        for caption, field, kind in [('Исполнители', self.assignees, 'users'), ('Метки', self.labels, 'labels')]:
            wrapper = QWidget()
            line = QHBoxLayout(wrapper)
            line.setContentsMargins(0, 0, 0, 0)
            line.addWidget(field, 1)
            catalog_button = button('+', lambda checked=False, kind=kind: self.open_catalog(kind), line)
            catalog_button.setFixedWidth(30)
            catalog_button.setToolTip('Открыть справочник')
            catalog_button.setAccessibleName('Открыть справочник ' + caption.lower())
            properties.addRow(caption, wrapper)
        properties.addRow('Приоритет', self.priority)
        properties.addRow('Срок', self.due)
        self.column.hide()
        if data.get('id'):
            properties.addRow('Архивирование', self.archived)
        self.checklist = ChecklistEditor(data.get('checklist', []))
        checklist_form = QFormLayout()
        checklist_form.addRow('Чек-лист', self.checklist)
        style_form(checklist_form)
        form_columns.addLayout(checklist_form)
        dates = QLabel('Создано: {}\nИзменено: {}\nЗавершено: {}'.format(data.get('created_at', '—'), data.get('updated_at', '—'), data.get('completed_at') or '—'))
        dates.setTextFormat(Qt.PlainText)
        self.dates = dates
        if data.get('id'):
            dates.setObjectName('Muted')
            dates.setToolTip('Время событий в UTC')
        style_form(form)
        style_form(properties)
        form_scroll = QScrollArea()
        form_scroll.setWidgetResizable(True)
        form_scroll.setWidget(page)
        self.tabs.addTab(form_scroll, 'Задача')
        comments_page = QWidget()
        comments_layout = QVBoxLayout(comments_page)
        chat_heading = QLabel('Обсуждение')
        chat_heading.setObjectName('ChatHeading')
        comments_layout.addWidget(chat_heading)
        self.comments = QTextEdit()
        self.comments.setReadOnly(True)
        self.comments.setObjectName('ChatTimeline')
        comments_layout.addWidget(self.comments)
        self.comment_text = QTextEdit()
        self.comment_text.setAcceptRichText(False)
        self.comment_text.setMaximumHeight(120)
        self.comment_text.setPlaceholderText('Написать сообщение…')
        self.comment_text.setMaximumHeight(88)
        comments_layout.addWidget(self.comment_text)
        self.comment_button = button('Отправить сообщение', self.add_comment, comments_layout, 'primary')
        self.comments.verticalScrollBar().valueChanged.connect(lambda: QTimer.singleShot(0, self.acknowledge_comments))
        self.chat_page = comments_page
        self.task_splitter.addWidget(comments_page)
        comments_page.setMinimumHeight(180)
        self.task_splitter.setChildrenCollapsible(False)
        self.task_splitter.setStretchFactor(0, 3)
        self.task_splitter.setStretchFactor(1, 2)
        self.task_splitter.setSizes([600, 380])
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
        self.history_markup = ''
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
        actions.addStretch()
        self.save_button = button('Сохранить задачу', self.save, actions, 'primary')
        self.save_button.setFixedWidth(172)
        self.save_button.setAutoDefault(False)
        self.save_button.setDefault(False)
        button('Закрыть', self.close, actions).setFixedWidth(100)
        root.addLayout(actions)
        self.last_read_comment = 0
        self.read_pending = False
        self.render_extras(data)
        self.initial = self.payload()
        self.refresh_timer = QTimer(self)
        self.refresh_timer.timeout.connect(self.refresh_extras)
        self.refresh_timer.start(5000)
        self.extras_pending = False
        self.load_catalogs()

    def payload(self):
        checklist = self.checklist.values()
        p = {k: self.data[k] for k in ['id', 'version', 'board_id'] if k in self.data}
        p.update(title=self.title.text(), description=self.description.toPlainText(), assignees=self.assignees.values(), labels=self.labels.values(), priority=self.priority.currentText(), due_date=self.due.text().strip() or None, column_id=self.column.currentData(), archived=self.archived.isChecked(), checklist=checklist)
        return p

    def render_extras(self, d):
        self.dates.setText('Создано: {}\nИзменено: {}\nЗавершено: {}'.format(date_text(d.get('created_at'), True), date_text(d.get('updated_at'), True), date_text(d.get('completed_at'), True)))
        self.history_entries = d.get('history', [])
        self.comment_entries = d.get('comments', [])
        self.render_comments()
        QTimer.singleShot(0, self.acknowledge_comments)
        selected = self.attachments.currentItem()
        selected_id = selected.data(Qt.UserRole)['id'] if selected else None
        self.attachments.clear()
        for x in d.get('attachments', []):
            item = QListWidgetItem(x['original_name'] + ' · ' + str(x['size']) + ' байт')
            item.setData(Qt.UserRole, x)
            self.attachments.addItem(item)
            if x['id'] == selected_id:
                self.attachments.setCurrentItem(item)
        self.history_entries = d.get('history', [])
        self.render_history()
        for b in [self.comment_button, self.file_button]:
            b.setEnabled(bool(d.get('id')) and not self.pending)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'task_splitter'):
            self.task_splitter.setOrientation(Qt.Vertical if self.width() < 1000 else Qt.Horizontal)

    def acknowledge_comments(self):
        if not self.isVisible() or not self.chat_page.isVisible() or self.read_pending or not self.data.get('id'):
            return
        if self.comments.verticalScrollBar().value() < self.comments.verticalScrollBar().maximum() - 4:
            return
        latest = max((x['id'] for x in self.comment_entries), default=0)
        if latest <= self.last_read_comment:
            return
        self.read_pending = True
        def done(value, error):
            self.read_pending = False
            if not error:
                self.last_read_comment = value
                self.changed.emit()
        self.work(lambda: self.store.mark_read(self.data['id'], latest), done)

    def render_comments(self):
        from .presentation import conversation_html
        markup = conversation_html(self.comment_entries, self.history_entries, self.store.login, self.columns)
        if markup != getattr(self, 'comments_markup', None):
            bar = self.comments.verticalScrollBar()
            at_bottom = bar.value() >= bar.maximum() - 4
            position = bar.value()
            self.comments.setHtml(markup)
            bar.setValue(bar.maximum() if at_bottom else position)
            self.comments_markup = markup

    def render_history(self):
        markup = history_html(self.history_entries, self.columns, self.catalog_data['users'])
        if markup != self.history_markup:
            scroll = self.history.verticalScrollBar().value()
            self.history.setHtml(markup)
            self.history.verticalScrollBar().setValue(scroll)
            self.history_markup = markup

    def load_catalogs(self):
        if self.catalog_pending:
            return
        self.catalog_pending = True
        if not self.catalog_ready:
            self.save_button.setEnabled(False)
        def done(data, error):
            self.catalog_pending = False
            if error:
                self.save_button.setEnabled(self.catalog_ready and not self.pending)
                self.status.setText('Справочники недоступны: ' + error_text(error) + '. Откройте справочник для повтора.')
                return
            self.catalog_ready = True
            self.save_button.setEnabled(not self.pending)
            self.catalog_data = data
            columns = data['columns']
            if columns != self.columns:
                selected, caption = self.column.currentData(), self.column.currentText()
                self.columns = columns
                self.column.blockSignals(True)
                self.column.clear()
                for column in columns:
                    self.column.addItem(column['name'], column['id'])
                if self.column.findData(selected) < 0:
                    self.column.addItem(caption + ' (удалена — выберите другую)', selected)
                    self.column.hide()
                self.column.setCurrentIndex(self.column.findData(selected))
                self.column.blockSignals(False)
            users = sorted(data['users'], key=lambda u: u['display_name'].casefold())
            self.assignees.set_options([(u['login'], u['display_name'] + ' · ' + u['login']) for u in users])
            self.labels.set_options([(x['name'], x['name']) for x in sorted(data['labels'], key=lambda l: l['name'].casefold())])
            self.render_history()
        def read():
            catalogs = self.store.catalogs(self.data['board_id'])
            catalogs['columns'] = self.store.columns(self.data['board_id'])
            return catalogs
        self.work(read, done)

    def open_catalog(self, kind):
        dialog = CatalogDialog(self.store, self.data['board_id'], kind, self)
        dialog.changed.connect(self.load_catalogs)
        dialog.exec_()
        self.load_catalogs()

    def refresh_extras(self):
        if self.extras_pending or self.pending:
            return
        self.load_catalogs()
        if not self.data.get('id'):
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
        self.save_button.setEnabled(not enabled and self.catalog_ready)
        self.comment_button.setEnabled(not enabled and bool(self.data.get('id')))
        self.file_button.setEnabled(not enabled and bool(self.data.get('id')))
        self.tabs.setEnabled(not enabled)
        self.rebase_button.setEnabled(not enabled)

    def save(self):
        if not self.catalog_ready:
            self.status.setText('Дождитесь загрузки справочников')
            return
        if self.checklist.has_draft():
            self.checklist.add_entry()
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
            self.changed.emit()
            if self.payload() == self.initial and not self.comment_text.toPlainText().strip() and not self.upload_queue and not self.comment_retry and not self.checklist.has_draft():
                self.close_saved_when_idle()
            else:
                self.status.setText('Задача сохранена. В карточке остались новые изменения или неотправленные данные.')
                self.refresh_extras()
        self.work(lambda: self.store.save_task(payload, rid), done)

    def close_saved_when_idle(self):
        if self.payload() != self.initial or self.comment_text.toPlainText().strip() or self.checklist.has_draft():
            return
        if self.jobs:
            QTimer.singleShot(50, self.close_saved_when_idle)
            return
        self.close()

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
        if self.column.currentData() not in {c['id'] for c in self.columns}:
            self.column.setCurrentIndex(self.column.findData(self.conflict_current['column_id']))
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
        dirty = self.checklist.has_draft() or self.payload() != self.initial or bool(self.comment_text.toPlainText().strip()) or bool(self.upload_queue) or bool(self.retry) or bool(self.comment_retry)
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
        self.removed = {}
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel('Порядок строк задаёт порядок колонок. Лимит 0 — без ограничения.'))
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(['Название', 'Завершение', 'Лимит'])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        layout.addWidget(self.table)
        for c in columns:
            self.add_row(c)
        actions = QHBoxLayout()
        button('Добавить', lambda: self.add_row({'name': 'Новая колонка', 'is_done': 0, 'wip_limit': 0}), actions)
        button('Выше', lambda: self.move(-1), actions)
        button('Ниже', lambda: self.move(1), actions)
        button('Удалить колонку…', self.remove_column, actions)
        layout.addLayout(actions)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText('Сохранить')
        buttons.button(QDialogButtonBox.Cancel).setText('Отмена')
        buttons.accepted.connect(self.validate_columns)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def validate_columns(self):
        try:
            values = self.values()
            if not values or any(not x['name'].strip() for x in values):
                raise ValueError('Укажите название каждой колонки.')
            if sum(x['is_done'] for x in values) > 1:
                raise ValueError('Отметьте только одну колонку завершения.')
        except ValueError as error:
            QMessageBox.warning(self, 'Проверьте колонки', str(error))
            return
        self.accept()

    def add_row(self, c):
        row = self.table.rowCount()
        self.table.insertRow(row)
        for i, value in enumerate([c['name'], 'да' if c['is_done'] else '', str(c.get('wip_limit') or 0)]):
            item = QTableWidgetItem(value)
            if i == 0:
                item.setData(Qt.UserRole, c.get('id'))
            elif i == 1:
                item.setText('Завершение')
                item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsUserCheckable)
                item.setCheckState(Qt.Checked if c['is_done'] else Qt.Unchecked)
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

    def remove_column(self):
        row = self.table.currentRow()
        if row < 0:
            QMessageBox.information(self, 'Выберите колонку', 'Сначала выберите строку колонки.')
            return
        if self.table.rowCount() <= 1:
            QMessageBox.information(self, 'Последняя колонка', 'На доске должна остаться хотя бы одна колонка.')
            return
        source = self.table.item(row, 0).data(Qt.UserRole)
        if source:
            candidates = [(self.table.item(i, 0).data(Qt.UserRole), self.table.item(i, 0).text())
                          for i in range(self.table.rowCount()) if i != row and self.table.item(i, 0).data(Qt.UserRole)]
            if not candidates:
                QMessageBox.information(self, 'Сначала сохраните колонки', 'Сохраните новую колонку, чтобы перенести в неё задачи.')
                return
            names = ['{} · №{}'.format(name, cid) for cid, name in candidates]
            choice, ok = QInputDialog.getItem(self, 'Удаление колонки',
                'Все задачи, включая архивные, будут перенесены. Выберите колонку:', names, 0, False)
            if not ok:
                return
            target = candidates[names.index(choice)][0]
            if QMessageBox.question(self, 'Удалить колонку?',
                    'Колонка «{}» будет удалена после сохранения. Задачи перейдут в «{}».'.format(
                        self.table.item(row, 0).text(), candidates[names.index(choice)][1]),
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                return
            self.removed[source] = target
            for old, dest in list(self.removed.items()):
                if dest == source:
                    self.removed[old] = target
        self.table.removeRow(row)

    def values(self):
        values = []
        for row in range(self.table.rowCount()):
            limit = int(self.table.item(row, 2).text())
            if limit < 0:
                raise ValueError('Лимит не может быть отрицательным')
            done = self.table.item(row, 1).checkState() == Qt.Checked
            values.append({'id': self.table.item(row, 0).data(Qt.UserRole), 'name': self.table.item(row, 0).text(), 'is_done': done, 'wip_limit': limit or None})
        return values


class Cards(QListWidget):
    moved = pyqtSignal(int, int, object)
    dragging = pyqtSignal(bool)
    def __init__(self, column_id):
        super().__init__()
        self.column_id = column_id
        self.setObjectName('Cards')
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setItemDelegate(CardDelegate(self))
        self.setMouseTracking(True)
        self.setDragDropMode(QListWidget.DragDrop)
        self.setDefaultDropAction(Qt.MoveAction)
        self.setSelectionMode(QListWidget.SingleSelection)
        self.setSpacing(4)
        self.setWordWrap(True)
        self.setMinimumWidth(220)
        self.setAccessibleName('Задачи колонки')
    def startDrag(self, supported_actions):
        if not self.selectedIndexes():
            return
        # Own QDrag lifecycle: QListWidget.startDrag may delete source rows after
        # MoveAction, although persistence and repaint are handled asynchronously.
        self.dragging.emit(True)
        try:
            drag = QDrag(self)
            drag.setMimeData(self.model().mimeData(self.selectedIndexes()))
            drag.exec_(Qt.MoveAction, Qt.MoveAction)
        finally:
            self.dragging.emit(False)

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
        self.drag_active = False
        self.refresh_deferred = False
        self.last_snapshot = None
        self.setWindowTitle('TeamSync')
        self.resize(1400, 850)
        self.setMinimumSize(900, 600)
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.sidebar = sidebar = QWidget()
        sidebar.setObjectName('side')
        sidebar.setAttribute(Qt.WA_StyledBackground, True)
        sidebar.setFixedWidth(240)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(16, 14, 16, 16)
        side.setSpacing(6)
        brand_row = QHBoxLayout()
        brand = QLabel('▦  TeamSync')
        brand.setObjectName('appName')
        brand_row.addWidget(brand, 1)
        self.collapse_button = button('‹', self.collapse_sidebar, brand_row, 'quiet')
        self.collapse_button.setToolTip('Свернуть навигацию')
        side.addLayout(brand_row)
        side.addSpacing(22)
        caption = QLabel('ПРОЕКТЫ И ДОСКИ')
        caption.setObjectName('Eyebrow')
        side.addWidget(caption)
        self.boards_list = QListWidget()
        self.boards_list.hide()
        self.boards_list.currentItemChanged.connect(self.select_board)
        self.board_tree = QTreeWidget()
        self.board_tree.setHeaderHidden(True)
        self.board_tree.setIndentation(14)
        self.board_tree.itemClicked.connect(self.select_tree_board)
        side.addWidget(self.board_tree, 1)
        button('+  Создать доску', lambda: self.board_action('create'), side, 'quiet')
        side.addSpacing(18)
        self.my_nav = button('☑  Мои задачи', lambda: self.pages.setCurrentWidget(self.my_tasks), side, 'quiet')
        self.my_nav.setCheckable(True)
        self.report_nav = button('▥  Отчёты', lambda: self.pages.setCurrentWidget(self.statistics), side, 'quiet')
        self.report_nav.setCheckable(True)
        button('♙  Сотрудники', lambda: self.open_catalog('users'), side, 'quiet')
        self.theme_button = button('Тёмная тема', self.toggle_theme, side, 'quiet')
        self.theme_button.setCheckable(True)
        self.theme_button.setChecked(theme_settings().value('dark', False, type=bool))
        self.theme_button.setText('Светлая тема' if self.theme_button.isChecked() else 'Тёмная тема')
        profile = QLabel('●  ' + getpass.getuser() + '\n     Локальное рабочее пространство')
        profile.setObjectName('Profile')
        profile.setTextFormat(Qt.PlainText)
        profile.setWordWrap(True)
        side.addSpacing(14)
        side.addWidget(profile)
        root.addWidget(sidebar)
        surface = QWidget()
        surface.setObjectName('content')
        workspace = QVBoxLayout(surface)
        workspace.setContentsMargins(0, 0, 0, 0)
        workspace.setSpacing(0)
        root.addWidget(surface, 1)
        header = QWidget()
        header.setObjectName('Header')
        header.setAttribute(Qt.WA_StyledBackground, True)
        header.setFixedHeight(56)
        self.header_layout = top = QHBoxLayout(header)
        top.setContentsMargins(20, 8, 16, 8)
        top.setSpacing(12)
        self.board_title = ElidedLabel()
        self.board_title.setText('Выберите доску')
        self.board_title.setTextFormat(Qt.PlainText)
        self.board_title.setObjectName('BoardName')
        self.board_title.setMinimumWidth(90)
        self.board_title.setMaximumWidth(220)
        self.board_title.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        top.addWidget(self.board_title, 1)
        self.view_switch = QComboBox()
        self.view_switch.setObjectName('ViewSwitch')
        self.view_switch.setAccessibleName('Вид рабочей области')
        self.search = QLineEdit()
        self.search.setPlaceholderText('Поиск задач…')
        self.search.setAccessibleName('Поиск задач')
        self.search.setClearButtonEnabled(True)
        self.search.setMaximumWidth(360)
        self._search_in_header = True
        top.addWidget(self.search, 2)
        self.view_switch.setFixedWidth(164)
        top.addWidget(self.view_switch)
        self.new_task_button = button('+ Добавить задачу', self.new_task, top, 'primary')
        self.new_task_button.setFixedWidth(176)
        settings_button = button('⋯', self.workspace_menu, top)
        settings_button.setFixedWidth(36)
        settings_button.setToolTip('Настройки доски и обслуживание')
        settings_button.setAccessibleName('Настройки доски')
        self.retry_button = button('Повторить', self.retry_mutation, top)
        self.retry_button.hide()
        workspace.addWidget(header)
        # Internal tab bar retains stable board IDs; tree is the visible navigation.
        self.board_tabs = QTabBar()
        self.board_tabs.hide()
        self.board_tabs.currentChanged.connect(self.select_board_tab)
        self.pages = QTabWidget()
        self.pages.tabBar().hide()
        board_page = QWidget()
        self.statistics = StatisticsPage()
        self.my_tasks = MyTasksPage()
        self.my_tasks.opened.connect(self.open_personal_task)
        self.list_view = BoardView()
        self.gantt_view = BoardView(timeline=True)
        for view in (self.list_view, self.gantt_view):
            view.opened.connect(self.open_task)
        for page, caption in [(board_page, 'Канбан-доска'), (self.list_view, 'Список'),
                              (self.gantt_view, 'Диаграмма Ганта'), (self.statistics, 'Сводка'),
                              (self.my_tasks, 'Мои задачи')]:
            self.pages.addTab(page, caption)
            self.view_switch.addItem(caption)
        self.view_switch.currentIndexChanged.connect(self.pages.setCurrentIndex)
        self.pages.currentChanged.connect(self.view_switch.setCurrentIndex)
        self.pages.currentChanged.connect(self.update_navigation)
        workspace.addWidget(self.pages, 1)
        content = QVBoxLayout(board_page)
        content.setContentsMargins(20, 16, 20, 12)
        content.setSpacing(14)
        self.filter_row = filters = QHBoxLayout()
        filters.setSpacing(8)
        self.assignee_filter = QComboBox()
        self.assignee_filter.addItem('Все исполнители', '')
        self.assignee_filter.setAccessibleName('Фильтр по исполнителю')
        self.label_filter = QComboBox()
        self.label_filter.addItem('Все стикеры', '')
        self.label_filter.setAccessibleName('Фильтр по стикеру')
        filters.addWidget(self.assignee_filter)
        filters.addWidget(self.label_filter)
        self.filter_toggle = button('Фильтры', lambda: self.advanced_filters.setVisible(not self.advanced_filters.isVisible()), filters)
        filters.addStretch()
        content.addLayout(filters)
        self.advanced_filters = QWidget()
        advanced_layout = QVBoxLayout(self.advanced_filters)
        advanced_layout.setContentsMargins(0, 0, 0, 0)
        advanced = QHBoxLayout()
        self.quick = QComboBox()
        self.quick.addItems(['Все задачи', 'Мои задачи', 'Просроченные', 'Без исполнителя'])
        advanced.addWidget(self.quick)
        self.priority_filter = PriorityCombo()
        self.priority_filter.insertItem(0, 'Все приоритеты')
        self.priority_filter.setCurrentIndex(0)
        advanced.addWidget(self.priority_filter)
        self.archive_filter = QCheckBox('Архив')
        advanced.addWidget(self.archive_filter)
        advanced.addStretch()
        advanced_layout.addLayout(advanced)
        deadlines = QHBoxLayout()
        self.due_from = DateField(caption='Срок с')
        self.due_to = DateField(caption='по')
        deadlines.addWidget(self.due_from)
        deadlines.addWidget(self.due_to)
        button('Сбросить', self.reset_filters, deadlines)
        advanced_layout.addLayout(deadlines)
        self.advanced_filters.hide()
        content.addWidget(self.advanced_filters)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        content.addWidget(self.scroll, 1)
        self.status = QuietStatus('Подключение…')
        self.status.setObjectName('Status')
        self.status.setTextFormat(Qt.PlainText)
        self.status.setWordWrap(True)
        workspace.addWidget(self.status)
        self.debounce = QTimer(self)
        self.debounce.setSingleShot(True)
        self.debounce.timeout.connect(self.refresh)
        self.search.textChanged.connect(self.filters_changed)
        self.assignee_filter.currentIndexChanged.connect(self.filters_changed)
        self.label_filter.currentIndexChanged.connect(self.filters_changed)
        self.due_from.changed.connect(self.filters_changed)
        self.due_to.changed.connect(self.filters_changed)
        self.quick.currentIndexChanged.connect(self.filters_changed)
        self.priority_filter.currentIndexChanged.connect(self.filters_changed)
        self.archive_filter.toggled.connect(self.filters_changed)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(5000)
        self.card_lists = []
        self.connect_store()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not hasattr(self, 'search'):
            return
        wide = self.width() >= 1200
        if wide != getattr(self, '_search_in_header', False):
            self._search_in_header = wide
            if wide:
                self.filter_row.removeWidget(self.search)
                self.search.setMaximumWidth(460)
                self.header_layout.insertWidget(1, self.search, 2)
            else:
                self.header_layout.removeWidget(self.search)
                self.search.setMaximumWidth(16777215)
                self.filter_row.insertWidget(0, self.search, 2)

    def update_navigation(self, index=None):
        self.my_nav.setChecked(self.pages.currentWidget() is self.my_tasks)
        self.report_nav.setChecked(self.pages.currentWidget() is self.statistics)
        if self.pages.currentWidget() is self.my_tasks:
            self.board_title.setText('Мои задачи')
        else:
            current = self.boards_list.currentItem()
            self.board_title.setText(current.text() if current else 'Выберите доску')

    def workspace_menu(self):
        menu = QMenu(self)
        menu.addAction('Колонки доски', self.edit_columns)
        menu.addAction('Стикеры доски', lambda: self.open_catalog('labels'))
        menu.addSeparator()
        menu.addAction('Переименовать доску', lambda: self.board_action('rename'))
        menu.addAction('Архивировать доску', lambda: self.board_action('archive'))
        menu.addSeparator()
        menu.addAction('Обновить', self.refresh)
        menu.addAction('Резервная копия', self.backup)
        menu.exec_(self.mapToGlobal(self.rect().topRight()) - QPoint(240, -56))

    def board_settings_menu(self):
        menu = QMenu(self)
        menu.addAction('Создать доску', lambda: self.board_action('create'))
        menu.addAction('Переименовать доску', lambda: self.board_action('rename'))
        menu.addAction('Архивировать доску', lambda: self.board_action('archive'))
        menu.exec_(self.sidebar.mapToGlobal(self.sidebar.rect().center()))

    def collapse_sidebar(self):
        collapsed = self.sidebar.width() > 100
        self.sidebar.setFixedWidth(64 if collapsed else 240)
        self.board_tree.setVisible(not collapsed)
        for label in self.sidebar.findChildren(QLabel):
            label.setVisible(not collapsed)
        for b in self.sidebar.findChildren(QPushButton):
            if b is self.collapse_button:
                b.setText('›' if collapsed else '‹')
                continue
            if collapsed:
                b.setAccessibleName(b.text())
                b.setProperty('fullText', b.text())
                b.setToolTip(b.text())
                b.setText(b.text()[:1])
            else:
                b.setText(b.property('fullText') or b.text())

    def select_tree_board(self, item, column):
        board_id = item.data(0, Qt.UserRole)
        if board_id is not None:
            self.board_tabs.setCurrentIndex(next((i for i in range(self.board_tabs.count()) if self.board_tabs.tabData(i) == board_id), -1))

    def toggle_theme(self):
        dark = self.theme_button.isChecked()
        theme_settings().setValue('dark', dark)
        apply_theme(QApplication.instance(), dark)
        caption = 'Светлая тема' if dark else 'Тёмная тема'
        if self.sidebar.width() < 100:
            self.theme_button.setProperty('fullText', caption)
            self.theme_button.setToolTip(caption)
            self.theme_button.setText(caption[:1])
        else:
            self.theme_button.setText(caption)

    def reset_filters(self):
        self.search.clear()
        for combo in (self.quick, self.priority_filter, self.assignee_filter, self.label_filter):
            combo.setCurrentIndex(0)
        self.archive_filter.setChecked(False)
        self.due_from.set_value(None)
        self.due_to.set_value(None)
        self.filters_changed()

    def new_task_in_column(self, column_id):
        if not self.store or not self.board_id:
            return
        self.open_task({'board_id': self.board_id, 'column_id': column_id, 'title': ''})

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
        count = sum(bool(v) for v in (self.assignee_filter.currentData(), self.label_filter.currentData(), self.due_from.value(), self.due_to.value()))
        self.filter_toggle.setText('Фильтры ({}) ▾'.format(count) if count else 'Фильтры ▾')
        self.generation += 1
        self.debounce.start(300)

    def select_board_tab(self, index):
        if index < 0:
            return
        self.board_id = self.board_tabs.tabData(index)
        self.generation += 1
        self.pages.setCurrentIndex(0)
        self.refresh()

    def open_personal_task(self, task):
        def done(columns, error):
            if error:
                self.status.setText(error_text(error))
                return
            d = TaskDialog(self.store, columns, task, self)
            self.dialogs.add(d)
            d.changed.connect(self.refresh)
            d.destroyed.connect(lambda: self.dialogs.discard(d))
            d.show()
        self.work(lambda: self.store.columns(task['board_id']), done)

    def select_board(self, current, previous):
        self.board_id = current.data(Qt.UserRole) if current else None
        self.generation += 1
        self.refresh()

    def filters(self):
        quick = self.quick.currentIndex()
        return {'query': self.search.text(), 'priority': self.priority_filter.currentText() if self.priority_filter.currentIndex() else '', 'archived': self.archive_filter.isChecked(), 'assignee': getpass.getuser() if quick == 1 else (self.assignee_filter.currentData() or ''), 'overdue': quick == 2, 'unassigned': quick == 3, 'label': self.label_filter.currentData() or '', 'due_from': self.due_from.text().strip(), 'due_to': self.due_to.text().strip()}

    def drag_state_changed(self, active):
        self.drag_active = active
        if not active and self.refresh_deferred:
            self.refresh_deferred = False
            # Run after startDrag/dropEvent have returned to Qt.
            self.debounce.start(0)

    def refresh(self):
        if self.drag_active:
            self.refresh_deferred = True
            return
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
            personal = []
            for board in boards:
                columns = {c['id']: c['name'] for c in self.store.columns(board['id'])}
                for task in self.store.tasks(board['id'], {'assignee': self.store.login}):
                    task.update(board_name=board['name'], column_name=columns.get(task['column_id'], ''))
                    personal.append(task)
            return boards, selected, self.store.columns(selected) if selected else [], self.store.tasks(selected, f) if selected else [], self.store.tasks(selected) if selected else [], self.store.catalogs(selected), personal
        def done(result, error):
            self.loading = False
            if self.drag_active:
                self.refresh_deferred = True
                return
            if self.mutating or self.pending_action:
                return
            if generation != self.generation:
                self.debounce.start(1)
                return
            if error:
                self.status.setText('Обновление не выполнено: ' + error_text(error) + '. Можно повторить. Открытые формы сохранены.')
                return
            snapshot = (result, dt.date.today().isoformat())
            if snapshot == self.last_snapshot:
                return
            self.last_snapshot = copy.deepcopy(snapshot)
            boards, selected, columns, tasks, all_tasks, catalogs, personal = result
            self.my_tasks.update_data(personal)
            self.list_view.update_data(tasks, columns)
            self.gantt_view.update_data(tasks, columns)
            structure = [(b['id'], b['name']) for b in boards]
            if structure != getattr(self, 'tree_structure', None):
                self.tree_structure = structure
                self.board_tree.clear()
                groups = {}
                for board_id, name in structure:
                    project, separator, title = name.partition(' / ')
                    group_name = project if separator else 'Рабочие доски'
                    if group_name not in groups:
                        groups[group_name] = QTreeWidgetItem(self.board_tree, [group_name])
                        groups[group_name].setExpanded(True)
                    item = QTreeWidgetItem(groups[group_name], [title if separator else name])
                    item.setData(0, Qt.UserRole, board_id)
            wanted = [(b['id'], b['name']) for b in boards]
            current = [(self.board_tabs.tabData(i), self.board_tabs.tabText(i)) for i in range(self.board_tabs.count())]
            self.board_tabs.blockSignals(True)
            if wanted != current:
                while self.board_tabs.count():
                    self.board_tabs.removeTab(0)
                for board_id, name in wanted:
                    index = self.board_tabs.addTab(name)
                    self.board_tabs.setTabData(index, board_id)
            self.board_tabs.setCurrentIndex(next((i for i in range(self.board_tabs.count()) if self.board_tabs.tabData(i) == selected), -1))
            self.board_tabs.blockSignals(False)
            self.update_catalog_filters(catalogs)
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
                    self.board_title.setToolTip(b['name'])
            self.boards_list.blockSignals(False)
            if not selected:
                self.board_title.setText('Создайте первую доску')
            for group_index in range(self.board_tree.topLevelItemCount()):
                group = self.board_tree.topLevelItem(group_index)
                for child_index in range(group.childCount()):
                    child = group.child(child_index)
                    if child.data(0, Qt.UserRole) == selected:
                        self.board_tree.setCurrentItem(child)
            self.update_navigation()
            self.columns_data = columns
            self.statistics.update_data(all_tasks, columns)
            selected_task = self.selected_task()
            horizontal_position = self.scroll.horizontalScrollBar().value()
            vertical_positions = {c.column_id: c.verticalScrollBar().value() for c in self.card_lists}
            focused = any(x.hasFocus() for x in self.card_lists)
            board = QWidget()
            board.setObjectName('BoardCanvas')
            row = QHBoxLayout(board)
            row.setSizeConstraint(QHBoxLayout.SetMinimumSize)
            row.setContentsMargins(4, 6, 4, 6)
            row.setSpacing(8)
            self.card_lists = []
            for column_index, column in enumerate(columns):
                panel = QWidget()
                panel.setObjectName('ColumnPanel')
                panel.setProperty('tone', column_index % 5)
                panel.setAttribute(Qt.WA_StyledBackground, True)
                panel.setFixedWidth(316)
                layout = QVBoxLayout(panel)
                layout.setContentsMargins(6, 4, 6, 6)
                layout.setSpacing(10)
                count = sum(t['column_id'] == column['id'] for t in all_tasks)
                warning = bool(column['wip_limit'] and count > column['wip_limit'])
                label = QLabel(column['name'] + ' · ' + str(count) + (' / ' + str(column['wip_limit']) if column['wip_limit'] else '') + (' · Лимит превышен' if warning else ''))
                label.setTextFormat(Qt.PlainText)
                label.setWordWrap(True)
                label.setObjectName('ColumnTitle')
                label.setProperty('tone', column_index % 5)
                header_row = QHBoxLayout()
                label.setText(column['name'])
                header_row.addWidget(label, 1)
                counter = QLabel(str(count) + (' / ' + str(column['wip_limit']) if column['wip_limit'] else '') + (' !' if warning else ''))
                counter.setToolTip('Лимит превышен' if warning else 'Количество задач')
                counter.setObjectName('CountBadge')
                header_row.addWidget(counter)
                column_menu = button('⋯', self.edit_columns, header_row)
                column_menu.setFixedWidth(24)
                column_menu.setToolTip('Настроить колонки')
                layout.addLayout(header_row)
                cards = Cards(column['id'])
                cards.itemClicked.connect(lambda item: self.open_task(item.data(Qt.UserRole)))
                cards.itemSelectionChanged.connect(lambda cards=cards: self.exclusive_selection(cards))
                cards.moved.connect(self.move_task)
                cards.dragging.connect(self.drag_state_changed)
                cards.setContextMenuPolicy(Qt.CustomContextMenu)
                cards.customContextMenuRequested.connect(lambda point, cards=cards: self.card_menu(cards, point))
                for task in tasks:
                    if task['column_id'] != column['id']:
                        continue
                    overdue = task['due_date'] and task['due_date'] < dt.date.today().isoformat() and not task['completed_at']
                    check = task['checklist']
                    text = '№{}  {}\n{}\n{}{} · {}\n{}{}'.format(task['number'], task['title'], ', '.join(task.get('assignee_names') or task['assignees']) or 'Без исполнителя', 'Просрочено: ' if overdue else 'Срок: ', task['due_date'] or 'не задан', task['priority'], ', '.join(task['labels']), '\nЧек-лист: {}/{}'.format(sum(bool(x['done']) for x in check), len(check)) if check else '')
                    item = QListWidgetItem(text)
                    item.setData(Qt.UserRole, task)
                    item.setToolTip(text)
                    cards.addItem(item)
                    if selected_task and selected_task['id'] == task['id']:
                        cards.setCurrentItem(item)
                        if focused:
                            QTimer.singleShot(0, cards.setFocus)
                cards.setFixedHeight(min(550, max(60, sum(148 if cards.item(i).data(Qt.UserRole).get('checklist') else 132 for i in range(cards.count())) + cards.count()*8 + 8)))
                layout.addWidget(cards)
                button('+ Добавить задачу', lambda checked=False, cid=column['id']: self.new_task_in_column(cid), layout)
                row.addWidget(panel, 0, Qt.AlignTop)
                self.card_lists.append(cards)
                cards.verticalScrollBar().setValue(vertical_positions.get(column['id'], 0))
            if not columns:
                row.addWidget(QLabel('Создайте доску, чтобы добавить задачи.'))
            old = self.scroll.takeWidget()
            if old:
                old.deleteLater()
            self.scroll.setWidget(board)
            self.scroll.horizontalScrollBar().setValue(horizontal_position)
            self.status.clear()
        self.work(read, done)

    def update_catalog_filters(self, catalogs):
        for combo, options, placeholder in [
            (self.assignee_filter, [(u['login'], u['display_name']) for u in sorted(catalogs['users'], key=lambda u: u['display_name'].casefold())], 'Все исполнители'),
            (self.label_filter, [(l['name'], l['name']) for l in sorted(catalogs['labels'], key=lambda l: l['name'].casefold())], 'Все метки')
        ]:
            selected = combo.currentData()
            combo.blockSignals(True)
            combo.clear()
            combo.addItem(placeholder, '')
            for value, label in options:
                combo.addItem(label_icon(value), label, value) if combo is self.label_filter else combo.addItem(label, value)
            combo.setCurrentIndex(max(0, combo.findData(selected)))
            combo.blockSignals(False)

    def open_catalog(self, kind):
        if not self.store or (kind == 'labels' and not self.board_id):
            self.status.setText('Сначала выберите доску и подключитесь к данным')
            return
        dialog = CatalogDialog(self.store, self.board_id, kind, self)
        dialog.changed.connect(self.refresh)
        dialog.exec_()
        self.refresh()

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
        self.generation += 1
        self.mutating = True
        self.status.setText('Сохранение…')
        def done(result, error):
            self.mutating = False
            self.retry_button.setVisible(bool(error) and not isinstance(error, (Conflict, ValueError)))
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
            name, ok = QInputDialog.getText(self, 'Доска', 'Название (можно Проект / Доска):', text=item.text() if action == 'rename' else '')
            if ok:
                self.mutate(lambda rid: self.store.board_action(name, bid if action == 'rename' else None, request_id=rid, column_names=['Бэклог', 'В работе', 'На проверке', 'Готово'] if action == 'create' else None))

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
            self.mutate(lambda rid: self.store.save_columns(bid, values, rid, removed=d.removed))

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

    def card_menu(self, cards, point):
        item = cards.itemAt(point)
        if not item:
            return
        cards.setCurrentItem(item)
        menu = QMenu(self)
        menu.addAction('Переместить в…', self.move_selected)
        menu.addAction('Восстановить' if item.data(Qt.UserRole)['archived'] else 'Архивировать', self.archive_selected)
        self.drag_state_changed(True)
        try:
            menu.exec_(cards.viewport().mapToGlobal(point))
        finally:
            self.drag_state_changed(False)

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
