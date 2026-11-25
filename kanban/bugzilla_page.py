import json

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QFormLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget


def field_text(value):
    if value is None or value == '':
        return '—'
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def field_label(value):
    label = QLabel(field_text(value))
    label.setTextFormat(Qt.PlainText)
    label.setWordWrap(True)
    label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
    label.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
    return label


class BugzillaPage(QWidget):
    def __init__(self, export):
        super().__init__()
        self.snapshot = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 12)
        actions = QHBoxLayout()
        self.export_button = QPushButton('Выгрузить баг по ID…')
        self.export_button.setCursor(Qt.PointingHandCursor)
        self.export_button.clicked.connect(export)
        actions.addWidget(self.export_button)
        actions.addStretch()
        layout.addLayout(actions)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        layout.addWidget(self.scroll, 1)
        self.show_cards(())

    def show_cards(self, cards):
        snapshot = tuple(cards)
        if snapshot == self.snapshot:
            return
        self.snapshot = snapshot
        canvas = QWidget()
        stack = QVBoxLayout(canvas)
        stack.setContentsMargins(0, 0, 4, 0)
        stack.setSpacing(10)
        if not snapshot:
            message = QLabel('На этой доске пока нет выгруженных багов.')
            message.setObjectName('Muted')
            message.setWordWrap(True)
            stack.addWidget(message)
        for bug in snapshot:
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
            for key in ('cf_event_1', 'cf_cr_num'):
                fields.addRow(key, field_label(bug[key]))
            body.addLayout(fields)
            stack.addWidget(card)
        stack.addStretch()
        position = self.scroll.verticalScrollBar().value()
        self.scroll.setWidget(canvas)
        self.scroll.verticalScrollBar().setValue(position)
