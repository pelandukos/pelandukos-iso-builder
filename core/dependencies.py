import importlib.util
import shutil
from pathlib import Path

REQUIRED = {
    "xorriso": "xorriso", "unsquashfs": "squashfs-tools",
    "mksquashfs": "squashfs-tools", "bwrap": "bubblewrap", "pkexec": "pkexec",
    "lsinitramfs": "initramfs-tools-core",
    "mkfs.ext4": "e2fsprogs", "stdbuf": "coreutils",
}
OPTIONAL = {"qemu-system-x86_64": "qemu-system-x86", "file": "file",
            "blkid": "util-linux", "lsblk": "util-linux", "glib-compile-schemas": "libglib2.0-bin",
            "busybox": "busybox-static", "modprobe": "kmod", "zstd": "zstd"}
HELPER = Path("/usr/lib/pelandukos-iso-builder/helper")


def check():
    from core.vm_bootstrap import capability
    try:
        qt_available = all(importlib.util.find_spec("PySide6." + module) is not None
                           for module in ("QtCore", "QtGui", "QtWidgets", "QtNetwork"))
    except (ImportError, ValueError):
        qt_available = False
    return {
        "tools": {name: {"available": bool(shutil.which(name)), "package": package,
                         "required": name in REQUIRED}
                  for name, package in (REQUIRED | OPTIONAL).items()},
        "PySide6": qt_available,
        "helper_installed": HELPER.is_file(),
        "kvm": Path("/dev/kvm").exists(),
        "target_vm": capability(),
    }
