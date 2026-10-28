import json
import subprocess
import queue
from PySide6.QtCore import QObject, QRunnable, Signal, QThreadPool

from core.dependencies import HELPER


class Signals(QObject):
    event = Signal(dict)
    result = Signal(object)
    error = Signal(str)
    finished = Signal()


class Job(QRunnable):
    def __init__(self, request):
        super().__init__()
        self.request = request
        self.signals = Signals()
        self.controls = queue.Queue(maxsize=256)

    def control(self, message):
        try:
            self.controls.put_nowait(message)
        except queue.Full:
            pass

    def run(self):
        try:
            if not HELPER.is_file():
                raise RuntimeError("The privileged helper is not installed. Build and install the Debian "
                                   "package using README.md. The GUI must stay unprivileged.")
            with subprocess.Popen(["pkexec", str(HELPER)], stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  text=True, encoding="utf-8") as process:
                # A regular temp file avoids a blocked stderr pipe during auth.
                import threading
                errors = []
                reader = threading.Thread(target=lambda: errors.append(process.stderr.read()), daemon=True)
                reader.start()
                process.stdin.write(json.dumps(self.request) + "\n")
                process.stdin.flush()
                interactive = self.request.get("operation") == "live_edit"
                if interactive:
                    def send_controls():
                        try:
                            while True:
                                message = self.controls.get()
                                if message is None:
                                    break
                                process.stdin.write(json.dumps(message) + "\n")
                                process.stdin.flush()
                        except (BrokenPipeError, ValueError, OSError):
                            pass
                    threading.Thread(target=send_controls, daemon=True).start()
                else:
                    process.stdin.close()
                result = None
                received = False
                failure = None
                for line in process.stdout:
                    try:
                        event = json.loads(line)
                    except ValueError:
                        self.signals.event.emit({"type": "log", "message": line.rstrip()})
                        continue
                    if event["type"] == "result":
                        result, received = event["result"], True
                    elif event["type"] == "error":
                        failure = event["message"]
                    else:
                        self.signals.event.emit(event)
                status = process.wait()
                if interactive:
                    self.control(None)
                    process.stdin.close()
                reader.join()
                if status or failure or not received:
                    raise RuntimeError(failure or "".join(errors).strip() or
                                       f"Authorization/helper failed (exit {status}).")
                self.signals.result.emit(result)
        except Exception as error:
            self.signals.error.emit(str(error))
        finally:
            self.signals.finished.emit()


class JobManager(QObject):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        self.active = None

    def submit(self, request, result, error, event, finished):
        job = Job(request)
        self.active = job
        job.signals.result.connect(result)
        job.signals.error.connect(error)
        job.signals.event.connect(event)
        job.signals.finished.connect(finished)
        self.pool.start(job)
