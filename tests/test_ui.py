import copy
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from PyQt5 import sip
from PyQt5.QtCore import Qt, QDate, QPoint, QSettings
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import (QApplication, QDialog, QDialogButtonBox, QFileDialog, QInputDialog, QLineEdit, QMessageBox,
                             QPlainTextEdit, QPushButton, QScrollArea, QTableWidget)
from kanban.store import SCHEMA_VERSION, Store
from kanban.cards import TASK_NOTICE_ROLE
from kanban.theme import stylesheet
from kanban.ui import BoardCreateDialog, MainWindow, TaskDialog, CatalogDialog
from kanban.theme import PRIORITIES
from kanban.presentation import history_text, history_html

SETTINGS_DIR = tempfile.TemporaryDirectory()
QSettings.setPath(QSettings.NativeFormat, QSettings.UserScope, SETTINGS_DIR.name)

APP = QApplication.instance() or QApplication([])
APP.setStyle('Fusion')
APP.setStyleSheet(stylesheet())


def wait_for(predicate, timeout=8):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        APP.processEvents()
        if predicate():
            return
        QTest.qWait(10)
    raise AssertionError('UI operation timed out')


def choose_option(combo, value, mouse=False):
    ancestor = combo.parentWidget()
    while ancestor is not None:
        if isinstance(ancestor, QScrollArea):
            ancestor.ensureWidgetVisible(combo)
        ancestor = ancestor.parentWidget()
    APP.processEvents()
    QTest.mouseMove(combo, combo.rect().center())
    QTest.mouseClick(combo, Qt.LeftButton, pos=combo.rect().center())
    QTest.qWait(30)
    wait_for(lambda: combo.view().isVisible())
    index = combo.findData(value)
    if index < 0:
        raise AssertionError('Value not offered by native combo: ' + str(value))
    if mouse:
        model_index = combo.model().index(index, 0)
        combo.view().scrollTo(model_index)
        point = combo.view().visualRect(model_index).center()
        QTest.mouseMove(combo.view().viewport(), point)
        QTest.qWait(30)
        QTest.mouseClick(combo.view().viewport(), Qt.LeftButton, pos=point)
    else:
        QTest.keyClick(combo.view(), Qt.Key_Home)
        for _ in range(index):
            QTest.keyClick(combo.view(), Qt.Key_Down)
        QTest.keyClick(combo.view(), Qt.Key_Return)
    wait_for(lambda: not combo.view().isVisible())


class UITest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / 'data', 'tester')
        self.store.initialize_admin('tester')
        self.bid = self.store.board_action('Проектный отдел')
        self.columns = self.store.columns(self.bid)
        import getpass
        for login in dict.fromkeys(['colleague', 'designer', 'another', getpass.getuser()]):
            if login != 'tester':
                uid = self.store.save_user(login, login)
                self.store.set_role(uid, 'manager')
        self.grant_all(self.bid)
        self.result = self.store.save_task({'board_id': self.bid, 'column_id': self.columns[0]['id'], 'title': 'Подготовить договор', 'description': 'Согласовать условия', 'due_date': '2020-01-01', 'priority': 'Высокий', 'assignees': ['tester'], 'labels': ['Документы'], 'checklist': [{'text': 'Проверить реквизиты', 'done': 1}, {'text': 'Подписать', 'done': 0}]})
        self.windows = []
    def grant_all(self, board_id):
        users = self.store.access_users()
        self.store.set_board_access(board_id, [u['id'] for u in users], self.store.profile()['id'])

    def test_employee_changes_own_avatar_on_existing_tasks_and_messages(self):
        from PyQt5.QtCore import QUrl
        from PyQt5.QtGui import QColor, QImage, QTextDocument
        from PyQt5.QtWidgets import QLabel

        employee = Store(self.store.folder, 'colleague')
        self.store.set_role(employee.profile()['id'], 'employee')
        task = self.store.detail(self.result['id'])
        self.store.save_task(dict(task, assignees=['colleague']))
        employee.comment(task['id'], 'Обсуждение задачи')
        employee.team_send('Сообщение доски', board_id=self.bid)
        source = QImage(240, 180, QImage.Format_RGB32)
        source.fill(QColor('#367bd1'))
        picture = str(Path(self.tmp.name) / 'avatar.png')
        self.assertTrue(source.save(picture))
        with patch('kanban.ui.Store', return_value=employee):
            owner = MainWindow(str(self.store.folder))
            self.windows.append(owner)
            owner.show()
            wait_for(lambda: not owner.loading and owner.board_id == self.bid)
        with patch('kanban.ui.Store', return_value=self.store):
            observer = MainWindow(str(self.store.folder))
            self.windows.append(observer)
            observer.show()
            wait_for(lambda: not observer.loading and observer.board_id == self.bid)

        def settings(action):
            def interact(dialog):
                buttons = {b.text(): b for b in dialog.findChildren(QPushButton)}
                wait_for(buttons['Выбрать фото…'].isEnabled)
                self.assertEqual(buttons['Удалить аватар'].isEnabled(), action == 'Удалить аватар')
                buttons[action].click()
                wait_for(buttons['Выбрать фото…'].isEnabled)
                self.assertEqual(buttons['Удалить аватар'].isEnabled(), action == 'Выбрать фото…')
                return QDialog.Accepted

            with patch.object(QDialog, 'exec_', new=interact), \
                 patch.object(QFileDialog, 'getOpenFileName', return_value=(picture, '')):
                owner.profile_button.click()

        settings('Выбрать фото…')
        self.assertFalse(owner.profile_button.icon().isNull())
        image = Store(self.store.folder, 'colleague').own_avatar()
        self.assertIsNone(self.store.own_avatar())
        self.assertEqual(QImage.fromData(image).pixelColor(48, 48).name(), '#367bd1')
        self.assertEqual(QImage.fromData(image).pixelColor(0, 0).alpha(), 0)
        observer.refresh()
        item = observer.card_lists[0].item(0)
        wait_for(lambda: item.data(Qt.UserRole).get('avatars') == {'colleague': image})
        observer.open_personal_task(task)
        wait_for(lambda: bool(observer.dialogs))
        dialog = next(iter(observer.dialogs))
        wait_for(lambda: dialog.assignees.list.count() > 0)
        self.assertFalse(dialog.assignees.list.item(0).icon().isNull())
        self.assertIn('src="avatar:', dialog.comments_markup)
        observer.messenger.poll()
        wait_for(lambda: 'src="avatar:' in (observer.messenger.markup or ''))
        import re
        url = re.search(r'src="(avatar:[^"]+)', observer.messenger.markup)[1]
        self.assertFalse(observer.messenger.timeline.document().resource(QTextDocument.ImageResource, QUrl(url)).isNull())
        profile_link = QUrl('employee:{}'.format(employee.profile()['id']))
        profiles = []

        def profile(card):
            self.assertEqual(card.windowTitle(), 'Профиль сотрудника')
            self.assertIn('colleague', [label.text() for label in card.findChildren(QLabel)])
            self.assertTrue(any(label.accessibleName() == 'Фото профиля' and not label.pixmap().isNull()
                                for label in card.findChildren(QLabel)))
            profiles.append(card.parentWidget())
            return QDialog.Accepted

        observer.messenger.open_chat()
        with patch.object(QDialog, 'exec_', new=profile):
            observer.messenger.timeline.anchorClicked.emit(profile_link)
            wait_for(lambda: len(profiles) == 1)
            dialog.comments.anchorClicked.emit(profile_link)
            wait_for(lambda: len(profiles) == 2)
        self.assertEqual(profiles, [observer.messenger, dialog])

        settings('Удалить аватар')
        self.assertTrue(owner.profile_button.icon().isNull())
        observer.refresh()
        observer.messenger.poll()
        dialog.refresh_extras()
        wait_for(lambda: 'avatars' not in item.data(Qt.UserRole) and 'src="avatar:' not in observer.messenger.markup
                 and 'src="avatar:' not in dialog.comments_markup and dialog.assignees.list.item(0).icon().isNull())
        self.assertIsNone(Store(self.store.folder, 'colleague').own_avatar())
        self.assertIn('Сообщение доски', observer.messenger.markup)
        dialog.refresh_timer.stop()
        wait_for(lambda: not dialog.jobs)
        self.assertTrue(dialog.close())

    def test_directory_read_only_and_manager_can_see_blocked_employee(self):
        from kanban.store import AccessDenied
        member = Store(self.store.folder, 'colleague')
        self.store.set_role(member.profile()['id'], 'employee')
        blocked = Store(self.store.folder, 'another').profile()['id']
        self.store.set_user_restricted(blocked, True)
        manager = Store(self.store.folder, 'designer')
        for store, blocked_visible in ((member, False), (manager, True)):
            dialog = CatalogDialog(store, self.bid, 'users')
            self.windows.append(dialog)
            dialog.show()
            wait_for(lambda: not dialog.jobs)
            items = [dialog.list.item(i) for i in range(dialog.list.count())]
            self.assertEqual('another' in [item.text() for item in items], blocked_visible)
            self.assertFalse(any(item.flags() & Qt.ItemIsUserCheckable for item in items))
            self.assertFalse(dialog.save_button.isVisible())
            self.assertFalse(dialog.edit_button.isVisible())
            self.assertTrue(all(set(item.data(Qt.UserRole)) == {'id', 'display_name', 'restricted', 'avatar'} for item in items))
            with self.assertRaises(AccessDenied):
                store.set_user_restricted(blocked, False)
        with self.assertRaises(AccessDenied):
            member.employee_profile(blocked)
        self.assertEqual(manager.employee_profile(blocked)['display_name'], 'another')

    def test_employee_directory_opens_profile_and_sends_ephemeral_message(self):
        colleague = Store(self.store.folder, 'colleague')
        windows = []
        for store in (self.store, colleague):
            with patch('kanban.ui.Store', return_value=store):
                window = MainWindow(str(store.folder))
                self.windows.append(window)
                windows.append(window)
                window.show()
                wait_for(lambda: window.private_chats.ready and not window.jobs)
        sender, receiver = windows
        roster = CatalogDialog(self.store, self.bid, 'users', sender)
        self.windows.append(roster)
        roster.show()
        wait_for(lambda: not roster.jobs)
        self.assertGreaterEqual(roster.width(), 780)
        self.assertGreaterEqual(roster.height(), 640)
        item = next(roster.list.item(i) for i in range(roster.list.count())
                    if roster.list.item(i).data(Qt.UserRole)['login'] == 'colleague')

        def profile(dialog):
            self.assertEqual(dialog.windowTitle(), 'Профиль сотрудника')
            message = next(button for button in dialog.findChildren(QPushButton) if button.text() == 'Написать сообщение')
            message.click()
            return dialog.result()

        with patch.object(QDialog, 'exec_', new=profile):
            roster.list.scrollToItem(item)
            QTest.mouseClick(roster.list.viewport(), Qt.LeftButton, pos=roster.list.visualItemRect(item).center())
            wait_for(lambda: sender.private_chats.dialog is not None)
        dialog = sender.private_chats.dialog
        colleague_id = colleague.profile()['id']
        chat = dialog.conversations[colleague_id]
        body = 'Личное сообщение, которое не осталось в базе'
        chat.input.setPlainText(body)
        QTest.keyClick(chat.input, Qt.Key_Return)
        sender_id = self.store.profile()['id']
        wait_for(lambda: receiver.private_chats.dialog is not None and bool(chat.timeline.bubbles))
        inbox = receiver.private_chats.dialog.conversations[sender_id]
        outgoing, out_body = chat.timeline.bubbles[0]
        incoming, in_body = inbox.timeline.bubbles[0]
        self.assertEqual((out_body.toPlainText(), in_body.toPlainText()), (body, body))
        wait_for(lambda: outgoing.x() > 0)
        self.assertEqual(outgoing.geometry().right(), outgoing.parentWidget().width() - 1)
        self.assertEqual(incoming.x(), 0)
        self.assertNotIn(body.encode(), self.store.db.read_bytes())
        chat.input.setPlainText('Черновик для коллеги')
        other_id = Store(self.store.folder, 'designer').profile()['id']
        sender.open_private_chat(other_id, 'designer')
        self.assertIs(sender.private_chats.dialog, dialog)
        self.assertIs(dialog.stack.currentWidget(), dialog.conversations[other_id])
        answer = 'Ответ в другой переписке'
        inbox.input.setPlainText(answer)
        inbox.send_button.click()
        wait_for(lambda: dialog.unread.get(colleague_id) == 1 and not receiver.jobs)
        self.assertIs(dialog.stack.currentWidget(), dialog.conversations[other_id])
        dialog.peers.setCurrentItem(dialog.items[colleague_id])
        self.assertIs(dialog.stack.currentWidget(), chat)
        self.assertNotIn(colleague_id, dialog.unread)
        self.assertEqual(chat.input.toPlainText(), 'Черновик для коллеги')
        self.assertEqual(chat.timeline.bubbles[-1][1].toPlainText(), answer)
        self.assertTrue(sender.close())
        self.assertTrue(receiver.close())
        self.assertIsNone(sender.private_chats.dialog)
        self.assertIsNone(receiver.private_chats.dialog)

    def test_newer_database_reports_required_client_update(self):
        newer = SCHEMA_VERSION + 1
        with self.store.connection() as connection:
            connection.execute('PRAGMA user_version={}'.format(newer))
        original = self.store.db.read_bytes()
        window = MainWindow(str(self.store.folder))
        self.windows.append(window)
        window.show()
        wait_for(lambda: not window.loading and not window.jobs)
        self.assertIsNone(window.store)
        self.assertIn('схемы базы — v{}'.format(newer), window.status.text())
        self.assertIn('поддерживает до v{}'.format(SCHEMA_VERSION), window.status.text())
        self.assertIn('Обновите файлы TeamSync', window.status.text())
        self.assertEqual(self.store.db.read_bytes(), original)

    def test_sidebar_badges_show_source_and_clear_only_read_events(self):
        self.store.save_user('badge-user', 'Получатель')
        self.grant_all(self.bid)
        second = self.store.board_action('Другая доска')
        self.grant_all(second)
        with patch('kanban.ui.Store', side_effect=lambda folder: Store(folder, 'badge-user')):
            w = MainWindow(str(self.store.folder))
            self.windows.append(w)
            w.show()
            wait_for(lambda: not w.loading and not w.jobs)
        current = self.store.detail(self.result['id'])
        current['assignees'] = sorted(set(current['assignees'] + [w.store.login]))
        self.store.save_task(current)
        self.store.team_send('Сообщение первой доски', board_id=self.bid)
        self.store.team_send('Сообщение второй доски', board_id=second)
        w.notifications.poll()
        def node(bid):
            return next(w.board_tree.topLevelItem(i).child(j) for i in range(w.board_tree.topLevelItemCount()) for j in range(w.board_tree.topLevelItem(i).childCount()) if w.board_tree.topLevelItem(i).child(j).data(0, Qt.UserRole) == bid)
        wait_for(lambda: node(self.bid).text(1) == '2' and node(second).text(1) == '1')
        self.assertEqual(node(self.bid).text(0), 'Проектный отдел')
        self.assertIn('Чат: 1 · Задачи: 1', node(self.bid).toolTip(0))
        self.assertEqual(node(self.bid).parent().text(1), '3')
        board_widget = w.card_lists[0]
        self.assertEqual(board_widget.item(0).data(TASK_NOTICE_ROLE), 1)
        self.assertEqual(w.notification_button.text(), '3')

        def inbox_items(expected, activate_task=False):
            captured = []

            def inspect(dialog):
                inbox = w.notifications.inbox
                wait_for(lambda: inbox.count() == expected)
                items = [inbox.item(i) for i in range(expected)]
                captured.extend(item.text() for item in items)
                if activate_task:
                    task_item = next(item for item in items if item.data(Qt.UserRole)[0] is not None)
                    inbox.itemActivated.emit(task_item)
                return dialog.result()

            with patch.object(QDialog, 'exec_', new=inspect):
                w.open_notifications()
            return captured

        entries = '\n'.join(inbox_items(3))
        self.assertIn('Подготовить договор', entries)
        self.assertIn('Сообщение первой доски', entries)
        self.assertIn('Сообщение второй доски', entries)
        evidence = Path(__file__).resolve().parent.parent / 'docs/screenshots'
        evidence.mkdir(parents=True, exist_ok=True)
        w.grab().save(str(evidence / 'sidebar-board-badges-linux.png'))
        w.update_board_badges({self.bid: {'chat': 120, 'tasks': 0, 'total': 120}, second: {'chat': 1, 'tasks': 0, 'total': 1}})
        self.assertEqual(node(self.bid).text(1), '99+')
        self.assertIn('120', node(self.bid).toolTip(1))
        self.assertIs(w.card_lists[0], board_widget)
        w.notifications.poll()
        wait_for(lambda: not w.notifications.loading and not w.jobs)
        w.messenger.open_chat()
        wait_for(lambda: node(self.bid).text(1) == '1')
        self.assertEqual(node(second).text(1), '1')
        inbox_items(2, activate_task=True)
        wait_for(lambda: node(self.bid).text(1) == '' and not w.jobs)
        self.assertEqual(node(second).text(1), '1')
        self.assertEqual(w.store.board_badges()[self.bid]['total'], 0)
        self.assertEqual(w.card_lists[0].item(0).data(TASK_NOTICE_ROLE), 0)
        remaining = inbox_items(1)
        self.assertIn('Сообщение второй доски', remaining[0])

    def test_manual_read_in_inbox_retries_and_clears_badges(self):
        self.store.save_user('badge-user', 'Получатель')
        self.grant_all(self.bid)
        task = self.store.detail(self.result['id'])
        task['assignees'].append('badge-user')
        self.store.save_task(task)
        self.store.team_send('Сообщение', board_id=self.bid)
        with patch('kanban.ui.Store', side_effect=lambda folder: Store(folder, 'badge-user')):
            w = MainWindow(str(self.store.folder))
            self.windows.append(w)
            w.show()
            wait_for(lambda: not w.loading and not w.jobs)

        def inspect(dialog):
            inbox = w.notifications.inbox
            mark = w.notifications.read_button
            wait_for(lambda: inbox.count() == 2)
            row = next(i for i in range(inbox.count()) if inbox.item(i).data(Qt.UserRole + 1)[0] == 'chat')
            inbox.setCurrentRow(row)
            with patch.object(w.store, 'mark_notification_read', side_effect=OSError('SMB unavailable')):
                mark.click()
                wait_for(mark.isEnabled)
            self.assertIn('Не удалось', w.notifications.inbox_status.text())
            self.assertEqual(inbox.count(), 2)
            mark.click()
            wait_for(lambda: inbox.count() == 1 and w.notification_button.text() == '1')
            self.assertEqual(w.messenger.bubble.unread, 0)
            mark.click()
            wait_for(lambda: inbox.count() == 0 and not w.notification_button.text())
            self.assertFalse(mark.isEnabled())
            self.assertEqual(w.card_lists[0].item(0).data(TASK_NOTICE_ROLE), 0)
            self.assertEqual(w.board_counts[self.bid]['total'], 0)
            self.assertFalse(w.messenger.isVisible())
            self.assertFalse(w.dialogs)
            return QDialog.Rejected

        with patch.object(QDialog, 'exec_', new=inspect):
            w.open_notifications()

    def test_task_notification_opens_card_and_keeps_existing_draft(self):
        w = MainWindow(str(self.store.folder))
        self.windows.append(w)
        w.show()
        wait_for(lambda: not w.loading and not w.jobs)
        w.show_task_notification(self.result['id'])
        wait_for(lambda: bool(w.dialogs) and not w.jobs and not any(d.jobs for d in w.dialogs))
        dialog = next(iter(w.dialogs))
        self.windows.append(dialog)
        dialog.title.setText('Несохранённый черновик')
        w.show_task_notification(self.result['id'])
        wait_for(lambda: not w.jobs)
        self.assertEqual(len(w.dialogs), 1)
        self.assertEqual(dialog.title.text(), 'Несохранённый черновик')
        self.store.set_board_access(self.bid, [], self.store.profile()['id'])
        w.show_task_notification(self.result['id'])
        wait_for(lambda: not w.jobs)
        self.assertIn('Не удалось открыть задачу', w.status.text())
        self.assertEqual(dialog.title.text(), 'Несохранённый черновик')

    def test_personal_section_employee_creation_management_and_restore(self):
        from PyQt5.QtWidgets import QInputDialog, QMenu
        self.store.save_user('private-user', 'Личный пользователь')
        with patch('kanban.ui.Store', side_effect=lambda folder: Store(folder, 'private-user')):
            w = MainWindow(str(self.store.folder))
            self.windows.append(w)
            w.show()
            wait_for(lambda: not w.loading and not w.jobs)
        root = next(w.board_tree.topLevelItem(i) for i in range(w.board_tree.topLevelItemCount()) if w.board_tree.topLevelItem(i).text(0) == 'Личное')
        self.assertTrue(w.create_board_button.isEnabled())
        self.assertTrue(w.create_project_item.isDisabled())
        self.assertEqual(root.child(1).data(0, Qt.UserRole + 1), 'create_personal')

        def fill(dialog):
            self.assertEqual(dialog.folder.count(), 1)
            self.assertEqual(dialog.folder.currentData(), 'personal')
            self.assertTrue(dialog.project.isHidden())
            dialog.name.setText('Планы на неделю')
            dialog.create.click()
            return dialog.result()

        with patch.object(BoardCreateDialog, 'exec_', new=fill):
            w.create_board_button.click()
            wait_for(lambda: not w.mutating and not w.loading and not w.jobs)
        private = w.board_id
        self.assertIsNotNone(private)
        self.assertTrue(w.permissions['personal'])
        self.assertTrue(w.new_task_button.isEnabled())
        self.assertTrue(w.create_board_button.isEnabled())
        self.assertFalse(any(b['id'] == private for b in self.store.boards()))
        menu = QMenu(w)
        share = menu.addAction('Участники доски')
        settings = menu.addAction('Колонки доски')
        w.apply_menu_permissions(menu)
        self.assertFalse(share.isEnabled())
        self.assertTrue(settings.isEnabled())
        w.new_task_button.click()
        wait_for(lambda: bool(w.dialogs) and not any(d.jobs for d in w.dialogs))
        dialog = next(iter(w.dialogs))
        self.windows.append(dialog)
        dialog.title.setText('Запланировать личные дела')
        dialog.save_button.click()
        wait_for(lambda: not dialog.pending and not w.jobs and not w.loading)
        self.assertEqual(w.store.tasks(private)[0]['title'], 'Запланировать личные дела')
        evidence = Path(__file__).resolve().parent.parent / 'docs/screenshots'
        evidence.mkdir(parents=True, exist_ok=True)
        w.grab().save(str(evidence / 'personal-boards-linux.png'))
        with patch.object(QMessageBox, 'question', return_value=QMessageBox.Yes):
            w.board_action('archive')
            wait_for(lambda: not w.mutating and not w.loading and not w.jobs)
        self.assertEqual(w.store.archived_personal_boards()[0]['id'], private)
        with patch.object(QInputDialog, 'getItem', side_effect=lambda *args: (args[3][0], True)):
            w.restore_personal_board()
            wait_for(lambda: not w.mutating and not w.loading and not w.jobs)
        self.assertEqual(w.board_id, private)
        self.assertEqual(len(w.store.tasks(private)), 1)

    def test_board_chat_switch_keeps_drafts_and_late_results_separate(self):
        second = self.store.board_action('Вторая переписка')
        self.grant_all(second)
        self.store.team_send('Сообщение A', board_id=self.bid)
        self.store.team_send('Сообщение B', board_id=second)
        w = MainWindow(str(self.store.folder))
        self.windows.append(w)
        w.show()
        wait_for(lambda: not w.loading and not w.jobs)
        a = w.messenger
        a.open_chat()
        wait_for(lambda: not w.jobs)
        a.input.setPlainText('Черновик A')
        self.store.team_send('Запоздалое A', board_id=self.bid)
        pending = []
        with patch.object(w, 'work', side_effect=lambda fn, cb: pending.append((fn, cb))):
            a.poll()
            w.activate_board_chat(second, 'Вторая переписка')
            b = w.messenger
            b.input.setPlainText('Черновик B')
            old_fn, old_done = pending.pop(0)
            new_fn, new_done = pending.pop(0)
            new_done(new_fn(), None)
            old_done(old_fn(), None)
            self.assertEqual([m['body'] for m in b.messages], ['Сообщение B'])
            self.assertEqual(b.input.toPlainText(), 'Черновик B')
            self.assertFalse(a.isVisible())
            self.assertFalse(a.bubble.isVisible())
            while pending:
                fn, cb = pending.pop(0)
                cb(fn(), None)
        w.activate_board_chat(self.bid, 'Проектный отдел')
        wait_for(lambda: not w.jobs)
        self.assertIs(w.messenger, a)
        self.assertEqual(a.input.toPlainText(), 'Черновик A')
        self.assertEqual([m['body'] for m in a.messages], ['Сообщение A', 'Запоздалое A'])
        evidence = Path(__file__).resolve().parent.parent / 'docs/screenshots'
        evidence.mkdir(parents=True, exist_ok=True)
        w.grab().save(str(evidence / 'board-chat-linux.png'))

    def test_label_dialog_renames_and_deletes_assigned_label(self):
        d = CatalogDialog(self.store, self.bid, 'labels')
        self.windows.append(d)
        d.show()
        wait_for(lambda: not d.jobs)
        d.list.setCurrentRow(0)
        d.edit_entry()
        d.name.setText('Договоры')
        evidence = Path(__file__).resolve().parent.parent / 'docs/screenshots'
        evidence.mkdir(parents=True, exist_ok=True)
        d.grab().save(str(evidence / 'labels-edit-linux.png'))
        d.save_entry()
        wait_for(lambda: not d.jobs)
        self.assertEqual(self.store.detail(self.result['id'])['labels'], ['Договоры'])
        d.list.setCurrentRow(0)
        with patch.object(QMessageBox, 'question', return_value=QMessageBox.Yes):
            d.delete_entry()
            wait_for(lambda: not d.jobs)
        self.assertEqual(self.store.detail(self.result['id'])['labels'], [])
        self.assertEqual(d.list.count(), 0)

    def test_close_to_tray_keeps_polling_and_explicit_exit_stops(self):
        w = MainWindow(str(self.store.folder))
        self.windows.append(w)
        w.show()
        wait_for(lambda: not w.loading and not w.jobs)
        w.notifications.available = True
        settings = QSettings(str(Path(self.tmp.name) / 'desktop.ini'), QSettings.IniFormat)
        settings.setValue('notifications/close_to_tray', True)
        w.notifications.settings = settings
        self.assertFalse(w.close())
        self.assertFalse(w.isVisible())
        self.assertTrue(w.messenger.timer.isActive())
        w.notifications.open_chat()
        self.assertTrue(w.isVisible())
        wait_for(lambda: not w.jobs)
        w.notifications.exit_app()
        self.assertFalse(w.messenger.timer.isActive())
        self.assertFalse(w.timer.isActive())

    def test_sticker_preview_retry_and_inline_image(self):
        from test_stickers import image_bytes
        from PyQt5.QtCore import QUrl, QTimer, QMimeData
        from PyQt5.QtGui import QImage, QTextDocument, QPainter, QColor
        from PyQt5.QtWidgets import QDialog
        from unittest.mock import patch
        w = MainWindow(str(self.store.folder))
        self.windows.append(w)
        w.resize(1000, 800)
        w.show()
        wait_for(lambda: w.store is not None and not w.loading and not w.jobs)
        m = w.messenger
        m.open_chat()
        wait_for(lambda: not m.loading and not w.jobs)
        self.store.add_standard_sticker('Привет', image_bytes())

        def select_standard(dialog):
            wait_for(lambda: not dialog.pending and dialog.list.count() == 1)
            self.assertFalse(dialog.manage)
            dialog.list.setCurrentRow(0)
            dialog.primary.click()
            return dialog.result()

        with patch.object(QDialog, 'exec_', new=select_standard):
            self.assertEqual(m.image_button.text(), 'Стикеры')
            m.image_button.click()
        self.assertTrue(m.preview.isVisible())
        m.remove_sticker()
        m.input.setPlainText('Текстовый черновик')
        mime = QMimeData()
        sample = QImage(400, 400, QImage.Format_ARGB32)
        sample.fill(Qt.transparent)
        painter = QPainter(sample)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setBrush(QColor('#0F766E'))
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(10, 10, 380, 380)
        painter.setPen(Qt.white)
        font = painter.font()
        font.setPixelSize(50)
        painter.setFont(font)
        painter.drawText(sample.rect(), Qt.AlignCenter, 'Привет!')
        painter.end()
        mime.setImageData(sample)
        APP.clipboard().setMimeData(mime)
        QTest.keyClick(m.input, Qt.Key_V, Qt.ControlModifier)
        APP.clipboard().clear()
        wait_for(lambda: not m.preparing and not w.jobs)
        self.assertTrue(m.preview.isVisible())
        self.assertEqual(m.input.toPlainText(), 'Текстовый черновик')
        original = w.store.team_send_sticker
        def uncertain(data, rid, board_id=None):
            original(data, rid, board_id=board_id)
            raise OSError('Connection lost after commit')
        with patch.object(w.store, 'team_send_sticker', side_effect=uncertain):
            m.send_sticker()
            wait_for(lambda: not m.sending and not w.jobs)
        self.assertIsNotNone(m.sticker_retry)
        self.assertFalse(m.remove_button.isEnabled())
        m.send_sticker()
        wait_for(lambda: not m.sending and not m.loading and not w.jobs)
        m.poll()
        wait_for(lambda: not m.loading and not w.jobs)
        self.assertIsNone(m.sticker_retry)
        self.assertEqual(len(m.messages), 1)
        self.assertEqual(m.input.toPlainText(), 'Текстовый черновик')
        self.assertIn('sticker:', m.markup)
        self.assertFalse(m.timeline.document().resource(QTextDocument.ImageResource, QUrl('sticker:1')).isNull())
        evidence = Path(__file__).resolve().parent.parent / 'docs/screenshots'
        evidence.mkdir(parents=True, exist_ok=True)
        w.grab().save(str(evidence / 'chat-sticker-linux.png'))
        def close_viewer():
            for dialog in m.findChildren(QDialog):
                if dialog.windowTitle() == 'Фотостикер':
                    dialog.accept()
        QTimer.singleShot(20, close_viewer)
        m.view_sticker(QUrl('sticker:1'))

    def test_missing_sticker_does_not_block_reading_and_can_be_reloaded(self):
        from test_stickers import image_bytes

        member = Store(self.store.folder, 'colleague')
        sticker_id = self.store.team_send_sticker(image_bytes(), board_id=self.bid)
        latest = self.store.team_send('Сообщение после стикера', board_id=self.bid)
        with self.store.connection() as connection:
            filename = connection.execute('SELECT stored_name FROM team_stickers WHERE message_id=?',
                                          (sticker_id,)).fetchone()[0]
        path = self.store.folder / 'attachments' / filename
        data = path.read_bytes()
        path.unlink()
        self.assertEqual(member.team_chat(board_id=self.bid)['unread'], 2)

        with patch('kanban.ui.Store', return_value=member):
            window = MainWindow(str(self.store.folder))
            self.windows.append(window)
            window.show()
            wait_for(lambda: window.board_id == self.bid and not window.loading)
        chat = window.messenger
        wait_for(lambda: 'Файл стикера отсутствует' in (chat.markup or ''))
        self.assertEqual(member.team_chat(board_id=self.bid)['unread'], 2)
        window.activateWindow()
        chat.open_chat()
        wait_for(lambda: chat.read_cursor == latest and chat.bubble.unread == 0)
        self.assertEqual(member.team_chat(board_id=self.bid)['unread'], 0)
        self.assertIn('Сообщение после стикера', chat.markup)

        wait_for(lambda: not chat.loading)
        path.write_bytes(data)
        chat.hide()
        chat.open_chat()
        wait_for(lambda: sticker_id in chat.stickers)
        self.assertNotIn('Файл стикера отсутствует', chat.markup)

    def test_employee_catalog_restricts_open_app_and_restores_access(self):
        with patch('kanban.ui.Store', side_effect=lambda folder: Store(folder, 'colleague')):
            w = MainWindow(str(self.store.folder))
            self.windows.append(w)
            w.show()
            wait_for(lambda: bool(w.card_lists) and not w.jobs)
        w.open_task(w.card_lists[0].item(0).data(Qt.UserRole))
        wait_for(lambda: bool(w.dialogs) and not w.jobs)
        task_dialog = next(iter(w.dialogs))
        wait_for(lambda: not task_dialog.jobs)
        w.messenger.open_chat()
        self.assertTrue(w.messenger.isVisible())

        dialog = CatalogDialog(self.store, self.bid, 'users')
        self.windows.append(dialog)
        dialog.show()
        wait_for(lambda: not dialog.jobs)
        users = {dialog.list.item(n).data(Qt.UserRole)['login']: dialog.list.item(n)
                 for n in range(dialog.list.count())}
        self.assertFalse(users['tester'].flags() & Qt.ItemIsUserCheckable)
        item = users['colleague']
        dialog.list.setCurrentItem(item)
        with patch.object(QMessageBox, 'question', return_value=QMessageBox.No):
            QTest.keyClick(dialog.list, Qt.Key_Space)
        self.assertEqual(item.checkState(), Qt.Unchecked)
        with patch.object(QMessageBox, 'question', return_value=QMessageBox.Yes):
            QTest.keyClick(dialog.list, Qt.Key_Space)
            wait_for(lambda: not dialog.jobs)
        self.assertEqual(item.checkState(), Qt.Checked)
        w.notifications.poll()
        wait_for(lambda: w.access_restricted)
        self.assertFalse(w.centralWidget().isVisible())
        self.assertFalse(task_dialog.isVisible())
        self.assertFalse(w.messenger.isVisible())
        self.assertFalse(w.messenger.bubble.isVisible())
        self.assertIn('ограничен администратором', w.statusBar().currentMessage())
        QTest.keyClick(dialog.list, Qt.Key_Space)
        wait_for(lambda: not dialog.jobs)
        self.assertEqual(item.checkState(), Qt.Unchecked)
        w.notifications.poll()
        wait_for(lambda: not w.access_restricted and not w.loading)
        self.assertTrue(w.centralWidget().isVisible())
        self.assertTrue(task_dialog.isVisible())

    def test_role_and_board_access_dialogs(self):
        from kanban.ui import AccessDialog
        from unittest.mock import patch
        from PyQt5.QtWidgets import QMessageBox
        dialog = AccessDialog(self.store, self.bid)
        self.windows.append(dialog)
        dialog.show()
        wait_for(lambda: not dialog.jobs)
        self.assertGreater(dialog.list.count(), 1)
        self.assertTrue(dialog.choice.isEnabled())
        evidence = Path(__file__).resolve().parent.parent / 'docs/screenshots'
        evidence.mkdir(parents=True, exist_ok=True)
        dialog.grab().save(str(evidence / 'access-board-linux.png'))
        with patch.object(QMessageBox, 'question', return_value=QMessageBox.Yes):
            dialog.save()
            wait_for(lambda: not dialog.jobs)
        roles = AccessDialog(self.store)
        self.windows.append(roles)
        roles.show()
        wait_for(lambda: not roles.jobs)
        colleague = next(i for i in range(roles.list.count()) if roles.list.item(i).data(Qt.UserRole)['login'] == 'colleague')
        roles.list.setCurrentRow(colleague)
        roles.choice.setCurrentIndex(roles.choice.findData('employee'))
        with patch.object(QMessageBox, 'question', return_value=QMessageBox.Yes):
            roles.save()
            wait_for(lambda: not roles.jobs)
        self.assertEqual(Store(self.store.folder, 'colleague').profile()['role'], 'employee')
        employee_dialog = AccessDialog(Store(self.store.folder, 'colleague'), self.bid)
        self.windows.append(employee_dialog)
        employee_dialog.show()
        wait_for(lambda: not employee_dialog.jobs)
        self.assertFalse(employee_dialog.save_button.isEnabled())
        self.assertIn('Недостаточно прав', employee_dialog.status.text())

    def tearDown(self):
        for w in list(self.windows):
            if sip.isdeleted(w):
                continue
            if hasattr(w, 'timer'):
                w.timer.stop()
            if hasattr(w, 'refresh_timer'):
                w.refresh_timer.stop()
            wait_for(lambda: not getattr(w, 'jobs', ()))
            if not sip.isdeleted(w):
                with patch.object(QMessageBox, 'question', return_value=QMessageBox.Yes):
                    w.close()
        APP.processEvents()
        self.tmp.cleanup()
    def dialog(self):
        d = TaskDialog(self.store, self.columns, self.store.detail(self.result['id']))
        self.windows.append(d)
        d.destroyed.connect(lambda: self.windows.remove(d) if d in self.windows else None)
        d.show()
        wait_for(lambda: not d.jobs)
        return d
    def test_chat_opens_at_end_and_same_board_returns_from_my_tasks(self):
        from PyQt5.QtWidgets import QScrollArea, QPushButton
        colleague = Store(self.store.folder, 'colleague')
        for i in range(35):
            colleague.comment(self.result['id'], 'Длинная переписка номер {}'.format(i))
        d = self.dialog()
        wait_for(lambda: d.comments.verticalScrollBar().maximum() > 0)
        wait_for(lambda: d.comments.verticalScrollBar().value() == d.comments.verticalScrollBar().maximum())
        self.assertFalse(d.task_page.findChildren(QScrollArea))
        self.assertIsInstance(d.tabs.widget(0), type(d.task_page))
        d.comments.verticalScrollBar().setValue(0)
        colleague.comment(self.result['id'], 'Новое сообщение после открытия')
        d.refresh_extras()
        wait_for(lambda: not d.jobs)
        self.assertEqual(d.comments.verticalScrollBar().value(), 0)
        w = MainWindow(str(self.store.folder))
        self.windows.append(w)
        w.show()
        wait_for(lambda: not w.loading and w.board_id is not None)
        current = w.board_tree.topLevelItem(0).child(0)
        personal = w.board_tree.topLevelItem(2)
        my_tasks = personal.child(0)
        self.assertEqual(my_tasks.text(0), 'Мои задачи')
        self.assertFalse(any(button.text() == 'Мои задачи' for button in w.sidebar.findChildren(QPushButton)))
        w.board_tree.setCurrentItem(my_tasks)
        QTest.keyClick(w.board_tree, Qt.Key_Return)
        self.assertIs(w.pages.currentWidget(), w.my_tasks)
        wait_for(lambda: not w.loading)
        self.assertIs(w.board_tree.currentItem(), my_tasks)
        w.select_tree_board(current, 0)
        self.assertEqual(w.pages.currentIndex(), 0)
        w.collapse_sidebar()
        visible = [b for b in w.sidebar.findChildren(QPushButton) if b.isVisible()]
        self.assertEqual(visible, [w.collapse_button])
        self.assertEqual(w.collapse_button.text(), '')
        self.assertFalse(w.collapse_button.icon().isNull())
        APP.processEvents()
        self.assertTrue(w.sidebar.rect().contains(w.collapse_button.geometry().center()))
        evidence = Path(__file__).resolve().parent.parent / 'docs/screenshots'
        w.grab().save(str(evidence / 'collapsed-menu-linux.png'))
        w.collapse_sidebar()
        self.assertTrue(w.board_tree.isVisible())

    def test_enter_sends_in_both_chats_and_shift_enter_adds_line(self):
        d = self.dialog()
        w = MainWindow(str(self.store.folder))
        self.windows.append(w)
        w.show()
        wait_for(lambda: w.store is not None and not w.loading and not w.jobs)
        m = w.messenger
        m.open_chat()
        wait_for(lambda: not w.jobs)
        for editor, send_button, pending, records in (
            (d.comment_text, d.comment_button, lambda: d.pending or bool(d.jobs),
             lambda: self.store.detail(self.result['id'])['comments']),
            (m.input, m.send_button, lambda: m.sending or bool(w.jobs),
             lambda: w.store.team_chat()['messages']),
        ):
            count = len(records())
            editor.setFocus()
            editor.setPlainText('Первая строка')
            editor.moveCursor(editor.textCursor().End)
            QTest.keyClick(editor, Qt.Key_Return, Qt.ShiftModifier)
            self.assertEqual(editor.toPlainText(), 'Первая строка\n')
            self.assertEqual(len(records()), count)
            editor.insertPlainText('Вторая строка')
            send_button.setEnabled(False)
            QTest.keyClick(editor, Qt.Key_Return)
            self.assertEqual(len(records()), count)
            self.assertEqual(editor.toPlainText(), 'Первая строка\nВторая строка')
            send_button.setEnabled(True)
            QTest.keyClick(editor, Qt.Key_Return)
            QTest.keyClick(editor, Qt.Key_Return)
            wait_for(lambda: not pending())
            self.assertEqual(len(records()), count + 1)
            self.assertEqual(records()[-1]['body'], 'Первая строка\nВторая строка')
            self.assertEqual(editor.toPlainText(), '')
            QTest.keyClick(editor, Qt.Key_Enter)
            self.assertEqual(len(records()), count + 1)
        self.assertTrue(d.isVisible())

    def test_team_messenger_badge_and_retry_preserves_draft(self):
        w = MainWindow(str(self.store.folder))
        self.windows.append(w)
        w.show()
        wait_for(lambda: w.store is not None and not w.loading and not w.jobs)
        colleague = Store(self.store.folder, 'colleague')
        colleague.team_send('Общее сообщение')
        colleague.team_send('Ещё одно')
        m = w.messenger
        m.poll()
        wait_for(lambda: not m.loading and m.bubble.unread == 2)
        self.assertTrue(m.bubble.isVisible())
        self.assertFalse(m.isVisible())
        evidence = Path(__file__).resolve().parent.parent / 'docs/screenshots'
        w.grab().save(str(evidence / 'messenger-badge-linux.png'))
        m.bubble.click()
        wait_for(lambda: not w.jobs and m.bubble.unread == 0)
        self.assertTrue(m.isVisible())
        self.assertFalse(m.bubble.isVisible())
        m.input.setPlainText('Моё сообщение')
        original = w.store.team_send
        def uncertain(text, rid=None, board_id=None):
            original(text, rid, board_id=board_id)
            raise OSError('Connection lost after commit')
        with patch.object(w.store, 'team_send', side_effect=uncertain):
            m.send()
            wait_for(lambda: not m.sending)
        self.assertEqual(m.input.toPlainText(), 'Моё сообщение')
        m.input.setPlainText('Новый черновик')
        m.send()
        wait_for(lambda: not m.sending and not w.jobs)
        self.assertEqual(m.input.toPlainText(), 'Новый черновик')
        self.assertEqual(len(w.store.team_chat()['messages']), 3)
        w.grab().save(str(evidence / 'messenger-open-linux.png'))
        m.hide()
        self.assertTrue(m.bubble.isVisible())
        self.assertEqual(m.input.toPlainText(), 'Новый черновик')

    def test_create_board_from_projects_and_choose_folder(self):
        existing = self.store.board_action('Разработка / Релиз')
        self.grant_all(existing)
        with patch('kanban.ui.Store', side_effect=lambda folder: Store(folder, 'tester')):
            w = MainWindow(str(self.store.folder))
            self.windows.append(w)
            w.show()
            wait_for(lambda: not w.loading and w.create_project_item is not None)
        self.assertFalse(w.create_project_item.isDisabled())
        tabs = w.project_sections
        self.assertFalse(tabs.tabBar().isVisible())

        def fill(dialog):
            self.assertEqual([dialog.folder.itemText(i) for i in range(dialog.folder.count())],
                             ['Рабочие доски', 'Проекты', 'Личное'])
            self.assertEqual(dialog.folder.currentData(), 'projects')
            self.assertEqual(dialog.project.itemText(0), 'Разработка')
            dialog.name.setText('Спринт')
            self.assertFalse(dialog.create.isEnabled())
            dialog.project.setCurrentIndex(0)
            dialog.project.setEditText('Разработка')
            dialog.create.click()
            return dialog.result()

        w.board_tree.setCurrentItem(w.create_project_item)
        with patch.object(BoardCreateDialog, 'exec_', new=fill):
            QTest.keyClick(w.board_tree, Qt.Key_Return)
            wait_for(lambda: not w.mutating and not w.loading and w.board_id in w.board_items)
        created = w.board_items[w.board_id]
        self.assertEqual(created.text(0), 'Спринт')
        self.assertEqual(created.parent().text(0), 'Разработка')
        self.assertEqual(created.parent().parent().text(0), 'Проекты')
        self.assertIs(w.board_tree.currentItem(), created)
        self.assertIn('Разработка / Спринт', [b['name'] for b in self.store.boards()])
        expected_columns = [
            'Проект ТИ',
            'ТИ согласовано внутри отдела',
            'ТИ согласовано со смежными отделами',
            'ТИ согласовано с РП',
            'ТИ открыто',
        ]
        self.assertEqual([column['name'] for column in self.store.columns(w.board_id)], expected_columns)
        self.assertEqual([column['name'] for column in w.columns_data], expected_columns)
        self.assertTrue(tabs.tabBar().isVisible())
        self.assertEqual([tabs.tabText(i) for i in range(tabs.count())],
                         ['Текущие работы', 'ИД', 'Баги'])
        self.assertEqual(tabs.currentIndex(), 0)
        canvas = w.scroll.widget()
        for index in (1, 2):
            QTest.mouseClick(tabs.tabBar(), Qt.LeftButton, pos=tabs.tabBar().tabRect(index).center())
            self.assertEqual(tabs.currentIndex(), index)
            if index == 1:
                self.assertIs(tabs.currentWidget(), w.identification_page)
            else:
                self.assertIs(tabs.currentWidget(), w.bugzilla_page)
            self.assertFalse(w.scroll.isVisible())
            self.assertFalse(w.new_task_button.isEnabled())
        tabs.setCurrentIndex(0)
        self.assertIs(w.scroll.widget(), canvas)
        self.assertTrue(w.scroll.isVisible())
        self.assertTrue(w.new_task_button.isEnabled())
        tabs.setCurrentIndex(2)

        def fill_new_project(dialog):
            self.assertEqual(dialog.folder.currentData(), 'work')
            dialog.folder.setCurrentIndex(dialog.folder.findData('projects'))
            dialog.project.setEditText('Новый проект')
            dialog.name.setText('Планирование')
            dialog.create.click()
            return dialog.result()

        with patch.object(BoardCreateDialog, 'exec_', new=fill_new_project):
            w.create_board_button.click()
            wait_for(lambda: not w.mutating and not w.loading and w.board_id in w.board_items)
        created = w.board_items[w.board_id]
        self.assertEqual(created.text(0), 'Планирование')
        self.assertEqual(created.parent().text(0), 'Новый проект')
        self.assertEqual([column['name'] for column in w.columns_data], expected_columns)
        self.assertEqual(tabs.currentIndex(), 0)
        self.assertEqual(created.parent().parent().text(0), 'Проекты')

        w.select_tree_board(w.board_items[self.bid], 0)
        wait_for(lambda: not w.loading and w.board_id == self.bid)
        self.assertFalse(tabs.tabBar().isVisible())
        self.assertTrue(w.scroll.isVisible())

    def test_admin_configures_installation_and_redmine_without_ini(self):
        from PyQt5.QtWidgets import QLineEdit, QPushButton, QTextEdit
        from kanban import installation

        base = Path(self.tmp.name) / 'installation'
        base.mkdir()
        legacy = base / 'kanban.ini'
        legacy.write_text('[storage]\nfolder = {}\n[redmine]\nurl = https://redmine.example.test\n'
                          '[redmine.status]\nНовый = Проект ТИ\n'.format(self.store.folder), encoding='utf-8')
        window = MainWindow(None, installation_base=base)
        self.windows.append(window)
        self.assertIsNone(window.store)

        def save(dialog):
            folder = next(field for field in dialog.findChildren(QLineEdit)
                          if field.accessibleName() == 'Папка базы')
            self.assertEqual(folder.text(), str(self.store.folder))
            submit = next(b for b in dialog.findChildren(QPushButton) if b.text() == 'Сохранить')
            submit.click()
            wait_for(lambda: not window.jobs)
            return dialog.result()

        with patch('kanban.ui.Store', side_effect=lambda folder: Store(folder, 'tester')), \
             patch.object(QDialog, 'exec_', new=save):
            window.open_connection_settings()
            wait_for(lambda: not window.loading and bool(window.card_lists))
        self.assertEqual(installation.load_folder(base), str(self.store.folder))
        self.assertEqual(window.redmine_url, 'https://redmine.example.test')
        self.assertEqual(window.redmine_statuses, {'новый': 'Проект ТИ'})
        legacy.unlink()
        with patch('kanban.ui.Store', side_effect=lambda folder: Store(folder, 'colleague')):
            colleague = MainWindow(None, installation_base=base)
            self.windows.append(colleague)
            wait_for(lambda: not colleague.loading and bool(colleague.card_lists))
        self.assertEqual(colleague.redmine_url, 'https://redmine.example.test')
        with patch.object(QDialog, 'exec_') as exec_dialog:
            colleague.open_connection_settings()
            exec_dialog.assert_not_called()

        window.redmine_client = object()
        def edit(dialog):
            url = next(field for field in dialog.findChildren(QLineEdit)
                       if field.placeholderText() == 'https://redmine.example.org')
            url.setText('http://new-redmine.example.test')
            dialog.findChild(QTextEdit).setPlainText('Открыт = ТИ открыто')
            next(b for b in dialog.findChildren(QPushButton) if b.text() == 'Сохранить').click()
            wait_for(lambda: not window.jobs)
            return dialog.result()

        with patch.object(QDialog, 'exec_', new=edit):
            window.open_connection_settings()
            wait_for(lambda: not window.loading)
        self.assertIsNone(window.redmine_client)
        colleague.refresh()
        wait_for(lambda: not colleague.loading)
        self.assertEqual(colleague.redmine_url, 'http://new-redmine.example.test')
        self.assertEqual(colleague.redmine_statuses, {'открыт': 'ТИ открыто'})

    def test_bugzilla_export_saves_the_json_response(self):
        from kanban.bugzilla import BugzillaAuthError
        from kanban.installation import save_folder

        base = Path(self.tmp.name)
        locations = patch('kanban.ui.QStandardPaths.writableLocation', return_value=str(base))
        locations.start()
        self.addCleanup(locations.stop)
        save_folder(base, self.store.folder)
        (base / 'employees.json').write_text('["anna"]', encoding='utf-8')
        board = self.store.board_action('Разработка / Версия 1')
        other = self.store.board_action('Разработка / Версия 2')
        self.grant_all(board)

        with patch('kanban.ui.Store', side_effect=lambda folder: Store(folder, 'tester')):
            window = MainWindow(None, installation_base=base)
            self.windows.append(window)
            window.show()
            wait_for(lambda: not window.loading and window.store is not None)
        url = 'https://bugs.example.test/bugzilla'
        domain = 'example.test'

        def save_connection(dialog):
            fields = {field.accessibleName(): field for field in dialog.findChildren(QLineEdit)}
            fields['Адрес Bugzilla'].setText(url)
            fields['Почтовый домен Bugzilla'].setText('@' + domain)
            next(button for button in dialog.findChildren(QPushButton) if button.text() == 'Сохранить').click()
            wait_for(lambda: not window.jobs)
            return dialog.result()

        with patch.object(QDialog, 'exec_', new=save_connection):
            window.open_connection_settings()
            wait_for(lambda: not window.loading)
        self.assertEqual(self.store.bugzilla_settings(), {'url': url, 'domain': domain})
        window.redmine_login = 'redmine-user@old.example.test'
        window.select_tree_board(window.board_items[board], 0)
        wait_for(lambda: not window.loading and window.project_sections_board == board)
        window.project_sections.setCurrentWidget(window.bugzilla_page)

        raw = ('{ "bugs": [{"id": 42, "assigned_to": "ANNA@example.test", "summary": "Задача", '
               '"cf_event_1": "Событие", "cf_cr_num": "КР-1"}] }\n')
        unmatched = raw.replace('ANNA@example.test', 'other@example.test')
        target = Path(self.tmp.name) / 'bugzilla-42.json'
        previews = []
        password_modes = []

        def run_dialog(dialog):
            preview = dialog.findChild(QPlainTextEdit)
            if preview is None:
                bug_id, *fields = dialog.findChildren(QLineEdit)
                bug_id.setText('42')
                password_modes.append([field.echoMode() for field in fields])
                if fields:
                    fields[0].setText('пример-пароля')
                dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.Ok).click()
                return dialog.result()
            previews.append((preview.isReadOnly(), preview.toPlainText()))
            next(button for button in dialog.findChildren(QPushButton) if button.text() == 'Сохранить JSON…').click()
            return QDialog.Accepted

        with patch.object(QFileDialog, 'getSaveFileName', return_value=(str(target), 'JSON (*.json)')), \
             patch.object(QDialog, 'exec_', new=run_dialog), \
             patch('kanban.bugzilla.read_bug', side_effect=[raw, raw, unmatched, BugzillaAuthError('Вход отклонён'), raw]) as fetch:
            card_counts = []
            for _ in range(5):
                window.export_bugzilla_bug()
                wait_for(lambda: not window.bugzilla_loading)
                card_counts.append(len(window.bugzilla_page.snapshot))
        self.assertEqual(card_counts, [1, 1, 0, 0, 1])
        self.assertEqual(fetch.call_count, 5)
        for request in fetch.call_args_list:
            self.assertEqual(request.args, (url, '42'))
            self.assertEqual(request.kwargs, {'login': 'redmine-user@example.test', 'password': 'пример-пароля'})
        self.assertEqual(password_modes, [[QLineEdit.Password], [], [], [], [QLineEdit.Password]])
        self.assertEqual(previews, [(True, raw), (True, raw), (True, unmatched), (True, raw)])
        self.assertEqual(target.read_bytes(), raw.encode('utf-8'))
        self.assertNotIn('пример-пароля'.encode(), self.store.db.read_bytes())
        self.assertIn('Событие'.encode(), self.store.db.read_bytes())
        self.assertEqual(window.bugzilla_page.snapshot, ({'id': 42, 'summary': 'Задача',
                                                        'cf_event_1': 'Событие', 'cf_cr_num': 'КР-1',
                                                        'assigned_to': 'ANNA@example.test'},))
        column = self.store.columns(board)[0]['name']
        self.store.link_redmine_issue(board, 27001, 'ТИ со ссылкой', column, column,
                                      issue_references={'bugs': ['142', 'BZ #00042']})
        bulk = {'42': window.bugzilla_page.snapshot[0], '4': {
            'id': 4, 'summary': 'Свободный баг', 'cf_event_1': 'Проверить', 'cf_cr_num': 'ТИ-2',
        }}
        with patch('kanban.bugzilla.search_board_bugs', return_value=bulk) as search, \
             patch.object(QDialog, 'exec_') as prompt:
            window.bugzilla_page.refresh_button.click()
            wait_for(lambda: not window.bugzilla_loading)
            prompt.assert_not_called()
        search.assert_called_once_with(url, 'Разработка', 'Версия 1', ['anna'],
                                       login='redmine-user@example.test', password='пример-пароля')
        window.refresh()
        wait_for(lambda: '42' in window.bugzilla_page.linked)

        def column_labels(name):
            column = next(column for column in window.bugzilla_page.columns if column.accessibleName() == name)
            return '\n'.join(column.item(i).data(Qt.AccessibleTextRole) for i in range(column.count())).splitlines()

        self.assertEqual(column_labels('Не привязаны'), ['4', 'Свободный баг', 'ТИ-2', 'Проверить'])
        self.assertEqual(column_labels('Привязаны'), ['42', 'Задача', 'КР-1', 'Событие'])
        self.store.link_redmine_issue(board, 27002, 'ТИ с другим багом', column, column,
                                      issue_references={'bugs': ['4']})
        window.refresh()
        wait_for(lambda: '4' in window.bugzilla_page.linked)
        self.assertIn('4', column_labels('Привязаны'))
        window.project_sections.setCurrentIndex(0)
        wait_for(lambda: not window.loading and window.last_snapshot[0] == 'board')
        items = [cards.item(i) for cards in window.card_lists for i in range(cards.count())]
        self.assertEqual({item.data(Qt.UserRole)['redmine_id']: item.data(Qt.UserRole)['linked_bugs']
                          for item in items}, {27001: ['BZ #00042'], 27002: ['4']})
        window.select_tree_board(window.board_items[other], 0)
        wait_for(lambda: not window.loading and window.project_sections_board == other)
        window.project_sections.setCurrentWidget(window.bugzilla_page)
        self.assertEqual(window.bugzilla_page.snapshot, ())
        self.assertTrue(window.bugzilla_page.refresh_button.isEnabled())
        self.assertEqual(window.bugzilla_page.findChildren(QPushButton), [window.bugzilla_page.refresh_button])
        self.assertFalse(window.bugzilla_cache_path(board).exists())
        window.close()

        with patch('kanban.ui.Store', side_effect=lambda folder: Store(folder, 'colleague')):
            reopened = MainWindow(None, installation_base=base)
            self.windows.append(reopened)
            reopened.show()
            wait_for(lambda: not reopened.loading and board in reopened.board_items)
        reopened.select_tree_board(reopened.board_items[board], 0)
        wait_for(lambda: not reopened.loading and reopened.project_sections_board == board)
        reopened.project_sections.setCurrentWidget(reopened.bugzilla_page)
        wait_for(lambda: reopened.bugzilla_page.snapshot == tuple(bulk.values()))
        self.assertIsNone(reopened.bugzilla_session)

        updated = {'42': dict(bulk['42'], summary='Обновлённый заголовок')}
        with patch.object(reopened, 'prompt_bugzilla_export', return_value=(None, 'пример-пароля', False)) as prompt, \
             patch('kanban.bugzilla.search_board_bugs', return_value=updated) as refresh:
            reopened.bugzilla_page.refresh_button.click()
            wait_for(lambda: not reopened.bugzilla_loading)
        prompt.assert_called_once_with('colleague@example.test', None, 'bulk')
        refresh.assert_called_once_with(url, 'Разработка', 'Версия 1', ['anna'],
                                        login='colleague@example.test', password='пример-пароля')
        self.assertEqual(reopened.bugzilla_page.snapshot, tuple(updated.values()))
        self.assertEqual(reopened.bugzilla_page.columns[2].count(), 0)
        from PyQt5.QtGui import QDesktopServices
        bug = dict(updated['42'], deadline='2026-12-20', assigned_to='anna@example.test', status='Открыто')
        edited = {'cf_event_1': 'Выпуск ТИ', 'deadline': '2026-12-31',
                  'cf_cr_num': 'ТИ-123', 'assigned_to': 'COLLEAGUE@example.test'}
        choices = (['Событие', 'Выпуск ТИ'], {'COLLEAGUE@example.test': 'Борис Петров'}, [])

        def edit(dialog):
            wait_for(lambda: not dialog.pending)
            self.assertEqual(dialog.deadline.text(), '2026-12-20')
            self.assertFalse(dialog.save_button.isEnabled())
            self.assertFalse(dialog.comment_section.isHidden())
            self.assertFalse(dialog.send_button.isEnabled())
            dialog.action.setCurrentIndex(dialog.action.findData('Выпуск ТИ'))
            dialog.deadline.set_value('2026-12-31')
            dialog.ti.setText('ТИ-123')
            dialog.assignee.setCurrentIndex(dialog.assignee.findData('COLLEAGUE@example.test'))
            next(button for button in dialog.findChildren(QPushButton) if button.text() == 'Открыть в Bugzilla').click()
            draft = '  Описание исправления  '
            dialog.comment.setPlainText(draft)
            dialog.save_button.click()
            wait_for(lambda: not dialog.pending)
            self.assertEqual(dialog.comment.toPlainText(), draft)
            self.assertFalse(dialog.save_button.isEnabled())
            dialog.send_button.click()
            wait_for(lambda: not dialog.pending and 'Текст сохранён' in dialog.comment_status.text())
            self.assertEqual(dialog.comment.toPlainText(), draft)
            dialog.send_button.click()
            wait_for(lambda: dialog.comment_status.text() == 'Комментарий отправлен.')
            self.assertEqual(dialog.comment.toPlainText(), '')
            self.assertEqual(dialog.result(), QDialog.Rejected)
            dialog.accept()
            return dialog.result()

        card_list = next(cards for cards in reopened.bugzilla_page.columns if cards.count())
        with patch.object(QDialog, 'exec_', new=edit), \
             patch.object(QDesktopServices, 'openUrl', return_value=True) as open_url, \
             patch('kanban.bugzilla.read_bug_details', side_effect=[
                 bug, bug, dict(bug, **edited, assigned_to_detail={'real_name': 'Борис Петров'})]), \
             patch('kanban.bugzilla.bug_edit_choices', return_value=choices), \
             patch('kanban.bugzilla.add_bug_comment', side_effect=[OSError('Таймаут'), 7]) as send, \
             patch('kanban.bugzilla.update_bug_fields') as save:
            card_list.itemClicked.emit(card_list.item(0))
            self.assertEqual(open_url.call_args.args[0].toString(), url + '/show_bug.cgi?id=42')
        save.assert_called_once_with(url, '42', edited, login='colleague@example.test', password='пример-пароля')
        shared = self.store.bugzilla_board_snapshot(board)['cards']['42']
        self.assertEqual((shared['cf_event_1'], shared['cf_cr_num']), ('Выпуск ТИ', 'ТИ-123'))
        mine = reopened.bugzilla_page.columns[2].item(0)
        self.assertEqual(mine.data(Qt.UserRole)[0::2], ('42', True))
        self.assertEqual(mine.data(Qt.UserRole)[3], 'БП')
        self.assertIn('Борис Петров', mine.toolTip())
        self.assertFalse(reopened.bugzilla_page.columns[1].count())
        self.assertEqual(send.call_count, 2)
        send.assert_called_with(url, '42', 'Описание исправления',
                                login='colleague@example.test', password='пример-пароля')
        with patch.object(reopened, 'open_bugzilla_card') as open_card:
            reopened.comment_bugzilla_bug('42')
        open_card.assert_called_once_with('42', focus_comment=True)

    def test_remembered_integration_passwords_survive_restart_and_can_be_forgotten(self):
        from PyQt5.QtWidgets import QCheckBox
        from kanban.credentials import PasswordStore
        from kanban.redmine import RedmineAuthError

        redmine_url, bugzilla_url = 'https://redmine.test', 'https://bugs.test'
        self.store.set_redmine_settings(redmine_url, {})
        self.store.set_connection_settings(bugzilla_config={'url': bugzilla_url, 'domain': 'work.test'})
        folder = Path(self.tmp.name) / 'local-credentials'

        def create_window():
            vault = PasswordStore()
            vault.folder = folder
            with patch('kanban.ui.Store', return_value=self.store), patch('kanban.ui.PasswordStore', return_value=vault):
                window = MainWindow(str(self.store.folder))
                self.windows.append(window)
                window.show()
                wait_for(lambda: not window.jobs and window.redmine_url == redmine_url
                         and window.bugzilla_config['url'] == bugzilla_url)
            return window

        def sign_in(dialog):
            fields = dialog.findChildren(QLineEdit)
            remember = dialog.findChild(QCheckBox)
            self.assertFalse(remember.isChecked())
            remember.setChecked(True)
            if dialog.windowTitle() == 'Вход в Redmine':
                fields[0].setText('custom-login')
                fields[1].setText('пароль redmine')
            else:
                fields[0].setText('42')
                fields[1].setText('пароль bugzilla')
            dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.Ok).click()
            return dialog.result()

        with patch('kanban.credentials.dpapi', side_effect=lambda data, **kwargs: bytes(value ^ 193 for value in data)):
            first = create_window()
            with patch.object(QDialog, 'exec_', new=sign_in), patch('kanban.redmine.connect') as connect:
                first.redmine_operation(lambda *args: [], 'Готово')
                wait_for(lambda: not first.mutating and first.redmine_client is not None)
                connect.assert_called_once_with(redmine_url, 'custom-login', 'пароль redmine')
            with patch.object(QDialog, 'exec_', new=sign_in), patch('kanban.bugzilla.read_bug', return_value='{}') as read, \
                 patch.object(first, 'show_bugzilla_json'):
                first.export_bugzilla_bug()
                wait_for(lambda: not first.bugzilla_loading and first.bugzilla_session is not None)
                read.assert_called_once_with(bugzilla_url, '42', login='custom-login@work.test', password='пароль bugzilla')
            wait_for(lambda: not first.jobs)
            self.assertTrue(first.close())

            reopened = create_window()
            with patch.object(QDialog, 'exec_') as prompt, patch('kanban.redmine.connect', side_effect=RedmineAuthError('Вход отклонён')) as connect:
                reopened.redmine_operation(lambda *args: [], 'Готово')
                wait_for(lambda: not reopened.mutating)
                prompt.assert_not_called()
                connect.assert_called_once_with(redmine_url, 'custom-login', 'пароль redmine')
            self.assertIsNone(reopened.saved_passwords.load('redmine', redmine_url))
            self.assertEqual(reopened.bugzilla_password((bugzilla_url, 'custom-login@work.test')), 'пароль bugzilla')

            def forget(dialog):
                next(button for button in dialog.findChildren(QPushButton) if button.text() == 'Забыть пароль Bugzilla').click()
                return QDialog.Rejected

            with patch.object(QDialog, 'exec_', new=forget):
                reopened.profile_button.click()
            self.assertIsNone(reopened.saved_passwords.load('bugzilla', bugzilla_url))
            self.assertEqual(reopened.bugzilla_login_for(reopened.bugzilla_config), 'tester@work.test')

    def test_bugzilla_deadline_calendar_opens_without_deadline_in_response(self):
        from kanban.bugzilla_page import BugEditDialog

        saved = []
        for editable in (True, False):
            dialog = BugEditDialog('42', lambda: None, lambda form: saved.append(form.changes()),
                                   lambda _: None, None)
            self.windows.append(dialog)
            dialog.load({'id': 42, 'summary': 'Баг без срока в ответе'}, ([], {}, []), editable)
            dialog.show()
            editor = dialog.deadline.editor
            self.assertEqual(editor.isEnabled(), editable)
            if not editable:
                continue
            self.assertFalse(dialog.save_button.isEnabled())
            QTest.mouseClick(editor, Qt.LeftButton, pos=editor.rect().center())
            wait_for(editor.calendarWidget().isVisible)
            QTest.keyClick(editor.calendarWidget(), Qt.Key_Escape)
            dialog.deadline.set_value('2026-12-31')
            dialog.save_button.click()
        self.assertEqual(saved, [{'deadline': '2026-12-31'}])

    def test_redmine_import_and_shared_refresh(self):
        from PyQt5.QtWidgets import QInputDialog, QLineEdit, QMenu
        from kanban.installation import save_folder
        from kanban.cards import CardDelegate
        from kanban.redmine import RedmineAuthError
        base = Path(self.tmp.name)
        save_folder(base, self.store.folder)
        projects = base / 'all_project.json'
        projects.write_text('{"Разработка": 49, "Внедрение": 88}', encoding='utf-8')
        board = self.store.board_action('Разработка / ТИ', column_names=['Проект ТИ', 'ТИ открыто'])
        self.grant_all(board)
        colleague_id = next(u['id'] for u in self.store.access_users() if u['login'] == 'colleague')
        self.store.set_role(colleague_id, 'employee')

        def window_for(login):
            with patch('kanban.ui.Store', side_effect=lambda folder: Store(folder, login)):
                window = MainWindow(None, 'https://redmine.example.test', installation_base=base)
                self.windows.append(window)
                window.show()
                wait_for(lambda: not window.loading and board in window.board_items)
            window.select_tree_board(window.board_items[board], 0)
            wait_for(lambda: not window.loading and window.board_id == board)
            return window

        admin = window_for('tester')
        member = window_for('colleague')
        self.assertTrue(admin.new_task_button.isEnabled())
        self.assertFalse(member.new_task_button.isEnabled())
        self.assertTrue(member.refresh_ti_button.isEnabled())
        self.assertFalse(member.import_sppi_button.isEnabled())
        self.assertTrue(all(cards.add_task.isHidden() for cards in admin.card_lists))
        with patch.object(QInputDialog, 'getText', return_value=('Внедрение', True)):
            admin.edit_basis_project()
            wait_for(lambda: not admin.jobs and not admin.mutating and not admin.loading)
        self.assertEqual(self.store.redmine_basis_project(board), 'Внедрение')

        def password_dialog(dialog):
            login = next(field for field in dialog.findChildren(QLineEdit) if field.echoMode() == QLineEdit.Normal)
            login.setText('tester-redmine')
            password = next(field for field in dialog.findChildren(QLineEdit) if field.echoMode() == QLineEdit.Password)
            password.setText('пример-пароля')
            return QDialog.Accepted

        with patch.object(QInputDialog, 'getText', return_value=('23018', True)), \
             patch.object(QDialog, 'exec_', new=password_dialog), \
             patch('kanban.redmine.connect', side_effect=[RedmineAuthError('Неверный пароль'), object()]) as auth, \
             patch('kanban.redmine.read_basis_statuses', return_value={'ТИ(200)-0123-04': 'ТИ утверждено'}) as basis, \
             patch('kanban.redmine.read_kris_progress', return_value=({23018: {
                 'done': 2, 'total': 3, 'coding': [{'subject': 'Сервис', 'due_date': '2027-02-03'}],
             }}, [])) as kris, \
             patch('kanban.redmine.read_issue', return_value={'title': 'Заголовок из Redmine', 'status': 'Проект ТИ', 'tracker_name': 'ТИ-1157',
                                                           'approvals': [{'department': 'ОСП', 'status': 'Нет'}],
                                                           'issue_references': {'basis': ['СП-23578', 'ТИ(200)-0123-04'],
                                                                                'bugs': ['48316', '70245'],
                                                                                'check_sheet': 'Нет'}}):
            admin.new_task_button.click()
            wait_for(lambda: not admin.mutating)
            self.assertIsNone(admin.redmine_client)
            self.assertEqual(self.store.redmine_tasks(board), [])
            admin.new_task_button.click()
            wait_for(lambda: not admin.mutating and not admin.loading and admin.card_lists[0].count() == 1)
            self.assertEqual(auth.call_count, 2)
            self.assertEqual(admin.redmine_login, 'tester-redmine')
            kris.assert_called_once_with(admin.redmine_client, [23018], 49)
            basis.assert_called_once_with(admin.redmine_client, 88, {'ТИ(200)-0123-04'})
        member.refresh()
        wait_for(lambda: not member.loading and member.card_lists[0].count() == 1)
        self.assertIsNone(member.redmine_client)
        self.assertIn('Основание: СП-23578', member.card_lists[0].item(0).toolTip())
        self.assertIn('ТИ(200)-0123-04: ТИ утверждено', member.card_lists[0].item(0).toolTip())
        self.assertIn('Баги: 48316, 70245', member.card_lists[0].item(0).text())
        self.assertIn('Лист проверки оформлен? Нет', member.card_lists[0].item(0).toolTip())
        self.assertIn('КРИС ТИ: 67% (2/3)', member.card_lists[0].item(0).toolTip())
        self.assertIn('Кодирование до 03.02', member.card_lists[0].item(0).toolTip())
        self.assertTrue(member.card_lists[0].item(0).text().startswith('ТИ-1157 #23018'))
        self.assertIn('ОСП: Нет', member.card_lists[0].item(0).text())
        self.assertFalse(member.card_lists[0].redmine_can_collapse(member.card_lists[0].item(0).data(Qt.UserRole)))

        projects.write_text('{"Разработка": 73, "Внедрение": 88}', encoding='utf-8')
        subtasks = [{'title': 'Интерфейс', 'assignee': 'Петров Иван', 'status': 'Кодирование'},
                    {'title': 'Развёрнутое название подзадачи для проверки переноса строк в узком окне',
                     'assignee': '', 'status': 'Выполнено'}]
        subtasks.extend({'title': f'Приложение {i}', 'assignee': 'Петров Иван', 'status': 'Выполнено'}
                        for i in range(1, 9))
        with patch('kanban.redmine.connect') as auth, patch(
                'kanban.redmine.read_kris_progress', return_value=({23018: {
                    'done': 3, 'total': 3, 'subject': 'ТИ-1157р4 из КРИС',
                    'coding': [{'subject': 'Интерфейс', 'due_date': '2027-02-04'}],
                    'subtasks': subtasks,
                }}, [])) as kris, patch(
                'kanban.redmine.read_issue', return_value={'title': 'Открытая ТИ', 'status': 'ТИ открыто', 'tracker_name': 'Редакция к ТИ-1157р4',
                                                        'approvals': [{'department': 'ОСЗ', 'status': 'Да'}],
                                                        'issue_references': {'bugs': ['61428'], 'check_sheet': 'Да'}}):
            admin.refresh_workspace()
            wait_for(lambda: not admin.mutating and not admin.loading and admin.card_lists[1].count() == 1)
            auth.assert_not_called()
            self.assertEqual(kris.call_args.args[2], 73)
        member.refresh()
        wait_for(lambda: not member.loading and member.card_lists[1].count() == 1)
        self.assertEqual(member.card_lists[0].count(), 0)
        item = member.card_lists[1].item(0)
        self.assertIn('Открытая ТИ', item.text())
        self.assertIn('Основание —', item.text())
        self.assertIn('Баги: 61428', item.toolTip())
        self.assertIn('Лист проверки оформлен? Да', item.data(Qt.AccessibleTextRole))
        self.assertIn('КРИС ТИ: 100% (3/3)', item.toolTip())
        self.assertNotIn('Кодирование до 03.02', item.toolTip())
        self.assertNotIn('48316', item.text())
        self.assertIn('ОСЗ: Да', item.text())
        self.assertNotIn('ОСП', item.text())
        self.assertTrue(item.text().startswith('ТИ-1157р4 из КРИС #23018'))
        self.assertEqual(item.data(Qt.UserRole)['status'], 'ТИ открыто')
        self.assertFalse(member.card_lists[1].grab().isNull())
        self.assertNotIn('пример-пароля'.encode(), self.store.db.read_bytes())

        dialog_tables = []
        dialog_sizes = []
        scroll_geometry = []
        table_geometry = []

        def read_dialog(dialog):
            dialog_sizes.append(dialog.size())
            table = dialog.findChild(QTableWidget)
            dialog_tables.append(table and (
                [table.horizontalHeaderItem(i).text() for i in range(table.columnCount())],
                [[table.item(r, c).text() for c in range(table.columnCount())] for r in range(table.rowCount())],
                table.editTriggers(),
            ))
            if table:
                dialog.resize(1050, 500)
                dialog.show()
                APP.processEvents()
                scroll = dialog.findChild(QScrollArea)
                left = table.mapTo(scroll.viewport(), QPoint()).x()
                right = scroll.viewport().width() - (left + table.width())
                table_geometry.append((table.width(), left, right))
                dialog.resize(430, 330)
                APP.processEvents()
                table_geometry.append((table.width(), scroll.horizontalScrollBar().maximum()))
                last = table.visualItemRect(table.item(table.rowCount() - 1, 0))
                table.setCurrentCell(0, 0)
                QTest.keyClick(table, Qt.Key_End, Qt.ControlModifier)
                scroll_geometry.append((table.verticalScrollBarPolicy(), table.verticalScrollBar().maximum(),
                                        scroll.verticalScrollBar().value(), last.bottom(), table.viewport().height(),
                                        table.currentRow()))
                dialog.hide()
            return QDialog.Accepted

        with patch.object(QDialog, 'exec_', new=read_dialog):
            member.open_task(item.data(Qt.UserRole))
            wait_for(lambda: bool(dialog_tables))
        expected_rows = [[row['title'], row['assignee'] or '—', row['status']] for row in subtasks]
        self.assertEqual(dialog_tables, [(['Название', 'Назначена', 'Статус'], expected_rows,
                                          QTableWidget.NoEditTriggers)])
        policy, table_scroll, dialog_scroll, last_row_bottom, viewport_height, current_row = scroll_geometry[0]
        self.assertEqual(policy, Qt.ScrollBarAlwaysOff)
        self.assertEqual(table_scroll, 0)
        self.assertGreater(dialog_scroll, 0)
        self.assertLess(last_row_bottom, viewport_height)
        self.assertEqual(current_row, len(subtasks) - 1)
        width, left, right = table_geometry[0]
        self.assertEqual(width, 680)
        self.assertLessEqual(abs(left - right), 5)
        self.assertLess(table_geometry[1][0], width)
        self.assertEqual(table_geometry[1][1], 0)
        size = dialog_sizes[0]
        available = member.screen().availableGeometry()
        self.assertEqual(size.width(), min(1200, available.width() - 24))
        self.assertGreater(size.width(), size.height())
        self.assertLess(size.height(), available.height())

        cards = member.card_lists[1]
        cards.setCurrentItem(item)
        full_height = cards.visualItemRect(item).height()
        from kanban.theme import card_refresh_rect
        with patch.object(member, 'show_redmine_task') as open_task:
            button = CardDelegate.ti_disclosure_rect(cards.visualItemRect(item)).center()
            QTest.mouseClick(cards.viewport(), Qt.LeftButton, pos=button)
            open_task.assert_not_called()
            self.assertLess(cards.visualItemRect(item).height(), full_height)
            self.assertIn('Кодирование до 04.02', item.toolTip())
            self.assertIn('Лист проверки оформлен? Да', item.toolTip())
            self.assertNotIn('Баги', item.toolTip())
            with patch.object(member, 'sync_project_ti') as refresh_card:
                QTest.mouseClick(cards.viewport(), Qt.LeftButton,
                                 pos=card_refresh_rect(cards.visualItemRect(item), 28).center())
                QTest.keyClick(cards, Qt.Key_R, Qt.ControlModifier)
                self.assertEqual([call.args for call in refresh_card.call_args_list], [(23018,), (23018,)])
            open_task.assert_not_called()
            member.refresh()
            wait_for(lambda: not member.loading)
            self.assertIs(cards.item(0), item)
            self.assertLess(cards.visualItemRect(item).height(), full_height)
            QTest.keyClick(cards, Qt.Key_Return)
            open_task.assert_called_once()
            QTest.keyClick(cards, Qt.Key_Space)
            self.assertEqual(cards.visualItemRect(item).height(), full_height)
            self.assertIn('Баги', item.toolTip())

        cards = admin.card_lists[1]
        point = cards.visualItemRect(cards.item(0)).center()

        def choose_remove(menu, *_):
            action = next(action for action in menu.actions() if action.text() == 'Убрать с доски')
            action.trigger()

        with patch.object(QMenu, 'exec_', new=choose_remove), \
             patch.object(QMessageBox, 'question', side_effect=[QMessageBox.No, QMessageBox.Yes]) as confirm:
            admin.card_menu(cards, point)
            self.assertEqual(cards.count(), 1)
            admin.card_menu(cards, point)
            wait_for(lambda: not admin.mutating and cards.count() == 0)
            self.assertIn('#23018', confirm.call_args.args[2])
        self.assertEqual(self.store.redmine_tasks(board), [])

    def test_current_work_refreshes_linked_ti_and_imports_sppi_with_separate_buttons(self):
        from threading import Event
        from PyQt5.QtWidgets import QInputDialog
        from kanban.installation import save_folder
        base = Path(self.tmp.name)
        save_folder(base, self.store.folder)
        (base / 'all_project.json').write_text('{"Проект": 49, "СППИ": 88, "Другой СППИ": 99}', encoding='utf-8')
        (base / 'employees.json').write_text('["Иванова Анна"]', encoding='utf-8')
        self.store.set_redmine_settings('https://redmine.example.test', {})
        board = self.store.board_action('Проект / 2.0-218', column_names=['Проект ТИ'])
        other = self.store.board_action('Проект / 2.0-219', column_names=['Проект ТИ'])
        self.store.set_redmine_sppi_project(other, 'Другой СППИ')
        self.store.link_redmine_issue(board, 118, 'Старые данные', 'Проект ТИ', 'Проект ТИ')
        with patch('kanban.ui.Store', side_effect=lambda folder: Store(folder, 'tester')):
            window = MainWindow(None, installation_base=base)
            self.windows.append(window)
            window.show()
            wait_for(lambda: not window.loading and board in window.board_items)
        window.select_tree_board(window.board_items[board], 0)
        wait_for(lambda: not window.loading and window.project_sections_board == board and window.card_lists[0].count() == 1)
        with patch.object(QInputDialog, 'getText', return_value=('СППИ', True)):
            window.edit_sppi_project()
            wait_for(lambda: not window.jobs and not window.mutating and not window.loading)
        self.assertEqual(self.store.redmine_sppi_project(board), 'СППИ')
        self.assertEqual(self.store.redmine_sppi_project(other), 'Другой СППИ')
        window.redmine_client = object()
        subtasks = [{'id': 120, 'title': 'Подзадача', 'status': 'В работе'}]
        issue = {'title': 'ТИ из СППИ', 'status': 'Проект ТИ', 'tracker_name': 'ТИ-119',
                 'approvals': [{'department': 'ОСП', 'status': 'Да'}],
                 'issue_references': {'basis': ['СП-12'], 'bugs': ['42'], 'children': subtasks}}
        child = {'title': 'Подзадача', 'status': 'В работе', 'tracker_name': 'Подзадача',
                 'approvals': [{'department': 'ОСЗ', 'status': 'Да'}],
                 'issue_references': {'basis': ['СП-120'], 'bugs': ['120'], 'parent_id': 119}}
        release_scan = Event()
        scan_lines = ['#{} · Ответственный: Анна Иванова · Версия: 2.0-218 И-01'.format(issue_id)
                      for issue_id in range(115, 120)]

        def scan(client, project_id, board_name, employees, *, on_issue):
            for line in scan_lines:
                on_issue(line)
            yield 119, {'subject': 'ТИ из СППИ'}, None
            release_scan.wait(8)
            yield 120, {'subject': 'Подзадача'}, 'Проект ТИ'

        with patch('kanban.redmine.iter_sppi_issues', side_effect=scan) as discover, \
             patch('kanban.redmine.read_issue', side_effect=lambda client, issue_id, **opts:
                   child if issue_id == 120 else issue) as fetch, \
             patch('kanban.redmine.read_kris_progress', return_value=({
                 119: None, 120: {'done': 1, 'total': 2, 'subject': 'КРИС подзадачи'},
             }, [])):
            self.assertTrue(window.refresh_ti_button.isVisible())
            self.assertTrue(window.import_sppi_button.isVisible())
            window.refresh_ti_button.click()
            wait_for(lambda: not window.mutating and not window.loading)
            discover.assert_not_called()
            self.assertEqual(fetch.call_args.args, (window.redmine_client, 118))
            self.assertEqual(fetch.call_count, 1)
            self.assertEqual(window.card_lists[0].count(), 1)
            self.assertIn('ТИ из СППИ', window.card_lists[0].item(0).toolTip())
            fetch.reset_mock()

            window.import_sppi_button.click()
            try:
                self.assertEqual(window.status.text(), 'Выгрузка задач из Redmine…')
                wait_for(lambda: getattr(window.sppi_log_lines[-1], 'full_text', '') == scan_lines[-1])
                wait_for(lambda: window.card_lists[0].count() == 2)
                self.assertTrue(window.mutating)
                self.assertTrue(window.sppi_log.isVisible())
                self.assertEqual([line.full_text for line in window.sppi_log_lines], scan_lines[-4:])
                self.assertIn('Ответственный', window.sppi_log_lines[-1].text())
            finally:
                release_scan.set()
            wait_for(lambda: not window.mutating and not window.loading and window.card_lists[0].count() == 3)
            self.assertEqual(discover.call_args.args, (window.redmine_client, 88, '2.0-218', ['Иванова Анна']))
            self.assertEqual([(args[1], kwargs['skip_closed']) for args, kwargs in fetch.call_args_list],
                             [(119, True), (120, True), (118, True)])
        self.assertFalse(window.sppi_log.isVisible())
        self.assertTrue(all(not line.full_text for line in window.sppi_log_lines))
        item = window.card_lists[0].item(1)
        self.assertIn('Основание: СП-12', item.toolTip())
        self.assertIn('ОСП: Да', item.toolTip())
        self.assertEqual(self.store.redmine_tasks(board)[1]['issue_references']['children'], subtasks)
        child_card = window.card_lists[0].item(2).toolTip()
        for entry in ('КРИС подзадачи #120', 'КРИС ТИ: 50% (1/2)', 'Основание: СП-120', 'Баги: 120', 'ОСЗ: Да'):
            self.assertIn(entry, child_card)

    def test_project_column_details_and_ti_archive(self):
        from kanban.ui import ColumnsDialog
        board = self.store.board_action('Проект / ТИ', column_names=['Проект ТИ', 'ТИ открыто'])
        self.store.link_redmine_issue(board, 101, 'Черновик', 'Проект ТИ', 'Проект ТИ')
        self.store.link_redmine_issue(board, 102, 'Открытое ТИ', 'ТИ открыто', 'ТИ открыто',
                                      issue_references={'bugs': ['42']},
                                      approvals=[{'department': 'ОСП', 'status': 'Да'}])
        with patch('kanban.ui.Store', return_value=self.store):
            window = MainWindow(str(self.store.folder))
            self.windows.append(window)
            window.show()
            wait_for(lambda: not window.loading and board in window.board_items)
        window.select_tree_board(window.board_items[board], 0)
        wait_for(lambda: not window.loading and window.project_sections_board == board)

        def configure(dialog):
            self.assertIsInstance(dialog, ColumnsDialog)
            self.assertEqual(dialog.table.item(1, 3).checkState(), Qt.Checked)
            dialog.table.item(1, 3).setCheckState(Qt.Unchecked)
            return QDialog.Accepted

        with patch.object(QDialog, 'exec_', new=configure):
            window.edit_columns()
        wait_for(lambda: not window.loading and not window.mutating
                 and window.columns_data[1].get('show_ti_details') is False)
        item = window.card_lists[1].item(0)
        self.assertNotIn('Баги', item.toolTip())
        self.assertNotIn('ОСП', item.toolTip())
        task = item.data(Qt.UserRole)
        self.assertTrue(window.can_archive_ti(task))
        self.assertFalse(window.can_archive_ti(window.card_lists[0].item(0).data(Qt.UserRole)))
        window.archive_ti(task)
        wait_for(lambda: not window.loading and not window.mutating and window.card_lists[1].count() == 0)
        self.assertEqual([task['issue_id'] for task in self.store.redmine_tasks(board)], [101])
        window.archive_filter.setChecked(True)
        wait_for(lambda: not window.loading and window.card_lists[1].count() == 1)
        archived = window.card_lists[1].item(0).data(Qt.UserRole)
        self.assertTrue(archived['archived'])
        window.archive_ti(archived)
        wait_for(lambda: not window.loading and not window.mutating and window.card_lists[1].count() == 0)
        window.archive_filter.setChecked(False)
        wait_for(lambda: not window.loading and window.card_lists[1].count() == 1)

    def test_ti_card_refresh_requests_only_the_selected_issue(self):
        from kanban.installation import save_folder
        from kanban.theme import card_refresh_rect

        base = Path(self.tmp.name)
        save_folder(base, self.store.folder)
        (base / 'all_project.json').write_text('{"Разработка": 49}', encoding='utf-8')
        self.store.set_redmine_settings('https://redmine.example.test', {})
        board = self.store.board_action('Разработка / 2.0-218', column_names=['Проект ТИ'])
        for number in (701, 702):
            self.store.link_redmine_issue(board, number, f'Задача #{number}', 'Проект ТИ', 'Проект ТИ')
        with patch('kanban.ui.Store', side_effect=lambda folder: Store(folder, 'tester')):
            window = MainWindow(None, installation_base=base)
            self.windows.append(window)
            window.show()
            wait_for(lambda: not window.loading and board in window.board_items)
        window.select_tree_board(window.board_items[board], 0)
        wait_for(lambda: not window.loading and window.project_sections_board == board and window.card_lists[0].count() == 2)
        window.redmine_client = object()
        cards = window.card_lists[0]
        target = next(cards.item(i) for i in range(cards.count()) if cards.item(i).data(Qt.UserRole)['redmine_id'] == 701)
        sibling = next(cards.item(i) for i in range(cards.count()) if cards.item(i).data(Qt.UserRole)['redmine_id'] == 702)
        issue = {'tracker_name': '', 'title': 'Обновлённая ТИ', 'status': 'Проект ТИ', 'approvals': [], 'issue_references': {}}
        with patch('kanban.redmine.read_issue', return_value=issue) as fetch, \
             patch('kanban.redmine.read_kris_progress', return_value=({}, [])), patch.object(window, 'open_task') as opened:
            QTest.mouseClick(cards.viewport(), Qt.LeftButton, pos=card_refresh_rect(cards.visualItemRect(target)).center())
            wait_for(lambda: target.data(Qt.UserRole)['title'] == issue['title'])
            self.assertEqual(fetch.call_count, 1)
            self.assertEqual(fetch.call_args.args, (window.redmine_client, 701))
            opened.assert_not_called()
        self.assertIs(window.card_lists[0], cards)
        self.assertGreaterEqual(cards.row(sibling), 0)
        self.assertEqual(sibling.data(Qt.UserRole)['title'], 'Задача #702')

    def test_identification_tab_loads_basis_project_and_shows_linked_ti(self):
        from kanban.installation import save_folder
        from kanban.theme import card_refresh_rect
        base = Path(self.tmp.name)
        save_folder(base, self.store.folder)
        (base / 'all_project.json').write_text('{"Основания": 88}', encoding='utf-8')
        self.store.set_redmine_settings('https://redmine.example.test', {})
        board = self.store.board_action('Разработка / 2.0-218')
        self.grant_all(board)
        self.store.set_redmine_basis_project(board, 'Основания')
        number = 'ТИ(200)-1234-01'
        column = self.store.columns(board)[0]['name']
        title = 'Согласовать изменения в комплекте документации'
        self.store.link_redmine_issue(board, 701, title, column, column,
                                      tracker_name='Редакция к ТИ-701р4',
                                      issue_references={'basis': [number, 'ТИ(200)-1234-04']})
        with patch('kanban.ui.Store', side_effect=lambda folder: Store(folder, 'tester')):
            window = MainWindow(None, installation_base=base)
            self.windows.append(window)
            window.show()
            wait_for(lambda: not window.loading and board in window.board_items)
        window.select_tree_board(window.board_items[board], 0)
        wait_for(lambda: not window.loading and window.project_sections_board == board)
        window.project_sections.setCurrentIndex(1)
        page = window.identification_page
        wait_for(lambda: page.refresh_button.isEnabled())
        window.redmine_client = object()
        with patch('kanban.redmine.read_identification_cards', return_value=[
                {'issue_id': 710, 'number': number, 'subject': 'Согласовать спецификацию', 'approval': 'Да', 'status': 'ТИ утверждено',
                 'versions': ['2.0-218 И-01']},
                {'number': 'ТИ(200)-1234-02', 'approval': 'Нет', 'status': 'В работе', 'versions': ['2.0-218 И-02']},
                {'number': 'ТИ(200)-1234-03', 'approval': 'Нет', 'status': 'В работе', 'versions': ['2.0-219 И-01']},
                {'number': 'ТИ(200)-1234-04', 'approval': 'Нет', 'status': 'В работе', 'versions': ['2.0-218 И-01']},
                {'number': 'ТИ(200)-1234-05', 'approval': 'Да', 'status': 'ТИ утверждено', 'versions': ['2.0-218 И-01']},
        ]) as fetch:
            page.refresh_button.click()
            wait_for(lambda: not window.mutating and sum(column.count() for column in page.columns) == 4)
            fetch.assert_called_once_with(window.redmine_client, 88)
        self.assertEqual([column.count() for column in page.columns], [1, 1, 1, 1])
        items = [column.item(0) for column in page.columns]
        self.assertEqual([item.data(Qt.UserRole)['number'] for item in items],
                         ['ТИ(200)-1234-02', 'ТИ(200)-1234-04', 'ТИ(200)-1234-05', number])
        self.assertEqual(items[3].data(Qt.UserRole)['links'], ['ТИ-701р4'])
        self.assertIn('ТИ-701р4', items[3].toolTip())
        self.assertNotIn(title, items[3].toolTip())
        self.assertIn(number + '\nСогласовать спецификацию', items[3].toolTip())
        self.assertIn('Не привязано', items[0].toolTip())
        self.assertFalse(page.grab().isNull())
        target_column = page.columns[3]
        page.scroll.ensureWidgetVisible(target_column)
        APP.processEvents()
        previous = items[3].data(Qt.UserRole)
        updated = dict(previous, subject='Проверить спецификацию')
        updated.pop('links')
        with patch('kanban.redmine.read_identification_card', return_value=updated) as fetch, \
             patch('kanban.redmine.read_identification_cards') as bulk:
            QTest.mouseClick(target_column.viewport(), Qt.LeftButton,
                             pos=card_refresh_rect(target_column.visualItemRect(items[3])).center())
            wait_for(lambda: target_column.item(0).data(Qt.UserRole)['subject'] == updated['subject'])
            fetch.assert_called_once_with(window.redmine_client, 88, previous)
            bulk.assert_not_called()
        self.assertIs(page.columns[0].item(0), items[0])
        self.assertIs(target_column.item(0), items[3])

    def test_redesigned_workspace_groups_and_message_badges(self):
        names = ['Бэклог', 'В работе', 'На проверке', 'Готово']
        board = self.store.board_action('Разработка / Релиз платформы', column_names=names)
        self.grant_all(board)
        cols = self.store.columns(board)
        labels = ['Frontend', 'Спринт 4']
        titles = ['Обновить форму регистрации пользователей', 'Добавить поиск по проектам',
                  'Проверить адаптивную вёрстку отчётов', 'Подготовить документацию API']
        tasks = []
        for i, title in enumerate(titles):
            tasks.append(self.store.save_task(dict(board_id=board, column_id=cols[i]['id'], title=title,
                priority='Срочный' if i == 0 else 'Обычный', labels=labels,
                assignees=[self.store.login], due_date='2026-12-20',
                checklist=[dict(text='Проверка', done=1), dict(text='Согласование', done=0)])))
        colleague = Store(self.store.folder, 'designer')
        for i in range(4):
            colleague.comment(tasks[0]['id'], 'Обсуждение макета {}'.format(i+1))
        w = MainWindow(str(self.store.folder))
        self.windows.append(w)
        w.show()
        wait_for(lambda: not w.loading and w.board_tabs.count() == 2)
        w.board_tabs.setCurrentIndex(1)
        wait_for(lambda: not w.loading and len(w.card_lists) == 4)
        roots = [w.board_tree.topLevelItem(i) for i in range(w.board_tree.topLevelItemCount())]
        self.assertEqual([root.text(0) for root in roots], ['Рабочие доски', 'Проекты', 'Личное'])
        projects = roots[1]
        group = projects.child(0)
        self.assertEqual(group.text(0), 'Разработка')
        self.assertEqual(group.child(0).text(0), 'Релиз платформы')
        self.assertIs(w.board_tree.currentItem(), group.child(0))
        w.update_board_badges({board: {'chat': 2, 'tasks': 1, 'total': 3}})
        self.assertEqual([node.text(1) for node in (projects, group, group.child(0))], ['3', '3', '3'])
        self.assertTrue(w.card_lists[0].item(0).data(Qt.UserRole)['unread_count'] >= 4)
        evidence = Path(__file__).resolve().parent.parent / 'docs/screenshots'
        w.grab().save(str(evidence / 'teamsync-new-workspace.png'))

    def test_desktop_sidebar_views_and_split_chat(self):
        w = MainWindow(str(self.store.folder))
        self.windows.append(w)
        w.show()
        wait_for(lambda: not w.loading and bool(w.card_lists))
        self.assertEqual(w.sidebar.width(), 240)
        self.assertFalse(w.board_tabs.isVisible())
        self.assertFalse(w.pages.tabBar().isVisible())
        w.view_switch.setCurrentIndex(w.pages.indexOf(w.list_view))
        self.assertTrue(w.list_view.isVisible())
        wait_for(lambda: not w.loading and w.list_view.table.rowCount() == 1)
        w.pages.setCurrentWidget(w.gantt_view)
        wait_for(lambda: not w.loading and w.gantt_view.table.rowCount() == 1)
        w.view_switch.setCurrentIndex(0)
        w.collapse_button.click()
        self.assertEqual(w.sidebar.width(), 64)
        w.collapse_button.click()
        self.assertEqual(w.sidebar.width(), 240)
        self.assertEqual(w.board_tree.topLevelItem(0).childCount(), 1)
        self.assertEqual(w.list_view.table.rowCount(), 1)
        self.assertEqual(w.gantt_view.table.rowCount(), 1)
        colleague = Store(self.store.folder, 'colleague')
        colleague.comment(self.result['id'], 'Непрочитанное сообщение')
        d = self.dialog()
        self.assertTrue(d.chat_page.isVisible())
        self.assertTrue(d.tabs.isVisible())
        wait_for(lambda: self.store.detail(self.result['id'])['unread_count'] == 0)
        self.assertIn('Создание', d.comments.toPlainText())
        self.assertIn('Непрочитанное сообщение', d.comments.toPlainText())
        d.title.setText('Черновик')
        d.comment_text.setPlainText('Неотправленное')
        d.refresh_extras()
        wait_for(lambda: not d.jobs)
        self.assertEqual(d.title.text(), 'Черновик')
        self.assertEqual(d.comment_text.toPlainText(), 'Неотправленное')
        evidence = Path(__file__).resolve().parent.parent / 'docs/screenshots'
        d.tabs.setCurrentIndex(0)
        d.grab().save(str(evidence / 'desktop-split-chat-linux.png'))
        d.resize(900, 750)
        APP.processEvents()
        self.assertEqual(d.task_splitter.orientation(), Qt.Vertical)
        d.grab().save(str(evidence / 'desktop-chat-narrow-linux.png'))
        w.pages.setCurrentWidget(w.gantt_view)
        w.grab().save(str(evidence / 'desktop-gantt-linux.png'))
        boundary = self.store.tasks(self.bid)
        boundary[0]['due_date'] = '9999-12-31'
        w.gantt_view.update_data(boundary, self.columns)
        self.assertEqual(w.gantt_view.table.rowCount(), 1)

    def test_top_board_tabs_and_personal_tasks_across_boards(self):
        import getpass
        login = getpass.getuser()
        second = self.store.board_action('Вторая доска')
        self.grant_all(second)
        second_columns = self.store.columns(second)
        own = self.store.save_task(dict(board_id=second, column_id=second_columns[0]['id'], title='Личная задача', assignees=[login]))
        self.store.save_task(dict(board_id=second, column_id=second_columns[0]['id'], title='Чужая задача', assignees=['another']))
        w = MainWindow(str(self.store.folder))
        self.windows.append(w)
        w.show()
        wait_for(lambda: not w.loading and w.board_tabs.count() == 2)
        self.assertFalse(w.boards_list.isVisible())
        self.assertEqual(w.my_tasks.tasks, [])
        w.pages.setCurrentWidget(w.my_tasks)
        self.assertTrue(w.my_tasks.isVisible())
        wait_for(lambda: not w.loading and len(w.my_tasks.tasks) == 1)
        self.assertEqual(w.my_tasks.tasks[0]['id'], own['id'])
        w.my_tasks.search.setText('Чужая')
        self.assertEqual(w.my_tasks.table.rowCount(), 0)
        w.my_tasks.search.clear()
        w.my_tasks.view_mode.setCurrentText('Доска')
        cards = w.my_tasks.card_lists[second_columns[0]['name']]
        self.assertEqual(cards.count(), 1)
        self.assertIn('Вторая доска', cards.item(0).text())
        self.assertFalse(cards.dragEnabled())
        w.my_tasks.search.setText('Чужая')
        self.assertFalse(w.my_tasks.card_lists)
        w.my_tasks.search.clear()
        cards = w.my_tasks.card_lists[second_columns[0]['name']]
        cards.setCurrentRow(0)
        QTest.keyClick(cards, Qt.Key_Return)
        wait_for(lambda: bool(w.dialogs) and not w.jobs)
        dialog = next(iter(w.dialogs))
        wait_for(lambda: not dialog.jobs)
        self.assertEqual(dialog.data['board_id'], second)
        self.assertEqual(dialog.columns[0]['id'], second_columns[0]['id'])
        dialog.close()
        w.board_tabs.setCurrentIndex(1)
        wait_for(lambda: not w.loading and w.columns_data[0]['board_id'] == second)
        self.assertEqual(w.board_id, second)
        evidence = Path(__file__).resolve().parent.parent / 'docs/screenshots'
        w.pages.setCurrentWidget(w.my_tasks)
        w.grab().save(str(evidence / 'my-tasks-linux.png'))

    def test_refined_task_history_and_chat(self):
        from kanban.presentation import comments_html, history_html
        from PyQt5.QtWidgets import QLabel
        self.store.comment(self.result['id'], '<b>Первое</b>')
        colleague = Store(self.store.folder, 'colleague')
        colleague.comment(self.result['id'], 'Ответ коллеги')
        d = self.dialog()
        self.assertFalse(d.column.isVisible())
        self.assertNotIn('Переместить в…', [label.text() for label in d.findChildren(QLabel)])
        comments = self.store.detail(self.result['id'])['comments']
        markup = comments_html(comments, 'tester')
        self.assertIn('Вы', markup)
        self.assertIn('&lt;b&gt;Первое&lt;/b&gt;', markup)
        self.assertLess(markup.index('Первое'), markup.index('Ответ коллеги'))
        entries = [dict(id=2, created_at='2026-01-02T12:00:00Z', author='Второй', operation='Комментарий', changes='{}'),
                   dict(id=1, created_at='2026-01-01T12:00:00Z', author='Первый', operation='Комментарий', changes='{}')]
        history = history_html(entries)
        self.assertLess(history.index('Первый'), history.index('Второй'))
        d.tabs.setCurrentIndex(0)
        evidence = Path(__file__).resolve().parent.parent / 'docs/screenshots'
        d.grab().save(str(evidence / 'chat-linux.png'))

    def test_statistics_charts_segments_empty_and_themes(self):
        from kanban.statistics import StatisticsPage
        from kanban.theme import apply_theme
        page = StatisticsPage()
        self.windows.append(page)
        page.resize(980, 900)
        tasks = []
        for index in range(12):
            tasks.append(dict(column_id=self.columns[index % 5]['id'],
                completed_at='2026-01-01' if index < 4 else None,
                due_date='2000-01-01' if index < 7 else None,
                assignees=['ivanov'] if index % 2 else ['petrov'],
                assignee_names=['Иванов Иван' if index % 2 else 'Петров Пётр']))
        page.update_data(tasks, self.columns)
        segments = page.completion_chart.rows
        self.assertEqual([row[1][0][0] for row in segments], [4, 5, 3])
        self.assertEqual(page.completion_chart.center, '33%')
        self.assertEqual(sum(row[1][0][0] for row in page.column_chart.rows), 12)
        for row in page.member_chart.rows:
            self.assertTrue(all(v >= 0 for v, color in row[1]))
            self.assertEqual(sum(v for v, color in row[1]), 6)
        evidence = Path(__file__).resolve().parent.parent / 'docs/screenshots'
        evidence.mkdir(parents=True, exist_ok=True)
        page.show()
        try:
            for dark in (False, True):
                apply_theme(APP, dark)
                APP.processEvents()
                page.grab().save(str(evidence / ('charts-dark-linux.png' if dark else 'charts-light-linux.png')))
                self.assertTrue(page.member_chart.grab().save(str(evidence / ('load-dark-linux.png' if dark else 'load-light-linux.png'))))
            page.update_data([], self.columns)
            self.assertEqual(page.completion_chart.center, '0%')
            self.assertFalse(page.member_chart.rows)
            page.resize(650, 650)
            APP.processEvents()
            page.grab().save(str(evidence / 'charts-empty-linux.png'))
        finally:
            apply_theme(APP, False)

    def test_refresh_during_drag_and_inflight_response(self):
        from kanban.ui import QDrag
        w = MainWindow(str(self.store.folder))
        self.windows.append(w)
        w.show()
        wait_for(lambda: not w.loading and len(w.card_lists) == 5)
        w.timer.stop()
        source = w.card_lists[0]
        source.setCurrentRow(0)
        old_board = w.scroll.widget()
        pending = []
        with patch.object(w, 'work', side_effect=lambda read, done: pending.append((read, done))):
            w.refresh()
        read, done = pending[0]
        stale = read()
        def drag_loop(*args):
            self.assertTrue(w.drag_active)
            w.refresh()
            done(stale, None)
            APP.processEvents()
            self.assertIs(w.scroll.widget(), old_board)
            self.assertFalse(sip.isdeleted(source))
            w.move_task(self.result['id'], self.columns[1]['id'])
            wait_for(lambda: not w.mutating)
            self.assertIs(w.scroll.widget(), old_board)
            self.assertEqual(source.count(), 1)
            return Qt.MoveAction
        with patch.object(QDrag, 'exec_', side_effect=drag_loop):
            source.startDrag(Qt.MoveAction)
        wait_for(lambda: not w.loading and not w.debounce.isActive() and w.card_lists[1].count() == 1)
        self.assertFalse(w.drag_active)
        self.assertEqual(self.store.detail(self.result['id'])['column_id'], self.columns[1]['id'])

    def test_unchanged_refresh_preserves_widgets_and_cancelled_drag(self):
        from kanban.ui import QDrag
        w = MainWindow(str(self.store.folder))
        self.windows.append(w)
        w.show()
        wait_for(lambda: not w.loading and len(w.card_lists) == 5)
        source = w.card_lists[0]
        source.setCurrentRow(0)
        board = w.scroll.widget()
        w.refresh()
        wait_for(lambda: not w.loading)
        self.assertIs(w.scroll.widget(), board)
        with patch.object(QDrag, 'exec_', side_effect=lambda *args: (w.refresh(), Qt.IgnoreAction)[1]):
            source.startDrag(Qt.MoveAction)
        wait_for(lambda: not w.loading and not w.debounce.isActive())
        self.assertIs(w.scroll.widget(), board)
        self.assertEqual(source.count(), 1)
        self.assertFalse(w.drag_active)

    def test_notification_poll_on_every_page(self):
        w = MainWindow(str(self.store.folder))
        self.windows.append(w)
        w.show()
        wait_for(lambda: not w.loading and bool(w.card_lists))
        colleague = Store(self.store.folder, 'colleague')
        for page in range(w.pages.count()):
            w.pages.setCurrentIndex(page)
            wait_for(lambda: not w.loading and not w.notifications.loading)
            colleague.team_send('Сообщение {}'.format(page), board_id=self.bid)
            w.notifications.poll()
            wait_for(lambda: not w.notifications.loading)
            self.assertEqual(w.notification_button.text(), str(page + 1))

    def test_redmine_basis_chips_are_single_line_at_the_shared_chip_height(self):
        from unittest.mock import Mock
        from PyQt5.QtGui import QImage, QPainter
        from PyQt5.QtWidgets import QListWidgetItem
        from kanban.cards import Cards, set_card_data

        cards = Cards(1)
        cards.resize(220, cards.height())
        values = ['СП-23578', 'СП ' + '1234567890 ' * 6 + '98765']
        item = QListWidgetItem(cards)
        set_card_data(item, {'redmine_id': 42, 'title': 'Задача', 'tracker_name': 'ТИ',
                             'status': 'Проект ТИ', 'approvals': [{'department': 'ОСП', 'status': 'Да'}],
                             'issue_references': {'basis': values, 'bugs': ['48316'],
                                                  'check_sheet': 'Да',
                                                  'ti_responsible': [{'name': 'Иван Петров', 'initials': 'ИП'}]},
                             'kris_progress': {
                                 'subject': 'ТИ-1157 КРИС', 'done': 1, 'total': 4,
                                 'subtasks': [{'status': status} for status in
                                              ('Выполнено', 'Кодирование', 'Кодирование', 'Новое')],
                                 'coding': [{'subject': 'Сервис', 'due_date': '2027-02-03'}],
                             }}, 0)
        cards.show()
        self.addCleanup(cards.close)
        APP.processEvents()
        option = cards.viewOptions()
        option.rect = cards.visualItemRect(item)
        image = QImage(cards.width(), cards.height(), QImage.Format_ARGB32)
        painter = QPainter(image)
        try:
            traced = Mock(wraps=painter, Antialiasing=QPainter.Antialiasing)
            cards.itemDelegate().paint(traced, option, cards.indexFromItem(item))
        finally:
            painter.end()
        labels = {call.args[2]: call.args[:2] for call in traced.drawText.call_args_list}
        self.assertIn('Основание', labels)
        self.assertIn('ТИ-1157 КРИС', labels)
        traced.drawEllipse.assert_called_once()
        self.assertLess(traced.drawEllipse.call_args.args[0].right(), labels['ТИ-1157 КРИС'][0].left())
        self.assertIn('ИП', labels)
        self.assertNotIn('Redmine', labels)
        self.assertNotIn('Согласование отделов', labels)
        self.assertIn('В кодировании: 2/4', item.toolTip())
        self.assertEqual(labels['Кодирование до 03.02'][0].height(), labels['ОСП'][0].height())
        from kanban.theme import TOKENS
        completed, coding = [call.args for call in traced.fillRect.call_args_list]
        self.assertAlmostEqual(completed[0].right(), coding[0].left())
        self.assertAlmostEqual(coding[0].width(), 2 * completed[0].width())
        self.assertEqual(coding[1].name().upper(), TOKENS['warning'].upper())
        self.assertIn('Ответственный за выпуск ТИ: Иван Петров', item.toolTip())
        self.assertNotIn('ТИ', labels)
        for value in values:
            rect, flags = labels[value]
            self.assertTrue(flags & Qt.TextSingleLine)
            self.assertEqual(rect.height(), labels['48316'][0].height())
            self.assertEqual(rect.height(), labels['ОСП'][0].height())
        traced.scale.assert_called_once()

    def test_two_line_card_title_leaves_space_for_badges(self):
        from PyQt5.QtCore import QRect
        from PyQt5.QtGui import QPainter, QPixmap
        from kanban.cards import Cards, set_card_data
        from PyQt5.QtWidgets import QListWidgetItem
        cards = Cards(self.columns[0]['id'])
        task = self.store.board_cards(self.bid)[0]
        task['title'] = 'Длинный заголовок карточки с важным контекстом для исполнителя'
        item = QListWidgetItem()
        set_card_data(item, task, 2)
        cards.addItem(item)
        option = cards.viewOptions()
        index = cards.model().index(0, 0)
        delegate = cards.itemDelegate()
        option.rect = QRect(0, 0, 220, delegate.sizeHint(option, index).height())
        pixmap = QPixmap(option.rect.size())
        painter = QPainter(pixmap)
        try:
            with patch.object(painter, 'drawText', wraps=painter.drawText) as draw_text:
                delegate.paint(painter, option, index)
            first, second, notice, due = (call.args for call in draw_text.call_args_list[:4])
            self.assertTrue(task['title'].startswith(first[2]))
            self.assertTrue(second[2])
            self.assertEqual(notice[2], '2')
            self.assertGreaterEqual(due[0].top() - second[0].bottom(), 7)
        finally:
            painter.end()

    def test_card_reconciliation_and_narrow_themes(self):
        from kanban.cards import EXPANDED_ROLE, DETAILS_ROLE
        from kanban.theme import apply_theme, theme_settings
        w = MainWindow(str(self.store.folder))
        self.windows.append(w)
        w.resize(900, 600)
        w.show()
        wait_for(lambda: not w.loading and bool(w.card_lists))
        self.assertTrue(w.narrow_search_row.isVisible())
        self.assertGreater(w.search.width(), 240)
        self.assertLess(w.new_task_button.geometry().right(), w.settings_button.geometry().left())
        canvas, source, target = w.scroll.widget(), *w.card_lists[:2]
        item = source.item(0)
        source.setCurrentItem(item)
        source.setFocus()
        QTest.keyClick(source, Qt.Key_Space)
        wait_for(lambda: bool(item.data(DETAILS_ROLE)))
        current = self.store.detail(self.result['id'])
        title = 'Очень длинный заголовок ' * 20
        current.update(title=title.strip(), labels=['Длинная метка ' * 15])
        self.store.save_task(current)
        w.refresh()
        wait_for(lambda: not w.loading and title.strip() in item.text())
        self.assertIs(w.scroll.widget(), canvas)
        self.assertIs(source.item(0), item)
        self.assertTrue(source.hasFocus())
        self.assertTrue(item.isSelected())
        self.assertTrue(item.data(EXPANDED_ROLE))
        w.update_unread_notifications([{'task_id': self.result['id']}], [], 'UTC')
        initial_theme = theme_settings().value('dark', False, type=bool)
        try:
            for dark in (False, True):
                apply_theme(APP, dark)
                APP.processEvents()
                self.assertFalse(source.grab().isNull())
                self.assertEqual(item.data(TASK_NOTICE_ROLE), 1)
        finally:
            apply_theme(APP, initial_theme)
        w.move_task(self.result['id'], target.column_id)
        wait_for(lambda: not w.loading and not w.mutating and target.count() == 1)
        self.assertIs(target.item(0), item)
        self.assertIs(w.card_lists[0], source)
        self.assertTrue(item.isSelected())
        self.assertTrue(target.hasFocus())
        self.assertTrue(item.data(EXPANDED_ROLE))
        self.assertEqual(self.store.detail(self.result['id'])['description'], current['description'])
        current = self.store.detail(self.result['id'])
        current['archived'] = True
        self.store.save_task(current)
        w.refresh()
        wait_for(lambda: not w.loading and target.count() == 0)
        self.assertIs(w.scroll.widget(), canvas)

    def test_saved_filters_restored_and_missing_label(self):
        from kanban.theme import theme_settings
        first = MainWindow(str(self.store.folder))
        self.windows.append(first)
        wait_for(lambda: not first.loading and bool(first.card_lists))
        self.addCleanup(lambda: theme_settings().remove(first.saved_filter_key()))
        first.search.setText('договор')
        first.label_filter.setCurrentIndex(first.label_filter.findData('Документы'))
        first.assignee_filter.setCurrentIndex(first.assignee_filter.findData('tester'))
        first.due_to.set_value('2020-01-01')
        with patch('kanban.ui.QInputDialog.getText', return_value=('Рабочие', True)):
            first.name_saved_filter()
        first.close()
        wait_for(lambda: not first.jobs)
        second = MainWindow(str(self.store.folder))
        self.windows.append(second)
        second.show()
        wait_for(lambda: not second.loading and bool(second.card_lists))
        with patch.object(second.store, 'login', 'other-user'):
            self.assertEqual(second.saved_filters(), {})
        second.apply_saved_filter(second.saved_filters()['Рабочие'])
        wait_for(lambda: not second.debounce.isActive() and not second.loading)
        self.assertEqual(second.search.text(), 'договор')
        self.assertEqual(second.label_filter.currentData(), 'Документы')
        self.assertEqual(second.assignee_filter.currentData(), 'tester')
        self.assertEqual(second.due_to.value(), '2020-01-01')
        self.assertEqual(sum(c.count() for c in second.card_lists), 1)
        with patch('kanban.ui.QInputDialog.getText', return_value=('Срок', True)):
            second.name_saved_filter('Рабочие')
        values = second.saved_filters()['Срок']
        values['label'] = 'Устаревшая'
        values['assignee'] = 'Неизвестный'
        second.apply_saved_filter(values)
        self.assertEqual(second.label_filter.currentIndex(), 0)
        self.assertEqual(second.assignee_filter.currentIndex(), 0)
        self.assertIn('Устаревшая', second.filter_message.text())
        self.assertIn('Неизвестный', second.filter_message.text())
        with patch('kanban.ui.QMessageBox.question', return_value=QMessageBox.Yes):
            second.delete_saved_filter('Срок')
        self.assertEqual(second.saved_filters(), {})

    def test_discussion_mention_completion_and_subscription(self):
        d = self.dialog()
        d.show()
        d.comment_text.setFocus()
        QTest.keyClicks(d.comment_text, '@coll')
        wait_for(lambda: d.comment_text.mention_popup.popup().isVisible())
        QTest.keyClick(d.comment_text, Qt.Key_Return)
        wait_for(lambda: d.comment_text.toPlainText() == '@colleague ')
        self.assertFalse(self.store.detail(self.result['id'])['comments'])
        QTest.keyClick(d.comment_text, Qt.Key_Return)
        wait_for(lambda: not d.pending and bool(self.store.detail(self.result['id'])['comments']))
        colleague = Store(self.store.folder, 'colleague')
        self.assertIn('Комментарий', [event['operation'] for event in colleague.task_updates(0)['events']])
        self.assertEqual(d.subscribe_button.text(), 'Подписаться')
        d.subscribe_button.click()
        wait_for(lambda: not d.pending and d.subscribe_button.text() == 'Отписаться')
        self.assertTrue(self.store.detail(self.result['id'])['subscribed'])
        d.subscribe_button.click()
        wait_for(lambda: not d.pending and d.subscribe_button.text() == 'Подписаться')
        self.assertFalse(self.store.detail(self.result['id'])['subscribed'])

    def test_statistics_theme_and_labels(self):
        from kanban.statistics import summarize
        from kanban.theme import apply_theme, label_colors, theme_settings, TOKENS
        initial_theme = theme_settings().value('dark', False, type=bool)
        w = MainWindow(str(self.store.folder))
        self.windows.append(w)
        w.show()
        wait_for(lambda: not w.loading and len(w.card_lists) == 5)
        w.search.setText('нет совпадений')
        wait_for(lambda: not w.loading and not w.debounce.isActive() and not any(c.count() for c in w.card_lists))
        self.assertIn('Выберите доску', w.statistics.summary.text())
        w.pages.setCurrentWidget(w.statistics)
        self.assertTrue(w.statistics.isVisible())
        wait_for(lambda: not w.loading and 'Всего: 1' in w.statistics.summary.text())
        evidence = Path(__file__).resolve().parent.parent / 'docs/screenshots'
        evidence.mkdir(parents=True, exist_ok=True)
        w.grab().save(str(evidence / 'statistics-light-linux.png'))
        try:
            w.theme_button.setChecked(False)
            QTest.mouseClick(w.theme_button, Qt.LeftButton, pos=w.theme_button.rect().center())
            self.assertTrue(theme_settings().value('dark', False, type=bool))
            self.assertEqual(TOKENS['canvas'], '#151d2b')
            w.grab().save(str(evidence / 'statistics-dark-linux.png'))
            w.pages.setCurrentIndex(0)
            w.reset_filters()
            wait_for(lambda: not w.loading and not w.debounce.isActive() and any(c.count() for c in w.card_lists))
            w.grab().save(str(evidence / 'board-dark-linux.png'))
            d = self.dialog()
            d.grab().save(str(evidence / 'task-dark-linux.png'))
            d.due.editor.setFocus()
            QTest.mouseClick(d.due.editor, Qt.LeftButton, pos=QPoint(d.due.editor.width() - 12, d.due.editor.height() // 2))
            QTest.qWait(30)
            calendar = d.due.editor.calendarWidget()
            self.assertTrue(calendar.isVisible())
            calendar.window().grab().save(str(evidence / 'calendar-dark-linux.png'))
            QTest.keyClick(calendar.focusWidget(), Qt.Key_Escape)
            self.assertEqual(d.due.value(), '2020-01-01')
            self.assertTrue(d.labels.list.item(0).icon().isNull())
            self.assertNotEqual(label_colors('Документы'), label_colors('Договоры'))
            current = self.store.tasks(self.bid)
            stats = summarize(current, self.columns, '2026-01-01')
            self.assertEqual((stats['total'], stats['overdue'], stats['completed']), (1, 1, 0))
            self.assertEqual(summarize([], self.columns)['total'], 0)
        finally:
            theme_settings().setValue('dark', initial_theme)
            apply_theme(APP, False)

    def test_columns_delete_dialog_and_new_in_column(self):
        from kanban.ui import ColumnsDialog
        from PyQt5.QtWidgets import QInputDialog
        d = ColumnsDialog(self.columns)
        d.show()
        d.table.setCurrentCell(0, 0)
        name = '{} · №{}'.format(self.columns[1]['name'], self.columns[1]['id'])
        with patch.object(QInputDialog, 'getItem', return_value=(name, True)), patch.object(QMessageBox, 'question', return_value=QMessageBox.No):
            d.remove_column()
        self.assertEqual(d.table.rowCount(), 5)
        with patch.object(QInputDialog, 'getItem', return_value=(name, True)), patch.object(QMessageBox, 'question', return_value=QMessageBox.Yes):
            d.remove_column()
        self.assertEqual(d.table.rowCount(), 4)
        self.assertEqual(d.removed, {self.columns[0]['id']: self.columns[1]['id']})
        self.store.save_columns(self.bid, d.values(), removed=d.removed)
        d.close()
        w = MainWindow(str(self.store.folder))
        self.windows.append(w)
        w.show()
        wait_for(lambda: not w.loading and len(w.card_lists) == 4)
        w.new_task_in_column(self.columns[2]['id'])
        dialog = next(iter(w.dialogs))
        wait_for(lambda: not dialog.jobs)
        self.assertEqual(dialog.payload()['column_id'], self.columns[2]['id'])
        dialog.title.setText('Из колонки')
        dialog.save()
        wait_for(lambda: sip.isdeleted(dialog) or not dialog.isVisible())
        self.assertEqual(self.store.tasks(self.bid, {'query': 'Из колонки'})[0]['column_id'], self.columns[2]['id'])

    def test_create_save_comment_and_keyboard(self):
        d = self.dialog()
        d.title.setText('Новая редакция')
        d.save_button.setFocus()
        QTest.keyClick(d.save_button, Qt.Key_Space)
        wait_for(lambda: not d.pending and not d.jobs)
        self.assertEqual(self.store.detail(self.result['id'])['title'], 'Новая редакция')
        self.assertTrue(sip.isdeleted(d) or not d.isVisible())
        d = self.dialog()
        d.tabs.setCurrentIndex(0)
        d.comment_text.setPlainText('Проверено')
        QTest.mouseClick(d.comment_button, Qt.LeftButton)
        wait_for(lambda: not d.pending and not d.jobs)
        self.assertEqual(len(self.store.detail(self.result['id'])['comments']), 1)
        self.assertEqual(d.comment_text.toPlainText(), '')
    def test_conflict_preserves_text(self):
        d = self.dialog()
        other = self.store.detail(self.result['id'])
        other['title'] = 'Изменение коллеги'
        self.store.save_task(other)
        d.description.setPlainText('Мой несохранённый текст')
        d.save()
        wait_for(lambda: not d.pending and not d.jobs)
        self.assertIsNotNone(d.conflict_current)
        self.assertEqual(d.description.toPlainText(), 'Мой несохранённый текст')
        self.assertTrue(d.compare_button.isVisible())
    def test_offline_retry_no_duplicate(self):
        d = self.dialog()
        real = self.store.comment
        state = {'first': True}
        def uncertain(*args):
            result = real(*args)
            if state['first']:
                state['first'] = False
                raise OSError('Соединение потеряно после записи')
            return result
        d.comment_text.setPlainText('Не дублировать')
        with patch.object(self.store, 'comment', side_effect=uncertain):
            d.add_comment()
            wait_for(lambda: not d.pending and not d.jobs)
            self.assertEqual(d.comment_text.toPlainText(), 'Не дублировать')
            d.add_comment()
            wait_for(lambda: not d.pending and not d.jobs)
        self.assertEqual(len(self.store.detail(self.result['id'])['comments']), 1)
    def test_uncertain_save_preserves_newer_edits(self):
        d = self.dialog()
        real = self.store.save_task
        state = {'first': True}
        def uncertain(*args):
            result = real(*args)
            if state['first']:
                state['first'] = False
                raise OSError('Ответ потерян')
            return result
        with patch.object(self.store, 'save_task', side_effect=uncertain):
            d.title.setText('Первая запись')
            d.save()
            wait_for(lambda: not d.pending and not d.jobs)
            d.title.setText('Новый текст после ошибки')
            d.save()
            wait_for(lambda: not d.pending and not d.jobs)
            self.assertEqual(self.store.detail(self.result['id'])['title'], 'Первая запись')
            self.assertEqual(d.title.text(), 'Новый текст после ошибки')
            self.assertNotEqual(d.initial, d.payload())
            d.save()
            wait_for(lambda: not d.pending and not d.jobs)
        self.assertEqual(self.store.detail(self.result['id'])['title'], 'Новый текст после ошибки')

    def test_escape_unsaved_guard(self):
        d = self.dialog()
        d.title.setText('Не терять')
        with patch.object(QMessageBox, 'question', return_value=QMessageBox.No):
            QTest.keyClick(d, Qt.Key_Escape)
        self.assertTrue(d.isVisible())
        self.assertEqual(d.title.text(), 'Не терять')
    def test_board_search_move_and_screenshots(self):
        w = MainWindow(str(self.store.folder))
        self.windows.append(w)
        w.show()
        w.timer.stop()
        wait_for(lambda: bool(w.card_lists) and not w.loading)
        self.assertEqual(sum(x.count() for x in w.card_lists), 1)
        self.assertIn('Просрочено', w.card_lists[0].item(0).text())
        evidence = Path(__file__).resolve().parent.parent / 'docs' / 'screenshots'
        evidence.mkdir(parents=True, exist_ok=True)
        self.assertTrue(w.grab().save(str(evidence / 'board-linux.png')))
        w.search.setText('Несуществующий текст')
        wait_for(lambda: not w.loading and sum(x.count() for x in w.card_lists) == 0)
        w.search.clear()
        wait_for(lambda: not w.loading and sum(x.count() for x in w.card_lists) == 1)
        w.move_task(self.result['id'], self.columns[-1]['id'])
        wait_for(lambda: not w.mutating and not w.loading)
        self.assertIsNotNone(self.store.detail(self.result['id'])['completed_at'])
        w.resize(900, 600)
        APP.processEvents()
        self.assertTrue(w.grab().save(str(evidence / 'board-narrow-linux.png')))
        d = self.dialog()
        self.assertTrue(d.grab().save(str(evidence / 'task-linux.png')))
    def test_new_task_catalog_selection_calendar_and_close(self):
        self.store.save_user('ivanov', 'Иванов Иван')
        self.grant_all(self.bid)
        self.store.add_label(self.bid, 'На проверку')
        d = TaskDialog(self.store, self.columns, {'board_id': self.bid, 'column_id': self.columns[0]['id']})
        self.windows.append(d)
        d.show()
        wait_for(lambda: not d.jobs)
        self.assertFalse(d.column.isVisible())
        d.title.setText('Новая задача из формы')
        self.assertTrue(any('Иванов Иван' in d.assignees.combo.itemText(i) for i in range(d.assignees.combo.count())))
        choose_option(d.assignees.combo, 'ivanov')
        self.assertEqual(d.assignees.values(), ['ivanov'])
        self.assertIn('Иванов Иван', d.assignees.list.item(0).text())
        choose_option(d.assignees.combo, 'tester', mouse=True)
        self.assertEqual(d.assignees.values(), ['ivanov', 'tester'])
        self.assertEqual(d.assignees.combo.findData('tester'), -1)
        row = next(i for i in range(d.assignees.list.count()) if d.assignees.list.item(i).data(Qt.UserRole) == 'tester')
        d.assignees.list.setCurrentRow(row)
        QTest.mouseClick(d.assignees.remove_button, Qt.LeftButton)
        self.assertEqual(d.assignees.values(), ['ivanov'])
        self.assertGreaterEqual(d.assignees.combo.findData('tester'), 0)
        choose_option(d.labels.combo, 'На проверку', mouse=True)
        self.assertEqual(d.labels.values(), ['На проверку'])
        self.assertIsNone(d.due.value())
        self.assertTrue(d.due.editor.isEnabled())
        QTest.mouseClick(d.due.editor, Qt.LeftButton, pos=QPoint(d.due.editor.width() - 10, d.due.editor.height() // 2))
        wait_for(lambda: d.due.editor.calendarWidget().isVisible())
        self.assertEqual(d.due.editor.calendarWidget().yearShown(), QDate.currentDate().year())
        self.assertEqual(d.due.editor.calendarWidget().monthShown(), QDate.currentDate().month())
        self.assertEqual(d.due.editor.calendarWidget().selectedDate(), QDate.currentDate())
        QTest.keyClick(d.due.editor.calendarWidget(), Qt.Key_Escape)
        self.assertIsNone(d.due.value())
        d.due.editor.setDate(QDate(2027, 3, 18))
        self.assertEqual(d.due.value(), '2027-03-18')
        QTest.mouseClick(d.due.clear_button, Qt.LeftButton)
        self.assertIsNone(d.due.value())
        d.due.editor.setDate(QDate(2027, 3, 18))
        QTest.mouseClick(d.due.editor, Qt.LeftButton, pos=QPoint(d.due.editor.width() - 12, d.due.editor.height() // 2))
        wait_for(lambda: d.due.editor.calendarWidget().isVisible())
        self.assertEqual(d.due.editor.calendarWidget().locale().name(), 'ru_RU')
        evidence = Path(__file__).resolve().parent.parent / 'docs/screenshots'
        evidence.mkdir(parents=True, exist_ok=True)
        d.due.editor.calendarWidget().window().grab().save(str(evidence / 'calendar-linux.png'))
        QTest.keyClick(d.due.editor.calendarWidget(), Qt.Key_Escape)
        d.checklist.entry.setText('Первый пункт')
        QTest.keyClick(d.checklist.entry, Qt.Key_Return)
        self.assertEqual(d.checklist.list.count(), 1)
        self.assertTrue(d.isVisible())
        d.checklist.list.item(0).setCheckState(Qt.Checked)
        d.checklist.entry.setText('Второй пункт')
        d.priority.setCurrentText('Срочный')
        APP.processEvents()
        d.grab().save(str(evidence / 'task-create-linux.png'))
        QTest.mouseClick(d.save_button, Qt.LeftButton)
        wait_for(lambda: not d.pending and not d.jobs)
        self.assertTrue(sip.isdeleted(d) or not d.isVisible())
        task = self.store.tasks(self.bid)[-1]
        self.assertEqual(task['title'], 'Новая задача из формы')
        self.assertEqual(task['assignees'], ['ivanov'])
        self.assertEqual(task['labels'], ['На проверку'])
        self.assertEqual(task['due_date'], '2027-03-18')
        self.assertEqual(task['column_id'], self.columns[0]['id'])
        self.assertEqual(task['checklist'], [{'text': 'Первый пункт', 'done': 1}, {'text': 'Второй пункт', 'done': 0}])

    def test_card_disclosure_preserves_data_and_survives_refresh(self):
        from kanban.cards import CardDelegate, EXPANDED_ROLE
        w = MainWindow(str(self.store.folder))
        self.windows.append(w)
        w.show()
        wait_for(lambda: not w.loading and bool(w.card_lists))
        cards = next(c for c in w.card_lists if c.count())
        item = cards.item(0)
        task = item.data(Qt.UserRole)
        original = self.store.detail(task['id'])
        height = cards.height()
        rect = cards.visualItemRect(item)
        arrow = CardDelegate.disclosure_rect(rect, rect.height())
        QTest.mouseClick(cards.viewport(), Qt.LeftButton, pos=arrow.center())
        self.assertTrue(item.data(EXPANDED_ROLE))
        self.assertGreater(cards.height(), height)
        self.assertFalse(w.dialogs)
        cards.grab().save(str(Path(__file__).resolve().parent.parent / 'docs/screenshots/card-expanded.png'))
        self.assertEqual(self.store.detail(task['id']), original)
        self.store.comment(task['id'], 'Обновление обсуждения')
        w.refresh()
        wait_for(lambda: not w.loading and not w.jobs)
        cards = next(c for c in w.card_lists if c.count())
        item = cards.item(0)
        self.assertTrue(item.data(EXPANDED_ROLE))
        cards.setCurrentItem(item)
        QTest.keyClick(cards, Qt.Key_Space)
        self.assertFalse(item.data(EXPANDED_ROLE))
        self.assertFalse(w.dialogs)

    def test_raster_controls_and_tab_labels_fit(self):
        from PyQt5.QtGui import QFontMetrics, QImageReader
        from kanban.theme import symbol_icon, apply_theme
        self.assertIn(b'png', [bytes(x) for x in QImageReader.supportedImageFormats()])
        for dark in (False, True):
            apply_theme(APP, dark)
            for name in ('menu', 'chat', 'calendar-prev', 'calendar-next'):
                self.assertFalse(symbol_icon(name).pixmap(24, 24).isNull(), name)
        apply_theme(APP, False)
        d = self.dialog()
        bar = d.tabs.tabBar()
        for selected in range(bar.count()):
            d.tabs.setCurrentIndex(selected)
            APP.processEvents()
            for i in range(bar.count()):
                text_width = QFontMetrics(bar.font()).horizontalAdvance(bar.tabText(i))
                self.assertGreaterEqual(bar.tabRect(i).width(), text_width + 24)

    def test_date_field_opens_from_text_and_keeps_date_on_cancel(self):
        d = self.dialog()
        editor = d.due.editor
        calendar = editor.calendarWidget()
        for value in ('2027-03-18', None):
            d.due.set_value(value)
            for target, point in ((editor, QPoint(8, editor.height() // 2)),
                                  (editor.lineEdit(), editor.lineEdit().rect().center())):
                QTest.mouseClick(target, Qt.LeftButton, pos=point)
                wait_for(calendar.isVisible)
                if value is None:
                    self.assertEqual(calendar.yearShown(), QDate.currentDate().year())
                QTest.keyClick(calendar, Qt.Key_Escape)
                wait_for(lambda: not calendar.isVisible())
                self.assertEqual(d.due.value(), value)
        for name, radio in d.priority.buttons.items():
            QTest.mouseClick(radio, Qt.LeftButton)
            self.assertEqual(d.priority.currentText(), name)
            self.assertEqual(sum(b.isChecked() for b in d.priority.buttons.values()), 1)

    def test_calendar_selects_date_with_keyboard(self):
        d = self.dialog()
        d.due.editor.setDate(QDate(2027, 3, 18))
        QTest.mouseClick(d.due.editor, Qt.LeftButton, pos=QPoint(d.due.editor.width() - 10, d.due.editor.height() // 2))
        calendar = d.due.editor.calendarWidget()
        wait_for(calendar.isVisible)
        target = calendar.focusWidget()
        self.assertIsNotNone(target)
        QTest.keyClick(target, Qt.Key_Right)
        QTest.keyClick(target, Qt.Key_Return)
        wait_for(lambda: not calendar.isVisible())
        self.assertEqual(d.due.value(), '2027-03-19')
        QTest.mouseClick(d.due.clear_button, Qt.LeftButton)
        self.assertIsNone(d.due.value())
        self.assertTrue(d.due.editor.isEnabled())
        QTest.mouseClick(d.due.editor, Qt.LeftButton, pos=QPoint(d.due.editor.width() - 10, d.due.editor.height() // 2))
        wait_for(calendar.isVisible)
        self.assertIsNone(d.due.value())
        QTest.keyClick(calendar.focusWidget(), Qt.Key_Return)
        wait_for(lambda: not calendar.isVisible())
        self.assertEqual(d.due.value(), QDate.currentDate().toString(Qt.ISODate))

    def test_column_add_button_and_long_assignee_layout(self):
        from PyQt5.QtWidgets import QPushButton
        w = MainWindow(str(self.store.folder))
        self.windows.append(w)
        w.show()
        wait_for(lambda: not w.loading and bool(w.card_lists))
        for cards in w.card_lists:
            panel = cards.parentWidget()
            add = panel.findChild(QPushButton, 'AddColumnTask')
            self.assertIsNotNone(add)
            self.assertLess(add.geometry().bottom(), cards.geometry().top())
        long_name = 'Иванов Иван Иванович — ведущий специалист проектного отдела'
        self.store.save_user('ivanov', long_name)
        self.grant_all(self.bid)
        d = self.dialog()
        choose_option(d.assignees.combo, 'ivanov')
        APP.processEvents()
        field = d.assignees
        self.assertFalse(field.list.horizontalScrollBar().isVisible())
        self.assertGreaterEqual(field.selected_row.height(), field.list.height())
        page = d.task_page
        from PyQt5.QtWidgets import QLabel
        labels_caption = next(l for l in page.findChildren(QLabel) if l.text() == 'Метки')
        self.assertLess(field.geometry().bottom(), labels_caption.geometry().top())
        item = next(field.list.item(i) for i in range(field.list.count())
                    if field.list.item(i).data(Qt.UserRole) == 'ivanov')
        self.assertIn(long_name, item.toolTip())
        self.assertGreaterEqual(field.list.visualItemRect(item).height(), field.list.fontMetrics().height() + 8)
        self.assertGreaterEqual(field.list.viewport().height(), field.list.visualItemRect(item).bottom() + 1)
        self.assertLessEqual(field.list.geometry().right(), field.remove_button.geometry().left())
        d.grab().save(str(Path(__file__).resolve().parent.parent / 'docs/screenshots/long-assignee-layout.png'))

    def test_selected_users_and_labels_reopen_and_remove(self):
        d = self.dialog()
        self.assertEqual(d.assignees.values(), ['tester'])
        self.assertEqual(d.labels.values(), ['Документы'])
        self.assertIn('tester', d.assignees.list.item(0).text())
        self.assertEqual(d.labels.list.item(0).text(), 'Документы')
        for field in [d.assignees, d.labels]:
            field.list.setCurrentRow(0)
            QTest.mouseClick(field.remove_button, Qt.LeftButton)
            self.assertEqual(field.values(), [])
        QTest.mouseClick(d.save_button, Qt.LeftButton)
        wait_for(lambda: not d.pending and not d.jobs)
        saved = self.store.detail(self.result['id'])
        self.assertEqual(saved['assignees'], [])
        self.assertEqual(saved['labels'], [])
        reopened = self.dialog()
        choose_option(reopened.assignees.combo, 'tester', mouse=True)
        choose_option(reopened.labels.combo, 'Документы')
        QTest.mouseClick(reopened.save_button, Qt.LeftButton)
        wait_for(lambda: not reopened.pending and not reopened.jobs)
        saved = self.store.detail(self.result['id'])
        self.assertEqual(saved['assignees'], ['tester'])
        self.assertEqual(saved['labels'], ['Документы'])

    def test_catalog_dialog_add_and_refresh(self):
        d = CatalogDialog(self.store, self.bid, 'users')
        self.windows.append(d)
        d.show()
        wait_for(lambda: not d.jobs)
        d.login.setText('petrov')
        d.name.setText('Петров Пётр')
        QTest.mouseClick(d.save_button, Qt.LeftButton)
        wait_for(lambda: not d.jobs)
        self.assertTrue(any('Петров Пётр' in d.list.item(i).text() for i in range(d.list.count())))
        self.grant_all(self.bid)
        task = self.dialog()
        self.assertTrue(any(task.assignees.combo.itemData(i) == 'petrov' for i in range(task.assignees.combo.count())))
        label = CatalogDialog(self.store, self.bid, 'labels')
        self.windows.append(label)
        label.show()
        wait_for(lambda: not label.jobs)
        label.name.setText('Согласование')
        QTest.mouseClick(label.save_button, Qt.LeftButton)
        wait_for(lambda: not label.jobs)
        task.load_catalogs()
        wait_for(lambda: not task.jobs)
        self.assertGreaterEqual(task.labels.combo.findData('Согласование'), 0)

    def test_create_task_has_dropdowns_without_catalog_navigation(self):
        from PyQt5.QtWidgets import QPushButton
        d = TaskDialog(self.store, self.columns,
                       {'board_id': self.bid, 'column_id': self.columns[0]['id']})
        self.windows.append(d)
        d.show()
        wait_for(lambda: not d.jobs)
        self.assertGreater(d.assignees.combo.count(), 1)
        self.assertGreater(d.labels.combo.count(), 1)
        for b in d.findChildren(QPushButton):
            self.assertNotIn('справочник', (b.toolTip() + b.accessibleName()).lower())
            self.assertNotEqual(b.text(), '+')
        choose_option(d.assignees.combo, 'tester')
        self.assertIn('tester', d.assignees.values())

    def test_readable_history_and_all_priorities(self):
        self.store.save_user('tester', 'Тестировщик', self.store.profile()['id'])
        data = self.store.detail(self.result['id'])
        data['due_date'] = '2027-04-01'
        data['column_id'] = self.columns[2]['id']
        data['priority'] = 'Срочный'
        data['description'] = '<b>Обычный текст, не разметка</b>'
        self.store.save_task(data)
        d = self.dialog()
        text = d.history.toPlainText()
        self.assertEqual(d.description.toPlainText(), data['description'])
        self.assertIn('Приоритет: Высокий → Срочный', text)
        self.assertIn('Колонка: Входящие → В работе', text)
        self.assertIn('Срок: 01.01.2020 → 01.04.2027', text)
        self.assertIn('Тестировщик', text)
        self.assertNotIn('"column_id"', text)
        self.assertNotIn('"assignees"', text)
        self.assertIn('&lt;b&gt;', history_html(self.store.detail(self.result['id'])['history'], self.columns))
        self.assertEqual(len(d.priority.buttons), 4)
        self.assertEqual(sum(b.isChecked() for b in d.priority.buttons.values()), 1)
        self.assertEqual(len({v[0] for v in PRIORITIES.values()}), 4)
        evidence = Path(__file__).resolve().parent.parent / 'docs/screenshots'
        evidence.mkdir(parents=True, exist_ok=True)
        d.tabs.setCurrentIndex(2)
        APP.processEvents()
        d.grab().save(str(evidence / 'history-linux.png'))
        d.tabs.setCurrentIndex(0)
        d.assignees.combo.showPopup()
        APP.processEvents()
        d.assignees.combo.view().window().grab().save(str(evidence / 'assignees-popup-linux.png'))
        d.assignees.combo.hidePopup()

    def test_priorities_board_and_narrow_layout(self):
        titles = ['Проверить реквизиты договора', 'Подготовить письмо партнёрам', 'Согласовать проект документа', 'Отправить итоговый комплект']
        for i, priority in enumerate(PRIORITIES):
            self.store.save_task({'board_id': self.bid, 'column_id': self.columns[i]['id'], 'title': titles[i], 'priority': priority, 'assignees': ['tester'], 'due_date': '2027-04-01', 'labels': ['Документы']})
        w = MainWindow(str(self.store.folder))
        self.windows.append(w)
        w.resize(1640, 900)
        w.show()
        w.timer.stop()
        wait_for(lambda: bool(w.card_lists) and not w.loading)
        QTest.qWait(100)
        evidence = Path(__file__).resolve().parent.parent / 'docs/screenshots'
        evidence.mkdir(parents=True, exist_ok=True)
        w.grab().save(str(evidence / 'priorities-linux.png'))
        w.resize(1000, 650)
        QTest.qWait(100)
        self.assertGreater(w.scroll.horizontalScrollBar().maximum(), 0)
        w.grab().save(str(evidence / 'board-narrow-linux.png'))

    def test_background_refresh_does_not_replace_form(self):
        d = self.dialog()
        d.description.setPlainText('Мой текст')
        selected = d.assignees.values()
        self.store.save_user('new-colleague', 'Новый сотрудник')
        self.grant_all(self.bid)
        self.store.add_label(self.bid, 'Новая метка коллеги')
        other = self.store.detail(self.result['id'])
        other['description'] = 'Текст коллеги'
        self.store.save_task(other)
        d.refresh_extras()
        wait_for(lambda: not d.jobs)
        self.assertEqual(d.description.toPlainText(), 'Мой текст')
        self.assertIn('изменения коллег', d.status.text())
        self.assertGreaterEqual(d.assignees.combo.findData('new-colleague'), 0)
        self.assertGreaterEqual(d.labels.combo.findData('Новая метка коллеги'), 0)
        self.assertEqual(d.assignees.values(), selected)


if __name__ == '__main__':
    unittest.main()
