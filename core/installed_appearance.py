"""Carry the edited live appearance to fresh accounts and installed boot menus.

Read imported dconf databases with the host tool in the offline sandbox. Never
copy a live user's entire database (history, credentials, and device state).
"""
import configparser
import hashlib
import io
import os
import posixpath
import shutil
import tempfile
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import unquote, urlparse

from core.errors import BuilderError
from core.util import safe_path, read_text, atomic_json

ASSETS = 'usr/share/pelandukos/appearance'
SECTIONS = {'org/gnome/desktop/interface', 'org/gnome/desktop/background',
            'org/gnome/desktop/screensaver', 'org/gnome/desktop/wm/preferences'}
SHELL_KEYS = {'enabled-extensions', 'disabled-extensions', 'favorite-apps',
              'disable-user-extensions', 'disable-extension-version-validation'}


def parse(text):
    p = configparser.ConfigParser(interpolation=None, strict=False)
    p.optionxform = str
    p.read_string(text)
    return p


def serialize(p):
    stream = io.StringIO(); p.write(stream, space_around_delimiters=False)
    return stream.getvalue()


def appearance_only(text):
    source, result = parse(text), parse('')
    for section in source.sections():
        if section not in SECTIONS and section != 'org/gnome/shell' and not section.startswith('org/gnome/shell/extensions/'):
            continue
        values = {k: v for k, v in source[section].items()
                  if (section != 'org/gnome/shell' or k in SHELL_KEYS)
                  and (section != 'org/gnome/desktop/screensaver' or k in
                       {'picture-uri', 'picture-options', 'primary-color', 'secondary-color', 'color-shading-type'})}
        if values: result[section] = values
    return result


def dump_database(path, sandbox):
    if not path.is_file(): return ''
    if path.stat().st_size > 16 * 1024**2:
        raise BuilderError('Live appearance database exceeds 16 MiB.')
    with tempfile.TemporaryDirectory(prefix='pelandukos-dconf-') as d:
        profile = Path(d) / 'profile'; profile.write_text('file-db:/database\n')
        return sandbox.run(['env', 'DCONF_PROFILE=/profile', 'dconf', 'dump', '/'],
                           reads={path: '/database', profile: '/profile'})


def write(root, relative, data):
    p = safe_path(root, relative)
    current = root
    for part in p.parent.relative_to(root).parts:
        current /= part
        if not current.exists(): current.mkdir(mode=0o755); current.chmod(0o755)
    p.write_bytes(data if isinstance(data, bytes) else data.encode()); p.chmod(0o644)
    return p


def compile_db(root, relative, text, sandbox):
    with tempfile.TemporaryDirectory(prefix='pelandukos-dconf-') as d:
        tmp = Path(d); (tmp/'keys').mkdir(); (tmp/'keys/00-appearance').write_text(text)
        sandbox.run(['dconf', 'compile', '/work/database', '/work/keys'], writes={tmp: '/work'})
        return write(root, relative, (tmp/'database').read_bytes())


def copy_tree(root, source, destination):
    src = safe_path(root, source)
    if not src.is_dir(): return
    total = 0
    for p in sorted(src.rglob('*')):
        relative = str(p.relative_to(root))
        if p.is_symlink():
            link = str(p.readlink())
            logical = posixpath.normpath(link if link.startswith('/') else '/'+str(p.parent.relative_to(root))+'/'+link)
            if not logical.startswith('/usr/share/'):
                raise BuilderError('Live theme links must point to shared system assets.')
            safe_path(root, logical.lstrip('/'))
            target = safe_path(root, destination+'/'+str(p.relative_to(src)), leaf_symlink=True)
            target.parent.mkdir(parents=True,exist_ok=True)
            if target.is_symlink() or target.is_file(): target.unlink()
            elif target.is_dir(): shutil.rmtree(target)
            target.symlink_to(logical)
            continue
        safe_path(root, relative)
        if not p.is_file(): continue
        total += p.stat().st_size
        if total > 128 * 1024**2: raise BuilderError('Live theme assets exceed 128 MiB.')
        dst = write(root, destination+'/'+str(p.relative_to(src)), p.read_bytes())
        dst.chmod(p.stat().st_mode & 0o777)


def prepare_login_logo(root):
    """Give GDM a bounded vector viewport matching the 160px unlock avatar."""
    source = safe_path(root, 'usr/share/pelandukos/branding/logo.svg')
    if not source.is_file(): return None
    data = source.read_bytes()
    if len(data) > 4 * 1024**2 or b'<!DOCTYPE' in data or b'<!ENTITY' in data:
        raise BuilderError('Invalid login logo SVG.')
    ET.register_namespace('', 'http://www.w3.org/2000/svg')
    ET.register_namespace('xlink', 'http://www.w3.org/1999/xlink')
    svg = ET.fromstring(data)
    if svg.tag != '{http://www.w3.org/2000/svg}svg' or not svg.get('viewBox'):
        raise BuilderError('Login logo needs an SVG viewBox.')
    svg.set('width', '160'); svg.set('height', '160')
    rel = 'usr/share/pelandukos/branding/login-logo.svg'
    write(root, rel, ET.tostring(svg, encoding='utf-8'))
    write(root, 'etc/dconf/db/gdm.d/99-pelandukos-brand-logo',
          "[org/gnome/login-screen]\nlogo='/"+rel+"'\nfallback-logo='/"+rel+"'\n")
    return rel


def prepare_installed_appearance(root, tree, changes, sandbox):
    if not safe_path(root, 'usr/bin/gnome-shell').is_file(): return {'gnome': 'not_present'}
    # PelandukOS live-config creates this account; never guess another personal account.
    live = 'home/user'
    database = safe_path(root, live+'/.config/dconf/user')
    settings = appearance_only(dump_database(database, sandbox))
    if not settings.sections():
        settings = appearance_only(dump_database(safe_path(root, 'etc/skel/.config/dconf/user'), sandbox))
    from core.login_theme import prepare_login_theme
    login_resources = prepare_login_theme(root, sandbox)
    # Relocate user-owned wallpaper files so the selected installation username
    # does not change whether the wallpaper can be opened.
    for section in settings.sections():
        for key, value in list(settings[section].items()):
            if key not in ('picture-uri', 'picture-uri-dark'): continue
            uri = value.strip("'"); parsed = urlparse(uri)
            if parsed.scheme != 'file': continue
            path = unquote(parsed.path)
            if path.startswith('/home/user/'):
                src = safe_path(root, path.lstrip('/'))
                if not src.is_file() or src.stat().st_size > 32 * 1024**2:
                    raise BuilderError('Live wallpaper is missing or too large.')
                name = hashlib.sha256(src.read_bytes()).hexdigest()+src.suffix
                dest = 'usr/share/backgrounds/pelandukos-live/'+name
                write(root, dest, src.read_bytes()); settings[section][key] = "'file:///"+dest+"'"
    desktop = serialize(settings)
    write(root, 'etc/dconf/db/local.d/99-pelandukos-live-appearance', desktop)
    # Keep the original skeleton for review/recovery before replacing stale defaults.
    backup = safe_path(root, 'var/backups/pelandukos-iso-builder/original-skel')
    if not backup.exists():
        backup.parent.mkdir(parents=True,exist_ok=True)
        skeleton = safe_path(root, 'etc/skel')
        skeleton.mkdir(parents=True, exist_ok=True)
        shutil.copytree(skeleton,backup,symlinks=True)
    # New accounts must not inherit old ISO defaults ahead of the system database.
    skel = parse(dump_database(safe_path(root, 'etc/skel/.config/dconf/user'), sandbox))
    for section in list(skel.sections()):
        if section in SECTIONS or section.startswith('org/gnome/shell/extensions/'):
            skel.remove_section(section)
        elif section == 'org/gnome/shell':
            for key in SHELL_KEYS: skel.remove_option(section, key)
    for section, keys in {
        'org/gnome/desktop/lockdown': ('disable-lock-screen',),
        'org/gnome/desktop/session': ('idle-delay',),
        'org/gnome/settings-daemon/plugins/power': ('sleep-inactive-ac-type', 'sleep-inactive-battery-type', 'idle-dim'),
    }.items():
        for key in keys: skel.remove_option(section, key) if skel.has_section(section) else None
    for section in settings.sections():
        if not skel.has_section(section): skel.add_section(section)
        skel[section].update(settings[section])
    compile_db(root, 'etc/skel/.config/dconf/user', serialize(skel), sandbox)
    write(root, 'etc/skel/.config/dconf/user.conf', desktop)
    for rel in ('.themes', '.icons', '.local/share/themes', '.local/share/icons',
                '.local/share/gnome-shell/extensions'):
        copy_tree(root, live+'/'+rel, 'etc/skel/'+rel)
    for rel in ('.config/gtk-3.0/gtk.css', '.config/gtk-3.0/settings.ini',
                '.config/gtk-4.0/gtk.css', '.config/gtk-4.0/settings.ini'):
        src = safe_path(root, live+'/'+rel)
        if src.is_file(): write(root, 'etc/skel/'+rel, src.read_bytes())
    # Save GDM assets separately from account/login policy; the installer restores
    # these after optional package installs, without copying live automatic login.
    login_logo = prepare_login_logo(root)
    payload = {}
    paths = ['etc/dconf/profile/gdm', 'etc/gdm3/greeter.dconf-defaults',
             'usr/share/gnome-shell/gnome-shell-theme.gresource']
    paths = list(dict.fromkeys(paths + login_resources))
    if login_logo: paths.append(login_logo)
    gdm = safe_path(root, 'etc/dconf/db/gdm.d')
    if gdm.is_dir(): paths += [str(p.relative_to(root)) for p in gdm.rglob('*') if p.is_file()]
    for rel in paths:
        src = safe_path(root, rel)
        if src.is_file():
            data = src.read_bytes(); write(root, ASSETS+'/payload/'+rel, data)
            payload[rel] = hashlib.sha256(data).hexdigest()
    # Stage the same effective GRUB assets used by this build, outside /boot so
    # the protected live kernel/initrd inventory is unchanged.
    theme_rel = 'boot/grub/live-theme/theme.txt'
    if 'boot/grub/theme.cfg' in changes:
        import re
        match = re.search(r'set theme=/?([^\s]+)', changes['boot/grub/theme.cfg'].read_text())
        if match: theme_rel = match[1]
    theme = changes.get(theme_rel, safe_path(tree, theme_rel))
    grub_theme = None
    if theme.is_file():
        prefix = str(Path(theme_rel).parent)
        source_dir = safe_path(tree, prefix)
        dest = 'usr/share/grub/themes/pelandukos-live'
        if source_dir.is_dir():
            for p in source_dir.rglob('*'):
                if p.is_file():
                    rel = str(p.relative_to(tree)); safe_path(tree, rel)
                    write(root, dest+'/'+str(p.relative_to(source_dir)), changes.get(rel,p).read_bytes())
        for rel, p in changes.items():
            if rel.startswith(prefix+'/'): write(root, dest+'/'+rel[len(prefix)+1:], p.read_bytes())
        text = theme.read_text()
        if '../splash.png' in text:
            splash = changes.get('boot/grub/splash.png', safe_path(tree,'boot/grub/splash.png'))
            write(root, dest+'/splash.png', splash.read_bytes()); text = text.replace('../splash.png','splash.png')
        write(root, dest+'/theme.txt', text)
        grub_theme = '/'+dest+'/theme.txt'
        write(root, 'etc/default/grub.d/99-pelandukos-appearance.cfg',
              'GRUB_THEME="'+grub_theme+'"\nGRUB_TERMINAL_OUTPUT="gfxterm"\nGRUB_GFXMODE="auto"\nGRUB_BACKGROUND=""\n')
    write(root, ASSETS+'/desktop.ini', desktop)
    manifest = {'schema_version': 1, 'source': 'edited-live-rootfs',
                'desktop_sha256': hashlib.sha256(desktop.encode()).hexdigest(),
                'gdm_assets': payload, 'grub_theme': grub_theme}
    atomic_json(safe_path(root, ASSETS+'/manifest.json'), manifest)
    # Compile system databases using the host tool, not executables from the image.
    for name in ('local','gdm'):
        directory = safe_path(root, 'etc/dconf/db/'+name+'.d')
        if directory.is_dir():
            with tempfile.TemporaryDirectory(prefix='pelandukos-dconf-') as d:
                sandbox.run(['dconf','compile','/out/database','/keys'], reads={directory:'/keys'}, writes={Path(d):'/out'})
                write(root, 'etc/dconf/db/'+name, (Path(d)/'database').read_bytes())
    return manifest
