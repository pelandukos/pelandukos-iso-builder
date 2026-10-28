import os
import re
import shutil

from adapters.debian_live import DebianLiveAdapter
from core.errors import BuilderError
from core.progress import ToolProgress, stage as report_stage, percentage_options
from core.util import safe_path
from core.validator import validate_boot, kernel_inventory_digest


def require_space(path, required):
    available = shutil.disk_usage(path).free
    if available < required:
        raise BuilderError(f"Insufficient free space: need {required / 2**30:.1f} GiB, "
                           f"available {available / 2**30:.1f} GiB. "
                           "Use ISO Projects → Move current project… to select a larger Linux "
                           "filesystem, or choose another storage folder when creating a project.")


class SquashFSSizeEstimator:
    """Reduce arbitrarily large numeric listings without retaining their text."""
    def __init__(self):
        self.pending = ""
        self.total = 0
        self.entries = 0

    def feed(self, text):
        lines = (self.pending + text).split("\n")
        self.pending = lines.pop()
        if len(self.pending) > 65536:
            raise BuilderError("Invalid SquashFS listing: excessively long record.")
        for line in lines:
            match = re.match(r"^[-dlcbps]\S+\s+\d+/\d+\s+(\d+)\s", line)
            if match:
                self.total += int(match[1])
                self.entries += 1

    def finish(self):
        self.feed("\n")
        if not self.entries or self.total == 0:
            raise BuilderError("Cannot estimate uncompressed filesystem size.")
        return self.total


def probe_filesystem(path):
    probe = path / ".filesystem-probe"
    link = path / ".symlink-probe"
    try:
        probe.write_bytes(b"")
        probe.chmod(0o640)
        link.symlink_to(probe.name)
        os.setxattr(probe, "user.pelandukos", b"probe")
        if probe.stat().st_mode & 0o777 != 0o640:
            raise OSError("Unix permission bits are not supported")
    except OSError as error:
        raise BuilderError("Project storage must support Unix modes, symlinks and xattrs "
                           "(use ext4, XFS or Btrfs, not exFAT/FAT).") from error
    finally:
        link.unlink(missing_ok=True)
        probe.unlink(missing_ok=True)


class ISOExtractor:
    def __init__(self, sandbox, emit=lambda event: None):
        self.sandbox = sandbox
        self.emit = emit

    def extract_tree(self, source, destination):
        destination.mkdir(mode=0o700)
        report_stage(self.emit, "Extracting ISO tree — tool does not report a percentage")
        self.sandbox.run(
            ["xorriso", "-abort_on", "FAILURE", "-osirrox", "on",
             "-acl", "on", "-xattr", "on", "-hardlinks", "on",
             "-indev", "/source.iso", "-extract", "/", "/out/tree"],
            reads={source: "/source.iso"}, writes={destination: "/out"},
        )
        return destination / "tree"

    def extract(self, project, analysis):
        adapter = DebianLiveAdapter()
        adapter.validate_layout(analysis)
        probe_filesystem(project)
        source = project / "source.iso"
        require_space(project, source.stat().st_size * 3 + 256 * 1024**2)
        stage = project / "iso-stage"
        root_stage = project / "root-stage"
        if stage.exists() or root_stage.exists():
            raise BuilderError("Staging data remains from an interrupted job. Reopen the project to recover.")
        try:
            tree = self.extract_tree(source, stage)
            squashfs = safe_path(tree, adapter.squashfs)
            estimate = SquashFSSizeEstimator()
            report_stage(self.emit, "Inspecting SquashFS size and available space")
            self.sandbox.run(
                ["unsquashfs", "-lln", "/filesystem.squashfs"],
                reads={squashfs: "/filesystem.squashfs"},
                capture=False, output_handler=estimate.feed, log_output=False,
            )
            uncompressed = estimate.finish()
            # Include extraction, next build, verification, and filesystem overhead.
            require_space(project, int(uncompressed * 1.25) + source.stat().st_size * 3 + 256 * 1024**2)
            root_stage.mkdir(mode=0o700)
            extraction = ToolProgress(self.emit, "Extracting Linux root filesystem", "squashfs")
            self.sandbox.run(
                ["unsquashfs", "-strict-errors", *percentage_options("unsquashfs"), "-processors", "2", "-xattrs",
                 "-d", "/out/rootfs", "/filesystem.squashfs"],
                reads={squashfs: "/filesystem.squashfs"}, writes={root_stage: "/out"},
                output_handler=extraction.feed, capture=False,
            )
            report_stage(self.emit, "Validating extracted filesystem and boot configuration")
            root = root_stage / "rootfs"
            target = adapter.identify_root(root)
            validation = validate_boot(tree)
            analysis.update(target)
            analysis["adapter"] = adapter.name
            analysis["uncompressed_estimate"] = uncompressed
            analysis["validation"] = validation
            analysis["kernel_inventory_sha256"] = kernel_inventory_digest(root)
            # Commit trees only after every check succeeds.
            os.replace(tree, project / "iso-tree")
            os.replace(root, project / "rootfs")
            return analysis
        finally:
            for directory in (stage, root_stage):
                if directory.exists():
                    shutil.rmtree(directory)
