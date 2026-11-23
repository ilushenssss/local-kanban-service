"""Read-only ИД cards from the board's Redmine basis project."""
from PyQt5.QtCore import QRect, QSize, Qt
from PyQt5.QtGui import QColor, QFont
from PyQt5.QtWidgets import (QLabel, QListView, QListWidget, QListWidgetItem, QPushButton,
                             QHBoxLayout, QStyle, QStyledItemDelegate, QVBoxLayout, QWidget)

from .redmine import basis_number, status_key
from .theme import STICKERS, TOKENS


class IdentificationCard(QStyledItemDelegate):
    def paint(self, painter, option, index):
        card = index.data(Qt.UserRole)
        box = option.rect.adjusted(2, 2, -13, -13)
        painter.save()
        painter.setRenderHint(painter.Antialiasing)
        focused = option.state & (QStyle.State_Selected | QStyle.State_HasFocus)
        painter.setPen(QColor(TOKENS['accent' if focused else 'border']))
        painter.setBrush(QColor(TOKENS['background']))
        painter.drawRoundedRect(box, 9, 9)
        left, top, width = box.x() + 12, box.y() + 8, box.width() - 24

        def text(value, x, y, span, color, bold=False, align=Qt.AlignLeft):
            font = QFont(option.font)
            font.setPixelSize(15 if bold else 12)
            font.setBold(bold)
            painter.setFont(font)
            painter.setPen(QColor(color))
            painter.drawText(QRect(x, y, span, 22), Qt.AlignVCenter | align,
                             painter.fontMetrics().elidedText(value, Qt.ElideRight, span))

        def badge(value, x, y, span, colors, height=24):
            foreground, background = colors
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(background))
            painter.drawRoundedRect(QRect(x, y, span, height), 4, 4)
            text(value, x + 3, y + (height - 22) // 2, span - 6, foreground, align=Qt.AlignHCenter)

        red, green = STICKERS[0], STICKERS[2]
        offset = 0
        if basis_number(card['number']):
            approved = status_key(card['status']) == 'ти утверждено'
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor('#59a479' if approved else TOKENS['danger']))
            painter.drawEllipse(QRect(left, top + 7, 8, 8))
            offset = 16
        text(card['number'], left + offset, top, width - offset, TOKENS['text'], bold=True)
        text(card.get('subject', ''), left, top + 22, width, TOKENS['secondary'])
        top += 22
        text('Согласовано:', left, top + 29, 90, TOKENS['secondary'])
        approval = card['approval'] or '—'
        key = status_key(approval)
        colors = {'да': green, 'нет': red}.get(key, (TOKENS['secondary'], TOKENS['hover']))
        if key in ('да', 'нет'):
            badge(approval, left + 90, top + 24, 32, colors, height=32)
        else:
            badge(approval, left + 90, top + 28, width - 90, colors)
        if card['links']:
            text(', '.join(card['links']), left, top + 59, width, TOKENS['text'])
        else:
            badge('Не привязано', left, top + 59, 105, red)
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
        self.cards = QListWidget()
        self.cards.setObjectName('Cards')
        self.cards.setAccessibleName('Карточки ИД')
        self.cards.setViewMode(QListView.IconMode)
        self.cards.setMovement(QListView.Static)
        self.cards.setResizeMode(QListView.Adjust)
        self.cards.setGridSize(QSize(276, 138))
        self.cards.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.cards.setItemDelegate(IdentificationCard(self.cards))
        layout.addWidget(self.cards, 1)

    def show_cards(self, board_id, project, cards):
        snapshot = (board_id, project, cards)
        if snapshot == self.snapshot:
            return
        self.snapshot = snapshot
        self.cards.clear()
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
            item = QListWidgetItem(self.cards)
            item.setSizeHint(self.cards.gridSize())
            item.setData(Qt.UserRole, card)
            lines = [card['number']]
            if card.get('subject'):
                lines.append(card['subject'])
            lines.extend(['Статус: ' + (card['status'] or '—'), 'Согласовано: ' + (card['approval'] or '—'),
                          ', '.join(card['links']) or 'Не привязано'])
            summary = '\n'.join(lines)
            item.setData(Qt.AccessibleTextRole, summary)
            item.setToolTip(summary)
