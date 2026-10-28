#!/usr/bin/env python3
"""Opt-in privileged storage integration; creates/removes only its own fixtures.

Run through pkexec with --folder on the filesystem to test and --iso pointing
to the small synthetic fixture from pytest. Never pass a large production ISO.
"""
import argparse
import json
import os
import shutil
import stat
import sys
import tempfile
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.project_manager import ProjectStore
from core.util import atomic_json, digest
from services.image_storage import create_image
from services.privileged_helper import UserFiles
from services.storage_manager import StorageManager


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--folder", required=True)
    parser.add_argument("--iso", required=True)
    args = parser.parse_args()
    uid = int(os.environ.get("PKEXEC_UID", "-1"))
    if os.geteuid() != 0 or uid <= 0:
        parser.error("Run this opt-in test through pkexec as a desktop user.")
    if Path(args.iso).stat().st_size > 32 * 1024**2:
        parser.error("Use a synthetic ISO smaller than 32 MiB.")
    files = UserFiles(uid)
    original = digest(args.iso)
    with files.identity():
        selected = Path(tempfile.mkdtemp(prefix="pelandukos-container-test-", dir=args.folder))
        export = selected / "export"
        export.mkdir()
    try:
        with tempfile.TemporaryDirectory(prefix="pelandukos-container-registry-") as temporary:
            store = ProjectStore(Path(temporary) / "store", uid)
            manager = StorageManager(store, files)
            fd = os.open(selected, os.O_RDONLY | os.O_DIRECTORY)
            try:
                location = create_image(fd, files, 512 * 1024**2, print)
            finally:
                os.close(fd)
            location["parent"] = str(selected)
            location_id = uuid.uuid4().hex
            atomic_json(manager.registry_path,
                        {"schema_version": 1, "locations": {location_id: location}, "projects": {}})
            project = manager.handle({"operation": "create", "name": "Container integration",
                                      "storage_folder": str(selected)})
            pid = project["id"]
            def call(operation, **values):
                return manager.handle({"operation": operation, "project_id": pid, **values})
            print("Importing into exFAT-backed ext4 container", flush=True)
            assert call("import", source=args.iso)["state"] == "ready"
            call("fs_write", path="etc/example.conf", text="container=true\n")
            call("fs_chown", path="etc/example.conf", uid=1234, gid=2345)
            entries = call("fs_list", path="etc")
            entry = next(item for item in entries if item["name"] == "example.conf")
            assert (entry["uid"], entry["gid"]) == (1234, 2345)
            with manager.locked_registry() as registry:
                with manager.open_location(registry, location_id) as mounted:
                    root = mounted.path(pid) / "rootfs"
                    assert stat.S_IMODE((root / "etc/example.conf").stat().st_mode) == 0o640
                    assert os.getxattr(root / "etc/example.conf", "user.test") == b"preserved"
                    assert (root / "etc/hardlink-a").stat().st_ino == (root / "etc/hardlink-b").stat().st_ino
                    assert os.readlink(root / "etc/absolute-link") == "/etc/example.conf"
            assert call("open")["state"] == "ready"
            assert call("fs_read", path="etc/example.conf") == "container=true\n"
            assert call("build", filename="container.iso")["last_build"]["validation"]["payload_checksum"]
            assert len(call("export_build", destination=str(export))["files"]) == 4
            print("Moving back to native storage and reopening", flush=True)
            moved = call("move_project", storage_folder="", confirmed=True)
            assert Path(moved["storage_path"]).parent == store.base
            assert call("fs_read", path="etc/example.conf") == "container=true\n"
            call("move_project", storage_folder=str(selected), confirmed=True)
            assert call("open")["state"] == "ready"
            assert len(manager.handle({"operation": "list_projects"})) == 1
            assert digest(args.iso) == original
            # No private mount or active loop device should survive a job.
            mounts = Path("/proc/self/mountinfo").read_text()
            assert str(store.base / ".mounts") not in mounts
            active = []
            for backing in Path("/sys/block").glob("loop*/loop/backing_file"):
                if selected.name in backing.read_text():
                    active.append(str(backing))
            assert not active, active
            print(json.dumps({"passed": True, "filesystem": "exFAT container",
                              "ownership": "1234:2345", "mount_cleanup": True,
                              "loop_cleanup": True, "source_unchanged": True}), flush=True)
    finally:
        shutil.rmtree(selected)


if __name__ == "__main__":
    main()
