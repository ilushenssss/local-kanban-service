"""Offline Qt charts. Values remain available as text and in statistics tables."""
from PyQt5.QtCore import Qt, QRectF
from PyQt5.QtGui import QColor, QPainter, QPen, QFont
from PyQt5.QtWidgets import QWidget, QSizePolicy
from .theme import TOKENS

COLORS = ['#5789da', '#a17bd0', '#dfad45', '#d78470', '#57a882']


class Chart(QWidget):
    def __init__(self, title, ring=False):
        super().__init__()
        self.title, self.ring = title, ring
        self.rows = []
        self.center = ''
        self.setMinimumWidth(260)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setAccessibleName(title)
        self.set_rows([])

    def set_rows(self, rows, center=''):
        # row: label, [(value, color)], textual value. Segments never overlap.
        self.rows, self.center = rows, center
        height = max(240, 82 + len(rows) * 58) if not self.ring else 270
        self.setFixedHeight(height)
        text = '\n'.join('{}: {}'.format(label, value) for label, segments, value in rows)
        self.setAccessibleDescription(text or 'Пока нет задач')
        self.setToolTip(text)
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        p.setPen(QPen(QColor(TOKENS['border']), 1))
        p.setBrush(QColor(TOKENS['background']))
        p.drawRoundedRect(rect, 12, 12)
        base = QFont(self.font())
        def text(box, value, size=10, bold=False, color='text', align=Qt.AlignLeft | Qt.AlignVCenter):
            font = QFont(base)
            font.setPointSizeF(size)
            font.setBold(bold)
            p.setFont(font)
            p.setPen(QColor(TOKENS.get(color, color)))
            p.drawText(QRectF(box), align, value)
        text(QRectF(20, 15, self.width()-40, 32), self.title, 12, True)
        total = sum(value for _, parts, _ in self.rows for value, _ in parts)
        if not total:
            text(QRectF(20, 80, self.width()-40, 90), 'Пока нет задач\nПоказатели появятся после добавления задач.', color='muted', align=Qt.AlignCenter)
            return
        if self.ring:
            diameter = min(160, self.width() * .36)
            ring = QRectF(32, 72, diameter, diameter)
            start = 90 * 16
            for _, parts, _ in self.rows:
                for value, color in parts:
                    if not value:
                        continue
                    span = round(360 * 16 * value / total)
                    p.setPen(QPen(QColor(color), 18))
                    p.setBrush(Qt.NoBrush)
                    p.drawArc(ring, start, -span)
                    start -= span
            text(ring.adjusted(12, 25, -12, -40), self.center, 27, True, align=Qt.AlignCenter)
            text(ring.adjusted(8, 85, -8, -12), 'завершено', 9, color='muted', align=Qt.AlignCenter)
            left = diameter + 68
            for index, (label, parts, value) in enumerate(self.rows):
                y = 82 + index * 51
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(parts[0][1]))
                p.drawEllipse(QRectF(left, y + 9, 9, 9))
                text(QRectF(left+18, y, self.width()-left-38, 25), label, 9, color='muted')
                text(QRectF(left+18, y+21, self.width()-left-38, 25), value, 12, True)
        else:
            maximum = max(sum(v for v, _ in parts) for _, parts, _ in self.rows)
            for index, (label, parts, value) in enumerate(self.rows):
                y = 66 + index * 58
                font = QFont(base)
                font.setPointSizeF(10)
                p.setFont(font)
                label = p.fontMetrics().elidedText(label, Qt.ElideRight, max(50, self.width()-210))
                text(QRectF(20, y-4, self.width()-40, 24), label)
                text(QRectF(self.width()-190, y-4, 170, 24), value, 9, color='muted', align=Qt.AlignRight | Qt.AlignVCenter)
                track = QRectF(20, y+25, self.width()-40, 12)
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(TOKENS['hover']))
                p.drawRoundedRect(track, 5, 5)
                x = track.x()
                for amount, color in parts:
                    width = track.width() * amount / maximum
                    if width:
                        p.setBrush(QColor(color))
                        p.drawRoundedRect(QRectF(x, track.y(), width, 12), min(4, width/2), 4)
                        x += width
        p.end()
