import io
import os
import shutil
import stat
from pathlib import Path

import pytest

from core.errors import BuilderError
from core.package_manager import PackageManager, apt_command, package_names
from core.vm_bootstrap import capability
from services.commands import CommandRunner, Sandbox
from services.target_vm import TargetVM, valid_input


@pytest.fixture
def tiny_root(tmp_path):
    info = capability()
    if not info["available"]:
        pytest.skip(info["reason"])
    root = tmp_path / "rootfs"
    for name in ("bin", "sbin", "dev", "proc", "sys", "run", "tmp", "etc", "usr/sbin", "var/lib/dpkg"):
        (root / name).mkdir(parents=True, exist_ok=True)
    shutil.copyfile(shutil.which("busybox"), root / "bin/busybox")
    (root / "bin/busybox").chmod(0o755)
    for name in ("sh", "mkdir", "chmod"):
        (root / "bin" / name).symlink_to("busybox")
    (root / "etc/resolv.conf").write_text("original DNS\n")
    (root / "var/lib/dpkg/status").write_text(
        "Package: hello\nStatus: install ok installed\nVersion: 1\nArchitecture: amd64\n"
        "Description: Hello\nDepends: libc6\nInstalled-Size: 12\n\n")
    (tmp_path / "snapshots").mkdir()
    return root


def test_package_names_and_host_targeting():
    for value in (["-oRootDir=/"], ["foo;id"], ["foo/bar"], ["foo=1"], ["x\nid"], []):
        with pytest.raises(BuilderError):
            package_names(value)
    assert package_names(["hello", "libc6:amd64"]) == ["hello", "libc6:amd64"]
    command = apt_command("install", ["hello"], simulate=True)
    assert command[0] == "/usr/bin/apt-get"
    assert "--simulate" in command and "--no-remove" in command
    assert command[-2:] == ["--", "hello"]
    assert "--only-upgrade" in apt_command("upgrade", ["hello"])
    with pytest.raises(BuilderError):
        apt_command("dist-upgrade", [])


def test_control_protocol_rejects_arbitrary_qmp():
    assert valid_input([{"type": "key", "data": {"down": True, "key": {"type": "qcode", "data": "a"}}}])
    assert not valid_input([{"type": "key", "data": {"down": True, "key": {"type": "number", "data": 1}}}])
    assert not valid_input([{"type": "abs", "data": {"axis": "x", "value": -1}}])
    assert not valid_input([{"execute": "human-monitor-command"}])
    assert not valid_input([{"type": "btn", "data": {"down": 1, "button": "left"}}])


def test_unavailable_egl_falls_back_without_exposing_gpu(monkeypatch):
    from services import graphics_devices
    node = Path('/dev/dri/renderD128')
    monkeypatch.setattr(graphics_devices, 'render_devices', lambda: (node, {node: str(node)}))
    class Unavailable:
        def run(self, *args, **kwargs):
            raise BuilderError('EGL initialization failed')
    display, device, devices = TargetVM(Unavailable()).graphics()
    assert display == 'none'
    assert device.startswith('virtio-vga,')
    assert devices == {}


@pytest.mark.integration
def test_vm_executes_and_persists_without_iso(tiny_root):
    log = []
    vm = TargetVM(Sandbox(CommandRunner()), log=log.append)
    result = vm.run(tiny_root, readonly=True,
                    commands=[["/bin/busybox", "echo", "TARGET_QUERY_OK"]], timeout=120)
    assert result["text"] == "TARGET_QUERY_OK"
    assert not (tiny_root / "usr/sbin/policy-rc.d").exists()
    vm.run(tiny_root, commands=[["/bin/sh", "-c",
                                'echo persisted > /etc/from-vm; chmod 640 /etc/from-vm']], timeout=120)
    assert (tiny_root / "etc/from-vm").read_text().strip() == "persisted"
    assert stat.S_IMODE((tiny_root / "etc/from-vm").stat().st_mode) == 0o640
    assert not list(tiny_root.parent.glob("*.iso"))


@pytest.mark.integration
@pytest.mark.parametrize("compressed", [False, True])
def test_live_switch_root_and_framebuffer(tiny_root, compressed):
    import base64
    init = tiny_root / "sbin/init"
    init.write_text("#!/bin/sh\necho live-edit-saved > /etc/live-edit\n"
                    "/bin/busybox sleep 3\n/bin/busybox sync\n/bin/busybox poweroff -f\n")
    init.chmod(0o755)
    events = []
    result = TargetVM(Sandbox(CommandRunner()), emit=events.append).run(
        tiny_root, live=True, timeout=120, control_stream=io.BytesIO(b""), compress_frames=compressed)
    assert result["forced"] is False
    assert (tiny_root / "etc/live-edit").read_text().strip() == "live-edit-saved"
    frames = [base64.b64decode(event["data"]) for event in events if event["type"] == "frame"]
    if compressed:
        import zlib
        frames = [zlib.decompress(frame) for frame in frames]
    assert frames and frames[0].startswith(b"P6")
    assert any(event.get("ready") is True for event in events)


@pytest.mark.integration
@pytest.mark.parametrize("gpu", ["software", "auto"])
def test_live_restart_preserves_project_and_returns_framebuffer(tiny_root, gpu):
    import base64
    init = tiny_root / "sbin/init"
    init.write_text("""#!/bin/sh
if [ ! -f /etc/restarted ]; then
 echo retained > /etc/restarted
 /bin/busybox sync
 /bin/busybox reboot -f
fi
echo booted-again > /etc/second-boot
/bin/busybox sleep 3
/bin/busybox poweroff -f
""")
    init.chmod(0o755)
    events, commands = [], []
    sandbox = Sandbox(CommandRunner())
    original_run = sandbox.run
    def run(args, **kwargs):
        commands.append(args)
        return original_run(args, **kwargs)
    sandbox.run = run
    readfd, writefd = os.pipe()
    try:
        with os.fdopen(readfd, "rb") as controls:
            result = TargetVM(sandbox, emit=events.append).run(
                tiny_root, live=True, timeout=120, control_stream=controls,
                vm_settings={"ram_gib": 2, "cpus": 1, "gpu": gpu,
                             "resolution": "1280x800", "gpu_memory_mib": 256})
    finally:
        os.close(writefd)
    assert result["forced"] is False
    assert (tiny_root / "etc/restarted").read_text().strip() == "retained"
    assert (tiny_root / "etc/second-boot").read_text().strip() == "booted-again"
    restart = next(i for i, event in enumerate(events)
                   if "VM restarting" in event.get("message", ""))
    assert any(event["type"] == "frame" and base64.b64decode(event["data"]).startswith(b"P6")
               for event in events[restart + 1:])
    args = commands[-1]
    if "-vnc" in args:
        assert args[args.index("-audiodev") + 1] == "none,id=live-audio"
        assert args[args.index("-vnc") + 1].endswith(",audiodev=live-audio")
    assert args[args.index("-m") + 1] == "2048"
    assert args[args.index("-smp") + 1] == "1"
    assert any(arg.startswith(("virtio-vga,", "virtio-vga-gl,")) and
               "xres=1280,yres=800,max_hostmem=268435456" in arg for arg in args)
    assert "-no-reboot" not in args


@pytest.mark.integration
def test_live_keyboard_reaches_guest_evdev(tiny_root):
    import json
    import struct
    init = tiny_root / "sbin/init"
    init.write_text("""#!/bin/sh
for attempt in 1 2 3 4 5; do
 for event in /sys/class/input/event*; do
  name=$(/bin/busybox cat "$event/device/name")
  case "$name" in
   *keyboard*|*Keyboard*)
    exec 3<"/dev/input/${event##*/}"
    echo ready > /etc/input-ready
    /bin/busybox dd of=/etc/keyboard-event bs=24 count=2 <&3
    /bin/busybox sync
    /bin/busybox poweroff -f
   ;;
  esac
 done
 /bin/busybox sleep 1
done
/bin/busybox poweroff -f
""")
    init.chmod(0o755)
    readfd, writefd = os.pipe()
    sent = False
    def emit(event):
        nonlocal sent
        if event["type"] == "frame" and (tiny_root / "etc/input-ready").exists() and not sent:
            sent = True
            for down in (True, False):
                message = {"action": "input", "events": [
                    {"type": "key", "data": {"down": down, "key": {"type": "qcode", "data": "a"}}}]}
                os.write(writefd, (json.dumps(message) + "\n").encode())
    import threading
    stop_sender = threading.Event()
    def wait_for_input():
        while not stop_sender.wait(0.05):
            emit({"type": "frame"})
            if sent:
                return
    sender = threading.Thread(target=wait_for_input)
    sender.start()
    try:
        with os.fdopen(readfd, "rb") as controls:
            TargetVM(Sandbox(CommandRunner())).run(
                tiny_root, live=True, timeout=45, control_stream=controls)
    finally:
        stop_sender.set()
        sender.join()
        os.close(writefd)
    assert sent
    events = list(struct.iter_unpack("qqHHi", (tiny_root / "etc/keyboard-event").read_bytes()))
    assert any(event[2:] == (1, 30, 1) for event in events)  # EV_KEY, KEY_A, pressed


@pytest.mark.integration
def test_live_absolute_tablet_reaches_guest_evdev(tiny_root):
    import json
    import struct
    init = tiny_root / "sbin/init"
    init.write_text("""#!/bin/sh
for attempt in 1 2 3 4 5; do
 for event in /sys/class/input/event*; do
  name=$(/bin/busybox cat "$event/device/name")
  case "$name" in
   *Tablet*|*tablet*)
    exec 3<"/dev/input/${event##*/}"
    echo ready > /etc/input-ready
    /bin/busybox dd of=/etc/keyboard-event bs=24 count=3 <&3
    /bin/busybox sync
    /bin/busybox poweroff -f
   ;;
  esac
 done
 /bin/busybox sleep 1
done
/bin/busybox poweroff -f
""")
    init.chmod(0o755)
    readfd, writefd = os.pipe()
    sent = False
    def emit(event):
        nonlocal sent
        if event["type"] == "frame" and (tiny_root / "etc/input-ready").exists() and not sent:
            sent = True
            message = {"action": "input", "events": [
                {"type": "abs", "data": {"axis": "x", "value": 10000}},
                {"type": "abs", "data": {"axis": "y", "value": 20000}}]}
            os.write(writefd, (json.dumps(message) + "\n").encode())
    import threading
    stop_sender = threading.Event()
    def wait_for_input():
        while not stop_sender.wait(0.05):
            emit({"type": "frame"})
            if sent:
                return
    sender = threading.Thread(target=wait_for_input)
    sender.start()
    try:
        with os.fdopen(readfd, "rb") as controls:
            TargetVM(Sandbox(CommandRunner())).run(
                tiny_root, live=True, timeout=45, control_stream=controls)
    finally:
        stop_sender.set()
        sender.join()
        os.close(writefd)
    assert sent
    events = list(struct.iter_unpack("qqHHi", (tiny_root / "etc/keyboard-event").read_bytes()))
    assert any(event[2:] == (3, 0, 10000) for event in events)
    assert any(event[2:] == (3, 1, 20000) for event in events)


def test_package_commit_restore_and_failure(tiny_root, monkeypatch):
    manager = PackageManager(tiny_root.parent, Sandbox(CommandRunner()))
    original = (tiny_root / "etc/resolv.conf").read_bytes()
    def fake_guest(self, root, **kwargs):
        assert root != tiny_root
        (root / "etc/package-installed").write_text("new")
        (root / "etc/resolv.conf").write_text("guest DNS")
        (root / "usr/sbin/policy-rc.d").write_text("temporary")
        return {"text": "installed"}
    monkeypatch.setattr(TargetVM, "run", fake_guest)
    manager.apply({"action": "install", "packages": ["hello"], "network": False})
    assert (tiny_root / "etc/package-installed").read_text() == "new"
    assert (tiny_root / "etc/resolv.conf").read_bytes() == original
    assert not (tiny_root / "usr/sbin/policy-rc.d").exists()
    assert manager.snapshot()["restorable"]
    manager.restore()
    assert not (tiny_root / "etc/package-installed").exists()
    assert manager.snapshot()["restorable"]
    manager.discard()
    def failing_guest(self, root, **kwargs):
        (root / "etc/failed-operation").write_text("never committed")
        raise BuilderError("APT failed")
    monkeypatch.setattr(TargetVM, "run", failing_guest)
    with pytest.raises(BuilderError, match="APT failed"):
        manager.apply({"action": "remove", "packages": ["hello"]})
    assert not (tiny_root / "etc/failed-operation").exists()
    assert manager.snapshot() == {"exists": False}


def test_package_crash_snapshot_and_kernel_guard(tiny_root, monkeypatch):
    from core.util import atomic_json
    manager = PackageManager(tiny_root.parent, Sandbox(CommandRunner()))
    atomic_json(manager.marker, {"original_inode": tiny_root.stat().st_ino})
    shutil.copytree(tiny_root, manager.backup, symlinks=True)
    assert manager.snapshot() == {"exists": True, "restorable": False}
    with pytest.raises(BuilderError, match="No completed"):
        manager.restore()
    manager.discard()
    def changed_kernel(self, root, **kwargs):
        (root / "boot").mkdir()
        (root / "boot/vmlinuz-new").write_text("unsupported kernel change")
        return {}
    monkeypatch.setattr(TargetVM, "run", changed_kernel)
    with pytest.raises(BuilderError, match="kernel/initramfs/modules"):
        manager.apply({"action": "install", "packages": ["hello"]})
    assert not (tiny_root / "boot").exists()
    assert manager.snapshot() == {"exists": False}
    assert manager.list()["packages"][0]["Package"] == "hello"


def test_package_metadata_rejects_special_files_and_oversized_lines(tiny_root):
    manager = PackageManager(tiny_root.parent, Sandbox(CommandRunner()))
    status = tiny_root / "var/lib/dpkg/status"
    status.unlink()
    os.mkfifo(status)
    with pytest.raises(BuilderError, match="regular file"):
        manager.list()
    status.unlink()
    status.write_text("Package: " + "x" * 65536 + "\n")
    with pytest.raises(BuilderError, match="oversized line"):
        manager.list()


def test_vm_confinement_keeps_host_hidden(tmp_path):
    runner = CommandRunner()
    output = Sandbox(runner).run(["sh", "-c", "test ! -e /etc/shadow && test ! -d /home/tunnelbiz && echo confined"])
    assert output.strip() == "confined"


@pytest.mark.integration
def test_confined_live_bridge_control_and_recovery(tiny_root):
    import json
    import subprocess
    from core.project_manager import ProjectStore
    store = ProjectStore(tiny_root.parent / "store", os.getuid())
    project = store.create("Live bridge fixture")
    root = store.path(project["id"]) / "rootfs"
    tiny_root.rename(root)
    (root / "sbin/init").write_text("#!/bin/sh\necho saved > /etc/bridge-edit\n"
                                   "/bin/busybox sleep 3\n/bin/busybox poweroff -f\n")
    (root / "sbin/init").chmod(0o755)
    project.update(state="ready", analysis={"architecture": "amd64"})
    store.save(project)
    script = """
import json,sys,os
from core.project_manager import ProjectStore
from services.privileged_helper import UserFiles
from services.storage_bridge import engine_call
store=ProjectStore(sys.argv[1],os.getuid())
events=[]
def emit(event):
    events.append(event["type"])
result=engine_call(store,UserFiles(os.getuid()),
 {"operation":"live_edit","project_id":sys.argv[2],"confirmed":True},emit)
print(json.dumps({"state":result["state"],"events":events}))
"""
    result = subprocess.run(["python3", "-c", script, str(store.base), project["id"]],
                            input="", capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["state"] == "ready" and "frame" in data["events"] and "live_state" in data["events"]
    assert (root / "etc/bridge-edit").read_text().strip() == "saved"
    project = store.load(project["id"])
    assert project["history"][-1]["operation"] == "live_edit_stopped"
    project["state"] = "live_editing"
    store.save(project)
    store.recover(project)
    assert project["state"] == "ready"


@pytest.mark.integration
@pytest.mark.skipif(os.geteuid() != 0, reason="APT ownership integration needs the narrowly privileged test process")
def test_real_apt_install_and_remove_offline(tiny_root):
    """Build a local package; exercise real guest APT/dpkg, never host APT."""
    import re
    import subprocess
    from core.util import digest

    root = tiny_root
    for relative in ("usr/bin", "etc/apt/sources.list.d", "var/lib/apt/lists/partial",
                     "var/cache/apt/archives/partial", "var/log/apt", "var/lib/dpkg/updates",
                     "var/lib/dpkg/info", "var/lib/dpkg/triggers", "repo"):
        (root / relative).mkdir(parents=True, exist_ok=True)
    (root / "var/lib/dpkg/status").write_text("")
    (root / "etc/passwd").write_text("root:x:0:0:root:/root:/bin/sh\n")
    (root / "etc/group").write_text("root:x:0:\n")
    (root / "etc/apt/sources.list").write_text("deb [trusted=yes] file:/repo ./\n")
    shutil.copytree("/usr/share/dpkg", root / "usr/share/dpkg")
    tools = ["/usr/bin/apt-get", "/usr/bin/apt-cache", "/usr/bin/dpkg", "/usr/bin/dpkg-deb", "/usr/bin/dpkg-split",
             "/usr/bin/tar", "/usr/lib/apt/methods/file", "/usr/lib/apt/methods/copy",
             "/usr/lib/apt/methods/store"]
    for tool in tools:
        if not Path(tool).exists():
            pytest.skip(f"Test dependency {tool} is unavailable")
        output = subprocess.run(["ldd", tool], capture_output=True, text=True).stdout
        paths = [tool, *re.findall(r"(/[^\s()]+)", output)]
        for path in paths:
            source = Path(path)
            destination = root / source.relative_to("/")
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source.resolve(), destination)
            destination.chmod(source.stat().st_mode & 0o777)
    for name in ("rm", "ln", "mv", "cp", "touch", "rmdir", "cat", "sync", "test"):
        (root / "bin" / name).symlink_to("busybox")
    for tool in ("/usr/bin/diff", "/usr/sbin/ldconfig", "/usr/sbin/start-stop-daemon"):
        output = subprocess.run(["ldd", tool], capture_output=True, text=True).stdout
        for path in [tool, *re.findall(r"(/[^\s()]+)", output)]:
            source = Path(path)
            destination = root / source.relative_to("/")
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source.resolve(), destination)
            destination.chmod(source.stat().st_mode & 0o777)
    package = root.parent / "deb-source"
    (package / "DEBIAN").mkdir(parents=True)
    (package / "usr/share/pelandukos-vm-test").mkdir(parents=True)
    (package / "usr/share/pelandukos-vm-test/installed").write_text("guest-installed\n")
    control = ("Package: pelandukos-vm-test\nVersion: 1.0\nArchitecture: all\n"
               "Maintainer: Test <test@example.invalid>\nDescription: Isolated APT fixture\n")
    (package / "DEBIAN/control").write_text(control)
    deb = root / "repo/test.deb"
    subprocess.run(["dpkg-deb", "--build", "--root-owner-group", str(package), str(deb)],
                   capture_output=True, check=True)
    (root / "repo/Packages").write_text(
        control + f"Filename: ./test.deb\nSize: {deb.stat().st_size}\nSHA256: {digest(deb)}\n\n")
    manager = PackageManager(root.parent, Sandbox(CommandRunner()), log=print)
    manager.apply({"action": "refresh", "network": False})
    manager.discard()
    plan = manager.query({"action": "install", "packages": ["pelandukos-vm-test"]})["text"]
    assert "pelandukos-vm-test" in plan
    manager.apply({"action": "install", "packages": ["pelandukos-vm-test"], "network": False})
    installed = root / "usr/share/pelandukos-vm-test/installed"
    assert installed.read_text() == "guest-installed\n"
    assert installed.stat().st_uid == 0
    assert "pelandukos-vm-test" in manager.query({"action": "details", "packages": ["pelandukos-vm-test"]})["text"]
    manager.discard()
    manager.apply({"action": "remove", "packages": ["pelandukos-vm-test"], "network": False})
    assert not installed.exists()
    manager.restore()
    assert installed.exists()


@pytest.mark.integration
def test_autofs_is_available_before_guest_userspace(tiny_root):
    vm = TargetVM(Sandbox(CommandRunner()))
    result = vm.run(tiny_root, readonly=True, commands=[[
        "/bin/busybox", "grep", "-w", "autofs", "/proc/filesystems"]], timeout=120)
    assert "autofs" in result["text"]
    # The matching host module is preloaded, not installed into the ISO root.
    assert not (tiny_root / "lib/modules").exists()
