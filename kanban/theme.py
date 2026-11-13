"""Offline application palette: shared by native controls and painted cards."""
import hashlib
from PyQt5.QtCore import QSettings, Qt
from PyQt5.QtGui import QColor, QPalette, QIcon, QPixmap, QPainter

LIGHT = dict(background='#ffffff', canvas='#F4F5F7', sidebar='#FFFFFF',
             text='#1E293B', muted='#64748B', border='#E2E8F0', accent='#2563EB',
             hover='#e9eef7', selection='#dce7ff', danger='#b42318',
             scroll='#96a6bc', accent_hover='#1d4ed8')
DARK = dict(background='#202b3c', canvas='#151d2b', sidebar='#111827',
            text='#edf2fa', muted='#b1bfd3', border='#50617a', accent='#8baeff',
            hover='#2c3b52', selection='#334c76', danger='#ffaba5',
            scroll='#7387a4', accent_hover='#acc4ff')
TOKENS = dict(LIGHT)
PRIORITIES = {
    'Низкий': ('#526078', '#edf0f4'), 'Обычный': ('#234ba5', '#eaf1ff'),
    'Высокий': ('#805000', '#fff0ca'), 'Срочный': ('#a32018', '#fde4e2'),
}


def label_colors(name):
    # Stable across machines, processes, updates and both themes; no Python hash().
    hue = int(hashlib.sha256(name.encode('utf-8')).hexdigest()[:8], 16) % 360
    return QColor.fromHsl(hue, 175, 70).name(), QColor.fromHsl(hue, 160, 230).name()


def label_icon(name):
    pixmap = QPixmap(16, 16)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(Qt.NoPen)
    foreground, background = label_colors(name)
    painter.setPen(QColor(background))
    painter.setBrush(QColor(foreground))
    painter.drawEllipse(2, 2, 12, 12)
    painter.end()
    return QIcon(pixmap)


def stylesheet():
    t = TOKENS
    return '''
QWidget#BoardCanvas { background: %(canvas)s; }
QWidget { color: %(text)s; font-family: "Arial", "Liberation Sans", sans-serif; font-size: 14px; }
QMainWindow, QDialog, QWidget#content, QWidget#scrollBody { background: %(canvas)s; }
QWidget#side { background: %(sidebar)s; }
QWidget#side QLabel { color: %(muted)s; }
QLabel#appName { color: %(text)s; font-size: 22px; font-weight: 600; }
QLabel#Heading, QLabel#title { font-size: 20px; font-weight: 600; }
QLabel#SectionTitle, QLabel#ColumnTitle { font-size: 14px; font-weight: 600; }
QLabel#Muted, QLabel#muted, QLabel#Status, QLabel#hint { color: %(muted)s; }
QLabel#fieldLabel { font-weight: 600; }
QWidget#ColumnPanel { background: %(canvas)s; border: none; border-radius: 8px; }
QWidget#ColumnPanel QLabel { background: transparent; border: none; }
QLabel#ColumnTitle { padding: 12px 4px; border-radius: 5px; color: #202c40; }
QWidget#ColumnPanel[tone="0"] { background: transparent; border-top: 3px solid #568bd7; }
QWidget#ColumnPanel[tone="1"] { background: transparent; border-top: 3px solid #9270cd; }
QWidget#ColumnPanel[tone="2"] { background: transparent; border-top: 3px solid #d5a033; }
QWidget#ColumnPanel[tone="3"] { background: transparent; border-top: 3px solid #d17c66; }
QWidget#ColumnPanel[tone="4"] { background: transparent; border-top: 3px solid #59a479; }
QPushButton { background: %(background)s; border: 1px solid %(border)s; border-radius: 6px; padding: 5px 10px; }
QPushButton:hover { border-color: %(accent)s; background: %(hover)s; }
QPushButton:focus { border: 2px solid %(accent)s; }
QPushButton:disabled { color: %(muted)s; background: %(canvas)s; }
QPushButton#primary, QPushButton[variant="primary"] { background: #2563EB; border-color: #2563EB; color: #ffffff; font-weight: 600; }
QPushButton#primary:hover { background: #1d4ed8; }
QPushButton#danger { color: %(danger)s; }
QPushButton#link { background: transparent; border: none; color: %(accent)s; padding: 0; text-decoration: underline; }
QWidget#side QPushButton[variant="quiet"] { color: %(text)s; background: transparent; border: none; text-align: left; padding: 9px 10px; }
QWidget#side QPushButton[variant="quiet"]:hover { background: %(selection)s; color: %(accent)s; }
QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QDateEdit, QSpinBox, QListWidget {
 background: %(background)s; border: 1px solid %(border)s; border-radius: 6px; padding: 5px 7px;
 selection-background-color: %(selection)s; selection-color: %(text)s; }
QLineEdit:focus, QTextEdit:focus, QComboBox:focus, QDateEdit:focus, QListWidget:focus { border-color: %(accent)s; }
QComboBox QAbstractItemView { background: %(background)s; color: %(text)s; selection-background-color: %(selection)s; selection-color: %(text)s; }
QListWidget#Boards { background: transparent; color: %(text)s; border: none; padding: 0; }
QListWidget#Boards::item { padding: 11px 12px; border-radius: 6px; margin: 3px 0; }
QListWidget#Boards::item:hover { background: %(hover)s; }
QListWidget#Boards::item:selected { background: %(selection)s; color: %(accent)s; font-weight: 600; }
QListWidget#Cards { background: transparent; border: none; padding: 0; }
QListWidget#Checklist::item { padding: 6px; }
QTableWidget { background: %(background)s; alternate-background-color: %(hover)s; border: 1px solid %(border)s; border-radius: 6px; gridline-color: %(border)s; selection-background-color: %(selection)s; selection-color: %(text)s; }
QHeaderView::section { background: %(hover)s; color: %(text)s; padding: 9px; border: none; border-bottom: 1px solid %(border)s; font-weight: 600; }
QTabWidget::pane { border: none; }
QTabBar::tab { padding: 10px 20px; color: %(muted)s; border-bottom: 3px solid transparent; }
QTabBar::tab:selected { color: %(accent)s; border-bottom: 3px solid %(accent)s; font-weight: 600; }
QScrollArea { background: transparent; border: none; }
QScrollBar:vertical { background: %(canvas)s; width: 14px; }
QScrollBar:horizontal { background: %(canvas)s; height: 14px; }
QScrollBar::handle { background: %(scroll)s; border-radius: 5px; min-height: 24px; min-width: 24px; }
QScrollBar::handle:hover { background: %(accent)s; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QProgressBar { background: %(hover)s; border: 1px solid %(border)s; border-radius: 5px; text-align: center; }
QProgressBar::chunk { background: %(accent)s; }
QTreeWidget { background: %(sidebar)s; color: %(text)s; border: none; padding: 4px; }
QTreeWidget::branch:selected { background: %(selection)s; }
QTreeWidget::item { padding: 7px 4px; border-radius: 4px; }
QTreeWidget::item:selected { background: %(selection)s; color: %(accent)s; }
QWidget#Header { background: %(background)s; border-bottom: 1px solid %(border)s; }
QSplitter::handle { background: %(border)s; }
QWidget#side QPushButton:checked { color: %(accent)s; background: %(selection)s; }
QLabel#ChatHeading { font-size: 14px; font-weight: 600; padding: 8px 12px; border-bottom: 1px solid %(border)s; }
QWidget#side QLabel#appName { color: %(text)s; }
QLabel#BoardName { font-size: 16px; font-weight: 600; }
QLabel#Eyebrow { font-size: 11px; color: %(muted)s; letter-spacing: 1px; }
QLabel#Profile { border-top: 1px solid %(border)s; padding-top: 16px; font-size: 12px; }
QLabel#CountBadge { background: %(hover)s; color: %(muted)s; border-radius: 9px; padding: 2px 6px; font-size: 12px; }
QComboBox#ViewSwitch { background: %(hover)s; border: none; padding: 7px 10px; }
QTextEdit#ChatTimeline { border: none; background: %(background)s; padding: 12px; }
QWidget#side { border-right: 1px solid %(border)s; }
QToolTip { color: %(text)s; background: %(background)s; border: 1px solid %(border)s; padding: 6px; }
''' % t


def apply_theme(app, dark=False):
    TOKENS.update(DARK if dark else LIGHT)
    palette = QPalette()
    roles = {'Window': 'canvas', 'WindowText': 'text', 'Base': 'background',
             'AlternateBase': 'hover', 'Text': 'text', 'Button': 'background',
             'ButtonText': 'text', 'Highlight': 'selection', 'HighlightedText': 'text',
             'ToolTipBase': 'background', 'ToolTipText': 'text', 'Link': 'accent'}
    for role, token in roles.items():
        palette.setColor(getattr(QPalette, role), QColor(TOKENS[token]))
    palette.setColor(QPalette.Disabled, QPalette.Text, QColor(TOKENS['muted']))
    palette.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(TOKENS['muted']))
    app.setPalette(palette)
    app.setStyleSheet(stylesheet())
    for widget in app.topLevelWidgets():
        widget.update()
        if hasattr(widget, 'render_history'):
            widget.render_history()
            widget.render_comments()


def theme_settings():
    # Keep the legacy settings identity so TeamSync retains theme preferences.
    return QSettings('LocalKanban', 'Kanban')
