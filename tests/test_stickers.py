from unittest.mock import patch
from pathlib import Path
import tempfile
import sqlite3
import unittest
from PyQt5.QtCore import QByteArray, QBuffer, QIODevice, Qt
from PyQt5.QtGui import QImage
from kanban.stickers import prepare_sticker, MAX_SOURCE
from kanban.store import Store, SCHEMA
from tools.restore import restore


def image_bytes(width=900, height=450, fmt='PNG'):
    image = QImage(width, height, QImage.Format_ARGB32)
    image.fill(Qt.transparent)
    image.setPixelColor(0, 0, Qt.red)
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.WriteOnly)
    assert image.save(buffer, fmt)
    return bytes(data)


class StickersTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = Store(self.root / 'data', 'alice')
        self.store.initialize_admin('alice')

    def test_resize_alpha_and_supported_formats(self):
        for fmt in ('PNG', 'JPEG', 'WEBP'):
            data, width, height = prepare_sticker(image_bytes(fmt=fmt))
            self.assertEqual((width, height), (512, 256))
            self.assertTrue(data.startswith(b'\x89PNG'))
            if fmt == 'PNG':
                self.assertEqual(QImage.fromData(data).pixelColor(200, 100).alpha(), 0)
        self.assertEqual(prepare_sticker(image_bytes(20, 10))[1:], (20, 10))
        for bad in (b'invalid', b'x' * (MAX_SOURCE + 1)):
            with self.assertRaises(ValueError):
                prepare_sticker(bad)

    def test_v3_upgrade_keeps_administrator_and_text_messages(self):
        folder = self.root / 'v3'
        folder.mkdir()
        with sqlite3.connect(folder / 'kanban.db') as c:
            c.executescript(SCHEMA)
            c.execute("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'employee'")
            c.execute('ALTER TABLE boards ADD COLUMN owner_id INTEGER REFERENCES users(id)')
            c.execute("CREATE UNIQUE INDEX one_admin ON users(role) WHERE role='admin'")
            c.execute('CREATE TABLE access_history(id INTEGER PRIMARY KEY,actor TEXT,created_at TEXT,operation TEXT,payload TEXT)')
            c.execute("INSERT INTO users VALUES(1,'alice','Администратор','admin')")
            c.execute('CREATE TABLE team_messages(id INTEGER PRIMARY KEY,user_id INTEGER REFERENCES users(id),body TEXT,created_at TEXT)')
            c.execute("INSERT INTO team_messages VALUES(1,1,'Старое сообщение','2026-01-01')")
            c.execute('PRAGMA user_version=3')
        upgraded = Store(folder, 'alice')
        self.assertEqual(upgraded.profile()['role'], 'admin')
        old = upgraded.team_chat()['messages'][0]
        self.assertEqual((old['body'], old['kind']), ('Старое сообщение', 'text'))
        mid = upgraded.team_send_sticker(image_bytes())
        self.assertTrue(upgraded.team_sticker(mid).startswith(b'\x89PNG'))

    def test_interrupted_file_write_does_not_publish_message(self):
        with patch('kanban.store.os.replace', side_effect=OSError('SMB unavailable')):
            with self.assertRaises(OSError):
                self.store.team_send_sticker(image_bytes(), 'interrupted')
        self.assertEqual(self.store.team_chat()['messages'], [])
        self.assertEqual(list((self.store.folder / 'attachments').glob('*.partial')), [])
        self.store.team_send_sticker(image_bytes(), 'interrupted')
        self.assertEqual(len(self.store.team_chat()['messages']), 1)

    def test_sticker_chat_retry_unread_and_backup_restore(self):
        png = image_bytes()
        mid = self.store.team_send_sticker(png, 'stable-request')
        self.assertEqual(mid, self.store.team_send_sticker(png, 'stable-request'))
        with self.assertRaises(ValueError):
            self.store.team_send_sticker(image_bytes(100, 100), 'stable-request')
        bob = Store(self.store.folder, 'bob')
        messages = bob.team_chat()
        self.assertEqual(messages['unread'], 1)
        self.assertEqual(len(messages['messages']), 1)
        self.assertEqual(messages['messages'][0]['kind'], 'sticker')
        data = bob.team_sticker(mid)
        bob.team_mark_read(mid)
        self.assertEqual(bob.team_chat()['unread'], 0)
        backup = self.store.backup(self.root / 'backups')
        restore(backup, self.root / 'restored')
        restored = Store(self.root / 'restored', 'bob')
        self.assertEqual(restored.team_sticker(mid), data)
        with self.store.connection() as c:
            name = c.execute('SELECT stored_name FROM team_stickers').fetchone()[0]
        (self.store.folder / 'attachments' / name).write_bytes(b'broken')
        with self.assertRaises(IOError):
            bob.team_sticker(mid)
        with self.assertRaises(IOError):
            self.store.backup(self.root / 'backups')
