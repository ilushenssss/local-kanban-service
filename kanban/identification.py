"""Read-only ИД cards from the board's Redmine basis project."""
from collections import defaultdict, deque
from math import ceil

from PyQt5.QtCore import QRect, QRectF, QSize, Qt, pyqtSignal
from PyQt5.QtGui import (QAbstractTextDocumentLayout, QColor, QFont, QFontMetrics, QPainterPath,
                         QPalette, QTextDocument, QTextOption)
from PyQt5.QtWidgets import (QLabel, QListView, QListWidget, QListWidgetItem, QPushButton, QScrollArea,
                             QHBoxLayout, QStyle, QStyledItemDelegate, QVBoxLayout, QWidget)

from .redmine import status_key
from .theme import STICKERS, TOKENS, card_refresh_rect, paint_card_refresh


COLUMN_TITLES = (
    'Не привязаны и не утверждены',
    'Привязаны и не утверждены',
    'Не привязаны и утверждены',
    'Привязаны и утверждены',
)


def is_approved(card):
    return status_key(card['status']) == 'ти утверждено'


class ProjectCardDelegate(QStyledItemDelegate):
    @staticmethod
    def body_font(option):
        font = QFont(option.font)
        font.setPixelSize(12)
        font.setBold(False)
        return font

    def heading_font(self, option):
        font = self.body_font(option)
        font.setPixelSize(15)
        font.setBold(True)
        return font

    def body_document(self, option, lines, width):
        document = QTextDocument()
        document.setDocumentMargin(0)
        document.setDefaultFont(self.body_font(option))
        text_option = QTextOption()
        text_option.setWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
        document.setDefaultTextOption(text_option)
        document.setPlainText('\n'.join(lines))
        document.setTextWidth(width)
        return document

    @staticmethod
    def paint_badge_background(painter, rect, color, border=None):
        painter.setPen(QColor(border) if border else Qt.NoPen)
        painter.setBrush(QColor(color))
        painter.drawRoundedRect(rect, 4, 4)

    @staticmethod
    def paint_surface(painter, option, positive):
        box = option.rect.adjusted(2, 2, -2, -2)
        painter.setRenderHint(painter.Antialiasing)
        focused = option.state & (QStyle.State_Selected | QStyle.State_HasFocus)
        painter.setPen(QColor(TOKENS['accent' if focused else 'border']))
        painter.setBrush(QColor(TOKENS['background']))
        painter.drawRoundedRect(box, 9, 9)
        outline = QPainterPath()
        outline.addRoundedRect(QRectF(box), 9, 9)
        painter.save()
        painter.setClipPath(outline, Qt.IntersectClip)
        stripe = QColor('#59a479' if positive else TOKENS['danger'])
        painter.fillRect(QRect(box.x(), box.y(), box.width(), 4), stripe)
        painter.restore()
        return box


class IdentificationCard(ProjectCardDelegate):
    minimum_height = 129
    links_top = 89

    def sizeHint(self, option, index):
        links = (index.data(Qt.UserRole) or {}).get('links')
        height = self.minimum_height
        if links:
            view = self.parent()
            width = max(1, view.viewport().width() - 2 * view.spacing() - 28)
            document = self.body_document(option, links, width)
            height = max(height, self.links_top + ceil(document.size().height()) + 12)
        return QSize(0, height)

    def paint(self, painter, option, index):
        card = index.data(Qt.UserRole)
        painter.save()
        box = self.paint_surface(painter, option, is_approved(card))
        left, top, width = box.x() + 12, box.y() + 8, box.width() - 24
        body_font = self.body_font(option)
        heading_font = self.heading_font(option)
        body_metrics = QFontMetrics(body_font)

        def text(value, x, y, span, color, bold=False, align=Qt.AlignLeft):
            painter.setFont(heading_font if bold else body_font)
            painter.setPen(QColor(color))
            painter.drawText(QRect(x, y, span, 22), Qt.AlignVCenter | align,
                             painter.fontMetrics().elidedText(value, Qt.ElideRight, span))

        def badge(value, x, y, max_width, colors):
            foreground, background = colors
            span = min(max_width, body_metrics.horizontalAdvance(value) + 9)
            self.paint_badge_background(painter, QRect(x, y, span, 20), background)
            text(value, x + 4, y - 1, span - 8, foreground, align=Qt.AlignHCenter)

        red, green = STICKERS[0], STICKERS[2]
        text(card['number'], left, top, width - 28, TOKENS['text'], bold=True)
        paint_card_refresh(painter, card_refresh_rect(option.rect))
        text(card.get('subject', ''), left, top + 22, width, TOKENS['secondary'])
        top += 22
        text('Согласовано:', left, top + 29, 90, TOKENS['secondary'])
        approval = card['approval'] or '—'
        key = status_key(approval)
        colors = {'да': green, 'нет': red}.get(key, (TOKENS['secondary'], TOKENS['hover']))
        badge(approval, left + 90, top + 30, width - 90, colors)
        if card['links']:
            document = self.body_document(option, card['links'], width)
            context = QAbstractTextDocumentLayout.PaintContext()
            context.palette.setColor(QPalette.Text, QColor(TOKENS['text']))
            painter.translate(left, option.rect.y() + self.links_top)
            document.documentLayout().draw(painter, context)
        else:
            badge('Не привязано', left, top + 60, width, red)
        painter.restore()


class IdentificationCards(QListWidget):
    refresh_requested = pyqtSignal(object)

    def mouseReleaseEvent(self, event):
        item = self.itemAt(event.pos())
        if item and event.button() == Qt.LeftButton and card_refresh_rect(self.visualItemRect(item)).contains(event.pos()):
            self.refresh_requested.emit(item.data(Qt.UserRole))
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        item = self.currentItem()
        if item and event.key() == Qt.Key_R and event.modifiers() == Qt.ControlModifier:
            self.refresh_requested.emit(item.data(Qt.UserRole))
            event.accept()
            return
        super().keyPressEvent(event)


def project_card_column(title, delegate, minimum_width=240, list_type=QListWidget):
    panel = QWidget()
    panel.setObjectName('ColumnPanel')
    panel.setAttribute(Qt.WA_StyledBackground)
    panel.setMinimumWidth(minimum_width)
    stack = QVBoxLayout(panel)
    stack.setContentsMargins(4, 4, 4, 4)
    stack.setSpacing(0)
    heading = QLabel(title)
    heading.setObjectName('ColumnTitle')
    heading.setWordWrap(True)
    heading.setMinimumHeight(64)
    stack.addWidget(heading)
    cards = list_type()
    cards.setObjectName('Cards')
    cards.setAccessibleName(title)
    cards.setResizeMode(QListView.Adjust)
    cards.setSpacing(10)
    cards.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    cards.setItemDelegate(delegate(cards))
    stack.addWidget(cards, 1)
    return panel, cards


class IdentificationPage(QWidget):
    def __init__(self, refresh, refresh_card):
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
            panel, cards = project_card_column(title, IdentificationCard, list_type=IdentificationCards)
            cards.refresh_requested.connect(refresh_card)
            columns.addWidget(panel, 1)
            self.columns.append(cards)
        self.scroll.setWidget(canvas)
        layout.addWidget(self.scroll, 1)

    def show_cards(self, board_id, project, cards):
        snapshot = (board_id, project, cards)
        if snapshot == self.snapshot:
            return
        self.snapshot = snapshot
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
        groups = [[] for _ in self.columns]
        for card in cards or ():
            groups[2 * is_approved(card) + bool(card['links'])].append(card)
        for column, group in zip(self.columns, groups):
            self.update_column(column, group)

    @staticmethod
    def update_column(column, cards):
        def key(card):
            return card.get('issue_id') or (card['number'], card.get('subject'))

        existing = defaultdict(deque)
        for row in range(column.count()):
            item = column.item(row)
            existing[key(item.data(Qt.UserRole))].append(item)
        for row, card in enumerate(cards):
            matches = existing[key(card)]
            item = matches.popleft() if matches else QListWidgetItem()
            old_row = column.row(item)
            if old_row != row:
                if old_row >= 0:
                    column.takeItem(old_row)
                column.insertItem(row, item)
            if item.data(Qt.UserRole) != card:
                item.setData(Qt.UserRole, card)
                lines = [card['number']]
                if card.get('subject'):
                    lines.append(card['subject'])
                lines.extend(['Статус: ' + (card['status'] or '—'), 'Согласовано: ' + (card['approval'] or '—')])
                lines.extend(card['links'] or ['Не привязано'])
                summary = '\n'.join(lines)
                hint = 'Ctrl+R — обновить карточку'
                item.setData(Qt.AccessibleTextRole, summary)
                item.setData(Qt.AccessibleDescriptionRole, hint)
                item.setToolTip(summary + '\n' + hint)
        for items in existing.values():
            for item in items:
                column.takeItem(column.row(item))
