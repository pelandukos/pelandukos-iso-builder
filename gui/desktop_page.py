"""Capability-driven editor for offline target desktop defaults."""
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QImageReader, QPixmap
from PySide6.QtWidgets import (
    QComboBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QPushButton, QVBoxLayout, QWidget,
)


class DesktopEditor(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window = window
        self.project_id = None
        self.capabilities = {}
        self.dirty = False
        self.source = ""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        self.status = QLabel("Desktop settings")
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.PlainText)
        row.addWidget(self.status, 1)
        self.detect = QPushButton("Detect")
        self.detect.clicked.connect(self.inspect)
        row.addWidget(self.detect)
        layout.addLayout(row)
        self.form_widget = QWidget()
        form = QFormLayout(self.form_widget)
        self.desktop = QComboBox()
        form.addRow("Target desktop", self.desktop)
        self.fields = {}
        for key, title in (("gtk_theme", "GTK theme"), ("icon_theme", "Icon theme"),
                           ("cursor_theme", "Cursor theme")):
            widget = QComboBox()
            widget.addItem("Keep existing", "")
            widget.currentIndexChanged.connect(self.changed)
            form.addRow(title, widget)
            self.fields[key] = widget
        self.font = QLineEdit()
        self.font.setPlaceholderText("Default font")
        self.font.textChanged.connect(self.changed)
        form.addRow("Default font", self.font)
        self.fields["font"] = self.font
        self.wallpaper_row = QWidget()
        row = QHBoxLayout(self.wallpaper_row)
        row.setContentsMargins(0, 0, 0, 0)
        self.wallpaper_name = QLabel("Default wallpaper")
        self.wallpaper_name.setWordWrap(True)
        self.wallpaper_name.setTextFormat(Qt.PlainText)
        row.addWidget(self.wallpaper_name, 1)
        choose = QPushButton("Choose image…")
        choose.clicked.connect(self.choose_wallpaper)
        row.addWidget(choose)
        clear = QPushButton("Clear selection")
        clear.clicked.connect(self.clear_wallpaper)
        row.addWidget(clear)
        form.addRow("Wallpaper", self.wallpaper_row)
        layout.addWidget(self.form_widget)
        self.preview = QLabel("Wallpaper")
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setMinimumHeight(110)
        self.preview.setObjectName("wallpaperPreview")
        layout.addWidget(self.preview, 1)
        self.note = QLabel()
        self.note.setWordWrap(True)
        self.note.setTextFormat(Qt.PlainText)
        self.note.hide()
        self.apply = QPushButton("Apply appearance")
        self.apply.setObjectName("primary")
        self.apply.clicked.connect(self.save)
        layout.addWidget(self.apply)
        self.desktop.currentIndexChanged.connect(self.select_desktop)
        self.form_widget.setEnabled(False)
        self.apply.setEnabled(False)

    def changed(self, *_):
        fields = self.capabilities.get("desktops", {}).get(self.desktop.currentText(), {}).get("fields", [])
        self.dirty = bool(self.source and "wallpaper" in fields) or any(
            (widget.text().strip() if key == "font" else widget.currentData())
            for key, widget in self.fields.items() if key in fields)
        self.apply.setEnabled(self.dirty)

    def reset(self):
        self.project_id = None
        self.capabilities = {}
        self.desktop.clear()
        self.clear_wallpaper()
        self.font.clear()
        self.form_widget.setEnabled(False)
        self.apply.setEnabled(False)
        self.dirty = False
        self.status.setText("Desktop settings")
        self.note.clear()

    def inspect(self):
        if self.dirty and QMessageBox.question(
                self, "Unsaved desktop choices", "Discard the choices in this form and detect again?",
                QMessageBox.Discard | QMessageBox.Cancel, QMessageBox.Cancel) != QMessageBox.Discard:
            return
        self.window.submit("desktop_inspect", self.loaded)

    def loaded(self, result):
        self.capabilities = result
        self.project_id = self.window.project["id"]
        self.desktop.blockSignals(True)
        self.desktop.clear()
        for name, info in result["desktops"].items():
            if info["fields"]:
                self.desktop.addItem(name)
        self.desktop.blockSignals(False)
        for key in ("gtk_theme", "icon_theme", "cursor_theme"):
            combo = self.fields[key]
            combo.clear()
            combo.addItem("Keep existing", "")
            for name in result[key]:
                combo.addItem(name, name)
        self.font.clear()
        self.clear_wallpaper()
        self.select_desktop()
        self.form_widget.setEnabled(self.desktop.count() > 0)
        notes = [f"{name}: {info['note']}" for name, info in result["desktops"].items() if not info["fields"]]
        self.status.setText("Appearance" if self.desktop.count()
                            else "\n".join(notes) or "No supported desktop detected. You can still edit files and build.")
        if result.get("saved"):
            self.status.setText(self.status.text() + "\nPreviously applied: " +
                                ", ".join(result["saved"]["settings"]))
        self.dirty = False
        self.apply.setEnabled(False)

    def select_desktop(self, *_):
        info = self.capabilities.get("desktops", {}).get(self.desktop.currentText(), {})
        fields = info.get("fields", [])
        for name, widget in self.fields.items():
            widget.setEnabled(name in fields)
        self.wallpaper_row.setEnabled("wallpaper" in fields)
        self.note.setText((info.get("note", "") + "\n\n" if info.get("note") else "") +
                         "Sets defaults inside the ISO. Existing user settings or distribution policies may override "
                         "them. Fonts must already be installed in the target. Build and boot to verify the desktop.")
        self.changed()

    def clear_wallpaper(self):
        self.source = ""
        self.wallpaper_name.setText("Default wallpaper")
        self.preview.clear()
        self.preview.setText("Wallpaper")
        self.changed()

    def choose_wallpaper(self):
        filename, _ = QFileDialog.getOpenFileName(self, "Choose ISO wallpaper", "",
                                                  "Wallpaper (*.png *.jpg *.jpeg)")
        if not filename:
            return
        try:
            if Path(filename).stat().st_size > 32 * 1024**2:
                raise ValueError("Choose a PNG or JPEG image up to 32 MiB.")
            reader = QImageReader(filename)
            size = reader.size()
            if not size.isValid() or size.width() * size.height() > 64_000_000:
                raise ValueError("Wallpaper must be a valid image up to 64 megapixels.")
            reader.setScaledSize(size.scaled(520, 220, Qt.KeepAspectRatio))
            image = reader.read()
            if image.isNull():
                raise ValueError("Cannot read this wallpaper image.")
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "Wallpaper", str(error))
            return
        self.source = str(Path(filename).absolute())
        self.wallpaper_name.setText(Path(filename).name)
        self.preview.setPixmap(QPixmap.fromImage(image))
        self.changed()

    def save(self):
        fields = self.capabilities.get("desktops", {}).get(self.desktop.currentText(), {}).get("fields", [])
        values = {"desktop": self.desktop.currentText()}
        for key, widget in self.fields.items():
            value = widget.text().strip() if key == "font" else widget.currentData()
            if key in fields and value:
                values[key] = value
        source = self.source if "wallpaper" in fields else ""
        if len(values) == 1 and not source:
            QMessageBox.information(self, "Desktop settings", "Choose a wallpaper or change an appearance setting first.")
            return
        descriptions = [f"{key.replace('_', ' ').title()}: {value}" for key, value in values.items()]
        if source:
            descriptions.append("Wallpaper: " + Path(source).name)
        if QMessageBox.question(
                self, "Apply desktop defaults", "\n".join(descriptions) +
                "\n\nApply to the extracted ISO? Existing configuration files will be backed up in this project.",
                QMessageBox.Apply | QMessageBox.Cancel, QMessageBox.Cancel) != QMessageBox.Apply:
            return
        def saved(project):
            self.window.set_project(project)
            self.dirty = False
            self.apply.setEnabled(False)
            self.status.setText("Appearance saved")
        self.window.submit("desktop_apply", saved, values=values, source=source, confirmed=True)
