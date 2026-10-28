"""Host devices needed by the QEMU renderer, never passed through to the guest."""
import os
import stat
from pathlib import Path


def render_devices():
    for node in sorted(Path('/dev/dri').glob('renderD[0-9]*')):
        if not os.access(node, os.R_OK | os.W_OK):
            continue
        devices = {node: str(node)}
        # NVIDIA's EGL implementation also opens these control/render nodes.
        # Do not expose modeset, display/card nodes, or arbitrary host devices.
        vendor = Path('/sys/class/drm') / node.name / 'device/vendor'
        nvidia = vendor.is_file() and vendor.read_text().strip() == '0x10de'
        for name in (('nvidiactl', 'nvidia0', 'nvidia-uvm') if nvidia else ()):
            path = Path('/dev') / name
            if path.exists() and stat.S_ISCHR(path.stat().st_mode) and os.access(path, os.R_OK | os.W_OK):
                devices[path] = str(path)
        return node, devices
    return None, {}
