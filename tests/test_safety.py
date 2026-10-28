import os
import stat

import pytest

from core.errors import BuilderError
from core.filesystem_manager import FilesystemManager
from core.util import safe_path, read_text
from services.privileged_helper import UserFiles
from services.commands import CommandRunner, Sandbox


@pytest.mark.parametrize("path", ["../escape", "/etc/shadow", "a/../../escape", "x\0y", r"a\b"])
def test_traversal_rejected(tmp_path, path):
    with pytest.raises(BuilderError):
        safe_path(tmp_path, path)


def test_links_cannot_escape_or_be_followed(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "secret"
    outside.write_text("safe")
    (root / "link").symlink_to(outside)
    fs = FilesystemManager(root)
    with pytest.raises(BuilderError):
        fs.write("link", "bad")
    with pytest.raises(BuilderError):
        fs.read("link")
    fs.delete("link")
    assert outside.read_text() == "safe"
    (root / "directory").symlink_to(tmp_path)
    with pytest.raises(BuilderError):
        fs.mkdir("directory/new")


def test_text_metadata_and_hardlinks(tmp_path):
    file = tmp_path / "text"
    file.write_text("before")
    file.chmod(0o640)
    os.setxattr(file, "user.test", b"kept")
    fs = FilesystemManager(tmp_path)
    with pytest.raises(BuilderError, match="already exists"):
        fs.write("text", "unintended replacement", create_only=True)
    fs.write("text", "after")
    assert file.read_text() == "after"
    assert stat.S_IMODE(file.stat().st_mode) == 0o640
    assert os.getxattr(file, "user.test") == b"kept"
    os.link(file, tmp_path / "hardlink")
    with pytest.raises(BuilderError, match="hard-linked"):
        fs.write("text", "again")


def test_special_files_never_opened(tmp_path):
    os.mkfifo(tmp_path / "fifo")
    with pytest.raises(BuilderError, match="regular"):
        read_text(tmp_path / "fifo")
    with pytest.raises(BuilderError):
        with UserFiles(os.getuid()).read(str(tmp_path / "fifo")):
            pytest.fail("A FIFO was opened as input")


def test_root_deletion_rename_rejected(tmp_path):
    fs = FilesystemManager(tmp_path)
    for value in ("", ".", "./"):
        with pytest.raises(BuilderError):
            fs.delete(value)
        with pytest.raises(BuilderError):
            fs.rename(value, "other")


def test_user_output_never_overwrites(tmp_path):
    path = tmp_path / "existing"
    path.write_text("original")
    with pytest.raises(FileExistsError):
        with UserFiles(os.getuid()).write(str(path)):
            pass
    assert path.read_text() == "original"


def test_commands_are_arrays_and_failures_propagate():
    with pytest.raises(BuilderError, match="failed"):
        CommandRunner().run(["/usr/bin/false"])
    result = CommandRunner().run(["/usr/bin/printf", "%s", "$(touch should-not-exist)"])
    assert result == "$(touch should-not-exist)"


def test_sandbox_does_not_expose_host_root(tmp_path):
    class Capture:
        def run(self, args, **options):
            self.args = args
            self.descriptors = options["pass_fds"]
            assert all(os.fstat(fd) for fd in self.descriptors)
            return ""
    capture = Capture()
    Sandbox(capture).run(["true"], reads={tmp_path: "/input"})
    assert "--unshare-net" in capture.args
    assert "--unshare-pid" in capture.args
    assert "--cap-drop" in capture.args
    assert ["--bind", "/", "/"] not in [capture.args[i:i+3] for i in range(len(capture.args))]
    assert "/etc/shadow" not in capture.args
    assert "--ro-bind-fd" in capture.args
    for fd in capture.descriptors:
        with pytest.raises(OSError):
            os.fstat(fd)
