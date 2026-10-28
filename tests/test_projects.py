import os
import pytest

from core.errors import BuilderError
from core.project_manager import ProjectStore


def test_persistence_and_recovery(engine):
    created = engine.handle({"operation": "create", "name": "Example"})
    reopened = engine.handle({"operation": "open", "project_id": created["id"]})
    assert reopened["name"] == "Example"
    reopened["state"] = "extracting"
    engine.store.save(reopened)
    stale = engine.store.path(created["id"]) / "iso-stage"
    stale.mkdir()
    (engine.store.path(created["id"]) / "iso-tree").mkdir()
    recovered = engine.handle({"operation": "open", "project_id": created["id"]})
    assert recovered["state"] == "interrupted"
    assert not stale.exists()
    assert not (engine.store.path(created["id"]) / "iso-tree").exists()
    assert recovered["history"][-1]["operation"] == "recovered_interrupted_job"


def test_manifest_owner_and_identifier(engine):
    project = engine.store.create("Owner")
    another = ProjectStore(engine.store.base, os.getuid() + 1)
    with pytest.raises(BuilderError, match="another user"):
        another.load(project["id"])
    with pytest.raises(BuilderError):
        engine.store.path("../../etc")
    assert engine.store.list()[0]["id"] == project["id"]


def test_atomic_metadata_and_lock(engine):
    project = engine.store.create("Lock")
    with engine.store.lock(project["id"]):
        with pytest.raises(BuilderError, match="Another job"):
            with engine.store.lock(project["id"]):
                pass
    engine.store.record(project, "edit", {"path": "etc/example"})
    loaded = engine.store.load(project["id"])
    assert loaded["history"][0]["details"]["path"] == "etc/example"
    assert not list(engine.store.path(project["id"]).glob(".manifest-*"))


def test_helper_has_no_arbitrary_command_api(engine):
    project = engine.store.create("Denied")
    with pytest.raises(BuilderError):
        engine.handle({"operation": "exec", "project_id": project["id"], "command": ["apt", "install", "x"]})
    assert engine.store.load(project["id"])["history"][-1]["operation"] == "job_failed"


def test_custom_version_persists_and_invalid_version_creates_nothing(engine):
    before = engine.store.list()
    for version in ("", " ", "bad/version", "\n1\n2", "x" * 65, 1):
        with pytest.raises(BuilderError):
            engine.handle({"operation": "create", "name": "Invalid", "version": version})
    assert engine.store.list() == before
    project = engine.handle({"operation": "create", "name": "Custom", "version": " 3.1-beta1 "})
    pid = project["id"]
    assert engine.handle({"operation": "open", "project_id": pid})["release"]["version"] == "3.1-beta1"
    engine.handle({"operation": "save_release", "project_id": pid, "values": {"version": "3.2"}})
    assert engine.store.load(pid)["release"]["version"] == "3.2"
