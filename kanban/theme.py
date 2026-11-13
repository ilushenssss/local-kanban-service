"""Shared controls follow electronic-document-management-system/pyqt-desktop.
Reference: 62db38c74eacd294885918d80db0874a943c3915, sz/ui/common.py.
The reference STYLE is kept verbatim; additions only map Kanban-specific surfaces.
"""
TOKENS = {
    'background': '#ffffff', 'canvas': '#f4f5f8', 'sidebar': '#1f2a44',
    'text': '#1d2433', 'muted': '#6b7385', 'border': '#c8cedb',
    'accent': '#2f5bd3', 'hover': '#eef1f7', 'selection': '#dfe7fb',
    'danger': '#b42318', 'scroll': '#c8cedb', 'accent_hover': '#2649ad',
}
PRIORITIES = {
    'Низкий': ('#617086', '#edf0f4'),
    'Обычный': ('#2f5bd3', '#eaf1ff'),
    'Высокий': ('#94600a', '#fff4da'),
    'Срочный': ('#b42318', '#fdecec'),
}

REFERENCE_STYLE = """
QMainWindow, QWidget#content { background: #f4f5f8; }
QWidget { font-size: 10pt; color: #1d2433; }
QWidget#side { background: #1f2a44; }
QWidget#side QLabel { color: #c9d1e3; }
QLabel#appName { color: #ffffff; font-size: 12pt; font-weight: 600; }
QLabel#sideFolder { color: #8d98b3; font-size: 8pt; }
QPushButton#nav { color: #c9d1e3; background: transparent; border: none; text-align: left;
    padding: 9px 14px; border-radius: 6px; font-size: 10.5pt; }
QPushButton#nav:hover { background: #2b3858; color: #ffffff; }
QPushButton#nav:checked { background: #3a4a72; color: #ffffff; font-weight: 600; }
QPushButton#sideLink { color: #8d98b3; background: transparent; border: none; text-align: left;
    padding: 2px 0; font-size: 8pt; text-decoration: underline; }
QPushButton#sideLink:hover { color: #ffffff; }
QLabel#title { font-size: 16pt; font-weight: 600; }
QLabel#reg { font-size: 16pt; font-weight: 600; color: #1d2433; }
QLabel#muted { color: #6b7385; }
QLabel#fieldLabel { color: #3c4457; font-weight: 600; }
QLabel#hint { color: #6b7385; font-size: 9pt; }
QLabel#warn { background: #fff4e5; border: 1px solid #f3c98b; border-radius: 6px; padding: 8px 10px; }
QLabel#flashError { background: #fdecec; border: 1px solid #f0a9a9; color: #8a1f1f;
    border-radius: 6px; padding: 9px 12px; }
QFrame#panel { background: #ffffff; border: 1px solid #dde1e9; border-radius: 8px; }
QFrame#panel QLabel { background: transparent; }
QLabel#heroValue { font-size: 30pt; font-weight: 600; color: #2f5bd3; }
QLabel#tileValue { font-size: 16pt; font-weight: 600; }
QLabel#panelTitle { font-size: 11pt; font-weight: 600; }
QPushButton { background: #ffffff; border: 1px solid #c8cedb; border-radius: 6px; padding: 6px 14px; }
QPushButton:hover { border-color: #2f5bd3; }
QPushButton:disabled { color: #a0a7b5; border-color: #e1e4ea; }
QPushButton#primary { background: #2f5bd3; border-color: #2f5bd3; color: #ffffff; font-weight: 600; }
QPushButton#primary:hover { background: #2649ad; }
QPushButton#primary:disabled { background: #9db0e6; border-color: #9db0e6; }
QPushButton#danger { color: #b42318; }
QPushButton#danger:hover { border-color: #b42318; }
QPushButton#link { border: none; background: transparent; color: #2f5bd3; padding: 0;
    text-decoration: underline; }
QLineEdit, QPlainTextEdit, QComboBox, QDateEdit, QListWidget {
    background: #ffffff; border: 1px solid #c8cedb; border-radius: 6px; padding: 4px 6px; }
QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus, QDateEdit:focus, QListWidget:focus {
    border-color: #2f5bd3; }
QTableWidget { background: #ffffff; border: 1px solid #dde1e9; border-radius: 8px;
    gridline-color: #eceef3; selection-background-color: #dfe7fb; selection-color: #1d2433; }
QHeaderView::section { background: #f7f8fa; border: none; border-bottom: 1px solid #dde1e9;
    padding: 6px; font-weight: 600; color: #3c4457; }
QTabWidget::pane { border: none; }
QTabBar::tab { background: transparent; padding: 8px 16px; border: none;
    border-bottom: 2px solid transparent; color: #3c4457; }
QTabBar::tab:selected { border-bottom: 2px solid #2f5bd3; color: #1d2433; font-weight: 600; }
QScrollArea { border: none; background: transparent; }
QWidget#scrollBody { background: #f4f5f8; }
QFrame#formatBar { background: #ffffff; border: 1px solid #c8cedb; border-radius: 6px; }
QFrame#formatBar QToolButton { padding: 3px; border-radius: 4px; color: #3c4457; }
QFrame#formatBar QToolButton:hover { background: #eef1f7; }
QFrame#formatBar QToolButton:checked { background: #dfe7fb; color: #1d2433; }
QFrame#formatBar QComboBox { padding: 1px 4px; min-width: 44px; }
QTextEdit#richEdit { background: #ffffff; border: 1px solid #c8cedb; border-radius: 6px; padding: 4px 6px; }
QTextEdit#richEdit:focus { border-color: #2f5bd3; }
QFrame#sheetWrap { background: #e4e7ee; border-radius: 8px; }
QTextBrowser#sheet { background: #ffffff; border: 1px solid #d3d8e2; }
"""

KANBAN_STYLE = """
QDialog, QWidget#BoardCanvas, QWidget#ColumnPanel { background: #f4f5f8; }
QLabel#Heading { font-size: 16pt; font-weight: 600; }
QLabel#SectionTitle, QLabel#ColumnTitle { font-size: 11pt; font-weight: 600; }
QLabel#Muted, QLabel#Status { color: #6b7385; }
QTextEdit { background: #ffffff; border: 1px solid #c8cedb; border-radius: 6px; padding: 4px 6px; }
QTextEdit:focus { border-color: #2f5bd3; }
QPushButton[variant="primary"] { background: #2f5bd3; border-color: #2f5bd3; color: #ffffff; font-weight: 600; }
QPushButton[variant="primary"]:hover { background: #2649ad; }
QPushButton[variant="primary"]:disabled { background: #9db0e6; border-color: #9db0e6; }
QWidget#side QPushButton[variant="quiet"] { color: #c9d1e3; background: transparent; border: none; text-align: left; padding: 9px 14px; border-radius: 6px; font-size: 10.5pt; }
QWidget#side QPushButton[variant="quiet"]:hover { background: #2b3858; color: #ffffff; }
QListWidget#Boards { background: transparent; color: #c9d1e3; border: none; padding: 0; }
QListWidget#Boards::item { padding: 9px 14px; border-radius: 6px; margin: 2px 0; }
QListWidget#Boards::item:hover { background: #2b3858; color: #ffffff; }
QListWidget#Boards::item:selected { background: #3a4a72; color: #ffffff; font-weight: 600; }
QListWidget#Cards { background: transparent; border: none; padding: 0; }
QListWidget#Checklist::item { padding: 6px; }
QListWidget#Cards:focus { border: none; }
"""


def stylesheet():
    return REFERENCE_STYLE + KANBAN_STYLE
