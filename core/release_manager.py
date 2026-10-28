import re

from core.errors import BuilderError


def validate_version(value):
    if not isinstance(value, str):
        raise BuilderError("Enter a release version, for example 1.1 or 3.0-beta1.")
    value = value.strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.+_-]{0,63}", value):
        raise BuilderError("Release version must be 1–64 letters/digits, dots, +, _ or -; "
                           "start with a letter or digit, e.g. 3.0-beta1.")
    return value


def suggested_filename(distribution, version):
    def component(value, limit):
        return re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip(".-_")[:limit]
    return f"{component(distribution, 32) or 'PelandukOS'}-{component(version, 64) or '0.1'}-amd64.iso"


def update_release(manifest, values):
    allowed = {"distribution", "version", "build_number", "release_date", "developer", "notes"}
    if not isinstance(values, dict) or set(values) - allowed:
        raise BuilderError("Unknown release field.")
    for name, value in values.items():
        if not isinstance(value, str) or len(value) > (20000 if name == "notes" else 200):
            raise BuilderError(f"Invalid release field: {name}")
    if "version" in values:
        values = values | {"version": validate_version(values["version"])}
    manifest["release"].update(values)
    return manifest
