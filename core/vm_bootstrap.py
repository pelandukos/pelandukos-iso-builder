"""Trusted host-kernel bootstrap for editing a target rootfs without an ISO."""
import gzip
import lzma
import re
import shutil
import stat
import subprocess
from pathlib import Path

from core.errors import BuilderError


def newc(entries):
    archive = bytearray()
    for index, (name, mode, content) in enumerate([*entries, ("TRAILER!!!", 0, b"")], 1):
        name = name.encode() + b"\0"
        fields = [index, mode, 0, 0, 1, 0, len(content), 0, 0, 0, 0, len(name), 0]
        archive.extend(b"070701" + b"".join(f"{value:08x}".encode() for value in fields))
        archive.extend(name)
        archive.extend(b"\0" * (-len(archive) % 4))
        archive.extend(content)
        archive.extend(b"\0" * (-len(archive) % 4))
    return gzip.compress(bytes(archive), mtime=0)


def kernel():
    candidates = sorted(Path("/boot").glob("vmlinuz-*"), key=lambda p: p.stat().st_mtime, reverse=True)
    for path in candidates:
        version = path.name.removeprefix("vmlinuz-")
        if (Path("/usr/lib/modules") / version).is_dir() or (Path("/lib/modules") / version).is_dir():
            return path, version
    raise BuilderError("Live editing needs a readable host /boot/vmlinuz-* and matching kernel modules.")


def capability():
    missing = [tool for tool in ("qemu-system-x86_64", "busybox", "modprobe", "file") if not shutil.which(tool)]
    if missing:
        return {"available": False, "reason": "Missing runtime tools: " + ", ".join(missing)}
    try:
        path, version = kernel()
        description = subprocess.run(["file", "-Lb", shutil.which("busybox")],
                                     capture_output=True, text=True, check=True).stdout
        if "statically linked" not in description:
            raise BuilderError("Install busybox-static; the VM bootstrap requires a static BusyBox.")
        return {"available": True, "kernel": str(path), "version": version}
    except (BuilderError, OSError, subprocess.SubprocessError) as error:
        return {"available": False, "reason": str(error)}


def bootstrap(script, destination):
    info = capability()
    if not info["available"]:
        raise BuilderError(info["reason"])
    # systemd needs autofs before switch_root: the imported filesystem may
    # contain modules for a different kernel than this host-kernel bootstrap.
    paths = []
    for module in ("9p", "9pnet_virtio", "virtio_net", "virtio_gpu", "virtio_input",
                   "evdev", "atkbd", "hid_generic", "usbhid", "xhci_pci", "button", "overlay", "autofs4"):
        result = subprocess.run(["modprobe", "--show-depends", "-S", info["version"], module],
                                capture_output=True, text=True)
        if result.returncode:
            raise BuilderError(f"Host kernel {info['version']} lacks bootstrap module {module}: "
                               f"{result.stderr.strip()}. Install matching host kernel modules.")
        for line in result.stdout.splitlines():
            if line.startswith("insmod "):
                path = Path(line.split()[1])
                if path not in paths:
                    paths.append(path)
    entries = [(name, stat.S_IFDIR | 0o755, b"") for name in
               ("bin", "dev", "proc", "sys", "run", "tmp", "target", "modules", "lower", "writable")]
    entries.append(("bin/busybox", stat.S_IFREG | 0o755, Path(shutil.which("busybox")).read_bytes()))
    for name in ("sh", "mount", "mkdir", "ln", "sleep", "sync", "poweroff", "chroot", "cat", "ip", "switch_root", "insmod"):
        entries.append(("bin/" + name, stat.S_IFLNK | 0o777, b"busybox"))
    loading = []
    for index, path in enumerate(paths):
        if not re.fullmatch(r"[\w./+-]+", str(path)):
            raise BuilderError("Unexpected host module path.")
        content = path.read_bytes()
        if path.suffix == ".xz":
            content = lzma.decompress(content)
        elif path.suffix == ".gz":
            content = gzip.decompress(content)
        elif path.suffix == ".zst":
            content = subprocess.run(["zstd", "-dc", str(path)], capture_output=True, check=True).stdout
        name = f"modules/{index}.ko"
        entries.append((name, stat.S_IFREG | 0o644, content))
        loading.append(f"insmod /{name} || exit 70")
    init = """#!/bin/sh
export PATH=/bin
mount -t proc proc /proc
mount -t sysfs sysfs /sys
mount -t devtmpfs devtmpfs /dev
mount -t tmpfs tmpfs /run
""" + "\n".join(loading) + """
mount -t 9p -o trans=virtio,version=9p2000.L,cache=none target /target || exit 71
""" + script
    entries.append(("init", stat.S_IFREG | 0o755, init.encode()))
    Path(destination).write_bytes(newc(entries))
    return Path(info["kernel"])
