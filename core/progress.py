"""Measured per-stage progress; no timer-based completion estimates."""
import re
import time
import subprocess
from functools import lru_cache

from core.errors import BuilderError


@lru_cache(maxsize=2)
def percentage_options(tool):
    """Older Debian squashfs-tools lack -percentage; keep their jobs working."""
    if tool not in ("mksquashfs", "unsquashfs"):
        raise ValueError("Unexpected progress tool")
    for option in ("-help", "-help-all"):
        result = subprocess.run([tool, option], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, timeout=10, env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
                                                         "LC_ALL": "C"})
        if re.search(r"(?m)^\s*-percentage(?:\s|$)", result.stdout):
            return ["-percentage"]
    return []


class Progress:
    def __init__(self, emit, message):
        self.emit = emit
        self.message = message
        self.last_time = 0
        self.last_percent = -1

    def update(self, completed, total, *, unit="bytes"):
        if total <= 0:
            return
        percent = min(100, max(0, int(completed * 100 / total)))
        timestamp = time.monotonic()
        if percent < self.last_percent:
            return
        if percent == self.last_percent and timestamp - self.last_time < 0.5:
            return
        self.last_percent, self.last_time = percent, timestamp
        self.emit({"type": "progress", "message": self.message, "percent": percent,
                   "completed": completed, "total": total, "unit": unit})


def stage(emit, message):
    emit({"type": "stage", "message": message})


def copy_stream(incoming, outgoing, emit, message):
    import os
    total = os.fstat(incoming.fileno()).st_size
    progress = Progress(emit, message)
    progress.update(0, total)
    completed = 0
    while chunk := incoming.read(4 * 1024**2):
        remaining = memoryview(chunk)
        while remaining:
            count = outgoing.write(remaining)
            if not count:
                raise BuilderError("Copy did not make progress.")
            remaining = remaining[count:]
            completed += count
        progress.update(completed, total)
    if completed != total:
        raise BuilderError("Source size changed during copying. Retry with an unchanged input file.")
    return completed


class ToolProgress:
    """Parse complete tool records, including CR/backspace progress updates."""
    def __init__(self, emit, message, kind):
        self.emit, self.message, self.kind = emit, message, kind
        self.progress = Progress(emit, message)
        self.pending = ""
        self.phase = None
        stage(emit, message)

    def mkfs_phase(self, record):
        for label in ("Allocating group tables", "Writing inode tables",
                      "Creating journal", "Writing superblocks and filesystem accounting information"):
            if label in record and label != self.phase:
                self.phase = label
                self.message = "Formatting ext4: " + label.lower()
                self.progress = Progress(self.emit, self.message)
                stage(self.emit, self.message)

    def feed(self, text):
        records = re.split(r"[\r\n\b]", self.pending + text)
        self.pending = records.pop()[-8192:]
        for record in records:
            record = record.strip()
            if self.kind == "mkfs":
                self.mkfs_phase(record)
                match = re.search(r"(\d+)\s*/\s*(\d+)\s*$", record)
                if match:
                    self.progress.update(int(match[1]), int(match[2]), unit="groups")
            elif self.kind == "squashfs":
                match = re.fullmatch(r"(\d{1,3}(?:\.\d+)?)%?", record)
                if not match:
                    match = re.fullmatch(r"\[.*\]\s+\d+/\d+\s+(\d{1,3})%", record)
                if match and 0 <= float(match[1]) <= 100:
                    self.progress.update(float(match[1]), 100, unit="percent")
            elif self.kind == "xorriso":
                match = re.search(r"(?:^|\bUPDATE\s*:)\s*(\d+(?:\.\d+)?)%.*\bdone\b", record)
                if match and 0 <= float(match[1]) <= 100:
                    self.progress.update(float(match[1]), 100, unit="percent")
        if self.kind == "mkfs":
            self.mkfs_phase(self.pending)
