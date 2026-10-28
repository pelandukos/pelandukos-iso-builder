"""App-created ext4 containers on exFAT, mounted only in the helper namespace.

No caller-supplied image/device/mount options are accepted. Container contents
are untrusted and must only be processed by the confined storage worker.
"""
import ctypes
import errno
import fcntl
import os
import stat
import tempfile
import uuid
from contextlib import contextmanager
from pathlib import Path

from core.errors import BuilderError
from core.iso_extractor import require_space
from core.progress import Progress, ToolProgress, stage
from services.commands import CommandRunner, Sandbox

GIB = 1024**3


def container_size(value):
    if type(value) is not int or not 4 <= value <= 16384:
        raise BuilderError("Container size must be between 4 and 16384 GiB.")
    return value * GIB


def allocate(fd, size, progress, emit=lambda event: None):
    """Reserve real disk space; exFAT does not provide sparse-file guarantees."""
    measured = Progress(emit, "Reserving exFAT container space")
    measured.update(0, size)
    done, fallback = 0, False
    block = bytes(8 * 1024**2)
    while done < size:
        length = min(256 * 1024**2, size - done)
        if not fallback:
            try:
                os.posix_fallocate(fd, done, length)
                done += length
            except OSError as error:
                if error.errno not in (errno.EOPNOTSUPP, errno.ENOSYS, errno.EINVAL):
                    raise
                fallback = True
                os.lseek(fd, done, os.SEEK_SET)
        if fallback:
            written = os.write(fd, block[:min(len(block), size - done)])
            if written <= 0:
                raise BuilderError("Container allocation made no progress.")
            done += written
        measured.update(done, size)
    stage(emit, "Flushing reserved container space to disk")
    os.fsync(fd)


def create_image(parent_fd, user_files, size, log, emit=lambda event: None):
    name = f"PelandukOS-{uuid.uuid4().hex}.ext4"
    require_space(Path(f"/proc/{os.getpid()}/fd/{parent_fd}"), size + GIB)
    with user_files.identity():
        fd = os.open(name, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                     0o600, dir_fd=parent_fd)
    try:
        log("Reserving space for the ext4 project container. This may take several minutes.")
        allocate(fd, size, log, emit)
        filesystem_uuid = str(uuid.uuid4())
        formatting = ToolProgress(emit, "Formatting ext4 container", "mkfs")
        Sandbox(CommandRunner(log)).run(
            ["stdbuf", "-o0", "-e0", "mkfs.ext4", "-F", "-m", "0", "-U", filesystem_uuid,
             "-E", "nodiscard,lazy_itable_init=0,lazy_journal_init=0", "/container"],
            writes={Path(f"/proc/{os.getpid()}/fd/{fd}"): "/container"},
            output_handler=formatting.feed, capture=False)
        stage(emit, "Flushing formatted container to disk")
        os.fsync(fd)
        info = os.fstat(fd)
        return {"kind": "ext4-image", "path": str(Path(os.readlink(
            f"/proc/{os.getpid()}/fd/{parent_fd}")) / name), "size": size,
            "inode": info.st_ino, "uuid": filesystem_uuid}
    except Exception:
        # Do not unlink a caller-controlled name after a race. The incomplete
        # container is never registered or mounted and can be removed by its owner.
        log(f"Container creation failed. An incomplete file may remain: {name}")
        raise
    finally:
        os.close(fd)


class LoopInfo(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint64) for name in
                ("device", "inode", "rdevice", "offset", "sizelimit")] + [
        ("number", ctypes.c_uint32), ("encrypt_type", ctypes.c_uint32),
        ("encrypt_key_size", ctypes.c_uint32), ("flags", ctypes.c_uint32),
        ("file_name", ctypes.c_char * 64), ("crypt_name", ctypes.c_char * 64),
        ("key", ctypes.c_char * 32), ("init", ctypes.c_uint64 * 2)]


class LoopConfig(ctypes.Structure):
    _fields_ = [("fd", ctypes.c_uint32), ("block_size", ctypes.c_uint32),
                ("info", LoopInfo), ("reserved", ctypes.c_uint64 * 8)]


def libc_call(name, *args):
    libc = ctypes.CDLL(None, use_errno=True)
    if getattr(libc, name)(*args) != 0:
        raise OSError(ctypes.get_errno(), f"{name} failed")


def private_mount_namespace():
    libc_call("unshare", 0x00020000)  # CLONE_NEWNS
    libc_call("mount", None, b"/", None, ctypes.c_ulong((1 << 14) | (1 << 18)), None)


@contextmanager
def loop_device(image_fd, size):
    """LO_FLAGS_AUTOCLEAR detaches even after a killed helper's last mount exits."""
    control = os.open("/dev/loop-control", os.O_RDWR | os.O_CLOEXEC)
    device = None
    try:
        for _ in range(16):
            number = fcntl.ioctl(control, 0x4C82)  # LOOP_CTL_GET_FREE
            path = f"/dev/loop{number}"
            fd = os.open(path, os.O_RDWR | os.O_CLOEXEC)
            config = LoopConfig()
            config.fd = image_fd
            config.info.sizelimit = size
            config.info.flags = 4  # LO_FLAGS_AUTOCLEAR
            try:
                fcntl.ioctl(fd, 0x4C0A, bytes(config))  # LOOP_CONFIGURE
            except OSError as error:
                os.close(fd)
                if error.errno == errno.EBUSY:
                    continue
                raise
            device = fd
            yield path
            return
        raise BuilderError("No unused loop device is available.")
    finally:
        if device is not None:
            os.close(device)
        os.close(control)


def validate_image(fd, location):
    info = os.fstat(fd)
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or info.st_size != location["size"]):
        raise BuilderError("Project container was replaced or resized. Restore the registered container.")
    header = os.pread(fd, 1024, 1024)
    if len(header) != 1024 or header[56:58] != b"\x53\xef" or header[104:120] != uuid.UUID(location["uuid"]).bytes:
        raise BuilderError("Project container has an invalid ext4 identity.")
    # App-created filesystems use an internal journal and no stored mount
    # options. Never let modified metadata request an external journal device.
    if (any(header[0xD0:0xE0]) or any(header[0xE4:0xE8])
            or any(header[0x200:0x240])
            or int.from_bytes(header[0x60:0x64], "little") & 0x8):
        raise BuilderError("External journals and stored mount options are forbidden in project containers.")


@contextmanager
def mounted_image(location, user_files, runtime):
    with user_files.identity():
        fd = os.open(location["path"], os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        validate_image(fd, location)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise BuilderError("This project container is already in use.") from error
        temporary = tempfile.mkdtemp(prefix="container-", dir=runtime)
        mounted = False
        try:
            with loop_device(fd, location["size"]) as device:
                # Fixed type/options; no fstab, host mount propagation or target programs.
                libc_call("mount", os.fsencode(device), os.fsencode(temporary), b"ext4",
                          ctypes.c_ulong(2 | 4 | 8), b"errors=remount-ro")
                mounted = True
                try:
                    yield Path(temporary)
                finally:
                    # Ordinary unmount flushes the filesystem. Never silently hide a
                    # flush error with lazy unmount or remove a still-mounted tree.
                    libc_call("umount2", os.fsencode(temporary), 0)
                    mounted = False
        finally:
            if not mounted:
                os.rmdir(temporary)
        os.fsync(fd)
    finally:
        os.close(fd)
