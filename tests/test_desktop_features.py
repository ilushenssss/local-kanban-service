import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock
from PyQt5.QtCore import QSettings, Qt
from PyQt5.QtGui import QTextCharFormat
from PyQt5.QtWidgets import QWidget, QVBoxLayout
from test_ui import APP, wait_for
from kanban.notifications import Notifications
from kanban.store import Store
from kanban.spellcheck import SpellTextEdit, SpellLineEdit, spell_engine


class DesktopFeaturesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.settings = QSettings(str(Path(self.tmp.name) / 'settings.ini'), QSettings.IniFormat)
        self.window = QWidget()
        self.window.messenger = QWidget(self.window)
        self.window.messenger.open_chat = lambda: self.window.messenger.show()
        self.addCleanup(self.window.close)
        with patch('kanban.notifications.theme_settings', return_value=self.settings), patch('kanban.notifications.QSystemTrayIcon.isSystemTrayAvailable', return_value=False):
            self.notifications = Notifications(self.window)
        self.addCleanup(self.notifications.shutdown)
        self.notifications.available = True
        self.addCleanup(lambda: delattr(self.window, 'messenger'))

    def message(self, mid, login='other', kind='text'):
        return {'id': mid, 'login': login, 'author': 'Иван Петров', 'body': 'Новое сообщение', 'kind': kind}

    def test_history_own_messages_and_duplicates_do_not_notify(self):
        n = self.notifications
        with patch.object(n, 'chat_active', return_value=False), patch.object(n.tray, 'showMessage') as show:
            n.observe([self.message(1)], 'me')
            n.flush()
            show.assert_not_called()
            n.observe([self.message(2, 'me')], 'me')
            n.flush()
            show.assert_not_called()
            n.observe([self.message(3)], 'me')
            n.observe([self.message(3), self.message(4, kind='sticker')], 'me')
            n.flush()
            show.assert_called_once()
            self.assertIn('2', show.call_args.args[0])
            self.assertIn('фотостикер', show.call_args.args[1])
            n.observe([self.message(4)], 'me')
            n.flush()
            self.assertEqual(show.call_count, 1)

    def test_active_chat_and_disabled_notifications_are_silent(self):
        n = self.notifications
        n.observe([], 'me')
        with patch.object(n, 'chat_active', return_value=True), patch.object(n.tray, 'showMessage') as show:
            n.observe([self.message(1)], 'me')
            n.flush()
            show.assert_not_called()
        self.settings.setValue('notifications/enabled', False)
        with patch.object(n, 'chat_active', return_value=False), patch.object(n.tray, 'showMessage') as show:
            n.observe([self.message(2)], 'me')
            n.flush()
            show.assert_not_called()

    def test_click_restores_minimized_window_and_opens_chat(self):
        self.window.showMaximized()
        self.window.showMinimized()
        self.notifications.open_chat()
        self.assertFalse(self.window.isMinimized())
        self.assertTrue(self.window.isMaximized())
        self.assertTrue(self.window.messenger.isVisible())

    def prepare_task_feed(self):
        creator = Store(Path(self.tmp.name) / 'data', 'creator')
        creator.initialize_admin('creator')
        uid = creator.save_user('alice', 'Алиса')
        board = creator.board_action('Уведомления')
        creator.set_board_access(board, [uid], creator.profile()['id'])
        self.window.store = Store(creator.folder, 'alice')
        def work(fn, cb):
            try:
                result = fn()
            except Exception as error:
                cb(None, error)
            else:
                cb(result, None)
        self.window.work = work
        self.window.show_task_notification = Mock()
        return creator, board

    def test_task_notification_click_settings_and_safe_repeat(self):
        creator, board = self.prepare_task_feed()
        n = self.notifications
        with patch.object(n.tray, 'showMessage') as show:
            n.poll()
            self.assertEqual(n.task_cursor, 0)
            task = creator.save_task({'board_id': board, 'column_id': creator.columns(board)[0]['id'], 'title': 'Новая задача', 'assignees': ['alice']})['id']
            # A failed poll keeps its cursor, so the next poll recovers the event.
            with patch.object(self.window.store, 'notification_snapshot', side_effect=OSError('Network')):
                n.poll()
            self.assertEqual(n.task_cursor, 0)
            n.poll()
            self.assertEqual(len(n.task_pending), 1)
            with patch.object(self.window.store, 'task_updates', side_effect=OSError('SMB unavailable')):
                n.flush_tasks()
            show.assert_not_called()
            self.assertEqual(len(n.task_pending), 1)
            n.flush_tasks()
            show.assert_called_once()
            self.assertIn('Вам назначена задача', show.call_args.args[1])
            n.open_notification()
            self.window.show_task_notification.assert_called_once_with(task)
            self.assertEqual(n.last_task, task)
            n.poll()
            n.flush_tasks()
            self.assertEqual(show.call_count, 1)
            self.settings.setValue('notifications/tasks', False)
            current = creator.detail(task)
            current['priority'] = 'Высокий'
            creator.save_task(current)
            n.poll()
            n.flush_tasks()
            self.assertEqual(show.call_count, 1)

    def test_read_task_does_not_show_queued_toast_and_badges_update(self):
        creator, board = self.prepare_task_feed()
        n = self.notifications
        self.window.update_board_badges = Mock()
        n.poll()
        tid = creator.save_task({'board_id': board, 'column_id': creator.columns(board)[0]['id'], 'title': 'Назначенная', 'assignees': ['alice']})['id']
        n.poll()
        self.assertEqual(self.window.update_board_badges.call_args.args[0][board]['tasks'], 1)
        snapshot = self.window.store.detail(tid)
        self.window.store.mark_task_notifications_read(tid, snapshot['notification_cursor'])
        with patch.object(n.tray, 'showMessage') as show:
            n.flush_tasks()
            show.assert_not_called()
        n.poll()
        self.assertEqual(self.window.update_board_badges.call_args.args[0][board]['total'], 0)

    def test_task_notification_rechecks_access_before_desktop_display(self):
        creator, board = self.prepare_task_feed()
        n = self.notifications
        n.poll()
        creator.save_task({'board_id': board, 'column_id': creator.columns(board)[0]['id'], 'title': 'Закрытая', 'assignees': ['alice']})
        n.poll()
        self.assertEqual(len(n.task_pending), 1)
        creator.set_board_access(board, [], creator.profile()['id'])
        with patch.object(n.tray, 'showMessage') as show:
            n.flush_tasks()
            show.assert_not_called()

    def test_spelling_formats_without_modifying_text_and_handles_emoji(self):
        engine = spell_engine()
        previous = engine.enabled()
        self.addCleanup(lambda: engine.set_enabled(previous))
        engine.set_enabled(True)
        wait_for(lambda: engine.dictionaries is not None or engine.error, timeout=15)
        self.assertIsNone(engine.error)
        self.assertTrue(engine.correct('сотрудниками'))
        self.assertTrue(engine.correct('договорами'))
        self.assertTrue(engine.correct('hello'))
        self.assertFalse(engine.correct('опечятка'))
        self.assertEqual(engine.mistakes('https://example.com/опечятка userr@example.com API TeamSync'), [])
        editor = SpellTextEdit()
        editor.setPlainText('😀 Привет, опечятка hello heloo')
        editor.spell_highlighter.rehighlight()
        block = editor.document().firstBlock()
        formats = block.layout().formats()
        self.assertEqual(len(formats), 2)
        self.assertEqual(formats[0].start, 11)  # Emoji occupies two UTF-16 units.
        self.assertEqual(formats[0].format.underlineStyle(), QTextCharFormat.SpellCheckUnderline)
        self.assertEqual(editor.toPlainText(), '😀 Привет, опечятка hello heloo')
        preview = QWidget()
        preview.resize(620, 230)
        self.addCleanup(preview.close)
        layout = QVBoxLayout(preview)
        line = SpellLineEdit('Договор: опечятка в названии')
        layout.addWidget(line)
        layout.addWidget(editor)
        preview.show()
        APP.processEvents()
        evidence = Path(__file__).resolve().parent.parent / 'docs/screenshots'
        evidence.mkdir(parents=True, exist_ok=True)
        preview.grab().save(str(evidence / 'spelling-linux.png'))
        engine.set_enabled(False)
        editor.spell_highlighter.rehighlight()
        self.assertEqual(len(block.layout().formats()), 0)

    def test_line_edit_paints_and_keeps_undo(self):
        engine = spell_engine()
        wait_for(lambda: engine.dictionaries is not None or engine.error, timeout=15)
        line = SpellLineEdit()
        self.addCleanup(line.close)
        line.resize(420, 42)
        line.show()
        line.setText('Привет, опечятка')
        line.grab()
        line.insert('!')
        line.undo()
        self.assertEqual(line.text(), 'Привет, опечятка')
