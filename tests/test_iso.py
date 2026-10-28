import copy
import pytest

from adapters.debian_live import DebianLiveAdapter
from core.errors import BuilderError
from core.iso_analyzer import parse_analysis
from core.iso_builder import iso_filename
from core.validator import validate_boot, compare_boot, resolve_iso_path


def test_detect_boot_metadata():
    analysis = parse_analysis("'/live/filesystem.squashfs'\n'/isolinux/isolinux.cfg'\n'/live/vmlinuz'\n",
                              "Volume id    : 'TEST'", "El Torito boot img :   1  BIOS  y\n"
                              "El Torito boot img :   2  UEFI  y", "MBR GPT",
                              "-boot_image any replay", 123)
    assert analysis["bios"] and analysis["uefi"]
    assert analysis["volume_id"] == "TEST"
    assert analysis["squashfs"] == ["/live/filesystem.squashfs"]
    DebianLiveAdapter().validate_layout(analysis)
    layered = copy.deepcopy(analysis)
    layered["squashfs"].append("/live/second.squashfs")
    with pytest.raises(BuilderError, match="exactly"):
        DebianLiveAdapter().validate_layout(layered)
    lost_efi = analysis | {"uefi": False}
    with pytest.raises(BuilderError, match="UEFI"):
        compare_boot(analysis, lost_efi)


def test_boot_paths_and_symlink_safety(tmp_path):
    (tmp_path / "boot/grub").mkdir(parents=True)
    (tmp_path / "live").mkdir()
    (tmp_path / "live/vmlinuz").write_bytes(b"kernel")
    (tmp_path / "live/initrd.img").write_bytes(b"initrd")
    cfg = tmp_path / "boot/grub/grub.cfg"
    cfg.write_text("linux /live/vmlinuz boot=live\ninitrd /live/initrd.img\n")
    assert len(validate_boot(tmp_path)["boot_references"]) == 2
    (tmp_path / "live/vmlinuz").unlink()
    (tmp_path / "live/vmlinuz").symlink_to("/etc/passwd")
    with pytest.raises(BuilderError, match="Missing"):
        validate_boot(tmp_path)
    (tmp_path / "loop").symlink_to("/loop")
    with pytest.raises(BuilderError, match="Loop"):
        resolve_iso_path(tmp_path, "loop")


@pytest.mark.parametrize("name", ["../source.iso", "source.iso;rm", "/tmp/host.iso", "-out.iso", "disk"])
def test_output_filename_is_not_a_path(name):
    with pytest.raises(BuilderError):
        iso_filename(name)
