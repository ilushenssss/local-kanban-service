import hashlib
from functools import lru_cache

from PyQt5.QtCore import QBuffer, QByteArray, QIODevice, QRect, QUrl, Qt
from PyQt5.QtGui import QImage, QPainter, QPainterPath, QTextDocument

from .stickers import read_image


def prepare_avatar(source):
    original = read_image(source, 512)
    edge = min(original.width(), original.height())
    crop = QRect((original.width() - edge) // 2, (original.height() - edge) // 2, edge, edge)
    size = 96
    avatar = QImage(size, size, QImage.Format_ARGB32_Premultiplied)
    avatar.fill(Qt.transparent)
    painter = QPainter(avatar)
    painter.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
    circle = QPainterPath()
    circle.addEllipse(0, 0, size, size)
    painter.setClipPath(circle)
    painter.drawImage(avatar.rect(), original, crop)
    painter.end()
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.WriteOnly)
    if not avatar.save(buffer, 'PNG'):
        raise ValueError('Не удалось подготовить аватар.')
    return bytes(data)


@lru_cache(maxsize=128)
def avatar_image(data):
    return QImage.fromData(data, 'PNG')


def avatar_resources(document, avatars):
    sources = {}
    for login, data in avatars.items():
        image = avatar_image(data)
        if not image.isNull():
            url = 'avatar:' + hashlib.sha256(data).hexdigest()
            document.addResource(QTextDocument.ImageResource, QUrl(url), image)
            sources[login] = url
    return sources
