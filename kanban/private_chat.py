import socket
import socketserver
import struct
import threading
import time
from collections import OrderedDict
from uuid import uuid4

from PyQt5.QtCore import QObject, Qt, QTimer, pyqtSignal
from PyQt5.QtWidgets import (QDialog, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout)

from .widgets import MessageEdit


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


class PrivateChatDialog(QDialog):
    def __init__(self, name, send, parent):
        super().__init__(parent)
        self.name = name
        self.retry = None
        self.setWindowTitle(name + ' · Личная переписка')
        self.resize(520, 480)
        layout = QVBoxLayout(self)
        hint = QLabel('Временная переписка. История исчезнет после выхода из TeamSync.')
        hint.setObjectName('Muted')
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.timeline = QPlainTextEdit()
        self.timeline.setReadOnly(True)
        self.timeline.setAccessibleName('Личная переписка')
        layout.addWidget(self.timeline, 1)
        self.input = MessageEdit()
        self.input.setAcceptRichText(False)
        self.input.setAccessibleName('Сообщение сотруднику')
        self.input.setPlaceholderText('Написать сообщение…')
        self.input.setMaximumHeight(104)
        layout.addWidget(self.input)
        row = QHBoxLayout()
        self.status = QLabel()
        self.status.setTextFormat(Qt.PlainText)
        self.status.setWordWrap(True)
        row.addWidget(self.status, 1)
        self.send_button = QPushButton('Отправить')
        self.send_button.setObjectName('primary')
        self.send_button.clicked.connect(lambda: send(self))
        self.input.sendRequested.connect(self.send_button.click)
        row.addWidget(self.send_button)
        layout.addLayout(row)

    def append_message(self, author, text):
        self.timeline.appendPlainText(author + '\n' + text)
        bar = self.timeline.verticalScrollBar()
        bar.setValue(bar.maximum())

    def set_sending(self, sending):
        self.send_button.setEnabled(not sending)
        self.input.setReadOnly(sending)


class PrivateChats(QObject):
    delivered = pyqtSignal(object, 'qint64', str, str)

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.transport = None
        self.ready = False
        self.renewing = False
        self.dialogs = {}
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
        for chat in self.dialogs.values():
            chat.timeline.clear()
            chat.input.clear()
            chat.retry = None
            chat.close()
            chat.deleteLater()
        self.dialogs.clear()

    def open(self, user_id, name):
        chat = self.dialogs.get(user_id)
        if chat is None:
            chat = self.dialogs[user_id] = PrivateChatDialog(name, lambda chat: self.send(user_id, chat), self.window)
        chat.show()
        chat.raise_()
        chat.activateWindow()
        chat.input.setFocus()
        return chat

    def receive(self, transport, user_id, name, text):
        if self.transport is transport and not self.window.access_restricted:
            chat = self.dialogs.get(user_id)
            if chat is None or not chat.isVisible():
                chat = self.open(user_id, name)
            chat.append_message(name, text)

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
            chat.append_message('Вы', text)
            chat.input.clear()
            chat.status.clear()
            chat.retry = None

        self.window.work(lambda: transport.send(user_id, message_id, text), done)
