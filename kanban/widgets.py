"""Shared native Qt form controls; no network or external resources."""
from PyQt5.QtCore import Qt, QDate, QLocale, pyqtSignal, QEvent, QPointF, QSize
from PyQt5.QtWidgets import (QWidget, QComboBox, QHBoxLayout, QVBoxLayout, QCheckBox,
    QDateEdit, QCalendarWidget, QListWidget, QListWidgetItem, QLineEdit, QPushButton, QLabel, QTextEdit, QRadioButton, QButtonGroup, QToolButton, QSizePolicy)

from .spellcheck import SpellTextEdit, SpellLineEdit, SpellItemDelegate


class MessageEdit(SpellTextEdit):
    """Shared chat keyboard behavior; leave ordinary form editors unchanged."""
    sendRequested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._composing = False
        self.setAcceptRichText(False)
        self.setToolTip('Enter — отправить, Shift+Enter — новая строка')
        self.setAccessibleDescription(self.toolTip())

    def inputMethodEvent(self, event):
        self._composing = bool(event.preeditString())
        super().inputMethodEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            if self._composing:
                event.accept()
                return
            if event.modifiers() in (Qt.NoModifier, Qt.KeypadModifier):
                event.accept()
                if not event.isAutoRepeat() and self.toPlainText().strip():
                    self.sendRequested.emit()
                return
        super().keyPressEvent(event)


class RegistrySelection(QWidget):
    """Native combo adds values; a separate native list shows/removes selections."""
    selectionChanged = pyqtSignal()

    def __init__(self, placeholder, selected=(), parent=None, colored=False):
        super().__init__(parent)
        self.placeholder = placeholder
        self.colored = colored
        self.options = None
        self.chosen = set(selected)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.combo = QComboBox()
        self.combo.setAccessibleName(placeholder)
        self.combo.activated[int].connect(self.add_current)
        layout.addWidget(self.combo)
        self.selected_row = QWidget()
        row = QHBoxLayout(self.selected_row)
        row.setContentsMargins(0, 0, 0, 0)
        self.list = QListWidget()
        self.list.setAccessibleName('Выбранные значения: ' + placeholder)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list.setTextElideMode(Qt.ElideRight)
        self.list.setWordWrap(False)
        self.list.setUniformItemSizes(True)
        self.list.setMinimumWidth(0)
        self.list.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.list.setMinimumHeight(42)
        self.list.setMaximumHeight(96)
        self.remove_button = QPushButton('Убрать')
        self.remove_button.setAutoDefault(False)
        self.remove_button.clicked.connect(self.remove_current)
        self.list.itemSelectionChanged.connect(lambda: self.remove_button.setEnabled(bool(self.list.selectedItems())))
        row.addWidget(self.list, 1)
        row.addWidget(self.remove_button)
        layout.addWidget(self.selected_row)
        self.set_options([], selected)

    def values(self):
        return sorted(self.chosen)

    def set_values(self, values):
        self.chosen = set(values)
        self.render()

    def set_options(self, options, selected=None):
        chosen = self.chosen if selected is None else set(selected)
        options = list(options)
        known = {value for value, label in options}
        options.extend((value, value) for value in sorted(chosen - known))
        if self.options == options and self.chosen == chosen:
            return
        self.options = options
        self.chosen = set(chosen)
        self.render()

    def render(self):
        # Block only combo signals; model row insertion/removal signals remain intact.
        self.combo.blockSignals(True)
        self.combo.clear()
        available = [(value, label) for value, label in self.options if value not in self.chosen]
        hint = self.placeholder if available else ('Все значения выбраны' if self.options else 'Список пуст')
        self.combo.addItem(hint, None)
        for value, label in self.options:
            if value not in self.chosen:
                self.combo.addItem(label, value)
        self.combo.setCurrentIndex(0)
        self.combo.blockSignals(False)
        selected = self.list.currentItem()
        current = selected.data(Qt.UserRole) if selected else None
        self.list.clear()
        for value, label in self.options:
            if value in self.chosen:
                item = QListWidgetItem(label)
                item.setData(Qt.UserRole, value)
                item.setToolTip(label)
                item.setSizeHint(QSize(0, max(28, self.list.fontMetrics().height() + 8)))
                self.list.addItem(item)
                if value == current:
                    self.list.setCurrentItem(item)
        row_height = max(28, self.list.fontMetrics().height() + 8)
        self.list.setFixedHeight(min(3, max(1, self.list.count())) * row_height + 18)
        self.updateGeometry()
        self.selected_row.setMinimumHeight(self.list.height())
        self.selected_row.setVisible(bool(self.chosen))
        self.setMinimumHeight(self.combo.sizeHint().height() + (6 + self.list.height() if self.chosen else 0))
        self.remove_button.setEnabled(bool(self.list.selectedItems()))
        self.selectionChanged.emit()

    def add_current(self, index):
        value = self.combo.itemData(index)
        if value is not None:
            self.chosen.add(value)
            self.render()

    def remove_current(self):
        item = self.list.currentItem()
        if item and item.isSelected():
            self.chosen.discard(item.data(Qt.UserRole))
            self.render()


class DeadlineCalendar(QCalendarWidget):
    def __init__(self, editor):
        super().__init__()
        self.editor = editor
        self.setLocale(QLocale('ru_RU'))
        self.setFirstDayOfWeek(Qt.Monday)
        self.setObjectName('DeadlineCalendar')
        self.setGridVisible(False)
        self.setVerticalHeaderFormat(QCalendarWidget.NoVerticalHeader)
        self.setHorizontalHeaderFormat(QCalendarWidget.ShortDayNames)
        self.setMinimumSize(308, 300)
        self.refresh_palette()

    def refresh_palette(self):
        from PyQt5.QtGui import QTextCharFormat, QColor
        from .theme import TOKENS, symbol_icon
        header = QTextCharFormat()
        header.setForeground(QColor(TOKENS['muted']))
        header.setBackground(QColor(TOKENS['background']))
        self.setHeaderTextFormat(header)
        for day in (Qt.Saturday, Qt.Sunday):
            weekend = QTextCharFormat()
            weekend.setForeground(QColor(TOKENS['secondary']))
            self.setWeekdayTextFormat(day, weekend)
        for obj, icon, label in (
            ('qt_calendar_prevmonth', 'calendar-prev', 'Предыдущий месяц'),
            ('qt_calendar_nextmonth', 'calendar-next', 'Следующий месяц'),
        ):
            control = self.findChild(QToolButton, obj)
            if control:
                control.setIcon(symbol_icon(icon))
                control.setIconSize(QSize(18, 18))
                control.setCursor(Qt.PointingHandCursor)
                control.setToolTip(label)
                control.setAccessibleName(label)
        self.updateCells()

    def paintCell(self, painter, rect, date):
        from PyQt5.QtGui import QColor, QPen
        from .theme import TOKENS
        painter.save()
        painter.setRenderHint(painter.Antialiasing)
        painter.fillRect(rect, QColor(TOKENS['background']))
        side = min(34, rect.width() - 4, rect.height() - 4)
        cell = rect.adjusted((rect.width()-side)//2, (rect.height()-side)//2,
                             -(rect.width()-side+1)//2, -(rect.height()-side+1)//2)
        selected = date == self.selectedDate()
        today = date == QDate.currentDate()
        available = self.minimumDate() <= date <= self.maximumDate()
        painter.setPen(Qt.NoPen)
        if selected:
            painter.setBrush(QColor(TOKENS['accent']))
            painter.drawRoundedRect(cell, 8, 8)
        elif today:
            painter.setPen(QPen(QColor(TOKENS['accent']), 1))
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(cell, 8, 8)
        color = TOKENS['on_accent'] if selected else TOKENS['text']
        if not available or date.month() != self.monthShown():
            color = TOKENS['muted'] if not selected else TOKENS['on_accent']
        painter.setPen(QColor(color))
        font = self.font()
        font.setPixelSize(13)
        font.setBold(selected or today)
        painter.setFont(font)
        painter.drawText(rect, Qt.AlignCenter, str(date.day()))
        painter.restore()

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh_palette()
        if self.editor.date() == self.editor.minimumDate():
            today = QDate.currentDate()
            blocked = self.blockSignals(True)
            self.setSelectedDate(today)
            self.setCurrentPage(today.year(), today.month())
            self.blockSignals(blocked)


class ClickDateEdit(QDateEdit):
    """Every click opens Qt's calendar; clicking digits never edits the year."""
    def __init__(self, date):
        super().__init__(date)
        self.lineEdit().setReadOnly(True)
        self.lineEdit().installEventFilter(self)
        self.setCursor(Qt.PointingHandCursor)

    def open_calendar(self):
        from PyQt5.QtGui import QMouseEvent
        point = QPointF(self.width() - 12, self.height() / 2)
        event = QMouseEvent(QEvent.MouseButtonPress, point, Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
        super().mousePressEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.open_calendar()
            event.accept()
        else:
            super().mousePressEvent(event)

    def eventFilter(self, source, event):
        if source is self.lineEdit() and event.type() == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
            self.open_calendar()
            return True
        return super().eventFilter(source, event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Space, Qt.Key_Return, Qt.Key_Enter, Qt.Key_Down):
            self.open_calendar()
            event.accept()
        elif event.key() in (Qt.Key_Tab, Qt.Key_Backtab, Qt.Key_Escape):
            super().keyPressEvent(event)
        else:
            event.accept()

    def wheelEvent(self, event):
        event.ignore()


class DateField(QWidget):
    changed = pyqtSignal()

    def __init__(self, value=None, caption=None, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        if caption:
            layout.addWidget(QLabel(caption))
        self.editor = ClickDateEdit(QDate.currentDate())
        self.editor.setCalendarPopup(True)
        self.editor.setDisplayFormat('dd.MM.yyyy')
        self.editor.setFixedWidth(160)
        self.editor.setMinimumDate(QDate(1, 1, 1))
        self.editor.setMaximumDate(QDate(9999, 12, 31))
        self.editor.setSpecialValueText('Без срока')
        self.editor.setLocale(QLocale('ru_RU'))
        self.editor.setCalendarWidget(DeadlineCalendar(self.editor))
        self.editor.setAccessibleName(caption or 'Срок выполнения')
        layout.addWidget(self.editor)
        self.clear_button = QPushButton('Сбросить')
        self.clear_button.setObjectName('link')
        self.clear_button.setAutoDefault(False)
        self.clear_button.clicked.connect(lambda: self.set_value(None))
        layout.addWidget(self.clear_button)
        layout.addStretch(1)
        self.editor.dateChanged.connect(self.changed)
        self.set_value(value)

    def set_value(self, value):
        date = QDate.fromString(value or '', Qt.ISODate)
        self.editor.setDate(date if date.isValid() else self.editor.minimumDate())

    def value(self):
        return None if self.editor.date() == self.editor.minimumDate() else self.editor.date().toString(Qt.ISODate)

    def text(self):
        return self.value() or ''


class ChecklistEditor(QWidget):
    def __init__(self, values=(), parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        header = QHBoxLayout()
        self.progress = QLabel()
        self.progress.setObjectName('Muted')
        header.addWidget(self.progress, 1)
        self.remove_button = QPushButton('Убрать пункт')
        self.remove_button.setProperty('variant', 'quiet')
        self.remove_button.clicked.connect(self.remove_selected)
        header.addWidget(self.remove_button)
        layout.addLayout(header)
        self.list = QListWidget()
        self.list.setObjectName('Checklist')
        self.list.setItemDelegate(SpellItemDelegate(self.list))
        self.list.setAccessibleName('Пункты чек-листа')
        self.list.setMinimumHeight(96)
        self.list.setMaximumHeight(190)
        layout.addWidget(self.list)
        add = QHBoxLayout()
        self.entry = SpellLineEdit()
        self.entry.setPlaceholderText('Добавить пункт чек-листа…')
        self.entry.setAccessibleName('Новый пункт чек-листа')
        self.add_button = QPushButton('Добавить')
        self.add_button.setAutoDefault(False)
        self.add_button.clicked.connect(self.add_entry)
        self.entry.returnPressed.connect(self.add_entry)
        add.addWidget(self.entry, 1)
        add.addWidget(self.add_button)
        layout.addLayout(add)
        self.list.itemChanged.connect(self.update_progress)
        self.list.itemSelectionChanged.connect(self.update_progress)
        for item in values:
            self.add_item(item['text'], item['done'])
        self.update_progress()

    def add_item(self, text, done=False):
        item = QListWidgetItem(text.strip())
        item.setFlags(item.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsEditable)
        item.setCheckState(Qt.Checked if done else Qt.Unchecked)
        self.list.addItem(item)
        self.update_progress()

    def add_entry(self):
        text = self.entry.text().strip()
        if text:
            self.add_item(text)
            self.entry.clear()
        self.entry.setFocus()

    def remove_selected(self):
        row = self.list.currentRow()
        if row >= 0:
            self.list.takeItem(row)
            self.update_progress()

    def update_progress(self, *args):
        items = self.values()
        done = sum(i['done'] for i in items)
        self.progress.setText('Выполнено {} из {}'.format(done, len(items)) if items else 'Пока нет пунктов')
        self.remove_button.setEnabled(bool(self.list.selectedItems()))

    def values(self):
        return [{'text': self.list.item(i).text().strip(), 'done': int(self.list.item(i).checkState() == Qt.Checked)} for i in range(self.list.count()) if self.list.item(i).text().strip()]

    def has_draft(self):
        return bool(self.entry.text().strip())


class PrioritySelector(QWidget):
    """One priority at a time, using keyboard-accessible native radio buttons."""
    def __init__(self, parent=None):
        super().__init__(parent)
        from .theme import PRIORITIES
        self.group = QButtonGroup(self)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.buttons = {}
        for name in PRIORITIES:
            radio = QRadioButton(name)
            radio.setCursor(Qt.PointingHandCursor)
            self.group.addButton(radio)
            self.buttons[name] = radio
            layout.addWidget(radio)
        self.setCurrentText('Обычный')
        self.setAccessibleName('Приоритет задачи')

    def setCurrentText(self, text):
        if text in self.buttons:
            self.buttons[text].setChecked(True)

    def currentText(self):
        selected = self.group.checkedButton()
        return selected.text() if selected else 'Обычный'


class PriorityCombo(QComboBox):
    """Compact priority filter for the board; task forms use PrioritySelector."""
    def __init__(self, parent=None):
        super().__init__(parent)
        from .theme import PRIORITIES
        self.addItems(list(PRIORITIES))
        self.setAccessibleName('Фильтр по приоритету')
