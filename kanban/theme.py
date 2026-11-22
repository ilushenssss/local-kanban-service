"""Offline application palette: shared by native controls and painted cards."""
import hashlib
from pathlib import Path
from PyQt5.QtCore import QSettings, Qt
from PyQt5.QtGui import QColor, QPalette, QFontDatabase, QIcon, QPainter, QPainterPath, QPen, QPixmap

LIGHT = dict(background='#FFFFFF', canvas='#F4F5F8', sidebar='#FFFFFF',
             text='#1B1F2A', secondary='#4A5264', muted='#5E6678',
             border='#E3E6EC', field_border='#D5D9E2', accent='#122C34',
             hover='#F0F2F6', selection='#E7EFF0', selected_text='#122C34',
             danger='#9B1C1C', danger_border='#F3C6C6',
             scroll='#9AA2B4', accent_hover='#1C414B', accent_pressed='#0C2026',
             column='#E8EAF0', properties='#F7F8FA', on_accent='#FFFFFF')
DARK = dict(background='#202b3c', canvas='#151d2b', sidebar='#111827',
            text='#edf2fa', secondary='#c3cddd', muted='#b1bfd3',
            border='#50617a', field_border='#65758c', accent='#9DC5CB',
            hover='#2c3b52', selection='#24434B', selected_text='#DCEFF1',
            danger='#ffaba5', danger_border='#95616a',
            scroll='#7387a4', accent_hover='#BCDADE', accent_pressed='#DCEFF1',
            column='#263346', properties='#1b2636', on_accent='#151d2b')
STICKERS = (
    ('#9B1C1C', '#FDE8E8'), ('#1E4FA8', '#E0ECFF'),
    ('#146C3A', '#DDF5E6'), ('#7A4B00', '#FFF1D1'),
    ('#5B34B8', '#EDE6FF'),
)
AVATAR_COLORS = ('#122C34', '#C2410C', '#0F766E')
_fonts_loaded = False


def load_fonts():
    """Load the reference's licensed Inter fonts without network access."""
    global _fonts_loaded
    if not _fonts_loaded:
        for path in sorted((Path(__file__).parent / 'assets' / 'fonts').glob('*.ttf')):
            if QFontDatabase.addApplicationFont(str(path)) < 0:
                raise RuntimeError('Не удалось загрузить шрифт ' + path.name)
        _fonts_loaded = True

TOKENS = dict(LIGHT)
PRIORITIES = {
    'Низкий': ('#4A5264', '#F0F2F6'), 'Обычный': STICKERS[1],
    'Высокий': STICKERS[3], 'Срочный': STICKERS[0],
}


def label_colors(name):
    # Stable palette assignment without mutating labels or their stored values.
    key = int(hashlib.sha256(name.encode('utf-8')).hexdigest(), 16)
    return STICKERS[key % len(STICKERS)]


def avatar_color(name):
    key = int(hashlib.sha256(name.encode('utf-8')).hexdigest(), 16)
    return AVATAR_COLORS[key % len(AVATAR_COLORS)]


def symbol_icon(name):
    if name == 'bell':
        pixmap = QPixmap(48, 48)
        pixmap.setDevicePixelRatio(2)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(QColor(TOKENS['text']), 1.7, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        outline = QPainterPath()
        outline.moveTo(4, 17)
        outline.cubicTo(7, 15, 6, 12, 7, 9)
        outline.cubicTo(8, 4, 16, 4, 17, 9)
        outline.cubicTo(18, 12, 17, 15, 20, 17)
        outline.closeSubpath()
        outline.moveTo(10, 20)
        outline.quadTo(12, 22, 14, 20)
        painter.drawPath(outline)
        painter.drawLine(12, 3, 12, 5)
        painter.end()
        return QIcon(pixmap)
    theme = 'dark' if TOKENS['background'] == DARK['background'] else 'light'
    return QIcon(str(Path(__file__).parent / 'assets' / 'controls' / (name + '-' + theme + '.png')))


def stylesheet():
    load_fonts()
    t = dict(TOKENS)
    arrow_theme = 'dark' if TOKENS['background'] == DARK['background'] else 'light'
    controls = Path(__file__).resolve().parent / 'assets' / 'controls'
    for state, suffix in [('arrow', ''), ('arrow_active', '-active'), ('arrow_disabled', '-disabled')]:
        t[state] = (controls / ('chevron-' + arrow_theme + suffix + '.png')).as_posix()
    return '''
QWidget#BoardCanvas { background: %(canvas)s; }
QWidget { color: %(text)s; font-family: "Inter", "Arial", sans-serif; font-size: 14px; }
QMainWindow, QDialog, QWidget#content, QWidget#scrollBody { background: %(canvas)s; }
QWidget#side { background: %(sidebar)s; }
QWidget#side QLabel { color: %(muted)s; }
QLabel#SystemLogo { background: #FFFFFF; border-radius: 6px; }
QLabel#appName { color: %(text)s; font-size: 22px; font-weight: 600; }
QLabel#Heading, QLabel#title { font-size: 20px; font-weight: 600; }
QLabel#SectionTitle, QLabel#ColumnTitle { font-size: 14px; font-weight: 600; }
QLabel#Muted, QLabel#muted, QLabel#Status, QLabel#hint { color: %(muted)s; }
QLabel#fieldLabel { font-weight: 600; }
QWidget#ColumnPanel { background: %(hover)s; border: 1px solid %(border)s; border-radius: 8px; }
QWidget#ColumnPanel QLabel { background: transparent; border: none; }
QLabel#ColumnTitle { padding: 12px 4px; border-radius: 5px; color: %(text)s; }
QWidget#ColumnPanel[tone="0"] { background: %(hover)s; border-top: 3px solid #568bd7; }
QWidget#ColumnPanel[tone="1"] { background: %(hover)s; border-top: 3px solid #9270cd; }
QWidget#ColumnPanel[tone="2"] { background: %(hover)s; border-top: 3px solid #d5a033; }
QWidget#ColumnPanel[tone="3"] { background: %(hover)s; border-top: 3px solid #d17c66; }
QWidget#ColumnPanel[tone="4"] { background: %(hover)s; border-top: 3px solid #59a479; }
QPushButton { background: %(background)s; border: 1px solid %(field_border)s; border-radius: 8px; padding: 7px 14px; min-height: 20px; }
QPushButton:hover { border-color: %(accent)s; background: %(hover)s; }
QPushButton:pressed { background: %(selection)s; border-color: %(accent)s; }
QPushButton:focus { border-color: %(accent)s; outline: none; }
QPushButton:disabled { color: %(muted)s; background: %(canvas)s; border-color: %(border)s; }
QPushButton#primary, QPushButton[variant="primary"] { background: %(accent)s; border-color: %(accent)s; color: %(on_accent)s; font-weight: 600; }
QPushButton#primary:hover, QPushButton[variant="primary"]:hover { background: %(accent_hover)s; border-color: %(accent_hover)s; }
QPushButton#primary:pressed, QPushButton[variant="primary"]:pressed { background: %(accent_pressed)s; }
QPushButton#primary:focus, QPushButton[variant="primary"]:focus { border-color: %(text)s; }
QPushButton#primary:disabled, QPushButton[variant="primary"]:disabled { background: %(hover)s; color: %(muted)s; border-color: %(border)s; }
QPushButton#danger, QPushButton[variant="danger"] { color: %(danger)s; border-color: %(danger_border)s; }
QPushButton#danger:hover, QPushButton[variant="danger"]:hover { background: %(hover)s; border-color: %(danger)s; }
QPushButton[variant="danger"]:disabled, QPushButton#danger:disabled { color: %(muted)s; border-color: %(border)s; background: %(canvas)s; }
QRadioButton { spacing: 10px; padding: 5px 0; min-height: 22px; }
QRadioButton::indicator { width: 16px; height: 16px; border: 1px solid %(field_border)s; border-radius: 9px; background: %(background)s; }
QRadioButton::indicator:checked { border: 5px solid %(accent)s; width: 8px; height: 8px; border-radius: 9px; }
QRadioButton:focus { color: %(accent)s; }
QPushButton#NotificationLauncher { padding: 7px 4px; color: %(danger)s; font-weight: 600; }
QPushButton#ChatLauncher { background: %(accent)s; color: %(on_accent)s; border: 1px solid %(accent)s; border-radius: 28px; padding: 0; }
QPushButton#ChatLauncher:hover { background: %(accent_hover)s; }
QPushButton#ChatLauncher:pressed { background: %(accent_pressed)s; }
QPushButton#ChatLauncher:focus { border: 2px solid %(text)s; }
QPushButton#AddColumnTask { text-align: left; background: transparent; color: %(accent)s; border: 1px solid transparent; border-radius: 6px; padding: 3px 8px; font-size: 12px; min-height: 20px; }
QPushButton#AddColumnTask:hover { background: %(selection)s; color: %(selected_text)s; }
QPushButton#AddColumnTask:focus { border-color: %(accent)s; }
QPushButton#AddColumnTask:pressed { background: %(selection)s; }
QPushButton#link { background: transparent; border: none; color: %(accent)s; padding: 0; text-decoration: underline; }
QWidget#side QPushButton[variant="quiet"] { color: %(text)s; background: transparent; border: none; text-align: left; padding: 9px 10px; }
QWidget#side QPushButton[variant="quiet"]:hover { background: %(selection)s; color: %(accent)s; }
QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QDateEdit, QSpinBox, QListWidget {
 background: %(background)s; border: 1px solid %(field_border)s; border-radius: 8px; padding: 7px 10px;
 selection-background-color: %(selection)s; selection-color: %(selected_text)s; }
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QComboBox:focus, QDateEdit:focus, QListWidget:focus { border-color: %(accent)s; }
/* Reserve a full click target, with a small centered chevron and no native bevel. */
QComboBox, QDateEdit { padding-right: 36px; }
QComboBox::drop-down, QDateEdit::drop-down {
 subcontrol-origin: border; subcontrol-position: top right; width: 32px;
 border: none; border-top-right-radius: 8px; border-bottom-right-radius: 8px;
 background: transparent; }
QComboBox::drop-down:hover, QDateEdit::drop-down:hover { background: %(hover)s; }
QComboBox::drop-down:pressed, QDateEdit::drop-down:pressed { background: %(selection)s; }
QComboBox::down-arrow, QDateEdit::down-arrow {
 image: url("%(arrow)s"); width: 12px; height: 8px; }
QComboBox::down-arrow:on, QComboBox::down-arrow:hover, QDateEdit::down-arrow:hover {
 image: url("%(arrow_active)s"); }
QComboBox::down-arrow:disabled, QDateEdit::down-arrow:disabled {
 image: url("%(arrow_disabled)s"); }
QComboBox QAbstractItemView { background: %(background)s; color: %(text)s; selection-background-color: %(selection)s; selection-color: %(selected_text)s; }
QListWidget#Boards { background: transparent; color: %(text)s; border: none; padding: 0; }
QListWidget#Boards::item { padding: 11px 12px; border-radius: 8px; margin: 3px 0; }
QListWidget#Boards::item:hover { background: %(hover)s; }
QListWidget#Boards::item:selected { background: %(selection)s; color: %(accent)s; font-weight: 600; }
QListWidget#Cards { background: transparent; border: none; padding: 0; }
QListWidget#Checklist::item { padding: 6px; }
QTableWidget { background: %(background)s; alternate-background-color: %(hover)s; border: 1px solid %(border)s; border-radius: 8px; gridline-color: %(border)s; selection-background-color: %(selection)s; selection-color: %(selected_text)s; }
QHeaderView::section { background: %(hover)s; color: %(text)s; padding: 9px; border: none; border-bottom: 1px solid %(border)s; font-weight: 600; }
QTabWidget::pane { border: none; }
QTabBar::tab { padding: 10px 16px; min-width: 76px; min-height: 22px; font-weight: 600; color: %(muted)s; border-bottom: 2px solid transparent; }
QTabBar::tab:selected { color: %(selected_text)s; border-bottom: 2px solid %(accent)s; }
QTabWidget#ProjectSections::tab-bar { left: 20px; }
QTabBar#ProjectSectionTabs::tab { padding: 7px 15px; min-width: 32px; background: %(hover)s; border: 1px solid %(border)s; border-top-left-radius: 8px; border-top-right-radius: 8px; margin-right: 4px; }
QTabBar#ProjectSectionTabs::tab:selected { background: %(background)s; color: %(text)s; border-top: 2px solid %(accent)s; }
QTabBar#ProjectSectionTabs::tab:focus { border-bottom: 2px solid %(accent)s; }
QScrollArea { background: transparent; border: none; }
QScrollBar:vertical { background: %(canvas)s; width: 14px; }
QScrollBar:horizontal { background: %(canvas)s; height: 14px; }
QScrollBar::handle { background: %(scroll)s; border-radius: 5px; min-height: 24px; min-width: 24px; }
QScrollBar::handle:pressed { background: %(selected_text)s; }
QScrollBar::handle:hover { background: %(accent)s; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QProgressBar { background: %(hover)s; border: 1px solid %(border)s; border-radius: 5px; text-align: center; }
QProgressBar::chunk { background: %(accent)s; border-radius: 2px; }
QTreeWidget { background: %(sidebar)s; color: %(text)s; border: none; padding: 4px; }
QTreeWidget::branch:selected { background: %(selection)s; }
QTreeWidget::item { padding: 7px 4px; border-radius: 4px; }
QTreeWidget::item:selected { background: %(selection)s; color: %(accent)s; }
QWidget#Header { background: %(background)s; border-bottom: 1px solid %(border)s; }
QSplitter::handle { background: %(border)s; }
QWidget#side QPushButton:checked { color: %(selected_text)s; background: %(selection)s; }
QLabel#ChatHeading { font-size: 14px; font-weight: 600; padding: 8px 12px; border-bottom: 1px solid %(border)s; }
QWidget#side QLabel#appName { color: %(text)s; }
QLabel#BoardName { font-size: 16px; font-weight: 600; }
QLabel#Eyebrow { font-size: 11px; color: %(muted)s; letter-spacing: 1px; }
QLabel#Profile { border-top: 1px solid %(border)s; padding-top: 16px; font-size: 12px; }
QLabel#CountBadge { background: %(hover)s; color: %(muted)s; border-radius: 9px; padding: 2px 6px; font-size: 12px; }
QComboBox#ViewSwitch { background: %(hover)s; border: none; padding: 7px 36px 7px 10px; }
QTextEdit#ChatTimeline { border: none; background: %(background)s; padding: 12px; }
QWidget#side { border-right: 1px solid %(border)s; }
QWidget#Messenger { background: %(background)s; border: 1px solid %(border)s; border-radius: 12px; }
QCalendarWidget#DeadlineCalendar { background: %(background)s; border: 1px solid %(field_border)s; border-radius: 12px; padding: 8px; }
QCalendarWidget QWidget#qt_calendar_navigationbar { background: %(background)s; padding: 4px; }
QCalendarWidget QToolButton { color: %(text)s; background: transparent; border: none; border-radius: 8px; padding: 6px; min-height: 24px; font-weight: 600; }
QCalendarWidget QToolButton:hover { background: %(hover)s; }
QCalendarWidget QToolButton:pressed { background: %(selection)s; }
QCalendarWidget QToolButton:focus { background: %(selection)s; color: %(selected_text)s; }
QCalendarWidget QToolButton::menu-indicator { image: none; width: 0; }
QCalendarWidget QAbstractItemView { background: %(background)s; color: %(text)s; selection-background-color: %(accent)s; selection-color: %(on_accent)s; border: none; outline: none; font-size: 13px; }
QCalendarWidget QSpinBox { background: %(background)s; color: %(text)s; border: 1px solid %(accent)s; padding: 2px 4px; }
QCalendarWidget QMenu { background: %(background)s; color: %(text)s; border: 1px solid %(field_border)s; padding: 4px; }
QCalendarWidget QMenu::item { padding: 6px 12px; }
QCalendarWidget QMenu::item:selected { background: %(selection)s; color: %(selected_text)s; }
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
        from PyQt5.QtWidgets import QPushButton, QCalendarWidget
        for calendar in widget.findChildren(QCalendarWidget):
            if hasattr(calendar, 'refresh_palette'):
                calendar.refresh_palette()
        for control in widget.findChildren(QPushButton):
            if control.property('symbol'):
                control.setIcon(symbol_icon(control.property('symbol')))
        if hasattr(widget, 'messenger'):
            widget.messenger.markup = None
            widget.messenger.render()
            widget.messenger.bubble.update()
        if hasattr(widget, 'render_history'):
            widget.render_history()
            widget.render_comments()


def theme_settings():
    # Keep the legacy settings identity so TeamSync retains theme preferences.
    return QSettings('LocalKanban', 'Kanban')
