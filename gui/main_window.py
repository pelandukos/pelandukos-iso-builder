import json
import tempfile
import time
from pathlib import Path, PurePosixPath

from PySide6.QtCore import Qt, QSettings, QTimer, QSize
from PySide6.QtGui import QAction, QKeySequence, QIcon, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QComboBox, QFileDialog, QFormLayout, QHBoxLayout,
    QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow,
    QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QSplitter, QStackedWidget, QGroupBox,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget, QCheckBox, QScrollArea, QFrame,
)

from core.dependencies import check
from core.errors import BuilderError
from core.release_manager import suggested_filename, validate_version
from gui.preview_widget import Console
from gui.design import icon as ui_icon, polish, stylesheet, RESOURCES
from gui.desktop_page import DesktopEditor
from gui.live_settings import LiveSettings
from gui.storage_dialog import ProjectDialog, StoragePicker
from services.job_manager import JobManager
from services.preview_manager import PreviewManager


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("PelandukOS ISO Builder")
        self.resize(1280, 850)
        icon = Path(__file__).resolve().parents[1] / "resources/pelandukos-iso-builder.svg"
        self.setWindowIcon(QIcon(str(icon)))
        self.settings = QSettings()
        self.project = None
        self.busy = False
        self.job_failed = False
        self.directory = ""
        self.edit_path = None
        self.edit_is_new = False
        self.export_temporary = None
        self.live_editor = None
        self.after_job = []
        self.jobs = JobManager(self)
        self.preview = PreviewManager(self)
        self.sidebar = QListWidget()
        self.sidebar.setFixedWidth(208)
        self.sidebar.setObjectName("sidebar")
        self.sidebar.setIconSize(QSize(22, 22))
        self.stack = QStackedWidget()
        self.pages = {}
        self.logs = QPlainTextEdit()
        self.logs.setReadOnly(True)
        self.logs.document().setMaximumBlockCount(2000)
        self.logs.setPlaceholderText("Activity")
        self.preview.log.connect(self.append_log)
        self.stage = QLabel("Ready")
        self.stage.setWordWrap(True)
        self.stage.setMinimumWidth(0)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setFormat("Ready")
        self.progress.setFixedWidth(205)
        self.progress.setToolTip("Measured progress for the current stage, not an estimated overall job percentage.")
        self.job_started = 0
        self.stage_message = ""
        self.stage_detail = ""
        self.elapsed_timer = QTimer(self)
        self.elapsed_timer.setInterval(1000)
        self.elapsed_timer.timeout.connect(self.update_job_status)
        self.statusBar().addWidget(self.stage, 1)
        self.statusBar().addPermanentWidget(self.progress)
        self.make_projects()
        layout = self.page("Desktop Customizer", "Choose wallpaper and appearance defaults for the desktop inside your ISO.")
        live_actions = QHBoxLayout()
        self.button("Live Desktop", self.start_live_edit, live_actions)
        self.live_network = QCheckBox("Network")
        self.live_network.setToolTip("Allow the VM to reach the Internet and local network through QEMU NAT.")
        live_actions.addWidget(self.live_network)
        layout.addLayout(live_actions)
        self.live_settings = LiveSettings(self.settings, self)
        layout.addWidget(self.live_settings)
        self.desktop_editor = DesktopEditor(self)
        layout.addWidget(self.desktop_editor, 1)
        from gui.package_page import PackagePage
        layout = self.page("Package Manager", "Manage packages in the extracted target, using an isolated VM.")
        self.package_page = PackagePage(self)
        layout.addWidget(self.package_page, 1)
        self.make_files()
        self.make_build()
        self.make_release()
        # Release fields use build-version widgets; display the release step first.
        release_page = self.pages["Release Manager"]
        self.stack.removeWidget(release_page)
        self.stack.insertWidget(4, release_page)
        self.sidebar.insertItem(4, self.sidebar.takeItem(5))
        self.make_preview()
        self.make_settings()
        for index, (title, description) in enumerate((
            ("Source ISO", "Create a project and import an ISO"),
            ("Appearance", "Wallpaper and appearance"),
            ("Packages", "Search, install, remove and upgrade target packages"),
            ("Files", "Edit files in the ISO"),
            ("Release", "Name, version and release notes"),
            ("Build & export", "Create the new ISO"),
            ("Test ISO", "Boot the result in QEMU"),
            ("Settings", "Storage and system dependencies"),
        )):
            item = self.sidebar.item(index)
            item.setText(title)
            item.setToolTip(description)
            item.setIcon(ui_icon(("disc", "desktop", "package", "files", "release", "build", "play", "settings")[index]))
        self.sidebar.currentRowChanged.connect(self.navigate)
        self.sidebar.setCurrentRow(0)
        body = QSplitter(Qt.Horizontal)
        navigation = QWidget()
        navigation.setObjectName("navigation")
        nav = QVBoxLayout(navigation)
        nav.setContentsMargins(16, 24, 16, 16)
        nav.setSpacing(12)
        logo = QLabel()
        logo.setPixmap(QPixmap(str(RESOURCES / "pelandukos-logo.png")).scaled(136, 136, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        logo.setAlignment(Qt.AlignCenter)
        logo.setAccessibleName("PelandukOS logo")
        nav.addWidget(logo)
        brand = QLabel("ISO Builder")
        brand.setObjectName("brand")
        brand.setAlignment(Qt.AlignCenter)
        nav.addWidget(brand)
        caption = QLabel("PELANDUKOS")
        caption.setObjectName("brandCaption")
        caption.setAlignment(Qt.AlignCenter)
        nav.addWidget(caption)
        nav.addWidget(self.sidebar, 1)
        self.project_badge = QLabel("No project")
        self.project_badge.setObjectName("projectBadge")
        self.project_badge.setWordWrap(True)
        self.project_badge.setTextFormat(Qt.PlainText)
        nav.addWidget(self.project_badge)
        body.addWidget(navigation)
        right = QWidget()
        right.setObjectName("workspace")
        content = QVBoxLayout(right)
        content.setContentsMargins(12, 8, 12, 12)
        content.setSpacing(10)
        content.addWidget(self.stack, 1)
        footer = QHBoxLayout()
        self.back = self.button("← Back", lambda: self.sidebar.setCurrentRow(self.sidebar.currentRow() - 1), footer)
        self.step_hint = QLabel()
        footer.addWidget(self.step_hint, 1)
        self.next_step = self.button("Next →", lambda: self.sidebar.setCurrentRow(self.sidebar.currentRow() + 1), footer)
        self.next_step.setObjectName("primary")
        content.addLayout(footer)
        self.log_toggle = QPushButton("Activity")
        self.log_toggle.setObjectName("activity")
        self.log_toggle.setCheckable(True)
        self.log_toggle.toggled.connect(self.logs.setVisible)
        content.addWidget(self.log_toggle)
        self.logs.setMaximumHeight(180)
        self.logs.hide()
        content.addWidget(self.logs)
        body.addWidget(right)
        body.setChildrenCollapsible(False)
        navigation.setFixedWidth(240)
        body.setStretchFactor(1, 1)
        self.setCentralWidget(body)
        for title, shortcut, slot in (
            ("New project", QKeySequence.New, self.new_project),
            ("Open project", QKeySequence.Open, self.open_pointer),
            ("Save changes", QKeySequence.Save, self.save_current),
        ):
            action = QAction(title, self)
            action.setShortcut(shortcut)
            action.triggered.connect(slot)
            self.addAction(action)
        polish(self)
        self.apply_theme("System")
        QApplication.instance().paletteChanged.connect(self.system_palette_changed)
        self.update_steps()

    def page(self, title, subtitle):
        page = QWidget()
        page.setObjectName("page")
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setObjectName("pageScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        content = QWidget()
        content.setObjectName("page")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(20, 20, 20, 16)
        layout.setSpacing(16)
        scroll.setWidget(content)
        outer.addWidget(scroll)
        heading = QLabel({"ISO Projects": "Source ISO", "Desktop Customizer": "Desktop appearance",
                          "File Manager": "Files", "Release Manager": "Release",
                          "Build ISO": "Build & export", "Live Preview": "Test ISO"}.get(title, title))
        heading.setObjectName("heading")
        heading.setFixedHeight(48)
        heading.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        layout.addWidget(heading)
        self.pages[title] = page
        item = QListWidgetItem(title)
        item.setData(Qt.UserRole, title)
        self.sidebar.addItem(item)
        self.stack.addWidget(page)
        return layout

    def navigate(self, index):
        self.stack.setCurrentIndex(index)
        item = self.sidebar.item(index)
        if hasattr(self, "next_step"):
            self.update_steps()
        if (item and item.data(Qt.UserRole) == "File Manager" and self.project
                and self.project["state"] == "ready" and self.files.rowCount() == 0):
            self.load_directory(self.directory)
        if (item and item.data(Qt.UserRole) == "Desktop Customizer" and self.project
                and self.project["state"] == "ready" and self.desktop_editor.project_id != self.project["id"]):
            self.desktop_editor.inspect()
        if (item and item.data(Qt.UserRole) == "Package Manager" and self.project
                and self.project["state"] == "ready" and self.package_page.project_id != self.project["id"]):
            self.package_page.reload()

    def update_steps(self):
        ready = bool(self.project and self.project["state"] == "ready")
        for index in range(1, 6):
            item = self.sidebar.item(index)
            enabled = ready or (index == 4 and self.project is not None)
            item.setFlags(item.flags() | Qt.ItemIsEnabled if enabled else item.flags() & ~Qt.ItemIsEnabled)
        index = self.sidebar.currentRow()
        self.back.setEnabled(not self.busy and index > 0)
        following = index < 6 and bool(self.sidebar.item(index + 1).flags() & Qt.ItemIsEnabled)
        self.next_step.setEnabled(not self.busy and following)
        self.step_hint.setText(f"Step {index + 1} of 7" if index < 7 else "Application settings")
        self.import_button.setEnabled(bool(self.project and not ready))
        self.build_button.setEnabled(ready)
        self.export_button.setEnabled(bool(self.project and self.project.get("last_build")))
        if not self.project:
            self.source_hint.setText("No source ISO")
        elif not ready:
            self.source_hint.setText("Awaiting ISO")
        else:
            self.source_hint.setText("Source ready")

    @staticmethod
    def button(text, function, layout, tooltip=None):
        button = QPushButton(text)
        button.clicked.connect(function)
        button.setToolTip(tooltip or text)
        layout.addWidget(button)
        return button

    def make_projects(self):
        layout = self.page("ISO Projects", "Turn an existing Debian Live or PelandukOS ISO into your next release.")
        self.source_hint = QLabel()
        self.source_hint.setWordWrap(True)
        self.source_hint.setObjectName("notice")
        layout.addWidget(self.source_hint)
        row = QHBoxLayout()
        self.button("Create project…", self.new_project, row)
        self.button("Open project…", self.open_pointer, row)
        self.import_button = self.button("Import ISO…", self.import_iso, row)
        self.import_button.setObjectName("primary")
        layout.addLayout(row)
        summary = QGroupBox("Current project")
        form = QFormLayout(summary)
        self.summary_fields = {}
        for key, label in (("name", "Project"), ("source", "Source ISO"), ("distribution", "Distribution"),
                           ("desktops", "Desktop"), ("boot", "Boot support"), ("version", "Release version")):
            field = QLabel("—")
            field.setTextFormat(Qt.PlainText)
            field.setWordWrap(True)
            field.setTextInteractionFlags(Qt.TextSelectableByMouse)
            self.summary_fields[key] = field
            form.addRow(label, field)
        layout.addWidget(summary)
        self.storage_path = QLabel("Storage —")
        self.storage_path.setWordWrap(True)
        self.storage_path.setTextFormat(Qt.PlainText)
        self.storage_path.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.storage_path)
        row = QHBoxLayout()
        self.button("Saved projects", self.list_projects, row)
        self.button("Save project reference…", self.save_pointer, row)
        self.button("Move storage…", self.move_project, row)
        layout.addLayout(row)
        self.project_list = QListWidget()
        self.project_list.setMaximumHeight(120)
        self.project_list.setToolTip("Double-click a saved project to open it.")
        self.project_list.itemDoubleClicked.connect(
            lambda item: self.open_project(item.data(Qt.UserRole)))
        layout.addWidget(self.project_list)
        self.project_list.hide()
        layout.addStretch()

    def make_files(self):
        layout = self.page("File Manager", "Edit target files only. Symlinks are displayed but never traversed. "
                           "Host imports create root-owned files with mode 0644.")
        row = QHBoxLayout()
        self.location = QLineEdit("")
        self.location.setPlaceholderText("Folder path")
        self.location.returnPressed.connect(lambda: self.load_directory(self.location.text()))
        self.button("Up", lambda: self.load_directory(str(PurePosixPath(self.directory).parent)), row)
        row.addWidget(self.location)
        self.button("Go", lambda: self.load_directory(self.location.text()), row)
        self.button("Find…", self.search_files, row)
        layout.addLayout(row)
        row = QHBoxLayout()
        for index, (label, action) in enumerate((
            ("Folder", self.create_folder), ("Text file", self.new_text),
            ("Import…", self.import_file), ("Copy…", self.copy_file),
            ("Move / rename", self.rename_file), ("Delete", self.delete_file),
            ("Permissions", self.permissions), ("Ownership", self.ownership),
        )):
            if index == 4:
                layout.addLayout(row)
                row = QHBoxLayout()
            self.button(label, action, row)
        layout.addLayout(row)
        split = QSplitter(Qt.Horizontal)
        self.files = QTableWidget(0, 5)
        self.files.setHorizontalHeaderLabels(["Name", "Type", "Mode", "UID:GID", "Bytes"])
        self.files.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.files.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.files.setSelectionMode(QAbstractItemView.SingleSelection)
        self.files.horizontalHeader().setStretchLastSection(True)
        self.files.cellDoubleClicked.connect(self.activate_file)
        split.addWidget(self.files)
        edit = QWidget()
        editor_layout = QVBoxLayout(edit)
        self.editor_label = QLabel("File editor")
        editor_layout.addWidget(self.editor_label)
        self.editor = QPlainTextEdit()
        self.editor.setReadOnly(True)
        editor_layout.addWidget(self.editor)
        self.button("Save file (Ctrl+S)", self.save_text, editor_layout)
        split.addWidget(edit)
        split.setSizes([480, 420])
        layout.addWidget(split, 1)

    def make_preview(self):
        layout = self.page("Live Preview", "Actual VM boot from an ISO. VM changes are temporary and never "
                           "copied into this project. Click the console to send keyboard and mouse input.")
        row = QHBoxLayout()
        self.preview_iso = QLineEdit()
        self.preview_iso.setPlaceholderText("ISO file")
        row.addWidget(self.preview_iso)
        self.button("Browse…", self.choose_preview, row)
        self.firmware = QComboBox()
        self.firmware.addItems(["BIOS", "UEFI"])
        row.addWidget(self.firmware)
        self.button("Start", self.start_preview, row)
        self.button("Stop", self.preview.stop, row)
        layout.addLayout(row)
        self.button("Build & test", self.build_preview, layout)
        self.console = Console(self.preview)
        layout.addWidget(self.console, 1)

    def make_build(self):
        layout = self.page("Build ISO", "Rebuild SquashFS, replay source boot equipment, verify ISO readback, "
                           "carry live GNOME/login/GRUB appearance into installations, and generate SHA256 and release metadata.")
        form = QFormLayout()
        self.filename = QLineEdit("PelandukOS-0.1-amd64.iso")
        self.suggested_iso_name = self.filename.text()
        self.build_version = QLineEdit("0.1")
        self.build_version.setMaxLength(64)
        self.build_version.setPlaceholderText("e.g. 1.1 or 3.0-beta1")
        self.build_version.setToolTip("Saved with the project and release manifest when you build.")
        form.addRow("Release version", self.build_version)
        form.addRow("ISO filename", self.filename)
        layout.addLayout(form)
        self.button("Bootsplash & GRUB…", self.edit_boot_appearance, layout)
        self.build_info = QPlainTextEdit()
        self.build_info.setReadOnly(True)
        self.build_info.setPlainText("No builds yet")
        layout.addWidget(self.build_info, 1)
        row = QHBoxLayout()
        self.build_button = self.button("Build ISO", self.build_iso, row)
        self.build_button.setObjectName("primary")
        self.export_button = self.button("Export last build…", self.export_build, row)
        layout.addLayout(row)

    def edit_boot_appearance(self):
        from gui.boot_appearance import BootAppearanceDialog
        from PySide6.QtWidgets import QDialog
        if not self.project or self.project.get("state") != "ready":
            self.error("Open a ready ISO project first.")
            return
        def loaded(values):
            dialog = BootAppearanceDialog(values, self)
            if dialog.exec() == QDialog.Accepted:
                self.submit("boot_appearance_save", lambda result: self.append_log(
                    "Boot appearance saved. It will be applied to the next ISO build."), values=dialog.values())
        self.submit("boot_appearance_get", loaded)

    def make_release(self):
        layout = self.page("Release Manager", "Release labels describe your output; they do not upgrade Debian "
                           "or change the target's /etc/os-release.")
        form = QFormLayout()
        self.release_fields = {}
        for name in ("distribution", "version", "build_number", "release_date", "developer"):
            field = QLineEdit()
            self.release_fields[name] = field
            form.addRow(name.replace("_", " ").title(), field)
        self.notes = QPlainTextEdit()
        self.notes.setMaximumHeight(130)
        form.addRow("Release notes", self.notes)
        layout.addLayout(form)
        self.button("Save release", self.save_release, layout)
        self.history = QPlainTextEdit()
        self.history.setReadOnly(True)
        layout.addWidget(self.history, 1)
        self.release_fields["version"].setMaxLength(64)
        self.release_fields["version"].setPlaceholderText("e.g. 1.1 or 3.0-beta1")
        self.release_fields["version"].textChanged.connect(self.build_version.setText)
        self.build_version.textChanged.connect(self.release_fields["version"].setText)
        self.build_version.textChanged.connect(lambda _: self.update_iso_suggestion())

    def update_iso_suggestion(self, force=False):
        distribution = self.project["release"]["distribution"] if self.project else "PelandukOS"
        suggestion = suggested_filename(distribution, self.build_version.text())
        if force or self.filename.text() == self.suggested_iso_name:
            self.filename.setText(suggestion)
        self.suggested_iso_name = suggestion

    def make_settings(self):
        layout = self.page("Settings", "Dependencies are detected only. No host packages or repositories are modified.")
        layout.addWidget(QLabel("Default project storage"))
        self.default_storage = StoragePicker(self.settings.value("project_folder", ""))
        self.default_storage.container_size.setValue(int(self.settings.value("container_size_gib", 128)))
        layout.addWidget(self.default_storage)
        self.button("Save default folder", self.save_storage_default, layout)
        self.dependencies = QTableWidget(0, 3)
        self.dependencies.setHorizontalHeaderLabels(["Component", "Status", "Package"])
        self.dependencies.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.dependencies.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.dependencies, 1)
        self.button("Check dependencies", self.refresh_dependencies, layout)
        self.refresh_dependencies()

    def apply_theme(self, theme="System"):
        self.settings.setValue("theme", "System")
        dark = QApplication.instance().palette().window().color().lightness() < 128
        self.setStyleSheet(stylesheet(dark))

    def system_palette_changed(self, *_):
        # Qt stylesheets cache palette-derived colors. Refresh after Qt has
        # propagated the new platform palette to child widgets.
        QTimer.singleShot(0, self.apply_theme)

    def refresh_dependencies(self):
        report = check()
        rows = [(name, info["available"], info["package"], "" if info["required"] else "Optional")
                for name, info in report["tools"].items()]
        rows += [("Qt interface", report["PySide6"], "PySide6", ""),
                 ("System helper", report["helper_installed"], "pelandukos-iso-builder", ""),
                 ("KVM acceleration", report["kvm"], "", "Optional"),
                 ("Live Desktop", report["target_vm"]["available"], "",
                  report["target_vm"].get("reason", ""))]
        self.dependencies.setRowCount(len(rows))
        for row, (name, available, package, note) in enumerate(rows):
            for column, text in enumerate((name, "Ready" if available else "Missing", package)):
                item = QTableWidgetItem(text)
                item.setToolTip(note)
                if column == 1:
                    item.setIcon(ui_icon("check" if available else "stop"))
                self.dependencies.setItem(row, column, item)
        self.dependencies.resizeColumnsToContents()
        self.dependencies.setColumnWidth(0, 230)
        self.dependencies.setColumnWidth(1, 130)

    def save_storage_default(self):
        try:
            self.default_storage.validate()
            self.settings.setValue("project_folder", self.default_storage.value())
            self.settings.setValue("container_size_gib", self.default_storage.container_size.value())
            self.stage.setText("Default folder saved for new projects.")
        except (OSError, BuilderError) as error:
            self.error(str(error))

    def append_log(self, message):
        self.logs.appendPlainText(message[-20000:])

    def error(self, message):
        self.log_toggle.setChecked(True)
        self.append_log(message)
        QMessageBox.critical(self, "Operation failed", message)

    def submit(self, operation, callback=None, **values):
        if self.busy:
            return
        if operation not in ("create", "list_projects") and not self.project and "project_id" not in values:
            self.error("Create or open a project first.")
            return
        request = {"operation": operation, **values}
        if self.project:
            request.setdefault("project_id", self.project["id"])
        self.busy = True
        self.job_failed = False
        self.stack.setEnabled(False)
        self.sidebar.setEnabled(False)
        self.update_steps()
        self.job_started = time.monotonic()
        self.stage_message = f"{operation}: waiting for authorization / starting"
        self.stage_detail = "Percentage not available yet"
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setRange(0, 0)
        self.elapsed_timer.start()
        self.update_job_status()
        def failed(message):
            self.job_failed = True
            self.error(message)
        self.jobs.submit(request, callback or self.set_project, failed, self.job_event, self.job_finished)

    def job_event(self, data):
        if self.live_editor and self.live_editor.running:
            self.live_editor.event_received(data)
        if data["type"] in ("frame", "live_state"):
            return
        if data["type"] == "progress":
            self.stage_message = data["message"]
            percent = max(0, min(100, int(data["percent"])))
            self.progress.setRange(0, 100)
            self.progress.setFormat("%p% of stage")
            self.progress.setValue(percent)
            self.stage_detail = f"{percent}% of current stage"
            if data.get("unit") == "bytes":
                self.stage_detail += f" · {data['completed'] / 1024**3:.2f}/{data['total'] / 1024**3:.2f} GiB"
        elif data["type"] == "stage":
            self.stage_message = data["message"]
            self.stage_detail = "Percentage unavailable for this stage"
            self.progress.setRange(0, 0)
        else:
            self.append_log(data["message"])
            return
        self.update_job_status()

    def update_job_status(self):
        if self.busy:
            seconds = max(0, int(time.monotonic() - self.job_started))
            elapsed = f"{seconds // 3600:02}:{seconds // 60 % 60:02}:{seconds % 60:02}"
            self.stage.setText(f"{self.stage_message} · {self.stage_detail} · Elapsed {elapsed}")
            self.stage.setToolTip(self.stage.text())

    def job_finished(self):
        if self.live_editor and self.live_editor.running:
            self.live_editor.finished_session(self.job_failed)
        previous = self.progress.value()
        self.elapsed_timer.stop()
        self.busy = False
        self.stack.setEnabled(True)
        self.sidebar.setEnabled(True)
        self.update_steps()
        self.progress.setRange(0, 100)
        self.progress.setValue(max(0, min(99, previous)) if self.job_failed else 100)
        self.progress.setFormat("Failed" if self.job_failed else "100% — complete")
        self.stage.setText("Job failed. Inspect the error/log; saved edits are retained." if self.job_failed
                           else "Job finished. Changes are saved automatically.")
        callbacks, self.after_job = self.after_job, []
        for callback in callbacks:
            QTimer.singleShot(0, callback)

    def start_live_edit(self):
        if not self.project or self.busy or not self.discard_project_edits():
            return
        if QMessageBox.question(
                self, "Live Desktop", "Open the project desktop? Changes are saved directly to this project.",
                QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel) != QMessageBox.Yes:
            return
        from gui.live_editor import LiveEditor
        if self.live_editor:
            self.live_editor.close()
            self.live_editor.deleteLater()
        self.live_editor = LiveEditor(self)
        self.live_editor.show()
        def completed(project):
            self.set_project(project)
            self.desktop_editor.reset()
            self.package_page.project_id = None
        self.submit("live_edit", completed, confirmed=True, network=self.live_network.isChecked(),
                    vm_settings=self.live_settings.values(), frame_encoding="ppm-zlib")

    def set_project(self, project):
        different_project = not self.project or self.project["id"] != project["id"]
        self.project = project
        if different_project:
            self.desktop_editor.reset()
            self.package_page.project_id = None
            self.package_page.table.setRowCount(0)
        self.project_badge.setText(project["name"] + "\n" + project["release"]["version"])
        self.storage_path.setText("Project storage: " + project.get("storage_path", "System default"))
        self.setWindowTitle(f"{project['name']} — PelandukOS ISO Builder")
        analysis = project.get("analysis") or {}
        source = project.get("source") or {}
        summary = {"name": project["name"], "source": source.get("original_path", "Not imported yet"),
                   "distribution": analysis.get("distribution", "Detected after import"),
                   "desktops": ", ".join(analysis.get("desktops", [])) or "Detected after import",
                   "boot": " + ".join(mode for mode in ("BIOS", "UEFI") if analysis.get(mode.lower())) or "—",
                   "version": project["release"]["version"]}
        for key, field in self.summary_fields.items():
            field.setText(summary[key])
        for name, field in self.release_fields.items():
            field.setText(project["release"].get(name, ""))
        self.notes.setPlainText(project["release"].get("notes", ""))
        self.history.setPlainText("\n".join(f"{entry['time'][:19].replace('T', ' ')}   "
                                          f"{entry['operation'].replace('_', ' ')}"
                                          for entry in reversed(project["history"])) or "No changes yet.")
        self.update_iso_suggestion(force=different_project)
        if project.get("last_build"):
            build = project["last_build"]
            self.build_info.setPlainText(f"Build complete: {build['filename']}\n\nSHA256\n{build['sha256']}\n\n"
                                         "ISO readback verified. Export the files, then test BIOS/UEFI boot in step 7.")
        else:
            self.build_info.setPlainText("No build in this project yet.")
        self.update_steps()

    def discard_edits(self):
        if not self.editor.document().isModified():
            return True
        return QMessageBox.question(self, "Unsaved text", "Discard the unsaved text editor changes?",
                                    QMessageBox.Discard | QMessageBox.Cancel,
                                    QMessageBox.Cancel) == QMessageBox.Discard

    def discard_project_edits(self):
        if self.desktop_editor.dirty:
            if QMessageBox.question(self, "Unsaved desktop choices",
                                    "Discard your unapplied desktop choices?",
                                    QMessageBox.Discard | QMessageBox.Cancel,
                                    QMessageBox.Cancel) != QMessageBox.Discard:
                return False
            self.desktop_editor.reset()
        return self.discard_edits()

    def reset_files(self):
        self.files.setRowCount(0)
        self.editor.clear()
        self.editor.document().setModified(False)
        self.editor.setReadOnly(True)
        self.editor_label.setText("File editor")
        self.edit_path, self.directory = None, ""
        self.edit_is_new = False
        self.location.clear()

    def new_project(self):
        if not self.discard_project_edits():
            return
        dialog = ProjectDialog(self, self.settings.value("project_folder", ""))
        dialog.storage.container_size.setValue(int(self.settings.value("container_size_gib", 128)))
        if dialog.exec():
            self.reset_files()
            self.submit("create", name=dialog.name.text().strip(), storage_folder=dialog.storage.value(),
                        container_size_gib=dialog.storage.container_size.value(),
                        version=dialog.version.text().strip())

    def move_project(self):
        if not self.project:
            self.error("Open the project you want to move first.")
            return
        if not self.discard_project_edits():
            return
        dialog = ProjectDialog(self, self.settings.value("project_folder", ""), moving=True)
        dialog.storage.container_size.setValue(int(self.settings.value("container_size_gib", 128)))
        if not dialog.exec():
            return
        destination = dialog.storage.value() or "/var/lib/pelandukos-iso-builder"
        if QMessageBox.question(
                self, "Move project",
                f"Move {self.project['name']} to {destination}?\n\n"
                "All project files will be copied and verified. The old copy will then be removed. "
                "This operation cannot be cancelled safely.",
                QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel) != QMessageBox.Yes:
            return
        def moved(project):
            self.reset_files()
            self.set_project(project)
            if project.get("storage_warning"):
                QMessageBox.warning(self, "Project moved", project["storage_warning"])
        self.submit("move_project", moved, storage_folder=dialog.storage.value(), confirmed=True,
                    container_size_gib=dialog.storage.container_size.value())

    def list_projects(self):
        def listed(projects):
            self.project_list.show()
            self.project_list.clear()
            for project in projects:
                item = QListWidgetItem(f"{project['name']}  •  {project['state']}  •  {project['id'][:8]}")
                item.setData(Qt.UserRole, project["id"])
                item.setToolTip(project.get("storage_error", project.get("storage_path", "")))
                self.project_list.addItem(item)
        self.submit("list_projects", listed)

    def open_project(self, project_id):
        if self.discard_project_edits():
            self.reset_files()
            self.submit("open", project_id=project_id)

    def open_pointer(self):
        name, _ = QFileDialog.getOpenFileName(self, "Open project", "", "Project (*.json)")
        if name:
            try:
                if Path(name).stat().st_size > 16 * 1024**2:
                    raise BuilderError("Project file is too large.")
                self.open_project(json.loads(Path(name).read_text())["id"])
            except (OSError, ValueError, KeyError, BuilderError) as error:
                self.error(str(error))

    def save_pointer(self):
        if not self.project:
            self.error("Create or open a project first.")
            return
        name, _ = QFileDialog.getSaveFileName(self, "Save project reference", "project.json", "Project (*.json)")
        if name:
            self.submit("export_project", lambda _: self.stage.setText("Project reference saved."),
                        destination=str(Path(name).absolute()))

    def import_iso(self):
        if not self.project:
            self.error("Create a project first.")
            return
        name, _ = QFileDialog.getOpenFileName(self, "Import source ISO", "", "ISO image (*.iso)")
        if name:
            self.submit("import", source=str(Path(name).absolute()))

    def load_directory(self, path=""):
        path = "" if path == "." else path
        def loaded(entries):
            self.directory = path
            self.location.setText(path)
            self.files.setRowCount(len(entries))
            for row, entry in enumerate(entries):
                values = [entry["name"], entry["kind"], entry["mode"],
                          f"{entry['uid']}:{entry['gid']}", str(entry["size"])]
                for column, value in enumerate(values):
                    item = QTableWidgetItem(value)
                    item.setData(Qt.UserRole, entry)
                    if entry["target"]:
                        item.setToolTip("Symlink → " + entry["target"])
                    self.files.setItem(row, column, item)
            self.files.resizeColumnsToContents()
        self.submit("fs_list", loaded, path=path)

    def selected(self):
        if self.files.currentRow() < 0:
            self.error("Select a file or folder first.")
            return None
        return self.files.item(self.files.currentRow(), 0).data(Qt.UserRole)

    def activate_file(self, row, _column):
        entry = self.files.item(row, 0).data(Qt.UserRole)
        if entry["kind"] == "directory":
            self.load_directory(entry["path"])
        elif entry["kind"] == "file" and self.discard_edits():
            def loaded(text):
                self.edit_path = entry["path"]
                self.edit_is_new = False
                self.editor_label.setText("/" + self.edit_path)
                self.editor.setPlainText(text)
                self.editor.setReadOnly(False)
                self.editor.document().setModified(False)
            self.submit("fs_read", loaded, path=entry["path"])

    def save_current(self):
        current = self.sidebar.currentItem().data(Qt.UserRole)
        if current == "Desktop Customizer":
            self.desktop_editor.save()
        elif current == "Release Manager":
            self.save_release()
        elif self.editor.document().isModified():
            self.save_text()
        else:
            self.save_pointer()

    def saved_file(self, manifest):
        self.set_project(manifest)
        self.editor.document().setModified(False)
        self.edit_is_new = False
        QTimer.singleShot(0, lambda: self.load_directory(self.directory))

    def save_text(self):
        if not self.edit_path:
            self.error("Open or create a text file first.")
            return
        self.submit("fs_write", self.saved_file, path=self.edit_path, text=self.editor.toPlainText(),
                    create_only=self.edit_is_new)

    def changed_files(self, manifest):
        self.set_project(manifest)
        QTimer.singleShot(0, lambda: self.load_directory(self.directory))

    def prompt_path(self, title, default=""):
        value, accepted = QInputDialog.getText(self, title, "Path relative to target rootfs",
                                             text=default or self.directory + ("/" if self.directory else ""))
        return value if accepted and value else None

    def create_folder(self):
        path = self.prompt_path("Create folder")
        if path:
            self.submit("fs_mkdir", self.changed_files, path=path)

    def new_text(self):
        if not self.discard_edits():
            return
        path = self.prompt_path("Create text file")
        if path:
            self.edit_path = path
            self.edit_is_new = True
            self.editor_label.setText("/" + path + " (unsaved)")
            self.editor.clear()
            self.editor.setReadOnly(False)
            self.editor.document().setModified(True)

    def import_file(self):
        source, _ = QFileDialog.getOpenFileName(self, "Import host file")
        if source:
            path = self.prompt_path("Target filename", str(PurePosixPath(self.directory) / Path(source).name))
            if path:
                self.submit("fs_import", self.changed_files, source=str(Path(source).absolute()), path=path)

    def copy_file(self):
        entry = self.selected()
        if entry:
            destination = self.prompt_path("Copy to", entry["path"] + ".copy")
            if destination:
                self.submit("fs_copy", self.changed_files, path=entry["path"], destination=destination)

    def rename_file(self):
        entry = self.selected()
        if entry and self.discard_edits():
            destination = self.prompt_path("Move / rename", entry["path"])
            if destination:
                self.reset_editor()
                self.submit("fs_rename", self.changed_files, path=entry["path"], destination=destination)

    def reset_editor(self):
        self.edit_path = None
        self.edit_is_new = False
        self.editor.clear()
        self.editor.document().setModified(False)
        self.editor.setReadOnly(True)

    def delete_file(self):
        entry = self.selected()
        if entry and QMessageBox.warning(self, "Delete target files",
                f"Permanently delete /{entry['path']} and its contents from this project?\n"
                "There is no undo in this milestone. The source ISO remains available.",
                QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel) == QMessageBox.Yes:
            if not self.discard_edits():
                return
            self.reset_editor()
            self.submit("fs_delete", self.changed_files, path=entry["path"], confirmed=True)

    def permissions(self):
        entry = self.selected()
        if entry:
            value, ok = QInputDialog.getText(self, "Unix permissions", "Octal permission mode, e.g. 0644")
            if ok:
                try:
                    mode = int(value, 8)
                except ValueError:
                    self.error("Enter an octal mode.")
                    return
                self.submit("fs_chmod", self.changed_files, path=entry["path"], mode=mode)

    def ownership(self):
        entry = self.selected()
        if entry:
            value, ok = QInputDialog.getText(self, "Numeric ownership", "UID:GID",
                                           text=f"{entry['uid']}:{entry['gid']}")
            if ok:
                try:
                    uid, gid = map(int, value.split(":"))
                except ValueError:
                    self.error("Enter numeric UID:GID, e.g. 0:0.")
                    return
                self.submit("fs_chown", self.changed_files, path=entry["path"], uid=uid, gid=gid)

    def search_files(self):
        query, ok = QInputDialog.getText(self, "Find filenames", "Name contains")
        if ok and query:
            def found(result):
                path, accepted = QInputDialog.getItem(self, "Search results", "Choose a result to locate",
                                                      result["paths"], editable=False)
                if accepted:
                    QTimer.singleShot(0, lambda: self.load_directory(str(PurePosixPath(path).parent)))
            self.submit("fs_search", found, query=query)

    def build_iso(self):
        if self.desktop_editor.dirty:
            self.error("Apply the desktop choices or use Detect desktop settings to discard them before building.")
            return
        if self.editor.document().isModified():
            self.error("Save or discard the text editor changes before building.")
            return
        try:
            version = validate_version(self.build_version.text())
        except BuilderError as error:
            self.error(str(error))
            return
        self.submit("build", filename=self.filename.text(), version=version)

    def export_build(self):
        directory = QFileDialog.getExistingDirectory(self, "Export ISO, checksum, manifest and log")
        if directory:
            def exported(result):
                self.append_log("Exported:\n" + "\n".join(result["files"]))
                self.preview_iso.setText(result["files"][0])
            self.submit("export_build", exported, destination=str(Path(directory).absolute()))

    def save_release(self):
        values = {name: field.text() for name, field in self.release_fields.items()}
        values["notes"] = self.notes.toPlainText()
        self.submit("save_release", values=values)

    def choose_preview(self):
        name, _ = QFileDialog.getOpenFileName(self, "Preview ISO", "", "ISO image (*.iso)")
        if name:
            self.preview_iso.setText(name)

    def start_preview(self):
        try:
            self.preview.start(self.preview_iso.text(), self.firmware.currentText())
        except (BuilderError, OSError) as error:
            self.error(str(error))

    def build_preview(self):
        if self.desktop_editor.dirty:
            self.error("Apply or discard your desktop choices before building the preview ISO.")
            return
        if self.editor.document().isModified():
            self.error("Save the text editor changes before previewing.")
            return
        if self.preview.temporary:
            self.error("Stop the running preview first.")
            return
        try:
            version = validate_version(self.build_version.text())
        except BuilderError as error:
            self.error(str(error))
            return
        def built(project):
            self.set_project(project)
            if self.export_temporary:
                self.export_temporary.cleanup()
            self.export_temporary = tempfile.TemporaryDirectory(prefix="pelandukos-preview-iso-")
            def exported(result):
                self.preview_iso.setText(result["files"][0])
                self.start_preview()
            QTimer.singleShot(0, lambda: self.submit("export_build", exported,
                                                    destination=self.export_temporary.name))
        self.submit("build", built, filename="PelandukOS-preview-amd64.iso", version=version)

    def closeEvent(self, event):
        if self.busy:
            QMessageBox.information(self, "Job running", "Wait for the current operation to finish before closing.")
            event.ignore()
        elif not self.discard_project_edits():
            event.ignore()
        elif self.preview.temporary:
            self.preview.stop()
            self.stage.setText("Stopping preview. Close the window again once it has stopped.")
            event.ignore()
        else:
            if self.export_temporary:
                self.export_temporary.cleanup()
            event.accept()
