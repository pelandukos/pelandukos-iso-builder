"""Parse xorriso report records; never execute its reported command text."""
import re
import shlex

from core.errors import BuilderError


def system_area_summary(analysis):
    match = re.search(r"^System area summary:\s*(.*)$",
                      analysis["system_area_report"], re.MULTILINE)
    return match[1].strip() if match else ""


def hybrid_apm_layout(analysis):
    """Recognize only the tested ISOLINUX MBR/GPT/APM alias of one EFI image.

    APM names/types and byte ranges must agree with the same El Torito, GPT and
    MBR payload. Relocation during rebuild is permitted, dropping/changing the
    payload or adding unrelated Apple partitions is not.
    """
    summary = system_area_summary(analysis)
    if "APM" not in summary.split():
        return None

    def require(condition, reason):
        if not condition:
            raise BuilderError(f"Unsupported APM layout: {reason}. "
                               "Only the replayable ISOLINUX hybrid EFI mapping is supported.")

    system = analysis["system_area_report"]
    boot = analysis["boot_report"]
    require({"MBR", "GPT", "isohybrid"}.issubset(summary.split()),
            "expected an MBR isohybrid with GPT")
    require(analysis["bios"] and analysis["uefi"] and "ISOLINUX" in analysis["bootloader"],
            "missing ISOLINUX BIOS/UEFI boot equipment")

    images = {}
    for match in re.finditer(
        r"^El Torito boot img\s*:\s*(\d+)\s+(\S+)\s+(\S+)\s+(\S+)"
        r"\s+\S+\s+\S+\s+(\d+)\s+(\d+)\s*$", boot, re.MULTILINE
    ):
        images[int(match[1])] = {"platform": match[2], "bootable": match[3],
                                "emulation": match[4], "size": int(match[5]) * 512,
                                "start": int(match[6]) * 2048}
    paths = {int(match[1]): match[2].strip() for match in re.finditer(
        r"^El Torito img path\s*:\s*(\d+)\s+(.+)$", boot, re.MULTILINE)}
    efi_images = [number for number, value in images.items() if value["platform"] == "UEFI"]
    require(len(efi_images) == 1, "expected exactly one EFI boot image")
    efi_number = efi_images[0]
    efi = images[efi_number]
    efi_path = paths.get(efi_number)
    require(efi["bootable"] == "y" and efi["emulation"] == "none" and efi["size"] > 0,
            "unsupported EFI boot entry")
    require(efi_path and efi_path.startswith("/") and efi_path in analysis["paths"],
            "EFI payload is not a file in the ISO")
    bios_images = [number for number, value in images.items() if value["platform"] == "BIOS"]
    require(len(bios_images) == 1, "expected exactly one BIOS boot image")
    bios = bios_images[0]
    require(images[bios]["bootable"] == "y" and images[bios]["emulation"] == "none",
            "unsupported BIOS boot entry")
    require(re.search(rf"^El Torito img opts\s*:\s*{bios}\s+.*\bisohybrid-suitable\b",
                      boot, re.MULTILINE), "BIOS image is not isohybrid-suitable")

    block = re.search(r"^APM block size\s*:\s*(\d+)\s*$", system, re.MULTILINE)
    gaps = re.search(r"^APM gap fillers\s*:\s*(\d+)\s*$", system, re.MULTILINE)
    require(block and int(block[1]) == 2048, "unrecognized APM block size")
    require(gaps and int(gaps[1]) == 0, "additional APM gap partitions")
    entries = {}
    for match in re.finditer(r"^APM partition (name|type|path)\s*:\s*(\d+)\s+(.+)$",
                             system, re.MULTILINE):
        entries.setdefault(int(match[2]), {})[match[1]] = match[3].strip()
    for match in re.finditer(r"^APM start and size\s*:\s*(\d+)\s+(\d+)\s+(\d+)\s*$",
                             system, re.MULTILINE):
        entries.setdefault(int(match[1]), {}).update(
            start=int(match[2]) * 2048, size=int(match[3]) * 2048)
    require(len(entries) == 1, "additional or missing Apple partitions")
    partition = next(iter(entries.values()))
    require(partition.get("name") == "EFI" and partition.get("type") == "Apple_HFS",
            "unrecognized Apple partition name/type")
    require(partition.get("path") == efi_path, "Apple partition does not point to the EFI image")
    require(partition.get("start") == efi["start"] and partition.get("size") == efi["size"],
            "Apple partition byte range differs from the EFI boot entry")

    gpt_paths = {int(match[1]): match[2].strip() for match in re.finditer(
        r"^GPT partition path\s*:\s*(\d+)\s+(.+)$", system, re.MULTILINE)}
    gpt_ranges = {int(match[1]): (int(match[2]) * 512, int(match[3]) * 512)
                  for match in re.finditer(
                      r"^GPT start and size\s*:\s*(\d+)\s+(\d+)\s+(\d+)\s*$", system, re.MULTILINE)}
    require(any(path == efi_path and gpt_ranges.get(number) == (efi["start"], efi["size"])
                for number, path in gpt_paths.items()), "GPT does not expose the same EFI byte range")
    mbr_paths = {int(match[1]): match[2].strip() for match in re.finditer(
        r"^MBR partition path\s*:\s*(\d+)\s+(.+)$", system, re.MULTILINE)}
    mbr_entries = {int(match[1]): (int(match[2], 16), int(match[3]) * 512, int(match[4]) * 512)
                   for match in re.finditer(
                       r"^MBR partition\s*:\s*(\d+)\s+0x[0-9a-fA-F]+\s+(0x[0-9a-fA-F]+)"
                       r"\s+(\d+)\s+(\d+)\s*$", system, re.MULTILINE)}
    require(any(path == efi_path and mbr_entries.get(number) == (0xEF, efi["start"], efi["size"])
                for number, path in mbr_paths.items()), "MBR does not expose the same EFI byte range")

    # Check that replay actually associates both partition entries with this
    # EFI image, rather than accepting a matching word elsewhere in a log.
    slot = {}
    slots = []
    apm_block = None
    for line in analysis["replay_report"].splitlines():
        if not line.startswith("-boot_image "):
            continue
        try:
            words = shlex.split(line)
        except ValueError as error:
            raise BuilderError("Malformed xorriso boot replay record.") from error
        require(len(words) == 3, "malformed boot replay record")
        setting = words[2]
        if setting == "next":
            slots.append(slot)
            slot = {}
        elif "=" in setting:
            key, value = setting.split("=", 1)
            slot.setdefault(key, []).append(value)
            if key == "apm_block_size":
                apm_block = value
    slots.append(slot)
    require(apm_block == "2048", "missing APM block-size replay setting")
    require(any(slot.get("efi_path") == [efi_path]
                and {"apm_hfsplus", "gpt_basdat"}.issubset(slot.get("partition_entry", []))
                for slot in slots), "missing EFI APM/GPT replay instructions")
    return {"block_size": 2048, "name": "EFI", "type": "Apple_HFS",
            "path": efi_path, "size_bytes": efi["size"]}
