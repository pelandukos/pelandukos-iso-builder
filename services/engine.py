import os
import shutil
import uuid
from pathlib import Path

from core.errors import BuilderError
from core.desktop_manager import DesktopManager
from core.filesystem_manager import FilesystemManager
from core.iso_analyzer import ISOAnalyzer
from core.iso_builder import ISOBuilder
from core.iso_extractor import ISOExtractor, require_space
from core.release_manager import update_release
from core.progress import Progress, copy_stream
from core.util import digest, now, safe_path
from services.commands import CommandRunner, Sandbox


class Engine:
    """Allowlisted operations; caller-supplied paths never select private roots."""
    def __init__(self, store, user_files, emit=lambda event: None):
        self.store = store
        self.user_files = user_files
        self.emit = emit
        self.log_stream = None
        self.sandbox = Sandbox(CommandRunner(self.log))

    def log(self, message):
        if self.log_stream:
            self.log_stream.write(message + "\n")
            self.log_stream.flush()
        self.emit({"type": "log", "message": message})

    def progress(self, message):
        self.log(message)
        self.emit({"type": "stage", "message": message})

    def handle(self, request):
        if not isinstance(request, dict):
            raise BuilderError("Request must be a JSON object.")
        operation = request.get("operation")
        if operation == "list_projects":
            return self.store.list()
        if operation == "create":
            return self.store.create(request.get("name"), request.get("version", "0.1"))
        project_id = request.get("project_id")
        with self.store.lock(project_id):
            manifest = self.store.load(project_id)
            project = self.store.path(project_id)
            if operation == "open":
                self.store.recover(manifest)
                self.store.clean_staging(project_id)
                return manifest
            if operation == "get":
                return manifest
            log_name = now().replace(":", "-") + "-" + uuid.uuid4().hex[:8] + ".log"
            with open(project / "logs" / log_name, "w") as log:
                self.log_stream = log
                try:
                    result = self.dispatch(operation, request, manifest, project)
                    return result
                except Exception as error:
                    self.log(f"FAILED: {type(error).__name__}: {error}")
                    if manifest["state"] in ("importing", "extracting", "building", "live_editing"):
                        manifest["state"] = "ready" if (project / "rootfs").is_dir() else "failed"
                    self.store.record(manifest, "job_failed",
                                      {"operation": operation, "error": str(error), "log": log_name})
                    raise
                finally:
                    self.log_stream = None

    def require_ready(self, manifest):
        if manifest["state"] != "ready":
            raise BuilderError("Import and extract a supported ISO first.")

    def dispatch(self, operation, request, manifest, project):
        if operation == "import":
            if (project / "rootfs").exists():
                raise BuilderError("This project already has an extracted filesystem. Create another project.")
            manifest["state"] = "importing"
            self.store.save(manifest)
            self.progress("Copying source ISO into protected project storage…")
            source = project / "source.iso"
            temporary = project / "source.partial"
            try:
                with self.user_files.read(request["source"]) as incoming:
                    size = os.fstat(incoming.fileno()).st_size
                    require_space(project, size * 4 + 256 * 1024**2)
                    with open(temporary, "wb") as outgoing:
                        copy_stream(incoming, outgoing, self.emit, "Copying source ISO")
                        self.progress("Flushing source ISO to disk…")
                        outgoing.flush()
                        os.fsync(outgoing.fileno())
                os.chmod(temporary, 0o400)
                os.replace(temporary, source)
            finally:
                temporary.unlink(missing_ok=True)
            manifest["source"] = {"original_path": request["source"],
                                  "sha256": digest(source, progress=Progress(self.emit, "Checksumming source ISO")),
                                  "size_bytes": source.stat().st_size}
            self.store.save(manifest)
            self.progress("Inspecting ISO boot equipment and filesystem layout…")
            analysis = ISOAnalyzer(self.sandbox).inspect(source)
            manifest["analysis"] = analysis
            manifest["state"] = "extracting"
            self.store.save(manifest)
            self.progress("Extracting ISO and SquashFS, preserving Linux metadata…")
            manifest["analysis"] = ISOExtractor(self.sandbox, self.emit).extract(project, analysis)
            manifest["state"] = "ready"
            self.store.record(manifest, "iso_imported", manifest["source"])
            return manifest
        if operation == "save_release":
            update_release(manifest, request["values"])
            self.store.record(manifest, operation, request["values"])
            return manifest
        if operation == "export_project":
            import json
            with self.user_files.write(request["destination"]) as stream:
                stream.write((json.dumps(manifest, indent=2) + "\n").encode())
            return {"destination": request["destination"]}
        if operation == "export_build":
            build = manifest.get("last_build")
            if not build:
                raise BuilderError("Build an ISO first.")
            directory = project / "outputs" / build["build_id"]
            # Export each file without overwrite and as the invoking user.
            destination = Path(request["destination"])
            exported = []
            for name in (build["filename"], build["filename"] + ".sha256",
                         build["filename"] + ".release.json", build["filename"] + ".build.log"):
                with self.user_files.write(str(destination / name)) as outgoing:
                    with open(directory / name, "rb") as incoming:
                        copy_stream(incoming, outgoing, self.emit, "Exporting " + name)
                        self.progress("Flushing exported file to disk…")
                exported.append(str(destination / name))
            return {"files": exported}
        self.require_ready(manifest)
        if operation in ("package_list", "package_query", "package_apply", "package_restore", "package_discard"):
            from core.package_manager import PackageManager
            packages = PackageManager(project, self.sandbox, self.emit, self.log)
            if operation == "package_list":
                return packages.list()
            if operation == "package_query":
                return packages.query(request)
            if request.get("confirmed") is not True:
                raise BuilderError("Confirm package changes before modifying this project.")
            if operation == "package_apply":
                result = packages.apply(request)
            elif operation == "package_restore":
                packages.restore()
                result = {}
            else:
                packages.discard()
                result = {}
            self.store.record(manifest, operation, {"action": request.get("action"),
                                                   "packages": request.get("packages", []),
                                                   "preset": request.get("preset")})
            return {"project": manifest, **result, "snapshot": packages.snapshot()}
        if operation == "vm_capability":
            from core.vm_bootstrap import capability
            return capability()
        if operation == "live_edit":
            from services.target_vm import TargetVM
            from core.live_settings import validate_live_settings
            vm_settings = validate_live_settings(request.get("vm_settings"))
            if request.get("confirmed") is not True:
                raise BuilderError("Confirm persistent live editing before starting the target desktop.")
            if (manifest.get("analysis") or {}).get("architecture") != "amd64":
                raise BuilderError("Live editing currently supports amd64 projects only.")
            # Resolve within the target, including Debian's /sbin -> usr/sbin link.
            from core.validator import resolve_iso_path
            init = resolve_iso_path(project / "rootfs", "/sbin/init")
            if not init.is_file():
                raise BuilderError("Target /sbin/init is missing; a desktop session cannot be started.")
            manifest["state"] = "live_editing"
            self.store.record(manifest, "live_edit_started", vm_settings)
            result = TargetVM(self.sandbox, self.emit, self.log).run(
                project / "rootfs", live=True, network=request.get("network") is True, timeout=86400,
                vm_settings=vm_settings, compress_frames=request.get("frame_encoding") == "ppm-zlib")
            manifest["state"] = "ready"
            self.store.record(manifest, "live_edit_stopped", result)
            return manifest
        if operation in ("boot_appearance_get", "boot_appearance_save"):
            from core.boot_appearance import load, save
            if operation == "boot_appearance_get":
                return load(project / "rootfs")
            result = save(project / "rootfs", request.get("values"))
            self.store.record(manifest, operation, result)
            return result
        if operation in ("desktop_inspect", "desktop_apply"):
            desktop = DesktopManager(project / "rootfs", self.sandbox)
            if operation == "desktop_inspect":
                return desktop.capabilities() | {"saved": manifest.get("desktop_customization")}
            if request.get("confirmed") is not True:
                raise BuilderError("Confirm applying desktop defaults before changing target files.")
            wallpaper = None
            if request.get("source"):
                with self.user_files.read(request["source"]) as stream:
                    if os.fstat(stream.fileno()).st_size > 32 * 1024**2:
                        raise BuilderError("Wallpaper must be at most 32 MiB.")
                    wallpaper = stream.read(32 * 1024**2 + 1)
            self.progress("Applying desktop defaults to the extracted ISO filesystem…")
            result = desktop.apply(project, request["values"], wallpaper)
            manifest["desktop_customization"] = result
            self.store.record(manifest, operation, result)
            return manifest
        if operation == "build":
            if "version" in request:
                update_release(manifest, {"version": request["version"]})
                self.store.record(manifest, "save_release", {"version": manifest["release"]["version"]})
            self.progress("Validating source and target. Build cannot be cancelled safely.")
            manifest["state"] = "building"
            self.store.save(manifest)
            release = ISOBuilder(self.sandbox, self.emit).build(project, manifest, request["filename"])
            manifest["last_build"] = release
            manifest["state"] = "ready"
            self.store.record(manifest, "iso_built", release)
            self.log("ISO payload readback verified. BIOS/UEFI boot tests have not been run.")
            self.log_stream.flush()
            shutil.copyfile(self.log_stream.name,
                            project / "outputs" / release["build_id"] / (release["filename"] + ".build.log"))
            return manifest
        filesystem = FilesystemManager(project / "rootfs")
        reads = {
            "fs_list": lambda: filesystem.list(request.get("path", "")),
            "fs_read": lambda: filesystem.read(request["path"]),
            "fs_search": lambda: filesystem.search(request["query"]),
        }
        if operation in reads:
            return reads[operation]()
        writes = {
            "fs_write": lambda: filesystem.write(request["path"], request["text"],
                                                  create_only=request.get("create_only", False)),
            "fs_mkdir": lambda: filesystem.mkdir(request["path"]),
            "fs_rename": lambda: filesystem.rename(request["path"], request["destination"]),
            "fs_delete": lambda: filesystem.delete(request["path"]),
            "fs_chmod": lambda: filesystem.chmod(request["path"], request["mode"]),
            "fs_chown": lambda: filesystem.chown(request["path"], request["uid"], request["gid"]),
        }
        if operation in writes:
            if operation == "fs_delete" and request.get("confirmed") is not True:
                raise BuilderError("Explicit deletion confirmation is required.")
            writes[operation]()
            self.store.record(manifest, operation, {k: v for k, v in request.items()
                                                    if k not in ("text", "operation", "project_id")})
            return manifest
        if operation == "fs_copy":
            root = project / "rootfs"
            source = safe_path(root, request["path"], leaf_symlink=True)
            target = safe_path(root, request["destination"], leaf_symlink=True)
            if source == root or target.exists() or target.is_symlink():
                raise BuilderError("Copy requires a non-root source and an unused destination.")
            if source in target.parents:
                raise BuilderError("Cannot copy a directory into itself.")
            self.sandbox.run(["cp", "-a", "--", "/rootfs/" + str(source.relative_to(root)),
                              "/rootfs/" + str(target.relative_to(root))], writes={root: "/rootfs"})
            self.store.record(manifest, operation, {"path": request["path"], "destination": request["destination"]})
            return manifest
        if operation == "fs_import":
            target = safe_path(project / "rootfs", request["path"])
            if target.exists():
                raise BuilderError("Import destination exists. Choose a new name.")
            with self.user_files.read(request["source"]) as incoming:
                require_space(project, os.fstat(incoming.fileno()).st_size + 64 * 1024**2)
                try:
                    with open(target, "xb") as outgoing:
                        copy_stream(incoming, outgoing, self.emit, "Importing file")
                    target.chmod(0o644)
                except Exception:
                    target.unlink(missing_ok=True)
                    raise
            self.store.record(manifest, operation, {"path": request["path"]})
            return manifest
        raise BuilderError(f"Unsupported operation: {operation!r}")
