from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QPixmap
from PyQt5.QtWidgets import (QDialog, QDialogButtonBox, QLabel, QListWidget, QStyle,
                             QStyleOptionViewItem, QVBoxLayout)

from .avatars import avatar_image


class EmployeeList(QListWidget):
    profile_requested = pyqtSignal(object)

    def mouseReleaseEvent(self, event):
        item = self.itemAt(event.pos())
        open_profile = item is not None and event.button() == Qt.LeftButton
        if open_profile and item.data(Qt.CheckStateRole) is not None:
            option = self.viewOptions()
            option.rect = self.visualItemRect(item)
            option.features |= QStyleOptionViewItem.HasCheckIndicator
            check = self.style().subElementRect(QStyle.SE_ItemViewItemCheckIndicator, option, self)
            open_profile = not check.contains(event.pos())
        super().mouseReleaseEvent(event)
        if open_profile:
            self.profile_requested.emit(item)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and self.currentItem():
            self.profile_requested.emit(self.currentItem())
            event.accept()
            return
        super().keyPressEvent(event)


def open_employee_profile(url, owner, store, work, status, open_chat=None):
    if url.scheme() != 'employee':
        return
    try:
        user_id = int(url.path())
    except ValueError:
        return

    def done(profile, error):
        if not owner.isVisible():
            return
        if error:
            status.setText('Профиль сотрудника недоступен.')
            return
        dialog = QDialog(owner)
        dialog.setWindowTitle('Профиль сотрудника')
        dialog.setMinimumWidth(280)
        layout = QVBoxLayout(dialog)
        if profile['avatar']:
            photo = QLabel()
            photo.setAlignment(Qt.AlignCenter)
            photo.setPixmap(QPixmap.fromImage(avatar_image(profile['avatar'])))
            photo.setAccessibleName('Фото профиля')
            layout.addWidget(photo)
        name = QLabel(profile['display_name'])
        name.setTextFormat(Qt.PlainText)
        name.setObjectName('SectionTitle')
        name.setAlignment(Qt.AlignCenter)
        name.setWordWrap(True)
        layout.addWidget(name)
        close = QDialogButtonBox(QDialogButtonBox.Close)
        close.rejected.connect(dialog.reject)
        send_requested = False
        if open_chat and profile['can_message']:
            message = close.addButton('Написать сообщение', QDialogButtonBox.AcceptRole)
            message.setObjectName('primary')

            def send():
                nonlocal send_requested
                send_requested = True
                dialog.accept()

            message.clicked.connect(send)
        layout.addWidget(close)
        if dialog.exec_() == QDialog.Accepted and send_requested:
            open_chat(user_id, profile['display_name'])
        dialog.deleteLater()

    work(lambda: store.employee_profile(user_id), done)
