import os
import shutil
import subprocess
from pathlib import Path

import pytest

from core.project_manager import ProjectStore
from services.engine import Engine
from services.privileged_helper import UserFiles


@pytest.fixture
def engine(tmp_path):
    return Engine(ProjectStore(tmp_path / "store", os.getuid()), UserFiles(os.getuid()))


@pytest.fixture(scope="session")
def fixture_iso(tmp_path_factory):
    for tool in ("xorriso", "mksquashfs", "unsquashfs", "bwrap"):
        if not shutil.which(tool):
            pytest.skip(f"{tool} is not installed")
    boot = Path("/usr/lib/ISOLINUX/isolinux.bin")
    if not boot.exists():
        pytest.skip("Install isolinux and syslinux-common for synthetic boot metadata fixture")
    base = tmp_path_factory.mktemp("synthetic-iso")
    root, tree = base / "root", base / "tree"
    for relative in ("etc", "var/lib/dpkg", "lib/modules/fixture-kernel", "home"):
        (root / relative).mkdir(parents=True, exist_ok=True)
    (root / "etc/os-release").write_text('ID=debian\nVERSION_ID="12"\nPRETTY_NAME="Debian fixture"\n')
    (root / "etc/example.conf").write_text("original=true\n")
    (root / "etc/example.conf").chmod(0o640)
    os.setxattr(root / "etc/example.conf", "user.test", b"preserved")
    (root / "etc/absolute-link").symlink_to("/etc/example.conf")
    (root / "etc/hardlink-a").write_text("hardlink data\n")
    os.link(root / "etc/hardlink-a", root / "etc/hardlink-b")
    (root / "var/lib/dpkg/status").write_text(
        "Package: base-files\nStatus: install ok installed\nArchitecture: amd64\nVersion: 12\n\n")
    for relative in ("live", "isolinux", ".disk"):
        (tree / relative).mkdir(parents=True)
    (tree / ".disk/info").write_text("Debian Live fixture amd64\n")
    (tree / "live/vmlinuz").write_bytes(b"synthetic kernel, not bootable\n")
    (tree / "live/initrd.img").write_bytes(b"synthetic initramfs, not bootable\n")
    (tree / "live/filesystem.size").write_text("1\n")
    (tree / "live/filesystem.packages").write_text("base-files 12\n")
    shutil.copyfile(boot, tree / "isolinux/isolinux.bin")
    (tree / "isolinux/isolinux.cfg").write_text(
        "default live\nlabel live\n  kernel /live/vmlinuz\n"
        "  append initrd=/live/initrd.img boot=live\n")
    subprocess.run(["mksquashfs", str(root), str(tree / "live/filesystem.squashfs"),
                    "-noappend", "-processors", "1", "-quiet"], check=True, capture_output=True)
    from core.util import digest
    (tree / "md5sum.txt").write_text(
        f"{digest(tree / 'live/filesystem.squashfs', 'md5')}  ./live/filesystem.squashfs\n"
        f"{digest(tree / 'isolinux/isolinux.bin', 'md5')}  ./isolinux/isolinux.bin\n")
    iso = base / "fixture.iso"
    subprocess.run(["xorriso", "-as", "mkisofs", "-o", str(iso), "-V", "DEBIAN_FIXTURE",
                    "-R", "-J", "-b", "isolinux/isolinux.bin", "-c", "isolinux/boot.cat",
                    "-no-emul-boot", "-boot-load-size", "4", "-boot-info-table",
                    "-isohybrid-mbr", "/usr/lib/ISOLINUX/isohdpfx.bin", str(tree)],
                   check=True, capture_output=True)
    return iso
