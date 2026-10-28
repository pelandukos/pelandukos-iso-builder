import re
import shlex
from pathlib import Path

from core.errors import BuilderError
from core.util import regular_file


def parse_analysis(listing, pvd, boot, system, replay, size):
    paths = []
    for line in listing.splitlines():
        if line.startswith("'"):
            try:
                parts = shlex.split(line)
            except ValueError as error:
                raise BuilderError("ISO contains a path that cannot be safely inspected.") from error
            if len(parts) == 1:
                paths.append(parts[0])
    for path in paths:
        if "\n" in path or ".." in Path(path).parts or "\0" in path:
            raise BuilderError("ISO contains unsafe paths.")
    volume = re.search(r"Volume id\s*:\s*'(.*)'", pvd)
    squashfs = [path for path in paths if path.endswith(".squashfs")]
    platforms = re.findall(r"El Torito boot img\s*:\s*\d+\s+(\S+)", boot)
    return {
        "size_bytes": size, "volume_id": volume.group(1) if volume else "",
        "paths": paths, "squashfs": squashfs,
        "bios": "BIOS" in platforms, "uefi": "UEFI" in platforms,
        "bootloader": [name for name, fragment in (("GRUB", "/boot/grub/"),
                       ("ISOLINUX", "/isolinux/"), ("SYSLINUX", "/syslinux/"))
                       if any(fragment in path for path in paths)],
        "boot_report": boot, "system_area_report": system, "replay_report": replay,
        "pvd_report": pvd, "adapter": None,
    }


class ISOAnalyzer:
    def __init__(self, sandbox):
        self.sandbox = sandbox

    def inspect(self, iso):
        iso = regular_file(iso)
        base = ["xorriso", "-abort_on", "FAILURE", "-indev", "/source.iso"]
        reads = {iso: "/source.iso"}
        listing = self.sandbox.run(base + ["-find", "/", "-exec", "echo", "--"], reads=reads)
        pvd = self.sandbox.run(base + ["-pvd_info"], reads=reads)
        boot = self.sandbox.run(base + ["-report_el_torito", "plain"], reads=reads)
        system = self.sandbox.run(base + ["-report_system_area", "plain"], reads=reads)
        replay = self.sandbox.run(base + ["-report_el_torito", "cmd"], reads=reads)
        return parse_analysis(listing, pvd, boot, system, replay, iso.stat().st_size)
