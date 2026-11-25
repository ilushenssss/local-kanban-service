import json

from PyQt5.QtCore import QPointF, QRect, QSize, Qt
from PyQt5.QtGui import QColor, QFontMetrics, QTextLayout, QTextOption
from PyQt5.QtWidgets import QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton, QScrollArea, QVBoxLayout, QWidget
from .bugzilla import is_closed, xml_value_to_json
from .identification import ProjectCardDelegate, project_card_column
from .theme import STICKERS, TOKENS


def field_text(value):
    if value is None or value == '':
        return '—'
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=xml_value_to_json)


class BugzillaGrid(QListWidget):
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


class BugzillaCard(ProjectCardDelegate):
    def sizeHint(self, option, index):
        grid = self.parent()
        return QSize(max(1, grid.gridSize().width() - grid.gap), grid.card_height)

    def paint(self, painter, option, index):
        number, (title, ti, action), linked = index.data(Qt.UserRole)
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
        ti_colors = STICKERS[0] if ti == '—' else (TOKENS['secondary'], TOKENS['hover'])
        ti_width = min(metrics.horizontalAdvance(ti) + 12, width // 2)
        for value, x, available, (foreground, background), border in (
            (ti, left, ti_width, ti_colors, None),
            (action, left + ti_width + 5, width - ti_width - 5,
             (TOKENS['secondary'], TOKENS['properties']), TOKENS['field_border']),
        ):
            span = min(available, metrics.horizontalAdvance(value) + 12)
            rect = QRect(x, badge_y, span, 20)
            self.paint_badge_background(painter, rect, background, border)
            painter.setPen(QColor(foreground))
            painter.drawText(rect, Qt.AlignCenter, metrics.elidedText(value, Qt.ElideRight, max(0, span - 10)))
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
            panel, cards = project_card_column(title, BugzillaCard, minimum_width=150, list_type=BugzillaGrid)
            cards.setSpacing(0)
            columns.addWidget(panel, 1)
            self.columns.append(cards)
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
        snapshot = tuple(card for card in cards if not is_closed(card))
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
            title = ' '.join(field_text(bug['summary']).split())
            if len(title) > 100:
                title = title[:99].rstrip() + '…'
            lines = [title,
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
