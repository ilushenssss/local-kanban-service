import json

from PyQt5.QtCore import QPointF, QRect, QSize, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QFontMetrics, QTextLayout, QTextOption
from PyQt5.QtWidgets import (QComboBox, QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                             QListWidget, QListWidgetItem, QMenu, QPlainTextEdit, QPushButton,
                             QScrollArea, QVBoxLayout, QWidget)
from .bugzilla import assignee_initials, is_assigned_to, is_closed, xml_value_to_json
from .identification import ProjectCardDelegate, project_card_column
from .theme import STICKERS, TOKENS, symbol_icon
from .widgets import DateField


def field_text(value):
    if value is None or value == '':
        return '—'
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=xml_value_to_json)


class BugzillaGrid(QListWidget):
    comment_requested = pyqtSignal(str)
    card_height = 90
    gap = 8

    def __init__(self):
        super().__init__()
        self.setViewMode(QListWidget.IconMode)
        self.setFlow(QListWidget.LeftToRight)
        self.setWrapping(True)
        self.setMovement(QListWidget.Static)
        self.setVerticalScrollMode(QListWidget.ScrollPerPixel)

    def resizeEvent(self, event):
        width = self.maximumViewportSize().width() - self.verticalScrollBar().sizeHint().width() - self.gap
        columns = 2 if width >= 400 else 1
        self.setGridSize(QSize(max(1, width // columns), self.card_height + self.gap))
        super().resizeEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Enter, Qt.Key_Return) and self.currentItem():
            if event.modifiers() == Qt.ControlModifier:
                self.comment_requested.emit(self.currentItem().data(Qt.UserRole)[0])
            else:
                self.itemClicked.emit(self.currentItem())
            event.accept()
            return
        super().keyPressEvent(event)


class BugzillaCard(ProjectCardDelegate):
    def sizeHint(self, option, index):
        grid = self.parent()
        return QSize(max(1, grid.gridSize().width() - grid.gap), grid.card_height)

    def paint(self, painter, option, index):
        number, (title, ti, action), linked, initials = index.data(Qt.UserRole)
        painter.save()
        box = self.paint_surface(painter, option, linked)
        left, top = box.x() + 8, box.y() + 9
        width = box.width() - 16
        if width < 32:
            painter.restore()
            return

        painter.setFont(self.heading_font(option))
        painter.setPen(QColor(TOKENS['text']))
        metrics = painter.fontMetrics()
        number_width = min(metrics.horizontalAdvance(number), width // 3)
        painter.drawText(QRect(left, top, number_width, 18), Qt.AlignTop | Qt.AlignLeft,
                         metrics.elidedText(number, Qt.ElideRight, number_width))

        font = self.body_font(option)
        metrics = QFontMetrics(font)
        title_x = left + number_width + 8
        title_width = width - number_width - 8
        layout = QTextLayout(title, font)
        wrap = QTextOption()
        wrap.setWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
        layout.setTextOption(wrap)
        layout.beginLayout()
        offset = 0
        y = top
        for _ in range(2):
            line = layout.createLine()
            if not line.isValid():
                break
            line.setLineWidth(title_width)
            line.setPosition(QPointF(0, y - top))
            offset = line.textStart() + line.textLength()
            y += metrics.lineSpacing()
        layout.endLayout()
        layout.draw(painter, QPointF(title_x, top))
        painter.setFont(font)
        painter.drawText(QRect(title_x, y, title_width, metrics.height()), Qt.AlignTop | Qt.AlignLeft,
                         metrics.elidedText(title[offset:], Qt.ElideRight, title_width))

        badge_y = box.bottom() - 25
        badges_width = width
        if initials:
            size = min(width // 3, max(22, metrics.horizontalAdvance(initials) + 8))
            rect = QRect(left + width - size, badge_y, size, 20)
            self.paint_badge_background(painter, rect, TOKENS['selection'])
            painter.setPen(QColor(TOKENS['selected_text']))
            painter.drawText(rect, Qt.AlignCenter, metrics.elidedText(initials, Qt.ElideRight, size))
            badges_width -= size + 5
        ti_colors = STICKERS[0] if ti == '—' else (TOKENS['secondary'], TOKENS['hover'])
        ti_width = min(metrics.horizontalAdvance(ti) + 12, badges_width // 2)
        for value, x, available, (foreground, background), border in (
            (ti, left, ti_width, ti_colors, None),
            (action, left + ti_width + 5, badges_width - ti_width - 5,
             (TOKENS['secondary'], TOKENS['properties']), TOKENS['field_border']),
        ):
            span = min(available, metrics.horizontalAdvance(value) + 12)
            rect = QRect(x, badge_y, span, 20)
            self.paint_badge_background(painter, rect, background, border)
            painter.setPen(QColor(foreground))
            painter.drawText(rect, Qt.AlignCenter, metrics.elidedText(value, Qt.ElideRight, max(0, span - 10)))
        painter.restore()


class BugzillaPage(QWidget):
    def __init__(self, refresh, open_bug, open_in_browser, comment_bug, can_comment):
        super().__init__()
        self.open_bug, self.comment_bug, self.can_comment = open_bug, comment_bug, can_comment
        self.open_in_browser = open_in_browser
        self.snapshot = None
        self.linked = frozenset()
        self.login = ''
        self.loading = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 12)
        actions = QHBoxLayout()
        self.refresh_button = QPushButton(symbol_icon('refresh'), '')
        self.refresh_button.setProperty('symbol', 'refresh')
        self.refresh_button.setToolTip('Выгрузить баги из Bugzilla по фильтрам')
        self.refresh_button.setAccessibleName(self.refresh_button.toolTip())
        self.refresh_button.setCursor(Qt.PointingHandCursor)
        self.refresh_button.clicked.connect(refresh)
        actions.addWidget(self.refresh_button)
        actions.addStretch()
        layout.addLayout(actions)
        self.progress = QLabel('Запрос к Bugzilla…')
        self.progress.setObjectName('Muted')
        self.progress.hide()
        layout.addWidget(self.progress)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        canvas = QWidget()
        canvas.setObjectName('BoardCanvas')
        columns = QHBoxLayout(canvas)
        columns.setContentsMargins(0, 0, 0, 0)
        columns.setSpacing(18)
        self.columns = []
        for title in ('Не привязаны', 'Привязаны', 'Мои баги'):
            panel, cards = project_card_column(title, BugzillaCard, minimum_width=150, list_type=BugzillaGrid)
            cards.setSpacing(0)
            cards.itemClicked.connect(lambda item: self.open_bug(item.data(Qt.UserRole)[0]))
            cards.comment_requested.connect(self.comment_bug)
            cards.setContextMenuPolicy(Qt.CustomContextMenu)
            cards.customContextMenuRequested.connect(lambda point, cards=cards: self.bug_menu(cards, point))
            columns.addWidget(panel, 1)
            self.columns.append(cards)
        self.scroll.setWidget(canvas)
        layout.addWidget(self.scroll, 1)
        self.show_cards(())

    def set_loading(self, loading):
        self.loading = loading
        self.refresh_button.setEnabled(not loading)
        self.progress.setVisible(loading)

    def bug_menu(self, cards, point):
        item = cards.itemAt(point)
        if item is None:
            return
        number = item.data(Qt.UserRole)[0]
        menu = QMenu(cards)
        menu.addAction('Открыть карточку', lambda: self.open_bug(number))
        menu.addAction('Открыть в Bugzilla', lambda: self.open_in_browser(number))
        if self.can_comment():
            menu.addAction('Написать комментарий…', lambda: self.comment_bug(number))
        menu.exec_(cards.viewport().mapToGlobal(point))

    def show_cards(self, cards, linked=(), login=''):
        snapshot = tuple(card for card in cards if not is_closed(card))
        linked = frozenset(linked)
        if snapshot == self.snapshot and linked == self.linked and login == self.login:
            return
        self.snapshot = snapshot
        self.linked = linked
        self.login = login
        scroll_positions = [column.verticalScrollBar().value() for column in self.columns]
        for column in self.columns:
            column.clear()
        for bug in snapshot:
            is_linked = str(bug['id']).lstrip('0') in linked
            number = str(bug['id'])
            title = ' '.join(field_text(bug['summary']).split())
            if len(title) > 100:
                title = title[:99].rstrip() + '…'
            lines = [title,
                     field_text(bug['cf_cr_num']),
                     field_text(bug['cf_event_1'])]
            column = 2 if is_assigned_to(bug, login) else int(is_linked)
            item = QListWidgetItem(self.columns[column])
            item.setData(Qt.UserRole, (number, lines, is_linked, assignee_initials(bug)))
            summary = '\n'.join([number, *lines])
            item.setData(Qt.AccessibleTextRole, summary)
            assignee = bug.get('assigned_to_name') or bug.get('assigned_to')
            description = f'Исполнитель: {assignee}. ' if assignee else ''
            item.setToolTip(summary + '\n' + description + 'Нажмите, чтобы открыть карточку. Действия доступны в контекстном меню.')
            item.setData(Qt.AccessibleDescriptionRole, description + 'Enter — открыть карточку. Действия доступны в контекстном меню.')
        for column, position in zip(self.columns, scroll_positions):
            column.verticalScrollBar().setValue(position)


class BugEditDialog(QDialog):
    def __init__(self, number, open_in_browser, save, send_comment, parent):
        super().__init__(parent)
        self.setWindowTitle(f'Баг #{number} · Bugzilla')
        self.setMinimumWidth(460)
        self.pending = True
        self.can_comment = False
        self.editable = set()
        self.original = {}
        self.initial = {}
        layout = QVBoxLayout(self)
        self.title = QLabel()
        self.title.setTextFormat(Qt.PlainText)
        self.title.setWordWrap(True)
        self.title.setObjectName('SectionTitle')
        layout.addWidget(self.title)
        form = QFormLayout()
        self.action = QComboBox()
        self.deadline = DateField()
        self.ti = QLineEdit()
        self.assignee = QComboBox()
        for combo in (self.action, self.assignee):
            combo.setMinimumContentsLength(18)
            combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.fields = {'cf_event_1': self.action, 'deadline': self.deadline,
                       'cf_cr_num': self.ti, 'assigned_to': self.assignee}
        for label, widget in zip(('Дальнейшее действие', 'Срок', 'ТИ', 'Исполнитель'), self.fields.values()):
            widget.setAccessibleName(label)
            form.addRow(label, widget)
        layout.addLayout(form)
        self.status = QLabel('Загрузка из Bugzilla…')
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.PlainText)
        layout.addWidget(self.status)
        self.comment_section = QWidget()
        comment_layout = QVBoxLayout(self.comment_section)
        comment_layout.setContentsMargins(0, 4, 0, 4)
        label = QLabel('Комментарий')
        label.setObjectName('SectionTitle')
        self.comment = QPlainTextEdit()
        self.comment.setAccessibleName('Комментарий к багу')
        self.comment.setPlaceholderText('Напишите комментарий…')
        self.comment.setMaximumHeight(130)
        label.setBuddy(self.comment)
        comment_layout.addWidget(label)
        comment_layout.addWidget(self.comment)
        comment_actions = QHBoxLayout()
        self.comment_status = QLabel()
        self.comment_status.setTextFormat(Qt.PlainText)
        self.comment_status.setWordWrap(True)
        comment_actions.addWidget(self.comment_status, 1)
        self.send_button = QPushButton('Отправить')
        self.send_button.setAutoDefault(False)
        self.send_button.clicked.connect(lambda: send_comment(self))
        comment_actions.addWidget(self.send_button)
        comment_layout.addLayout(comment_actions)
        layout.addWidget(self.comment_section)
        self.comment_section.hide()
        buttons = QHBoxLayout()
        page = QPushButton('Открыть в Bugzilla')
        page.clicked.connect(open_in_browser)
        buttons.addWidget(page)
        buttons.addStretch()
        close = QPushButton('Закрыть')
        close.clicked.connect(self.reject)
        buttons.addWidget(close)
        self.save_button = QPushButton('Сохранить')
        self.save_button.setObjectName('primary')
        self.save_button.clicked.connect(lambda: save(self))
        buttons.addWidget(self.save_button)
        layout.addLayout(buttons)
        self.action.currentIndexChanged.connect(self.update_save)
        self.deadline.changed.connect(self.update_save)
        self.ti.textChanged.connect(self.update_save)
        self.assignee.currentIndexChanged.connect(self.update_save)
        self.comment.textChanged.connect(self.update_save)
        self.set_pending(True)

    def load(self, bug, choices, editable):
        self.original = bug
        self.title.setText(str(bug.get('summary') or ''))
        actions, assignees, warnings = choices
        original_action = bug.get('cf_event_1')
        multiple = isinstance(original_action, list)
        current_assignee = bug.get('assigned_to') or ''
        for widget, current, options in (
                (self.action, original_action, [(value, [value] if multiple else value) for value in actions or ()]),
                (self.assignee, current_assignee, [(label, account) for account, label in sorted(
                    (assignees or {}).items(), key=lambda item: item[1].casefold())])):
            widget.addItem(field_text(current), current)
            for label, value in options:
                if value != current:
                    widget.addItem(label, value)
        self.deadline.set_value(str(bug['deadline'])[:10] if bug.get('deadline') else None)
        ti = bug.get('cf_cr_num')
        self.ti.setText(field_text(ti) if ti is not None else '')
        if editable:
            self.editable = {field for field in self.fields if field in bug}
            self.editable.add('deadline')
            if not actions:
                self.editable.discard('cf_event_1')
            if not assignees:
                self.editable.discard('assigned_to')
            if 'deadline' not in bug:
                warnings = [*warnings, 'Bugzilla не вернула текущий срок. Можно выбрать новую дату.']
        self.initial = self.values()
        self.status.setText('\n'.join(warnings) if editable else 'Просмотр')
        self.save_button.setVisible(editable)
        self.can_comment = editable
        self.comment_section.setVisible(self.can_comment)
        self.set_pending(False)

    def values(self):
        return {'cf_event_1': self.action.currentData(), 'deadline': self.deadline.text(),
                'cf_cr_num': self.ti.text(), 'assigned_to': self.assignee.currentData()}

    def changes(self):
        return {field: value for field, value in self.values().items()
                if field in self.editable and value != self.initial[field]}

    def update_save(self):
        self.save_button.setEnabled(not self.pending and bool(self.changes()))
        self.send_button.setEnabled(self.can_comment and not self.pending and bool(self.comment.toPlainText().strip()))

    def set_pending(self, pending):
        self.pending = pending
        for field, widget in self.fields.items():
            widget.setEnabled(not pending and field in self.editable)
        self.comment.setReadOnly(pending)
        self.update_save()

    def reject(self):
        if not self.pending:
            super().reject()
