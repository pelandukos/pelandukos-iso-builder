from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QTableWidget, QTableWidgetItem,
    QPushButton, QPlainTextEdit, QLabel, QCheckBox, QMessageBox, QAbstractItemView, QComboBox,
)


class PackagePage(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window = window
        self.project_id = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        from core.pelandukos_preset import PRESETS
        presets = QHBoxLayout()
        self.preset = QComboBox()
        for key, label in PRESETS.items():
            self.preset.addItem(label, key)
        presets.addWidget(self.preset, 1)
        self.button("Preview preset…", self.preview_preset, presets)
        layout.addLayout(presets)
        hint = QLabel("PelandukOS presets keep Software and use repository Settings and installer. "
                      "Compiz, Snap, Flatpak and AppImage support are kept. Optional apps can be selected during installation.")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        actions = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search packages")
        self.search.textChanged.connect(self.filter)
        actions.addWidget(self.search, 1)
        self.button("Installed", self.reload, actions)
        self.button("Search repositories", self.repository_search, actions)
        layout.addLayout(actions)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Package", "Version", "Size · KiB", "Description"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table, 2)
        self.names = QLineEdit()
        self.names.setPlaceholderText("Package names")
        layout.addWidget(self.names)
        operations = QHBoxLayout()
        for name, action in (("Details", "details"), ("Install…", "install"),
                             ("Remove…", "remove"), ("Upgrade…", "upgrade"), ("Check dependencies", "audit")):
            self.button(name, lambda checked=False, action=action: self.query(action), operations)
        layout.addLayout(operations)
        network = QHBoxLayout()
        self.network = QCheckBox("Network")
        self.network.setChecked(True)
        network.addWidget(self.network)
        self.button("Refresh repositories…", lambda: self.apply("refresh"), network)
        layout.addLayout(network)
        snapshots = QHBoxLayout()
        self.snapshot_label = QLabel("No package snapshot")
        snapshots.addWidget(self.snapshot_label, 1)
        self.restore = self.button("Restore snapshot…", lambda: self.snapshot_action("package_restore"), snapshots)
        self.discard = self.button("Discard snapshot…", lambda: self.snapshot_action("package_discard"), snapshots)
        self.restore.setEnabled(False)
        self.discard.setEnabled(False)
        layout.addLayout(snapshots)
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setPlaceholderText("Package details")
        layout.addWidget(self.output, 1)

    @staticmethod
    def button(label, slot, layout):
        button = QPushButton(label)
        button.clicked.connect(slot)
        layout.addWidget(button)
        return button

    def selected_names(self):
        if self.names.text().strip():
            return self.names.text().split()
        return [self.table.item(index.row(), 0).text() for index in self.table.selectionModel().selectedRows()]

    def filter(self, text):
        for row in range(self.table.rowCount()):
            self.table.setRowHidden(row, text.lower() not in " ".join(
                self.table.item(row, col).text().lower() for col in (0, 3)))

    def reload(self):
        self.window.submit("package_list", self.loaded)

    def snapshot_status(self, snapshot):
        self.restore.setEnabled(snapshot.get("restorable", False))
        self.discard.setEnabled(snapshot["exists"])
        self.snapshot_label.setText("Rollback snapshot available" if snapshot.get("restorable") else
                                    "Interrupted transaction: discard its copy to retry" if snapshot["exists"] else
                                    "No package snapshot")

    def loaded(self, result):
        self.project_id = self.window.project["id"]
        self.table.setRowCount(len(result["packages"]))
        for row, package in enumerate(result["packages"]):
            for column, key in enumerate(("Package", "Version", "Installed-Size", "Description")):
                item = QTableWidgetItem(package[key])
                item.setToolTip(package.get("Depends", ""))
                self.table.setItem(row, column, item)
        self.table.resizeColumnsToContents()
        self.filter(self.search.text())
        self.snapshot_status(result["snapshot"])

    def repository_search(self):
        self.window.submit("package_query", self.show_text, action="search", query=self.search.text().strip())

    def show_text(self, result):
        self.output.setPlainText(result.get("text") or "No issues / no matching results.")

    def query(self, action):
        names = self.selected_names()
        if action not in ("audit",) and not names:
            self.window.error("Enter package names or select installed packages.")
            return
        def reviewed(result):
            self.show_text(result)
            if action in ("install", "remove", "upgrade"):
                self.window.after_job.append(lambda: self.apply(action, names, result["text"]))
        self.window.submit("package_query", reviewed, action=action, packages=names)

    def preview_preset(self):
        preset = self.preset.currentData()
        def reviewed(result):
            self.show_text(result)
            self.window.after_job.append(lambda: self.apply("preset", plan=result["text"], preset=preset))
        self.window.submit("package_query", reviewed, action="preset", preset=preset)

    def apply(self, action, names=None, plan="", preset=None):
        box = QMessageBox(self)
        box.setWindowTitle("Confirm target package operation")
        box.setText(f"{action.capitalize()} packages in this project's VM?\n\n"
                    "A full rootfs copy is required. The previous rootfs is retained for rollback. "
                    "Package scripts execute only inside the guest.")
        if plan:
            box.setDetailedText(plan)
            box.setInformativeText("Review the dependency/removal plan in Show Details. "
                                   "APT will recheck dependencies before committing.")
        box.setStandardButtons(QMessageBox.Yes | QMessageBox.Cancel)
        box.setDefaultButton(QMessageBox.Cancel)
        if box.exec() == QMessageBox.Yes:
            self.window.submit("package_apply", self.changed, action=action, packages=names or [],
                               confirmed=True, network=self.network.isChecked(), preset=preset)

    def changed(self, result):
        self.window.set_project(result["project"])
        self.show_text(result)
        self.snapshot_status(result["snapshot"])
        self.project_id = None
        self.table.setRowCount(0)

    def snapshot_action(self, operation):
        message = ("Replace the current filesystem with the previous package snapshot? "
                   "All edits since that snapshot move into the alternate snapshot."
                   if operation == "package_restore" else "Permanently remove the package rollback copy?")
        if QMessageBox.question(self, "Package snapshot", message, QMessageBox.Yes | QMessageBox.Cancel,
                                QMessageBox.Cancel) == QMessageBox.Yes:
            self.window.submit(operation, self.changed, confirmed=True)
