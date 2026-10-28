"""Prevent a legacy live-config hook from overwriting project desktop choices."""
import hashlib
import os
import shutil
from core.util import safe_path, read_text

LEGACY = 'usr/lib/live/config/0040-pelandukos-theme'
# Exact known vendor hook: do not disable other distributions' custom hooks.
LEGACY_SHA256 = '74299977bb831f04feaf508088edbc0aa9c7341658b70ed63917644414af6c32'
REPLACEMENT = '''#!/bin/sh
# PelandukOS ISO Builder: desktop defaults are already stored in the image.
# Do not start a second user D-Bus session or overwrite its saved settings here.
exit 0
'''


def preserve_desktop_defaults(root):
    hook = safe_path(root, LEGACY)
    if not hook.exists():
        return {'legacy_theme_hook': 'absent'}
    content = read_text(hook).encode()
    if content == REPLACEMENT.encode():
        return {'legacy_theme_hook': 'project_defaults_preserved'}
    if hashlib.sha256(content).hexdigest() != LEGACY_SHA256:
        return {'legacy_theme_hook': 'unrecognized_preserved'}
    defaults = safe_path(root, 'etc/dconf/db/local.d')
    if not defaults.is_dir() or not any(defaults.glob('90-*')):
        return {'legacy_theme_hook': 'no_project_defaults_preserved'}
    backup = safe_path(root, 'var/backups/pelandukos-iso-builder/0040-pelandukos-theme.original')
    backup.parent.mkdir(parents=True, exist_ok=True)
    if not backup.exists():
        shutil.copy2(hook, backup)
    temporary = hook.with_name('.0040-pelandukos-theme.builder')
    with open(temporary, 'x') as stream:
        stream.write(REPLACEMENT)
        stream.flush(); os.fsync(stream.fileno())
    temporary.chmod(0o755)
    os.replace(temporary, hook)
    return {'legacy_theme_hook': 'project_defaults_preserved', 'backup': str(backup.relative_to(root))}
