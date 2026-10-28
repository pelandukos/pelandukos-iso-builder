"""Synthetic boot fixture, not a Debian desktop or installer acceptance image.

Uses an explicitly supplied readable host kernel, static BusyBox and installed
ISOLINUX/GRUB utilities. No downloads, mounts or root privileges.
"""
import gzip
import math
import shutil
import stat
import struct
import subprocess
from pathlib import Path


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


def fat16_efi(path, executable):
    """Write a minimal 16 MiB FAT16 EFI system partition for the test fixture."""
    image = bytearray(16 * 1024**2)
    boot = image
    boot[:3] = b"\xeb\x3c\x90"
    boot[3:11] = b"PELANDUK"
    struct.pack_into("<HBHBHHBHHHII", boot, 11, 512, 4, 1, 2, 512, 32768,
                     0xF8, 32, 32, 64, 0, 0)
    boot[36:39] = b"\x80\x00\x29"
    struct.pack_into("<I", boot, 39, 0x50454C41)
    boot[43:54] = b"PELANDUKEFI"
    boot[54:62] = b"FAT16   "
    boot[510:512] = b"\x55\xaa"
    fat = bytearray(32 * 512)
    struct.pack_into("<HHHH", fat, 0, 0xFFF8, 0xFFFF, 0xFFFF, 0xFFFF)
    data_offset = (1 + 64 + 32) * 512
    cluster_size = 2048
    def offset(cluster):
        return data_offset + (cluster - 2) * cluster_size
    def entry(name, attributes, cluster, size=0):
        result = bytearray(32)
        result[:11] = name
        result[11] = attributes
        struct.pack_into("<H", result, 26, cluster)
        struct.pack_into("<I", result, 28, size)
        return result
    image[(1 + 64)*512:(1+64)*512 + 32] = entry(b"EFI        ", 0x10, 2)
    image[offset(2):offset(2)+96] = (entry(b".          ", 0x10, 2) +
                                    entry(b"..         ", 0x10, 0) +
                                    entry(b"BOOT       ", 0x10, 3))
    content = Path(executable).read_bytes()
    clusters = math.ceil(len(content) / cluster_size)
    if clusters + 4 >= 8192:
        raise ValueError("EFI binary exceeds fixture FAT capacity")
    for cluster in range(4, 4 + clusters):
        struct.pack_into("<H", fat, cluster * 2, 0xFFFF if cluster == clusters + 3 else cluster + 1)
    image[offset(3):offset(3)+96] = (entry(b".          ", 0x10, 3) +
                                    entry(b"..         ", 0x10, 2) +
                                    entry(b"BOOTX64 EFI", 0x20, 4, len(content)))
    image[offset(4):offset(4)+len(content)] = content
    image[512:33*512] = fat
    image[33*512:65*512] = fat
    Path(path).write_bytes(image)


def create_boot_fixture(base, kernel, *, apm=False):
    base = Path(base)
    root, tree = base / "root", base / "tree"
    for relative in ("etc", "var/lib/dpkg", "lib/modules/fixture"):
        (root / relative).mkdir(parents=True, exist_ok=True)
    (root / "etc/os-release").write_text('ID=debian\nVERSION_ID=12\nPRETTY_NAME="Synthetic boot fixture"\n')
    (root / "etc/fixture").write_text("before\n")
    (root / "var/lib/dpkg/status").write_text(
        "Package: base-files\nVersion: 12\nArchitecture: amd64\nStatus: install ok installed\n\n")
    for relative in ("live", "isolinux", "boot/grub"):
        (tree / relative).mkdir(parents=True, exist_ok=True)
    subprocess.run(["mksquashfs", str(root), str(tree / "live/filesystem.squashfs"),
                    "-noappend", "-processors", "1"], check=True, capture_output=True)
    shutil.copyfile(kernel, tree / "live/vmlinuz")
    init = b"""#!/bin/busybox sh
/bin/busybox mount -t proc proc /proc
/bin/busybox mount -t sysfs sysfs /sys
/bin/busybox mount -t devtmpfs devtmpfs /dev
echo PELANDUKOS_SYNTHETIC_BOOT_OK
/bin/busybox sleep 1
/bin/busybox poweroff -f
"""
    initrd = newc([
        ("bin", stat.S_IFDIR | 0o755, b""), ("dev", stat.S_IFDIR | 0o755, b""),
        ("proc", stat.S_IFDIR | 0o755, b""), ("sys", stat.S_IFDIR | 0o755, b""),
        ("bin/busybox", stat.S_IFREG | 0o755, Path("/usr/bin/busybox").read_bytes()),
        ("init", stat.S_IFREG | 0o755, init),
    ])
    (tree / "live/initrd.img").write_bytes(initrd)
    shutil.copyfile("/usr/lib/ISOLINUX/isolinux.bin", tree / "isolinux/isolinux.bin")
    shutil.copyfile("/usr/lib/syslinux/modules/bios/ldlinux.c32", tree / "isolinux/ldlinux.c32")
    (tree / "isolinux/isolinux.cfg").write_text(
        "serial 0 115200\nprompt 0\ntimeout 1\ndefault live\nlabel live\n"
        "kernel /live/vmlinuz\nappend initrd=/live/initrd.img console=ttyS0,115200 rdinit=/init\n")
    grub = tree / "boot/grub/grub.cfg"
    grub.write_text(
        "serial --unit=0 --speed=115200\nterminal_output serial\n"
        "set timeout=0\nset default=0\nmenuentry 'Synthetic PelandukOS boot test' {\n"
        "linux /live/vmlinuz console=ttyS0,115200 rdinit=/init\ninitrd /live/initrd.img\n}\n")
    standalone = base / "efi.cfg"
    standalone.write_text("search --file --set=root /live/vmlinuz\nconfigfile /boot/grub/grub.cfg\n")
    binary = base / "BOOTX64.EFI"
    subprocess.run(["grub-mkstandalone", "-O", "x86_64-efi", "--locales=", "--fonts=",
                    "-o", str(binary), f"boot/grub/grub.cfg={standalone}"], check=True, capture_output=True)
    fat16_efi(tree / "boot/grub/efi.img", binary)
    iso = base / "boot-fixture.iso"
    arguments = ["xorriso", "-as", "mkisofs", "-o", str(iso), "-V", "PELANDUKOS_TEST",
                    "-R", "-J", "-b", "isolinux/isolinux.bin", "-c", "isolinux/boot.cat",
                    "-no-emul-boot", "-boot-load-size", "4", "-boot-info-table",
                    "-isohybrid-mbr", "/usr/lib/ISOLINUX/isohdpfx.bin",
                    "-eltorito-alt-boot", "-e", "boot/grub/efi.img", "-no-emul-boot",
                    "-isohybrid-gpt-basdat"]
    if apm:
        arguments += ["-isohybrid-apm-hfsplus", "-apm-block-size", "2048", "-partition_offset", "16"]
    subprocess.run([*arguments, str(tree)], check=True, capture_output=True)
    return iso
