"""Shared local team chat, separate from task discussions; no network services."""
import uuid
from PyQt5.QtCore import Qt, QTimer, QSize, QUrl, QByteArray, QBuffer, QIODevice, pyqtSignal
from PyQt5.QtGui import QPainter, QColor, QFont, QImage, QPixmap, QTextDocument
from PyQt5.QtWidgets import QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTextEdit, QTextBrowser, QDialog, QDialogButtonBox
from .theme import TOKENS, symbol_icon
from .widgets import MessageEdit
from .presentation import comments_html
from .stickers import prepare_sticker
from .avatars import avatar_resources
from .people import open_employee_profile
from .sticker_pack import StickerPackDialog


class StickerInput(MessageEdit):
    imagePasted = pyqtSignal(bytes)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_V and event.modifiers() == Qt.ControlModifier:
            source = QApplication.clipboard().mimeData()
            if source and source.hasImage():
                value = source.imageData()
                image = value.toImage() if isinstance(value, QPixmap) else QImage(value)
                data = QByteArray()
                buffer = QBuffer(data)
                buffer.open(QIODevice.WriteOnly)
                if image.save(buffer, 'PNG'):
                    self.imagePasted.emit(bytes(data))
                event.accept()
                return
        super().keyPressEvent(event)


class StickerTimeline(QTextBrowser):
    def loadResource(self, kind, url):
        return None


class ChatBubble(QPushButton):
    def __init__(self, parent):
        super().__init__(parent)
        self._unread = 0
        self.setObjectName('ChatLauncher')
        self.setFixedSize(56, 56)
        self.setProperty('symbol', 'chat')
        self.setIcon(symbol_icon('chat'))
        self.setIconSize(QSize(24, 24))
        self.setCursor(Qt.PointingHandCursor)
        self.unread = 0

    @property
    def unread(self):
        return self._unread

    @unread.setter
    def unread(self, count):
        self._unread = count
        self.setText('')
        self.update()
        self.setAccessibleName('Открыть чат доски. Непрочитанных: {}'.format(count))
        self.setToolTip('Чат доски. Непрочитанных: {}'.format(count))

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.unread:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.Antialiasing)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor('#9B1C1C'))
            painter.drawEllipse(35, 0, 21, 21)
            font = QFont(self.font())
            font.setPixelSize(9)
            font.setBold(True)
            painter.setFont(font)
            painter.setPen(Qt.white)
            painter.drawText(35, 0, 21, 21, Qt.AlignCenter, str(self.unread) if self.unread < 100 else '99+')


class Messenger(QWidget):
    def __init__(self, window, board_id=None, board_name=""):
        super().__init__(window)
        self.window = window
        self.board_id = board_id
        self.board_name = board_name
        self.active = board_id is not None
        self.setObjectName('Messenger')
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.messages = []
        self.avatars = {}
        self.loading = False
        self.sending = False
        self.reading = False
        self.read_cursor = 0
        self.retry = None
        self.markup = None
        self.stickers = {}
        self.sticker = None
        self.sticker_retry = None
        self.preparing = False
        self.bubble = ChatBubble(window)
        self.bubble.clicked.connect(self.open_chat)
        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        title = QLabel('Чат · ' + board_name if board_id else 'Выберите доску для чата')
        self.title_label = title
        title.setObjectName('SectionTitle')
        top.addWidget(title, 1)
        hide = QPushButton('Свернуть')
        hide.setToolTip('Свернуть чат')
        hide.clicked.connect(self.hide)
        top.addWidget(hide)
        layout.addLayout(top)
        self.timeline = StickerTimeline()
        self.timeline.setOpenLinks(False)
        self.timeline.setOpenExternalLinks(False)
        self.timeline.anchorClicked.connect(self.view_sticker)
        self.timeline.anchorClicked.connect(self.view_profile)
        self.timeline.setReadOnly(True)
        layout.addWidget(self.timeline, 1)
        self.input = StickerInput()
        self.input.imagePasted.connect(self.paste_sticker)
        self.input.setAcceptRichText(False)
        self.input.setPlaceholderText('Сообщение участникам доски…')
        self.input.setMaximumHeight(90)
        layout.addWidget(self.input)
        self.send_button = QPushButton('Отправить')
        self.send_button.setObjectName('primary')
        self.send_button.clicked.connect(self.send)
        self.input.sendRequested.connect(self.send_button.click)
        actions = QHBoxLayout()
        self.image_button = QPushButton('Стикеры')
        self.image_button.setToolTip('Открыть стандартные стикеры. Своё изображение можно вставить через Ctrl+V.')
        self.image_button.clicked.connect(self.choose_sticker)
        actions.addWidget(self.image_button)
        actions.addWidget(self.send_button)
        layout.addLayout(actions)
        self.preview = QWidget()
        preview_layout = QVBoxLayout(self.preview)
        self.preview_image = QLabel()
        self.preview_image.setAlignment(Qt.AlignCenter)
        preview_layout.addWidget(self.preview_image)
        preview_actions = QHBoxLayout()
        self.sticker_button = QPushButton('Отправить стикер')
        self.sticker_button.clicked.connect(self.send_sticker)
        self.remove_button = QPushButton('Убрать')
        self.remove_button.clicked.connect(self.remove_sticker)
        preview_actions.addWidget(self.sticker_button)
        preview_actions.addWidget(self.remove_button)
        preview_layout.addLayout(preview_actions)
        layout.addWidget(self.preview)
        self.preview.hide()
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.PlainText)
        layout.addWidget(self.status)
        self.timeline.verticalScrollBar().valueChanged.connect(lambda: QTimer.singleShot(0, self.mark_read))
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.timer.start(3000)
        self.hide()
        self.bubble.setVisible(self.active)
        self.input.setEnabled(self.active)
        self.send_button.setEnabled(self.active)
        self.image_button.setEnabled(self.active)
        self.reposition()

    def showEvent(self, event):
        super().showEvent(event)
        self.bubble.hide()

    def hideEvent(self, event):
        super().hideEvent(event)
        self.bubble.setVisible(self.active and getattr(self.window, "messenger", None) is self)

    def deactivate(self):
        self.active = False
        self.timer.stop()
        self.hide()
        self.bubble.hide()

    def activate(self, opened=False):
        self.active = self.board_id is not None
        if not self.active:
            return
        self.timer.start()
        self.bubble.show()
        self.reposition()
        if opened:
            self.open_chat()
        else:
            self.poll()

    def reposition(self):
        w = self.window
        self.setGeometry(max(0, w.width()-392), 60, 376, max(260, w.height()-84))
        self.bubble.move(max(0, w.width()-80), w.height()-68)
        self.bubble.raise_()
        self.raise_()

    def open_chat(self):
        if not self.active:
            return
        self.show()
        self.reposition()
        self.end()
        self.poll()

    def end(self):
        bar = self.timeline.verticalScrollBar()
        bar.setValue(bar.maximum())
        QTimer.singleShot(0, self.mark_read)

    def poll(self):
        if not self.active or self.loading or not self.window.store:
            return
        store = self.window.store
        self.loading = True
        after = self.messages[-1]['id'] if self.messages else 0
        def done(result, error):
            self.loading = False
            if self.window.store is not store or not self.active:
                return
            if error:
                self.status.setText('Чат недоступен. Повторная проверка выполняется автоматически; введённый текст сохранён.')
                return
            if not self.retry and not self.sticker_retry and not self.preparing:
                self.status.setText('Некоторые стикеры недоступны. Загрузка повторится автоматически.' if result.get('image_errors') else '')
            self.messages.extend(result['messages'])
            self.avatars = result['avatars']
            self.stickers.update(result.get('stickers', {}))
            self.update_unread(result['unread'])
            self.render()
            self.mark_read()
        missing = [m['id'] for m in self.messages if m.get('kind') == 'sticker' and m['id'] not in self.stickers]
        def read():
            result = store.team_chat(after, board_id=self.board_id)
            ids = missing + [m['id'] for m in result['messages'] if m.get('kind') == 'sticker']
            result['stickers'] = {}
            for mid in ids:
                try:
                    result['stickers'][mid] = store.team_sticker(mid)
                except (OSError, ValueError):
                    result['image_errors'] = True
            return result
        self.window.work(read, done)

    def update_unread(self, count):
        if self.bubble.unread != count:
            self.bubble.unread = count

    def render(self):
        if not self.window.store:
            return
        def sticker_html(entry):
            mid = entry['id']
            data = self.stickers.get(mid)
            if data is None:
                return 'Стикер недоступен — повторная загрузка…'
            image = QImage.fromData(data, 'PNG')
            url = QUrl('sticker:{}'.format(mid))
            self.timeline.document().addResource(QTextDocument.ImageResource, url, image)
            size = image.size().scaled(180, 180, Qt.KeepAspectRatio)
            return '<a href="sticker:{0}"><img src="sticker:{0}" width="{1}" height="{2}" alt="Фотостикер — открыть" /></a>'.format(mid, size.width(), size.height())
        sources = avatar_resources(self.timeline.document(), self.avatars)
        markup = comments_html(self.messages, self.window.store.login, sticker_html, getattr(self.window, 'timezone', 'UTC'),
                               avatar_sources=sources)
        if markup == self.markup:
            return
        bar = self.timeline.verticalScrollBar()
        bottom = bar.value() >= bar.maximum()-4
        position = bar.value()
        self.timeline.setHtml(markup)
        self.markup = markup
        bar.setValue(bar.maximum() if bottom else position)
        if bottom:
            QTimer.singleShot(0, self.end)

    def mark_read(self):
        if not self.isVisible() or self.window.isMinimized() or not self.window.isActiveWindow() or not self.messages or self.reading:
            return
        bar = self.timeline.verticalScrollBar()
        if bar.value() < bar.maximum()-4:
            return
        if any(m.get('kind') == 'sticker' and m['id'] not in self.stickers for m in self.messages):
            return
        latest = self.messages[-1]['id']
        if latest <= self.read_cursor:
            return
        self.reading = True
        def done(value, error):
            self.reading = False
            if not error:
                self.read_cursor = value
                self.poll()
                if hasattr(self.window, 'notifications'):
                    self.window.notifications.poll()
        self.window.work(lambda: self.window.store.team_mark_read(latest, board_id=self.board_id), done)

    def send(self):
        if not self.active or self.sending or self.sticker_retry or not self.window.store:
            return
        if not self.retry:
            text = self.input.toPlainText()
            if not text.strip():
                return
            self.retry = (uuid.uuid4().hex, text)
        request_id, text = self.retry
        self.sending = True
        self.send_button.setEnabled(False)
        def done(value, error):
            self.sending = False
            self.send_button.setEnabled(True)
            if error:
                self.status.setText('Отправка не подтверждена. Повтор безопасен; текст сохранён.')
                return
            self.retry = None
            if self.input.toPlainText() == text:
                self.input.clear()
            self.status.clear()
            self.poll()
        self.window.work(lambda: self.window.store.team_send(text, request_id, board_id=self.board_id), done)

    def choose_sticker(self):
        if not self.active or self.sending or self.retry or self.sticker_retry or self.preparing:
            self.status.setText('Сначала завершите текущую отправку')
            return
        dialog = StickerPackDialog(self.window.store, self.window.work, self)
        if dialog.exec_() == QDialog.Accepted and dialog.selected_sticker:
            self.preview_sticker(dialog.selected_sticker)
        dialog.deleteLater()

    def paste_sticker(self, data):
        if not self.active or self.sending or self.retry or self.sticker_retry or self.preparing:
            self.status.setText('Сначала завершите текущую отправку')
            return
        self.prepare_image(lambda: prepare_sticker(data))

    def prepare_image(self, fn):
        self.preparing = True
        self.image_button.setEnabled(False)
        self.status.setText('Подготовка фотостикера…')
        def done(result, error):
            self.preparing = False
            self.image_button.setEnabled(True)
            if error:
                self.status.setText('Не удалось подготовить стикер: ' + str(error))
                return
            self.preview_sticker(result[0])
        self.window.work(fn, done)

    def preview_sticker(self, data):
        self.sticker = data
        self.preview_image.setPixmap(QPixmap.fromImage(QImage.fromData(data)).scaled(
            140, 140, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        self.preview.show()
        self.status.setText('Предпросмотр. Нажмите «Отправить стикер».')

    def remove_sticker(self):
        if not self.sending and not self.sticker_retry:
            self.sticker = None
            self.preview.hide()
            self.status.clear()

    def send_sticker(self):
        if self.sending or self.retry or self.preparing or not self.sticker or not self.window.store:
            return
        if not self.sticker_retry:
            self.sticker_retry = (uuid.uuid4().hex, self.sticker)
        rid, data = self.sticker_retry
        self.sending = True
        for button in (self.send_button, self.image_button, self.sticker_button, self.remove_button):
            button.setEnabled(False)
        self.status.setText('Отправка стикера…')
        def done(value, error):
            self.sending = False
            self.send_button.setEnabled(True)
            self.image_button.setEnabled(True)
            self.sticker_button.setEnabled(True)
            if error:
                self.sticker_button.setText('Повторить стикер')
                self.status.setText('Отправка не подтверждена. Повторите стикер: дубликат не появится.')
                return
            self.sticker_retry = None
            self.remove_button.setEnabled(True)
            self.sticker_button.setText('Отправить стикер')
            self.remove_sticker()
            self.poll()
        self.window.work(lambda: self.window.store.team_send_sticker(data, rid, board_id=self.board_id), done)

    def view_profile(self, url):
        if self.active and self.window.store:
            open_employee_profile(url, self, self.window.store, self.window.work, self.status)

    def view_sticker(self, url):
        if url.scheme() != 'sticker':
            return
        try:
            data = self.stickers[int(url.path())]
        except (ValueError, KeyError):
            return
        dialog = QDialog(self)
        dialog.setWindowTitle('Фотостикер')
        layout = QVBoxLayout(dialog)
        label = QLabel()
        label.setPixmap(QPixmap.fromImage(QImage.fromData(data, 'PNG')))
        label.setAlignment(Qt.AlignCenter)
        layout.addWidget(label)
        close = QPushButton('Закрыть')
        close.clicked.connect(dialog.accept)
        layout.addWidget(close)
        dialog.exec_()
