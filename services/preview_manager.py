import json
import shutil
import tempfile
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QTimer, Signal
from PySide6.QtNetwork import QLocalSocket

from core.errors import BuilderError
from services.qemu import qemu_arguments


class PreviewManager(QObject):
    frame = Signal(str)
    log = Signal(str)
    state = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.MergedChannels)
        self.process.readyReadStandardOutput.connect(
            lambda: self.log.emit(bytes(self.process.readAllStandardOutput()).decode(errors="replace")))
        self.process.finished.connect(self.finished)
        self.process.errorOccurred.connect(lambda _: self.log.emit(self.process.errorString()))
        self.socket = QLocalSocket(self)
        self.socket.readyRead.connect(self.received)
        self.timer = QTimer(self)
        self.timer.setInterval(500)
        self.timer.timeout.connect(self.tick)
        self.temporary = None
        self.ready = False
        self.pending = False
        self.buffer = b""

    def start(self, iso, mode):
        if self.process.state() != QProcess.NotRunning:
            raise BuilderError("Stop the current preview first.")
        if not shutil.which("qemu-system-x86_64"):
            raise BuilderError("Install qemu-system-x86 to enable preview.")
        self.temporary = tempfile.TemporaryDirectory(prefix="pelandukos-preview-")
        try:
            args, acceleration = qemu_arguments(iso, self.temporary.name, mode)
        except Exception:
            self.temporary.cleanup()
            self.temporary = None
            raise
        self.ready, self.pending, self.buffer = False, False, b""
        self.log.emit(f"Starting {mode} preview using {acceleration}. VM changes are temporary.")
        self.process.start("qemu-system-x86_64", args)
        self.timer.start()
        self.state.emit(True)

    def tick(self):
        if self.process.state() == QProcess.NotRunning:
            self.finished()
            return
        if self.socket.state() == QLocalSocket.UnconnectedState:
            self.socket.connectToServer(str(Path(self.temporary.name) / "qmp.sock"))
        elif self.ready and not self.pending:
            self.pending = True
            self.command("screendump", {"filename": str(Path(self.temporary.name) / "frame.ppm")}, "frame")

    def command(self, execute, arguments=None, identifier=None):
        if self.socket.state() == QLocalSocket.ConnectedState:
            message = {"execute": execute}
            if arguments:
                message["arguments"] = arguments
            if identifier:
                message["id"] = identifier
            self.socket.write((json.dumps(message) + "\n").encode())

    def received(self):
        self.buffer += bytes(self.socket.readAll())
        while b"\n" in self.buffer:
            line, self.buffer = self.buffer.split(b"\n", 1)
            try:
                message = json.loads(line)
            except ValueError:
                continue
            if "QMP" in message:
                self.command("qmp_capabilities", identifier="capabilities")
            if message.get("id") == "capabilities" and "return" in message:
                self.ready = True
            if message.get("id") == "frame":
                self.pending = False
                if "return" in message:
                    self.frame.emit(str(Path(self.temporary.name) / "frame.ppm"))
            if "error" in message:
                self.log.emit(str(message["error"]))

    def stop(self):
        if self.process.state() != QProcess.NotRunning:
            self.command("quit")
            self.process.terminate()
            QTimer.singleShot(3000, self.kill_if_running)

    def kill_if_running(self):
        if self.process.state() != QProcess.NotRunning:
            self.process.kill()

    def finished(self, *_):
        self.timer.stop()
        self.socket.abort()
        if self.temporary:
            serial = Path(self.temporary.name) / "serial.log"
            if serial.exists():
                self.log.emit(serial.read_text(errors="replace")[-100000:])
            self.temporary.cleanup()
            self.temporary = None
        self.ready = False
        self.state.emit(False)
