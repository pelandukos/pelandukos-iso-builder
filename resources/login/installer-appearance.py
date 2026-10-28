# SPDX-License-Identifier: GPL-3.0-or-later
"""Restore the builder's live appearance after optional package transactions."""
from pathlib import Path
import hashlib
import json
import shutil
import libcalamares

ASSETS = 'usr/share/pelandukos/appearance'


def pretty_name():
    return 'Applying the live desktop, login and boot appearance'


def safe(root, relative):
    parts = Path(relative).parts
    if not parts or Path(relative).is_absolute() or '..' in parts:
        raise ValueError('Invalid appearance asset path.')
    current = root
    for part in parts:
        current /= part
        if current.is_symlink(): raise ValueError('Appearance assets cannot follow symbolic links.')
    return current


def apply(root, desktop, call):
    root = Path(root).resolve()
    if root == Path('/') or not (root/'etc/os-release').is_file():
        raise ValueError('The installation target is missing or invalid.')
    manifest_path = safe(root, ASSETS+'/manifest.json')
    if not manifest_path.is_file():
        # Older images remain installable; new builder images always supply this.
        return
    manifest = json.loads(manifest_path.read_text())
    if manifest.get('schema_version') != 1: raise ValueError('Unsupported appearance snapshot.')
    if desktop:
        defaults = safe(root, ASSETS+'/desktop.ini').read_bytes()
        if hashlib.sha256(defaults).hexdigest() != manifest['desktop_sha256']:
            raise ValueError('Live desktop appearance checksum mismatch.')
        destination = safe(root, 'etc/dconf/db/local.d/99-pelandukos-live-appearance')
        destination.parent.mkdir(parents=True, exist_ok=True); destination.write_bytes(defaults)
        for rel, expected in manifest['gdm_assets'].items():
            if not (rel.startswith('etc/dconf/') or rel == 'etc/gdm3/greeter.dconf-defaults'
                    or rel in ('usr/share/gnome-shell/gnome-shell-theme.gresource',
                               'usr/share/gnome-shell/gdm-theme.gresource')
                    or rel == 'usr/share/pelandukos/branding/login-logo.svg'):
                raise ValueError('Unexpected login appearance asset.')
            src = safe(root, ASSETS+'/payload/'+rel)
            if hashlib.sha256(src.read_bytes()).hexdigest() != expected:
                raise ValueError('Login appearance checksum mismatch: '+rel)
            if rel in ('usr/share/gnome-shell/gnome-shell-theme.gresource',
                       'usr/share/gnome-shell/gdm-theme.gresource'):
                # Packages may recreate the GDM alternatives link. Replace only
                # this exact leaf after validating its parent and payload hash.
                parent = safe(root, str(Path(rel).parent))
                leaf = parent / Path(rel).name
                if leaf.is_symlink(): leaf.unlink()
            dst = safe(root, rel); dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src,dst); dst.chmod(0o644)
        if call(['dconf','update']): raise RuntimeError('Could not compile desktop/login defaults.')
    theme = manifest.get('grub_theme')
    if theme:
        if theme != '/usr/share/grub/themes/pelandukos-live/theme.txt' or not safe(root,theme[1:]).is_file():
            raise ValueError('Installed GRUB theme is missing.')
        dst = safe(root, 'etc/default/grub.d/99-pelandukos-appearance.cfg')
        dst.parent.mkdir(parents=True,exist_ok=True)
        dst.write_text('GRUB_THEME="'+theme+'"\nGRUB_TERMINAL_OUTPUT="gfxterm"\nGRUB_GFXMODE="auto"\nGRUB_BACKGROUND=""\n')
    report = safe(root,'var/lib/pelandukos/installed-appearance.json')
    report.parent.mkdir(parents=True,exist_ok=True)
    report.write_text(json.dumps({'source': manifest['source'], 'desktop': desktop,
                                 'desktop_sha256': manifest['desktop_sha256'],
                                 'grub_theme': theme},indent=2)+'\n')


def run():
    try:
        apply(libcalamares.globalstorage.value('rootMountPoint'),
              libcalamares.globalstorage.value('pelandukosProfile') == 'desktop',
              libcalamares.utils.target_env_call)
    except Exception as error:
        return ('Could not apply the live appearance', str(error))
    return None
