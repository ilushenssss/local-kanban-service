"""Shared local team chat, separate from task discussions; no network services."""
import uuid
from PyQt5.QtCore import Qt, QTimer, QRectF
from PyQt5.QtGui import QPainter, QColor, QPen, QFont
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTextEdit, QAbstractButton
from .theme import TOKENS
from .presentation import comments_html


class ChatBubble(QAbstractButton):
    def __init__(self, parent):
        super().__init__(parent)
        self.unread = 0
        self.setFixedSize(64, 64)
        self.setCursor(Qt.PointingHandCursor)
        self.setAccessibleName('Открыть общий чат TeamSync')
        self.setToolTip('Общий чат TeamSync')

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor('#2563EB'))
        p.drawEllipse(4, 8, 52, 52)
        p.setPen(QPen(Qt.white, 2))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(QRectF(18, 23, 24, 17), 5, 5)
        p.drawLine(23, 40, 23, 46)
        p.drawLine(23, 46, 30, 40)
        if self.unread:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor('#dc2626'))
            p.drawEllipse(36, 0, 28, 28)
            p.setPen(Qt.white)
            f = QFont(self.font())
            f.setPixelSize(11)
            f.setBold(True)
            p.setFont(f)
            p.drawText(36, 0, 28, 28, Qt.AlignCenter, str(self.unread) if self.unread < 100 else '99+')


class Messenger(QWidget):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.setObjectName('Messenger')
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.messages = []
        self.loading = False
        self.sending = False
        self.reading = False
        self.read_cursor = 0
        self.retry = None
        self.markup = None
        self.bubble = ChatBubble(window)
        self.bubble.clicked.connect(self.open_chat)
        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        title = QLabel('Общий чат TeamSync')
        title.setObjectName('SectionTitle')
        top.addWidget(title, 1)
        hide = QPushButton('−')
        hide.setToolTip('Свернуть чат')
        hide.clicked.connect(self.hide)
        top.addWidget(hide)
        layout.addLayout(top)
        self.timeline = QTextEdit()
        self.timeline.setReadOnly(True)
        layout.addWidget(self.timeline, 1)
        self.input = QTextEdit()
        self.input.setAcceptRichText(False)
        self.input.setPlaceholderText('Сообщение всем сотрудникам…')
        self.input.setMaximumHeight(90)
        layout.addWidget(self.input)
        self.send_button = QPushButton('Отправить')
        self.send_button.setObjectName('primary')
        self.send_button.clicked.connect(self.send)
        layout.addWidget(self.send_button)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.PlainText)
        layout.addWidget(self.status)
        self.timeline.verticalScrollBar().valueChanged.connect(lambda: QTimer.singleShot(0, self.mark_read))
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.timer.start(3000)
        self.hide()
        self.bubble.show()
        self.reposition()

    def showEvent(self, event):
        super().showEvent(event)
        self.bubble.hide()

    def hideEvent(self, event):
        super().hideEvent(event)
        self.bubble.show()

    def reposition(self):
        w = self.window
        self.setGeometry(max(0, w.width()-392), 60, 376, max(260, w.height()-84))
        self.bubble.move(w.width()-80, w.height()-80)
        self.bubble.raise_()
        self.raise_()

    def open_chat(self):
        self.show()
        self.reposition()
        self.end()
        self.poll()

    def end(self):
        bar = self.timeline.verticalScrollBar()
        bar.setValue(bar.maximum())
        QTimer.singleShot(0, self.mark_read)

    def poll(self):
        if self.loading or not self.window.store:
            return
        self.loading = True
        after = self.messages[-1]['id'] if self.messages else 0
        def done(result, error):
            self.loading = False
            if error:
                self.status.setText('Чат недоступен. Повторная проверка выполняется автоматически; введённый текст сохранён.')
                return
            if not self.retry:
                self.status.clear()
            self.messages.extend(result['messages'])
            self.bubble.unread = result['unread']
            self.bubble.setToolTip('Общий чат · непрочитанных: {}'.format(result['unread']))
            self.bubble.update()
            self.render()
            self.mark_read()
        self.window.work(lambda: self.window.store.team_chat(after), done)

    def render(self):
        if not self.window.store:
            return
        markup = comments_html(self.messages, self.window.store.login)
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
        if not self.isVisible() or not self.messages or self.reading:
            return
        bar = self.timeline.verticalScrollBar()
        if bar.value() < bar.maximum()-4:
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
        self.window.work(lambda: self.window.store.team_mark_read(latest), done)

    def send(self):
        if self.sending or not self.window.store:
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
        self.window.work(lambda: self.window.store.team_send(text, request_id), done)
