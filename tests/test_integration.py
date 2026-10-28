import json
import os
import stat

import pytest

from core.util import digest
from core.errors import BuilderError

pytestmark = pytest.mark.integration


def test_extract_edit_rebuild_readback_reopen(engine, fixture_iso, tmp_path):
    original_checksum = digest(fixture_iso)
    project = engine.handle({"operation": "create", "name": "Roundtrip"})
    pid = project["id"]
    def call(operation, **values):
        return engine.handle({"operation": operation, "project_id": pid, **values})
    project = call("import", source=str(fixture_iso))
    assert project["state"] == "ready"
    assert project["analysis"]["adapter"] == "debian-live-single"
    assert project["analysis"]["architecture"] == "amd64"
    root = engine.store.path(pid) / "rootfs"
    assert stat.S_IMODE((root / "etc/example.conf").stat().st_mode) == 0o640
    assert os.getxattr(root / "etc/example.conf", "user.test") == b"preserved"
    assert (root / "etc/hardlink-a").stat().st_ino == (root / "etc/hardlink-b").stat().st_ino
    assert (root / "etc/absolute-link").is_symlink()
    call("fs_write", path="etc/example.conf", text="customized=true\n")
    reopened = call("open")
    assert call("fs_read", path="etc/example.conf") == "customized=true\n"
    assert reopened["history"][-1]["operation"] == "fs_write"
    built = call("build", filename="PelandukOS-fixture-amd64.iso", version="2.1-beta1")
    release = built["last_build"]
    output_directory = tmp_path / "export"
    output_directory.mkdir()
    exported = call("export_build", destination=str(output_directory))
    assert len(exported["files"]) == 4
    image = output_directory / release["filename"]
    assert image.is_file()
    assert digest(image) == release["sha256"]
    assert original_checksum == digest(fixture_iso)
    metadata = json.loads((output_directory / (image.name + ".release.json")).read_text())
    assert metadata["release"]["version"] == "2.1-beta1"
    assert call("open")["release"]["version"] == "2.1-beta1"
    assert metadata["validation"]["bios_boot_test"] == "not_run"
    assert metadata["validation"]["payload_checksum"] is True
    private = engine.store.path(pid)
    assert not (private / "build-stage").exists()
    assert not (private / "iso-stage").exists()
    assert not (private / "root-stage").exists()
    assert call("open")["last_build"] == release
    # Inspect the *rebuilt* SquashFS, not only the editable workspace.
    verify = tmp_path / "verify"
    verify.mkdir()
    engine.sandbox.run(
        ["xorriso", "-osirrox", "on", "-indev", "/image.iso", "-extract",
         "/live/filesystem.squashfs", "/out/root.squashfs"],
        reads={image: "/image.iso"}, writes={verify: "/out"})
    result = engine.sandbox.run(["unsquashfs", "-cat", "/root.squashfs", "etc/example.conf"],
                                reads={verify / "root.squashfs": "/root.squashfs"})
    assert result == "customized=true\n"


def test_failed_import_preserves_source_and_reopens(engine, tmp_path):
    source = tmp_path / "bad.iso"
    source.write_text("not an ISO")
    project = engine.store.create("Bad image")
    with pytest.raises(BuilderError):
        engine.handle({"operation": "import", "project_id": project["id"], "source": str(source)})
    assert source.read_text() == "not an ISO"
    recovered = engine.handle({"operation": "open", "project_id": project["id"]})
    assert recovered["state"] == "failed"
    assert recovered["history"][-1]["operation"] == "job_failed"
