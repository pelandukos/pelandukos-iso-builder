"""Unprivileged folder selection; the helper independently validates storage."""
import os
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QMessageBox, QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

from core.errors import BuilderError
from core.release_manager import validate_version
from core.storage import filesystem_info, require_linux_filesystem


class StoragePicker(QWidget):
    def __init__(self, folder="", parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        self.folder = QLineEdit(folder)
        self.folder.setPlaceholderText("System default")
        row.addWidget(self.folder, 1)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self.browse)
        row.addWidget(browse)
        reset = QPushButton("System default")
        reset.clicked.connect(self.folder.clear)
        row.addWidget(reset)
        layout.addLayout(row)
        self.info = QLabel()
        self.info.setWordWrap(True)
        self.info.setTextFormat(Qt.PlainText)
        layout.addWidget(self.info)
        self.container_options = QWidget()
        form = QFormLayout(self.container_options)
        self.container_size = QSpinBox()
        self.container_size.setRange(4, 16384)
        self.container_size.setValue(128)
        self.container_size.setSuffix(" GiB")
        self.container_size.setToolTip("Reserved when creating a new ext4 container. Existing containers are reused.")
        form.addRow("New exFAT container capacity", self.container_size)
        layout.addWidget(self.container_options)
        self.folder.textChanged.connect(self.refresh)
        self.refresh()

    def browse(self):
        chosen = QFileDialog.getExistingDirectory(
            self, "Choose project storage folder", self.folder.text() or str(Path.home()))
        if chosen:
            self.folder.setText(chosen)

    def value(self):
        return self.folder.text().strip()

    def inspect(self):
        default = Path("/var/lib/pelandukos-iso-builder")
        path = self.value() or str(default if default.exists() else default.parent)
        if not Path(path).is_absolute():
            raise BuilderError("Choose an absolute folder path.")
        # O_PATH permits capacity inspection of the private default store
        # without granting the GUI access to its contents.
        fd = os.open(path, os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            return filesystem_info(fd)
        finally:
            os.close(fd)

    def refresh(self):
        try:
            info = self.inspect()
            self.container_options.setVisible(info["filesystem"] == "exFAT")
            message = (f"{info['free_bytes'] / 1024**3:.1f} GiB free of "
                       f"{info['total_bytes'] / 1024**3:.1f} GiB • {info['filesystem']}. ")
            if info["filesystem"] == "exFAT":
                message += "Supported using an ext4 container"
            elif not info["supported"]:
                message += "Unsupported for project storage. Choose ext4, XFS or Btrfs."
            elif info["temporary"]:
                message += "Temporary storage: projects will be lost after reboot."
            else:
                message = message.rstrip(". ")
            self.info.setText(message)
        except (OSError, BuilderError) as error:
            self.info.setText(str(error))

    def validate(self):
        info = self.inspect()
        if info["filesystem"] != "exFAT":
            require_linux_filesystem(info)
        if self.value() and not os.access(self.value(), os.W_OK | os.X_OK):
            raise BuilderError("Select a folder you can write to.")
        return info


class ProjectDialog(QDialog):
    def __init__(self, parent=None, folder="", *, moving=False):
        super().__init__(parent)
        self.setWindowTitle("Move project" if moving else "Create project")
        self.resize(680, 260)
        layout = QVBoxLayout(self)
        self.name = QLineEdit()
        self.version = QLineEdit("0.1")
        self.version.setPlaceholderText("e.g. 1.1 or 3.0-beta1")
        self.version.setMaxLength(64)
        if not moving:
            form = QFormLayout()
            form.addRow("Project name", self.name)
            form.addRow("Release version", self.version)
            layout.addLayout(form)
        layout.addWidget(QLabel("Project storage folder"))
        self.storage = StoragePicker(folder)
        layout.addWidget(self.storage)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.validate)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.moving = moving

    def validate(self):
        try:
            if not self.moving and not 1 <= len(self.name.text().strip()) <= 120:
                raise BuilderError("Enter a project name between 1 and 120 characters.")
            if not self.moving:
                validate_version(self.version.text())
            self.storage.validate()
        except (OSError, BuilderError) as error:
            QMessageBox.warning(self, "Select project storage", str(error))
            return
        self.accept()
