import os
import shutil
import time

import pytest

from core.errors import BuilderError
from services.qemu import qemu_arguments


def test_qemu_has_no_host_share_or_network(tmp_path):
    iso = tmp_path / "image with spaces.iso"
    iso.write_bytes(b"fixture")
    arguments, _ = qemu_arguments(iso, tmp_path, "BIOS")
    assert arguments[arguments.index("-cdrom") + 1] == str(iso)
    assert arguments[arguments.index("-nic") + 1] == "none"
    assert "-virtfs" not in arguments
    assert "-drive" not in arguments
    assert "-sandbox" in arguments
    with pytest.raises(BuilderError):
        qemu_arguments(iso, tmp_path, "unknown")


@pytest.mark.integration
def test_embedded_preview_qmp_frame_and_cleanup(fixture_iso):
    pytest.importorskip("PySide6.QtWidgets")
    if not shutil.which("qemu-system-x86_64"):
        pytest.skip("QEMU is not installed")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from services.preview_manager import PreviewManager
    app = QApplication.instance() or QApplication([])
    preview = PreviewManager()
    frames, logs = [], []
    preview.frame.connect(frames.append)
    preview.log.connect(logs.append)
    preview.start(fixture_iso, "BIOS")
    directory = preview.temporary.name
    deadline = time.monotonic() + 10
    try:
        while not frames and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.03)
        assert frames, "\n".join(logs)
        assert os.path.isfile(frames[0])
        assert preview.ready
    finally:
        preview.stop()
        deadline = time.monotonic() + 5
        while preview.temporary and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.03)
        preview.kill_if_running()
        app.processEvents()
    assert not os.path.exists(directory)
