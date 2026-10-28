"""Interactive project-rootfs console; no host X11/Wayland socket sharing."""
import base64
import zlib

from PySide6.QtCore import QObject, Signal, Qt
from PySide6.QtGui import QPixmap, QPainter
from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QMessageBox

from gui.preview_widget import Console
from gui.design import polish, stylesheet, RESOURCES
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication


class InputBridge(QObject):
    frame = Signal(str)

    def __init__(self, window):
        super().__init__()
        self.window = window
        self.ready = False

    def command(self, name, arguments):
        if self.ready and name == "input-send-event" and self.window.jobs.active:
            self.window.jobs.active.control({"action": "input", "events": arguments["events"]})


class LiveEditor(QDialog):
    def __init__(self, window):
        super().__init__(window)
        self.setWindowTitle("PelandukOS Live Desktop")
        self.setWindowIcon(QIcon(str(RESOURCES / "pelandukos-iso-builder.svg")))
        self.setStyleSheet(stylesheet(QApplication.instance().palette().window().color().lightness() < 128))
        self.resize(1100, 780)
        self.window = window
        self.running = True
        self.bridge = InputBridge(window)
        layout = QVBoxLayout(self)
        self.status = QLabel("Starting Live Desktop…")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.console = Console(self.bridge)
        self.console.setText("Starting…")
        layout.addWidget(self.console, 1)
        actions = QHBoxLayout()
        self.stop = QPushButton("Shut down & save")
        self.force = QPushButton("Force stop…")
        self.close_button = QPushButton("Close")
        for button in (self.stop, self.force, self.close_button):
            actions.addWidget(button)
            button.setEnabled(False)
        self.stop.clicked.connect(self.shutdown)
        self.force.clicked.connect(self.force_stop)
        self.close_button.clicked.connect(self.accept)
        layout.addLayout(actions)
        polish(self)

    def event_received(self, event):
        if event["type"] == "frame":
            data = base64.b64decode(event["data"])
            if event.get("encoding") == "ppm-zlib":
                decoder = zlib.decompressobj()
                data = decoder.decompress(data, 16 * 1024**2 + 1)
                if len(data) > 16 * 1024**2 or not decoder.eof:
                    return
            region = event.get("region")
            if region:
                patch = QPixmap()
                if not patch.loadFromData(data, "PPM"):
                    return
                width, height = region["canvas_width"], region["canvas_height"]
                x, y = region["x"], region["y"]
                if not (0 < width <= 4096 and 0 < height <= 4096 and width * height <= 4096*1024
                        and x >= 0 and y >= 0 and x + patch.width() <= width and y + patch.height() <= height):
                    return
                if self.console.image.width() != width or self.console.image.height() != height:
                    self.console.image = QPixmap(width, height)
                    self.console.image.fill(Qt.black)
                painter = QPainter(self.console.image)
                painter.drawPixmap(x, y, patch)
                painter.end()
            else:
                self.console.image.loadFromData(data, "PPM")
            self.console.redraw()
        elif event["type"] == "live_state":
            self.bridge.ready = event["ready"]
            self.stop.setEnabled(event["ready"])
            self.force.setEnabled(event["ready"])
            if event["ready"]:
                self.status.setText("Live Desktop")
        elif event["type"] == "stage":
            self.status.setText(event["message"])

    def shutdown(self):
        if self.window.jobs.active:
            self.window.jobs.active.control({"action": "shutdown"})
            self.stop.setEnabled(False)
            self.status.setText("Saving and shutting down…")

    def force_stop(self):
        if QMessageBox.warning(self, "Force stop", "Unsaved desktop work may be lost and files may be incomplete. "
                               "Force this VM to stop?", QMessageBox.Yes | QMessageBox.Cancel,
                               QMessageBox.Cancel) == QMessageBox.Yes:
            self.window.jobs.active.control({"action": "force_stop"})

    def finished_session(self, failed):
        self.running = False
        self.bridge.ready = False
        self.stop.setEnabled(False)
        self.force.setEnabled(False)
        self.close_button.setEnabled(True)
        self.status.setText("Session failed — see activity" if failed else
                            "Session saved")

    def reject(self):
        if not self.running:
            super().reject()

    def closeEvent(self, event):
        if self.running:
            self.status.setText("Use Shut down & save to close the running desktop safely.")
            event.ignore()
        else:
            event.accept()
