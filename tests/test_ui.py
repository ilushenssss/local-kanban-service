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
from PyQt5.QtWidgets import QApplication, QMessageBox, QScrollArea
from kanban.store import Store
from kanban.theme import stylesheet
from kanban.ui import MainWindow, TaskDialog, CatalogDialog
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
    # Click the field body, as a user does; native combo owns the whole hit area.
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
        self.bid = self.store.board_action('Проектный отдел')
        self.columns = self.store.columns(self.bid)
        self.result = self.store.save_task({'board_id': self.bid, 'column_id': self.columns[0]['id'], 'title': 'Подготовить договор', 'description': 'Согласовать условия', 'due_date': '2020-01-01', 'priority': 'Высокий', 'assignees': ['tester'], 'labels': ['Документы'], 'checklist': [{'text': 'Проверить реквизиты', 'done': 1}, {'text': 'Подписать', 'done': 0}]})
        self.windows = []
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
        # Updates preserve deliberate reading of earlier messages.
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
        w.pages.setCurrentWidget(w.my_tasks)
        w.select_tree_board(current, 0)
        self.assertEqual(w.pages.currentIndex(), 0)
        w.collapse_sidebar()
        visible = [b for b in w.sidebar.findChildren(QPushButton) if b.isVisible()]
        self.assertEqual(visible, [w.collapse_button])
        self.assertEqual(w.collapse_button.text(), '☰')
        APP.processEvents()
        self.assertTrue(w.sidebar.rect().contains(w.collapse_button.geometry().center()))
        evidence = Path(__file__).resolve().parent.parent / 'docs/screenshots'
        w.grab().save(str(evidence / 'collapsed-menu-linux.png'))
        w.collapse_sidebar()
        self.assertTrue(w.board_tree.isVisible())

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
        def uncertain(text, rid=None):
            original(text, rid)
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

    def test_redesigned_workspace_groups_and_message_badges(self):
        names = ['Бэклог', 'В работе', 'На проверке', 'Готово']
        board = self.store.board_action('Разработка / Релиз платформы', column_names=names)
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
        group = next(w.board_tree.topLevelItem(i) for i in range(w.board_tree.topLevelItemCount()) if w.board_tree.topLevelItem(i).text(0) == 'Разработка')
        self.assertEqual(group.child(0).text(0), 'Релиз платформы')
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
        second_columns = self.store.columns(second)
        own = self.store.save_task(dict(board_id=second, column_id=second_columns[0]['id'], title='Личная задача', assignees=[login]))
        self.store.save_task(dict(board_id=second, column_id=second_columns[0]['id'], title='Чужая задача', assignees=['another']))
        w = MainWindow(str(self.store.folder))
        self.windows.append(w)
        w.show()
        wait_for(lambda: not w.loading and w.board_tabs.count() == 2)
        self.assertFalse(w.boards_list.isVisible())
        self.assertEqual(len(w.my_tasks.tasks), 1)
        self.assertEqual(w.my_tasks.tasks[0]['id'], own['id'])
        w.pages.setCurrentWidget(w.my_tasks)
        self.assertTrue(w.my_tasks.isVisible())
        w.my_tasks.search.setText('Чужая')
        self.assertEqual(w.my_tasks.table.rowCount(), 0)
        w.my_tasks.search.clear()
        w.open_personal_task(w.my_tasks.tasks[0])
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
        self.assertIn('Всего: 1', w.statistics.summary.text())
        w.pages.setCurrentWidget(w.statistics)
        self.assertTrue(w.statistics.isVisible())
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
            self.assertFalse(d.labels.list.item(0).icon().isNull())
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
        # Empty deadline opens today's month immediately, with no enabling checkbox.
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
        # An unfinished input is included on Save rather than being silently lost.
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

    def test_readable_history_and_all_priorities(self):
        self.store.save_user('tester', 'Тестировщик', self.store.catalogs(self.bid)['users'][0]['id'])
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
        for i in range(d.priority.count()):
            self.assertFalse(d.priority.itemIcon(i).isNull())
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
