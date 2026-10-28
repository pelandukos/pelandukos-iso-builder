import copy
import json
from pathlib import Path

import pytest

from adapters.debian_live import DebianLiveAdapter
from core.boot_metadata import hybrid_apm_layout
from core.errors import BuilderError
from core.validator import compare_boot


@pytest.fixture
def analysis():
    return json.loads((Path(__file__).parent / "fixtures/pelandukos-hybrid-apm.json").read_text())


def test_reported_pelandukos_layout_is_supported(analysis):
    DebianLiveAdapter().validate_layout(analysis)
    assert hybrid_apm_layout(analysis) == {
        "block_size": 2048, "name": "EFI", "type": "Apple_HFS",
        "path": "/boot/grub/efi.img", "size_bytes": 9536 * 512,
    }


@pytest.mark.parametrize(("field", "old", "new", "reason"), [
    ("system_area_report", "Apple_HFS", "Apple_UNIX_SVR2", "name/type"),
    ("system_area_report", "APM gap fillers    :      0", "APM gap fillers    :      1", "gap"),
    ("system_area_report", "APM block size     :      2048", "APM block size     :      512", "block size"),
    ("system_area_report", "APM partition path :   1  /boot/grub/efi.img",
     "APM partition path :   1  /unrelated.img", "does not point"),
    ("system_area_report", "APM start and size :   1  1663  2384",
     "APM start and size :   1  1664  2384", "byte range"),
    ("system_area_report", "GPT start and size :   2  6652  9536",
     "GPT start and size :   2  6652  9537", "GPT"),
    ("system_area_report", "MBR partition      :   2   0x00  0xef",
     "MBR partition      :   2   0x00  0x83", "MBR"),
    ("replay_report", "-boot_image isolinux partition_entry=apm_hfsplus", "", "replay instructions"),
    ("replay_report", "-boot_image any efi_path='/boot/grub/efi.img'",
     "-boot_image any efi_path='/another.img'", "replay instructions"),
    ("system_area_report", "System area summary: MBR isohybrid cyl-align-off GPT APM",
     "System area summary: APM", "MBR isohybrid"),
])
def test_unknown_or_inconsistent_apm_stays_rejected(analysis, field, old, new, reason):
    assert old in analysis[field]
    analysis[field] = analysis[field].replace(old, new)
    with pytest.raises(BuilderError, match=reason):
        DebianLiveAdapter().validate_layout(analysis)


def test_additional_apm_partition_is_rejected(analysis):
    analysis["system_area_report"] += "\nAPM partition name : 2 unrelated\n"
    with pytest.raises(BuilderError, match="additional"):
        hybrid_apm_layout(analysis)


def test_apm_loss_during_rebuild_is_rejected(analysis):
    rebuilt = copy.deepcopy(analysis)
    rebuilt["system_area_report"] = rebuilt["system_area_report"].replace(
        "System area summary: MBR isohybrid cyl-align-off GPT APM",
        "System area summary: MBR isohybrid cyl-align-off GPT")
    with pytest.raises(BuilderError, match="lost source APM"):
        compare_boot(analysis, rebuilt)


def test_boot_log_volume_name_does_not_define_layout(analysis):
    # Human-readable banners/volume labels must not trigger architecture checks.
    analysis["system_area_report"] += "\nVolume id : 'MIPS SPARC PReP APM'\n"
    DebianLiveAdapter().validate_layout(analysis)


def test_non_x86_architecture_stays_rejected(analysis):
    analysis["system_area_report"] = analysis["system_area_report"].replace(
        "System area summary: MBR isohybrid cyl-align-off GPT APM",
        "System area summary: MIPS Big Endian")
    with pytest.raises(BuilderError, match="x86 BIOS/UEFI"):
        DebianLiveAdapter().validate_layout(analysis)
