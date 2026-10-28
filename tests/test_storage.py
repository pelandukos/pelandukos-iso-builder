import json
import os
from pathlib import Path

import pytest

from core.errors import BuilderError
from core.project_manager import ProjectStore
from core.storage import filesystem_info, tree_inventory
from services.privileged_helper import UserFiles
from services.storage_manager import StorageManager


@pytest.fixture
def manager(tmp_path):
    return StorageManager(ProjectStore(tmp_path / "default", os.getuid()), UserFiles(os.getuid()))


def selected_project(manager, tmp_path):
    parent = tmp_path / "selected"
    parent.mkdir()
    return manager.handle({"operation": "create", "name": "Selected", "storage_folder": str(parent)})


def test_selected_storage_reopens_and_lists(manager, tmp_path):
    project = selected_project(manager, tmp_path)
    path = Path(project["storage_path"])
    assert path.is_relative_to(tmp_path / "selected")
    assert not manager.default.path(project["id"]).exists()
    assert "/proc/" not in path.as_posix()
    reopened = StorageManager(manager.default, manager.user_files)
    assert reopened.handle({"operation": "get", "project_id": project["id"]}) == project
    assert reopened.handle({"operation": "list_projects"}) == [project]
    assert json.loads((path / "project.json").read_text())["storage_path"] == str(path)


def test_missing_drive_is_visible(manager, tmp_path):
    project = selected_project(manager, tmp_path)
    path = Path(project["storage_path"]).parent
    path.rename(path.with_name("disconnected"))
    listed = manager.handle({"operation": "list_projects"})
    assert listed[0]["id"] == project["id"]
    assert listed[0]["state"] == "storage unavailable"
    with pytest.raises(BuilderError, match="unavailable"):
        manager.handle({"operation": "open", "project_id": project["id"]})


def test_marker_swap_and_symlink_refused(manager, tmp_path):
    project = selected_project(manager, tmp_path)
    root = Path(project["storage_path"]).parent
    (root / ".storage-id").write_text("replacement")
    with pytest.raises(BuilderError, match="replaced"):
        manager.handle({"operation": "get", "project_id": project["id"]})
    moved = root.with_name("real-store")
    root.rename(moved)
    root.symlink_to(moved, target_is_directory=True)
    with pytest.raises(BuilderError, match="unavailable"):
        manager.handle({"operation": "get", "project_id": project["id"]})


def test_pinned_root_survives_parent_rename(manager, tmp_path):
    project = selected_project(manager, tmp_path)
    with manager.locked_registry() as registry:
        location = manager.location_for(registry, project["id"])
        with manager.open_location(registry, location) as store:
            Path(project["storage_path"]).parent.rename(tmp_path / "renamed")
            assert store.load(project["id"])["name"] == "Selected"


def test_unsupported_filesystem_does_not_create_storage(manager, tmp_path, monkeypatch):
    parent = tmp_path / "unsupported"
    parent.mkdir()
    monkeypatch.setattr("services.storage_manager.filesystem_info",
                        lambda fd: {"supported": False, "filesystem": "FAT"})
    with pytest.raises(BuilderError, match="FAT"):
        manager.handle({"operation": "create", "name": "Rejected", "storage_folder": str(parent)})
    assert list(parent.iterdir()) == []


def test_storage_check_and_invalid_relative_path(manager, tmp_path):
    result = manager.handle({"operation": "storage_check", "folder": str(tmp_path)})
    assert result["free_bytes"] > 0
    fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        assert filesystem_info(fd)["supported"]
    finally:
        os.close(fd)
    with pytest.raises(BuilderError, match="absolute"):
        manager.handle({"operation": "create", "name": "Bad", "storage_folder": "../bad"})


@pytest.mark.integration
def test_selected_import_edit_move_rebuild(manager, fixture_iso, tmp_path):
    project = selected_project(manager, tmp_path)
    pid = project["id"]
    old = Path(project["storage_path"])
    def call(operation, **values):
        return manager.handle({"operation": operation, "project_id": pid, **values})
    call("import", source=str(fixture_iso))
    call("fs_write", path="etc/example.conf", text="selected-store=true\n")
    before = tree_inventory(old / "rootfs")
    destination = tmp_path / "another-drive"
    destination.mkdir()
    moved = call("move_project", storage_folder=str(destination), confirmed=True)
    new = Path(moved["storage_path"])
    assert not old.exists()
    assert new.is_relative_to(destination)
    assert tree_inventory(new / "rootfs") == before
    assert call("open")["history"][-1]["operation"] == "project_moved"
    assert call("fs_read", path="etc/example.conf") == "selected-store=true\n"
    built = call("build", filename="relocated.iso")
    assert built["last_build"]["validation"]["payload_checksum"]
    # Return to the legacy store, preserving the project reference ID.
    returned = call("move_project", storage_folder="", confirmed=True)
    assert Path(returned["storage_path"]) == manager.default.path(pid)
    assert not new.exists()
    assert len(manager.handle({"operation": "list_projects"})) == 1


def test_failed_move_retains_original(manager, tmp_path, monkeypatch):
    project = manager.handle({"operation": "create", "name": "Original"})
    original = Path(project["storage_path"])
    destination = tmp_path / "destination"
    destination.mkdir()
    def fail(*args, **kwargs):
        raise BuilderError("copy failed")
    monkeypatch.setattr("services.storage_manager.Sandbox.run", fail)
    with pytest.raises(BuilderError, match="copy failed"):
        manager.handle({"operation": "move_project", "project_id": project["id"],
                        "storage_folder": str(destination), "confirmed": True})
    assert original.exists()
    assert manager.handle({"operation": "get", "project_id": project["id"]})["storage_path"] == str(original)
    assert not list(destination.glob("*/.moving-*"))
    with pytest.raises(BuilderError, match="Confirm"):
        manager.handle({"operation": "move_project", "project_id": project["id"]})


@pytest.mark.integration
def test_corrupt_copy_never_replaces_original(manager, tmp_path, monkeypatch):
    from services.commands import Sandbox
    project = manager.handle({"operation": "create", "name": "Original"})
    original = Path(project["storage_path"])
    (original / "source.iso").write_bytes(b"original")
    destination = tmp_path / "destination"
    destination.mkdir()
    run = Sandbox.run
    def corrupt(self, command, **options):
        result = run(self, command, **options)
        staging = next(iter(options["writes"]))
        (staging / "source.iso").write_bytes(b"tampered")
        return result
    monkeypatch.setattr(Sandbox, "run", corrupt)
    with pytest.raises(BuilderError, match="verification failed"):
        manager.handle({"operation": "move_project", "project_id": project["id"],
                        "storage_folder": str(destination), "confirmed": True})
    assert (original / "source.iso").read_bytes() == b"original"
    assert manager.handle({"operation": "get", "project_id": project["id"]})["storage_path"] == str(original)
