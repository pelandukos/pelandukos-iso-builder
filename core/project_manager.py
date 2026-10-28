import fcntl
import json
import re
import shutil
import uuid
from contextlib import contextmanager
from pathlib import Path

from core.errors import BuilderError
from core.release_manager import validate_version
from core.util import atomic_json, now

ID_PATTERN = re.compile(r"[0-9a-f]{32}")


class ProjectStore:
    def __init__(self, base, uid, *, display_base=None):
        self.base = Path(base)
        self.uid = uid
        self.display_base = Path(display_base) if display_base is not None else self.base
        self.base.mkdir(mode=0o700, parents=True, exist_ok=True)

    def path(self, project_id):
        if not isinstance(project_id, str) or not ID_PATTERN.fullmatch(project_id):
            raise BuilderError("Invalid project identifier.")
        path = self.base / project_id
        if path.is_symlink():
            raise BuilderError("Project storage cannot be a symlink.")
        return path

    def create(self, name, version="0.1"):
        if not isinstance(name, str) or not name.strip() or len(name) > 120:
            raise BuilderError("Use a project name between 1 and 120 characters.")
        version = validate_version(version)
        project_id = uuid.uuid4().hex
        path = self.path(project_id)
        path.mkdir(mode=0o700)
        for directory in ("logs", "outputs", "snapshots"):
            (path / directory).mkdir(mode=0o700)
        data = {
            "schema_version": 1, "id": project_id, "owner_uid": self.uid,
            "name": name.strip(), "created_at": now(), "updated_at": now(),
            "state": "new", "source": None, "analysis": None, "history": [],
            "storage_path": str(self.display_base / project_id),
            "release": {"distribution": "PelandukOS", "version": version,
                        "build_number": "1", "release_date": now()[:10],
                        "developer": "Tunnelbiz Studio Sdn Bhd", "notes": ""},
            "last_build": None,
        }
        self.save(data)
        return data

    def load(self, project_id):
        path = self.path(project_id)
        try:
            data = json.loads((path / "project.json").read_text())
        except (OSError, ValueError) as error:
            raise BuilderError("Project is missing or its manifest is invalid.") from error
        if data.get("owner_uid") != self.uid or data.get("id") != project_id:
            raise BuilderError("This project belongs to another user.")
        if data.get("schema_version") != 1:
            raise BuilderError("Unsupported project manifest version.")
        data["storage_path"] = str(self.display_base / project_id)
        return data

    def save(self, data):
        data["updated_at"] = now()
        atomic_json(self.path(data["id"]) / "project.json", data)

    def record(self, data, operation, details=None):
        data["history"].append({"time": now(), "operation": operation, "details": details or {}})
        self.save(data)

    def list(self, exclude=()):
        result = []
        for path in self.base.iterdir():
            if ID_PATTERN.fullmatch(path.name) and path.name not in exclude and not path.is_symlink():
                try:
                    result.append(self.load(path.name))
                except BuilderError:
                    continue
        return sorted(result, key=lambda item: item["updated_at"], reverse=True)

    @contextmanager
    def lock(self, project_id):
        path = self.path(project_id)
        if not path.is_dir():
            raise BuilderError("Project does not exist.")
        with open(path / ".lock", "a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise BuilderError("Another job is using this project.") from error
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def recover(self, data):
        if data["state"] in ("importing", "extracting", "building", "live_editing"):
            data["state"] = "ready" if (self.path(data["id"]) / "rootfs").is_dir() else "interrupted"
            self.record(data, "recovered_interrupted_job")
        return data

    def clean_staging(self, project_id):
        path = self.path(project_id)
        for name in ("iso-stage", "root-stage", "build-stage"):
            if (path / name).exists():
                shutil.rmtree(path / name)
        # A crash can occur between the two directory renames at import commit.
        # An ISO tree without a rootfs has no user edits and is safe to retry.
        if not (path / "rootfs").exists() and (path / "iso-tree").exists():
            shutil.rmtree(path / "iso-tree")
