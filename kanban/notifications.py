"""Native desktop notifications and optional background operation."""
from pathlib import Path
from PyQt5.QtCore import QObject, QTimer, Qt
from PyQt5.QtGui import QIcon
from PyQt5.QtWidgets import QSystemTrayIcon, QMenu
from .theme import theme_settings
from .spellcheck import spell_engine


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
        self.poll_timer = QTimer(self)
        self.poll_timer.setInterval(3000)
        self.poll_timer.timeout.connect(self.poll)
        self.poll_timer.start()

    def enabled(self):
        return self.settings.value('notifications/enabled', True, type=bool)

    def background(self):
        return self.available and self.settings.value('notifications/close_to_tray', True, type=bool)

    def add_settings(self, menu):
        action = menu.addAction('Уведомления чатов досок')
        action.setCheckable(True)
        action.setChecked(self.enabled())
        action.toggled.connect(lambda value: self.settings.setValue('notifications/enabled', value))
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

    def open_chat(self):
        w = self.window
        if w.isMinimized():
            w.setWindowState(w.windowState() & ~Qt.WindowMinimized)
            w.show()
        else:
            w.show()
        w.raise_()
        w.activateWindow()
        w.messenger.open_chat()
        self.pending.clear()
        self.timer.stop()

    def open_notification(self):
        self.open_chat()
        if self.last_board is not None and hasattr(self.window, 'show_board_chat'):
            self.window.show_board_chat(self.last_board)

    def poll(self):
        store = getattr(self.window, 'store', None)
        if self.loading or store is None:
            return
        self.loading = True
        def done(messages, error):
            self.loading = False
            if not error:
                self.observe(messages, store.login)
        self.window.work(lambda: store.chat_updates(self.cursor), done)

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
            self.tray.showMessage(title[:100], text[:240], QSystemTrayIcon.Information, 6000)
        store = getattr(self.window, 'store', None)
        if store is not None and board_id is not None:
            self.window.work(lambda: store.team_chat(board_id=board_id), show)
        else:
            show()

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
        self.tray.hide()
