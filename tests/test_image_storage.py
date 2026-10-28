import errno
import json
import os
import uuid
from pathlib import Path

import pytest

from core.errors import BuilderError
from core.project_manager import ProjectStore
from services.image_storage import allocate, container_size, validate_image, LoopConfig
from services.privileged_helper import UserFiles
from services.storage_bridge import engine_call, worker


def test_container_size_is_bounded():
    assert container_size(128) == 128 * 1024**3
    for value in (0, -1, 16385, True, "128", 4.5):
        with pytest.raises(BuilderError):
            container_size(value)
    assert __import__("ctypes").sizeof(LoopConfig) == 304


def test_allocation_fallback_and_no_space(tmp_path, monkeypatch):
    path = tmp_path / "image"
    with path.open("w+b") as stream:
        def unsupported(*args):
            raise OSError(errno.EOPNOTSUPP, "unsupported")
        monkeypatch.setattr(os, "posix_fallocate", unsupported)
        allocate(stream.fileno(), 1024**2, lambda text: None)
        assert path.stat().st_size == 1024**2
        def full(*args):
            raise OSError(errno.ENOSPC, "full")
        monkeypatch.setattr(os, "posix_fallocate", full)
        with pytest.raises(OSError, match="full"):
            allocate(stream.fileno(), 2 * 1024**2, lambda text: None)


def test_invalid_image_identity_rejected(tmp_path):
    path = tmp_path / "image"
    identity = uuid.uuid4()
    header = bytearray(2048)
    header[1080:1082] = b"\x53\xef"
    header[1128:1144] = identity.bytes
    path.write_bytes(header)
    location = {"uuid": str(identity), "size": 2048}
    with path.open("rb") as stream:
        validate_image(stream.fileno(), location)
        with pytest.raises(BuilderError, match="identity"):
            validate_image(stream.fileno(), location | {"uuid": str(uuid.uuid4())})
        with pytest.raises(BuilderError, match="resized"):
            validate_image(stream.fileno(), location | {"size": 4096})
    for offset in (0xD0, 0xE4, 0x200):
        unsafe = bytearray(header)
        unsafe[1024 + offset] = 1
        path.write_bytes(unsafe)
        with path.open("rb") as stream, pytest.raises(BuilderError, match="forbidden"):
            validate_image(stream.fileno(), location)


@pytest.mark.integration
def test_confined_worker_import_build_export_reopen(tmp_path, fixture_iso):
    store = ProjectStore(tmp_path / "store", os.getuid())
    files = UserFiles(os.getuid())
    project = engine_call(store, files, {"operation": "create", "name": "Confined", "version": "3.0"},
                          lambda event: None)
    assert project["release"]["version"] == "3.0"
    events = []
    def call(operation, **values):
        return engine_call(store, files, {"operation": operation, "project_id": project["id"], **values},
                           events.append)
    assert call("import", source=str(fixture_iso))["state"] == "ready"
    call("fs_write", path="etc/example.conf", text="confined=true\n")
    assert call("fs_read", path="etc/example.conf") == "confined=true\n"
    built = call("build", filename="confined.iso", version="3.1")
    assert built["last_build"]["validation"]["payload_checksum"]
    assert built["last_build"]["release"]["version"] == "3.1"
    export = tmp_path / "export"
    export.mkdir()
    result = call("export_build", destination=str(export))
    assert len(result["files"]) == 4
    assert Path(result["files"][0]).is_file()
    call("export_project", destination=str(export / "project.json"))
    assert json.loads((export / "project.json").read_text())["id"] == project["id"]
    assert call("open")["state"] == "ready"
    measured = [event for event in events if event["type"] == "progress"]
    assert any(event["message"] == "Copying source ISO" and event["percent"] == 100 for event in measured)
    assert any(event["message"] == "Compressing Linux root filesystem" for event in measured)


@pytest.mark.integration
def test_confined_worker_cannot_follow_link_to_host(tmp_path):
    store = ProjectStore(tmp_path / "store", os.getuid())
    project = store.create("Tampered")
    secret = tmp_path / "host-secret"
    secret.write_text('{"sensitive": "host"}')
    manifest = store.path(project["id"]) / "project.json"
    manifest.unlink()
    manifest.symlink_to(secret)
    with pytest.raises(BuilderError, match="manifest"):
        engine_call(store, UserFiles(os.getuid()), {"operation": "get", "project_id": project["id"]},
                    lambda event: None)
    assert secret.read_text() == '{"sensitive": "host"}'


@pytest.mark.integration
def test_isolated_copy_and_cleanup_preserve_host(tmp_path):
    source, destination = tmp_path / "source", tmp_path / "destination"
    source.mkdir()
    destination.mkdir()
    secret = tmp_path / "secret"
    secret.write_text("host")
    (source / "link").symlink_to(secret)
    (source / "data").write_text("copy")
    project = {"history": [], "id": uuid.uuid4().hex}
    worker({"action": "copy", "project": project, "origin": "old", "destination": "new"},
           reads={source: "/source"}, writes={destination: "/staging"})
    assert (destination / "link").is_symlink()
    worker({"action": "clear"}, writes={destination: "/staging"})
    assert list(destination.iterdir()) == []
    assert secret.read_text() == "host"


def test_worker_rejects_substituted_directory(tmp_path):
    secret = tmp_path / "host"
    secret.mkdir()
    (secret / "keep").write_text("host data")
    substituted = tmp_path / "substituted"
    substituted.symlink_to(secret)
    with pytest.raises(OSError):
        worker({"action": "clear"}, writes={substituted: "/staging"})
    assert (secret / "keep").read_text() == "host data"


def test_failed_unmount_never_deletes_mounted_contents(tmp_path, monkeypatch):
    from contextlib import contextmanager
    from services import image_storage
    image = tmp_path / "image"
    image.write_bytes(b"fixture")
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    monkeypatch.setattr(image_storage, "validate_image", lambda *args: None)
    @contextmanager
    def loop(*args):
        yield "/dev/fixture"
    monkeypatch.setattr(image_storage, "loop_device", loop)
    def mount_api(name, *args):
        if name == "umount2":
            raise OSError(errno.EBUSY, "unmount failed")
    monkeypatch.setattr(image_storage, "libc_call", mount_api)
    with pytest.raises(OSError, match="unmount failed"):
        with image_storage.mounted_image({"path": str(image), "size": 7},
                                         UserFiles(os.getuid()), runtime) as root:
            (root / "must-survive").write_text("data")
    assert next(runtime.glob("*/must-survive")).read_text() == "data"
