import copy
import datetime as dt
import getpass
import hashlib
import json
from pathlib import Path
import tempfile
import uuid
from PyQt5.QtCore import Qt, QTimer, QObject, QRunnable, QThreadPool, pyqtSignal, QUrl, QPoint, QSize, QEvent, QTimeZone, QSignalBlocker, QSaveFile, QIODevice, QStandardPaths
from PyQt5.QtGui import QDesktopServices, QDrag, QIcon, QPixmap
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QListWidget, QListWidgetItem, QPushButton, QLabel, QLineEdit, QTextEdit, QTextBrowser, QPlainTextEdit, QComboBox,
    QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QMessageBox, QInputDialog,
    QScrollArea, QFileDialog, QTabWidget, QTableWidget, QTableWidgetItem, QHeaderView, QMenu, QTabBar, QSplitter, QTreeWidget, QTreeWidgetItem, QSizePolicy)
from .store import Store, Conflict, AccessRestricted, DatabaseTooNew
from . import bugzilla, installation, redmine
from .widgets import RegistrySelection, DateField, ChecklistEditor, PriorityCombo, PrioritySelector, MessageEdit
from .presentation import history_html, date_text, comments_html
from .cards import Cards, EXPANDED_ROLE, DETAILS_ROLE, set_card_notice, update_cards
from .identification import IdentificationPage
from .bugzilla_page import BugzillaPage
from .statistics import StatisticsPage
from .my_tasks import MyTasksPage
from .board_views import BoardView
from .messenger import Messenger
from .people import open_employee_profile
from .notifications import Notifications
from .board_badges import BoardBadgeDelegate
from .avatars import avatar_image, avatar_resources
from .spellcheck import SpellTextEdit, SpellLineEdit, spell_engine
from .stickers import MAX_SOURCE
from .sticker_pack import StickerPackDialog
from .theme import apply_theme, theme_settings, symbol_icon


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
        self.can_manage = False
        self.retry = None
        self.delete_retry = None
        self.setWindowTitle('Сотрудники' if kind == 'users' else 'Метки доски')
        self.resize(540, 540)
        root = QVBoxLayout(self)
        heading = QLabel(self.windowTitle())
        heading.setObjectName('Heading')
        root.addWidget(heading)
        self.list = QListWidget()
        self.edit_intro = QLabel('Отметка «Ограничить доступ» блокирует вход в TeamSync. Снимите отметку, чтобы восстановить доступ.' if kind == 'users' else 'Метки доступны всем задачам этой доски.')
        self.edit_intro.setObjectName('Muted')
        self.edit_intro.setWordWrap(True)
        root.addWidget(self.edit_intro)
        if kind == 'users':
            self.list.setIconSize(QSize(36, 36))
            self.list.itemChanged.connect(self.change_restriction)
        root.addWidget(self.list, 1)
        self.edit_form = QWidget()
        form = QFormLayout(self.edit_form)
        form.setContentsMargins(0, 0, 0, 0)
        form.setRowWrapPolicy(QFormLayout.WrapAllRows)
        self.login = QLineEdit()
        self.login.setPlaceholderText('Например, ivanov')
        self.name = SpellLineEdit()
        self.name.setPlaceholderText('Иванов Иван' if kind == 'users' else 'Например, Документы')
        if kind == 'users':
            form.addRow('Пользователь Windows', self.login)
        form.addRow('ФИО сотрудника' if kind == 'users' else 'Название метки', self.name)
        style_form(form)
        root.addWidget(self.edit_form)
        actions = QHBoxLayout()
        self.save_button = button('Добавить', self.save_entry, actions, 'primary')
        self.edit_button = button('Изменить имя' if kind == 'users' else 'Переименовать', self.edit_entry, actions)
        if kind == 'labels':
            self.delete_button = button('Удалить', self.delete_entry, actions, 'danger')
        button('Закрыть', self.reject, actions)
        root.addLayout(actions)
        self.status = QLabel('Загрузка справочника…')
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.PlainText)
        root.addWidget(self.status)
        if kind == 'users':
            for control in (self.edit_intro, self.edit_form, self.save_button, self.edit_button):
                control.hide()
        self.load()

    def load(self):
        self.save_button.setEnabled(False)
        def done(data, error):
            if error:
                self.status.setText(error_text(error) + '. Закройте и откройте справочник для повтора.')
                return
            allowed = self.can_manage = data.get('permissions', {}).get('manage', False)
            if self.kind == 'users':
                for control in (self.edit_intro, self.edit_form, self.save_button, self.edit_button):
                    control.setVisible(allowed)
            self.save_button.setEnabled(allowed)
            self.edit_button.setEnabled(allowed)
            if self.kind == 'labels':
                self.delete_button.setEnabled(allowed)
            self.list.clear()
            rows = sorted(data[self.kind], key=lambda x: (x.get('display_name') or x.get('name')).casefold())
            for row in rows:
                title = row['name'] if self.kind == 'labels' else row['display_name']
                if self.kind == 'users' and allowed:
                    title += ' · ' + row['login']
                item = QListWidgetItem(title)
                item.setData(Qt.UserRole, row)
                if self.kind == 'users':
                    item.setSizeHint(QSize(0, 44))
                    if row['avatar']:
                        item.setIcon(QIcon(QPixmap.fromImage(avatar_image(row['avatar']))))
                    if not allowed:
                        item.setFlags(item.flags() & ~Qt.ItemIsUserCheckable)
                        if row['restricted']:
                            item.setToolTip('Доступ к TeamSync ограничен')
                            item.setData(Qt.AccessibleDescriptionRole, 'Доступ к TeamSync ограничен')
                    elif row['role'] == 'admin':
                        item.setFlags(item.flags() & ~Qt.ItemIsUserCheckable)
                        item.setToolTip('Доступ действующего администратора нельзя ограничить')
                    else:
                        item.setCheckState(Qt.Checked if row['restricted'] else Qt.Unchecked)
                        item.setData(Qt.AccessibleDescriptionRole, 'Ограничить доступ к TeamSync')
                self.list.addItem(item)
            self.status.setText('Записей: {}'.format(len(rows)))
        self.work(self.store.employee_directory if self.kind == 'users' else lambda: self.store.catalogs(self.board_id),
                  done)

    def change_restriction(self, item):
        if not self.can_manage:
            return
        row = item.data(Qt.UserRole)
        restricted = item.checkState() == Qt.Checked
        if row['role'] == 'admin' or restricted == bool(row['restricted']):
            return

        def apply_check(value):
            blocker = QSignalBlocker(self.list)
            item.setCheckState(Qt.Checked if value else Qt.Unchecked)
            item.setData(Qt.UserRole, dict(row, restricted=value))
            del blocker

        if self.jobs or self.retry:
            apply_check(row['restricted'])
            return
        if restricted and QMessageBox.question(
                self, 'Ограничить доступ', 'Ограничить доступ к TeamSync для «{}»?'.format(row['display_name']),
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            apply_check(row['restricted'])
            return
        self.list.setEnabled(False)
        self.save_button.setEnabled(False)
        self.edit_button.setEnabled(False)
        self.status.setText('Сохранение…')

        def done(result, error):
            self.list.setEnabled(True)
            self.save_button.setEnabled(True)
            self.edit_button.setEnabled(True)
            if error:
                apply_check(row['restricted'])
                self.status.setText(error_text(error))
                return
            apply_check(result)
            self.status.setText('Доступ ограничен' if result else 'Доступ восстановлен')
            self.changed.emit()

        self.work(lambda: self.store.set_user_restricted(row['id'], restricted), done)

    def edit_entry(self):
        if not self.can_manage or self.jobs or self.retry or self.delete_retry or not self.list.currentItem():
            return
        row = self.list.currentItem().data(Qt.UserRole)
        self.edit_id = row['id']
        self.edit_name = row.get('name')
        if self.kind == 'users':
            self.login.setText(row['login'])
            self.login.setReadOnly(True)
        self.name.setText(row.get('display_name') or row['name'])
        self.save_button.setText('Сохранить')
        self.name.setFocus()

    def save_entry(self):
        if not self.can_manage or self.jobs or self.delete_retry:
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
            if user_id is not None:
                return self.store.edit_label(self.board_id, user_id, name, rid, self.edit_name)
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

    def delete_entry(self):
        if self.jobs or self.retry or (not self.delete_retry and not self.list.currentItem()):
            return
        row = self.delete_retry[1] if self.delete_retry else self.list.currentItem().data(Qt.UserRole)
        text = 'Удалить метку «{}»? Она будет снята со всех задач этой доски ({}). История задач сохранится.'.format(row['name'], row.get('task_count', 0))
        if not self.delete_retry and QMessageBox.question(self, 'Удалить метку', text, QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        if not self.delete_retry:
            self.delete_retry = (uuid.uuid4().hex, row)
        rid = self.delete_retry[0]
        self.save_button.setEnabled(False)
        self.name.setEnabled(False)
        def done(result, error):
            self.save_button.setEnabled(True)
            self.name.setEnabled(True)
            if error:
                if isinstance(error, ValueError):
                    self.delete_retry = None
                self.status.setText(error_text(error) + '. Повторите удаление: повтор безопасен.')
                return
            self.delete_retry = None
            self.edit_id = None
            self.name.clear()
            self.save_button.setText('Добавить')
            self.changed.emit()
            self.load()
        self.work(lambda: self.store.edit_label(self.board_id, row['id'], None, rid, row['name']), done)

    def reject(self):
        if self.jobs:
            self.status.setText('Дождитесь завершения операции')
            return
        if (self.name.text().strip() or self.login.text().strip() or self.retry or self.delete_retry) and QMessageBox.question(self, 'Несохранённые данные', 'Закрыть справочник без сохранения введённых данных?', QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        super().reject()

    def closeEvent(self, event):
        if self.jobs:
            event.ignore()
        else:
            event.ignore()
            self.reject()


class AccessDialog(QDialog, Async):
    """Shared native list/form workflow for roles and board membership."""
    changed = pyqtSignal()
    ROLE_NAMES = {'admin': 'Администратор', 'manager': 'Менеджер', 'employee': 'Сотрудник'}

    def __init__(self, store, board_id=None, parent=None):
        super().__init__(parent)
        self.init_async()
        self.store, self.board_id = store, board_id
        self.setWindowTitle('Участники доски' if board_id else 'Роли сотрудников')
        self.resize(560, 520)
        root = QVBoxLayout(self)
        hint = QLabel('Отметьте сотрудников, которым доступна вся доска.' if board_id else
                      'Администратор один. Назначение нового передаст ему все права; прежний станет менеджером.')
        hint.setWordWrap(True)
        root.addWidget(hint)
        self.list = QListWidget()
        root.addWidget(self.list, 1)
        self.choice = QComboBox()
        form = QFormLayout()
        form.addRow('Владелец доски' if board_id else 'Роль сотрудника', self.choice)
        root.addLayout(form)
        self.list.currentItemChanged.connect(self.select_user)
        actions = QHBoxLayout()
        self.save_button = button('Сохранить доступ' if board_id else 'Назначить роль', self.save, actions, 'primary')
        button('Закрыть', self.reject, actions)
        root.addLayout(actions)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.PlainText)
        root.addWidget(self.status)
        self.load()

    def select_user(self, item, previous):
        if item and self.board_id is None:
            self.choice.setCurrentIndex(self.choice.findData(item.data(Qt.UserRole)['role']))

    def load(self):
        self.save_button.setEnabled(False)
        self.status.setText('Загрузка…')
        def done(data, error):
            if error:
                self.status.setText(error_text(error) + '. Закройте и откройте окно для повтора.')
                return
            self.list.clear()
            self.choice.clear()
            if self.board_id is None:
                for role, title in self.ROLE_NAMES.items():
                    self.choice.addItem(title, role)
                rows = data
            else:
                rows = data['users']
                self.choice.setEnabled(data['can_transfer'])
            for row in rows:
                item = QListWidgetItem(row['display_name'] + ' · ' + row['login'] + ' · ' + self.ROLE_NAMES[row['role']])
                item.setData(Qt.UserRole, row)
                if self.board_id:
                    item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                    item.setCheckState(Qt.Checked if row['member'] or row['id'] == data['owner_id'] or row['role'] == 'admin' else Qt.Unchecked)
                    if row['id'] == data['owner_id'] or row['role'] == 'admin':
                        item.setFlags(item.flags() & ~Qt.ItemIsUserCheckable)
                    if row['role'] in ('manager', 'admin'):
                        self.choice.addItem(row['display_name'] + ' · ' + row['login'], row['id'])
                self.list.addItem(item)
            if self.board_id:
                self.choice.setCurrentIndex(self.choice.findData(data['owner_id']))
            elif self.list.count():
                self.list.setCurrentRow(0)
            self.save_button.setEnabled(True)
            self.status.setText('Пользователей: ' + str(len(rows)))
        self.work(lambda: self.store.board_access(self.board_id) if self.board_id else self.store.access_users(), done)

    def save(self):
        if self.jobs or (not self.board_id and not self.list.currentItem()):
            return
        if self.board_id:
            members = [self.list.item(i).data(Qt.UserRole)['id'] for i in range(self.list.count()) if self.list.item(i).checkState() == Qt.Checked]
            owner = self.choice.currentData()
            action = lambda: self.store.set_board_access(self.board_id, members, owner)
            text = 'Сохранить выбранный доступ к доске? Снятые отметки отзовут доступ сотрудников.'
        else:
            row = self.list.currentItem().data(Qt.UserRole)
            role = self.choice.currentData()
            action = lambda: self.store.set_role(row['id'], role)
            text = 'Назначить сотруднику «{}» роль «{}»?'.format(row['display_name'], self.ROLE_NAMES[role])
        if QMessageBox.question(self, self.windowTitle(), text, QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        self.save_button.setEnabled(False)
        self.status.setText('Сохранение…')
        def done(result, error):
            self.save_button.setEnabled(True)
            if error:
                self.status.setText(error_text(error))
                return
            self.changed.emit()
            self.load()
        self.work(action, done)

    def reject(self):
        if self.jobs:
            self.status.setText('Дождитесь завершения операции')
            return
        super().reject()


class TaskDialog(QDialog, Async):
    changed = pyqtSignal()
    def __init__(self, store, columns, data, parent=None):
        super().__init__(parent)
        self.init_async()
        self.store, self.columns = store, columns
        self.data = copy.deepcopy(data)
        self.notice_pending = False
        self.notice_snapshot = data.get("notification_cursor", 0)
        self.notice_read = data.get("notification_read", 0)
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
        root.setSizeConstraint(QVBoxLayout.SetMinimumSize)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(12)
        heading = QLabel('Задача №{}'.format(data['number']) if data.get('id') else 'Новая задача')
        heading.setObjectName('Heading')
        root.addWidget(heading)
        self.tabs = QTabWidget()
        self.tabs.tabBar().setExpanding(False)
        self.tabs.tabBar().setElideMode(Qt.ElideRight)
        self.tabs.tabBar().setUsesScrollButtons(True)
        self.task_splitter = QSplitter(Qt.Horizontal)
        root.addWidget(self.task_splitter, 1)
        self.task_splitter.addWidget(self.tabs)
        page = QWidget()
        page.setObjectName('scrollBody')
        form_columns = QHBoxLayout(page)
        form = QFormLayout()
        properties = QFormLayout()
        properties.setRowWrapPolicy(QFormLayout.WrapAllRows)
        properties.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        form_columns.addLayout(form, 3)
        form_columns.addLayout(properties, 2)
        form_columns.setContentsMargins(12, 20, 12, 16)
        form_columns.setSpacing(20)
        form.setContentsMargins(0, 0, 0, 0)
        properties.setContentsMargins(0, 0, 0, 0)
        form.setFormAlignment(Qt.AlignTop)
        properties.setFormAlignment(Qt.AlignTop)
        form.setVerticalSpacing(8)
        form.setRowWrapPolicy(QFormLayout.WrapAllRows)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        self.title = SpellLineEdit(data.get('title', ''))
        self.title.setPlaceholderText('Что нужно сделать?')
        self.description = SpellTextEdit()
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
        self.priority = PrioritySelector()
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
        properties.addRow('Исполнители', self.assignees)
        properties.addRow('Метки', self.labels)
        properties.addRow('Приоритет', self.priority)
        properties.addRow('Срок', self.due)
        self.column.hide()
        if data.get('id'):
            properties.addRow('Архивирование', self.archived)
        self.checklist = ChecklistEditor(data.get('checklist', []))
        form.addRow('Чек-лист', self.checklist)
        self.checklist.list.setMinimumHeight(64)
        self.checklist.list.setMaximumHeight(100)
        dates = QLabel('Создано: {}\nИзменено: {}\nЗавершено: {}'.format(data.get('created_at', '—'), data.get('updated_at', '—'), data.get('completed_at') or '—'))
        dates.setTextFormat(Qt.PlainText)
        self.dates = dates
        if data.get('id'):
            dates.setObjectName('Muted')
            dates.setToolTip('Время событий в UTC')
        style_form(form)
        style_form(properties)
        self.task_page = page
        self.tabs.addTab(page, 'Задача')
        comments_page = QWidget()
        comments_layout = QVBoxLayout(comments_page)
        chat_heading = QLabel('Обсуждение')
        chat_heading.setObjectName('ChatHeading')
        comments_layout.addWidget(chat_heading)
        self.comments = QTextBrowser()
        self.comments.setOpenLinks(False)
        self.comments.setOpenExternalLinks(False)
        self.comments.anchorClicked.connect(
            lambda url: open_employee_profile(url, self, self.store, self.work, self.status))
        self.comments.setReadOnly(True)
        self.comments.setObjectName('ChatTimeline')
        comments_layout.addWidget(self.comments)
        self.comment_text = MessageEdit()
        self.comment_text.setAcceptRichText(False)
        self.comment_text.setMaximumHeight(120)
        self.comment_text.setPlaceholderText('Написать сообщение…')
        self.comment_text.setMaximumHeight(88)
        comments_layout.addWidget(self.comment_text)
        self.comment_button = button('Отправить сообщение', self.add_comment, comments_layout, 'primary')
        self.comment_text.sendRequested.connect(self.comment_button.click)
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
        self.subscribed = bool(data.get('subscribed'))
        self.subscribe_button = button('Подписаться', self.toggle_subscription, actions)
        self.subscribe_button.setVisible(bool(data.get('id')))
        self.update_subscription_label()
        button('Закрыть', self.close, actions).setFixedWidth(100)
        root.addLayout(actions)
        self.initial_scroll_timer = QTimer(self)
        self.initial_scroll_timer.setSingleShot(True)
        self.initial_scroll_timer.timeout.connect(self.scroll_chat_to_end)
        self.comments.verticalScrollBar().actionTriggered.connect(self.initial_scroll_timer.stop)
        self.chat_initial_scroll = True
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
        self.notice_snapshot = max(self.notice_snapshot, d.get('notification_cursor', 0))
        QTimer.singleShot(0, self.acknowledge_task_notifications)
        self.timestamps = [d.get(key) for key in ('created_at', 'updated_at', 'completed_at')]
        self.render_dates()
        self.history_entries = d.get('history', [])
        self.comment_entries = d.get('comments', [])
        self.avatars = d.get('avatars', {})
        assignee_avatars = {login: self.avatars[login] for login in d.get('assignees', []) if login in self.avatars}
        if assignee_avatars != getattr(self, 'assignee_avatars', None):
            self.assignee_avatars = assignee_avatars
            self.assignees.set_icons({login: QIcon(QPixmap.fromImage(avatar_image(image)))
                                      for login, image in assignee_avatars.items()})
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
        self.subscribe_button.setVisible(bool(d.get('id')))
        if not self.pending:
            self.subscribed = bool(d.get('subscribed'))
            self.update_subscription_label()
        for b in (self.comment_button, self.file_button, self.subscribe_button):
            b.setEnabled(bool(d.get('id')) and not self.pending)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'task_splitter'):
            self.task_splitter.setOrientation(Qt.Vertical if self.width() < 1000 else Qt.Horizontal)

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, self.scroll_chat_to_end)
        self.initial_scroll_timer.start(50)
        QTimer.singleShot(0, self.acknowledge_task_notifications)

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.ActivationChange and hasattr(self, 'notice_snapshot'):
            QTimer.singleShot(0, self.acknowledge_task_notifications)

    def acknowledge_task_notifications(self):
        if not self.isVisible() or not self.isActiveWindow() or self.isMinimized() or self.notice_pending or not self.data.get('id'):
            return
        snapshot = self.notice_snapshot
        if snapshot <= self.notice_read:
            return
        self.notice_pending = True
        def done(value, error):
            self.notice_pending = False
            if not error:
                self.notice_read = value
                self.changed.emit()
                owner = self.parentWidget()
                if hasattr(owner, 'notifications'):
                    owner.notifications.poll()
                if self.notice_snapshot > self.notice_read:
                    QTimer.singleShot(0, self.acknowledge_task_notifications)
        self.work(lambda: self.store.mark_task_notifications_read(self.data['id'], snapshot), done)

    def scroll_chat_to_end(self):
        self.comments.document().documentLayout().documentSize()
        bar = self.comments.verticalScrollBar()
        bar.setValue(bar.maximum())
        self.chat_initial_scroll = False
        self.acknowledge_comments()

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

    def display_timezone(self):
        return getattr(self.parentWidget(), 'timezone', 'UTC')

    def render_dates(self):
        dates = [date_text(value, True, self.display_timezone()) for value in self.timestamps]
        self.dates.setText('Создано: {}\nИзменено: {}\nЗавершено: {}'.format(*dates))

    def render_comments(self):
        from .presentation import conversation_html
        sources = avatar_resources(self.comments.document(), self.avatars)
        markup = conversation_html(self.comment_entries, self.history_entries, self.store.login, self.columns,
                                   self.display_timezone(), avatar_sources=sources)
        if markup != getattr(self, 'comments_markup', None):
            bar = self.comments.verticalScrollBar()
            at_bottom = getattr(self, 'chat_initial_scroll', True) or bar.value() >= bar.maximum() - 4
            position = bar.value()
            self.comments.setHtml(markup)
            bar.setValue(bar.maximum() if at_bottom else position)
            self.comments_markup = markup
            if at_bottom:
                QTimer.singleShot(0, self.scroll_chat_to_end)

    def render_history(self):
        markup = history_html(self.history_entries, self.columns, self.catalog_data['users'], self.display_timezone())
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
            self.archived.setEnabled(data.get('permissions', {}).get('create', False))
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
            self.comment_text.set_mention_users(users)
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
        self.subscribe_button.setEnabled(not enabled and bool(self.data.get('id')))
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

    def update_subscription_label(self):
        self.subscribe_button.setText('Отписаться' if self.subscribed else 'Подписаться')

    def toggle_subscription(self):
        if self.pending or not self.data.get('id'):
            return
        enabled = not self.subscribed
        self.busy(True)
        def done(result, error):
            self.busy(False)
            if error:
                self.status.setText('Не удалось изменить подписку: ' + error_text(error))
                return
            self.subscribed = result
            self.update_subscription_label()
            self.status.setText('Подписка включена' if result else 'Подписка отключена')
        self.work(lambda: self.store.subscribe(self.data['id'], enabled), done)

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


class BoardCreateDialog(QDialog):
    def __init__(self, can_create_shared, folder, projects, current_project, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Создать доску')
        self.setMinimumWidth(400)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.folder = QComboBox()
        if can_create_shared:
            self.folder.addItem('Рабочие доски', 'work')
            self.folder.addItem('Проекты', 'projects')
        self.folder.addItem('Личное', 'personal')
        self.folder.setCurrentIndex(max(0, self.folder.findData(folder)))
        self.project = QComboBox()
        self.project.setEditable(True)
        self.project.setInsertPolicy(QComboBox.NoInsert)
        self.project.addItems(projects)
        self.project.setEditText(current_project)
        self.project.lineEdit().setPlaceholderText('Выберите проект или введите название')
        self.name = QLineEdit()
        self.name.setPlaceholderText('Название доски')
        for label, field in (('Папка', self.folder), ('Проект', self.project), ('Название доски', self.name)):
            form.addRow(label, field)
            field.setAccessibleName(label)
        style_form(form)
        self.project_label = form.labelForField(self.project)
        layout.addLayout(form)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        actions = QHBoxLayout()
        actions.addStretch()
        self.create = button('Создать доску', self.accept, actions, 'primary')
        self.create.setDefault(True)
        button('Отмена', self.reject, actions)
        layout.addLayout(actions)
        self.folder.currentIndexChanged.connect(self.update_fields)
        self.project.currentTextChanged.connect(self.update_fields)
        self.name.textChanged.connect(self.update_fields)
        self.update_fields()
        self.name.setFocus()

    def update_fields(self):
        folder = self.folder.currentData()
        in_project = folder == 'projects'
        self.project.setVisible(in_project)
        self.project_label.setVisible(in_project)
        name = self.name.text().strip()
        project = self.project.currentText().strip()
        error = ''
        if in_project and ' / ' in project:
            error = 'Введите название одного проекта без разделителя « / ».'
        elif folder == 'work' and ' / ' in name:
            error = 'Для создания доски в проекте выберите папку «Проекты».'
        self.status.setText(error)
        self.status.setVisible(bool(error))
        self.create.setEnabled(bool(name and (project or not in_project) and not error))

    def accept(self):
        if self.create.isEnabled():
            super().accept()

    def values(self):
        folder = self.folder.currentData()
        name = self.name.text().strip()
        if folder == 'projects':
            name = '{} / {}'.format(self.project.currentText().strip(), name)
        return name, folder == 'personal'


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


class MainWindow(QMainWindow, Async):
    sppi_progress = pyqtSignal(int, str)
    sppi_card_saved = pyqtSignal(int)

    def __init__(self, folder, redmine_url='', redmine_statuses=None, installation_base=None):
        super().__init__()
        self.init_async()
        self.folder = folder
        self.installation_base = installation_base
        self.installation_error = ''
        if installation_base is not None:
            try:
                self.folder = installation.load_folder(installation_base)
            except (OSError, ValueError, KeyError, TypeError):
                self.installation_error = 'Не удалось прочитать настройки установки. Откройте «Настройки → Подключение…».'
        self.redmine_url = redmine_url
        self.redmine_statuses = {redmine.status_key(k): v.strip() for k, v in (redmine_statuses or {}).items()}
        self.redmine_client = None
        self.redmine_login = None
        self.bugzilla_config = {'url': '', 'domain': ''}
        self.bugzilla_session = None
        self.bugzilla_cards = {}
        self.bugzilla_links = {}
        self.bugzilla_loading = False
        self.redmine_refresh_message = ''
        self.sppi_view_board = None
        self.sppi_view_dirty = False
        self.store = None
        self.access_restricted = False
        self.hidden_task_dialogs = set()
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
        self.expanded_task_ids = set()
        self.compact_ti_ids = set()
        self.board_counts = {}
        self.task_notice_counts = {}
        self.timezone = 'UTC'
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
        brand = QLabel('TeamSync')
        brand.setObjectName('appName')
        logo = QLabel()
        logo.setObjectName('SystemLogo')
        logo.setFixedSize(32, 32)
        logo.setPixmap(QPixmap(str(Path(__file__).parent / 'assets' / 'teamsync-logo.png')).scaled(32, 32, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        brand_row.addWidget(logo)
        brand_row.addWidget(brand, 1)
        self.collapse_button = button('', self.collapse_sidebar, brand_row, 'quiet')
        self.collapse_button.setToolTip('Свернуть навигацию')
        self.collapse_button.setAccessibleName('Свернуть навигацию')
        self.collapse_button.setProperty('symbol', 'menu')
        self.collapse_button.setIcon(symbol_icon('menu'))
        self.collapse_button.setIconSize(QSize(22, 22))
        self.collapse_button.setFixedSize(36, 36)
        side.addLayout(brand_row)
        side.addSpacing(22)
        caption = QLabel('ПРОЕКТЫ И ДОСКИ')
        caption.setObjectName('Eyebrow')
        side.addWidget(caption)
        self.boards_list = QListWidget()
        self.boards_list.hide()
        self.boards_list.currentItemChanged.connect(self.select_board)
        self.board_tree = QTreeWidget()
        self.board_tree.setColumnCount(2)
        self.board_tree.setHeaderLabels(['Доска', 'Уведомления'])
        self.board_tree.setHeaderHidden(True)
        self.board_tree.header().setStretchLastSection(False)
        self.board_tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.board_tree.header().setSectionResizeMode(1, QHeaderView.Fixed)
        self.board_tree.setColumnWidth(1, 42)
        self.board_tree.setItemDelegate(BoardBadgeDelegate(self.board_tree))
        self.board_tree.setIndentation(14)
        self.board_tree.setAccessibleName('Рабочие доски, проекты и личное')
        self.board_items = {}
        self.my_tasks_item = None
        self.create_project_item = None
        self.board_tree.itemClicked.connect(self.select_tree_board)
        self.board_tree.itemActivated.connect(self.select_tree_board)
        side.addWidget(self.board_tree, 1)
        self.create_board_button = button('Создать доску', lambda: self.create_board(), side, 'quiet')
        self.create_board_button.setToolTip('Создать доску в доступной папке')
        self.create_board_button.setEnabled(False)
        side.addSpacing(18)
        self.report_nav = button('Отчёты', lambda: self.pages.setCurrentWidget(self.statistics), side, 'quiet')
        self.report_nav.setCheckable(True)
        self.users_button = button('Сотрудники', lambda: self.open_catalog('users'), side, 'quiet')
        self.theme_button = button('Тёмная тема', self.toggle_theme, side, 'quiet')
        self.theme_button.setCheckable(True)
        self.theme_button.setChecked(theme_settings().value('dark', False, type=bool))
        self.theme_button.setText('Светлая тема' if self.theme_button.isChecked() else 'Тёмная тема')
        profile = QLabel(getpass.getuser() + '\nЛокальное рабочее пространство')
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
        self.search.setClearButtonEnabled(False)
        self.search.setMaximumWidth(360)
        self._search_in_header = True
        top.addWidget(self.search, 2)
        self.clear_search_button = button('Очистить поиск', self.search.clear, top)
        self.view_switch.setFixedWidth(164)
        top.addWidget(self.view_switch)
        self.new_task_button = button('Добавить задачу', self.new_task, top, 'primary')
        self.new_task_button.setFixedWidth(176)
        self.settings_button = settings_button = button('Настройки', self.workspace_menu, top)
        settings_button.setMinimumWidth(112)
        settings_button.setToolTip('Настройки доски и обслуживание')
        settings_button.setAccessibleName('Настройки доски')
        self.notification_button = button('', self.open_notifications, top)
        self.notification_button.setObjectName('NotificationLauncher')
        self.notification_button.setProperty('symbol', 'bell')
        self.notification_button.setIcon(symbol_icon('bell'))
        self.notification_button.setIconSize(QSize(22, 22))
        self.notification_button.setFixedWidth(66)
        self.update_notification_button(0)
        self.retry_button = button('Повторить', self.retry_mutation, top)
        self.retry_button.hide()
        workspace.addWidget(header)
        self.narrow_search_row = QWidget()
        self.narrow_search_row.setObjectName('Header')
        self.narrow_search_row.setAttribute(Qt.WA_StyledBackground, True)
        self.narrow_search_layout = QHBoxLayout(self.narrow_search_row)
        self.narrow_search_layout.setContentsMargins(20, 0, 16, 8)
        workspace.addWidget(self.narrow_search_row)
        self.narrow_search_row.hide()
        self.board_tabs = QTabBar()
        self.board_tabs.hide()
        self.board_tabs.currentChanged.connect(self.select_board_tab)
        self.pages = QTabWidget()
        self.pages.tabBar().hide()
        board_page = QWidget()
        self.statistics = StatisticsPage()
        self.my_tasks = MyTasksPage()
        self.my_tasks.opened.connect(self.open_personal_task)
        self.my_tasks.details_requested.connect(self.load_card_details)
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
        self.pages.currentChanged.connect(self.page_changed)
        workspace.addWidget(self.pages, 1)
        board_layout = QVBoxLayout(board_page)
        board_layout.setContentsMargins(0, 0, 0, 0)
        self.project_sections = QTabWidget()
        self.project_sections.setObjectName('ProjectSections')
        tab_bar = self.project_sections.tabBar()
        tab_bar.setObjectName('ProjectSectionTabs')
        tab_bar.setAccessibleName('Разделы проектной доски')
        tab_bar.setExpanding(False)
        tab_bar.setUsesScrollButtons(True)
        tab_bar.hide()
        self.project_sections_board = None
        board_layout.addWidget(self.project_sections)
        current_work = QWidget()
        self.project_sections.addTab(current_work, 'Текущие работы')
        self.identification_page = IdentificationPage(self.refresh_identification, self.refresh_identification_card)
        self.project_sections.addTab(self.identification_page, 'ИД')
        self.bugzilla_page = BugzillaPage(self.export_bugzilla_bug, lambda: self.export_bugzilla_bug(mode='bulk'),
                                         lambda: self.export_bugzilla_bug(mode='refresh'))
        self.project_sections.addTab(self.bugzilla_page, 'Баги')
        self.project_sections.currentChanged.connect(self.project_section_changed)
        content = QVBoxLayout(current_work)
        content.setContentsMargins(20, 16, 20, 12)
        content.setSpacing(14)
        self.ti_actions = QWidget()
        ti_actions = QHBoxLayout(self.ti_actions)
        ti_actions.setContentsMargins(0, 0, 0, 0)
        ti_actions.setSpacing(8)
        self.refresh_ti_button = button('Обновить по #ID', self.refresh_workspace, ti_actions)
        self.refresh_ti_button.setToolTip('Обновить уже добавленные ТИ по их ID в Redmine')
        self.import_sppi_button = button('Выгрузить из Redmine', self.import_sppi_ti, ti_actions)
        self.import_sppi_button.setToolTip('Проверить проект СППИ, добавить новые ТИ и обновить загруженные')
        ti_actions.addStretch()
        self.ti_actions.hide()
        content.addWidget(self.ti_actions)
        filters = QHBoxLayout()
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
        self.saved_filter_button = button('Наборы фильтров', self.saved_filter_menu, filters)
        filters.addStretch()
        content.addLayout(filters)
        self.filter_message = QLabel()
        self.filter_message.setWordWrap(True)
        self.filter_message.setTextFormat(Qt.PlainText)
        self.filter_message.hide()
        content.addWidget(self.filter_message)
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
        self.sppi_log_board = None
        self.sppi_log_lines = [ElidedLabel() for _ in range(4)]
        self.sppi_log = QWidget()
        self.sppi_log.setAccessibleName('Журнал проверки ТИ')
        log_layout = QVBoxLayout(self.sppi_log)
        log_layout.setContentsMargins(0, 0, 0, 0)
        log_layout.setSpacing(2)
        for line in self.sppi_log_lines:
            line.setObjectName('Muted')
            line.setTextFormat(Qt.PlainText)
            line.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
            line.hide()
            log_layout.addWidget(line)
        self.sppi_log.hide()
        content.addWidget(self.sppi_log)
        self.sppi_progress.connect(self.show_sppi_progress, Qt.QueuedConnection)
        self.sppi_card_saved.connect(self.refresh_sppi_board, Qt.QueuedConnection)
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
        self.messenger = Messenger(self)
        self.board_chats = {None: self.messenger}
        self.notifications = Notifications(self)
        self.connect_store()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not hasattr(self, 'search'):
            return
        if hasattr(self, 'messenger'):
            self.messenger.reposition()
        wide = self.width() >= 1200
        if wide != getattr(self, '_search_in_header', False):
            self._search_in_header = wide
            if wide:
                self.header_layout.insertWidget(1, self.search, 2)
                self.header_layout.insertWidget(2, self.clear_search_button)
                self.header_layout.addWidget(self.retry_button)
            else:
                self.narrow_search_layout.addWidget(self.search, 1)
                self.narrow_search_layout.addWidget(self.clear_search_button)
                self.narrow_search_layout.addWidget(self.retry_button)
            self.search.setMaximumWidth(360 if wide else 16777215)
            self.header_layout.setSpacing(12 if wide else 8)
            self.new_task_button.setFixedWidth(176 if wide else 156)
            self.settings_button.setMinimumWidth(112 if wide else 90)
            self.label_filter.setMaximumWidth(16777215 if wide else 148)
            self.narrow_search_row.setVisible(not wide)

    def update_navigation(self, index=None):
        personal = self.pages.currentWidget() is self.my_tasks
        self.report_nav.setChecked(self.pages.currentWidget() is self.statistics)
        current = self.boards_list.currentItem()
        title = 'Мои задачи' if personal else current.text() if current else 'Выберите доску'
        self.board_title.setText(title)
        self.board_title.setToolTip(title)
        node = self.my_tasks_item if personal else self.board_items.get(self.board_id)
        if node is not None and self.board_tree.currentItem() is not node:
            self.board_tree.setCurrentItem(node)
        board_node = self.board_items.get(self.board_id)
        project_board = self.board_id if board_node is not None and board_node.parent().parent() is not None else None
        if project_board != self.project_sections_board:
            self.project_sections_board = project_board
            self.sppi_log.setVisible(project_board is not None and project_board == self.sppi_log_board)
            self.identification_page.show_cards(None, '', None)
            self.render_bugzilla_cards()
            self.project_sections.setCurrentIndex(0)
            self.project_sections.tabBar().setVisible(project_board is not None)
        self.update_board_controls()

    def project_section_changed(self):
        self.update_board_controls()
        self.generation += 1
        self.debounce.start(0)

    def update_board_controls(self):
        empty_section = self.pages.currentIndex() == 0 and self.project_sections.currentIndex() != 0
        self.search.setEnabled(not empty_section)
        self.clear_search_button.setEnabled(not empty_section)
        can_create = getattr(self, 'permissions', {}).get('create', False)
        project = self.project_sections_board == self.board_id and self.board_id is not None
        current_work = self.pages.currentIndex() == 0 and not empty_section
        self.new_task_button.setEnabled(can_create and bool(self.board_id) and (current_work or not project))
        self.ti_actions.setVisible(project and current_work)
        ready = not self.mutating and not self.pending_action
        self.refresh_ti_button.setEnabled(ready)
        self.import_sppi_button.setEnabled(ready and can_create)

    def apply_menu_permissions(self, menu):
        permissions = getattr(self, 'permissions', {})
        for action in menu.actions():
            text = action.text()
            if text in ('Роли сотрудников', 'Набор стандартных стикеров…', 'Резервная копия', 'Часовой пояс…', 'Проект поиска оснований…',
                        'Проект СППИ…'):
                action.setEnabled(permissions.get('admin', False))
            elif text == 'Выгрузить баг из Bugzilla…':
                action.setEnabled(self.store is not None and not self.access_restricted and not self.bugzilla_loading)
            elif text == 'Личные настройки…':
                action.setEnabled(self.store is not None and not self.access_restricted)
            elif text == 'Подключение…':
                action.setEnabled((self.store is None and self.installation_base is not None)
                                  or permissions.get('admin', False))
            elif text == 'Создать доску':
                action.setEnabled(bool(permissions))
            elif text == 'Участники доски':
                action.setEnabled(bool(self.board_id) and permissions.get('manage', False) and permissions.get('share', False))
            elif text not in ('Обновить', 'Уведомления чатов досок', 'Уведомления об изменениях задач', 'Закрывать окно в трей', 'Проверять орфографию') and not action.isSeparator():
                action.setEnabled(bool(self.board_id) and permissions.get('manage', False))

    def open_access(self, board_id=None):
        if not self.store:
            return
        dialog = AccessDialog(self.store, board_id, self)
        dialog.changed.connect(self.refresh)
        dialog.exec_()

    def workspace_menu(self):
        menu = QMenu(self)
        menu.addAction('Участники доски', lambda: self.open_access(self.board_id))
        menu.addAction('Роли сотрудников', lambda: self.open_access())
        menu.addAction('Колонки доски', self.edit_columns)
        menu.addAction('Метки доски', lambda: self.open_catalog('labels'))
        if self.board_id is not None and self.project_sections_board == self.board_id:
            menu.addAction('Проект поиска оснований…', self.edit_basis_project)
            menu.addAction('Проект СППИ…', self.edit_sppi_project)
        menu.addSeparator()
        menu.addAction('Переименовать доску', lambda: self.board_action('rename'))
        menu.addAction('Архивировать доску', lambda: self.board_action('archive'))
        menu.addSeparator()
        menu.addAction('Обновить', self.refresh_workspace)
        menu.addAction('Личные настройки…', self.open_avatar_settings)
        menu.addAction('Набор стандартных стикеров…', self.open_sticker_pack)
        menu.addAction('Подключение…', self.open_connection_settings)
        menu.addAction('Выгрузить баг из Bugzilla…', self.export_bugzilla_bug)
        self.notifications.add_settings(menu)
        menu.addAction('Часовой пояс…', self.change_timezone)
        menu.addAction('Резервная копия', self.backup)
        self.apply_menu_permissions(menu)
        menu.exec_(self.mapToGlobal(self.rect().topRight()) - QPoint(240, -56))

    def open_sticker_pack(self):
        if not self.store or self.access_restricted or not self.permissions.get('admin', False):
            return
        dialog = StickerPackDialog(self.store, self.work, self, manage=True)
        dialog.exec_()
        dialog.deleteLater()

    def open_avatar_settings(self):
        store = self.store
        if store is None or self.access_restricted:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle('Личные настройки')
        dialog.setMinimumWidth(320)
        layout = QVBoxLayout(dialog)
        profile = QHBoxLayout()
        preview = QLabel()
        preview.setFixedSize(64, 64)
        preview.setAlignment(Qt.AlignCenter)
        profile.addWidget(preview)
        description = QLabel()
        description.setWordWrap(True)
        profile.addWidget(description, 1)
        layout.addLayout(profile)
        actions = QHBoxLayout()
        choose_button = QPushButton('Выбрать фото…')
        remove_button = QPushButton('Удалить аватар')
        actions.addWidget(choose_button)
        actions.addWidget(remove_button)
        layout.addLayout(actions)
        status = QuietStatus()
        status.setWordWrap(True)
        layout.addWidget(status)
        close = QDialogButtonBox(QDialogButtonBox.Close)
        close.rejected.connect(dialog.reject)
        layout.addWidget(close)
        current = None
        finished = False

        def update_preview():
            if current:
                pixmap = QPixmap.fromImage(avatar_image(current))
                preview.setPixmap(pixmap.scaled(preview.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))
            else:
                preview.clear()
            preview.setAccessibleName('Текущий аватар' if current else 'Аватар отсутствует')
            description.setText('Фото профиля' if current else 'Аватар не добавлен')

        def run(action, *, saving=False):
            choose_button.setEnabled(False)
            remove_button.setEnabled(False)
            status.setText('Сохранение…' if saving else 'Загрузка…')

            def done(result, error):
                nonlocal current
                if self.store is not store:
                    return
                if isinstance(error, AccessRestricted):
                    self.set_access_restricted(True)
                    return
                if saving and error is None:
                    self.refresh()
                    self.messenger.poll()
                    for task in self.dialogs:
                        task.refresh_extras()
                if finished:
                    return
                if error is None:
                    current = result
                    update_preview()
                choose_button.setEnabled(True)
                remove_button.setEnabled(bool(current))
                status.setText(error_text(error) if error else ('Сохранено' if saving else ''))

            self.work(action, done)

        def choose():
            name, _ = QFileDialog.getOpenFileName(dialog, 'Выберите фото профиля', '',
                                                  'Изображения (*.png *.jpg *.jpeg *.webp)')
            if not name:
                return

            def save():
                with Path(name).open('rb') as source:
                    return store.set_avatar(source.read(MAX_SOURCE + 1))

            run(save, saving=True)

        choose_button.clicked.connect(choose)
        remove_button.clicked.connect(lambda: run(lambda: store.set_avatar(None), saving=True))
        update_preview()
        run(store.own_avatar)
        dialog.exec_()
        finished = True
        dialog.deleteLater()

    def export_bugzilla_bug(self, *, mode='single'):
        store = self.store
        if store is None or self.access_restricted or self.bugzilla_loading:
            return
        board_id = self.project_sections_board if self.project_sections_board == self.board_id else None
        if mode != 'single':
            node = self.board_items.get(board_id)
            if node is None:
                return
            project, board = node.parent().text(0), node.text(0)
        config = self.bugzilla_config
        if not config['url'] or not config['domain']:
            self.status.setText('Администратору нужно указать адрес и домен Bugzilla в «Настройки → Подключение…».')
            return
        numbers = tuple(self.bugzilla_cards.get(board_id, ()))
        if mode == 'refresh' and not numbers:
            return
        source_login = self.redmine_login or store.login
        username = source_login.rsplit('\\', 1)[-1].partition('@')[0]
        login = f"{username}@{config['domain']}"
        scope = (config['url'], login)
        session = self.bugzilla_session
        password = session[2] if session and session[:2] == scope else None
        export = self.prompt_bugzilla_export(login, password, mode)
        if export is None or self.access_restricted or self.store is not store:
            return
        number, password = export

        def done(result, error):
            nonlocal password
            used_password, password = password, None
            self.bugzilla_loading = False
            self.bugzilla_page.set_loading(False)
            if self.access_restricted or self.store is not store:
                return
            if self.bugzilla_config != config or (self.redmine_login or store.login) != source_login:
                return
            if error:
                if isinstance(error, bugzilla.BugzillaAuthError):
                    self.reset_bugzilla_session()
                elif isinstance(error, AccessRestricted):
                    self.set_access_restricted(True)
                self.status.setText(error_text(error))
                return
            self.bugzilla_session = (*scope, used_password)
            if mode != 'single':
                if board_id in self.board_items and result is not None:
                    self.bugzilla_cards[board_id] = result['cards']
                    self.render_bugzilla_cards()
                    verb = 'Обновлено' if mode == 'refresh' else 'Выгружено'
                    self.status.setText(f'{verb} багов из Bugzilla: {len(result["cards"])}')
                return
            raw, snapshot, notice = result
            if board_id in self.board_items and snapshot is not None:
                self.bugzilla_cards[board_id] = snapshot['cards']
                self.render_bugzilla_cards()
            self.status.setText(f'Баг #{number} загружен из Bugzilla')
            self.show_bugzilla_json(number, raw, notice)

        def load():
            if store.bugzilla_settings() != config:
                raise bugzilla.BugzillaError('Настройки Bugzilla изменились. Обновите TeamSync и повторите выгрузку.')
            shared = store.bugzilla_board_snapshot(board_id) if board_id is not None else None
            if mode != 'single':
                employees = installation.load_redmine_employees(self.redmine_config_base())
                if mode == 'refresh':
                    current = shared['cards'] if shared else {}
                    cards = bugzilla.refresh_board_bugs(scope[0], tuple(current) if shared else numbers, employees,
                                                         login=login, password=password, keep_unavailable=current)
                else:
                    cards = bugzilla.search_board_bugs(scope[0], project, board, employees, login=login, password=password)
                store.save_bugzilla_cards(board_id, config, cards, replace=True,
                                          revision=shared['revision'] if shared else None, board_name=f'{project} / {board}')
                return store.bugzilla_board_snapshot(board_id)
            raw = bugzilla.read_bug(scope[0], number, login=login, password=password)
            if board_id is None:
                return raw, None, ''
            try:
                employees = installation.load_redmine_employees(self.redmine_config_base())
            except ValueError as error:
                return raw, None, error_text(error)
            card = bugzilla.board_card(raw, number, employees)
            store.save_bugzilla_cards(board_id, config, {number: card})
            notice = ('Баг добавлен во вкладку «Баги».' if card is not None else
                      'Баг не добавлен на доску. Закрытые баги и баги других исполнителей исключаются.')
            return raw, store.bugzilla_board_snapshot(board_id), notice

        self.bugzilla_loading = True
        self.bugzilla_page.set_loading(True)
        self.status.setText('Запрос к Bugzilla…' if mode != 'single' else f'Загрузка бага #{number} из Bugzilla…')
        self.work(load, done)

    def prompt_bugzilla_export(self, login, password, mode):
        if mode != 'single' and password is not None:
            return None, password
        dialog = QDialog(self)
        dialog.setWindowTitle({'single': 'Выгрузить баг из Bugzilla', 'bulk': 'Выгрузить баги по фильтрам',
                               'refresh': 'Обновить сохранённые баги'}[mode])
        dialog.setMinimumWidth(370)
        layout = QVBoxLayout(dialog)
        form = QFormLayout()
        bug_id = QLineEdit() if mode == 'single' else None
        if bug_id is not None:
            form.addRow('ID бага', bug_id)
        login_label = QLabel(login)
        login_label.setTextFormat(Qt.PlainText)
        form.addRow('Логин Bugzilla', login_label)
        password_input = None
        if password is None:
            password_input = QLineEdit()
            password_input.setEchoMode(QLineEdit.Password)
            form.addRow('Пароль Bugzilla', password_input)
        style_form(form)
        layout.addLayout(form)
        error_label = QLabel()
        error_label.setWordWrap(True)
        layout.addWidget(error_label)

        def validate():
            if bug_id is not None:
                try:
                    bugzilla.bug_number(bug_id.text())
                except bugzilla.BugzillaError as error:
                    error_label.setText(error_text(error))
                    bug_id.setFocus()
                    return
            if password_input is not None and not password_input.text():
                error_label.setText('Введите пароль Bugzilla.')
                password_input.setFocus()
                return
            dialog.accept()

        actions = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        actions.button(QDialogButtonBox.Ok).setText('Обновить' if mode == 'refresh' else 'Выгрузить')
        actions.button(QDialogButtonBox.Cancel).setText('Отмена')
        actions.accepted.connect(validate)
        actions.rejected.connect(dialog.reject)
        layout.addWidget(actions)
        accepted = dialog.exec_() == QDialog.Accepted
        if accepted:
            number = bugzilla.bug_number(bug_id.text()) if bug_id is not None else None
            if password_input is not None:
                password = password_input.text()
        if password_input is not None:
            password_input.clear()
        dialog.deleteLater()
        return (number, password) if accepted else None

    def render_bugzilla_cards(self):
        board_id = self.project_sections_board
        cards = self.load_bugzilla_cards(board_id)
        self.bugzilla_page.show_cards(cards.values(), self.bugzilla_links.get(board_id, ()))

    def bugzilla_cache_path(self, board_id):
        local = QStandardPaths.writableLocation(QStandardPaths.GenericDataLocation)
        if not local:
            return None
        scope = json.dumps([str(self.store.folder), self.store.login, self.bugzilla_config, board_id], sort_keys=True)
        key = hashlib.sha256(scope.encode('utf-8')).hexdigest()
        return Path(local) / 'TeamSync' / 'bugzilla' / f'{key}.json'

    def load_bugzilla_cards(self, board_id):
        if self.access_restricted or self.store is None or board_id is None or not self.bugzilla_config['url']:
            return {}
        if board_id not in self.bugzilla_cards:
            path = self.bugzilla_cache_path(board_id)
            self.bugzilla_cards[board_id] = bugzilla.load_board_cache(path) if path else {}
        return self.bugzilla_cards[board_id]

    def reset_bugzilla_session(self):
        self.bugzilla_session = None

    def show_bugzilla_json(self, number, raw, notice=''):
        dialog = QDialog(self)
        dialog.setWindowTitle(f'Баг #{number} · Bugzilla')
        dialog.resize(760, 460)
        layout = QVBoxLayout(dialog)
        preview = QPlainTextEdit()
        preview.setReadOnly(True)
        preview.setLineWrapMode(QPlainTextEdit.NoWrap)
        preview.setAccessibleName('Ответ Bugzilla в формате JSON')
        preview.setPlainText(raw)
        layout.addWidget(preview)
        status = QLabel(notice)
        status.setTextFormat(Qt.PlainText)
        status.setWordWrap(True)
        layout.addWidget(status)

        def save():
            path, _ = QFileDialog.getSaveFileName(dialog, 'Сохранить ответ Bugzilla',
                                                  f'bugzilla-{number}.json', 'JSON (*.json)')
            if not path or self.access_restricted:
                return
            content = raw.encode('utf-8')
            output = QSaveFile(path)
            if not output.open(QIODevice.WriteOnly) or output.write(content) != len(content) or not output.commit():
                status.setText('Не удалось сохранить JSON. Проверьте доступ к выбранной папке.')
            else:
                status.setText('JSON сохранён')

        actions = QHBoxLayout()
        actions.addStretch()
        button('Сохранить JSON…', save, actions, 'primary')
        button('Закрыть', dialog.accept, actions)
        layout.addLayout(actions)
        dialog.exec_()
        dialog.deleteLater()

    def open_connection_settings(self):
        if self.loading or self.mutating or self.pending_action:
            self.status.setText('Дождитесь завершения текущей операции.')
            return
        store = self.store
        if store is not None and not getattr(self, 'permissions', {}).get('admin', False):
            return
        folder, url, statuses = self.folder or '', self.redmine_url, self.redmine_statuses
        if not folder and not self.installation_error and self.installation_base is not None:
            try:
                folder, url, statuses = installation.legacy_settings(self.installation_base)
            except (OSError, ValueError):
                pass

        class ConnectionDialog(QDialog):
            def reject(self):
                if self.isEnabled():
                    super().reject()

        dialog = ConnectionDialog(self)
        dialog.setWindowTitle('Подключение TeamSync')
        dialog.setMinimumWidth(490)
        layout = QVBoxLayout(dialog)
        form = QFormLayout()
        folder_input = QLineEdit(str(folder))
        folder_input.setPlaceholderText('Папка с kanban.db')
        folder_input.setReadOnly(self.installation_base is None)
        folder_row = QHBoxLayout()
        folder_row.addWidget(folder_input, 1)

        def select_folder():
            selected = QFileDialog.getExistingDirectory(dialog, 'Папка с kanban.db', folder_input.text())
            if selected:
                folder_input.setText(selected)

        browse = button('Выбрать…', select_folder, folder_row)
        browse.setEnabled(not folder_input.isReadOnly())
        form.addRow('Папка базы', folder_row)
        folder_input.setAccessibleName('Папка базы')
        url_input = QLineEdit(url)
        url_input.setPlaceholderText('https://redmine.example.org')
        url_input.setAccessibleName('Адрес Redmine')
        form.addRow('Адрес Redmine', url_input)
        mapping_input = QTextEdit()
        mapping_input.setAcceptRichText(False)
        mapping_input.setAccessibleName('Статусы Redmine')
        mapping_input.setPlaceholderText('Новый = Проект ТИ\nОткрыт = ТИ открыто')
        mapping_input.setPlainText('\n'.join('{} = {}'.format(k, v) for k, v in statuses.items()))
        mapping_input.setMaximumHeight(130)
        form.addRow('Статусы Redmine', mapping_input)
        original_bugzilla = self.bugzilla_config
        bugzilla_url = QLineEdit(original_bugzilla['url'])
        bugzilla_url.setPlaceholderText('https://bugs.example.org/bugzilla')
        bugzilla_url.setAccessibleName('Адрес Bugzilla')
        form.addRow('Адрес Bugzilla', bugzilla_url)
        bugzilla_domain = QLineEdit(original_bugzilla['domain'])
        bugzilla_domain.setPlaceholderText('example.org')
        bugzilla_domain.setAccessibleName('Почтовый домен Bugzilla')
        form.addRow('Почтовый домен Bugzilla', bugzilla_domain)
        style_form(form)
        layout.addLayout(form)
        hint = QLabel('Укажите соответствия по одному на строку. Если поле пустое, колонки подбираются по названию статуса.')
        if self.installation_base is not None:
            hint.setText(hint.text() + '\nПри переходе на другую базу используются её настройки Redmine и Bugzilla.')
        hint.setWordWrap(True)
        layout.addWidget(hint)
        status = QLabel()
        status.setTextFormat(Qt.PlainText)
        status.setWordWrap(True)
        layout.addWidget(status)
        actions = QHBoxLayout()
        actions.addStretch()
        button('Сохранить', lambda: save_settings(), actions, 'primary')
        button('Отмена', dialog.reject, actions)
        layout.addLayout(actions)
        saved = {}

        def save_settings():
            raw_folder = folder_input.text().strip()
            if not raw_folder:
                status.setText('Укажите папку с существующей базой TeamSync.')
                folder_input.setFocus()
                return
            target = Path(raw_folder).expanduser()
            if not target.is_absolute():
                status.setText('Укажите полный путь к папке базы.')
                folder_input.setFocus()
                return
            url = url_input.text().strip()
            mapping = {}
            try:
                bugzilla_config = bugzilla.connection_settings(bugzilla_url.text(), bugzilla_domain.text())
                if url:
                    redmine.validate_url(url)
                for line in mapping_input.toPlainText().splitlines():
                    if not line.strip():
                        continue
                    source, _, destination = line.partition('=')
                    key = redmine.status_key(source)
                    if not key or not destination.strip() or key in mapping:
                        raise ValueError('Каждая строка должна содержать уникальный статус и название колонки через «=».')
                    mapping[key] = destination.strip()
            except ValueError as error:
                status.setText(str(error))
                return

            def apply():
                if store is not None and store.profile()['role'] != 'admin':
                    raise ValueError('Изменить подключение может администратор текущей базы.')
                if not (target / 'kanban.db').is_file():
                    raise ValueError('В указанной папке нет kanban.db.')
                same_database = store is not None and target == store.folder
                destination = store if same_database else Store(target)
                if destination.profile()['role'] != 'admin':
                    raise ValueError('Сохранить подключение может администратор выбранной базы.')
                redmine_config = destination.redmine_settings()
                save_redmine = same_database or (store is None and redmine_config is None and (url or mapping))
                save_bugzilla = ((same_database and bugzilla_config != original_bugzilla)
                                 or (store is None and any(bugzilla_config.values())))
                if save_redmine or save_bugzilla:
                    if store is None and save_bugzilla and not bugzilla_config['url']:
                        bugzilla_config['url'] = destination.bugzilla_settings()['url']
                    result = destination.set_connection_settings(
                        redmine={'url': url, 'statuses': mapping} if save_redmine else None,
                        bugzilla_config=bugzilla_config if save_bugzilla else None,
                    )
                else:
                    result = {'redmine': redmine_config, 'bugzilla': destination.bugzilla_settings()}
                if self.installation_base is not None and self.folder != str(target):
                    installation.save_folder(self.installation_base, target)
                return result

            def done(result, error):
                dialog.setEnabled(True)
                if error:
                    status.setText(error_text(error))
                    return
                saved.update(folder=str(target), **result)
                dialog.accept()

            status.setText('Сохранение…')
            dialog.setEnabled(False)
            self.work(apply, done)

        dialog.exec_()
        dialog.deleteLater()
        if not saved:
            return
        if store is not None and Path(saved['folder']) != store.folder:
            self.status.setText('Путь сохранён. Полностью выйдите из TeamSync и запустите его снова.')
            return
        self.installation_error = ''
        self.folder = saved['folder']
        self.apply_redmine_settings(saved['redmine'])
        self.apply_bugzilla_settings(saved['bugzilla'])
        self.refresh()

    def apply_redmine_settings(self, settings):
        if settings is None:
            return
        if settings['url'] != self.redmine_url:
            self.redmine_client = None
            self.redmine_login = None
            self.reset_bugzilla_session()
        self.redmine_url = settings['url']
        self.redmine_statuses = settings['statuses']

    def apply_bugzilla_settings(self, settings):
        if settings != self.bugzilla_config:
            self.bugzilla_config = settings
            self.reset_bugzilla_session()
            self.bugzilla_cards.clear()
            self.render_bugzilla_cards()

    def change_timezone(self):
        zones = sorted({bytes(zone).decode('ascii') for zone in QTimeZone.availableTimeZoneIds()})
        if 'UTC' not in zones:
            zones.insert(0, 'UTC')
        index = zones.index(self.timezone) if self.timezone in zones else zones.index('UTC')
        name, accepted = QInputDialog.getItem(self, 'Часовой пояс', 'Часовой пояс для всех сотрудников', zones, index, False)
        if not accepted or name == self.timezone:
            return

        def done(value, error):
            if error:
                self.status.setText('Часовой пояс не сохранён: ' + error_text(error))
                return
            self.set_display_timezone(value)
            self.status.setText('Часовой пояс сохранён: ' + value)
        self.work(lambda: self.store.set_timezone(name), done)

    def set_display_timezone(self, name):
        if name == self.timezone:
            return
        self.timezone = name
        for dialog in self.dialogs:
            dialog.render_dates()
            dialog.render_history()
            dialog.render_comments()
        for chat in self.board_chats.values():
            chat.render()

    def open_notifications(self):
        self.notifications.open_inbox()

    def update_notification_button(self, count):
        self.notification_button.setText('99+' if count > 99 else str(count) if count else '')
        label = 'Уведомления · непрочитанных: {}'.format(count)
        self.notification_button.setAccessibleName(label)
        self.notification_button.setToolTip(label)

    def update_unread_notifications(self, tasks, chat, timezone):
        counts = {}
        for event in tasks:
            tid = event['task_id']
            counts[tid] = counts.get(tid, 0) + 1
        if counts != self.task_notice_counts:
            self.task_notice_counts = counts
            self.my_tasks.update_notices(counts)
            for cards in self.card_lists:
                for row in range(cards.count()):
                    self.update_card_notice(cards.item(row))
        self.update_notification_button(len(tasks) + len(chat))
        self.set_display_timezone(timezone)

    def update_card_notice(self, item):
        set_card_notice(item, self.task_notice_counts.get(item.data(Qt.UserRole)['id'], 0))

    def board_settings_menu(self):
        menu = QMenu(self)
        menu.addAction('Создать доску', lambda: self.create_board())
        menu.addAction('Переименовать доску', lambda: self.board_action('rename'))
        menu.addAction('Архивировать доску', lambda: self.board_action('archive'))
        self.apply_menu_permissions(menu)
        menu.exec_(self.sidebar.mapToGlobal(self.sidebar.rect().center()))

    def collapse_sidebar(self):
        collapsed = self.sidebar.width() > 100
        self.sidebar.setFixedWidth(64 if collapsed else 240)
        for widget in self.sidebar.findChildren(QWidget):
            if widget is self.collapse_button:
                continue
            if widget.parentWidget() is self.sidebar:
                widget.setVisible(not collapsed)
        self.boards_list.hide()
        self.board_tabs.hide()
        self.collapse_button.setText('')
        self.collapse_button.setAccessibleName('Развернуть меню' if collapsed else 'Свернуть меню')
        self.collapse_button.setToolTip('Развернуть меню' if collapsed else 'Свернуть меню')
        self.sidebar.layout().setAlignment(Qt.AlignTop if collapsed else Qt.Alignment(0))
        self.sidebar.layout().invalidate()
        self.sidebar.layout().activate()

    def select_tree_board(self, item, column):
        action = item.data(0, Qt.UserRole + 1)
        if action == 'my_tasks':
            self.pages.setCurrentWidget(self.my_tasks)
            return
        if action in ('create_personal', 'create_project'):
            self.create_board('personal' if action == 'create_personal' else 'projects')
            return
        if action == 'personal_archive':
            self.restore_personal_board()
            return
        board_id = item.data(0, Qt.UserRole)
        if board_id is not None:
            index = next((i for i in range(self.board_tabs.count()) if self.board_tabs.tabData(i) == board_id), -1)
            if index == self.board_tabs.currentIndex():
                self.pages.setCurrentIndex(0)
                self.refresh()
            else:
                self.board_tabs.setCurrentIndex(index)

    def toggle_theme(self):
        dark = self.theme_button.isChecked()
        theme_settings().setValue('dark', dark)
        apply_theme(QApplication.instance(), dark)
        spell_engine().changed.emit()
        caption = 'Светлая тема' if dark else 'Тёмная тема'
        if self.sidebar.width() < 100:
            self.theme_button.setProperty('fullText', caption)
            self.theme_button.setToolTip(caption)
            self.theme_button.setText(caption[:1])
        else:
            self.theme_button.setText(caption)

    def saved_filter_key(self):
        login = self.store.login if self.store else getpass.getuser()
        return 'board_filters/' + login.encode('utf-8').hex()

    def saved_filters(self):
        try:
            return json.loads(theme_settings().value(self.saved_filter_key(), '{}', type=str))
        except ValueError:
            return {}

    def saved_filter_menu(self):
        if not self.store:
            return
        menu = QMenu(self)
        menu.addAction('Сохранить текущие…', lambda: self.name_saved_filter())
        for name, values in sorted(self.saved_filters().items(), key=lambda pair: pair[0].casefold()):
            entry = menu.addMenu(name.replace('&', '&&'))
            entry.addAction('Применить', lambda checked=False, values=values: self.apply_saved_filter(values))
            entry.addAction('Переименовать…', lambda checked=False, name=name: self.name_saved_filter(name))
            entry.addAction('Удалить…', lambda checked=False, name=name: self.delete_saved_filter(name))
        anchor = self.saved_filter_button
        menu.exec_(anchor.mapToGlobal(anchor.rect().bottomLeft()))

    def name_saved_filter(self, previous=None):
        presets = self.saved_filters()
        caption = 'Переименовать набор' if previous else 'Сохранить набор'
        name, ok = QInputDialog.getText(self, caption, 'Название набора', text=previous or '')
        name = name.strip()
        if not ok or name == previous:
            return
        if not name or name in presets:
            QMessageBox.information(self, caption, 'Введите новое непустое название набора.')
            return
        if previous:
            values = presets.pop(previous)
        else:
            values = self.filters()
            values.update(quick=self.quick.currentIndex(), assignee=self.assignee_filter.currentData())
        presets[name] = values
        self.write_saved_filters(presets)

    def delete_saved_filter(self, name):
        if QMessageBox.question(self, 'Удалить набор', 'Удалить набор «{}»?'.format(name),
                                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes:
            presets = self.saved_filters()
            presets.pop(name, None)
            self.write_saved_filters(presets)

    def write_saved_filters(self, presets):
        settings = theme_settings()
        settings.setValue(self.saved_filter_key(), json.dumps(presets, ensure_ascii=False))
        settings.sync()
        if settings.status() != settings.NoError:
            self.status.setText('Не удалось записать наборы фильтров в локальные настройки.')

    def apply_saved_filter(self, values):
        controls = (self.search, self.quick, self.priority_filter, self.assignee_filter,
                    self.label_filter, self.archive_filter, self.due_from, self.due_to)
        blockers = [QSignalBlocker(control) for control in controls]
        self.search.setText(values['query'])
        self.quick.setCurrentIndex(values['quick'])
        self.priority_filter.setCurrentIndex(max(0, self.priority_filter.findText(values['priority'])))
        self.archive_filter.setChecked(values['archived'])
        self.due_from.set_value(values['due_from'])
        self.due_to.set_value(values['due_to'])
        missing = []
        for combo, key in ((self.assignee_filter, 'assignee'), (self.label_filter, 'label')):
            value = values.get(key) or ''
            index = combo.findData(value)
            if value and index < 0:
                missing.append(value)
            combo.setCurrentIndex(max(0, index))
        del blockers
        self.filters_changed()
        self.advanced_filters.show()
        self.filter_message.setText('Недоступны на этой доске, пропущены: ' + ', '.join(missing) if missing else '')
        self.filter_message.setVisible(bool(missing))

    def reset_filters(self):
        self.filter_message.hide()
        self.search.clear()
        for combo in (self.quick, self.priority_filter, self.assignee_filter, self.label_filter):
            combo.setCurrentIndex(0)
        self.archive_filter.setChecked(False)
        self.due_from.set_value(None)
        self.due_to.set_value(None)
        self.filters_changed()

    def new_task_in_column(self, column_id):
        if self.project_sections_board == self.board_id:
            return
        if not getattr(self, 'permissions', {}).get('create', False):
            self.status.setText('Создавать задачи могут менеджер и администратор')
            return
        if not self.store or not self.board_id:
            return
        self.open_task({'board_id': self.board_id, 'column_id': column_id, 'title': ''})

    def set_access_restricted(self, restricted):
        if restricted == self.access_restricted:
            return
        self.access_restricted = restricted
        self.centralWidget().setVisible(not restricted)
        if restricted:
            self.redmine_client = None
            self.redmine_login = None
            self.reset_bugzilla_session()
            self.bugzilla_cards.clear()
            self.render_bugzilla_cards()
            self.last_snapshot = None
            self.generation += 1
            self.messenger.deactivate()
            self.hidden_task_dialogs = {dialog for dialog in self.dialogs if dialog.isVisible()}
            for dialog in self.findChildren(QDialog):
                if dialog.isVisible():
                    dialog.hide()
            self.statusBar().showMessage(str(AccessRestricted()))
            self.statusBar().show()
        else:
            self.statusBar().hide()
            self.render_bugzilla_cards()
            visible = {self.board_tabs.tabData(i) for i in range(self.board_tabs.count())}
            for dialog in self.hidden_task_dialogs & self.dialogs:
                if dialog.data['board_id'] in visible:
                    dialog.show()
            self.hidden_task_dialogs.clear()
            if not self.messenger.active:
                self.messenger.activate()

    def connect_store(self):
        if not self.folder:
            self.status.setText(self.installation_error or
                                'Администратору нужно указать папку базы в «Настройки → Подключение…».')
            return
        if self.loading:
            return
        self.loading = True
        def done(store, error):
            self.loading = False
            if isinstance(error, AccessRestricted):
                self.set_access_restricted(True)
                return
            if isinstance(error, DatabaseTooNew):
                self.status.setText(str(error))
                return
            if error:
                self.status.setText('Общая папка недоступна: ' + error_text(error) + '. Проверьте путь в «Настройки → Подключение…» и нажмите «Обновить».')
                return
            self.store = store
            self.notifications.poll()
            self.refresh()
        def connect():
            if self.installation_base is not None and not (Path(self.folder) / 'kanban.db').is_file():
                raise ValueError('В указанной папке нет kanban.db')
            return Store(self.folder)

        self.work(connect, done)

    def filters_changed(self, *args):
        count = sum(bool(v) for v in (self.assignee_filter.currentData(), self.label_filter.currentData(), self.due_from.value(), self.due_to.value()))
        self.filter_toggle.setText('Фильтры ({})'.format(count) if count else 'Фильтры')
        self.generation += 1
        self.debounce.start(300)

    def update_board_badges(self, counts):
        self.board_counts = counts
        for board_id, chat in self.board_chats.items():
            chat.update_unread(counts.get(board_id, {}).get('chat', 0))
        def update_item(item):
            bid = item.data(0, Qt.UserRole)
            if bid is not None:
                badge = counts.get(bid, {'chat': 0, 'tasks': 0, 'total': 0})
                total = badge['total']
                title = item.data(0, Qt.UserRole + 2) or item.text(0)
                tooltip = '{}\nЧат: {} · Задачи: {}'.format(title, badge['chat'], badge['tasks'])
                item.setToolTip(0, tooltip)
            else:
                total = sum(update_item(item.child(index)) for index in range(item.childCount()))
                tooltip = 'Непрочитанных уведомлений: {}'.format(total)
            item.setText(1, '99+' if total > 99 else (str(total) if total else ''))
            item.setToolTip(1, tooltip)
            return total

        for row in range(self.board_tree.topLevelItemCount()):
            update_item(self.board_tree.topLevelItem(row))

    def activate_board_chat(self, board_id, name=''):
        if self.messenger.board_id == board_id:
            if name:
                self.messenger.board_name = name
                self.messenger.title_label.setText("Чат · " + name)
            return
        opened = self.messenger.isVisible()
        self.messenger.deactivate()
        if board_id not in self.board_chats:
            self.board_chats[board_id] = Messenger(self, board_id, name)
        self.messenger = self.board_chats[board_id]
        if not self.access_restricted:
            self.messenger.activate(opened)

    def show_board_chat(self, board_id):
        if self.access_restricted:
            return
        for index in range(self.board_tabs.count()):
            if self.board_tabs.tabData(index) == board_id:
                self.board_tabs.setCurrentIndex(index)
                self.board_id = board_id
                self.activate_board_chat(board_id, self.board_tabs.tabText(index))
                self.messenger.open_chat()
                return
        self.status.setText('Доступ к доске отозван или доска архивирована. Обновите список досок.')
        self.refresh()

    def show_task_notification(self, task_id):
        self.open_personal_task({'id': task_id})

    def select_board_tab(self, index):
        if index < 0:
            return
        self.board_id = self.board_tabs.tabData(index)
        self.activate_board_chat(self.board_id, self.board_tabs.tabText(index))
        self.generation += 1
        self.pages.setCurrentIndex(0)
        self.refresh()

    def open_personal_task(self, task):
        task_id = task['id']
        def done(context, error):
            if error:
                self.status.setText('Не удалось открыть задачу: ' + error_text(error))
                return
            detail, columns = context
            for dialog in self.dialogs:
                if dialog.data.get('id') == task_id and dialog.isVisible():
                    dialog.render_extras(detail)
                    dialog.raise_()
                    dialog.activateWindow()
                    return
            self.show_task_dialog(detail, columns)
        self.work(lambda: self.store.task_context(task_id), done)

    def page_changed(self, index):
        self.generation += 1
        self.refresh()

    def select_board(self, current, previous):
        self.board_id = current.data(Qt.UserRole) if current else None
        self.activate_board_chat(self.board_id, current.text() if current else "")
        self.generation += 1
        self.refresh()

    def filters(self):
        quick = self.quick.currentIndex()
        return {'query': self.search.text(), 'priority': self.priority_filter.currentText() if self.priority_filter.currentIndex() else '', 'archived': self.archive_filter.isChecked(), 'assignee': getpass.getuser() if quick == 1 else (self.assignee_filter.currentData() or ''), 'overdue': quick == 2, 'unassigned': quick == 3, 'label': self.label_filter.currentData() or '', 'due_from': self.due_from.text().strip(), 'due_to': self.due_to.text().strip()}

    def drag_state_changed(self, active):
        self.drag_active = active
        if not active and self.refresh_deferred:
            self.refresh_deferred = False
            self.debounce.start(0)

    def refresh(self):
        if self.drag_active:
            self.refresh_deferred = True
            return
        live_sppi = self.mutating and self.sppi_view_board is not None and self.sppi_view_board == self.board_id
        if self.loading or (not live_sppi and (self.mutating or (self.pending_action and not self.access_restricted))):
            return
        if not self.store:
            self.connect_store()
            return
        f = self.filters()
        page = self.pages.currentIndex()
        view = ('board', 'list', 'timeline', 'statistics', 'personal')[page]
        generation = self.generation
        bid = self.board_id
        if page == 0 and self.project_sections.currentWidget() is self.identification_page and self.project_sections_board == bid:
            view = 'identification'
        elif page == 0 and self.project_sections.currentWidget() is self.bugzilla_page:
            view = 'bugs'
        self.loading = True
        if live_sppi:
            self.sppi_view_dirty = False
        def read():
            return self.store.board_snapshot(bid, f, view)
        def done(result, error):
            self.loading = False
            if live_sppi and (self.sppi_view_dirty or not self.mutating):
                self.debounce.start(0)
            if self.drag_active:
                self.refresh_deferred = True
                return
            if (live_sppi and not self.mutating) or (not live_sppi and (
                    self.mutating or (self.pending_action and not self.access_restricted))):
                return
            if generation != self.generation:
                self.debounce.start(1)
                return
            if isinstance(error, AccessRestricted):
                self.set_access_restricted(True)
                return
            if error:
                self.status.setText('Обновление не выполнено: ' + error_text(error) + '. Можно повторить. Открытые формы сохранены.')
                return
            snapshot = (view, result, dt.date.today().isoformat())
            if snapshot == self.last_snapshot:
                self.redmine_refresh_message = ''
                return
            self.last_snapshot = snapshot
            boards, selected, columns, tasks, totals, catalogs, personal = result
            self.permissions = catalogs.get('permissions', {})
            self.apply_redmine_settings(catalogs.get('redmine'))
            self.apply_bugzilla_settings(catalogs['bugzilla'])
            self.users_button.setEnabled(True)
            self.create_board_button.setEnabled(True)
            if page == 4:
                self.my_tasks.update_data(personal)
            elif page == 3:
                self.statistics.update_data(tasks, columns)
            elif page == 2:
                self.gantt_view.update_data(tasks, columns)
            elif page == 1:
                self.list_view.update_data(tasks, columns)
            structure = [(b['id'], b['name'], b['personal']) for b in boards]
            if structure != getattr(self, 'tree_structure', None):
                self.tree_structure = structure
                self.rebuild_board_tree(structure)
                for stored_board in self.bugzilla_cards.keys() - self.board_items.keys():
                    del self.bugzilla_cards[stored_board]
                for stored_board in self.bugzilla_links.keys() - self.board_items.keys():
                    del self.bugzilla_links[stored_board]
            self.create_project_item.setDisabled(not self.permissions.get('create_board', False))
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
            if bid != selected:
                self.filter_message.hide()
            self.update_catalog_filters(catalogs)
            if page in (0, 1, 2) and f != self.filters():
                self.debounce.start(1)
            self.board_id = selected
            self.activate_board_chat(selected, next((b["name"] for b in boards if b["id"] == selected), ""))
            for chat_board_id, chat in self.board_chats.items():
                if chat_board_id is not None and not any(b["id"] == chat_board_id for b in boards):
                    chat.deactivate()
                    chat.messages.clear()
                    chat.avatars.clear()
                    chat.stickers.clear()
                    chat.missing_stickers.clear()
                    chat.timeline.clear()
                    chat.markup = None
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
            self.update_board_badges(self.board_counts)
            self.update_navigation()
            self.columns_data = columns
            if view == 'identification':
                self.identification_page.show_cards(selected, catalogs.get('identification_project'), tasks)
            elif view == 'bugs' and selected is not None:
                self.bugzilla_links[selected] = catalogs['linked_bug_ids']
                if tasks is None:
                    self.bugzilla_cards.pop(selected, None)
                else:
                    self.bugzilla_cards[selected] = tasks['cards']
                self.render_bugzilla_cards()
            elif view == 'board':
                self.render_board(selected, columns, tasks, totals)
            self.set_access_restricted(False)
            if self.mutating:
                return
            if self.redmine_refresh_message:
                self.status.setText(self.redmine_refresh_message)
                self.redmine_refresh_message = ''
            else:
                self.status.clear()
        self.work(read, done)

    def rebuild_board_tree(self, structure):
        self.board_tree.clear()
        self.board_items.clear()
        work = QTreeWidgetItem(self.board_tree, ['Рабочие доски'])
        projects = QTreeWidgetItem(self.board_tree, ['Проекты'])
        private = QTreeWidgetItem(self.board_tree, ['Личное'])
        private.setToolTip(0, 'Мои задачи и доски, видимые только вам')
        for folder in (work, projects, private):
            folder.setExpanded(True)

        self.my_tasks_item = QTreeWidgetItem(private, ['Мои задачи'])
        self.my_tasks_item.setFirstColumnSpanned(True)
        self.my_tasks_item.setData(0, Qt.UserRole + 1, 'my_tasks')
        create = QTreeWidgetItem(private, ['＋ Создать доску'])
        create.setFirstColumnSpanned(True)
        create.setData(0, Qt.UserRole + 1, 'create_personal')
        create.setToolTip(0, 'Создать личную доску — доступна только вам')

        groups = {}
        for board_id, name, personal_board in structure:
            project, separator, title = name.partition(' / ')
            if personal_board:
                parent, title = private, name
            elif separator:
                if project not in groups:
                    groups[project] = QTreeWidgetItem(projects, [project])
                    groups[project].setExpanded(True)
                parent = groups[project]
            else:
                parent, title = work, name
            item = QTreeWidgetItem(parent, [title])
            item.setData(0, Qt.UserRole, board_id)
            item.setData(0, Qt.UserRole + 2, name)
            self.board_items[board_id] = item

        self.create_project_item = QTreeWidgetItem(projects, ['＋ Создать доску'])
        self.create_project_item.setFirstColumnSpanned(True)
        self.create_project_item.setData(0, Qt.UserRole + 1, 'create_project')
        self.create_project_item.setToolTip(0, 'Создание доски в проекте доступно менеджеру и администратору')

        archive = QTreeWidgetItem(private, ['Архив личных досок…'])
        archive.setFirstColumnSpanned(True)
        archive.setData(0, Qt.UserRole + 1, 'personal_archive')

    def render_board(self, board_id, columns, tasks, totals):
        board = None
        structure = (board_id, columns, self.project_sections_board == board_id)
        if structure != getattr(self, 'card_structure', None):
            self.card_structure = structure
            self.expanded_task_ids.clear()
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
                label = QLabel(column['name'])
                label.setTextFormat(Qt.PlainText)
                label.setWordWrap(True)
                label.setObjectName('ColumnTitle')
                label.setProperty('tone', column_index % 5)
                header_row = QHBoxLayout()
                label.setText(column['name'])
                header_row.addWidget(label, 1)
                counter = QLabel()
                counter.setObjectName('CountBadge')
                header_row.addWidget(counter)
                layout.addLayout(header_row)
                add_task = button('Добавить задачу', lambda checked=False, cid=column['id']: self.new_task_in_column(cid), layout)
                add_task.setObjectName('AddColumnTask')
                add_task.setVisible(board_id != self.project_sections_board)
                add_task.setCursor(Qt.PointingHandCursor)
                can_collapse_ti = structure[2] and redmine.status_key(column['name']) == 'ти открыто'
                cards = Cards(column['id'], self.expanded_task_ids,
                              compact_ti_ids=self.compact_ti_ids if can_collapse_ti else None)
                cards.counter = counter
                cards.add_task = add_task
                cards.details_requested.connect(self.load_card_details)
                cards.redmine_refresh_requested.connect(self.refresh_ti_card)
                cards.itemClicked.connect(lambda item: self.open_task(item.data(Qt.UserRole)))
                cards.itemSelectionChanged.connect(lambda cards=cards: self.exclusive_selection(cards))
                cards.moved.connect(self.move_task)
                cards.dragging.connect(self.drag_state_changed)
                cards.setContextMenuPolicy(Qt.CustomContextMenu)
                cards.customContextMenuRequested.connect(lambda point, cards=cards: self.card_menu(cards, point))
                layout.addWidget(cards)
                row.addWidget(panel, 0, Qt.AlignTop)
                self.card_lists.append(cards)
            if not columns:
                row.addWidget(QLabel('Создайте доску, чтобы добавить задачи.'))
        tasks_by_column = {}
        for task in tasks:
            tasks_by_column.setdefault(task['column_id'], []).append(task)
        for column, cards in zip(columns, self.card_lists):
            count = totals.get(column['id'], 0)
            limit = column['wip_limit']
            warning = bool(limit and count > limit)
            cards.counter.setText(str(count) + (' / ' + str(limit) if limit else '') + (' Лимит превышен' if warning else ''))
            cards.counter.setToolTip('Лимит превышен' if warning else 'Количество задач')
            cards.add_task.setEnabled(self.permissions.get('create', False))
        update_cards({cards.column_id: cards for cards in self.card_lists}, tasks_by_column,
                     self.task_notice_counts, self.expanded_task_ids)
        if board is not None:
            self.scroll.setWidget(board)

    def load_card_details(self, item):
        task = item.data(Qt.UserRole)
        key = (task['id'], task['version'])
        pending = getattr(self, 'pending_card_details', set())
        self.pending_card_details = pending
        if key in pending:
            return
        pending.add(key)
        def done(details, error):
            pending.discard(key)
            lists = self.card_lists + list(self.my_tasks.card_lists.values())
            for cards in lists:
                for row in range(cards.count()):
                    current = cards.item(row)
                    data = current.data(Qt.UserRole)
                    if (data['id'], data['version']) != key or not current.data(EXPANDED_ROLE):
                        continue
                    if error:
                        current.setData(DETAILS_ROLE, dict(data, details_error=True, description='Подробности недоступны: ' + error_text(error)))
                    elif details['version'] == key[1]:
                        current.setData(DETAILS_ROLE, dict(data, **details))
                    else:
                        self.refresh()
                    cards.doItemsLayout()
                    cards.update_card_height()
        self.work(lambda: self.store.card_details(task['id']), done)

    def update_catalog_filters(self, catalogs):
        for combo, options, placeholder in [
            (self.assignee_filter, [(u['login'], u['display_name']) for u in sorted(catalogs['users'], key=lambda u: u['display_name'].casefold())], 'Все исполнители'),
            (self.label_filter, [(l['name'], l['name']) for l in sorted(catalogs['labels'], key=lambda l: l['name'].casefold())], 'Все метки')
        ]:
            selected = combo.currentData()
            if [(combo.itemData(i), combo.itemText(i)) for i in range(combo.count())] == [('', placeholder)] + options:
                continue
            combo.blockSignals(True)
            combo.clear()
            combo.addItem(placeholder, '')
            for value, label in options:
                combo.addItem(label, value)
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
        if task.get('redmine_id'):
            self.show_redmine_task(task)
            return
        if task.get('id') and 'history' not in task:
            self.open_personal_task(task)
        else:
            self.show_task_dialog(task, self.columns_data)

    def show_task_dialog(self, task, columns):
        if self.access_restricted:
            return
        d = TaskDialog(self.store, columns, task, self)
        self.dialogs.add(d)
        d.changed.connect(self.refresh)
        d.destroyed.connect(lambda: self.dialogs.discard(d))
        d.show()

    def show_redmine_task(self, task):
        if self.access_restricted:
            return

        def done(tasks, error):
            if error:
                self.status.setText(error_text(error))
                return
            if self.access_restricted:
                return
            current = next((entry for entry in tasks if entry['issue_id'] == task['redmine_id']), None)
            if current is None:
                return
            dialog = QDialog(self)
            progress = current['kris_progress']
            label = redmine.issue_label(current['tracker_name'], current['issue_id'], progress)
            dialog.setWindowTitle('{} #{} · Redmine'.format(label, current['issue_id']))
            available = self.screen().availableGeometry()
            width = min(1200, available.width() - 24)
            height = min(760, width * 2 // 3, available.height() - 48)
            dialog.setMinimumWidth(min(430, width))
            layout = QVBoxLayout(dialog)
            scroll = QScrollArea()
            content = QWidget()
            details = QVBoxLayout(content)
            details.setContentsMargins(0, 0, 4, 0)
            heading = QLabel(current['title'])
            heading.setObjectName('Heading')
            heading.setTextFormat(Qt.PlainText)
            heading.setWordWrap(True)
            details.addWidget(heading)
            form = QFormLayout()
            references = current['issue_references']
            fields = [('Статус задачи', current['status']),
                      ('Основание', ', '.join(references.get('basis', [])) or '—'),
                      ('Баги', ', '.join(references.get('bugs', [])) or '—'),
                      ('КРИС ТИ', '\n'.join([redmine.kris_progress_text(progress), *redmine.kris_coding_lines(progress)]))]
            if references.get('ti_responsible'):
                fields.insert(1, ('Ответственный за выпуск ТИ', ', '.join(
                    person['name'] for person in references['ti_responsible'])))
            if current['approvals']:
                fields.append(('Согласование отделов', '\n'.join(
                    '{}: {}'.format(entry['department'], entry['status']) for entry in current['approvals'])))
            for label, value in fields:
                field = QLabel(value)
                field.setTextFormat(Qt.PlainText)
                field.setWordWrap(True)
                form.addRow(label, field)
            subtasks = (progress or {}).get('subtasks')
            if subtasks:
                table = QTableWidget(len(subtasks), 3)
                table.setAccessibleName('Согласование приложений')
                table.setHorizontalHeaderLabels(['Название', 'Назначена', 'Статус'])
                table.setEditTriggers(QTableWidget.NoEditTriggers)
                table.setSelectionBehavior(QTableWidget.SelectRows)
                table.setSelectionMode(QTableWidget.SingleSelection)
                table.setAlternatingRowColors(True)
                table.setWordWrap(True)
                table.setTextElideMode(Qt.ElideNone)
                table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
                table.setSizeAdjustPolicy(QTableWidget.AdjustToContents)
                table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
                header = table.horizontalHeader()
                header.setSectionResizeMode(0, QHeaderView.Stretch)
                table.setColumnWidth(1, 114)
                table.setColumnWidth(2, 122)
                vertical = table.verticalHeader()
                vertical.hide()
                vertical.setSectionResizeMode(QHeaderView.ResizeToContents)
                vertical.sectionResized.connect(table.updateGeometry)
                for row, subtask in enumerate(subtasks):
                    for column, key in enumerate(('title', 'assignee', 'status')):
                        table.setItem(row, column, QTableWidgetItem(subtask[key] or '—'))

                def scroll_to_subtask(item, _previous):
                    if item is not None:
                        rect = table.visualItemRect(item)
                        y = table.viewport().mapTo(content, rect.center()).y()
                        scroll.ensureVisible(scroll.horizontalScrollBar().value(), y, 0, rect.height() // 2)

                table.currentItemChanged.connect(scroll_to_subtask)
                section = QWidget()
                section.setMaximumWidth(680)
                section_layout = QVBoxLayout(section)
                section_layout.setContentsMargins(0, 0, 0, 0)
                caption = QLabel('Согласование приложений')
                caption.setObjectName('fieldLabel')
                section_layout.addWidget(caption)
                section_layout.addWidget(table)
                centered = QHBoxLayout()
                centered.addStretch()
                centered.addWidget(section, 1)
                centered.addStretch()
                form.addRow(centered)
            else:
                message = 'Обновите ТИ по #ID, чтобы загрузить подзадачи.' if progress and subtasks is None else '—'
                message_label = QLabel(message)
                message_label.setWordWrap(True)
                form.addRow('Согласование приложений', message_label)
            form.addRow('Приложения', QLabel('—'))
            style_form(form)
            details.addLayout(form)
            if references.get('children'):
                details.addWidget(QLabel('Подзадачи Redmine'))
                children = QTextEdit()
                children.setReadOnly(True)
                children.setAccessibleName('Подзадачи Redmine')
                children.setPlainText('\n'.join('#{} {} · {}'.format(child['id'], child['title'], child['status'] or '—')
                                                for child in references['children']))
                children.setMaximumHeight(140)
                details.addWidget(children)
            details.addStretch()
            scroll.setWidgetResizable(True)
            scroll.setWidget(content)
            layout.addWidget(scroll, 1)
            if self.can_unlink_redmine_task(current):
                button('Убрать с доски', lambda: self.unlink_redmine_task(current, dialog), layout)
            button('Закрыть', dialog.accept, layout)
            dialog.resize(width, height)
            dialog.exec_()
            dialog.deleteLater()

        self.work(lambda: self.store.redmine_tasks(task['board_id']), done)

    def can_unlink_redmine_task(self, task):
        return (not self.access_restricted and task['board_id'] == self.board_id
                and self.permissions.get('create', False) and task.get('source') in ('manual', 'legacy'))

    def unlink_redmine_task(self, task, dialog=None):
        if not self.can_unlink_redmine_task(task):
            return
        if self.mutating or self.pending_action:
            self.status.setText('Дождитесь завершения текущей операции')
            return
        board_id, issue_id = task['board_id'], task['issue_id']
        board = self.board_items[board_id].text(0)
        confirmation = QMessageBox.question(
            dialog or self, 'Убрать ТИ с доски?',
            'Убрать карточку #{} с доски «{}»? Задача останется в Redmine.'.format(issue_id, board),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if confirmation != QMessageBox.Yes:
            return
        if dialog is not None:
            dialog.accept()
        self.mutate(lambda rid: self.store.unlink_redmine_issue(board_id, issue_id, task['revision'], rid),
                    'Карточка ТИ убрана с доски')

    def redmine_operation(self, operation, success, *, busy_message='Сохранение…'):
        if self.access_restricted or self.mutating or self.pending_action:
            self.status.setText('Дождитесь завершения текущей операции')
            return
        if not self.redmine_url:
            self.status.setText('Администратору нужно указать адрес Redmine в «Настройки → Подключение…».')
            return
        client = self.redmine_client
        login_name = self.redmine_login or self.store.login
        url = self.redmine_url
        statuses = self.redmine_statuses
        credentials = None
        if client is None:
            dialog = QDialog(self)
            dialog.setWindowTitle('Вход в Redmine')
            layout = QVBoxLayout(dialog)
            form = QFormLayout()
            login = QLineEdit(self.store.login)
            password = QLineEdit()
            password.setEchoMode(QLineEdit.Password)
            form.addRow('Логин Redmine', login)
            form.addRow('Пароль Redmine', password)
            layout.addLayout(form)
            server = QLabel(url)
            server.setTextFormat(Qt.PlainText)
            server.setWordWrap(True)
            layout.insertWidget(0, server)
            layout.addWidget(QLabel('Вход сохраняется до выхода из TeamSync.'))
            actions = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
            actions.button(QDialogButtonBox.Ok).setText('Войти')
            actions.button(QDialogButtonBox.Cancel).setText('Отмена')
            actions.accepted.connect(dialog.accept)
            actions.rejected.connect(dialog.reject)
            layout.addWidget(actions)
            password.setFocus()
            if dialog.exec_() == QDialog.Accepted:
                credentials = login.text().strip(), password.text()
            password.clear()
            dialog.deleteLater()
            if credentials is None:
                return
            if not all(credentials):
                self.status.setText('Введите логин и пароль Redmine')
                return
            login_name = credentials[0]

        def apply(rid):
            nonlocal client, credentials
            current = self.store.redmine_settings()
            if current is not None and (current['url'] != url or current['statuses'] != statuses):
                credentials = None
                raise redmine.RedmineError('Настройки Redmine изменились. Повторите операцию после обновления доски.')
            if client is None:
                try:
                    client = redmine.connect(url, *credentials)
                finally:
                    credentials = None
            try:
                return {'redmine_client': client, 'redmine_login': login_name, 'redmine_errors': operation(client, rid, statuses)}
            except redmine.RedmineAuthError:
                raise
            except ValueError as error:
                return {'redmine_client': client, 'redmine_login': login_name, 'redmine_errors': [str(error)]}

        self.mutate(apply, success, busy_message=busy_message)

    def redmine_project_id(self, project):
        return installation.load_redmine_project_id(self.redmine_config_base(), project)

    def redmine_config_base(self):
        return self.installation_base or Path(__file__).resolve().parent.parent

    def edit_basis_project(self):
        board_id = self.board_id
        if not self.store:
            return
        self.edit_redmine_project('Проект поиска оснований', lambda: self.store.redmine_basis_project(board_id),
                                  lambda value, rid: self.store.set_redmine_basis_project(board_id, value, rid))

    def edit_sppi_project(self):
        board_id = self.board_id
        if not self.store:
            return
        self.edit_redmine_project('Проект СППИ', lambda: self.store.redmine_sppi_project(board_id),
                                  lambda value, rid: self.store.set_redmine_sppi_project(board_id, value, rid))

    def edit_redmine_project(self, title, load, persist):
        board_id = self.board_id
        if not self.store or board_id is None or self.project_sections_board != board_id or not self.permissions.get('admin'):
            return

        def done(project, error):
            if error:
                self.status.setText(error_text(error))
                return
            if self.board_id != board_id:
                return
            value, accepted = QInputDialog.getText(
                self, title, 'Название проекта из all_project.json (пусто — отключить)', text=project)
            if not accepted or value.strip() == project:
                return
            value = value.strip()

            def save(rid):
                if value:
                    self.redmine_project_id(value)
                return persist(value, rid)

            self.mutate(save, 'Настройка сохранена')

        self.work(load, done)

    def read_project_kris_progress(self, client, issue_ids, project, **options):
        try:
            project_id = self.redmine_project_id(project)
        except ValueError as error:
            return {}, [str(error)]
        return redmine.read_kris_progress(client, issue_ids, project_id, **options)

    def read_project_basis_statuses(self, client, board_id, issues, **options):
        numbers = {number for issue in issues for number in issue['issue_references'].get('basis', [])
                   if redmine.basis_number(number)}
        if not numbers:
            return '', {}, []
        project = self.store.redmine_basis_project(board_id)
        if not project:
            return '', {}, []
        try:
            return project, redmine.read_basis_statuses(client, self.redmine_project_id(project), numbers, **options), []
        except redmine.RedmineAuthError:
            raise
        except ValueError as error:
            return project, {}, [str(error)]

    def refresh_identification(self):
        board_id = self.board_id
        if not self.store or board_id is None or self.project_sections_board != board_id:
            return

        def update(client, rid, statuses):
            project = self.store.redmine_basis_project(board_id)
            if not project:
                raise ValueError('Укажите проект поиска оснований в настройках доски.')
            cards = redmine.read_identification_cards(client, self.redmine_project_id(project))
            self.store.save_identification_cards(board_id, project, cards, rid)
            return []

        self.redmine_operation(update, 'Карточки ИД обновлены')

    def refresh_identification_card(self, previous):
        board_id = self.board_id
        if not self.store or board_id is None or self.project_sections_board != board_id:
            return
        project = self.identification_page.snapshot[1]
        if not project:
            return
        result = None

        def update(client, rid, statuses):
            nonlocal result
            if self.store.redmine_basis_project(board_id) != project:
                raise ValueError('Проект поиска оснований изменился. Обновите ИД.')
            if result is None:
                result = (redmine.read_identification_card(client, self.redmine_project_id(project), previous),)
            self.store.update_identification_card(board_id, project, previous, result[0], rid)
            return []

        self.redmine_operation(update, 'Карточка ИД обновлена', busy_message='Обновление карточки ИД…')

    def show_sppi_progress(self, board_id, message):
        if message:
            if self.access_restricted or board_id != self.board_id:
                return
            lines = [line.full_text for line in self.sppi_log_lines
                     if self.sppi_log_board == board_id and getattr(line, 'full_text', '')]
            self.sppi_log_board = board_id
            lines = [*lines, message][-len(self.sppi_log_lines):]
        else:
            if self.sppi_log_board != board_id:
                return
            self.sppi_log_board = None
            lines = []
        for index, line in enumerate(self.sppi_log_lines):
            line.setText(lines[index] if index < len(lines) else '')
            line.setVisible(index < len(lines))
        self.sppi_log.setVisible(bool(lines))

    def refresh_workspace(self):
        if self.pages.currentIndex() == 0 and self.project_sections.currentWidget() is self.bugzilla_page:
            self.refresh()
            return
        if self.pages.currentIndex() == 0 and self.project_sections.currentWidget() is self.identification_page:
            self.refresh_identification()
            return
        if not self.store or self.project_sections_board is None or self.project_sections_board != self.board_id:
            self.refresh()
            return
        self.sync_project_ti()

    def import_sppi_ti(self):
        if not self.store or self.board_id is None or self.project_sections_board != self.board_id:
            return
        if not self.permissions.get('create'):
            self.status.setText('Выгружать ТИ из СППИ могут менеджер и администратор')
            return
        board_id = self.board_id
        project = self.board_items[board_id].parent().text(0)
        board_name = self.board_items[board_id].text(0)

        def update(client, rid, statuses):
            try:
                return self.stream_sppi_tasks(client, board_id, board_name, project, statuses)
            finally:
                self.sppi_progress.emit(board_id, '')

        self.redmine_operation(update, 'Выгрузка из Redmine завершена', busy_message='Выгрузка задач из Redmine…')

    def refresh_sppi_board(self, board_id):
        if self.mutating and not self.access_restricted:
            self.sppi_view_board = board_id
            self.sppi_view_dirty = True
            if self.board_id == board_id:
                self.debounce.start(0)

    def stream_sppi_tasks(self, client, board_id, board_name, project, statuses):
        sppi_project = self.store.redmine_sppi_project(board_id)
        if not sppi_project:
            raise ValueError('Укажите проект СППИ в настройках.')
        employees = installation.load_redmine_employees(self.redmine_config_base())
        source_id = self.redmine_project_id(sppi_project)
        linked = {task['issue_id']: task for task in self.store.redmine_tasks(board_id)}
        columns = {column['id']: column['name'] for column in self.store.columns(board_id)}
        errors = []
        attempted = set()
        kris_cache, basis_cache = {}, {}
        responsible_cache = {}

        def save(issue_id, parent_status=None, **options):
            attempted.add(issue_id)
            try:
                issue = redmine.read_issue(client, issue_id, skip_closed=True, responsible_cache=responsible_cache, **options)
            except redmine.RedmineAuthError:
                raise
            except redmine.RedmineError as error:
                errors.append(str(error))
                return
            if issue is None:
                return
            updates, warnings = self.prepare_ti_updates(
                client, board_id, project, statuses, linked, [(issue_id, issue)],
                columns=columns, parent_status=parent_status, kris_options={'cache': kris_cache},
                basis_options={'cache': basis_cache})
            errors.extend(warnings)
            failures = self.store.update_redmine_tasks(board_id, updates, sppi_project=sppi_project)
            errors.extend(failures)
            if not failures:
                self.sppi_card_saved.emit(board_id)

        candidates = redmine.iter_sppi_issues(
            client, source_id, board_name, employees,
            on_issue=lambda message: self.sppi_progress.emit(board_id, message))
        try:
            for issue_id, data, parent_status in candidates:
                save(issue_id, parent_status, data=data)
        except redmine.RedmineAuthError:
            raise
        except redmine.RedmineError as error:
            errors.append(str(error))
        for issue_id, task in linked.items():
            if issue_id not in attempted and not redmine.ti_is_closed(task['status']):
                save(issue_id)
        return list(dict.fromkeys(errors))

    def prepare_ti_updates(self, client, board_id, project, statuses, linked, issues, *, columns,
                           parent_status=None, kris_options=None, basis_options=None):
        progress, warnings = self.read_project_kris_progress(
            client, (issue_id for issue_id, _ in issues), project, **(kris_options or {}))
        basis_project, basis_statuses, basis_warnings = self.read_project_basis_statuses(
            client, board_id, (issue for _, issue in issues), **(basis_options or {}))
        updates = []
        column_names = {redmine.status_key(name) for name in columns.values()}
        for issue_id, issue in issues:
            task = linked.get(issue_id, {})
            target = redmine.column_name(issue['status'], statuses)
            references = redmine.with_basis_statuses(issue['issue_references'], basis_project, basis_statuses)
            if redmine.status_key(target) not in column_names:
                fallback = redmine.column_name(parent_status, statuses) if parent_status else ''
                if fallback and redmine.status_key(fallback) in column_names:
                    target = fallback
                elif task.get('source') == 'sppi' and references.get('parent_id'):
                    target = columns.get(task['column_id'], target)
            updates.append((issue_id, task.get('revision'), issue['title'], issue['status'], target,
                            issue['tracker_name'], issue['approvals'], references,
                            progress.get(issue_id, task.get('kris_progress'))))
        return updates, warnings + basis_warnings

    def refresh_ti_card(self, task):
        if task['board_id'] == self.board_id == self.project_sections_board:
            self.sync_project_ti(task['redmine_id'])

    def sync_project_ti(self, issue_id=None):
        board_id = self.board_id
        if not self.redmine_url:
            self.refresh()
            return
        project = self.board_items[board_id].parent().text(0)
        snapshot = None

        def prepare(client, statuses):
            issues, errors = [], []
            linked = {task['issue_id']: task for task in self.store.redmine_tasks(board_id, issue_id=issue_id)}
            if issue_id is not None and not linked:
                return [], ['Карточка ТИ уже убрана с доски.']
            responsible_cache = {}
            self.sppi_progress.emit(board_id, '')
            for linked_id in linked:
                try:
                    issue = redmine.read_issue(client, linked_id, responsible_cache=responsible_cache)
                except redmine.RedmineAuthError:
                    raise
                except redmine.RedmineError as error:
                    errors.append(str(error))
                    continue
                issues.append((linked_id, issue))
            if not issues:
                return [], errors
            columns = {column['id']: column['name'] for column in self.store.columns(board_id)}
            updates, warnings = self.prepare_ti_updates(client, board_id, project, statuses, linked, issues, columns=columns)
            return updates, errors + warnings

        def update(client, rid, statuses):
            nonlocal snapshot
            if snapshot is None:
                snapshot = prepare(client, statuses)
            updates, errors = snapshot
            if updates:
                return errors + self.store.update_redmine_tasks(board_id, updates, request_id=rid)
            return errors

        success = 'Добавленные ТИ обновлены' if issue_id is None else f'Карточка ТИ #{issue_id} обновлена'
        self.redmine_operation(update, success, busy_message='Обновление ТИ по #ID…')

    def add_redmine_task(self):
        if not self.redmine_url:
            self.status.setText('Администратору нужно указать адрес Redmine в «Настройки → Подключение…».')
            return
        board_id = self.board_id
        project = self.board_items[board_id].parent().text(0)
        value, accepted = QInputDialog.getText(self, 'Добавить задачу Redmine', 'Номер задачи в Redmine')
        if not accepted:
            return
        value = value.strip()
        if not value.isascii() or not value.isdecimal() or len(value) > 19 or not 0 < int(value) <= 2**63 - 1:
            self.status.setText('Введите положительный номер задачи Redmine')
            return
        issue_id = int(value)

        snapshot = None

        def add(client, rid, statuses):
            nonlocal snapshot
            if snapshot is None:
                issue = redmine.read_issue(client, issue_id)
                progress, warnings = self.read_project_kris_progress(client, [issue_id], project)
                basis_project, basis_statuses, basis_warnings = self.read_project_basis_statuses(client, board_id, [issue])
                references = redmine.with_basis_statuses(issue['issue_references'], basis_project, basis_statuses)
                snapshot = issue, progress.get(issue_id), references, warnings + basis_warnings
            issue, progress, references, warnings = snapshot
            target = redmine.column_name(issue['status'], statuses)
            self.store.link_redmine_issue(board_id, issue_id, issue['title'], issue['status'], target, rid,
                                          tracker_name=issue['tracker_name'], approvals=issue['approvals'],
                                          issue_references=references, kris_progress=progress)
            return warnings

        self.redmine_operation(add, 'Задача Redmine добавлена')

    def new_task(self):
        if not getattr(self, 'permissions', {}).get('create', False):
            self.status.setText('Создавать задачи могут менеджер и администратор')
            return
        if not self.board_id or not self.columns_data:
            self.status.setText('Сначала создайте или выберите доску')
            return
        if self.project_sections_board == self.board_id:
            if self.pages.currentIndex() == 0 and self.project_sections.currentIndex() == 0:
                self.add_redmine_task()
            return
        self.open_task({'board_id': self.board_id, 'column_id': self.columns_data[0]['id']})

    def mutate(self, fn, success='Сохранено', retry=False, *, busy_message='Сохранение…'):
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
        self.update_board_controls()
        self.status.setText(busy_message)
        def done(result, error):
            self.mutating = False
            live_sppi = self.sppi_view_board is not None
            self.sppi_view_board = None
            self.sppi_view_dirty = False
            self.retry_button.setVisible(bool(error) and not isinstance(error, (Conflict, ValueError)))
            if error:
                if isinstance(error, redmine.RedmineAuthError):
                    self.redmine_client = None
                self.status.setText(error_text(error) if isinstance(error, ValueError) else
                                    error_text(error) + '. При сетевой ошибке нажмите «Повторить запись».')
                if isinstance(error, (Conflict, ValueError)):
                    self.pending_action = None
                if isinstance(error, Conflict):
                    self.open_task(error.current)
                if live_sppi and not self.pending_action:
                    self.refresh()
            else:
                self.pending_action = None
                if isinstance(result, dict) and 'select_board' in result:
                    self.board_id = result['select_board']
                    self.pages.setCurrentIndex(0)
                if isinstance(result, dict) and 'redmine_client' in result:
                    if not self.access_restricted:
                        self.redmine_client = result['redmine_client']
                        if (self.redmine_login or self.store.login) != result['redmine_login']:
                            self.reset_bugzilla_session()
                        self.redmine_login = result['redmine_login']
                    success_message = '\n'.join(result['redmine_errors']) or success
                    self.redmine_refresh_message = success_message
                else:
                    success_message = success
                self.status.setText(success_message)
                self.refresh()
            self.update_board_controls()
        self.work(lambda: fn(rid), done)

    def retry_mutation(self):
        if self.pending_action:
            self.mutate(self.pending_action[0], self.pending_action[1], retry=True)
        else:
            self.status.setText('Нет незавершённой записи')

    def restore_personal_board(self):
        if not self.store:
            return
        def done(boards, error):
            if error:
                self.status.setText(error_text(error))
                return
            if not boards:
                self.status.setText('В архиве нет личных досок')
                return
            choices = ['{} · №{}'.format(b['name'], b['id']) for b in boards]
            choice, ok = QInputDialog.getItem(self, 'Архив личных досок', 'Восстановить доску:', choices, 0, False)
            if ok:
                bid = boards[choices.index(choice)]['id']
                self.mutate(lambda rid: {'select_board': self.store.restore_personal_board(bid, rid)}, 'Личная доска восстановлена')
        self.work(self.store.archived_personal_boards, done)

    def create_board(self, folder='work'):
        if not self.store or not getattr(self, 'permissions', None):
            self.status.setText('Сначала подключитесь к общей папке')
            return
        can_create_shared = self.permissions.get('create_board', False)
        if folder == 'projects' and not can_create_shared:
            self.status.setText('Создавать доски в проектах могут менеджер и администратор')
            return
        projects = set()
        current_project = ''
        for board_id, name, personal in self.tree_structure:
            project, separator, _ = name.partition(' / ')
            if not personal and separator:
                projects.add(project)
                if board_id == self.board_id:
                    current_project = project
        dialog = BoardCreateDialog(can_create_shared, folder, sorted(projects, key=str.casefold),
                                   current_project, self)
        if dialog.exec_() == QDialog.Accepted:
            name, personal = dialog.values()
            if dialog.folder.currentData() == 'projects':
                columns = [
                    'Проект ТИ',
                    'ТИ согласовано внутри отдела',
                    'ТИ согласовано со смежными отделами',
                    'ТИ согласовано с РП',
                    'ТИ открыто',
                ]
            else:
                columns = ['Бэклог', 'В работе', 'На проверке', 'Готово']
            self.mutate(lambda rid: {'select_board': self.store.board_action(
                name, request_id=rid, column_names=columns, personal=personal)}, 'Доска создана')

    def board_action(self, action):
        if not self.store:
            self.status.setText('Сначала подключитесь к общей папке')
            return
        item = self.boards_list.currentItem()
        if not item:
            return
        bid = item.data(Qt.UserRole)
        if action == 'archive':
            name = item.text()
            if QMessageBox.question(self, 'Архивирование доски', 'Архивировать «' + name + ('»? Её можно будет восстановить из архива личных досок.' if getattr(self, 'permissions', {}).get('personal') else '»? Доска будет скрыта у всех сотрудников.'), QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                return
            self.mutate(lambda rid: self.store.board_action(name, bid, True, rid))
        else:
            name, ok = QInputDialog.getText(self, 'Доска', 'Название (можно Проект / Доска):', text=item.text())
            if ok:
                self.mutate(lambda rid: self.store.board_action(name, bid, request_id=rid))

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
        task = next((cards.item(i).data(Qt.UserRole) for cards in self.card_lists for i in range(cards.count()) if cards.item(i).data(Qt.UserRole)['id'] == task_id), None)
        if not task or task.get('redmine_id'):
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
        self.mutate_task_card(task)

    def card_menu(self, cards, point):
        item = cards.itemAt(point)
        if not item:
            return
        cards.setCurrentItem(item)
        menu = QMenu(self)
        task = item.data(Qt.UserRole)
        if task.get('redmine_id'):
            menu.addAction('Открыть карточку', self.open_selected)
            if self.can_unlink_redmine_task(task):
                menu.addAction('Убрать с доски', lambda: self.unlink_redmine_task(task))
            menu.exec_(cards.viewport().mapToGlobal(point))
            return
        menu.addAction('Переместить в…', self.move_selected)
        menu.addAction('Восстановить' if item.data(Qt.UserRole)['archived'] else 'Архивировать', self.archive_selected).setEnabled(getattr(self, 'permissions', {}).get('create', False))
        self.drag_state_changed(True)
        try:
            menu.exec_(cards.viewport().mapToGlobal(point))
        finally:
            self.drag_state_changed(False)

    def move_selected(self):
        task = self.selected_task()
        if task and task.get('redmine_id'):
            return
        if not task:
            self.status.setText('Выберите задачу')
            return
        names = [c['name'] for c in self.columns_data]
        name, ok = QInputDialog.getItem(self, 'Переместить в…', 'Колонка:', names, 0, False)
        if ok:
            self.move_task(task['id'], self.columns_data[names.index(name)]['id'])

    def copy_selected(self):
        task = self.selected_task()
        if self.project_sections_board is not None and self.project_sections_board == self.board_id:
            return
        if not task:
            self.status.setText('Выберите задачу')
            return
        def done(detail, error):
            if error:
                self.status.setText(error_text(error))
                return
            source, columns = detail
            fields = ('board_id', 'column_id', 'title', 'description', 'priority', 'due_date',
                      'assignees', 'assignee_names', 'labels', 'checklist')
            data = {field: source[field] for field in fields}
            data['title'] += ' (копия)'
            self.show_task_dialog(data, columns)
        self.work(lambda: self.store.task_context(task['id']), done)

    def archive_selected(self):
        task = self.selected_task()
        if task and task.get('redmine_id'):
            return
        if not task:
            self.status.setText('Выберите задачу')
            return
        data = copy.deepcopy(task)
        data['archived'] = not data['archived']
        verb = 'Восстановить' if not data['archived'] else 'Архивировать'
        if QMessageBox.question(self, verb, verb + ' задачу №' + str(data['number']) + '?', QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes:
            self.mutate_task_card(data)

    def mutate_task_card(self, task):
        self.mutate(lambda rid: self.store.update_card(task, rid))

    def backup(self):
        if not self.store:
            return
        folder = QFileDialog.getExistingDirectory(self, 'Каталог резервных копий (вне рабочей папки данных)')
        if folder:
            self.mutate(lambda rid: self.store.backup(folder), 'Проверенная резервная копия создана')

    def closeEvent(self, event):
        if self.notifications.background() and not self.notifications.exiting:
            self.hide()
            event.ignore()
            return
        if self.jobs or self.mutating or any(d.pending for d in self.dialogs):
            self.status.setText('Дождитесь завершения операций перед выходом')
            event.ignore()
            return
        if self.pending_action and QMessageBox.question(self, 'Незавершённая запись', 'Результат записи неизвестен. Перед выходом рекомендуется повторить запись. Всё равно выйти?', QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            event.ignore()
            return
        if any(chat.input.toPlainText().strip() or chat.retry or chat.sticker or chat.sticker_retry for chat in self.board_chats.values()) and QMessageBox.question(self, 'Неотправленное сообщение', 'Закрыть TeamSync без отправки сообщения общего чата?', QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            event.ignore()
            return
        for dialog in list(self.dialogs):
            if not dialog.close():
                event.ignore()
                return
        self.timer.stop()
        for chat in self.board_chats.values():
            chat.timer.stop()
        self.notifications.shutdown()
        self.redmine_client = None
        self.redmine_login = None
        self.bugzilla_session = None
        self.bugzilla_cards.clear()
        event.accept()
