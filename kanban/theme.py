from pathlib import Path

# Canonical runtime theme; mapped in docs/DESIGN.md.
TOKENS = {
    'background': '#ffffff', 'canvas': '#f5f6f8', 'sidebar': '#f7f7f5',
    'text': '#2e333b', 'muted': '#70757c', 'border': '#e3e6ea',
    'accent': '#2865cf', 'hover': '#edf1f7', 'selection': '#edf4ff',
    'danger': '#ac343a', 'scroll': '#bec4cd', 'accent_hover': '#2057b9',
}
PRIORITIES = {
    'Низкий': ('#617086', '#edf0f4'),
    'Обычный': ('#2865cf', '#eaf1ff'),
    'Высокий': ('#94600a', '#fff4da'),
    'Срочный': ('#ac343a', '#fdecee'),
}


def stylesheet():
    return '''
    QWidget {{ color: {text}; font-family: "Segoe UI", "DejaVu Sans"; font-size: 13px; }}
    QMainWindow, QDialog {{ background: {background}; }}
    QWidget#Sidebar {{ background: {sidebar}; border-right: 1px solid {border}; }}
    QWidget#BoardCanvas, QWidget#ColumnPanel {{ background: {canvas}; }}
    QLabel#Heading {{ font-size: 25px; font-weight: 600; padding: 8px 0; }}
    QLabel#SectionTitle {{ font-size: 14px; font-weight: 600; }}
    QLabel#Muted {{ color: {muted}; }}
    QLabel#Status {{ color: {muted}; min-height: 24px; padding: 4px 0; }}
    QLabel#ColumnTitle {{ font-size: 13px; font-weight: 600; padding: 3px 0; }}
    QPushButton {{ background: {background}; border: 1px solid {border}; border-radius: 6px; padding: 7px 12px; min-height: 20px; }}
    QPushButton:hover {{ background: {hover}; border-color: {scroll}; }}
    QPushButton:pressed {{ background: {border}; }}
    QPushButton[variant="primary"] {{ background: {accent}; border-color: {accent}; color: white; font-weight: 600; }}
    QPushButton[variant="primary"]:hover {{ background: {accent_hover}; }}
    QPushButton[variant="quiet"] {{ border-color: transparent; background: transparent; text-align: left; }}
    QPushButton[variant="quiet"]:hover {{ background: {hover}; }}
    QPushButton:focus, QLineEdit:focus, QTextEdit:focus, QComboBox:focus, QDateEdit:focus {{ border-color: {accent}; }}
    QPushButton:disabled {{ color: {muted}; background: {canvas}; border-color: {border}; }}
    QLineEdit, QTextEdit, QComboBox, QDateEdit {{ background: {background}; border: 1px solid {border}; padding: 7px 9px; border-radius: 6px; selection-background-color: {accent}; }}
    QLineEdit#TaskTitle {{ font-size: 19px; font-weight: 600; padding: 10px; }}
    QComboBox::drop-down, QDateEdit::drop-down {{ width: 28px; border: none; }}
    QComboBox::down-arrow, QDateEdit::down-arrow {{ image: url("{arrow}"); width: 16px; height: 16px; }}
    QDateEdit:disabled {{ color: {muted}; background: {canvas}; }}
    QComboBox QAbstractItemView {{ background: {background}; border: 1px solid {border}; selection-background-color: {selection}; selection-color: {text}; padding: 4px; outline: none; }}
    QComboBox QAbstractItemView::item {{ min-height: 30px; padding: 3px 8px; }}
    QListWidget, QTableWidget {{ background: {background}; border: 1px solid {border}; border-radius: 6px; outline: none; }}
    QListWidget::item {{ padding: 10px; border-bottom: 1px solid {border}; }}
    QListWidget::item:hover {{ background: {hover}; }}
    QListWidget::item:selected {{ background: {selection}; color: {text}; }}
    QListWidget#Boards {{ background: transparent; border: none; }}
    QListWidget#Boards::item {{ border: none; border-radius: 5px; margin: 2px 0; padding: 10px 12px; }}
    QListWidget#Cards {{ background: transparent; border: none; }}
    QListWidget#Checklist::item {{ padding: 7px; }}
    QScrollArea {{ border: none; background: {background}; }}
    QScrollArea > QWidget > QWidget {{ background: {background}; }}
    QTabWidget::pane {{ border: none; border-top: 1px solid {border}; }}
    QTabBar::tab {{ color: {muted}; padding: 11px 17px; background: {background}; border-bottom: 2px solid transparent; }}
    QTabBar::tab:selected {{ color: {accent}; border-bottom-color: {accent}; }}
    QCheckBox {{ spacing: 7px; }}
    QCheckBox::indicator {{ width: 16px; height: 16px; }}
    QCalendarWidget QWidget {{ alternate-background-color: {canvas}; }}
    QCalendarWidget QWidget#qt_calendar_navigationbar {{ background: {canvas}; }}
    QCalendarWidget QToolButton {{ color: {text}; background: {canvas}; padding: 6px; border: none; }}
    QCalendarWidget QAbstractItemView {{ selection-background-color: {accent}; selection-color: white; }}
    QScrollBar:vertical {{ background: transparent; width: 12px; margin: 0; }}
    QScrollBar:horizontal {{ background: {canvas}; height: 12px; margin: 0; }}
    QScrollBar::handle {{ background: {scroll}; border-radius: 5px; min-height: 30px; min-width: 30px; }}
    QScrollBar::handle:hover {{ background: {muted}; }}
    QScrollBar::handle:pressed {{ background: {text}; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
    '''.format(arrow=(Path(__file__).resolve().parent / 'assets/chevron.png').as_posix(), **TOKENS)
