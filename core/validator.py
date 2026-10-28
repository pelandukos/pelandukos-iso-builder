import shlex
from pathlib import Path

from core.errors import BuilderError
from core.boot_metadata import hybrid_apm_layout, system_area_summary
from core.util import read_text, safe_path


def kernel_inventory_digest(root):
    """Guard unsupported kernel/initramfs/module edits, including same-version edits."""
    import hashlib
    import os
    from core.util import digest
    value = hashlib.sha256()
    folders = ["boot", "usr/lib/modules" if (root / "lib").is_symlink() else "lib/modules"]
    for relative in folders:
        directory = safe_path(root, relative)
        if not directory.exists():
            value.update((relative + ":absent").encode())
            continue
        for base, directories, files in os.walk(directory, followlinks=False):
            for name in sorted(directories + files):
                path = Path(base) / name
                info = path.lstat()
                entry = [str(path.relative_to(root)), info.st_mode, info.st_uid, info.st_gid]
                if path.is_symlink():
                    entry.append(os.readlink(path))
                elif path.is_file():
                    entry.append(digest(path))
                value.update(repr(entry).encode("utf-8", errors="surrogateescape"))
            directories.sort()
    return value.hexdigest()


def resolve_iso_path(tree, value):
    """Resolve in-image symlinks without ever resolving against the host /."""
    parts = list(Path(value.lstrip("/")).parts)
    resolved = []
    links = 0
    while parts:
        part = parts.pop(0)
        if part in ("", "."):
            continue
        if part == "..":
            if not resolved:
                raise BuilderError("Boot reference escapes the ISO.")
            resolved.pop()
            continue
        candidate = tree.joinpath(*resolved, part)
        if candidate.is_symlink():
            import os
            target = os.readlink(candidate)
            links += 1
            if links > 40:
                raise BuilderError("Loop in ISO boot symlinks.")
            if target.startswith("/"):
                resolved = []
            parts = list(Path(target).parts if not target.startswith("/") else Path(target[1:]).parts) + parts
        else:
            resolved.append(part)
    return tree.joinpath(*resolved)


def validate_boot(tree):
    configs = []
    for folder in ("boot/grub", "isolinux", "syslinux"):
        base = safe_path(tree, folder)
        if base.is_dir():
            configs.extend(base.rglob("*.cfg"))
    references = []
    live_kernels = []
    live_initrds = []
    for config in configs:
        config = safe_path(tree, str(config.relative_to(tree)))
        for line in read_text(config).splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                words = shlex.split(line, comments=True)
            except ValueError:
                continue
            if not words:
                continue
            command = words[0].lower()
            candidates = []
            if command in ("linux", "linuxefi", "kernel", "initrd", "initrdefi") and len(words) > 1:
                candidates = words[1:] if command.startswith("initrd") else words[1:2]
            if command == "append":
                candidates += [item.split("=", 1)[1] for item in words[1:] if item.startswith("initrd=")]
            for candidate in candidates:
                for value in candidate.split(","):
                    if "$" in value or "(" in value:
                        raise BuilderError(f"Dynamic boot reference is not supported: {value}")
                    relative = value.lstrip("/") if value.startswith("/") else str(config.parent.relative_to(tree) / value)
                    path = resolve_iso_path(tree, relative)
                    if not path.is_file() or path.stat().st_size == 0:
                        raise BuilderError(f"Missing or empty boot artifact: {value} in {config.relative_to(tree)}")
                    references.append(relative)
                    if path.relative_to(tree).as_posix().startswith("live/"):
                        (live_initrds if command.startswith("initrd") or command == "append"
                         else live_kernels).append(relative)
    if not live_kernels or not live_initrds:
        raise BuilderError("Could not validate both live kernel and initramfs boot references.")
    return {"boot_references": sorted(set(references)), "kernel_references": sorted(set(live_kernels)),
            "initramfs_references": sorted(set(live_initrds))}


def compare_boot(source, output):
    for mode in ("bios", "uefi"):
        if source[mode] != output[mode]:
            raise BuilderError(f"Rebuild changed {mode.upper()} boot capability.")
    for marker in ("MBR", "GPT", "APM"):
        if marker in system_area_summary(source).split() and marker not in system_area_summary(output).split():
            raise BuilderError(f"Rebuild lost source {marker} partition metadata.")
    original_apm = hybrid_apm_layout(source)
    if original_apm is not None and original_apm != hybrid_apm_layout(output):
        raise BuilderError("Rebuild changed the source APM EFI mapping.")
