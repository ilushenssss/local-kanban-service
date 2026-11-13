"""Shared native Qt form controls; no network or external resources."""
from PyQt5.QtCore import Qt, QDate, QLocale, QEvent, pyqtSignal
from PyQt5.QtGui import QStandardItem, QStandardItemModel, QIcon
from PyQt5.QtWidgets import (QWidget, QComboBox, QHBoxLayout, QVBoxLayout, QCheckBox,
    QDateEdit, QCalendarWidget, QListWidget, QListWidgetItem, QLineEdit, QPushButton, QLabel, QToolButton)


class CheckComboBox(QComboBox):
    """Multi-select dropdown. Space/Enter toggles; Escape closes the popup."""
    selectionChanged = pyqtSignal()

    def __init__(self, placeholder, selected=(), parent=None):
        super().__init__(parent)
        self.placeholder = placeholder
        self.setEditable(True)
        self.lineEdit().setReadOnly(True)
        self.lineEdit().setPlaceholderText(placeholder)
        self.setModel(QStandardItemModel(self))
        self.setMaxVisibleItems(12)
        self.view().setMinimumWidth(280)
        self.view().viewport().installEventFilter(self)
        self.view().installEventFilter(self)
        self.model().itemChanged.connect(self.update_summary)
        self.currentIndexChanged.connect(self.update_summary)
        self.set_options([], selected)

    def set_options(self, options, selected=None):
        chosen = set(self.values() if selected is None else selected)
        options = list(options)
        known = {value for value, label in options}
        options.extend((value, value) for value in sorted(chosen - known))
        current = [(self.itemData(i, Qt.UserRole), self.itemText(i)) for i in range(self.count())]
        if current == options and chosen == set(self.values()):
            self.update_summary()
            return
        self.model().blockSignals(True)
        self.clear()
        for value, label in options:
            item = QStandardItem(label)
            item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable | Qt.ItemIsSelectable)
            item.setData(value, Qt.UserRole)
            item.setData(Qt.Checked if value in chosen else Qt.Unchecked, Qt.CheckStateRole)
            self.model().appendRow(item)
        self.model().blockSignals(False)
        self.update_summary()

    def values(self):
        return sorted(self.itemData(i, Qt.UserRole) for i in range(self.count()) if self.itemData(i, Qt.CheckStateRole) == Qt.Checked)

    def set_values(self, values):
        values = set(values)
        for i in range(self.count()):
            self.model().item(i).setCheckState(Qt.Checked if self.itemData(i, Qt.UserRole) in values else Qt.Unchecked)
        self.update_summary()

    def update_summary(self, *args):
        names = [self.itemText(i) for i in range(self.count()) if self.itemData(i, Qt.CheckStateRole) == Qt.Checked]
        self.lineEdit().setText(', '.join(names))
        self.setToolTip(', '.join(names) or self.placeholder)
        self.selectionChanged.emit()

    def toggle_index(self, index):
        if index.isValid():
            item = self.model().item(index.row())
            item.setCheckState(Qt.Unchecked if item.checkState() == Qt.Checked else Qt.Checked)

    def eventFilter(self, obj, event):
        if obj is self.view().viewport() and event.type() == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton:
            self.toggle_index(self.view().indexAt(event.pos()))
            return True
        if obj is self.view() and event.type() == QEvent.KeyPress and event.key() in (Qt.Key_Space, Qt.Key_Return, Qt.Key_Enter):
            self.toggle_index(self.view().currentIndex())
            return True
        return super().eventFilter(obj, event)

    def hidePopup(self):
        super().hidePopup()
        self.update_summary()

    def wheelEvent(self, event):
        event.ignore()


class DateField(QWidget):
    changed = pyqtSignal()

    def __init__(self, value=None, caption='Указать срок', parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.enabled = QCheckBox(caption)
        self.editor = QDateEdit()
        self.editor.setCalendarPopup(True)
        self.editor.setDisplayFormat('dd.MM.yyyy')
        self.editor.setMinimumDate(QDate(1, 1, 1))
        self.editor.setMaximumDate(QDate(9999, 12, 31))
        self.editor.setLocale(QLocale('ru_RU'))
        calendar = QCalendarWidget()
        calendar.setLocale(QLocale('ru_RU'))
        calendar.setFirstDayOfWeek(Qt.Monday)
        calendar.setVerticalHeaderFormat(QCalendarWidget.NoVerticalHeader)
        for name, arrow in [('qt_calendar_prevmonth', Qt.LeftArrow), ('qt_calendar_nextmonth', Qt.RightArrow)]:
            control = calendar.findChild(QToolButton, name)
            if control:
                control.setIcon(QIcon())
                control.setArrowType(arrow)
        self.editor.setCalendarWidget(calendar)
        self.editor.setAccessibleName(caption)
        layout.addWidget(self.enabled)
        layout.addWidget(self.editor, 1)
        self.enabled.toggled.connect(self.editor.setEnabled)
        self.enabled.toggled.connect(self.changed)
        self.editor.dateChanged.connect(self.changed)
        self.set_value(value)

    def set_value(self, value):
        date = QDate.fromString(value or '', Qt.ISODate)
        self.enabled.setChecked(date.isValid())
        self.editor.setDate(date if date.isValid() else QDate.currentDate())
        self.editor.setEnabled(date.isValid())

    def value(self):
        return self.editor.date().toString(Qt.ISODate) if self.enabled.isChecked() else None

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
