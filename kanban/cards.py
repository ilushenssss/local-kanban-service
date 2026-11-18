"""Compact task cards with semantic badges and optional inline details."""
import datetime as dt
from PyQt5.QtCore import Qt, QRect, QRectF, QSize, QPointF
from PyQt5.QtGui import QColor, QFont, QPen, QTextDocument
from PyQt5.QtWidgets import QStyledItemDelegate, QStyle, QApplication
from .theme import TOKENS, STICKERS, avatar_color

EXPANDED_ROLE = Qt.UserRole + 1
TASK_NOTICE_ROLE = Qt.UserRole + 2


def card_height(task):
    return 122 if task.get('labels') else 98


def details_document(task, width):
    document = QTextDocument()
    font = QFont(QApplication.font())
    font.setPixelSize(12)
    document.setDefaultFont(font)
    document.setDocumentMargin(0)
    document.setTextWidth(max(100, width))
    lines = [task.get('description') or 'Описание не добавлено']
    names = task.get('assignee_names') or task.get('assignees', [])
    lines.append('Участники: ' + (', '.join(names) or 'не назначены'))
    checks = task.get('checklist', [])
    if checks:
        lines.append('Чек-лист:')
        lines.extend(('Готово: ' if c['done'] else 'Осталось: ') + c['text'] for c in checks)
    document.setPlainText('\n\n'.join(lines))
    return document


class CardDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index):
        task = index.data(Qt.UserRole) or {}
        height = card_height(task)
        if index.data(EXPANDED_ROLE):
            width = max(220, self.parent().viewport().width() - 32)
            height += min(260, int(details_document(task, width).size().height()) + 24)
        return QSize(292, height)

    @staticmethod
    def disclosure_rect(rect, task):
        return QRect(rect.right()-30, rect.y()+card_height(task)-32, 26, 26)

    def paint(self, p, option, index):
        t = index.data(Qt.UserRole)
        if not t:
            return super().paint(p, option, index)
        p.save()
        p.setRenderHint(p.Antialiasing)
        box = option.rect.adjusted(2, 2, -2, -5)
        selected = bool(option.state & QStyle.State_Selected)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(20, 25, 40, 16))
        p.drawRoundedRect(box.translated(0, 1), 8, 8)
        notices = index.data(TASK_NOTICE_ROLE) or 0
        border = 'accent' if selected else 'danger' if notices else 'border'
        p.setPen(QPen(QColor(TOKENS[border]), 2 if notices else 1))
        p.setBrush(QColor(TOKENS['background']))
        p.drawRoundedRect(box, 8, 8)
        p.setClipRect(box)
        x, y, w = box.x()+10, box.y()+8, box.width()-20

        def font(px=12, bold=False):
            f = QFont(option.font)
            f.setPixelSize(px)
            f.setBold(bold)
            p.setFont(f)

        done = bool(t.get('completed_at'))
        font(13)
        p.setPen(QPen(QColor(TOKENS['muted']), 1))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QRectF(x, y+3, 12, 12))
        if done:
            p.drawPolyline(QPointF(x+3, y+9), QPointF(x+5, y+11), QPointF(x+10, y+6))
        p.setPen(QColor(TOKENS['muted'] if done else TOKENS['text']))
        f = p.font(); f.setStrikeOut(done); p.setFont(f)
        title_width = w - 24 - (34 if notices else 0)
        words = t['title'].split(); lines = []
        while words and len(lines) < 2:
            line = words.pop(0)
            if len(lines) == 1:
                line = ' '.join([line]+words); words = []
            else:
                while words and p.fontMetrics().horizontalAdvance(line+' '+words[0]) < title_width:
                    line += ' '+words.pop(0)
            lines.append(p.fontMetrics().elidedText(line, Qt.ElideRight, title_width))
        p.drawText(QRect(x+20, y, w-20, 34), Qt.AlignTop, '\n'.join(lines))
        if notices:
            badge_rect = QRect(x+w-30, y, 30, 18)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(TOKENS['danger']))
            p.drawRoundedRect(badge_rect, 9, 9)
            p.setPen(QColor(TOKENS['background']))
            font(10, True)
            p.drawText(badge_rect, Qt.AlignCenter, '99+' if notices > 99 else str(notices))
        font(10)
        cursor = x+20
        names = t.get('assignee_names') or t.get('assignees', [])
        badge_right = x+w-(min(2, len(names))*22+4 if names else 0)

        def badge(text, fg, bg, kind=None):
            nonlocal cursor
            icon_width = 15 if kind else 0
            width = min(p.fontMetrics().horizontalAdvance(text)+12+icon_width, badge_right-cursor)
            if width < 34:
                return
            r = QRect(cursor, y+38, width, 20)
            p.setPen(Qt.NoPen); p.setBrush(QColor(bg)); p.drawRoundedRect(r, 4, 4)
            if kind:
                p.setPen(QPen(QColor(fg), 1)); p.setBrush(QColor(fg))
                if kind == 'priority':
                    for i in range(3):
                        p.drawRoundedRect(QRectF(cursor+5+i*3, y+51-i*3, 2, 4+i*3), 1, 1)
                else:
                    p.setBrush(Qt.NoBrush)
                    p.drawRoundedRect(QRectF(cursor+5, y+43, 9, 9), 2, 2)
                    p.drawLine(cursor+5, y+46, cursor+14, y+46)
            p.setPen(QColor(fg))
            p.drawText(r.adjusted(6+icon_width, 0, -5, 0), Qt.AlignVCenter,
                       p.fontMetrics().elidedText(text, Qt.ElideRight, width-11-icon_width))
            cursor += width+5

        priority = t.get('priority', 'Обычный')
        if priority != 'Обычный':
            fg,bg = {'Низкий':STICKERS[2], 'Высокий':STICKERS[3], 'Срочный':STICKERS[0]}[priority]
            badge({'Низкий':'Не срочно','Высокий':'Важно','Срочный':'Очень важно'}[priority], fg,bg,'priority')
        due = t.get('due_date')
        if due:
            date = dt.date.fromisoformat(due)
            month = ('янв','фев','мар','апр','май','июн','июл','авг','сен','окт','ноя','дек')[date.month-1]
            late = due < dt.date.today().isoformat() and not done
            badge('{} {}'.format(date.day,month), *(STICKERS[0] if late else (TOKENS['secondary'],TOKENS['hover'])), kind='date')
        # Participant initials remain readable when no photo exists in the model.
        for i,name in enumerate(names[:2]):
            r = QRect(x+w-(min(2,len(names))-i)*22, y+37, 20, 20)
            p.setPen(Qt.NoPen);p.setBrush(QColor(avatar_color(name)));p.drawEllipse(r)
            p.setPen(Qt.white);font(9,True)
            initials = ('+' + str(len(names)-1)) if i == 1 and len(names) > 2 else ''.join(part[0] for part in name.split()[:2]).upper()
            p.drawText(r,Qt.AlignCenter,initials)
        font(10)
        if t.get('labels'):
            cursor=x+20
            for label in t['labels']:
                width=min(110,p.fontMetrics().horizontalAdvance(label)+16,x+w-cursor)
                if width<24:break
                r=QRect(cursor,y+63,width,18)
                p.setPen(Qt.NoPen);p.setBrush(QColor(TOKENS['hover']));p.drawRoundedRect(r,4,4)
                p.setPen(QColor(TOKENS['secondary']))
                p.drawText(r.adjusted(6,0,-6,0),Qt.AlignVCenter,p.fontMetrics().elidedText(label,Qt.ElideRight,width-12))
                cursor+=width+4
        footer=option.rect.y()+card_height(t)-25
        checks=t.get('checklist',[]);checked=sum(bool(c['done']) for c in checks)
        unread = t.get('unread_count', 0)
        status = '{}/{}'.format(checked,len(checks)) if checks else ''
        if unread:
            status += (' · ' if status else '') + '{} новых'.format(unread)
        status_width = 100 if unread else 50
        track=QRectF(x,footer+8,max(40,w-status_width-36),4)
        p.setPen(Qt.NoPen);p.setBrush(QColor(TOKENS['border']));p.drawRoundedRect(track,2,2)
        if checks and checked:
            p.setBrush(QColor(TOKENS['accent']));p.drawRoundedRect(QRectF(track.x(),track.y(),track.width()*checked/len(checks),4),2,2)
        p.setPen(QColor(TOKENS['muted']));font(10)
        p.drawText(QRect(x+w-status_width-30,footer,status_width,20),Qt.AlignCenter,status)
        arrow=self.disclosure_rect(option.rect,t);expanded=bool(index.data(EXPANDED_ROLE))
        p.setPen(QPen(QColor(TOKENS['muted']),1.5));p.setBrush(Qt.NoBrush)
        cx,cy=arrow.center().x(),arrow.center().y()
        p.drawPolyline(QPointF(cx-4,cy+(2 if expanded else -2)),QPointF(cx,cy+(-2 if expanded else 2)),QPointF(cx+4,cy+(2 if expanded else -2)))
        if expanded:
            top=option.rect.y()+card_height(t)
            p.setPen(QPen(QColor(TOKENS['border']),1));p.drawLine(x,top-4,x+w,top-4)
            doc=details_document(t,w)
            doc.setDefaultStyleSheet('body { color: '+TOKENS['text']+'; }')
            p.translate(x,top+4)
            context=doc.documentLayout().PaintContext();context.palette.setColor(context.palette.Text,QColor(TOKENS['text']))
            p.setClipRect(QRectF(0,0,w,box.bottom()-top-8));doc.documentLayout().draw(p,context)
        p.restore()
