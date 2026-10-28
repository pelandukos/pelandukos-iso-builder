"""Executed only inside bubblewrap; container metadata never selects host paths."""
import json
import os
import shutil
import sys
from contextlib import contextmanager
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, "/app")

from core.errors import BuilderError
from core.iso_extractor import require_space
from core.project_manager import ProjectStore
from core.storage import copy_space, tree_inventory
from core.util import atomic_json, now
from services.commands import CommandRunner
from services.engine import Engine


class ConfinedTools:
    """The whole worker is already confined; map tool aliases within that root."""
    def __init__(self, runner):
        self.runner = runner

    @staticmethod
    def guest_path(host, guest):
        return str(host)

    def run(self, args, *, reads=None, writes=None, allow_network=False, **options):
        mappings = sorted(((guest, str(host)) for host, guest in
                           ((reads or {}) | (writes or {})).items()), reverse=True)
        def translate(argument):
            argument = str(argument)
            for guest, host in mappings:
                if argument == guest or argument.startswith(guest + "/"):
                    return host + argument[len(guest):]
            return argument
        return self.runner.run([translate(arg) for arg in args], **options)


class GrantedFiles:
    """Only exact inputs and exclusive outputs opened as the caller by the broker."""
    def __init__(self, grants):
        self.grants = grants

    @contextmanager
    def read(self, value):
        if value not in self.grants.get("read", {}):
            raise BuilderError("Input was not authorized by the storage broker.")
        with open(self.grants["read"][value], "rb") as stream:
            yield stream

    @contextmanager
    def write(self, value):
        if value not in self.grants.get("write", {}):
            raise BuilderError("Output was not authorized by the storage broker.")
        with open(self.grants["write"][value], "wb") as stream:
            yield stream
            stream.flush()
            os.fsync(stream.fileno())


def copy_project(payload):
    """Copy into an isolated staging directory; parent commits the routing."""
    source, stage = Path("/source"), Path("/staging")
    project = payload["project"]
    require_space(stage, copy_space(source))
    CommandRunner().run(["cp", "-a", "--reflink=auto", "--", "/source/.", "/staging/"])
    if tree_inventory(source) != tree_inventory(stage):
        raise BuilderError("Project copy verification failed; original retained.")
    project["storage_path"] = payload["destination"]
    project["updated_at"] = now()
    project["history"].append({"time": now(), "operation": "project_moved",
                               "details": {"from": payload["origin"], "to": payload["destination"]}})
    atomic_json(stage / "project.json", project)
    return project


def execute(payload, emit):
    action = payload.get("action", "engine")
    if action == "copy":
        return copy_project(payload)
    if action == "clear":
        # Only a project/staging directory is bound, never its host parent.
        for path in Path("/staging").iterdir():
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
            else:
                path.unlink()
        return {}
    store = ProjectStore("/store", payload["uid"], display_base=payload["display_base"])
    engine = Engine(store, GrantedFiles(payload.get("grants", {})), emit)
    engine.sandbox = ConfinedTools(CommandRunner(engine.log))
    return engine.handle(payload["request"])


def main():
    def emit(event):
        print(json.dumps(event), flush=True)
    try:
        payload = json.loads(Path("/request.json").read_text())
        emit({"type": "result", "result": execute(payload, emit)})
    except Exception as error:
        emit({"type": "error", "message": f"{type(error).__name__}: {error}"})
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
