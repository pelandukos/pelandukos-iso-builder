import pytest

from core.errors import BuilderError
from core.iso_extractor import require_space
from core.validator import kernel_inventory_digest


def test_space_guard_rejects_unavailable_capacity(tmp_path):
    with pytest.raises(BuilderError, match="Insufficient free space"):
        require_space(tmp_path, 2**70)


@pytest.mark.integration
def test_failed_build_keeps_edits_and_recovers(engine, fixture_iso):
    project = engine.store.create("Failed build")
    pid = project["id"]
    request = {"project_id": pid}
    project = engine.handle(request | {"operation": "import", "source": str(fixture_iso)})
    engine.handle(request | {"operation": "fs_write", "path": "etc/example.conf", "text": "saved before failure"})
    sandbox = engine.sandbox
    class FailingSandbox:
        def run(self, *_args, **_kwargs):
            raise BuilderError("Injected compressor failure")
    engine.sandbox = FailingSandbox()
    try:
        with pytest.raises(BuilderError, match="Injected compressor"):
            engine.handle(request | {"operation": "build", "filename": "failed.iso"})
    finally:
        engine.sandbox = sandbox
    loaded = engine.handle(request | {"operation": "open"})
    assert loaded["state"] == "ready"
    assert loaded["last_build"] is None
    assert engine.handle(request | {"operation": "fs_read", "path": "etc/example.conf"}) == "saved before failure"
    assert not (engine.store.path(pid) / "build-stage").exists()


def test_same_version_kernel_edits_detected(tmp_path):
    directory = tmp_path / "lib/modules/version"
    directory.mkdir(parents=True)
    module = directory / "module.ko"
    module.write_text("original")
    before = kernel_inventory_digest(tmp_path)
    module.write_text("modified")
    assert kernel_inventory_digest(tmp_path) != before
