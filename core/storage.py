"""Filesystem checks shared by storage selection and the privileged router."""
import ctypes
import os
import stat
from pathlib import Path

from core.errors import BuilderError
from core.util import digest


FILESYSTEMS = {0xEF53: "ext2/ext3/ext4", 0x58465342: "XFS", 0x9123683E: "Btrfs",
               0x01021994: "tmpfs", 0x2011BAB0: "exFAT", 0x4D44: "FAT",
               0x5346544E: "NTFS", 0x65735546: "FUSE"}
SUPPORTED = {0xEF53, 0x58465342, 0x9123683E, 0x01021994}


class StatFS(ctypes.Structure):
    _fields_ = [("type", ctypes.c_long), ("bsize", ctypes.c_long),
                ("blocks", ctypes.c_ulong), ("bfree", ctypes.c_ulong),
                ("bavail", ctypes.c_ulong), ("files", ctypes.c_ulong),
                ("ffree", ctypes.c_ulong), ("fsid", ctypes.c_int * 2),
                ("namelen", ctypes.c_long), ("frsize", ctypes.c_long),
                ("flags", ctypes.c_long), ("spare", ctypes.c_long * 4)]


def filesystem_info(fd):
    value = StatFS()
    libc = ctypes.CDLL(None, use_errno=True)
    function = libc.fstatfs
    function.argtypes = [ctypes.c_int, ctypes.POINTER(StatFS)]
    function.restype = ctypes.c_int
    if function(fd, ctypes.byref(value)) != 0:
        raise OSError(ctypes.get_errno(), "Could not inspect the selected filesystem")
    kind = value.type & 0xFFFFFFFF
    usage = os.fstatvfs(fd)
    return {"filesystem": FILESYSTEMS.get(kind, f"unknown (0x{kind:x})"),
            "supported": kind in SUPPORTED, "temporary": kind == 0x01021994,
            "free_bytes": usage.f_bavail * usage.f_frsize,
            "total_bytes": usage.f_blocks * usage.f_frsize}


def require_linux_filesystem(info):
    if not info["supported"]:
        raise BuilderError(f"Selected folder uses {info['filesystem']}. Project filesystems need "
                           "ext4, XFS or Btrfs to preserve ownership, permissions, links and xattrs. "
                           "Source and output ISO files may stay on this drive.")


def tree_inventory(root, *, content=True):
    """No symlink traversal or special-file reads; retain hardlink relationships."""
    result = {}
    links = {}
    for parent, directories, files in os.walk(root, followlinks=False):
        directories.sort()
        for name in sorted(directories + files):
            path = Path(parent) / name
            relative = str(path.relative_to(root))
            if relative in (".lock", ".relocation.json"):
                continue
            info = path.lstat()
            entry = {"mode": info.st_mode, "uid": info.st_uid, "gid": info.st_gid}
            if stat.S_ISREG(info.st_mode):
                entry["size"] = info.st_size
                if content:
                    entry["sha256"] = digest(path)
                if info.st_nlink > 1:
                    entry["hardlink"] = links.setdefault((info.st_dev, info.st_ino), relative)
            elif stat.S_ISLNK(info.st_mode):
                entry["target"] = os.readlink(path)
            elif stat.S_ISBLK(info.st_mode) or stat.S_ISCHR(info.st_mode):
                entry["rdev"] = info.st_rdev
            if content:
                entry["xattrs"] = {key: os.getxattr(path, key, follow_symlinks=False)
                                   for key in os.listxattr(path, follow_symlinks=False)}
            result[relative] = entry
    return result


def copy_space(root):
    inventory = tree_inventory(root, content=False)
    return sum(entry.get("size", 0) for path, entry in inventory.items()
               if entry.get("hardlink", path) == path) + 256 * 1024**2
