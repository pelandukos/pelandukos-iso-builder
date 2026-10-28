import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest


def test_window_and_enabled_workflows():
    pytest.importorskip("PySide6.QtWidgets")
    from PySide6.QtWidgets import QApplication
    from gui.main_window import MainWindow
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    app.processEvents()
    assert window.sidebar.count() == 8
    assert window.stack.count() == 8
    assert window.editor.isReadOnly()
    assert window.preview.process.program() == ""
    window.apply_theme("Light")
    window.apply_theme("Dark")
    window.close()


def test_steps_system_theme_and_desktop_controls(engine):
    pytest.importorskip("PySide6.QtWidgets")
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QPalette, QColor
    from PySide6.QtWidgets import QApplication
    from gui.main_window import MainWindow
    app = QApplication.instance() or QApplication([])
    palette = QPalette(app.palette())
    window = MainWindow()
    assert not (window.sidebar.item(1).flags() & Qt.ItemIsEnabled)
    assert window.pages["Release Manager"] == window.stack.widget(4)
    assert window.pages["Build ISO"] == window.stack.widget(5)
    assert window.pages["Package Manager"] == window.stack.widget(2)
    assert window.settings.value("theme") == "System"
    assert "#141d28" not in window.styleSheet()
    for color in ("#202020", "#fafafa"):
        current = QPalette(palette)
        current.setColor(QPalette.Window, QColor(color))
        current.setColor(QPalette.Base, QColor(color))
        text = "#eeeeee" if color == "#202020" else "#111111"
        current.setColor(QPalette.WindowText, QColor(text))
        app.setPalette(current)
        app.processEvents()
        assert ("#1d1e22" if color == "#202020" else "#f5f5f7") in window.styleSheet()
        assert not window.sidebar.iconSize().isEmpty()
        assert window.source_hint.palette().color(QPalette.WindowText).name() == text
    app.setPalette(palette)
    project = engine.store.create("Step fixture")
    project["state"] = "ready"
    window.set_project(project)
    assert window.sidebar.item(1).flags() & Qt.ItemIsEnabled
    assert window.next_step.isEnabled()
    editor = window.desktop_editor
    editor.loaded({"desktops": {"XFCE": {"fields": ["font", "icon_theme"], "note": ""}},
                   "gtk_theme": [], "icon_theme": ["Test"], "cursor_theme": [], "saved": None})
    assert not editor.wallpaper_row.isEnabled()
    assert editor.font.isEnabled()
    assert not editor.dirty
    editor.font.setText("Sans 12")
    assert editor.dirty
    assert editor.apply.isEnabled()
    editor.reset()
    window.close()


def test_exfat_picker_accepts_container_storage(tmp_path, monkeypatch):
    pytest.importorskip("PySide6.QtWidgets")
    from PySide6.QtWidgets import QApplication
    from gui.storage_dialog import StoragePicker
    app = QApplication.instance() or QApplication([])
    picker = StoragePicker(str(tmp_path))
    monkeypatch.setattr(picker, "inspect", lambda: {
        "filesystem": "exFAT", "supported": False, "temporary": False,
        "free_bytes": 1024**4, "total_bytes": 2 * 1024**4})
    picker.refresh()
    assert not picker.container_options.isHidden()
    assert "Supported using an ext4 container" in picker.info.text()
    assert picker.validate()["filesystem"] == "exFAT"
    picker.container_size.setValue(256)
    assert picker.container_size.value() == 256
    app.processEvents()
    picker.close()


def test_new_project_submits_selected_folder(tmp_path, monkeypatch):
    pytest.importorskip("PySide6.QtWidgets")
    from PySide6.QtWidgets import QApplication
    from gui.main_window import MainWindow
    from gui.storage_dialog import ProjectDialog
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    calls = []
    monkeypatch.setattr(window, "submit", lambda *args, **kwargs: calls.append((args, kwargs)))
    def accept(dialog):
        dialog.name.setText("Selected folder")
        dialog.version.setText("3.1-beta1")
        dialog.storage.folder.setText(str(tmp_path))
        dialog.storage.validate()
        assert "GiB free" in dialog.storage.info.text()
        return 1
    monkeypatch.setattr(ProjectDialog, "exec", accept)
    window.new_project()
    assert calls == [(("create",), {"name": "Selected folder", "storage_folder": str(tmp_path),
                                   "container_size_gib": 128, "version": "3.1-beta1"})]
    app.processEvents()
    window.close()


def test_build_version_suggestion_and_custom_filename(engine, monkeypatch):
    pytest.importorskip("PySide6.QtWidgets")
    from PySide6.QtWidgets import QApplication
    from gui.main_window import MainWindow
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    project = engine.store.create("Version", "3.1")
    window.set_project(project)
    assert window.filename.text() == "PelandukOS-3.1-amd64.iso"
    window.release_fields["version"].setText("3.2-beta1")
    assert window.build_version.text() == "3.2-beta1"
    assert window.filename.text() == "PelandukOS-3.2-beta1-amd64.iso"
    window.filename.setText("my-custom.iso")
    window.build_version.setText("3.2")
    assert window.filename.text() == "my-custom.iso"
    assert window.release_fields["version"].text() == "3.2"
    calls = []
    monkeypatch.setattr(window, "submit", lambda *args, **kwargs: calls.append((args, kwargs)))
    window.build_iso()
    assert calls == [(("build",), {"filename": "my-custom.iso", "version": "3.2"})]
    app.processEvents()
    window.close()


def test_progress_stage_transitions_and_failed_jobs():
    pytest.importorskip("PySide6.QtWidgets")
    import time
    from PySide6.QtWidgets import QApplication
    from gui.main_window import MainWindow
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.busy = True
    window.job_started = time.monotonic() - 65
    window.job_event({"type": "progress", "message": "Copying ISO", "percent": 42,
                      "completed": 42 * 1024**3, "total": 100 * 1024**3, "unit": "bytes"})
    assert window.progress.value() == 42
    assert "42.00/100.00 GiB" in window.stage.text()
    assert "00:01:05" in window.stage.text()
    window.job_event({"type": "stage", "message": "Flushing"})
    assert window.progress.maximum() == 0
    assert "unavailable" in window.stage.text()
    window.job_failed = True
    window.job_finished()
    assert window.progress.value() < 100
    assert window.progress.format() == "Failed"
    window.job_failed = False
    window.job_finished()
    assert window.progress.value() == 100
    app.processEvents()
    window.close()


def test_live_edit_does_not_require_a_build(engine, monkeypatch, tmp_path):
    pytest.importorskip("PySide6.QtWidgets")
    from PySide6.QtWidgets import QApplication, QMessageBox
    from gui.main_window import MainWindow
    app = QApplication.instance() or QApplication([])
    from PySide6.QtCore import QSettings
    monkeypatch.setattr("gui.main_window.QSettings",
                        lambda: QSettings(str(tmp_path / "live.ini"), QSettings.IniFormat))
    window = MainWindow()
    project = engine.store.create("Before ISO")
    project["state"] = "ready"
    window.set_project(project)
    calls = []
    monkeypatch.setattr(window, "submit", lambda *args, **kwargs: calls.append((args, kwargs)))
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.Yes)
    window.live_settings.controls["ram_gib"].setValue(8)
    window.live_settings.controls["cpus"].setValue(2)
    window.start_live_edit()
    assert project["last_build"] is None
    assert calls[0][0][0] == "live_edit"
    assert calls[0][1] == {"confirmed": True, "network": False,
                             "vm_settings": window.live_settings.values(), "frame_encoding": "ppm-zlib"}
    assert calls[0][1]["vm_settings"]["ram_gib"] == 8
    assert calls[0][1]["vm_settings"]["cpus"] == 2
    assert window.live_editor.running
    window.live_editor.reject()
    assert window.live_editor.isVisible()
    window.live_editor.finished_session(False)
    window.live_editor.close()
    window.close()
    app.processEvents()


def test_package_page_loads_and_filters(engine, monkeypatch):
    pytest.importorskip("PySide6.QtWidgets")
    from PySide6.QtWidgets import QApplication
    from gui.main_window import MainWindow
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    project = engine.store.create("Packages")
    project["state"] = "ready"
    window.set_project(project)
    page = window.package_page
    page.loaded({"packages": [{"Package": "hello", "Version": "1.0", "Installed-Size": "12",
                               "Description": "Test package", "Depends": "libc6"}],
                 "snapshot": {"exists": True, "restorable": True}})
    assert page.restore.isEnabled()
    assert page.table.item(0, 0).text() == "hello"
    page.search.setText("absent")
    assert page.table.isRowHidden(0)
    page.search.setText("hello")
    assert not page.table.isRowHidden(0)
    calls = []
    monkeypatch.setattr(window, "submit", lambda *args, **kwargs: calls.append((args, kwargs)))
    page.names.setText("hello")
    page.query("install")
    assert calls[0][0][0] == "package_query"
    assert calls[0][1] == {"action": "install", "packages": ["hello"]}
    window.close()
    app.processEvents()


def test_live_settings_persist_and_recover_invalid_preferences(tmp_path):
    pytest.importorskip("PySide6.QtWidgets")
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication
    from gui.live_settings import LiveSettings
    from core.live_settings import DEFAULTS
    app = QApplication.instance() or QApplication([])
    settings = QSettings(str(tmp_path / "preferences.ini"), QSettings.IniFormat)
    widget = LiveSettings(settings)
    widget.controls["ram_gib"].setValue(12)
    widget.controls["gpu"].setCurrentIndex(1)
    widget.controls["resolution"].setCurrentIndex(0)
    restored = LiveSettings(settings)
    assert restored.values() == DEFAULTS | {"ram_gib": 12, "gpu": "software", "resolution": "1280x800"}
    settings.setValue("live_vm/settings", '{"ram_gib": -1}')
    assert LiveSettings(settings).values() == DEFAULTS
    widget.close()
    restored.close()
