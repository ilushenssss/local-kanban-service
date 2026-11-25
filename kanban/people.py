from PyQt5.QtCore import Qt
from PyQt5.QtGui import QPixmap
from PyQt5.QtWidgets import QDialog, QDialogButtonBox, QLabel, QVBoxLayout

from .avatars import avatar_image


def open_employee_profile(url, owner, store, work, status):
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
        layout.addWidget(close)
        dialog.exec_()
        dialog.deleteLater()

    work(lambda: store.employee_profile(user_id), done)
