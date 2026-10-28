import os
import re
import shutil
import uuid

from adapters.debian_live import DebianLiveAdapter
from core.boot_metadata import hybrid_apm_layout
from core.boot_menu import clean_live_grub_menu
from core.errors import BuilderError
from core.boot_splash import update_live_splash, update_pelandukos_boot_menu, apply_plymouth_defaults
from core.progress import Progress, ToolProgress, stage as report_stage
from core.iso_analyzer import ISOAnalyzer
from core.iso_extractor import ISOExtractor, require_space
from core.squashfs_builder import SquashFSBuilder
from core.util import atomic_json, digest, now, read_text, safe_path
from core.validator import compare_boot, resolve_iso_path, validate_boot, kernel_inventory_digest


def iso_filename(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,120}\.iso", value):
        raise BuilderError("Use an ISO filename containing only letters, digits, dots, dashes and underscores.")
    return value


class ISOBuilder:
    def __init__(self, sandbox, emit=lambda event: None):
        self.sandbox = sandbox
        self.emit = emit

    def build(self, project, manifest, filename):
        iso_filename(filename)
        source = project / "source.iso"
        if digest(source, progress=Progress(self.emit, "Verifying source ISO checksum")) != manifest["source"]["sha256"]:
            raise BuilderError("The protected source ISO checksum changed. Build refused.")
        report_stage(self.emit, "Validating target filesystem and boot configuration")
        analysis = manifest["analysis"]
        adapter = DebianLiveAdapter()
        adapter.validate_layout(analysis)
        root, tree = project / "rootfs", project / "iso-tree"
        target = adapter.identify_root(root)
        if target["kernels"] != analysis["kernels"]:
            raise BuilderError("Kernel inventory changed. Kernel replacement is not supported in Phase 1.")
        if kernel_inventory_digest(root) != analysis["kernel_inventory_sha256"]:
            raise BuilderError("Target /boot or kernel modules changed. Phase 1 preserves the original "
                               "kernel/initramfs pair; restore those files before building.")
        validation = validate_boot(tree)
        require_space(project, analysis["size_bytes"] * 4 +
                      analysis.get("uncompressed_estimate", 0) + 256 * 1024**2)
        stage = project / "build-stage"
        if stage.exists():
            raise BuilderError("A previous build was interrupted. Reopen the project to recover.")
        stage.mkdir(mode=0o700)
        build_id = uuid.uuid4().hex
        try:
            report_stage(self.emit, "Updating live ISO bootsplash from project theme")
            splash_changes, splash_report = update_live_splash(
                root, tree, stage, validation, self.sandbox)
            if splash_report is None:
                splash_report = {}
            splash_changes.update(update_pelandukos_boot_menu(
                root, tree, stage, self.sandbox, splash_report))
            splash_changes.update(clean_live_grub_menu(tree, stage, splash_report))
            apply_plymouth_defaults(root)
            from core.live_desktop_defaults import preserve_desktop_defaults
            desktop_sync = preserve_desktop_defaults(root)
            from core.live_session_policy import prepare_live_session
            desktop_sync["live_session"] = prepare_live_session(root)
            from core.installed_appearance import prepare_installed_appearance
            desktop_sync["installed_appearance"] = prepare_installed_appearance(root, tree, splash_changes, self.sandbox)
            squashfs = SquashFSBuilder(self.sandbox, self.emit).build(root, stage)
            report_stage(self.emit, "Updating live filesystem metadata")
            # Update the payload, its metadata, and the live Plymouth assets.
            # Bootloader images and appended EFI partition remain original.
            metadata_tree = stage / "metadata"
            (metadata_tree / "live").mkdir(parents=True)
            changes = {adapter.squashfs: squashfs, **splash_changes}
            for relative in ("live/filesystem.size", "live/filesystem.packages"):
                original = safe_path(tree, relative)
                if original.exists():
                    shutil.copy2(original, metadata_tree / relative)
            adapter.refresh_metadata(root, metadata_tree)
            for path in (metadata_tree / "live").iterdir():
                changes[str(path.relative_to(metadata_tree))] = path
            md5 = safe_path(tree, "md5sum.txt")
            if md5.exists():
                # Boot-info tables in these images can be patched by xorriso.
                # Excluding them avoids preserving obsolete internal checksums.
                boot_images = {
                    match.lstrip("/") for match in re.findall(
                        r"El Torito img path\s*:\s*\d+\s+(.+)", analysis["boot_report"])
                }
                lines = []
                for line in read_text(md5, 32 * 1024**2).splitlines():
                    match = re.fullmatch(r"([0-9a-fA-F]{32}) [ *](.+)", line)
                    if not match:
                        raise BuilderError("Unsupported md5sum.txt format.")
                    relative = match[2].removeprefix("./")
                    if (relative == "md5sum.txt" or relative in boot_images or
                            relative.endswith(("/boot.cat", "/boot.catalog"))):
                        continue
                    safe_path(tree, relative, leaf_symlink=True)
                    resolved = resolve_iso_path(tree, relative)
                    if not resolved.is_file():
                        raise BuilderError(f"Checksum references a missing file: {relative}")
                    checksum = digest(changes[relative], "md5") if relative in changes else match[1]
                    lines.append(f"{checksum}  {match[2]}")
                replacement = metadata_tree / "md5sum.txt"
                replacement.write_text("\n".join(lines) + "\n")
                changes["md5sum.txt"] = replacement
            args = ["xorriso", "-abort_on", "FAILURE", "-pacifier", "mkisofs", "-indev", "/source.iso",
                    "-outdev", "/out/rebuilt.iso"]
            for index, (relative, path) in enumerate(changes.items()):
                # Every changed file is under stage and available as /out/...
                args += ["-map", "/out/" + str(path.relative_to(stage)), "/" + relative]
            args += ["-boot_image", "any", "replay", "-commit", "-end"]
            writing = ToolProgress(self.emit, "Writing rebuilt ISO", "xorriso")
            self.sandbox.run(args, reads={source: "/source.iso"}, writes={stage: "/out"},
                             output_handler=writing.feed, capture=False)
            output = stage / "rebuilt.iso"
            report_stage(self.emit, "Verifying rebuilt ISO structure and media")
            new_analysis = ISOAnalyzer(self.sandbox).inspect(output)
            compare_boot(analysis, new_analysis)
            self.sandbox.run(
                ["xorriso", "-abort_on", "FAILURE", "-indev", "/image.iso",
                 "-check_media", "use=indev", "what=disc", "--"],
                reads={output: "/image.iso"},
            )
            verified_tree = ISOExtractor(self.sandbox, self.emit).extract_tree(output, stage / "verify")
            report_stage(self.emit, "Verifying rebuilt payloads and boot paths")
            validate_boot(verified_tree)
            apm = hybrid_apm_layout(analysis)
            if apm:
                relative = apm["path"].lstrip("/")
                if digest(safe_path(verified_tree, relative)) != digest(safe_path(tree, relative)):
                    raise BuilderError(f"Rebuild changed the hybrid EFI payload: {relative}")
            for relative, replacement in changes.items():
                expected = digest(replacement, progress=Progress(self.emit, "Checksumming build payload: " + relative))
                if digest(safe_path(verified_tree, relative),
                          progress=Progress(self.emit, "Verifying " + relative)) != expected:
                    raise BuilderError(f"ISO verification failed for {relative}.")
            if "md5sum.txt" in changes:
                report_stage(self.emit, "Checking internal ISO MD5 manifest")
                for line in read_text(verified_tree / "md5sum.txt", 32 * 1024**2).splitlines():
                    checksum, relative = line.split("  ", 1)
                    if digest(resolve_iso_path(verified_tree, relative.removeprefix("./")), "md5",
                              progress=Progress(self.emit, "Verifying MD5: " + relative)) != checksum.lower():
                        raise BuilderError(f"Internal ISO MD5 verification failed for {relative}.")
            checksum = digest(output, progress=Progress(self.emit, "Generating ISO SHA256 checksum"))
            report_stage(self.emit, "Saving verified ISO and release metadata")
            destination = project / "outputs" / build_id
            destination.mkdir(mode=0o700)
            final = destination / filename
            os.replace(output, final)
            (destination / (filename + ".sha256")).write_text(f"{checksum}  {filename}\n")
            release = {
                "schema_version": 1, "build_id": build_id, "project_id": manifest["id"],
                "built_at": now(), "filename": filename, "sha256": checksum,
                "source_sha256": manifest["source"]["sha256"],
                "release": manifest["release"], "target": target,
                "validation": validation | {"iso_readback": True, "payload_checksum": True,
                                           "boot_capabilities_preserved": True,
                                           "bios_boot_test": "not_run", "uefi_boot_test": "not_run"},
                "bootsplash": splash_report,
                "desktop_defaults": desktop_sync,
                "adapter": adapter.name,
            }
            atomic_json(destination / (filename + ".release.json"), release)
            return release
        finally:
            if stage.exists():
                shutil.rmtree(stage)
