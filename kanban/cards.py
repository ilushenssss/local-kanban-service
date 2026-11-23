"""Compact task cards with semantic badges and optional inline details."""
import datetime as dt
from PyQt5.QtCore import Qt, QRect, QRectF, QSize, QPointF, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QFontMetrics, QPen, QTextDocument, QDrag
from PyQt5.QtWidgets import QStyledItemDelegate, QStyle, QApplication, QListWidget, QListWidgetItem
from .redmine import APPROVAL_COLORS, issue_label, kris_coding_lines, kris_progress_text, status_key
from .theme import TOKENS, STICKERS, avatar_color

EXPANDED_ROLE = Qt.UserRole + 1
TASK_NOTICE_ROLE = Qt.UserRole + 2
DETAILS_ROLE = Qt.UserRole + 3


DEPARTMENT_CHIPS_PER_ROW = 4
DEPARTMENT_PALETTE = {'red': STICKERS[0], 'yellow': STICKERS[3], 'green': STICKERS[2]}
BUG_CHIPS_PER_ROW = 4
CHIP_HEIGHT = 19
CHIP_GAP = 4
CHIP_PITCH = CHIP_HEIGHT + CHIP_GAP
CHIP_TEXT_INSET = 3
REFERENCE_LABEL_HEIGHT = 22
CODING_LINE_HEIGHT = 19


def chip_rows(values, per_row):
    return (len(values) + per_row - 1) // per_row


def chip_font(base_font):
    font = QFont(base_font)
    font.setPixelSize(11)
    return font


def basis_chip_layout(values, width, font):
    metrics = QFontMetrics(font)
    chips = []
    x = y = 0
    for value in values:
        text_width = metrics.horizontalAdvance(value) + 1
        span = min(width, text_width + 2 * CHIP_TEXT_INSET)
        if x and x + span > width:
            x = 0
            y += CHIP_PITCH
        chips.append((value, QRect(x, y, span, CHIP_HEIGHT), text_width))
        x += span + CHIP_GAP
    return chips, y + CHIP_PITCH if chips else 0


def redmine_title_font(base_font):
    font = QFont(base_font)
    font.setPixelSize(13)
    return font


def title_font(base_font):
    font = QFont(base_font)
    font.setPixelSize(15)
    font.setBold(True)
    return font


def title_extra_height(metrics):
    # Reserve two lines and leave six pixels before the metadata badges.
    return max(0, metrics.height() + metrics.lineSpacing() + 6 - 38)


def card_height(task, metrics):
    return 124 + title_extra_height(metrics) + (24 if task.get('labels') else 0) + (18 if task.get('board_name') else 0)


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
    def basis_layout(self, task, option):
        cards = self.parent()
        # Reserve the scrollbar width so the row count stays stable when it appears.
        width = (cards.maximumViewportSize().width() - cards.verticalScrollBar().sizeHint().width()
                 - 2 * cards.spacing() - 28)
        values = task['issue_references'].get('basis', [])
        return basis_chip_layout(values, max(2 * CHIP_TEXT_INSET + 1, width), chip_font(option.font))

    def sizeHint(self, option, index):
        task = index.data(Qt.UserRole) or {}
        if task.get('redmine_id'):
            rows = chip_rows(task['approvals'], DEPARTMENT_CHIPS_PER_ROW)
            approval_height = 19 + rows * CHIP_PITCH if rows else 0
            _, basis_height = self.basis_layout(task, option)
            bugs = task['issue_references'].get('bugs', [])
            reference_height = 2 * REFERENCE_LABEL_HEIGHT + basis_height + chip_rows(bugs, BUG_CHIPS_PER_ROW) * CHIP_PITCH
            title_height = 2 * QFontMetrics(redmine_title_font(option.font)).lineSpacing()
            coding_height = len(kris_coding_lines(task.get('kris_progress'))) * CODING_LINE_HEIGHT
            return QSize(292, 94 + title_height + reference_height + approval_height + coding_height)
        height = card_height(task, QFontMetrics(title_font(option.font)))
        if index.data(EXPANDED_ROLE):
            width = max(220, self.parent().viewport().width() - 32)
            height += min(260, int(details_document(index.data(DETAILS_ROLE) or task, width).size().height()) + 24)
        return QSize(292, height)

    @staticmethod
    def disclosure_rect(rect, height):
        return QRect(rect.right()-30, rect.y()+height-32, 26, 26)

    def paint(self, p, option, index):
        t = index.data(Qt.UserRole)
        if not t:
            return super().paint(p, option, index)
        if t.get('redmine_id'):
            self.paint_redmine(p, option, t)
            return
        p.save()
        p.setRenderHint(p.Antialiasing)
        box = option.rect.adjusted(2, 2, -2, -5)
        selected = bool(option.state & QStyle.State_Selected)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(20, 25, 40, 16))
        p.drawRoundedRect(box.translated(0, 1), 8, 8)
        notices = index.data(TASK_NOTICE_ROLE) or 0
        border = 'accent' if selected or option.state & QStyle.State_HasFocus else 'danger' if notices else 'border'
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
        heading = title_font(option.font)
        heading.setStrikeOut(done)
        p.setFont(heading)
        metrics = p.fontMetrics()
        height = card_height(t, metrics)
        p.setPen(QPen(QColor(TOKENS['muted']), 1))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QRectF(x, y+3, 12, 12))
        if done:
            p.drawPolyline(QPointF(x+3, y+9), QPointF(x+5, y+11), QPointF(x+10, y+6))
        p.setPen(QColor(TOKENS['muted'] if done else TOKENS['text']))
        title_width = w - 24 - (34 if notices else 0)
        words = t['title'].split(); lines = []
        while words and len(lines) < 2:
            line = words.pop(0)
            if len(lines) == 1:
                line = ' '.join([line]+words); words = []
            else:
                while words and metrics.horizontalAdvance(line+' '+words[0]) < title_width:
                    line += ' '+words.pop(0)
            lines.append(metrics.elidedText(line, Qt.ElideRight, title_width))
        for i, line in enumerate(lines):
            p.drawText(QRect(x+20, y+i*metrics.lineSpacing(), title_width, metrics.height()), Qt.AlignTop, line)
        if notices:
            badge_rect = QRect(x+w-30, y, 30, 18)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(TOKENS['danger']))
            p.drawRoundedRect(badge_rect, 9, 9)
            p.setPen(QColor(TOKENS['background']))
            font(10, True)
            p.drawText(badge_rect, Qt.AlignCenter, '99+' if notices > 99 else str(notices))
        y += title_extra_height(metrics)
        font(11)
        cursor = x+20
        names = t.get('assignee_names') or t.get('assignees', [])
        badge_right = x+w

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
        fg, bg = {'Низкий': STICKERS[2], 'Высокий': STICKERS[3], 'Срочный': STICKERS[0]}.get(
            priority, (TOKENS['secondary'], TOKENS['hover']))
        due = t.get('due_date')
        if due:
            date = dt.date.fromisoformat(due)
            month = ('янв','фев','мар','апр','май','июн','июл','авг','сен','окт','ноя','дек')[date.month-1]
            late = due < dt.date.today().isoformat() and not done
            badge('{} {}'.format(date.day,month), *(STICKERS[0] if late else (TOKENS['secondary'],TOKENS['hover'])), kind='date')
        else:
            badge('Без срока', TOKENS['secondary'], TOKENS['hover'], kind='date')
        badge(priority, fg, bg, 'priority')
        # Participant initials remain readable when no photo exists in the model.
        for i,name in enumerate(names[:2]):
            r = QRect(x+20+i*22, y+61, 18, 18)
            p.setPen(Qt.NoPen);p.setBrush(QColor(avatar_color(name)));p.drawEllipse(r)
            p.setPen(Qt.white);font(9,True)
            initials = ('+' + str(len(names)-1)) if i == 1 and len(names) > 2 else ''.join(part[0] for part in name.split()[:2]).upper()
            p.drawText(r,Qt.AlignCenter,initials)
        font(11)
        name_x = x+20+min(2, len(names))*22
        name_width = x+w-name_x
        p.setPen(QColor(TOKENS['secondary']))
        p.drawText(QRect(name_x, y+61, name_width, 18), Qt.AlignVCenter,
                   p.fontMetrics().elidedText(', '.join(names) or 'Без исполнителя', Qt.ElideRight, name_width))
        font(10)
        if t.get('labels'):
            cursor=x+20
            for label in t['labels']:
                width=min(110,p.fontMetrics().horizontalAdvance(label)+16,x+w-cursor)
                if width<24:break
                r=QRect(cursor,y+83,width,18)
                p.setPen(Qt.NoPen);p.setBrush(QColor(TOKENS['hover']));p.drawRoundedRect(r,4,4)
                p.setPen(QColor(TOKENS['secondary']))
                p.drawText(r.adjusted(6,0,-6,0),Qt.AlignVCenter,p.fontMetrics().elidedText(label,Qt.ElideRight,width-12))
                cursor+=width+4
        if t.get('board_name'):
            p.setPen(QColor(TOKENS['secondary']))
            board_y = y + (105 if t.get('labels') else 81)
            p.drawText(QRect(x+20, board_y, w-20, 17), Qt.AlignVCenter,
                       p.fontMetrics().elidedText(t['board_name'], Qt.ElideRight, w-20))
        footer=option.rect.y()+height-25
        total, checked = t.get('check_total', 0), t.get('check_done', 0)
        unread = t.get('unread_count', 0)
        status = '{}/{}'.format(checked,total) if total else ''
        if unread:
            status += (' · ' if status else '') + '{} новых'.format(unread)
        status_width = 100 if unread else 50
        track=QRectF(x,footer+8,max(0,w-status_width-36),4)
        if total:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(TOKENS['border']))
            p.drawRoundedRect(track, 2, 2)
            if checked:
                p.setBrush(QColor(TOKENS['accent']))
                p.drawRoundedRect(QRectF(track.x(), track.y(), track.width()*checked/total, 4), 2, 2)
        p.setPen(QColor(TOKENS['muted']));font(10)
        p.drawText(QRect(x+w-status_width-30,footer,status_width,20),Qt.AlignCenter,status)
        arrow=self.disclosure_rect(option.rect,height);expanded=bool(index.data(EXPANDED_ROLE))
        p.setPen(QPen(QColor(TOKENS['muted']),1.5));p.setBrush(Qt.NoBrush)
        cx,cy=arrow.center().x(),arrow.center().y()
        p.drawPolyline(QPointF(cx-4,cy+(2 if expanded else -2)),QPointF(cx,cy+(-2 if expanded else 2)),QPointF(cx+4,cy+(2 if expanded else -2)))
        if expanded:
            top=option.rect.y()+height
            p.setPen(QPen(QColor(TOKENS['border']),1));p.drawLine(x,top-4,x+w,top-4)
            details = index.data(DETAILS_ROLE)
            doc = details_document(details or dict(t, description='Загрузка подробностей…'), w)
            doc.setDefaultStyleSheet('body { color: '+TOKENS['text']+'; }')
            p.translate(x,top+4)
            context=doc.documentLayout().PaintContext();context.palette.setColor(context.palette.Text,QColor(TOKENS['text']))
            p.setClipRect(QRectF(0,0,w,box.bottom()-top-8));doc.documentLayout().draw(p,context)
        p.restore()


    def paint_redmine(self, painter, option, task):
        painter.save()
        painter.setRenderHint(painter.Antialiasing)
        card = option.rect.adjusted(2, 2, -2, -5)
        active = option.state & (QStyle.State_Selected | QStyle.State_HasFocus)
        painter.setPen(QColor(TOKENS['accent' if active else 'border']))
        painter.setBrush(QColor(TOKENS['background']))
        painter.drawRoundedRect(card, 9, 9)
        painter.setClipRect(card)
        x, y, width = card.left()+12, card.top()+9, card.width()-24

        def text(caption, left, top, span, height=19, bold=False, color='muted', align=Qt.AlignLeft,
                 elide=Qt.ElideRight):
            font = QFont(option.font)
            font.setPixelSize(12 if not bold else 15)
            font.setBold(bold)
            painter.setFont(font)
            painter.setPen(QColor(TOKENS[color]))
            painter.drawText(QRect(left, top, span, height), align | Qt.AlignVCenter,
                             painter.fontMetrics().elidedText(caption, elide, span))

        def chip(caption, rect, palette=None, text_width=None):
            foreground, background = palette or (TOKENS['secondary'], TOKENS['hover'])
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(background))
            painter.drawRoundedRect(rect, 4, 4)
            painter.setFont(chip_font(option.font))
            painter.setPen(QColor(foreground))
            inset = CHIP_TEXT_INSET
            label_rect = rect.adjusted(inset, 0, -inset, 0)
            if text_width is None:
                caption = painter.fontMetrics().elidedText(caption, Qt.ElideRight, label_rect.width())
            elif text_width > label_rect.width():
                # Oversized references remain on one line at the shared chip height.
                painter.save()
                painter.translate(label_rect.topLeft())
                painter.scale(label_rect.width() / text_width, 1)
                painter.drawText(QRect(0, 0, text_width, label_rect.height()), Qt.AlignCenter | Qt.TextSingleLine, caption)
                painter.restore()
                return
            painter.drawText(label_rect, Qt.AlignCenter | Qt.TextSingleLine, caption)

        progress = task.get('kris_progress')
        label = issue_label(task['tracker_name'], task['redmine_id'], progress)
        text(label, x, y, width-68, bold=True, color='text')
        chip('Redmine', QRect(x+width-60, y, 60, CHIP_HEIGHT))
        painter.setFont(redmine_title_font(option.font))
        painter.setPen(QColor(TOKENS['secondary']))
        title_rect = QRect(x, y+23, width, 2*painter.fontMetrics().lineSpacing()+2)
        painter.drawText(title_rect, Qt.AlignTop | Qt.TextWordWrap, ' '.join(task['title'].split()))
        y = title_rect.bottom()+6
        basis_chips, basis_height = self.basis_layout(task, option)
        text('СП' if basis_chips else 'СП —', x, y, width)
        y += REFERENCE_LABEL_HEIGHT
        for value, rect, text_width in basis_chips:
            chip(value, rect.translated(x, y), text_width=text_width)
        y += basis_height
        bugs = task['issue_references'].get('bugs', [])
        text('Баги' if bugs else 'Баги —', x, y, width)
        y += REFERENCE_LABEL_HEIGHT
        span = (width - CHIP_GAP * (BUG_CHIPS_PER_ROW - 1)) // BUG_CHIPS_PER_ROW
        for index, value in enumerate(bugs):
            row, column = divmod(index, BUG_CHIPS_PER_ROW)
            chip(value, QRect(x+column*(span+CHIP_GAP), y+row*CHIP_PITCH, span, CHIP_HEIGHT))
        y += chip_rows(bugs, BUG_CHIPS_PER_ROW) * CHIP_PITCH
        text('КРИС ТИ', x, y, width-106)
        text(kris_progress_text(progress), x+width-100, y, 100, align=Qt.AlignRight)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(TOKENS['hover']))
        painter.drawRoundedRect(QRectF(x, y+19, width, 4), 2, 2)
        if progress and progress['total'] and progress['done']:
            painter.setBrush(QColor(TOKENS['accent']))
            fill = width * progress['done'] / progress['total']
            painter.drawRoundedRect(QRectF(x, y+19, fill, 4), 2, 2)
        footer = y+25
        for line in kris_coding_lines(progress):
            text(line, x, footer, width, height=CODING_LINE_HEIGHT, elide=Qt.ElideMiddle)
            footer += CODING_LINE_HEIGHT
        if task['approvals']:
            text('Согласование отделов', x, footer, width)
            footer += 19
            span = (width - 4*(DEPARTMENT_CHIPS_PER_ROW-1)) // DEPARTMENT_CHIPS_PER_ROW
            for index, approval in enumerate(task['approvals']):
                row, column = divmod(index, DEPARTMENT_CHIPS_PER_ROW)
                palette = DEPARTMENT_PALETTE[APPROVAL_COLORS[status_key(approval['status'])]]
                chip(approval['department'], QRect(x+column*(span+4), footer+row*CHIP_PITCH, span, CHIP_HEIGHT),
                     palette=palette)
            footer += chip_rows(task['approvals'], DEPARTMENT_CHIPS_PER_ROW)*CHIP_PITCH
        text('Прил. —', x, footer, width-20)
        text('—', x+width-16, footer, 16)
        painter.restore()


class Cards(QListWidget):
    moved = pyqtSignal(int, int, object)
    dragging = pyqtSignal(bool)
    details_requested = pyqtSignal(object)
    def __init__(self, column_id, expanded_ids=None):
        super().__init__()
        self.column_id = column_id
        self.expanded_ids = expanded_ids if expanded_ids is not None else set()
        self.setObjectName('Cards')
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setItemDelegate(CardDelegate(self))
        self.setMouseTracking(True)
        self.setDragDropMode(QListWidget.DragDrop)
        self.setDefaultDropAction(Qt.MoveAction)
        self.setSelectionMode(QListWidget.SingleSelection)
        self.setSpacing(4)
        self.setWordWrap(True)
        self.setMinimumWidth(220)
        self.setAccessibleName('Задачи колонки')
        self.setFixedHeight(40)
    def update_card_height(self):
        option = self.viewOptions()
        height = sum(self.itemDelegate().sizeHint(option, self.model().index(i, 0)).height() + self.spacing()*2
                     for i in range(self.count())) + 8
        self.setFixedHeight(min(650, max(40, height)))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.count() and event.size().width() != event.oldSize().width():
            self.doItemsLayout()
            self.update_card_height()

    def toggle_details(self, item):
        if item.data(Qt.UserRole).get('redmine_id'):
            self.itemClicked.emit(item)
            return
        expanded = not item.data(EXPANDED_ROLE)
        item.setData(EXPANDED_ROLE, expanded)
        item.setData(Qt.AccessibleDescriptionRole, 'Подробности раскрыты. Пробел — свернуть.' if expanded else 'Пробел — раскрыть подробности.')
        tid = item.data(Qt.UserRole)['id']
        if expanded:
            self.expanded_ids.add(tid)
            if item.data(DETAILS_ROLE) is None:
                self.details_requested.emit(item)
        else:
            self.expanded_ids.discard(tid)
            details = item.data(DETAILS_ROLE)
            if details and details.get('details_error'):
                item.setData(DETAILS_ROLE, None)
        self.doItemsLayout()
        self.update_card_height()
        self.viewport().update()

    def mouseReleaseEvent(self, event):
        item = self.itemAt(event.pos())
        if item and event.button() == Qt.LeftButton:
            task = item.data(Qt.UserRole)
            metrics = QFontMetrics(title_font(self.viewOptions().font))
            if not task.get('redmine_id') and CardDelegate.disclosure_rect(self.visualItemRect(item), card_height(task, metrics)).contains(event.pos()):
                self.toggle_details(item)
                event.accept()
                return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if self.currentItem() and event.key() == Qt.Key_Space:
            self.toggle_details(self.currentItem())
            event.accept()
            return
        if self.currentItem() and event.key() in (Qt.Key_Return, Qt.Key_Enter):
            self.itemClicked.emit(self.currentItem())
            event.accept()
            return
        super().keyPressEvent(event)

    def startDrag(self, supported_actions):
        if not self.selectedIndexes() or self.currentItem().data(Qt.UserRole).get('redmine_id'):
            return
        # Own QDrag lifecycle: QListWidget.startDrag may delete source rows after
        # MoveAction, although persistence and repaint are handled asynchronously.
        self.dragging.emit(True)
        try:
            drag = QDrag(self)
            drag.setMimeData(self.model().mimeData(self.selectedIndexes()))
            drag.exec_(Qt.MoveAction, Qt.MoveAction)
        finally:
            self.dragging.emit(False)

    def dropEvent(self, event):
        source = event.source()
        if not isinstance(source, Cards) or not source.currentItem():
            event.ignore()
            return
        task = source.currentItem().data(Qt.UserRole)
        index = self.indexAt(event.pos()).row()
        before_id = self.item(index).data(Qt.UserRole)['id'] if index >= 0 else None
        self.moved.emit(task['id'], self.column_id, before_id)
        event.acceptProposedAction()




def set_card_notice(item, count):
    item.setData(TASK_NOTICE_ROLE, count)
    summary = item.text()
    if count:
        summary += '\nНепрочитанных уведомлений: {}'.format(count)
    item.setData(Qt.AccessibleTextRole, summary)
    item.setToolTip(summary + '\nПробел — подробности, Enter — открыть задачу')


def set_card_data(item, task, count):
    item.setData(Qt.UserRole, task)
    if task.get('redmine_id'):
        progress = task.get('kris_progress')
        summary = '{} #{} · Redmine\n{}\n{}'.format(
            issue_label(task['tracker_name'], task['redmine_id'], progress), task['redmine_id'], task['title'], task['status'])
        for label, key in (('СП', 'basis'), ('Баги', 'bugs')):
            values = task['issue_references'].get(key, [])
            summary += '\n{}: {}'.format(label, ', '.join(values)) if values else '\n' + label + ' —'
        summary += '\nКРИС ТИ: {}\n'.format(kris_progress_text(progress))
        summary += '\n'.join([*kris_coding_lines(progress), 'Приложения —'])
        if task['approvals']:
            summary += '\nСогласование отделов: ' + ', '.join(
                '{}: {}'.format(entry['department'], entry['status']) for entry in task['approvals'])
        item.setText(summary)
        set_card_notice(item, count)
        return
    names = ', '.join(task['assignee_names']) or 'Без исполнителя'
    due = task['due_date']
    overdue = due and due < dt.date.today().isoformat() and not task['completed_at']
    summary = '№{} {}\n{}\n{}: {} · {}'.format(
        task['number'], task['title'], names, 'Просрочено' if overdue else 'Срок',
        due or 'не задан', task['priority'])
    if task['labels']:
        summary += '\n' + ', '.join(task['labels'])
    if task.get('board_name'):
        summary += '\n' + task['board_name']
    item.setText(summary)
    set_card_notice(item, count)


def update_cards(lists, tasks_by_column, counts, expanded_ids):
    """Reconcile rows by task ID, reusing the same item even when it moves."""
    wanted = {task['id']: key for key, tasks in tasks_by_column.items() for task in tasks}
    items, positions = {}, {}
    dirty = set()
    selected = focused = None
    for key, cards in lists.items():
        positions[key] = cards.verticalScrollBar().value()
        current = cards.currentItem()
        if current and current.isSelected():
            selected = current.data(Qt.UserRole)['id']
            if cards.hasFocus():
                focused = selected
        cards.blockSignals(True)
        for row in reversed(range(cards.count())):
            item = cards.item(row)
            tid = item.data(Qt.UserRole)['id']
            if wanted.get(tid) != key:
                item = cards.takeItem(row)
                dirty.add(key)
            if tid in wanted:
                items[tid] = item

    for key, cards in lists.items():
        for row, task in enumerate(tasks_by_column.get(key, ())):
            tid = task['id']
            item = items.get(tid)
            if item is None:
                item = QListWidgetItem()
                item.setData(EXPANDED_ROLE, tid in expanded_ids)
                item.setData(Qt.AccessibleDescriptionRole, 'Пробел — подробности, Enter — открыть задачу')
            old = item.data(Qt.UserRole)
            if old != task:
                dirty.add(key)
                if old and old['version'] != task['version']:
                    item.setData(DETAILS_ROLE, None)
                set_card_data(item, task, counts.get(tid, 0))
            if cards.row(item) != row:
                dirty.add(key)
                if item.listWidget() is cards:
                    cards.takeItem(cards.row(item))
                cards.insertItem(row, item)
            if item.data(EXPANDED_ROLE) and item.data(DETAILS_ROLE) is None:
                # Signals are blocked during reconciliation.
                cards.blockSignals(False)
                cards.details_requested.emit(item)
                cards.blockSignals(True)
            if tid == selected and not item.isSelected():
                cards.setCurrentItem(item)
                if tid == focused:
                    cards.setFocus()
        if selected is not None and wanted.get(selected) != key:
            cards.clearSelection()
        cards.blockSignals(False)
        if key in dirty:
            cards.doItemsLayout()
            cards.update_card_height()
            cards.verticalScrollBar().setValue(positions[key])
