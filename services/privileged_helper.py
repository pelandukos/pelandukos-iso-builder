"""Installed, root-owned helper. No arbitrary command, mount, or target API."""
import json
import os
import pwd
import stat
import sys
from contextlib import contextmanager
from pathlib import Path

from core.errors import BuilderError
from core.project_manager import ProjectStore
from services.storage_manager import StorageManager

STORE = Path("/var/lib/pelandukos-iso-builder")


class UserFiles:
    """Open external paths using the caller's actual filesystem permissions."""
    def __init__(self, uid):
        self.uid = uid
        self.gid = pwd.getpwuid(uid).pw_gid

    @contextmanager
    def identity(self):
        if os.geteuid() != 0:
            if os.geteuid() != self.uid:
                raise BuilderError("Caller identity mismatch.")
            yield
            return
        groups = os.getgroups()
        gid = os.getegid()
        os.setgroups([])
        os.setegid(self.gid)
        os.seteuid(self.uid)
        try:
            yield
        finally:
            os.seteuid(0)
            os.setegid(gid)
            os.setgroups(groups)

    @contextmanager
    def read(self, value):
        path = self.validate(value)
        with self.identity():
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                os.close(fd)
                raise BuilderError("Input must be a regular file, not a device, pipe or symlink.")
        with os.fdopen(fd, "rb") as stream:
            yield stream

    @contextmanager
    def write(self, value):
        path = self.validate(value)
        with self.identity():
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        # Keep a failed partial export: unlinking a caller-controlled path here
        # could remove a different file if the caller renamed it during export.
        with os.fdopen(fd, "wb") as stream:
            yield stream
            stream.flush()
            os.fsync(stream.fileno())

    @staticmethod
    def validate(value):
        if not isinstance(value, str) or not value.startswith("/") or "\0" in value:
            raise BuilderError("Select an absolute file path.")
        return value


def secure_store(uid):
    if STORE.is_symlink():
        raise BuilderError("Unsafe helper storage.")
    STORE.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = STORE.stat()
    if info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o700:
        raise BuilderError("Helper store must be root-owned with mode 0700.")
    directory = STORE / str(uid)
    if directory.is_symlink():
        raise BuilderError("Unsafe user storage.")
    directory.mkdir(mode=0o700, exist_ok=True)
    info = directory.stat()
    if info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o700:
        raise BuilderError("User storage must be root-owned with mode 0700.")
    return ProjectStore(directory, uid)


def main():
    def emit(event):
        print(json.dumps(event, ensure_ascii=True), flush=True)
    try:
        if os.geteuid() != 0:
            raise BuilderError("Use the installed helper through Polkit; do not run the GUI as root.")
        uid = int(os.environ.get("PKEXEC_UID", "-1"))
        if uid <= 0:
            raise BuilderError("A non-root Polkit caller is required.")
        os.umask(0o077)
        raw = sys.stdin.buffer.readline(4 * 1024**2 + 1)
        if len(raw) > 4 * 1024**2:
            raise BuilderError("Request exceeds the 4 MiB limit.")
        request = json.loads(raw)
        result = StorageManager(secure_store(uid), UserFiles(uid), emit).handle(request)
        emit({"type": "result", "result": result})
        return 0
    except Exception as error:
        emit({"type": "error", "message": f"{type(error).__name__}: {error}"})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
