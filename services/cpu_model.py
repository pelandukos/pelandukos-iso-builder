"""Keep the virtual CPU's mitigation interfaces consistent with its vendor."""
from pathlib import Path


def cpu_model(acceleration):
    if acceleration != "kvm":
        return "max"
    try:
        info = Path("/proc/cpuinfo").read_text()
    except OSError:
        return "host"
    fields = dict(line.split(":", 1) for line in info.split("\n\n", 1)[0].splitlines()
                  if ":" in line)
    fields = {key.strip(): value.strip() for key, value in fields.items()}
    if fields.get("vendor_id") == "AuthenticAMD" and "spec_ctrl" not in fields.get("flags", "").split():
        # Some QEMU/KVM versions expose Intel SSBD without its SPEC_CTRL
        # prerequisite on AMD. Keep the AMD/virtual SSBD interfaces inherited
        # from host; do not disable the guest's speculative-execution mitigations.
        return "host,ssbd=off"
    return "host"
