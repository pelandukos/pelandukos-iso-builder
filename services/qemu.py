"""Qt-independent QEMU command construction for preview and smoke tests."""
import os
import shutil
from pathlib import Path

from core.errors import BuilderError
from services.cpu_model import cpu_model


def find_ovmf():
    for directory in (Path("/usr/share/OVMF"), Path("/usr/share/edk2/ovmf")):
        for code, variables in (("OVMF_CODE_4M.fd", "OVMF_VARS_4M.fd"),
                                ("OVMF_CODE.fd", "OVMF_VARS.fd")):
            if (directory / code).is_file() and (directory / variables).is_file():
                return directory / code, directory / variables
    raise BuilderError("UEFI preview requires matching OVMF CODE and VARS firmware. Install ovmf.")


def qemu_arguments(iso, temporary, mode, *, memory=2048):
    iso, temporary = Path(iso).resolve(strict=True), Path(temporary)
    if not iso.is_file():
        raise BuilderError("Select a regular ISO file.")
    if "," in str(temporary):
        raise BuilderError("Preview temporary directory cannot contain a comma.")
    acceleration = "kvm" if os.access("/dev/kvm", os.R_OK | os.W_OK) else "tcg"
    args = ["-name", "PelandukOS preview", "-m", str(memory), "-smp", "2",
            "-accel", acceleration, "-cpu", cpu_model(acceleration),
            "-boot", "d", "-cdrom", str(iso), "-nic", "none", "-display", "none",
            "-global", "isa-fdc.fdtypeA=none", "-global", "isa-fdc.fdtypeB=none",
            "-device", "qemu-xhci", "-device", "usb-tablet",
            "-qmp", f"unix:{temporary / 'qmp.sock'},server=on,wait=off",
            "-serial", f"file:{temporary / 'serial.log'}", "-no-reboot",
            "-sandbox", "on,obsolete=deny,elevateprivileges=deny,spawn=deny,resourcecontrol=deny"]
    if mode == "UEFI":
        code, variables = find_ovmf()
        shutil.copyfile(variables, temporary / "vars.fd")
        args += ["-drive", f"if=pflash,format=raw,readonly=on,file={code}",
                 "-drive", f"if=pflash,format=raw,file={temporary / 'vars.fd'}"]
    elif mode != "BIOS":
        raise BuilderError("Choose BIOS or UEFI.")
    return args, acceleration
