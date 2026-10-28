import os
import json
import shutil

import pytest

from boot_fixture import create_boot_fixture
from scripts.smoke_boot import smoke


@pytest.mark.integration
@pytest.mark.parametrize("mode", ["BIOS", "UEFI"])
@pytest.mark.parametrize("apm", [False, True], ids=["mbr-gpt", "mbr-gpt-apm"])
def test_rebuilt_synthetic_iso_boots(engine, tmp_path, mode, apm):
    kernel = os.environ.get("PELANDUKOS_BOOT_KERNEL")
    if not kernel:
        pytest.skip("Set PELANDUKOS_BOOT_KERNEL to a readable x86_64 kernel for synthetic VM boot tests")
    for tool in ("grub-mkstandalone", "qemu-system-x86_64"):
        if not shutil.which(tool):
            pytest.skip(f"Missing optional boot fixture tool: {tool}")
    iso = create_boot_fixture(tmp_path / "fixture", kernel, apm=apm)
    project = engine.handle({"operation": "create", "name": f"Boot {mode}"})
    pid = project["id"]
    engine.handle({"operation": "import", "project_id": pid, "source": str(iso)})
    engine.handle({"operation": "fs_write", "project_id": pid, "path": "etc/fixture", "text": "after\n"})
    project = engine.handle({"operation": "build", "project_id": pid, "filename": "boot-test.iso"})
    release = project["last_build"]
    rebuilt = engine.store.path(pid) / "outputs" / release["build_id"] / release["filename"]
    report = smoke(rebuilt, mode, "PELANDUKOS_SYNTHETIC_BOOT_OK", timeout=90)
    (tmp_path / f"{mode.lower()}-boot-report.json").write_text(json.dumps(report, indent=2) + "\n")
    assert report["passed"], report["serial_log"][-5000:] + report["qemu_log"]
