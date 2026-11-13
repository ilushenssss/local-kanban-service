"""Shared native Qt form controls; no network or external resources."""
from PyQt5.QtCore import Qt, QDate, QLocale, pyqtSignal
from PyQt5.QtWidgets import (QWidget, QComboBox, QHBoxLayout, QVBoxLayout, QCheckBox,
    QDateEdit, QCalendarWidget, QListWidget, QListWidgetItem, QLineEdit, QPushButton, QLabel)
from .theme import label_icon


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
        hint = self.placeholder if available else ('Все значения выбраны' if self.options else 'Список пуст — откройте справочник')
        self.combo.addItem(hint, None)
        for value, label in self.options:
            if value not in self.chosen:
                self.combo.addItem(label_icon(str(value)), label, value) if self.colored else self.combo.addItem(label, value)
        self.combo.setCurrentIndex(0)
        self.combo.blockSignals(False)
        selected = self.list.currentItem()
        current = selected.data(Qt.UserRole) if selected else None
        self.list.clear()
        for value, label in self.options:
            if value in self.chosen:
                item = QListWidgetItem(label)
                if self.colored:
                    item.setIcon(label_icon(str(value)))
                item.setData(Qt.UserRole, value)
                self.list.addItem(item)
                if value == current:
                    self.list.setCurrentItem(item)
        self.list.setFixedHeight(min(96, max(42, self.list.count() * 27 + 12)))
        self.selected_row.setVisible(bool(self.chosen))
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

    def showEvent(self, event):
        super().showEvent(event)
        if self.editor.date() == self.editor.minimumDate():
            today = QDate.currentDate()
            blocked = self.blockSignals(True)
            self.setSelectedDate(today)
            self.setCurrentPage(today.year(), today.month())
            self.blockSignals(blocked)


class DateField(QWidget):
    changed = pyqtSignal()

    def __init__(self, value=None, caption=None, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        if caption:
            layout.addWidget(QLabel(caption))
        self.editor = QDateEdit(QDate.currentDate())
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
        self.list.setAccessibleName('Пункты чек-листа')
        self.list.setMinimumHeight(96)
        self.list.setMaximumHeight(190)
        layout.addWidget(self.list)
        add = QHBoxLayout()
        self.entry = QLineEdit()
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


class PriorityCombo(QComboBox):
    def __init__(self, parent=None):
        super().__init__(parent)
        from PyQt5.QtGui import QPainter, QPixmap, QIcon, QColor
        from .theme import PRIORITIES
        for name, colors in PRIORITIES.items():
            pixmap = QPixmap(16, 16)
            pixmap.fill(Qt.transparent)
            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.Antialiasing)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(colors[0]))
            painter.drawEllipse(3, 3, 10, 10)
            painter.end()
            self.addItem(QIcon(pixmap), name)
        self.setAccessibleName('Приоритет задачи')
