import re
from core.errors import BuilderError
from core.boot_metadata import hybrid_apm_layout, system_area_summary
from core.util import read_text, safe_path


class DebianLiveAdapter:
    name = "debian-live-single"
    squashfs = "live/filesystem.squashfs"

    def validate_layout(self, analysis):
        if analysis["squashfs"] != ["/" + self.squashfs]:
            raise BuilderError("Supported layout requires exactly /live/filesystem.squashfs. "
                               "Layered and multi-SquashFS ISOs are not supported.")
        if not (analysis["bios"] or analysis["uefi"]):
            raise BuilderError("No supported El Torito BIOS/UEFI boot entry was detected.")
        if not analysis["bootloader"]:
            raise BuilderError("No recognized GRUB, ISOLINUX or SYSLINUX tree.")
        if "-boot_image" not in analysis["replay_report"]:
            raise BuilderError("xorriso could not describe replayable boot equipment.")
        summary = system_area_summary(analysis)
        if any(word in summary for word in ("MIPS", "SPARC", "SUN Disk", "PReP")):
            raise BuilderError(f"Unsupported system-area layout ({summary}); "
                               "this adapter supports x86 BIOS/UEFI only.")
        hybrid_apm_layout(analysis)
        if not any(path.startswith("/live/vmlinuz") for path in analysis["paths"]):
            raise BuilderError("Missing Debian Live kernel under /live.")

    def identify_root(self, root):
        # os-release is often a symlink; resolve only this known alternative.
        path = root / "etc/os-release"
        if path.is_symlink():
            path = root / "usr/lib/os-release"
        text = read_text(safe_path(root, str(path.relative_to(root))))
        fields = {}
        for line in text.splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                fields[key] = value.strip().strip("\"'")
        distro = fields.get("ID", "")
        if distro not in ("debian", "pelandukos"):
            raise BuilderError(f"Unsupported distribution ID {distro!r}; only Debian/PelandukOS "
                               "Live is enabled in this milestone.")
        status = read_text(safe_path(root, "var/lib/dpkg/status"), 64 * 1024 * 1024)
        architectures = set(re.findall(r"^Architecture: (.+)$", status, re.MULTILINE)) - {"all"}
        if architectures != {"amd64"}:
            raise BuilderError(f"Only single-architecture amd64 targets are supported: {architectures}")
        desktops = [name for name, package in (("GNOME", "gnome-shell"),
                    ("KDE Plasma", "plasma-desktop"), ("XFCE", "xfce4-session"))
                    if f"Package: {package}\n" in status]
        modules = safe_path(root, "usr/lib/modules") if (root / "lib").is_symlink() else safe_path(root, "lib/modules")
        return {"distribution": fields.get("PRETTY_NAME", distro), "id": distro,
                "base_version": fields.get("VERSION_ID", "unknown"), "architecture": "amd64",
                "desktops": desktops, "kernels": sorted(p.name for p in modules.iterdir()) if modules.is_dir() else []}

    def refresh_metadata(self, root, tree):
        status = read_text(safe_path(root, "var/lib/dpkg/status"), 64 * 1024 * 1024)
        packages = []
        for paragraph in status.split("\n\n"):
            fields = dict(line.split(": ", 1) for line in paragraph.splitlines()
                          if ": " in line and not line.startswith(" "))
            if fields.get("Status") == "install ok installed":
                packages.append(f"{fields['Package']} {fields['Version']}")
        manifest = safe_path(tree, "live/filesystem.packages")
        if manifest.exists():
            manifest.write_text("\n".join(sorted(packages)) + "\n")
        # Installer-specific removal manifests are preserved; no package edits in Phase 1.
        size_file = safe_path(tree, "live/filesystem.size")
        if size_file.exists():
            import os
            total = sum(os.lstat(os.path.join(base, name)).st_size
                        for base, dirs, files in os.walk(root, followlinks=False)
                        for name in [*dirs, *files])
            size_file.write_text(str(total) + "\n")
