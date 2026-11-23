"""Read-only ИД cards from the board's Redmine basis project."""
from PyQt5.QtCore import QRect, QRectF, QSize, Qt
from PyQt5.QtGui import QColor, QFont, QFontMetrics, QPainterPath
from PyQt5.QtWidgets import (QLabel, QListView, QListWidget, QListWidgetItem, QPushButton, QScrollArea,
                             QHBoxLayout, QStyle, QStyledItemDelegate, QVBoxLayout, QWidget)

from .redmine import status_key
from .theme import STICKERS, TOKENS


COLUMN_TITLES = (
    'Не привязаны и не утверждены',
    'Привязаны и не утверждены',
    'Не привязаны и утверждены',
    'Привязаны и утверждены',
)


def is_approved(card):
    return status_key(card['status']) == 'ти утверждено'


class IdentificationCard(QStyledItemDelegate):
    def paint(self, painter, option, index):
        card = index.data(Qt.UserRole)
        box = option.rect.adjusted(2, 2, -2, -2)
        painter.save()
        painter.setRenderHint(painter.Antialiasing)
        focused = option.state & (QStyle.State_Selected | QStyle.State_HasFocus)
        painter.setPen(QColor(TOKENS['accent' if focused else 'border']))
        painter.setBrush(QColor(TOKENS['background']))
        painter.drawRoundedRect(box, 9, 9)
        outline = QPainterPath()
        outline.addRoundedRect(QRectF(box), 9, 9)
        painter.save()
        painter.setClipPath(outline, Qt.IntersectClip)
        stripe = QColor('#59a479' if is_approved(card) else TOKENS['danger'])
        painter.fillRect(QRect(box.x(), box.y(), box.width(), 4), stripe)
        painter.restore()
        left, top, width = box.x() + 12, box.y() + 8, box.width() - 24
        body_font = QFont(option.font)
        body_font.setPixelSize(12)
        body_font.setBold(False)
        heading_font = QFont(body_font)
        heading_font.setPixelSize(15)
        heading_font.setBold(True)
        body_metrics = QFontMetrics(body_font)

        def text(value, x, y, span, color, bold=False, align=Qt.AlignLeft):
            painter.setFont(heading_font if bold else body_font)
            painter.setPen(QColor(color))
            painter.drawText(QRect(x, y, span, 22), Qt.AlignVCenter | align,
                             painter.fontMetrics().elidedText(value, Qt.ElideRight, span))

        def badge(value, x, y, max_width, colors):
            foreground, background = colors
            span = min(max_width, body_metrics.horizontalAdvance(value) + 9)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(background))
            painter.drawRoundedRect(QRect(x, y, span, 20), 4, 4)
            text(value, x + 4, y - 1, span - 8, foreground, align=Qt.AlignHCenter)

        red, green = STICKERS[0], STICKERS[2]
        text(card['number'], left, top, width, TOKENS['text'], bold=True)
        text(card.get('subject', ''), left, top + 22, width, TOKENS['secondary'])
        top += 22
        text('Согласовано:', left, top + 29, 90, TOKENS['secondary'])
        approval = card['approval'] or '—'
        key = status_key(approval)
        colors = {'да': green, 'нет': red}.get(key, (TOKENS['secondary'], TOKENS['hover']))
        badge(approval, left + 90, top + 30, width - 90, colors)
        if card['links']:
            text(', '.join(card['links']), left, top + 59, width, TOKENS['text'])
        else:
            badge('Не привязано', left, top + 60, width, red)
        painter.restore()


class IdentificationPage(QWidget):
    def __init__(self, refresh):
        super().__init__()
        self.snapshot = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 12)
        controls = QHBoxLayout()
        self.message = QLabel()
        self.message.setWordWrap(True)
        self.message.setTextFormat(Qt.PlainText)
        controls.addWidget(self.message, 1)
        self.refresh_button = QPushButton('Обновить из Redmine')
        self.refresh_button.setEnabled(False)
        self.refresh_button.setCursor(Qt.PointingHandCursor)
        self.refresh_button.clicked.connect(refresh)
        controls.addWidget(self.refresh_button)
        layout.addLayout(controls)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        canvas = QWidget()
        canvas.setObjectName('BoardCanvas')
        columns = QHBoxLayout(canvas)
        columns.setContentsMargins(0, 0, 0, 0)
        columns.setSpacing(18)
        self.columns = []
        for title in COLUMN_TITLES:
            panel = QWidget()
            panel.setObjectName('ColumnPanel')
            panel.setAttribute(Qt.WA_StyledBackground)
            panel.setMinimumWidth(240)
            stack = QVBoxLayout(panel)
            stack.setContentsMargins(4, 4, 4, 4)
            stack.setSpacing(0)
            heading = QLabel(title)
            heading.setObjectName('ColumnTitle')
            heading.setWordWrap(True)
            heading.setMinimumHeight(64)
            stack.addWidget(heading)
            cards = QListWidget()
            cards.setObjectName('Cards')
            cards.setAccessibleName(title)
            cards.setResizeMode(QListView.Adjust)
            cards.setSpacing(10)
            cards.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            cards.setItemDelegate(IdentificationCard(cards))
            stack.addWidget(cards, 1)
            columns.addWidget(panel, 1)
            self.columns.append(cards)
        self.scroll.setWidget(canvas)
        layout.addWidget(self.scroll, 1)

    def show_cards(self, board_id, project, cards):
        snapshot = (board_id, project, cards)
        if snapshot == self.snapshot:
            return
        self.snapshot = snapshot
        for column in self.columns:
            column.clear()
        self.refresh_button.setEnabled(bool(project))
        if not project:
            message = 'Укажите проект поиска оснований в настройках доски.'
        elif cards is None:
            message = 'Загрузите карточки ИД из Redmine.'
        elif not cards:
            message = 'В проекте нет подходящих задач для ИД.'
        else:
            message = ''
        self.message.setText(message)
        for card in cards or ():
            column = self.columns[2 * is_approved(card) + bool(card['links'])]
            item = QListWidgetItem(column)
            item.setSizeHint(QSize(0, 129))
            item.setData(Qt.UserRole, card)
            lines = [card['number']]
            if card.get('subject'):
                lines.append(card['subject'])
            lines.extend(['Статус: ' + (card['status'] or '—'), 'Согласовано: ' + (card['approval'] or '—'),
                          ', '.join(card['links']) or 'Не привязано'])
            summary = '\n'.join(lines)
            item.setData(Qt.AccessibleTextRole, summary)
            item.setToolTip(summary)
