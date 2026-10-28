import os
import shutil
import stat
import tempfile

from core.errors import BuilderError
from core.util import read_text, regular_file, safe_path


class FilesystemManager:
    def __init__(self, root):
        self.root = root

    def list(self, path=""):
        directory = safe_path(self.root, path)
        if not directory.is_dir():
            raise BuilderError("Select a directory.")
        result = []
        for child in sorted(directory.iterdir(), key=lambda p: p.name.casefold()):
            info = child.lstat()
            result.append({"name": child.name, "path": str(child.relative_to(self.root)),
                           "kind": "symlink" if stat.S_ISLNK(info.st_mode) else
                                   "directory" if stat.S_ISDIR(info.st_mode) else
                                   "file" if stat.S_ISREG(info.st_mode) else "special",
                           "mode": stat.filemode(info.st_mode), "uid": info.st_uid,
                           "gid": info.st_gid, "size": info.st_size,
                           "target": os.readlink(child) if child.is_symlink() else ""})
        return result

    def read(self, path):
        return read_text(safe_path(self.root, path))

    def write(self, path, text, *, create_only=False):
        if not isinstance(text, str) or len(text.encode("utf-8")) > 2 * 1024**2:
            raise BuilderError("Text must be UTF-8 and at most 2 MiB.")
        target = safe_path(self.root, path)
        info = None
        if target.exists():
            if create_only:
                raise BuilderError("A file already exists at this path. Open it before editing.")
            regular_file(target)
            info = target.stat()
            if info.st_nlink > 1:
                raise BuilderError("Editing hard-linked files is not yet supported; this protects link relationships.")
        fd, temporary = tempfile.mkstemp(prefix=".edit-", dir=target.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(text.encode("utf-8"))
                stream.flush()
                os.fsync(stream.fileno())
            if info:
                os.chown(temporary, info.st_uid, info.st_gid)
                shutil.copystat(target, temporary, follow_symlinks=False)
            else:
                os.chmod(temporary, 0o644)
            os.replace(temporary, target)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def mkdir(self, path):
        target = safe_path(self.root, path)
        target.mkdir(mode=0o755)

    def rename(self, path, destination):
        source = safe_path(self.root, path, leaf_symlink=True)
        target = safe_path(self.root, destination, leaf_symlink=True)
        if source == self.root or target == self.root:
            raise BuilderError("Cannot rename the root filesystem.")
        if target.exists() or target.is_symlink():
            raise BuilderError("Destination already exists.")
        source.rename(target)

    def delete(self, path):
        target = safe_path(self.root, path, leaf_symlink=True)
        if target == self.root:
            raise BuilderError("Cannot delete the root filesystem.")
        if target.is_dir() and not target.is_symlink():
            shutil.rmtree(target)
        else:
            target.unlink()

    def chmod(self, path, mode):
        if not isinstance(mode, int) or mode < 0 or mode > 0o7777:
            raise BuilderError("Invalid Unix permission mode.")
        target = safe_path(self.root, path)
        if not (target.is_dir() or stat.S_ISREG(target.lstat().st_mode)):
            raise BuilderError("Permissions can only be changed for files and directories.")
        target.chmod(mode)

    def chown(self, path, uid, gid):
        if not all(isinstance(value, int) and 0 <= value < 2**31 for value in (uid, gid)):
            raise BuilderError("UID and GID must be nonnegative numeric IDs.")
        os.chown(safe_path(self.root, path, leaf_symlink=True), uid, gid, follow_symlinks=False)

    def search(self, query):
        if not query or len(query) > 200:
            raise BuilderError("Enter a filename search up to 200 characters.")
        matches = []
        for parent, directories, files in os.walk(self.root, followlinks=False):
            for name in directories + files:
                if query.casefold() in name.casefold():
                    matches.append(str((self.root.__class__(parent) / name).relative_to(self.root)))
                    if len(matches) >= 1000:
                        return {"paths": matches, "truncated": True}
        return {"paths": matches, "truncated": False}
