"""Bounded, shared configuration for the Live Desktop VM."""
from core.errors import BuilderError

RESOLUTIONS = ("1280x800", "1600x900", "1920x1080", "2560x1440")
DEFAULTS = {"ram_gib": 16, "cpus": 4, "gpu": "auto", "gpu_memory_mib": 1024,
            "resolution": "1920x1080"}


def validate_live_settings(value=None):
    if value is None:
        return dict(DEFAULTS)
    if not isinstance(value, dict) or set(value) - set(DEFAULTS):
        raise BuilderError("Invalid Live Desktop settings.")
    result = DEFAULTS | value
    for key, lower, upper in (("ram_gib", 1, 64), ("cpus", 1, 64), ("gpu_memory_mib", 128, 4096)):
        if type(result[key]) is not int or not lower <= result[key] <= upper:
            raise BuilderError(f"Live Desktop {key} must be an integer from {lower} to {upper}.")
    if result["gpu"] not in ("auto", "software", "accelerated"):
        raise BuilderError("Select automatic, software or accelerated graphics.")
    if result["resolution"] not in RESOLUTIONS:
        raise BuilderError("Unsupported Live Desktop resolution.")
    return result
