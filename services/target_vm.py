"""QEMU-backed target execution. No imported executable runs on the host."""
import base64
import json
import os
import re
import shlex
import socket
import select
import sys
import tempfile
import time
import uuid
import zlib
from pathlib import Path

from core.errors import BuilderError
from core.live_settings import validate_live_settings
from core.vm_bootstrap import bootstrap
from services.cpu_model import cpu_model


def valid_input(events):
    if not isinstance(events, list) or len(events) > 8:
        return False
    for event in events:
        if not isinstance(event, dict) or set(event) != {"type", "data"}:
            return False
        data = event["data"]
        if not isinstance(data, dict):
            return False
        if event["type"] == "abs":
            if set(data) != {"axis", "value"} or data["axis"] not in ("x", "y") or type(data["value"]) is not int:
                return False
            if not 0 <= data["value"] <= 32767:
                return False
        elif event["type"] == "btn":
            if set(data) != {"down", "button"} or type(data["down"]) is not bool:
                return False
            if data["button"] not in ("left", "right", "middle", "wheel-up", "wheel-down"):
                return False
        elif event["type"] == "key":
            if set(data) != {"down", "key"} or type(data["down"]) is not bool:
                return False
            key = data["key"]
            if not isinstance(key, dict) or set(key) != {"type", "data"} or key["type"] != "qcode":
                return False
            if not isinstance(key["data"], str) or not re.fullmatch(r"[a-z0-9_-]{1,24}", key["data"]):
                return False
        else:
            return False
    return True


class TargetVM:
    def __init__(self, sandbox, emit=lambda event: None, log=lambda text: None):
        self.sandbox, self.emit, self.log = sandbox, emit, log

    def graphics(self, settings=None):
        """Probe EGL/VirGL in confinement before choosing accelerated graphics."""
        from services.graphics_devices import render_devices
        settings = validate_live_settings(settings)
        width, height = settings["resolution"].split("x")
        suffix = f",xres={width},yres={height},max_hostmem={settings['gpu_memory_mib'] * 1024**2}"
        if settings["gpu"] == "software":
            return "none", "virtio-vga" + suffix, {}
        node, devices = render_devices()
        if node:
            display = "egl-headless,rendernode=" + str(node)
            device = "virtio-vga-gl" + suffix
            try:
                with tempfile.TemporaryFile() as commands:
                    commands.write(b'{"execute":"qmp_capabilities"}\n{"execute":"quit"}\n')
                    commands.seek(0)
                    self.sandbox.run(["qemu-system-x86_64", "-machine", "q35", "-nodefaults",
                                      "-m", "256", "-S", "-display", display, "-device", device,
                                      "-qmp", "stdio", "-sandbox",
                                      "on,obsolete=deny,elevateprivileges=deny,spawn=deny,resourcecontrol=deny"],
                                     reads={Path("/sys"): "/sys"}, writes=devices, stdin=commands, timeout=20)
                self.emit({"type": "stage", "message": "Live Desktop: accelerated VirtIO/VirGL graphics."})
                return display, device, devices
            except (BuilderError, OSError) as error:
                self.log("Accelerated graphics unavailable; using VirtIO software display: " + str(error))
        if settings["gpu"] == "accelerated":
            raise BuilderError("Accelerated 3D is unavailable. Select Automatic or Software graphics and try again.")
        return "none", "virtio-vga" + suffix, {}

    def run(self, root, *, commands=None, network=False, readonly=False, live=False, timeout=7200,
            control_stream=None, vm_settings=None, compress_frames=False):
        settings = validate_live_settings(vm_settings) if live else None
        token = uuid.uuid4().hex
        if live:
            from core.live_session_policy import bootstrap_policy
            # Read-only live mode is for non-mutating qualification runs.
            script = ("""
mount --move /target /lower
mount -t tmpfs tmpfs /writable
mkdir -p /writable/upper /writable/work
mount -t overlay overlay -o lowerdir=/lower,upperdir=/writable/upper,workdir=/writable/work /target || exit 72
""" if readonly else "") + bootstrap_policy() + """
mkdir -p /target/proc /target/sys /target/dev /target/run
mount --move /proc /target/proc
mount --move /sys /target/sys
mount --move /dev /target/dev
mount --move /run /target/run
mkdir -p /target/run/systemd/system/sysinit.target.wants
cat > /target/run/systemd/system/pelandukos-diagnostics.service <<'UNIT'
[Unit]
Description=PelandukOS desktop startup diagnostics
DefaultDependencies=no
After=systemd-journald.service
[Service]
Type=simple
ExecStart=/bin/sh -c 'sleep 25; echo PELANDUKOS_DESKTOP_DIAGNOSTICS; journalctl -b -u live-config -u display-manager --no-pager -n 50; ps -eo pid,ppid,stat,comm'
StandardOutput=tty
StandardError=tty
TTYPath=/dev/ttyS0
UNIT
ln -s ../pelandukos-diagnostics.service /target/run/systemd/system/sysinit.target.wants/pelandukos-diagnostics.service
exec switch_root /target /sbin/init
"""
        else:
            quoted = "\n".join("chroot /target " + shlex.join(command) + "\nrc=$?\n[ \"$rc\" -eq 0 ] || break"
                               for command in commands)
            setup = """
mkdir -p /target/proc /target/sys /target/dev /target/run /target/tmp
mount -t proc proc /target/proc
mount -t sysfs sysfs /target/sys
mount -t devtmpfs devtmpfs /target/dev
mount -t tmpfs tmpfs /target/run
mount -t tmpfs -o mode=1777 tmpfs /target/tmp
export DEBIAN_FRONTEND=noninteractive
export LC_ALL=C
export PATH=/usr/sbin:/usr/bin:/sbin:/bin
"""
            if not readonly:
                # This changes only the disposable package transaction root.
                setup += """
chroot /target /bin/sh -c 'mkdir -p /usr/sbin; printf "#!/bin/sh\\nexit 101\\n" > /usr/sbin/policy-rc.d; chmod 755 /usr/sbin/policy-rc.d'
"""
            if network:
                setup += """
ip link set eth0 up
ip addr add 10.0.2.15/24 dev eth0
ip route add default via 10.0.2.2
chroot /target /bin/sh -c 'rm -f /etc/resolv.conf; printf "nameserver 10.0.2.3\\n" > /etc/resolv.conf'
"""
            script = setup + f"""
echo PELANDUKOS_BEGIN_{token}
rc=0
while true; do
{quoted}
break
done
echo PELANDUKOS_END_{token}:$rc
sync
poweroff -f
"""
        self.emit({"type": "stage", "message": "Preparing isolated VM from the project filesystem (no ISO)…"})
        with tempfile.TemporaryDirectory(prefix="pelandukos-target-vm-") as temporary:
            runtime = Path(temporary)
            kernel = bootstrap(script, runtime / "initrd.gz")
            vm = self.sandbox.guest_path(runtime, "/vm")
            target = self.sandbox.guest_path(root, "/target-root")
            guest_kernel = self.sandbox.guest_path(kernel, "/kernel")
            kvm = Path("/dev/kvm")
            acceleration = "kvm" if os.access(kvm, os.R_OK | os.W_OK) else "tcg"
            display, graphics, graphics_paths = self.graphics(settings) if live else ("none", "virtio-vga", {})
            if live:
                self.emit({"type": "stage", "message":
                           f"Live Desktop: {settings['ram_gib']} GiB RAM, {settings['cpus']} CPUs, "
                           f"{settings['resolution']}, {settings['gpu_memory_mib']} MiB graphics limit."})
            args = ["qemu-system-x86_64", "-name", "PelandukOS live editor" if live else "PelandukOS package worker",
                    "-m", str(settings["ram_gib"] * 1024) if live else "1024", "-smp", str(settings["cpus"]) if live else "2", "-accel", acceleration,
                    "-cpu", cpu_model(acceleration),
                    "-global", "isa-fdc.fdtypeA=none", "-global", "isa-fdc.fdtypeB=none",
                    "-kernel", guest_kernel, "-initrd", vm + "/initrd.gz",
                    "-append", "console=tty0 console=ttyS0,115200 loglevel=3 rdinit=/init panic=1 net.ifnames=0 fsck.mode=skip" +
                    (" quiet splash plymouth.ignore-serial-consoles boot=live live-config.components=hostname,hosts,user-setup,sudo,gdm3,lightdm,sddm"
                     " rw fstab=no systemd.gpt_auto=no systemd.unit=graphical.target" if live else ""),
                    "-display", display, "-monitor", "none", "-serial", "stdio",
                    # Keep a 2D display listener active for EGL framebuffer readback.
                    # VNC otherwise auto-selects host PipeWire, whose startup can
                    # deadlock inside confinement before the guest even boots.
                    *(["-audiodev", "none,id=live-audio", "-vnc",
                       "unix:" + vm + "/display.sock,audiodev=live-audio"] if graphics_paths else []),
                    # Restart from the desktop must reboot, rather than exit QEMU.
                    *([] if live else ["-no-reboot"]),
                    "-device", graphics,
                    "-device", "qemu-xhci", "-device", "usb-tablet",
                    "-fsdev", f"local,id=root,path={target},security_model=none" + (",readonly=on" if readonly else ""),
                    "-device", "virtio-9p-pci,fsdev=root,mount_tag=target",
                    "-qmp", f"unix:{vm}/qmp.sock,server=on,wait=off",
                    "-nic", "user,model=virtio-net-pci" if network else "none",
                    "-sandbox", "on,obsolete=deny,elevateprivileges=deny,spawn=deny,resourcecontrol=deny"]
            read_paths = {kernel: "/kernel"}
            if graphics_paths:
                read_paths[Path("/sys")] = "/sys"
            write_paths = {runtime: "/vm"} | graphics_paths
            (read_paths if readonly else write_paths)[root] = "/target-root"
            if acceleration == "kvm":
                write_paths[kvm] = "/dev/kvm"
            controls = control_stream or sys.stdin.buffer if live else None
            control_buffer = b""
            control_eof = False

            def read_controls():
                nonlocal control_buffer, control_eof
                if control_eof:
                    return []
                try:
                    fd = controls.fileno()
                except (AttributeError, OSError):
                    incoming = controls.read(8192)  # bounded in-memory test stream
                else:
                    if not select.select([fd], [], [], 0)[0]:
                        return []
                    incoming = os.read(fd, 8192)
                if not incoming:
                    control_eof = True
                    return [{"action": "shutdown"}]
                control_buffer += incoming
                messages = []
                while b"\n" in control_buffer:
                    line, control_buffer = control_buffer.split(b"\n", 1)
                    if len(line) > 8192:
                        continue
                    try:
                        messages.append(json.loads(line))
                    except ValueError:
                        continue
                if len(control_buffer) > 8192:
                    raise BuilderError("Live control message exceeds 8 KiB.")
                return messages
            connection = None
            framebuffer = None
            buffer, serial = b"", ""
            ready, pending = False, False
            last_frame = 0
            shutdown_at = None
            forced = False

            def command(name, arguments=None, identifier=None):
                if connection:
                    data = {"execute": name}
                    if arguments:
                        data["arguments"] = arguments
                    if identifier:
                        data["id"] = identifier
                    try:
                        connection.sendall((json.dumps(data) + "\n").encode())
                    except (BrokenPipeError, ConnectionResetError):
                        # A normal guest poweroff may race the last framebuffer request.
                        pass

            def emit_frame(data, region=None):
                if compress_frames:
                    self.emit({"type": "frame", "encoding": "ppm-zlib",
                               "data": base64.b64encode(zlib.compress(data, 1)).decode(),
                               **({"region": region} if region else {})})
                else:
                    self.emit({"type": "frame", "data": base64.b64encode(data).decode()})

            frame_interval = 0.1 if compress_frames else 0.7

            def tick():
                nonlocal connection, framebuffer, buffer, ready, pending, last_frame, shutdown_at, forced
                if not live:
                    return
                if connection is None:
                    if not (runtime / "qmp.sock").exists():
                        return
                    connection = socket.socket(socket.AF_UNIX)
                    connection.connect(str(runtime / "qmp.sock"))
                    connection.setblocking(False)
                try:
                    buffer += connection.recv(65536)
                except BlockingIOError:
                    pass
                except ConnectionResetError:
                    # QMP can disappear just before QEMU's exit is reaped.
                    # CommandRunner still checks the process exit status.
                    return
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    data = json.loads(line)
                    if data.get("event") == "RESET":
                        self.emit({"type": "stage", "message": "VM restarting. Waiting for the desktop to return…"})
                    if "QMP" in data:
                        command("qmp_capabilities", identifier="ready")
                    if data.get("id") == "ready" and "return" in data:
                        ready = True
                        self.emit({"type": "live_state", "ready": True})
                    if data.get("id") == "frame":
                        pending = False
                        frame = runtime / "frame.ppm"
                        if "return" in data and frame.exists() and frame.stat().st_size <= 16 * 1024**2:
                            emit_frame(frame.read_bytes())
                if len(buffer) > 65536:
                    raise BuilderError("Invalid VM control response.")
                if not ready:
                    return
                for message in read_controls():
                    if not isinstance(message, dict):
                        continue
                    if message.get("action") == "input" and valid_input(message.get("events")):
                        command("input-send-event", {"events": message["events"]})
                    elif message.get("action") == "shutdown":
                        command("system_powerdown")
                        shutdown_at = shutdown_at or time.monotonic()
                        self.emit({"type": "stage", "message": "Waiting for the live desktop to shut down and save…"})
                    elif message.get("action") == "force_stop":
                        forced = True
                        command("quit")
                if shutdown_at and time.monotonic() - shutdown_at > 120:
                    forced = True
                    command("quit")
                if graphics_paths:
                    from services.rfb import Framebuffer
                    if framebuffer is None:
                        framebuffer = Framebuffer(runtime / "display.sock")
                    due = time.monotonic() - last_frame > frame_interval
                    frame = framebuffer.poll(request=due)
                    if frame is not None:
                        last_frame = time.monotonic()
                        patch = framebuffer.patch() if compress_frames else None
                        if patch:
                            emit_frame(*patch)
                        else:
                            emit_frame(frame)
                    return
                if not pending and time.monotonic() - last_frame > frame_interval:
                    pending = True
                    last_frame = time.monotonic()
                    command("screendump", {"filename": vm + "/frame.ppm"}, "frame")

            def output(text):
                nonlocal serial
                self.log(text)
                if not live:
                    serial += text
                    if len(serial) > 8 * 1024**2:
                        raise BuilderError("Package VM output exceeds 8 MiB.")

            try:
                self.emit({"type": "stage", "message": "Booting target filesystem in an isolated VM…"})
                self.sandbox.run(args, reads=read_paths, writes=write_paths, allow_network=network,
                                 capture=False, log_output=False, output_handler=output, tick=tick,
                                 stream=True, timeout=timeout)
            finally:
                if framebuffer:
                    framebuffer.close()
                if connection:
                    connection.close()
            if live:
                self.emit({"type": "live_state", "ready": False})
                return {"forced": forced}
            begin, end = f"PELANDUKOS_BEGIN_{token}", f"PELANDUKOS_END_{token}:"
            if begin not in serial or end not in serial:
                raise BuilderError("Target VM did not complete its operation. Inspect the boot log.")
            body = serial.split(begin, 1)[1].split(end, 1)[0].strip()
            status = serial.split(end, 1)[1].splitlines()[0].strip()
            if status != "0":
                raise BuilderError("Target package operation failed:\n" + body[-6000:])
            return {"text": body}
