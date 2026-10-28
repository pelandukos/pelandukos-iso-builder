"""Host-side broker for confined operations on caller-writable containers."""
import json
import os
import sys
import tempfile
from contextlib import ExitStack
from pathlib import Path

from core.errors import BuilderError
from core.iso_builder import iso_filename
from core.project_manager import ID_PATTERN
from services.commands import CommandRunner, Sandbox


def worker(payload, *, reads=None, writes=None, emit=lambda event: None):
    result = []
    pending = ""
    def output(text):
        nonlocal pending
        pending += text
        if len(pending) > 32 * 1024**2:
            raise BuilderError("Storage worker response is too large.")
        lines = pending.split("\n")
        pending = lines.pop()
        for line in lines:
            if not line:
                continue
            event = json.loads(line)
            if event["type"] == "result":
                result.append(event["result"])
            elif event["type"] == "error":
                raise BuilderError(event["message"])
            elif event["type"] in ("log", "stage", "progress", "frame", "live_state"):
                emit(event)
    with ExitStack() as stack:
        request = stack.enter_context(tempfile.NamedTemporaryFile(mode="w", prefix="pelandukos-request-"))
        json.dump(payload, request)
        request.flush()
        read_paths = {Path(__file__).resolve().parents[1]: "/app",
                      Path(request.name): "/request.json"} | (reads or {})
        operation = payload.get("request", {}).get("operation")
        vm_operation = operation in ("live_edit", "vm_capability", "package_query", "package_apply")
        write_paths = dict(writes or {})
        if vm_operation:
            read_paths[Path("/boot")] = "/boot"
            if Path("/dev/kvm").exists():
                write_paths[Path("/dev/kvm")] = "/dev/kvm"
        if operation == "live_edit":
            from services.graphics_devices import render_devices
            from core.live_settings import validate_live_settings
            settings = validate_live_settings(payload["request"].get("vm_settings"))
            _, devices = render_devices() if settings["gpu"] != "software" else (None, {})
            write_paths.update(devices)
            if devices:
                read_paths[Path("/sys")] = "/sys"
        def pin(mapping):
            result = {}
            for host, guest in (mapping or {}).items():
                if guest in ("/store", "/source", "/staging") or guest.startswith("/store/"):
                    # The backing image is caller-writable. Never follow a
                    # substituted project/staging symlink into the host.
                    fd = os.open(host, os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
                    stack.callback(os.close, fd)
                    host = Path(f"/proc/{os.getpid()}/fd/{fd}")
                result[host] = guest
            return result
        Sandbox(CommandRunner()).run(
            ["python3", "-I", "/app/services/storage_worker.py"],
            reads=pin(read_paths), writes=pin(write_paths), capture=False, log_output=False,
            allow_network=vm_operation and payload["request"].get("network") is True,
            stdin=sys.stdin if operation == "live_edit" else None,
            stream=operation == "live_edit",
            timeout=86400 if operation == "live_edit" else 7200, output_handler=output)
    if len(result) != 1:
        raise BuilderError("Storage worker did not return one complete result.")
    return result[0]


def engine_call(store, user_files, request, emit):
    operation = request.get("operation")
    project_id = request.get("project_id")
    if operation == "create":
        writes = {store.base: "/store"}
    else:
        # Bind just this project. Even a hostile image cannot access sibling
        # projects, the helper registry, caller home, or host /etc.
        writes = {store.path(project_id): "/store/" + project_id}
    reads = {}
    payload = {"uid": store.uid, "display_base": str(store.display_base),
               "request": request, "grants": {"read": {}, "write": {}}}
    with ExitStack() as stack:
        if operation in ("import", "fs_import") or (operation == "desktop_apply" and request.get("source")):
            stream = stack.enter_context(user_files.read(request["source"]))
            path = Path(f"/proc/{os.getpid()}/fd/{stream.fileno()}")
            reads[path] = "/grants/input"
            payload["grants"]["read"][request["source"]] = "/grants/input"
        destinations = []
        if operation == "export_project":
            destinations = [request["destination"]]
        elif operation == "export_build":
            project = engine_call(store, user_files,
                                  {"operation": "get", "project_id": project_id}, emit)
            build = project.get("last_build")
            if not build or not ID_PATTERN.fullmatch(str(build.get("build_id", ""))):
                raise BuilderError("Build an ISO first.")
            name = iso_filename(build["filename"])
            destinations = [str(Path(request["destination"]) / (name + suffix))
                            for suffix in ("", ".sha256", ".release.json", ".build.log")]
        for index, destination in enumerate(destinations):
            stream = stack.enter_context(user_files.write(destination))
            guest = f"/grants/output-{index}"
            writes[Path(f"/proc/{os.getpid()}/fd/{stream.fileno()}")] = guest
            payload["grants"]["write"][destination] = guest
        result = worker(payload, reads=reads, writes=writes, emit=emit)
    if operation == "create" and (not isinstance(result, dict)
                                 or not ID_PATTERN.fullmatch(str(result.get("id", "")))):
        raise BuilderError("Invalid created project identifier.")
    return result
