"""Root-owned registry for selected storage; no user-supplied privileged roots.

External parents may be writable by the caller. Pin each private storage root
with a directory FD, verify its root-owned marker, and keep that FD alive while
the engine runs. Later renames/symlink swaps of the parent cannot redirect a job.
"""
import fcntl
import json
import os
import shutil
import stat
import uuid
from contextlib import contextmanager, nullcontext
from pathlib import Path

from core.errors import BuilderError
from core.iso_extractor import probe_filesystem, require_space
from core.project_manager import ProjectStore, ID_PATTERN
from core.release_manager import validate_version
from core.storage import filesystem_info, require_linux_filesystem, tree_inventory, copy_space
from core.util import atomic_json, now
from services.commands import CommandRunner, Sandbox
from services.engine import Engine
from services.image_storage import container_size, create_image, mounted_image, private_mount_namespace
from services.storage_bridge import engine_call, worker


class StorageManager:
    def __init__(self, default_store, user_files, emit=lambda event: None):
        self.default = default_store
        self.user_files = user_files
        self.emit = emit
        self.registry_path = default_store.base / "storage.json"
        self.owner = os.geteuid()  # root in the installed helper; test UID in unit tests
        self.namespaced = False

    def call_engine(self, store, request):
        if getattr(store, "isolated", False):
            return engine_call(store, self.user_files, request, self.emit)
        return Engine(store, self.user_files, self.emit).handle(request)

    @contextmanager
    def locked_registry(self):
        with open(self.default.base / ".storage.lock", "a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise BuilderError("Another project/storage operation is running. Wait for it to finish.") from error
            try:
                registry = (json.loads(self.registry_path.read_text()) if self.registry_path.exists()
                            else {"schema_version": 1, "locations": {}, "projects": {}})
                if registry.get("schema_version") != 1:
                    raise BuilderError("Unsupported storage registry version.")
                yield registry
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    @staticmethod
    def fd_path(fd):
        # Explicit parent PID also works when sandbox subprocesses close FDs.
        return Path(f"/proc/{os.getpid()}/fd/{fd}")

    @contextmanager
    def parent_directory(self, value):
        value = self.user_files.validate(value)
        with self.user_files.identity():
            fd = os.open(value, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
            try:
                name = ".pelandukos-write-check-" + uuid.uuid4().hex
                probe = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                0o600, dir_fd=fd)
                os.close(probe)
                os.unlink(name, dir_fd=fd)
            except Exception:
                os.close(fd)
                raise
        try:
            yield fd
        finally:
            os.close(fd)

    def verify_private_root(self, fd, location):
        info = os.fstat(fd)
        if info.st_uid != self.owner or stat.S_IMODE(info.st_mode) != 0o700:
            raise BuilderError("Project storage must be privately owned by the helper (mode 0700).")
        marker = os.open(".storage-id", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        with os.fdopen(marker, "rb") as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != self.owner
                    or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
                raise BuilderError("Invalid private storage marker.")
            if stream.read(4096).decode() != location["token"]:
                raise BuilderError("The selected storage folder has been replaced. Reconnect the original drive.")

    @contextmanager
    def open_location(self, registry, location_id):
        if location_id == "default":
            yield self.default
            return
        location = registry["locations"].get(location_id)
        if location is None:
            raise BuilderError("Project storage location is not registered.")
        if location.get("kind") == "ext4-image":
            if not self.namespaced:
                private_mount_namespace()
                self.namespaced = True
            runtime = self.default.base / ".mounts"
            runtime.mkdir(mode=0o700, exist_ok=True)
            with mounted_image(location, self.user_files, runtime) as root:
                store = ProjectStore(root, self.default.uid, display_base=location["path"] + "::")
                store.isolated = True
                yield store
            return
        try:
            fd = os.open(location["path"], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        except OSError as error:
            raise BuilderError(f"Project drive is unavailable: {location['path']}. "
                               "Reconnect it at the same location.") from error
        try:
            self.verify_private_root(fd, location)
            require_linux_filesystem(filesystem_info(fd))
            yield ProjectStore(self.fd_path(fd), self.default.uid, display_base=location["path"])
        finally:
            os.close(fd)

    def register_location(self, registry, value, size_gib=128):
        if not value:
            return "default"
        with self.parent_directory(value) as parent_fd:
            info = filesystem_info(parent_fd)
            if info["filesystem"] != "exFAT":
                require_linux_filesystem(info)
            parent = os.readlink(self.fd_path(parent_fd))
            if parent.endswith(" (deleted)"):
                raise BuilderError("Selected folder was removed; select it again.")
            for location_id, location in registry["locations"].items():
                if location["parent"] == parent:
                    # Reuse only a registered private root; never adopt a directory.
                    with self.open_location(registry, location_id):
                        return location_id
            location_id = uuid.uuid4().hex
            if info["filesystem"] == "exFAT":
                location = create_image(parent_fd, self.user_files, container_size(size_gib), self.log, self.emit)
                location["parent"] = parent
                registry["locations"][location_id] = location
                atomic_json(self.registry_path, registry)
                return location_id
            name = f"PelandukOS-Projects-{self.default.uid}-{location_id[:12]}"
            # Open as the caller before sealing the directory. A hostile parent
            # must never trick root into adopting an unrelated privileged folder.
            with self.user_files.identity():
                os.mkdir(name, 0o700, dir_fd=parent_fd)
                fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                             dir_fd=parent_fd)
            try:
                root = self.fd_path(fd)
                info = os.fstat(fd)
                if info.st_uid != self.user_files.uid or stat.S_IMODE(info.st_mode) != 0o700:
                    raise BuilderError("Selected storage changed during creation.")
                os.fchown(fd, self.owner, os.getegid())
                os.fchmod(fd, 0o700)
                if os.listdir(fd):
                    raise BuilderError("New storage must be empty; select the folder again.")
                token = uuid.uuid4().hex + "\n"
                marker = os.open(".storage-id", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                 0o600, dir_fd=fd)
                with os.fdopen(marker, "w") as stream:
                    stream.write(token)
                    stream.flush()
                    os.fsync(stream.fileno())
                probe_filesystem(root)
                registry["locations"][location_id] = {
                    "parent": parent, "path": str(Path(parent) / name), "token": token,
                }
                atomic_json(self.registry_path, registry)
            finally:
                os.close(fd)
            return location_id

    def location_for(self, registry, project_id):
        if not isinstance(project_id, str) or not ID_PATTERN.fullmatch(project_id):
            raise BuilderError("Invalid project identifier.")
        return registry["projects"].get(project_id, {}).get("location", "default")

    def log(self, message):
        self.emit({"type": "log", "message": message})

    def handle(self, request):
        if not isinstance(request, dict):
            raise BuilderError("Request must be a JSON object.")
        operation = request.get("operation")
        if operation == "storage_check":
            with self.parent_directory(request["folder"]) as fd:
                return filesystem_info(fd) | {"folder": os.readlink(self.fd_path(fd))}
        with self.locked_registry() as registry:
            if operation == "list_projects":
                result = self.default.list(exclude=registry["projects"])
                for project_id, record in registry["projects"].items():
                    try:
                        with self.open_location(registry, record["location"]) as store:
                            store.isolated = getattr(store, "isolated", False) or record.get("isolated", False)
                            result.append(self.call_engine(store, {"operation": "get", "project_id": project_id}))
                    except (OSError, BuilderError) as error:
                        result.append({"id": project_id, "name": record["name"],
                                       "state": "storage unavailable", "storage_error": str(error)})
                return sorted(result, key=lambda p: p.get("updated_at", ""), reverse=True)
            if operation == "create":
                validate_version(request.get("version", "0.1"))
                location_id = self.register_location(registry, request.get("storage_folder", ""),
                                                     request.get("container_size_gib", 128))
                with self.open_location(registry, location_id) as store:
                    project = self.call_engine(store, request)
                    if location_id != "default":
                        registry["projects"][project["id"]] = {"location": location_id, "name": project["name"]}
                        try:
                            atomic_json(self.registry_path, registry)
                        except Exception:
                            if not getattr(store, "isolated", False):
                                shutil.rmtree(store.path(project["id"]))
                            raise
                    return project
            project_id = request.get("project_id")
            source_id = self.location_for(registry, project_id)
            with self.open_location(registry, source_id) as store:
                store.isolated = (getattr(store, "isolated", False)
                                  or registry["projects"].get(project_id, {}).get("isolated", False))
                if operation == "move_project":
                    if request.get("confirmed") is not True:
                        raise BuilderError("Confirm the project relocation first.")
                    with (nullcontext() if store.isolated else store.lock(project_id)):
                        destination_id = self.register_location(registry, request.get("storage_folder", ""),
                                                                request.get("container_size_gib", 128))
                        if destination_id == source_id:
                            raise BuilderError("The project is already stored in this location.")
                        with self.open_location(registry, destination_id) as destination:
                            if store.isolated or getattr(destination, "isolated", False):
                                return self.move_isolated(registry, store, destination, project_id,
                                                          destination_id)
                            return self.move_project(registry, store, destination, project_id,
                                                     source_id, destination_id)
                return self.call_engine(store, request)

    def move_isolated(self, registry, source, destination, project_id, destination_id):
        project = self.call_engine(source, {"operation": "get", "project_id": project_id})
        source_path = source.path(project_id)
        target = destination.path(project_id)
        # A fresh unpredictable staging leaf avoids adopting image-controlled
        # recovery paths. The parent does not traverse anything inside it.
        staging = destination.base / (".moving-" + uuid.uuid4().hex)
        if target.exists() or target.is_symlink():
            raise BuilderError("Destination contains this project; select a different folder.")
        require_space(destination.base, 256 * 1024**2)
        staging.mkdir(mode=0o700)
        committed = False
        try:
            self.emit({"type": "stage", "message": "Copying and verifying the complete project in an isolated worker…"})
            project = worker({"action": "copy", "project": project,
                              "origin": str(source.display_base / project_id),
                              "destination": str(destination.display_base / project_id)},
                             reads={source_path: "/source"}, writes={staging: "/staging"}, emit=self.emit)
            os.replace(staging, target)
            # Even after leaving an external image its contents remain untrusted.
            registry["projects"][project_id] = {"location": destination_id,
                                                "name": project["name"], "isolated": True}
            atomic_json(self.registry_path, registry)
            committed = True
            worker({"action": "clear"}, writes={source_path: "/staging"}, emit=self.emit)
            source_path.rmdir()
            return project
        except Exception as error:
            if committed:
                project["storage_warning"] = f"Project moved; old-copy cleanup failed: {error}"
                self.log(project["storage_warning"])
                return project
            raise
        finally:
            if staging.exists():
                worker({"action": "clear"}, writes={staging: "/staging"}, emit=self.emit)
                staging.rmdir()

    def move_project(self, registry, source, destination, project_id, source_id, destination_id):
        project = source.load(project_id)
        source_path = source.path(project_id)
        target = destination.path(project_id)
        staging = destination.base / (".moving-" + project_id)
        # Only a failed move from this same source may leave a committed-name copy.
        recovery_marker = {"project_id": project_id, "source": source_id, "target": destination_id}
        if target.exists():
            marker = target / ".relocation.json"
            if not marker.is_file() or json.loads(marker.read_text()) != recovery_marker:
                raise BuilderError("The destination already contains this project.")
            shutil.rmtree(target)
        if staging.exists():
            shutil.rmtree(staging)
        require_space(destination.base, copy_space(source_path))
        staging.mkdir(mode=0o700)
        try:
            self.emit({"type": "stage", "message": "Copying project to the selected drive; original is retained until verified…"})
            Sandbox(CommandRunner(self.log)).run(
                ["cp", "-a", "--reflink=auto", "--", "/source/.", "/destination/"],
                reads={source_path: "/source"}, writes={staging: "/destination"})
            self.emit({"type": "stage", "message": "Verifying copied files, ownership, permissions, links and xattrs…"})
            if tree_inventory(source_path) != tree_inventory(staging):
                raise BuilderError("Project copy verification failed; the original remains unchanged.")
            project["storage_path"] = str(destination.display_base / project_id)
            project["updated_at"] = now()
            project["history"].append({"time": now(), "operation": "project_moved",
                                       "details": {"from": str(source.display_base / project_id),
                                                   "to": project["storage_path"]}})
            atomic_json(staging / "project.json", project)
            atomic_json(staging / ".relocation.json", recovery_marker)
            os.replace(staging, target)
            if destination_id == "default":
                registry["projects"].pop(project_id, None)
            else:
                registry["projects"][project_id] = {"location": destination_id, "name": project["name"]}
            atomic_json(self.registry_path, registry)
            try:
                (target / ".relocation.json").unlink()
                shutil.rmtree(source_path)
            except OSError as error:
                project["storage_warning"] = (
                    f"Project moved successfully, but the old copy could not be fully removed: {error}")
                destination.save(project)
                self.log(project["storage_warning"])
            return project
        finally:
            if staging.exists():
                shutil.rmtree(staging)
            # Keep a completed marked copy if registry persistence fails. An
            # fsync error can occur *after* atomic replacement of the registry;
            # deleting the copy then could invalidate its new authoritative route.
            # Retrying from the old route removes only this matching marked copy.
