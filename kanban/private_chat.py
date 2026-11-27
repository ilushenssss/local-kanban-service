import socket
import socketserver
import struct
import threading
import time
from collections import OrderedDict
from math import ceil
from uuid import uuid4

from PyQt5.QtCore import QEvent, QObject, QRect, QSize, Qt, QTime, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QPainter, QTextOption
from PyQt5.QtWidgets import (QDialog, QFrame, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
                             QPushButton, QScrollArea, QStackedWidget, QTextEdit, QVBoxLayout, QWidget)

from .widgets import MessageEdit
from .theme import STICKERS, symbol_icon


MAX_TEXT = 8192
PROTOCOL_VERSION = 2
HEADER = struct.Struct('!BQ16s16s')
MAX_FRAME = HEADER.size + 16 + MAX_TEXT


def receive_frame(conn):
    deadline = time.monotonic() + 6

    def read(size):
        data = bytearray()
        while len(data) < size:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError()
            conn.settimeout(remaining)
            chunk = conn.recv(size - len(data))
            if not chunk:
                raise EOFError()
            data.extend(chunk)
        return bytes(data)

    size = struct.unpack('!I', read(4))[0]
    if not 0 < size <= MAX_FRAME:
        raise ValueError('Недопустимый размер сообщения.')
    return read(size)


def send_frame(conn, data):
    conn.sendall(struct.pack('!I', len(data)) + data)


class PrivateChatServer(socketserver.ThreadingTCPServer):
    daemon_threads = True
    request_queue_size = 8

    def __init__(self, transport):
        self.transport = transport
        self.slots = threading.BoundedSemaphore(self.request_queue_size)
        super().__init__(('0.0.0.0', 0), PrivateChatHandler)

    def process_request(self, request, address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request, address)
        finally:
            self.slots.release()


class PrivateChatHandler(socketserver.BaseRequestHandler):
    def handle(self):
        try:
            reply = self.server.transport.receive(receive_frame(self.request))
            send_frame(self.request, reply)
        except (OSError, EOFError, ValueError, struct.error):
            pass


class PrivateChatTransport:
    def __init__(self, store, deliver):
        self.store = store
        self.deliver = deliver
        self.session = uuid4().bytes
        self.seen = OrderedDict()
        self.lock = threading.Lock()
        self.stopped = threading.Event()
        self.server = None
        self.user_id = None

    def start(self):
        hostname = socket.gethostname()
        try:
            addresses = socket.gethostbyname_ex(hostname)[2]
        except OSError:
            addresses = []
        with self.lock:
            if self.stopped.is_set():
                return
            self.server = PrivateChatServer(self)
            self.endpoint = {'session': self.session.hex(), 'version': PROTOCOL_VERSION, 'addresses': list(
                dict.fromkeys([*addresses, hostname, '127.0.0.1']))[:8], 'port': self.server.server_address[1]}
            threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': 0.1}, daemon=True).start()
        try:
            self.user_id = self.store.publish_private_chat(self.endpoint)
        except Exception:
            self.stop()
            raise

    def renew(self):
        if not self.stopped.is_set():
            return self.store.publish_private_chat(self.endpoint, renew=True)

    def stop(self):
        self.stopped.set()
        with self.lock:
            self.seen.clear()
            if self.server:
                self.server.shutdown()
                self.server.server_close()
                self.server = None

    def send(self, user_id, message_id, text):
        if self.stopped.is_set() or self.user_id is None:
            raise ValueError('Личная переписка сейчас недоступна.')
        encoded = text.encode('utf-8')
        if not encoded or len(encoded) > MAX_TEXT:
            raise ValueError('Сообщение пустое или слишком длинное.')
        peer, _ = self.store.private_chat_peer(user_id)
        local, _ = self.store.private_chat_peer(self.user_id)
        if local['session'] != self.endpoint['session']:
            raise ValueError('Личная переписка активна в другом сеансе TeamSync.')
        if peer.get('version') != PROTOCOL_VERSION:
            raise ValueError('Попросите сотрудника обновить и перезапустить TeamSync.')
        header = HEADER.pack(PROTOCOL_VERSION, self.user_id, self.session, bytes.fromhex(peer['session']))
        body = header + message_id + encoded
        for address in peer['addresses']:
            if self.stopped.is_set():
                break
            try:
                with socket.create_connection((address, peer['port']), timeout=1.2) as conn:
                    conn.settimeout(6)
                    send_frame(conn, body)
                    reply = receive_frame(conn)
                    if reply == message_id:
                        return
            except (OSError, EOFError, ValueError):
                continue
        raise ValueError('Не удалось доставить сообщение. Проверьте, что TeamSync открыт у сотрудника.')

    def receive(self, frame):
        version, sender_id, sender_session, target = HEADER.unpack_from(frame)
        if (self.stopped.is_set() or version != PROTOCOL_VERSION or target != self.session
                or not 0 < sender_id < 2**63):
            raise ValueError('Сеанс личной переписки закрыт.')
        peer, name = self.store.private_chat_peer(sender_id)
        if peer.get('version') != PROTOCOL_VERSION or peer['session'] != sender_session.hex():
            raise ValueError('Сеанс отправителя изменился.')
        body = frame[HEADER.size:]
        if not 16 < len(body) <= 16 + MAX_TEXT:
            raise ValueError('Недопустимый размер сообщения.')
        message_id, text = body[:16], body[16:].decode('utf-8')
        with self.lock:
            if self.stopped.is_set():
                raise ValueError('Сеанс личной переписки закрыт.')
            key = (sender_id, message_id)
            if key not in self.seen:
                self.deliver(self, sender_id, name, text)
                self.seen[key] = None
                if len(self.seen) > 1024:
                    self.seen.popitem(last=False)
        return message_id


class MessageText(QTextEdit):
    def __init__(self, text, outgoing):
        super().__init__()
        self.setObjectName('PrivateChatText')
        self.setFrameStyle(QFrame.NoFrame)
        self.setReadOnly(True)
        self.setUndoRedoEnabled(False)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setFocusPolicy(Qt.ClickFocus)
        self.setWordWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
        self.document().setDocumentMargin(0)
        self.document().documentLayout().documentSizeChanged.connect(self.fit_height)
        self.setPlainText(text)
        self.setAccessibleDescription('Исходящее сообщение' if outgoing else 'Входящее сообщение')

    def fit_height(self, size):
        self.setFixedHeight(max(self.fontMetrics().height(), ceil(size.height())))

    def wheelEvent(self, event):
        event.ignore()


class MessageTimeline(QScrollArea):
    def __init__(self):
        super().__init__()
        self.setObjectName('PrivateChatTimeline')
        self.setAccessibleName('История личной переписки')
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        canvas = QWidget()
        canvas.setObjectName('PrivateChatCanvas')
        self.rows = QVBoxLayout(canvas)
        self.rows.setContentsMargins(16, 14, 16, 14)
        self.rows.setSpacing(8)
        self.rows.addStretch(1)
        self.setWidget(canvas)
        self.bubbles = []
        self.follow = QTimer(self)
        self.follow.setSingleShot(True)
        self.follow.timeout.connect(self.scroll_to_end)

    def append_message(self, text, *, outgoing):
        bar = self.verticalScrollBar()
        at_end = bar.value() >= bar.maximum() - 4
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        bubble = QFrame()
        bubble.setObjectName('PrivateChatBubble')
        bubble.setProperty('outgoing', outgoing)
        layout = QVBoxLayout(bubble)
        layout.setContentsMargins(12, 7, 12, 7)
        layout.setSpacing(2)
        body = MessageText(text, outgoing)
        layout.addWidget(body)
        stamp = QLabel(QTime.currentTime().toString('HH:mm'))
        stamp.setObjectName('PrivateChatTime')
        stamp.setAlignment(Qt.AlignRight)
        layout.addWidget(stamp)
        row_layout.addWidget(bubble, 0, Qt.AlignTop | (Qt.AlignRight if outgoing else Qt.AlignLeft))
        self.bubbles.append((bubble, body))
        self.fit_bubble(bubble, body)
        self.rows.insertWidget(self.rows.count() - 1, row)
        if at_end or outgoing:
            self.follow.start(0)

    def fit_bubble(self, bubble, body):
        natural = max(map(body.fontMetrics().horizontalAdvance, body.toPlainText().splitlines() or ['']))
        available = max(1, self.viewport().width() - 32)
        width = min(max(76, natural + 26), max(1, int(available * 0.75)), 540)
        bubble.setFixedWidth(width)
        document = body.document()
        document.setTextWidth(max(1, width - 26))
        body.fit_height(document.size())

    def resizeEvent(self, event):
        bar = self.verticalScrollBar()
        at_end = bar.value() >= bar.maximum() - 4
        super().resizeEvent(event)
        for bubble, body in self.bubbles:
            self.fit_bubble(bubble, body)
        if at_end:
            self.follow.start(0)

    def scroll_to_end(self):
        bar = self.verticalScrollBar()
        bar.setValue(bar.maximum())


class PrivateConversation(QWidget):
    def __init__(self, name, send, parent):
        super().__init__(parent)
        self.retry = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        header = QFrame()
        header.setObjectName('PrivateChatHeader')
        heading = QVBoxLayout(header)
        heading.setContentsMargins(16, 12, 16, 10)
        heading.setSpacing(3)
        title = QLabel(name)
        title.setTextFormat(Qt.PlainText)
        title.setWordWrap(True)
        title.setObjectName('SectionTitle')
        heading.addWidget(title)
        hint = QLabel('История хранится до выхода из TeamSync')
        hint.setObjectName('Muted')
        hint.setWordWrap(True)
        heading.addWidget(hint)
        layout.addWidget(header)
        self.timeline = MessageTimeline()
        layout.addWidget(self.timeline, 1)
        composer = QFrame()
        composer.setObjectName('PrivateChatComposer')
        compose = QVBoxLayout(composer)
        compose.setContentsMargins(14, 10, 14, 10)
        self.input = MessageEdit()
        self.input.setAcceptRichText(False)
        self.input.setAccessibleName('Сообщение сотруднику')
        self.input.setPlaceholderText('Написать сообщение…')
        self.input.setMaximumHeight(84)
        compose.addWidget(self.input)
        row = QHBoxLayout()
        self.status = QLabel()
        self.status.setTextFormat(Qt.PlainText)
        self.status.setWordWrap(True)
        row.addWidget(self.status, 1)
        self.send_button = QPushButton('Отправить')
        self.send_button.setObjectName('primary')
        self.send_button.setEnabled(False)
        self.send_button.clicked.connect(lambda: send(self))
        self.input.sendRequested.connect(self.send_button.click)
        self.input.textChanged.connect(self.update_send)
        row.addWidget(self.send_button)
        compose.addLayout(row)
        layout.addWidget(composer)

    def update_send(self):
        self.send_button.setEnabled(not self.input.isReadOnly() and bool(self.input.toPlainText().strip()))

    def set_sending(self, sending):
        self.input.setReadOnly(sending)
        self.update_send()


class PrivateChatLauncher(QPushButton):
    def __init__(self):
        super().__init__()
        self.setObjectName('PrivateChatLauncher')
        self.setProperty('symbol', 'private_chat')
        self.setIcon(symbol_icon('private_chat'))
        self.setIconSize(QSize(22, 22))
        self.setFixedSize(40, 40)
        self.setCursor(Qt.PointingHandCursor)
        self.set_unread(0)

    def set_unread(self, count):
        self.unread = count
        label = 'Личные переписки'
        if count:
            label += f'. Непрочитанных сообщений: {count}'
        self.setAccessibleName(label)
        self.setToolTip(label)
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        if not self.unread:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(STICKERS[0][0]))
        text = str(self.unread) if self.unread <= 99 else '99+'
        width = 24 if len(text) > 2 else 19
        badge = QRect(self.width() - width - 1, 1, width, 16)
        painter.drawRoundedRect(badge, 8, 8)
        font = painter.font()
        font.setPixelSize(9)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(Qt.white)
        painter.drawText(badge, Qt.AlignCenter, text)


class PrivateChatDialog(QDialog):
    unread_changed = pyqtSignal(int)

    def __init__(self, new_chat, parent):
        super().__init__(parent)
        self.setWindowTitle('Личные переписки')
        self.resize(860, 610)
        self.setMinimumSize(540, 400)
        self.conversations = {}
        self.items = {}
        self.unread = {}
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        sidebar = QFrame()
        sidebar.setObjectName('PrivateChatSidebar')
        sidebar.setFixedWidth(200)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(10, 12, 10, 10)
        side.setSpacing(8)
        add = QPushButton('Новая переписка…')
        add.clicked.connect(new_chat)
        side.addWidget(add)
        self.peers = QListWidget()
        self.peers.setObjectName('PrivateChatPeers')
        self.peers.setAccessibleName('Список личных переписок')
        self.peers.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.peers.currentItemChanged.connect(self.select)
        side.addWidget(self.peers, 1)
        layout.addWidget(sidebar)
        self.stack = QStackedWidget()
        empty = QLabel('Пока нет личных переписок')
        empty.setObjectName('Muted')
        empty.setAlignment(Qt.AlignCenter)
        empty.setWordWrap(True)
        self.stack.addWidget(empty)
        layout.addWidget(self.stack, 1)

    def conversation(self, user_id, name, send):
        chat = self.conversations.get(user_id)
        if chat is None:
            chat = self.conversations[user_id] = PrivateConversation(name, send, self.stack)
            self.stack.addWidget(chat)
            item = self.items[user_id] = QListWidgetItem(name)
            item.setData(Qt.UserRole, user_id)
            item.setData(Qt.UserRole + 1, name)
            item.setToolTip(name)
            self.peers.addItem(item)
        return chat

    def select(self, item, previous=None):
        if item is None:
            return
        user_id = item.data(Qt.UserRole)
        chat = self.conversations[user_id]
        self.stack.setCurrentWidget(chat)
        if self.isVisible() and not self.isMinimized():
            self.mark_current_read()

    def mark_current_read(self):
        item = self.peers.currentItem()
        if item is None or self.unread.pop(item.data(Qt.UserRole), None) is None:
            return
        item.setText(item.data(Qt.UserRole + 1))
        item.setData(Qt.AccessibleDescriptionRole, None)
        self.unread_changed.emit(sum(self.unread.values()))

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.ActivationChange and self.isVisible() and self.isActiveWindow():
            self.mark_current_read()

    def show_conversation(self, user_id=None):
        if user_id is not None:
            self.peers.setCurrentItem(self.items[user_id])
        elif self.peers.currentItem() is None and self.items:
            self.peers.setCurrentRow(0)
        if self.isMinimized():
            self.showNormal()
        self.show()
        self.raise_()
        self.activateWindow()
        self.mark_current_read()
        item = self.peers.currentItem()
        if item:
            self.conversations[item.data(Qt.UserRole)].input.setFocus()

    def mark_unread(self, user_id):
        count = self.unread[user_id] = self.unread.get(user_id, 0) + 1
        item = self.items[user_id]
        item.setText('({}) {}'.format(count, item.data(Qt.UserRole + 1)))
        item.setData(Qt.AccessibleDescriptionRole, 'Непрочитанных сообщений: {}'.format(count))
        self.unread_changed.emit(sum(self.unread.values()))


class PrivateChats(QObject):
    delivered = pyqtSignal(object, 'qint64', str, str)
    unread_changed = pyqtSignal(int)

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.transport = None
        self.ready = False
        self.renewing = False
        self.dialog = None
        self.timer = QTimer(self)
        self.timer.setInterval(60_000)
        self.timer.timeout.connect(self.renew)
        self.delivered.connect(self.receive)

    def start(self, store):
        if self.transport is not None:
            return
        transport = self.transport = PrivateChatTransport(store, self.delivered.emit)
        self.error = ''

        def done(_, error):
            if self.transport is not transport:
                return
            if error:
                self.error = str(error)
                return
            self.ready = True
            self.timer.start()

        self.window.work(transport.start, done)

    def renew(self):
        transport = self.transport
        if transport is None or self.renewing:
            return
        self.renewing = True

        def done(user_id, error):
            self.renewing = False
            if self.transport is transport and not error and user_id is None:
                self.timer.stop()
                transport.stop()
                self.ready = False
                self.error = 'Личная переписка активна в другом сеансе TeamSync.'

        self.window.work(transport.renew, done)

    def stop(self):
        self.timer.stop()
        if self.transport:
            self.transport.stop()
            self.transport = None
        self.ready = False
        if self.dialog:
            self.dialog.close()
            self.dialog.deleteLater()
            self.dialog = None
        self.unread_changed.emit(0)

    def window_dialog(self):
        if self.dialog is None:
            self.dialog = PrivateChatDialog(lambda: self.window.open_catalog('users'), self.window)
            self.dialog.unread_changed.connect(self.unread_changed.emit)
        return self.dialog

    def conversation(self, user_id, name):
        return self.window_dialog().conversation(user_id, name, lambda chat: self.send(user_id, chat))

    def open_inbox(self):
        dialog = self.window_dialog()
        dialog.show_conversation(next(iter(dialog.unread), None))

    def open(self, user_id, name):
        self.conversation(user_id, name)
        self.dialog.show_conversation(user_id)

    def receive(self, transport, user_id, name, text):
        if self.transport is transport and not self.window.access_restricted:
            chat = self.conversation(user_id, name)
            if (not self.dialog.isVisible() or self.dialog.isMinimized() or not self.dialog.isActiveWindow()
                    or self.dialog.stack.currentWidget() is not chat):
                self.dialog.mark_unread(user_id)
            chat.timeline.append_message(text, outgoing=False)

    def send(self, user_id, chat):
        text = chat.input.toPlainText().strip()
        if not text:
            return
        if not self.ready:
            chat.status.setText(getattr(self, 'error', '') or 'Сеанс личной переписки ещё запускается.')
            return
        transport = self.transport
        if not chat.retry or chat.retry[0] != text:
            chat.retry = text, uuid4().bytes
        message_id = chat.retry[1]
        chat.set_sending(True)
        chat.status.setText('Отправка…')

        def done(_, error):
            if self.transport is not transport:
                return
            chat.set_sending(False)
            if error:
                chat.status.setText(str(error))
                return
            chat.timeline.append_message(text, outgoing=True)
            chat.input.clear()
            chat.status.clear()
            chat.retry = None

        self.window.work(lambda: transport.send(user_id, message_id, text), done)
