"""Native tree cells with a compact unread count; board names remain unchanged."""
from PyQt5.QtCore import Qt, QRectF
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import QStyledItemDelegate, QStyleOptionViewItem, QStyle, QApplication
from .theme import TOKENS


class BoardBadgeDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        if index.column() != 1:
            return super().paint(painter, option, index)
        value = index.data(Qt.DisplayRole)
        cell = QStyleOptionViewItem(option)
        self.initStyleOption(cell, index)
        cell.text = ''
        style = cell.widget.style() if cell.widget else QApplication.style()
        style.drawControl(QStyle.CE_ItemViewItem, cell, painter, cell.widget)
        if not value:
            return
        text = str(value)
        painter.save()
        painter.setRenderHint(painter.Antialiasing)
        font = painter.font()
        font.setPixelSize(11)
        font.setBold(True)
        painter.setFont(font)
        width = max(22, painter.fontMetrics().horizontalAdvance(text) + 12)
        rect = QRectF(option.rect.center().x() - width / 2, option.rect.center().y() - 10, width, 20)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(TOKENS['danger']))
        painter.drawRoundedRect(rect, 10, 10)
        painter.setPen(QColor(TOKENS['background']))
        painter.drawText(rect, Qt.AlignCenter, text)
        painter.restore()
