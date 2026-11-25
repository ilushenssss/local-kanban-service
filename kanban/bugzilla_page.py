import json

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QFormLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget
from .bugzilla import xml_value_to_json


def field_text(value):
    if value is None or value == '':
        return '—'
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=xml_value_to_json)


def field_label(value):
    label = QLabel(field_text(value))
    label.setTextFormat(Qt.PlainText)
    label.setWordWrap(True)
    label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
    label.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
    return label


class BugzillaPage(QWidget):
    def __init__(self, export, export_all):
        super().__init__()
        self.snapshot = None
        self.linked = frozenset()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 12)
        actions = QHBoxLayout()
        self.export_button = QPushButton('Выгрузить баг по ID…')
        self.export_button.setCursor(Qt.PointingHandCursor)
        self.export_button.clicked.connect(export)
        actions.addWidget(self.export_button)
        self.export_all_button = QPushButton('Выгрузить по фильтрам')
        self.export_all_button.setCursor(Qt.PointingHandCursor)
        self.export_all_button.clicked.connect(export_all)
        actions.addWidget(self.export_all_button)
        actions.addStretch()
        layout.addLayout(actions)
        self.progress = QLabel('Выгрузка из Bugzilla…')
        self.progress.setObjectName('Muted')
        self.progress.hide()
        layout.addWidget(self.progress)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        layout.addWidget(self.scroll, 1)
        self.show_cards(())

    def set_loading(self, loading):
        for button in (self.export_button, self.export_all_button):
            button.setEnabled(not loading)
        self.progress.setVisible(loading)

    def show_cards(self, cards, linked=()):
        snapshot = tuple(cards)
        linked = frozenset(linked)
        if snapshot == self.snapshot and linked == self.linked:
            return
        self.snapshot = snapshot
        self.linked = linked
        canvas = QWidget()
        columns = QHBoxLayout(canvas)
        columns.setContentsMargins(0, 0, 4, 0)
        columns.setSpacing(18)
        stacks = []
        for title in ('Не привязаны', 'Привязаны'):
            panel = QWidget()
            panel.setObjectName('ColumnPanel')
            panel.setAccessibleName(title)
            panel.setAttribute(Qt.WA_StyledBackground)
            panel.setMinimumWidth(200)
            stack = QVBoxLayout(panel)
            stack.setSpacing(10)
            heading = QLabel(title)
            heading.setObjectName('SectionTitle')
            stack.addWidget(heading)
            columns.addWidget(panel, 1)
            stacks.append(stack)
        unlinked, linked_stack = stacks
        for bug in snapshot:
            number = str(bug['id']).lstrip('0')
            stack = linked_stack if number in linked else unlinked
            stack.addWidget(self.make_card(bug))
        for stack in stacks:
            if stack.count() == 1:
                message = QLabel('Нет багов')
                message.setObjectName('Muted')
                stack.addWidget(message)
            stack.addStretch()
        position = self.scroll.verticalScrollBar().value()
        self.scroll.setWidget(canvas)
        self.scroll.verticalScrollBar().setValue(position)

    @staticmethod
    def make_card(bug):
        card = QWidget()
        card.setObjectName('BugzillaCard')
        card.setAttribute(Qt.WA_StyledBackground)
        body = QVBoxLayout(card)
        number = field_label(f"#{bug['id']}")
        number.setObjectName('SectionTitle')
        body.addWidget(number)
        body.addWidget(field_label(bug['summary']))
        fields = QFormLayout()
        fields.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        fields.setRowWrapPolicy(QFormLayout.WrapAllRows)
        for key, title in (('cf_event_1', 'Дальнейшее действие'), ('cf_cr_num', 'ТИ')):
            label = QLabel(title)
            label.setObjectName('Muted')
            fields.addRow(label, field_label(bug[key]))
        body.addLayout(fields)
        return card
