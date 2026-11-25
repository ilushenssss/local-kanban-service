from pathlib import Path

from PyQt5 import sip
from PyQt5.QtCore import QSize, Qt
from PyQt5.QtGui import QIcon, QImage, QPixmap
from PyQt5.QtWidgets import (QDialog, QFileDialog, QHBoxLayout, QLabel, QListView, QListWidget, QListWidgetItem,
                             QPushButton, QVBoxLayout)

from .stickers import MAX_SOURCE


class StickerPackDialog(QDialog):
    def __init__(self, store, work, parent, *, manage=False):
        super().__init__(parent)
        self.store, self.work, self.manage = store, work, manage
        self.pending = False
        self.selected_sticker = None
        self.setWindowTitle('Набор стандартных стикеров' if manage else 'Стикеры')
        self.resize(460, 400)
        layout = QVBoxLayout(self)
        self.list = QListWidget()
        self.list.setAccessibleName('Стандартные стикеры')
        self.list.setViewMode(QListView.IconMode)
        self.list.setResizeMode(QListView.Adjust)
        self.list.setMovement(QListView.Static)
        self.list.setIconSize(QSize(72, 72))
        self.list.setGridSize(QSize(105, 108))
        self.list.currentItemChanged.connect(lambda *_: self.update_actions())
        if not manage:
            self.list.itemActivated.connect(lambda *_: self.choose())
        layout.addWidget(self.list, 1)
        self.status = QLabel()
        self.status.setTextFormat(Qt.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        actions = QHBoxLayout()
        self.primary = QPushButton('Добавить…' if manage else 'Выбрать')
        self.primary.clicked.connect(self.add_sticker if manage else self.choose)
        actions.addWidget(self.primary)
        self.remove_button = QPushButton('Удалить из набора')
        self.remove_button.clicked.connect(self.remove_sticker)
        self.remove_button.setVisible(manage)
        actions.addWidget(self.remove_button)
        actions.addStretch()
        close = QPushButton('Закрыть')
        close.clicked.connect(self.reject)
        actions.addWidget(close)
        layout.addLayout(actions)
        self.load()

    def update_actions(self):
        selected = self.list.currentItem() is not None
        self.primary.setEnabled(not self.pending and (self.manage or selected))
        self.remove_button.setEnabled(not self.pending and self.manage and selected)

    def load(self, change=None):
        self.pending = True
        self.update_actions()
        self.status.setText('Сохранение…' if change else 'Загрузка…')

        def read():
            if change:
                change()
            return self.store.standard_stickers()

        def done(rows, error):
            if sip.isdeleted(self):
                return
            self.pending = False
            if error:
                self.status.setText(str(error) or 'Не удалось прочитать набор стикеров.')
            else:
                self.list.clear()
                for key, name, data in sorted(rows, key=lambda row: row[1].casefold()):
                    image = QImage.fromData(data, 'PNG')
                    if image.isNull():
                        continue
                    item = QListWidgetItem(QIcon(QPixmap.fromImage(image)), name)
                    item.setToolTip(name)
                    item.setData(Qt.UserRole, (key, data))
                    self.list.addItem(item)
                self.status.setText('В наборе пока нет стикеров.' if not self.list.count() else '')
            self.update_actions()

        self.work(read, done)

    def add_sticker(self):
        if self.pending or not self.manage:
            return
        name, _ = QFileDialog.getOpenFileName(self, 'Добавить стикер в набор', '',
                                              'Изображения (*.png *.jpg *.jpeg *.webp)')
        if not name:
            return
        path = Path(name)

        def add():
            with path.open('rb') as file:
                self.store.add_standard_sticker(path.stem, file.read(MAX_SOURCE + 1))

        self.load(add)

    def remove_sticker(self):
        item = self.list.currentItem()
        if item and self.manage and not self.pending:
            key, _ = item.data(Qt.UserRole)
            self.load(lambda: self.store.remove_standard_sticker(key))

    def choose(self):
        item = self.list.currentItem()
        if item and not self.pending and not self.manage:
            _, self.selected_sticker = item.data(Qt.UserRole)
            self.accept()

    def reject(self):
        if not self.pending:
            super().reject()
