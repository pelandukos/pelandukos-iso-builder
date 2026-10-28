"""Opt-in installed-helper acceptance; never runs unless a real ISO is supplied."""
import json
import os
import subprocess
from pathlib import Path

import pytest

from core.dependencies import HELPER
from core.util import digest
from scripts.smoke_boot import smoke


@pytest.mark.real_iso
def test_real_iso_with_installed_polkit_helper(tmp_path):
    source = os.environ.get("PELANDUKOS_TEST_ISO")
    if not source:
        pytest.skip("Set PELANDUKOS_TEST_ISO to opt in to full privileged real-ISO acceptance")
    if not HELPER.is_file():
        pytest.skip("Install the root-owned helper package before real-ISO acceptance")
    source = Path(source).resolve(strict=True)
    before = digest(source)
    project_id = None
    def call(operation, **values):
        request = {"operation": operation, **values}
        if project_id:
            request["project_id"] = project_id
        process = subprocess.run(["pkexec", str(HELPER)], input=json.dumps(request) + "\n",
                                 capture_output=True, text=True, timeout=7200)
        events = [json.loads(line) for line in process.stdout.splitlines() if line.startswith("{")]
        assert process.returncode == 0, process.stderr + str(events[-3:])
        return next(event["result"] for event in reversed(events) if event["type"] == "result")
    project = call("create", name="Real ISO acceptance test")
    project_id = project["id"]
    project = call("import", source=str(source))
    assert project["state"] == "ready"
    call("fs_write", path="etc/pelandukos-builder-acceptance.conf", text="acceptance_test=true\n")
    call("open")
    assert call("fs_read", path="etc/pelandukos-builder-acceptance.conf") == "acceptance_test=true\n"
    project = call("build", filename="PelandukOS-acceptance-amd64.iso")
    call("export_build", destination=str(tmp_path))
    output = tmp_path / project["last_build"]["filename"]
    assert digest(output) == project["last_build"]["sha256"]
    assert digest(source) == before
    marker = os.environ.get("PELANDUKOS_TEST_BOOT_MARKER")
    if marker:
        for capability, mode in (("bios", "BIOS"), ("uefi", "UEFI")):
            if project["analysis"][capability]:
                result = smoke(output, mode, marker, timeout=300)
                (tmp_path / f"{mode.lower()}-boot.json").write_text(json.dumps(result, indent=2))
                assert result["passed"], result["serial_log"][-5000:] + result["qemu_log"]
