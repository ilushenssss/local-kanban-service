# Shared visual tokens. See DESIGN.md.
TOKENS = {'background': '#ffffff', 'sidebar': '#f7f7f5', 'text': '#37352f', 'muted': '#787774', 'border': '#e6e6e3', 'accent': '#2383e2', 'hover': '#efefed'}

def stylesheet():
    return '''
    QWidget {{ color: {text}; font-family: "Segoe UI", "DejaVu Sans"; font-size: 13px; }}
    QMainWindow, QDialog {{ background: {background}; }}
    QWidget#Sidebar {{ background: {sidebar}; border-radius: 6px; }}
    QLabel#Heading {{ font-size: 23px; font-weight: 600; padding: 10px 0; }}
    QPushButton {{ background: {background}; border: 1px solid {border}; border-radius: 4px; padding: 7px 10px; min-height: 18px; }}
    QPushButton:hover {{ background: {hover}; }}
    QPushButton:pressed {{ background: {border}; }}
    QPushButton:focus, QLineEdit:focus, QTextEdit:focus, QComboBox:focus, QListWidget:focus {{ border: 2px solid {accent}; }}
    QPushButton:disabled {{ color: {muted}; background: {sidebar}; }}
    QLineEdit, QTextEdit, QComboBox {{ background: {background}; border: 1px solid {border}; padding: 6px; border-radius: 4px; selection-background-color: {accent}; }}
    QListWidget {{ background: {sidebar}; border: 1px solid {border}; border-radius: 5px; }}
    QListWidget::item {{ background: {background}; padding: 11px; border: 1px solid {border}; border-radius: 4px; }}
    QListWidget::item:hover {{ background: {hover}; }}
    QListWidget::item:selected {{ background: #e6f0fb; border-color: {accent}; color: {text}; }}
    QScrollArea {{ border: none; background: {background}; }}
    QTabWidget::pane {{ border: 1px solid {border}; }}
    QTabBar::tab {{ padding: 9px 14px; background: {sidebar}; }}
    QTabBar::tab:selected {{ background: {background}; border-bottom: 2px solid {accent}; }}
    QScrollBar:vertical {{ background: {sidebar}; width: 14px; }}
    QScrollBar:horizontal {{ background: {sidebar}; height: 14px; }}
    QScrollBar::handle {{ background: #b4b4b0; border-radius: 4px; min-height: 28px; min-width: 28px; }}
    QScrollBar::handle:hover {{ background: {muted}; }}
    QScrollBar::handle:pressed {{ background: {text}; }}
    '''.format(**TOKENS)
