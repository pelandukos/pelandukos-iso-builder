"""Shared PelandukOS desktop styling and action icons."""
from pathlib import Path
from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QPushButton, QFormLayout, QTableWidget, QAbstractItemView, QStyle, QComboBox, QListView

RESOURCES = Path(__file__).resolve().parents[1] / 'resources'


def icon(name):
    return QIcon(str(RESOURCES / 'icons' / (name + '.svg')))


def polish(window):
    actions = {'open':'folder', 'browse':'folder', 'choose':'folder', 'import':'download',
               'export':'download', 'save':'save', 'apply':'check', 'build':'build',
               'start':'play', 'live':'play', 'stop':'stop', 'shut':'stop', 'new':'plus',
               'create':'plus', 'delete':'trash', 'remove':'trash', 'discard':'trash',
               'search':'search', 'find':'search', 'detect':'refresh', 'refresh':'refresh',
               'check':'check', 'restore':'refresh', 'installed':'package', 'install':'package',
               'upgrade':'download', 'settings':'settings', 'copy':'files', 'move':'folder',
               'folder':'folder', 'text':'files', 'permissions':'settings', 'ownership':'settings'}
    for button in window.findChildren(QPushButton):
        first = button.text().lower().split(' ')[0].rstrip('….')
        name = actions.get(first)
        if name:
            button.setIcon(icon(name + ('-white' if button.objectName() == 'primary' else '')))
        button.setIconSize(QSize(16, 16))
        button.setCursor(Qt.PointingHandCursor)
        button.setMinimumHeight(36)
    for combo in window.findChildren(QComboBox):
        # A widget popup uses the same palette and item styling as the field,
        # rather than inheriting conflicting native menu colours.
        view = QListView(combo)
        view.setUniformItemSizes(True)
        view.setSpacing(2)
        combo.setView(view)
        combo.setMaxVisibleItems(10)
    for form in window.findChildren(QFormLayout):
        form.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        form.setFormAlignment(Qt.AlignTop)
        form.setHorizontalSpacing(20)
        form.setVerticalSpacing(9)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        for row in range(form.rowCount()):
            item = form.itemAt(row, QFormLayout.LabelRole)
            if item and item.widget():
                item.widget().setMinimumWidth(140)
    for table in window.findChildren(QTableWidget):
        table.horizontalHeader().setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        table.setShowGrid(False)
        table.setAlternatingRowColors(True)
        table.verticalHeader().hide()
        table.verticalHeader().setDefaultSectionSize(36)
        table.setFocusPolicy(Qt.StrongFocus)


def stylesheet(dark):
    canvas, sidebar, card, line, hover = (('#1d1e22','#26272c','#2e3036','#41434a','#383a42') if dark
                                       else ('#f5f5f7','#ebedf1','#ffffff','#dcdfe5','#edf0f5'))
    disabled = '#a0a3aa' if dark else '#737780'
    foreground = '#f1f2f5' if dark else '#292c34'
    button_top = '#36383f' if dark else '#ffffff'
    button_bottom = '#2c2e34' if dark else '#f8f9fb'
    arrow = (RESOURCES / 'icons' / ('chevron-down-light.svg' if dark else 'chevron-down-dark.svg')).as_posix()
    up_arrow = (RESOURCES / 'icons' / ('chevron-up-light.svg' if dark else 'chevron-up-dark.svg')).as_posix()
    return f'''
        QWidget {{ font-size:13px; }}
        QMainWindow {{ background:{canvas}; }}
        QWidget#navigation {{ background:{sidebar}; border-right:1px solid {line}; }}
        QWidget#workspace, QWidget#page, QScrollArea#pageScroll {{ background:{canvas}; border:0; }}
        QLabel#brand {{ font-size:18px; font-weight:650; }}
        QLabel#brandCaption {{ font-size:11px; letter-spacing:2px; color:palette(mid); }}
        QLabel#heading {{ font-size:25px; font-weight:650; padding:0 0 12px; }}
        QLabel#notice {{ color:palette(window-text); padding:8px 0; }}
        QLabel#projectBadge {{ padding:12px; border-radius:10px; background:{card}; }}
        QLabel#wallpaperPreview {{ background:{card}; border:1px solid {line}; border-radius:12px; color:palette(mid); }}
        QGroupBox {{ background:{card}; border:1px solid {line}; border-radius:12px;
                    margin-top:12px; padding:18px 16px 14px; font-weight:600; }}
        QGroupBox::title {{ subcontrol-origin:margin; left:16px; padding:0 6px; }}
        QLineEdit, QComboBox, QSpinBox {{ color:{foreground}; background:{card}; border:1px solid {line};
                    border-radius:7px; padding:7px 10px; min-height:18px; selection-background-color:#e67b25; }}
        QComboBox {{ padding-right:36px; border-radius:9px; }}
        QComboBox:hover:enabled {{ border-color:#a9adb6; }}
        QComboBox:on {{ border-color:#e8873a; }}
        QComboBox::drop-down {{ subcontrol-origin:padding; subcontrol-position:top right;
                    width:30px; border:0; border-top-right-radius:9px; border-bottom-right-radius:9px; }}
        QComboBox::down-arrow {{ image:url("{arrow}"); width:14px; height:14px; }}
        QComboBox QAbstractItemView {{ color:{foreground}; background:{card};
                    border:1px solid {line}; border-radius:9px; padding:5px; outline:0;
                    selection-background-color:#d96a13; selection-color:#ffffff; }}
        QComboBox QAbstractItemView::item {{ min-height:30px; padding:3px 10px; border-radius:6px;
                    color:{foreground}; background:{card}; }}
        QComboBox QAbstractItemView::item:selected {{ background:#d96a13; color:#ffffff; }}
        QComboBox QAbstractItemView::item:disabled {{ color:{disabled}; }}
        QSpinBox {{ padding-right:30px; border-radius:9px; }}
        QSpinBox::up-button {{ subcontrol-origin:border; subcontrol-position:top right;
                    width:26px; height:18px; border:0; border-top-right-radius:9px; background:{hover}; }}
        QSpinBox::down-button {{ subcontrol-origin:border; subcontrol-position:bottom right;
                    width:26px; height:18px; border:0; border-bottom-right-radius:9px; background:{hover}; }}
        QSpinBox::up-arrow {{ image:url("{up_arrow}"); width:11px; height:11px; }}
        QSpinBox::down-arrow {{ image:url("{arrow}"); width:11px; height:11px; }}
        QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{ border:1px solid #e8873a; }}
        QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled {{ color:{disabled}; placeholder-text-color:{disabled}; background:{canvas}; }}
        QPushButton {{ color:{foreground}; background:qlineargradient(x1:0,y1:0,x2:0,y2:1,
                    stop:0 {button_top},stop:1 {button_bottom}); border:1px solid {line};
                    border-radius:9px; padding:6px 14px; font-weight:500; }}
        QPushButton:focus:enabled {{ border:1px solid #e8873a; }}
        QPushButton:hover:enabled {{ background:{hover}; border-color:#b9bdc6; }}
        QPushButton:pressed:enabled {{ background:{sidebar}; }}
        QPushButton:disabled, QPushButton#activity:disabled {{ color:{disabled}; background:{canvas}; }}
        QPushButton#primary:enabled {{ background:#d96a13; color:white; border:1px solid #c75b08; font-weight:600; }}
        QPushButton#primary:hover:enabled {{ background:#e77b26; }}
        QPushButton#activity {{ border:0; background:transparent; text-align:left; color:{foreground}; }}
        QListWidget {{ border:1px solid {line}; border-radius:10px; background:{card}; padding:5px; }}
        QListWidget#sidebar {{ background:transparent; border:0; padding:0; outline:0; }}
        QListWidget#sidebar::item {{ padding:10px 12px; margin:3px 0; border-radius:9px; }}
        QListWidget#sidebar::item:selected {{ background:{card}; color:palette(text); font-weight:600; }}
        QListWidget#sidebar::item:hover:!selected {{ background:{hover}; }}
        QListWidget#sidebar::item:disabled, QListWidget#sidebar::item:disabled:selected {{ color:{disabled}; }}
        QLabel:disabled, QCheckBox:disabled, QGroupBox:disabled, QPlainTextEdit:disabled, QTableWidget:disabled {{ color:{disabled}; }}
        QPlainTextEdit, QTableWidget {{ background:{card}; border:1px solid {line}; border-radius:9px; padding:8px; }}
        QHeaderView::section {{ background:{sidebar}; border:0; border-bottom:1px solid {line}; padding:9px; font-weight:600; }}
        QTableWidget::item {{ padding:5px; }}
        QSplitter::handle {{ background:{line}; width:1px; height:1px; }}
        QProgressBar {{ border:1px solid {line}; border-radius:6px; background:{card}; text-align:center; min-height:18px; }}
        QProgressBar::chunk {{ background:#e8873a; border-radius:5px; }}
        QStatusBar {{ background:{canvas}; border-top:1px solid {line}; padding:5px 12px; }}
        QScrollBar:vertical {{ background:transparent; width:9px; margin:2px; }}
        QScrollBar::handle:vertical {{ background:{line}; border-radius:4px; min-height:30px; }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height:0; }}
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background:transparent; }}
    '''
