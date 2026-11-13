"""Task surfaces with a two-line title, stickers and a quiet metadata footer."""
import datetime as dt
from PyQt5.QtCore import Qt, QRect, QRectF, QSize
from PyQt5.QtGui import QColor, QFont, QPen
from PyQt5.QtWidgets import QStyledItemDelegate, QStyle
from .theme import TOKENS, PRIORITIES, label_colors


class CardDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index):
        task = index.data(Qt.UserRole) or {}
        return QSize(292, 148 if task.get('checklist') else 132)

    def paint(self, p, option, index):
        t = index.data(Qt.UserRole)
        if not t:
            return super().paint(p, option, index)
        p.save()
        p.setRenderHint(p.Antialiasing)
        box = option.rect.adjusted(2, 2, -2, -5)
        selected = bool(option.state & QStyle.State_Selected)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(15, 23, 42, 12))
        p.drawRoundedRect(box.translated(0, 2), 8, 8)
        p.setPen(QPen(QColor(TOKENS['accent'] if selected else TOKENS['border']), 1))
        p.setBrush(QColor(TOKENS['selection'] if selected else TOKENS['background']))
        p.drawRoundedRect(box, 8, 8)
        p.setClipRect(box)
        done = bool(t['completed_at'])
        if done:
            p.setOpacity(.5)
        x, y, w = box.x()+12, box.y()+11, box.width()-24
        def font(px=12, bold=False, strike=False):
            f = QFont(option.font)
            f.setPixelSize(px)
            f.setBold(bold)
            f.setStrikeOut(strike)
            p.setFont(f)
        font(14, strike=done)
        p.setPen(QColor(TOKENS['text']))
        words = t['title'].split()
        lines = []
        while words and len(lines) < 2:
            line = words.pop(0)
            if len(lines) == 1:
                line = ' '.join([line]+words)
                words = []
            else:
                while words and p.fontMetrics().horizontalAdvance(line+' '+words[0]) < w-8:
                    line += ' '+words.pop(0)
            lines.append(p.fontMetrics().elidedText(line, Qt.ElideRight, w-8))
        p.drawText(QRect(x, y, w, 38), Qt.AlignTop, '\n'.join(lines))
        font(12)
        cursor = x
        def chip(text, fg, bg, limit):
            nonlocal cursor
            width = min(limit, p.fontMetrics().horizontalAdvance(text)+20)
            r = QRect(cursor, y+45, width, 21)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(bg))
            p.drawRoundedRect(r, 5, 5)
            p.setPen(QColor(fg))
            p.drawText(r.adjusted(6, 0, -6, 0), Qt.AlignVCenter, p.fontMetrics().elidedText(text, Qt.ElideRight, width-12))
            cursor += width+5
        priority = t.get('priority', 'Обычный')
        fg, bg = PRIORITIES.get(priority, PRIORITIES['Обычный'])
        chip(priority, fg, bg, 94)
        labels = t.get('labels', [])
        for name in labels[:2]:
            room = x+w-cursor
            if room < 40:
                break
            fg, bg = label_colors(name)
            chip(name, fg, bg, min(112, room))
        footer = y+79
        font(12)
        p.setPen(QColor(TOKENS['muted']))
        checks = t.get('checklist', [])
        checked = sum(bool(c['done']) for c in checks)
        if checks:
            p.drawText(QRect(x, footer, 58, 22), Qt.AlignVCenter, '✓ {}/{}'.format(checked, len(checks)))
        unread = t.get('unread_count', 0)
        if unread:
            cx = x+(64 if checks else 0)
            p.setPen(QColor(TOKENS['accent']))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(QRect(cx, footer+5, 14, 10), 3, 3)
            p.drawLine(cx+3, footer+15, cx+3, footer+18)
            p.drawText(QRect(cx+19, footer, 30, 22), Qt.AlignVCenter, str(unread))
        due = t.get('due_date')
        if due:
            date = dt.date.fromisoformat(due)
            late = due < dt.date.today().isoformat() and not done
            p.setPen(QColor(TOKENS['danger'] if late else TOKENS['muted']))
            text = ('! ' if late else '')+date.strftime('%d.%m')
            p.drawText(QRect(box.right()-113, footer, 70, 22), Qt.AlignRight | Qt.AlignVCenter, text)
        names = t.get('assignee_names') or t.get('assignees', [])
        if names:
            avatar = QRect(box.right()-35, footer, 24, 24)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(TOKENS['selection']))
            p.drawEllipse(avatar)
            p.setPen(QColor(TOKENS['accent']))
            font(10, True)
            initials = ''.join(part[0] for part in names[0].split()[:2]).upper()
            p.drawText(avatar, Qt.AlignCenter, initials)
        if checks:
            track = QRectF(x, y+115, w, 4)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(TOKENS['hover']))
            p.drawRoundedRect(track, 2, 2)
            if checked:
                p.setBrush(QColor('#78b895' if done else '#a8b3c5'))
                p.drawRoundedRect(QRectF(x, track.y(), w*checked/len(checks), 4), 2, 2)
        p.restore()
