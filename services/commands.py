import os
import re
import stat
import shlex
import shutil
import subprocess
from pathlib import Path

from core.errors import BuilderError


class CommandRunner:
    def __init__(self, log=lambda message: None):
        self.log = log

    def run(self, args, *, timeout=7200, capture=True, output_handler=None, log_output=True,
            pass_fds=(), tick=None, stdin=None, stream=False):
        args = [str(arg) for arg in args]
        self.log("$ " + shlex.join(args))
        # Spool instead of PIPE to avoid deadlocks and retain bounded memory.
        import tempfile
        with tempfile.NamedTemporaryFile(prefix="pelandukos-command-") as output:
            process = subprocess.Popen(
                args, stdout=subprocess.PIPE if stream else output, stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL if stdin is None else stdin,
                env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C",
                     "HOME": "/nonexistent"}, start_new_session=True, pass_fds=pass_fds,
            )
            import time
            import signal
            started = time.monotonic()
            chunks = []
            captured = 0
            tail = ""
            reader = process.stdout if stream else open(output.name, "rb")
            try:
                while True:
                    status = process.poll()
                    if stream:
                        import select
                        data = os.read(reader.fileno(), 65536) if select.select([reader], [], [], 0)[0] else b""
                    else:
                        data = reader.read(65536)
                    if data:
                        text = data.decode("utf-8", errors="replace")
                        tail = (tail + text)[-6000:]
                        if log_output:
                            self.log(text.rstrip())
                        if output_handler:
                            output_handler(text)
                        if capture:
                            captured += len(data)
                            if captured > 16 * 1024 * 1024:
                                raise BuilderError("Command output exceeded the safety limit.")
                            chunks.append(text)
                    if status is not None and not data:
                        break
                    if tick:
                        tick()
                    if time.monotonic() - started > timeout:
                        raise BuilderError("Command timed out; inspect the job log before retrying.")
                    if not data:
                        time.sleep(0.02 if stream and tick else 0.08)
            finally:
                reader.close()
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            result = "".join(chunks)
            if process.returncode:
                raise BuilderError(f"{Path(args[0]).name} failed ({process.returncode}):\n{tail}")
            return result


class Sandbox:
    """Offline tool sandbox. Never executes programs from the imported image.

    Only explicitly selected inputs and output directories are exposed. The
    privileged service keeps numeric ownership by using the host user namespace;
    all other relevant namespaces are separate and capabilities are restricted.
    """
    def __init__(self, runner):
        self.runner = runner

    @staticmethod
    def guest_path(host, guest):
        return guest

    def run(self, command, *, reads=None, writes=None, allow_network=False, **options):
        if not shutil.which("bwrap"):
            raise BuilderError("bubblewrap is required; install it using the documented dependencies.")
        args = ["bwrap", "--die-with-parent", "--new-session", "--unshare-pid",
                "--unshare-ipc", "--unshare-uts", "--clearenv",
                "--setenv", "PATH", "/usr/sbin:/usr/bin:/sbin:/bin",
                "--setenv", "LC_ALL", "C", "--ro-bind", "/usr", "/usr"]
        if not allow_network:
            args += ["--unshare-net"]
        elif Path("/etc/resolv.conf").is_file():
            reads = dict(reads or {}) | {Path("/etc/resolv.conf"): "/etc/resolv.conf"}
        for name in ("bin", "sbin", "lib", "lib64"):
            path = Path("/") / name
            if path.is_symlink():
                args += ["--symlink", os.readlink(path), f"/{name}"]
            elif path.exists():
                args += ["--ro-bind", str(path), str(path)]
        args += ["--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp",
                 "--dir", "/etc", "--cap-drop", "ALL"]
        if os.geteuid() == 0:
            for cap in ("CAP_CHOWN", "CAP_DAC_OVERRIDE", "CAP_FOWNER",
                        "CAP_FSETID", "CAP_MKNOD", "CAP_SETFCAP"):
                args += ["--cap-add", cap]
        descriptors = []
        try:
            for flag, mapping in (("--ro-bind-fd", reads), ("--bind-fd", writes)):
                for host, guest in (mapping or {}).items():
                    if str(host) == "/dev/kvm" and guest == "/dev/kvm":
                        args += ["--dev-bind", "/dev/kvm", "/dev/kvm"]
                        continue
                    if (str(host) == guest and (re.fullmatch(r"/dev/dri/renderD[0-9]+", guest)
                                              or guest in ("/dev/nvidiactl", "/dev/nvidia0", "/dev/nvidia-uvm"))
                            and stat.S_ISCHR(Path(host).stat().st_mode)):
                        args += ["--dev-bind", str(host), guest]
                        continue
                    # Pin inputs before namespace setup. Passing a parent
                    # process's /proc FD path fails in unprivileged namespaces.
                    fd = os.open(host, os.O_PATH | os.O_CLOEXEC)
                    descriptors.append(fd)
                    args += [flag, str(fd), guest]
            args += ["--chdir", "/tmp", "--", *map(str, command)]
            return self.runner.run(args, pass_fds=tuple(descriptors), **options)
        finally:
            for fd in descriptors:
                os.close(fd)
