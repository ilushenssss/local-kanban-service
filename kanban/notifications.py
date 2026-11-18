"""Native desktop notifications and optional background operation."""
from pathlib import Path
from PyQt5.QtCore import QObject, QTimer, Qt
from PyQt5.QtGui import QIcon
from PyQt5.QtWidgets import QSystemTrayIcon, QMenu, QDialog, QDialogButtonBox, QLabel, QListWidget, QListWidgetItem, QVBoxLayout
from .theme import theme_settings
from .spellcheck import spell_engine
from .presentation import date_text, task_notification_text


class Notifications(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.settings = theme_settings()
        self.initialized = False
        self.cursor = 0
        self.pending = []
        self.exiting = False
        self.loading = False
        self.last_board = None
        self.last_task = None
        self.task_cursor = None
        self.task_pending = []
        self.inbox = None
        self.available = QSystemTrayIcon.isSystemTrayAvailable()
        self.tray = QSystemTrayIcon(QIcon(str(Path(__file__).parent / 'assets/teamsync-logo.png')), window)
        self.tray.setToolTip('TeamSync — чаты досок')
        self.menu = QMenu(window)
        self.menu.addAction('Открыть TeamSync', self.open_chat)
        self.add_settings(self.menu)
        self.menu.addSeparator()
        self.menu.addAction('Выйти из TeamSync', self.exit_app)
        self.tray.setContextMenu(self.menu)
        self.tray.messageClicked.connect(self.open_notification)
        self.tray.activated.connect(self.activated)
        if self.available:
            self.tray.show()
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(1200)
        self.timer.timeout.connect(self.flush)
        self.task_timer = QTimer(self)
        self.task_timer.setSingleShot(True)
        self.task_timer.setInterval(1200)
        self.task_timer.timeout.connect(self.flush_tasks)
        self.poll_timer = QTimer(self)
        self.poll_timer.setInterval(3000)
        self.poll_timer.timeout.connect(self.poll)
        self.poll_timer.start()

    def enabled(self):
        return self.settings.value('notifications/enabled', True, type=bool)

    def tasks_enabled(self):
        return self.settings.value('notifications/tasks', True, type=bool)

    def background(self):
        return self.available and self.settings.value('notifications/close_to_tray', True, type=bool)

    def add_settings(self, menu):
        action = menu.addAction('Уведомления чатов досок')
        action.setCheckable(True)
        action.setChecked(self.enabled())
        action.toggled.connect(lambda value: self.settings.setValue('notifications/enabled', value))
        tasks = menu.addAction('Уведомления об изменениях задач')
        tasks.setCheckable(True)
        tasks.setChecked(self.tasks_enabled())
        tasks.toggled.connect(lambda value: self.settings.setValue('notifications/tasks', value))
        menu.aboutToShow.connect(lambda: tasks.setChecked(self.tasks_enabled()))
        spelling = menu.addAction('Проверять орфографию')
        spelling.setCheckable(True)
        spelling.setChecked(spell_engine().enabled())
        spelling.toggled.connect(spell_engine().set_enabled)
        background = menu.addAction('Закрывать окно в трей')
        background.setCheckable(True)
        background.setChecked(self.background())
        background.setEnabled(self.available)
        background.toggled.connect(lambda value: self.settings.setValue('notifications/close_to_tray', value))
        menu.aboutToShow.connect(lambda: action.setChecked(self.enabled()))
        menu.aboutToShow.connect(lambda: background.setChecked(self.background()))
        menu.aboutToShow.connect(lambda: spelling.setChecked(spell_engine().enabled()))

    def activated(self, reason):
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.open_chat()

    def restore_window(self):
        w = self.window
        if w.isMinimized():
            w.setWindowState(w.windowState() & ~Qt.WindowMinimized)
            w.show()
        else:
            w.show()
        w.raise_()
        w.activateWindow()
    def open_chat(self):
        self.restore_window()
        self.window.messenger.open_chat()
        self.pending.clear()
        self.timer.stop()

    def open_notification(self):
        if self.last_task is not None:
            self.restore_window()
            self.window.show_task_notification(self.last_task)
            return
        self.open_chat()
        if self.last_board is not None and hasattr(self.window, 'show_board_chat'):
            self.window.show_board_chat(self.last_board)

    def poll(self):
        store = getattr(self.window, 'store', None)
        if self.loading or store is None:
            return
        self.loading = True
        def done(result, error):
            self.loading = False
            if error:
                if self.inbox is not None:
                    self.fill_inbox([], 'Не удалось обновить уведомления. Повторим автоматически.')
            else:
                self.observe(result['chat'], store.login)
                self.observe_tasks(result['tasks'])
                if hasattr(self.window, 'update_unread_notifications'):
                    self.window.update_unread_notifications(result['tasks']['events'], result['unread_chat'], result['timezone'])
                    if self.inbox is not None:
                        self.fill_inbox(result['tasks']['events'] + result['unread_chat'])
                if hasattr(self.window, 'update_board_badges'):
                    self.window.update_board_badges(result['badges'])
        def read():
            return {'chat': store.chat_updates(self.cursor), 'tasks': store.task_updates(0),
                    'unread_chat': store.chat_updates(unread_only=True), 'badges': store.board_badges(),
                    'timezone': store.timezone()}
        self.window.work(read, done)

    def open_inbox(self):
        dialog = QDialog(self.window)
        dialog.setWindowTitle('Непрочитанные уведомления')
        dialog.resize(530, 420)
        layout = QVBoxLayout(dialog)
        self.inbox_status = QLabel(
            'Загрузка уведомлений…' if getattr(self.window, 'store', None)
            else 'Уведомления появятся после подключения к базе.')
        self.inbox_status.setWordWrap(True)
        layout.addWidget(self.inbox_status)
        self.inbox = QListWidget(dialog)
        self.inbox.setAccessibleName('Непрочитанные уведомления')
        self.inbox.setWordWrap(True)
        self.inbox.setSpacing(4)
        layout.addWidget(self.inbox, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.Close, parent=dialog)
        buttons.button(QDialogButtonBox.Close).setText('Закрыть')
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        selected = []
        def activate(item):
            selected.append(item.data(Qt.UserRole))
            dialog.accept()
        self.inbox.itemActivated.connect(activate)
        self.inbox.setFocus()
        self.poll()
        dialog.exec_()
        self.inbox = None
        dialog.deleteLater()
        if selected:
            task_id, board_id = selected[0]
            if task_id is not None:
                self.window.show_task_notification(task_id)
            else:
                self.window.show_board_chat(board_id)

    def fill_inbox(self, events, error=None):
        rows = []
        for event in sorted(events, key=lambda e: (e['created_at'], e['id']), reverse=True):
            if 'task_id' in event:
                summary = task_notification_text(event, self.window.store.login)
            else:
                body = 'Отправил фотостикер' if event.get('kind') == 'sticker' else ' '.join(event['body'].split())
                if len(body) > 180:
                    body = body[:179] + '…'
                summary = 'Чат · {}: {}'.format(event['author'], body)
            text = '{} · {}\n{}'.format(event['board_name'], date_text(event['created_at'], True, self.window.timezone), summary)
            key = ('task' if 'task_id' in event else 'chat', event['id'])
            rows.append((key, text, (event.get('task_id'), event['board_id'])))
        self.inbox_status.setText(error or ('Непрочитанных: {}'.format(len(rows)) if rows else 'Нет непрочитанных уведомлений'))
        if rows == getattr(self.inbox, 'rows', None):
            return
        current = self.inbox.currentItem()
        selected = current.data(Qt.UserRole + 1) if current else None
        self.inbox.rows = rows
        self.inbox.clear()
        for key, text, target in rows:
            item = QListWidgetItem(text, self.inbox)
            item.setData(Qt.UserRole, target)
            item.setData(Qt.UserRole + 1, key)
            if key == selected:
                self.inbox.setCurrentItem(item)
        if rows and self.inbox.currentRow() == -1:
            self.inbox.setCurrentRow(0)

    def chat_active(self, board_id=None):
        w = self.window
        return w.isActiveWindow() and not w.isMinimized() and w.messenger.isVisible() and (board_id is None or getattr(w.messenger, "board_id", None) == board_id)

    def observe(self, messages, login):
        fresh = [m for m in messages if m['id'] > self.cursor]
        self.cursor = max([self.cursor] + [m['id'] for m in messages])
        if not self.initialized:
            self.initialized = True
            return
        if not self.enabled():
            return
        self.pending.extend(m for m in fresh if m['login'] != login and not self.chat_active(m.get('board_id')))
        if self.pending and not self.timer.isActive():
            self.timer.start()

    def flush(self):
        messages, self.pending = self.pending, []
        if not messages or not self.available or not self.enabled():
            return
        messages = [m for m in messages if not self.chat_active(m.get('board_id'))]
        if not messages:
            return
        last = messages[-1]
        text = 'Отправил фотостикер' if last.get('kind') == 'sticker' else ' '.join(last['body'].split())
        title = 'TeamSync · ' + last.get('board_name', last['author'])
        if last.get('board_name'):
            text = last['author'] + ': ' + text
        if len(messages) > 1:
            title = 'TeamSync · новых сообщений: {}'.format(len(messages))
            if not last.get('board_name'):
                text = last['author'] + ': ' + text
        board_id = last.get('board_id')
        def show(result=None, error=None):
            if error or not self.enabled() or self.chat_active(board_id):
                return
            self.last_board = board_id
            self.last_task = None
            self.tray.showMessage(title[:100], text[:240], QSystemTrayIcon.Information, 6000)
        store = getattr(self.window, 'store', None)
        if store is not None and board_id is not None:
            self.window.work(lambda: store.team_chat(board_id=board_id), show)
        else:
            show()

    def observe_tasks(self, result):
        fresh = [e for e in result['events'] if self.task_cursor is not None and e['id'] > self.task_cursor]
        self.task_cursor = max(self.task_cursor or 0, result['cursor'])
        if not self.tasks_enabled():
            return
        self.task_pending.extend(fresh)
        if self.task_pending and not self.task_timer.isActive():
            self.task_timer.start(1200)

    def flush_tasks(self):
        events, self.task_pending = self.task_pending, []
        if not events or not self.available or not self.tasks_enabled():
            return
        store = getattr(self.window, 'store', None)
        if store is None:
            return
        # Recheck access to every queued task before constructing desktop text.
        def read():
            allowed = []
            for event in events:
                try:
                    detail = store.detail(event['task_id'])
                    with store.connection() as c:
                        store.active_board(c, detail['board_id'])
                    if event['id'] > detail.get('notification_read', 0):
                        allowed.append(event)
                except ValueError:
                    continue
            return allowed
        def show(allowed, error):
            if error:
                if self.tasks_enabled():
                    self.task_pending = events + self.task_pending
                    self.task_timer.start(3000)
                return
            if not allowed or not self.tasks_enabled():
                return
            last = allowed[-1]
            title = 'TeamSync · ' + last['board_name']
            text = task_notification_text(last, store.login)
            if len(allowed) > 1:
                tasks = len({event['task_id'] for event in allowed})
                title = 'TeamSync · изменений: {}, задач: {}'.format(len(allowed), tasks)
            self.last_task = last['task_id']
            self.last_board = last['board_id']
            self.tray.showMessage(title[:100], text[:240], QSystemTrayIcon.Information, 6000)
        self.window.work(read, show)

    def exit_app(self):
        self.exiting = True
        if self.window.close():
            self.tray.hide()
        else:
            self.exiting = False

    def shutdown(self):
        self.poll_timer.stop()
        self.timer.stop()
        self.pending.clear()
        self.task_timer.stop()
        self.task_pending.clear()
        self.tray.hide()
