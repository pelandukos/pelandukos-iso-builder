import hashlib
import json
import os
import stat
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from core.errors import BuilderError


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(path, algorithm="sha256", progress=None):
    value = hashlib.new(algorithm)
    with open(path, "rb") as stream:
        total, completed = os.fstat(stream.fileno()).st_size, 0
        if progress:
            progress.update(0, total)
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            value.update(chunk)
            completed += len(chunk)
            if progress:
                progress.update(completed, total)
    return value.hexdigest()


def atomic_json(path, data):
    path = Path(path)
    fd, temporary = tempfile.mkstemp(prefix=".manifest-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(data, stream, indent=2, ensure_ascii=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def relative_path(value):
    if not isinstance(value, str) or "\0" in value or "\\" in value:
        raise BuilderError("Invalid project path.")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        raise BuilderError("Paths must remain inside the project.")
    return path


def safe_path(root, value, *, leaf_symlink=False):
    """No symlink traversal, including dangling links and absolute target links.

    Production roots live under a root-owned 0700 store and all operations hold
    the project lock. Untrusted users cannot race ancestor replacement.
    """
    root = Path(root)
    parts = relative_path(value).parts
    current = root
    for index, part in enumerate(parts):
        current /= part
        if current.is_symlink() and not (leaf_symlink and index == len(parts) - 1):
            raise BuilderError("Symlink traversal is disabled. Select the link itself.")
    return current


def regular_file(path):
    if not stat.S_ISREG(Path(path).lstat().st_mode):
        raise BuilderError("This operation requires a regular file.")
    return Path(path)


def read_text(path, limit=2 * 1024 * 1024):
    path = regular_file(path)
    if path.stat().st_size > limit:
        raise BuilderError(f"File exceeds the text editor limit ({limit // 1024} KiB).")
    try:
        data = path.read_bytes()
        if b"\0" in data:
            raise ValueError()
        return data.decode("utf-8")
    except (UnicodeError, ValueError) as error:
        raise BuilderError("The file is not UTF-8 text.") from error
