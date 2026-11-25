import json
from math import ceil

from PyQt5.QtCore import QRect, QSize, Qt
from PyQt5.QtGui import QAbstractTextDocumentLayout, QColor, QFontMetrics, QPalette
from PyQt5.QtWidgets import QHBoxLayout, QLabel, QListWidgetItem, QPushButton, QScrollArea, QVBoxLayout, QWidget
from .bugzilla import xml_value_to_json
from .identification import ProjectCardDelegate, project_card_column
from .theme import TOKENS


def field_text(value):
    if value is None or value == '':
        return '—'
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=xml_value_to_json)


class BugzillaCard(ProjectCardDelegate):
    side_padding = 8

    def details_offset(self, option, number, width):
        number_width = QFontMetrics(self.heading_font(option)).horizontalAdvance(number)
        return max(width * 2 // 5, number_width + 10)

    def sizeHint(self, option, index):
        card = index.data(Qt.UserRole)
        if card is None:
            return QSize(0, 66)
        number, lines, _ = card
        view = self.parent()
        width = max(1, view.viewport().width() - 2 * (view.spacing() + self.side_padding) - 4)
        details_width = max(1, width - self.details_offset(option, number, width))
        height = ceil(self.body_document(option, lines, details_width).size().height())
        return QSize(0, max(66, 22 + height))

    def paint(self, painter, option, index):
        number, lines, linked = index.data(Qt.UserRole)
        painter.save()
        box = self.paint_surface(painter, option, linked)
        x, y = box.x() + self.side_padding, box.y() + 8
        width = box.width() - 2 * self.side_padding
        offset = self.details_offset(option, number, width)
        painter.setFont(self.heading_font(option))
        painter.setPen(QColor(TOKENS['text']))
        painter.drawText(QRect(x, y, offset, 22), Qt.AlignTop | Qt.AlignLeft, number)
        document = self.body_document(option, lines, max(1, width - offset))
        context = QAbstractTextDocumentLayout.PaintContext()
        context.palette.setColor(QPalette.Text, QColor(TOKENS['text']))
        painter.translate(x + offset, y + 1)
        document.documentLayout().draw(painter, context)
        painter.restore()


class BugzillaPage(QWidget):
    def __init__(self, export, export_all, refresh):
        super().__init__()
        self.snapshot = None
        self.linked = frozenset()
        self.loading = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 12)
        actions = QHBoxLayout()
        self.export_button = QPushButton('По ID…')
        self.export_button.setAccessibleName('Выгрузить баг по ID')
        self.export_button.setToolTip('Выгрузить баг по ID')
        self.export_all_button = QPushButton('Выгрузить по фильтрам')
        self.refresh_button = QPushButton('Обновить')
        for button, callback in ((self.export_button, export), (self.export_all_button, export_all),
                                 (self.refresh_button, refresh)):
            button.setCursor(Qt.PointingHandCursor)
            button.clicked.connect(callback)
            actions.addWidget(button)
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
        for title in ('Не привязаны', 'Привязаны'):
            panel, cards = project_card_column(title, BugzillaCard, minimum_width=150)
            cards.setSpacing(5)
            columns.addWidget(panel, 1)
            self.columns.append(cards)
        columns.addStretch(2)
        self.scroll.setWidget(canvas)
        layout.addWidget(self.scroll, 1)
        self.show_cards(())

    def set_loading(self, loading):
        self.loading = loading
        self.export_button.setEnabled(not loading)
        self.export_all_button.setEnabled(not loading)
        self.refresh_button.setEnabled(not loading and bool(self.snapshot))
        self.progress.setVisible(loading)

    def show_cards(self, cards, linked=()):
        snapshot = tuple(cards)
        linked = frozenset(linked)
        if snapshot == self.snapshot and linked == self.linked:
            return
        self.snapshot = snapshot
        self.linked = linked
        scroll_positions = [column.verticalScrollBar().value() for column in self.columns]
        for column in self.columns:
            column.clear()
        for bug in snapshot:
            is_linked = str(bug['id']).lstrip('0') in linked
            number = str(bug['id'])
            lines = [field_text(bug['summary']),
                     field_text(bug['cf_cr_num']),
                     field_text(bug['cf_event_1'])]
            item = QListWidgetItem(self.columns[int(is_linked)])
            item.setData(Qt.UserRole, (number, lines, is_linked))
            summary = '\n'.join([number, *lines])
            item.setData(Qt.AccessibleTextRole, summary)
            item.setToolTip(summary)
        for column, position in zip(self.columns, scroll_positions):
            column.verticalScrollBar().setValue(position)
        self.refresh_button.setEnabled(not self.loading and bool(snapshot))
