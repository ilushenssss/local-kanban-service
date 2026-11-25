"""Bounded, static PNG stickers. Qt codecs are shared by preview and storage."""
from pathlib import Path
import sys
from PyQt5.QtCore import QBuffer, QByteArray, QCoreApplication, QIODevice, Qt
from PyQt5.QtGui import QImageReader

MAX_SOURCE = 10 * 1024 * 1024
MAX_PIXELS = 25_000_000
MAX_PNG = 2 * 1024 * 1024


def prepare_sticker(source):
    if sys.platform == 'win32':
        QCoreApplication.addLibraryPath(str(Path(__file__).parent / 'qt_plugins'))
    if len(source) > MAX_SOURCE:
        raise ValueError('Изображение превышает 10 МБ')
    data = QByteArray(source)
    buffer = QBuffer(data)
    buffer.open(QIODevice.ReadOnly)
    reader = QImageReader(buffer)
    reader.setAutoTransform(True)
    if bytes(reader.format()).lower() not in (b'png', b'jpeg', b'jpg', b'webp'):
        raise ValueError('Выберите изображение PNG, JPG или WebP')
    size = reader.size()
    if not size.isValid() or size.width() * size.height() > MAX_PIXELS:
        raise ValueError('Изображение повреждено или превышает 25 миллионов пикселей')
    if max(size.width(), size.height()) > 512:
        reader.setScaledSize(size.scaled(512, 512, Qt.KeepAspectRatio))
    image = reader.read()
    if image.isNull():
        raise ValueError('Не удалось прочитать изображение')
    image = image.scaled(512, 512, Qt.KeepAspectRatio, Qt.SmoothTransformation) if max(image.width(), image.height()) > 512 else image
    output = QByteArray()
    dest = QBuffer(output)
    dest.open(QIODevice.WriteOnly)
    if not image.save(dest, 'PNG') or len(output) > MAX_PNG:
        raise ValueError('Не удалось подготовить стикер')
    return bytes(output), image.width(), image.height()


def sticker_from_file(path):
    with Path(path).open('rb') as src:
        return prepare_sticker(src.read(MAX_SOURCE + 1))
