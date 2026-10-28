"""APT operations inside a VM, committed by atomic rootfs exchange."""
import ctypes
import os
import re
import shutil
from pathlib import Path

from core.errors import BuilderError
from core.iso_extractor import require_space
from core.storage import copy_space
from core.util import atomic_json, read_text, regular_file, safe_path
from core.validator import kernel_inventory_digest
from services.target_vm import TargetVM


def preset_command(request, *, simulate=False):
    from core import pelandukos_preset
    preset = request.get("preset")
    if not isinstance(preset, str) or preset not in pelandukos_preset.PRESETS:
        raise BuilderError("Choose a supported PelandukOS preset.")
    if not simulate and request.get("network") is not True:
        raise BuilderError("PelandukOS presets require network access to refresh signed repositories.")
    return ["/usr/bin/python3", "-c", Path(pelandukos_preset.__file__).read_text(),
            preset, "preview" if simulate else "apply"]


def package_names(values):
    if not isinstance(values, list) or not 1 <= len(values) <= 100:
        raise BuilderError("Choose between 1 and 100 package names.")
    if any(not isinstance(value, str) or not re.fullmatch(r"[a-z0-9][a-z0-9+.-]+(?::[a-z0-9-]+)?", value)
           for value in values):
        raise BuilderError("Use exact Debian package names, optionally with :architecture.")
    return values


def apt_command(action, names=(), *, simulate=False):
    if action not in ("install", "remove", "upgrade", "refresh"):
        raise BuilderError("Unsupported package action.")
    command = ["/usr/bin/apt-get", "-o", "Dpkg::Use-Pty=0"]
    if simulate:
        command += ["--simulate"]
    else:
        command += ["--assume-yes", "-o", "Dpkg::Options::=--force-confold"]
    command += ["--no-remove"] if action in ("install", "upgrade") else []
    if action == "refresh":
        if simulate:
            raise BuilderError("Repository refresh does not have a simulation.")
        return command + ["update", "--error-on=any"]
    command += ["--only-upgrade"] if action == "upgrade" else []
    return command + ["install" if action == "upgrade" else action, "--", *package_names(names)]


def exchange(left, right):
    """Linux renameat2 is atomic, including across a crash; same filesystem only."""
    libc = ctypes.CDLL(None, use_errno=True)
    function = libc.renameat2
    function.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    function.restype = ctypes.c_int
    if function(-100, os.fsencode(left), -100, os.fsencode(right), 2):
        raise OSError(ctypes.get_errno(), "Cannot atomically commit package transaction")
    for parent in {left.parent, right.parent}:
        fd = os.open(parent, os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


class PackageManager:
    def __init__(self, project, sandbox, emit=lambda event: None, log=lambda text: None):
        self.project, self.sandbox, self.emit, self.log = project, sandbox, emit, log
        self.root = safe_path(project, "rootfs")
        self.backup = safe_path(project, "snapshots/package-rootfs")
        self.marker = safe_path(project, "snapshots/package-transaction.json")

    def snapshot(self):
        import json
        if not self.marker.exists():
            if self.backup.exists():
                raise BuilderError("Package snapshot has no transaction metadata; inspect project storage.")
            return {"exists": False}
        info = json.loads(read_text(self.marker))
        return {"exists": True, "restorable": self.backup.is_dir()
                and self.root.stat().st_ino != info["original_inode"]}

    def list(self):
        status = regular_file(safe_path(self.root, "var/lib/dpkg/status"))
        if status.stat().st_size > 64 * 1024**2:
            raise BuilderError("Target package database exceeds 64 MiB.")
        packages, fields = [], {}
        def finish():
            if fields.get("Status") == "install ok installed":
                packages.append({key: fields.get(key, "") for key in
                                 ("Package", "Version", "Architecture", "Installed-Size", "Description", "Depends")})
            fields.clear()
        with open(status, encoding="utf-8", errors="replace") as stream:
            total = 0
            while line := stream.readline(65537):
                total += len(line)
                if total > 64 * 1024**2:
                    raise BuilderError("Target package database exceeds 64 MiB.")
                if len(line) > 65536:
                    raise BuilderError("Target package database has an oversized line.")
                if not line.strip():
                    finish()
                elif not line[0].isspace() and ": " in line:
                    key, value = line.rstrip().split(": ", 1)
                    fields[key] = value
            finish()
        return {"packages": packages, "snapshot": self.snapshot()}

    def query(self, request):
        action = request["action"]
        if action == "preset":
            command = preset_command(request, simulate=True)
        elif action == "search":
            query = request.get("query", "")
            if not isinstance(query, str) or not re.fullmatch(r"[a-zA-Z0-9+._ -]{1,100}", query):
                raise BuilderError("Search using letters, digits, spaces, + . _ or -.")
            command = ["/usr/bin/apt-cache", "search", "--", query]
        elif action == "details":
            command = ["/usr/bin/apt-cache", "show", "--", *package_names(request["packages"])]
        elif action == "audit":
            command = ["/usr/bin/apt-get", "--simulate", "check"]
        else:
            command = apt_command(action, request.get("packages"), simulate=True)
        commands = [command] + ([["/usr/bin/dpkg", "--audit"]] if action == "audit" else [])
        return TargetVM(self.sandbox, self.emit, self.log).run(
            self.root, commands=commands, readonly=True, timeout=600)

    def discard(self):
        # The original/committed root is never removed by this method.
        if self.backup.exists():
            shutil.rmtree(self.backup)
        self.marker.unlink(missing_ok=True)

    def restore(self):
        if not self.snapshot().get("restorable"):
            raise BuilderError("No completed package rollback snapshot is available.")
        atomic_json(self.marker, {"original_inode": self.root.stat().st_ino})
        exchange(self.root, self.backup)

    def apply(self, request):
        command = (preset_command(request) if request["action"] == "preset" else
                   apt_command(request["action"], request.get("packages")))
        if self.snapshot()["exists"]:
            raise BuilderError("Discard or inspect the existing package snapshot before another transaction.")
        require_space(self.project, copy_space(self.root))
        inventory = kernel_inventory_digest(self.root)
        atomic_json(self.marker, {"original_inode": self.root.stat().st_ino})
        try:
            self.emit({"type": "stage", "message": "Copying rootfs for package rollback; this needs extra disk space…"})
            self.sandbox.run(["cp", "-a", "--reflink=auto", "--", "/project/rootfs",
                              "/project/snapshots/package-rootfs"], writes={self.project: "/project"})
            commands = [command, ["/usr/bin/apt-get", "--simulate", "check"],
                        ["/bin/sh", "-c", 'audit="$(/usr/bin/dpkg --audit)" || exit; '
                                  '[ -z "$audit" ] || { printf "%s\\n" "$audit"; exit 1; }']]
            result = TargetVM(self.sandbox, self.emit, self.log).run(
                self.backup, commands=commands, network=request.get("network") is True)
            # Restore guest-only service/DNS overrides without following target links.
            for relative in ("usr/sbin/policy-rc.d", "etc/resolv.conf"):
                source = safe_path(self.root, relative, leaf_symlink=True)
                destination = safe_path(self.backup, relative, leaf_symlink=True)
                if destination.exists() and destination.is_dir() and not destination.is_symlink():
                    raise BuilderError("Package operation replaced a runtime configuration file with a directory.")
                destination.unlink(missing_ok=True)
                if source.exists() or source.is_symlink():
                    self.sandbox.run(["cp", "-a", "--", "/original/" + relative, "/changed/" + relative],
                                     reads={self.root: "/original"}, writes={self.backup: "/changed"})
            if kernel_inventory_digest(self.backup) != inventory:
                raise BuilderError("Package operation changed kernel/initramfs/modules. "
                                   "Automatic ISO boot artifact synchronization is not supported; original retained.")
            exchange(self.root, self.backup)
            return result
        except Exception:
            # If exchange already committed (e.g. a later fsync failed), retain rollback.
            if not self.snapshot().get("restorable"):
                self.discard()
            raise
