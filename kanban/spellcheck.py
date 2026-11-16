"""Offline Russian/English spelling; formatting never changes saved text."""
from functools import lru_cache
from pathlib import Path
import logging
import re
import weakref
from PyQt5 import sip
from PyQt5.QtCore import QObject, QRunnable, QThreadPool, pyqtSignal, Qt
from PyQt5.QtGui import QSyntaxHighlighter, QTextCharFormat, QColor, QPainter, QPainterPath
from PyQt5.QtWidgets import QTextEdit, QLineEdit, QStyledItemDelegate
from .theme import theme_settings, TOKENS

WORDS = re.compile(r"(?<![\w])[A-Za-zА-Яа-яЁё]+(?:['’-][A-Za-zА-Яа-яЁё]+)*(?![\w])")
SKIP = re.compile(r"(?:https?://|www\.)\S+|[\w.+-]+@[\w.-]+|[A-Za-z]:\\\S+|\\\\\S+")


class LoadDictionaries(QRunnable):
    def __init__(self, engine):
        super().__init__()
        self.engine = engine

    def run(self):
        try:
            from ._vendor.spylls.hunspell import Dictionary
            root = Path(__file__).parent / 'dictionaries'
            result = tuple(Dictionary.from_files(str(root / name)) for name in ('ru_RU', 'en_US'))
            self.engine.loaded.emit(result, None)
        except Exception as error:
            logging.exception('Spelling dictionaries could not be loaded')
            self.engine.loaded.emit(None, str(error))


class SpellEngine(QObject):
    changed = pyqtSignal()
    loaded = pyqtSignal(object, object)

    def __init__(self):
        super().__init__()
        self.dictionaries = None
        self.error = None
        self.loaded.connect(self.finish)
        QThreadPool.globalInstance().start(LoadDictionaries(self))

    def finish(self, dictionaries, error):
        self.dictionaries, self.error = dictionaries, error
        self.correct.cache_clear()
        self.changed.emit()

    def enabled(self):
        return theme_settings().value('spelling/enabled', True, type=bool)

    def set_enabled(self, value):
        theme_settings().setValue('spelling/enabled', value)
        self.changed.emit()

    @lru_cache(maxsize=30000)
    def correct(self, word):
        if self.dictionaries is None:
            return True
        if word.casefold() == 'teamsync':
            return True
        dictionary = self.dictionaries[0 if re.search('[А-Яа-яЁё]', word) else 1]
        return dictionary.lookup(word) or dictionary.lookup(word.lower())

    def mistakes(self, text):
        if not self.enabled() or self.dictionaries is None:
            return []
        ignored = [m.span() for m in SKIP.finditer(text)]
        result = []
        for match in WORDS.finditer(text):
            word = match.group()
            if len(word) < 3 or len(word) > 80 or (word.isupper() and len(word) <= 3):
                continue
            if any(start <= match.start() < end for start, end in ignored):
                continue
            if not self.correct(word):
                result.append((match.start(), match.end()))
        return result


_engine = None


def spell_engine():
    global _engine
    if _engine is None:
        _engine = SpellEngine()
    return _engine


class SpellHighlighter(QSyntaxHighlighter):
    def __init__(self, editor):
        super().__init__(editor.document())
        self.editor_ref = weakref.ref(editor)
        self.engine = spell_engine()
        self.engine.changed.connect(self.rehighlight)
        self.error_format = QTextCharFormat()
        self.error_format.setUnderlineStyle(QTextCharFormat.SpellCheckUnderline)
        self.error_format.setUnderlineColor(QColor(TOKENS['danger']))

    def highlightBlock(self, text):
        editor = self.editor_ref()
        if editor is None or sip.isdeleted(editor) or editor.isReadOnly():
            return
        self.error_format.setUnderlineColor(QColor(TOKENS['danger']))
        for start, end in self.engine.mistakes(text):
            offset = len(text[:start].encode('utf-16-le')) // 2
            length = len(text[start:end].encode('utf-16-le')) // 2
            self.setFormat(offset, length, self.error_format)


class SpellTextEdit(QTextEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.spell_highlighter = SpellHighlighter(self)


class SpellLineEdit(QLineEdit):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.engine = spell_engine()
        self.engine.changed.connect(self.update)
        self.textChanged.connect(self.update)
        self.cursorPositionChanged.connect(self.update)

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.isReadOnly() or self.echoMode() != QLineEdit.Normal:
            return
        text = self.text()
        mistakes = self.engine.mistakes(text)
        if not mistakes:
            return
        painter = QPainter(self)
        painter.setClipRect(self.rect().adjusted(3, 2, -3, -2))
        painter.setPen(QColor(TOKENS['danger']))
        metrics = self.fontMetrics()
        prefix = text.encode('utf-16-le')[:self.cursorPosition() * 2].decode('utf-16-le', errors='ignore')
        origin = self.cursorRect().x() - metrics.horizontalAdvance(prefix)
        y = min(self.height() - 4, self.cursorRect().bottom() - 1)
        for start, end in mistakes:
            left = origin + metrics.horizontalAdvance(text[:start])
            right = origin + metrics.horizontalAdvance(text[:end])
            path = QPainterPath()
            path.moveTo(left, y)
            for x in range(max(left, -2), min(right + 1, self.width()), 2):
                path.lineTo(x, y + (2 if ((x - left) // 2) % 2 else 0))
            painter.drawPath(path)


class SpellItemDelegate(QStyledItemDelegate):
    def createEditor(self, parent, option, index):
        return SpellLineEdit(parent)
