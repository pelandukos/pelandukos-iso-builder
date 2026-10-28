"""Carry the project's Plymouth theme into the ISO's live initramfs.

Linux accepts concatenated initramfs archives. A small, last-applied data
archive updates the theme while retaining the source's early microcode,
modules, binaries and live-boot scripts byte for byte. No target code runs.
"""
import configparser
import hashlib
import re
import shutil
import stat
from pathlib import Path

from core.errors import BuilderError
from core.util import read_text, safe_path
from core.validator import resolve_iso_path
from core.vm_bootstrap import newc
from core.boot_appearance import load as appearance_settings


def theme_payload(root):
    settings = appearance_settings(root)
    config = safe_path(root, 'etc/plymouth/plymouthd.conf')
    if not config.exists():
        return None
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.optionxform = str
    try:
        parser.read_string(read_text(config))
        theme = (settings['plymouth_theme'] or parser.get('Daemon', 'Theme', fallback='')).strip()
        if settings['plymouth_mode'] == 'managed':
            theme = 'pelandukos'
    except configparser.Error as error:
        raise BuilderError('Invalid Plymouth daemon configuration.') from error
    if not theme:
        return None
    if not re.fullmatch(r'[A-Za-z0-9_-]+', theme):
        raise BuilderError('Invalid Plymouth theme name.')
    prefix = f'usr/share/plymouth/themes/{theme}'
    directory = safe_path(root, prefix)
    descriptor = safe_path(root, f'{prefix}/{theme}.plymouth')
    if not descriptor.is_file():
        raise BuilderError(f'Plymouth theme is incomplete: {theme}')
    definition = configparser.ConfigParser(interpolation=None)
    try:
        definition.read_string(read_text(descriptor))
        module = definition.get('Plymouth Theme', 'ModuleName')
    except configparser.Error as error:
        raise BuilderError('Invalid Plymouth theme descriptor.') from error
    if not re.fullmatch(r'[A-Za-z0-9_-]+', module):
        raise BuilderError('Invalid Plymouth module name.')
    payload = {}
    total = 0
    for path in sorted(directory.rglob('*')):
        relative = path.relative_to(root).as_posix()
        path = safe_path(root, relative)
        if path.is_dir():
            continue
        if not stat.S_ISREG(path.lstat().st_mode):
            raise BuilderError(f'Plymouth assets must be regular files: {relative}')
        total += path.stat().st_size
        if total > 64 * 1024**2 or len(payload) >= 4096:
            raise BuilderError('Plymouth theme exceeds the 64 MiB / 4096 file limit.')
        payload[relative] = path.read_bytes()
    # Copy daemon settings too, including the selected theme and ShowDelay.
    if not parser.has_section('Daemon'):
        parser.add_section('Daemon')
    parser.set('Daemon', 'Theme', theme)
    import io
    stream = io.StringIO(); parser.write(stream)
    payload['etc/plymouth/plymouthd.conf'] = stream.getvalue().encode()
    if settings['plymouth_mode'] == 'managed':
        if module != 'script':
            raise BuilderError('Managed Plymouth appearance requires the script module.')
        script = (Path(__file__).resolve().parents[1] / 'resources/managed-plymouth.script').read_text()
        def rgb(value):
            return ', '.join(str(int(value[i:i+2], 16)/255) for i in (1, 3, 5))
        script = script.replace('1.0, 1.0, 1.0', rgb(settings['background']))
        script = script.replace('0.12, 0.21, 0.30', rgb(settings['foreground']))
        payload[f'{prefix}/pelandukos.script'] = script.encode()
        payload[f'{prefix}/pelandukos.plymouth'] = (
            '[Plymouth Theme]\nName=PelandukOS\nModuleName=script\n\n[script]\n'
            f'ImageDir=/{prefix}\nScriptFile=/{prefix}/pelandukos.script\n').encode()
        logo = safe_path(root, settings['logo'])
        if not logo.is_file() or logo.stat().st_size > 16 * 1024**2:
            raise BuilderError('Missing or oversized boot logo.')
        payload[f'{prefix}/pelandukos.png'] = logo.read_bytes()
        # Small progress-bar PNGs are generated from trusted RGB values.
        import struct, zlib
        def png(colour):
            data = bytes.fromhex(colour[1:]) * 240
            def chunk(kind, content):
                return struct.pack('!I', len(content)) + kind + content + struct.pack('!I', zlib.crc32(kind+content))
            return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('!2I5B',240,4,8,2,0,0,0))
                    + chunk(b'IDAT', zlib.compress((b'\0'+data)*4)) + chunk(b'IEND', b''))
        payload[f'{prefix}/crystal-fill.png'] = png(settings['accent'])
        payload[f'{prefix}/crystal-track.png'] = png(settings['foreground'])
    return theme, module, payload


def update_live_splash(root, tree, stage, validation, sandbox):
    selected = theme_payload(root)
    if selected is None:
        return {}, None
    theme, module, payload = selected
    settings = appearance_settings(root)
    if settings['plymouth_mode'] == 'managed':
        sandbox.run(['python3', '-c',
            "import sys; from PIL import Image; im=Image.open(sys.argv[1]); assert im.format == 'PNG'; "
            "assert im.width*im.height <= 16777216; im.verify()", "/logo"],
            reads={safe_path(root, settings['logo']): '/logo'})
    parents = set()
    for name in payload:
        parents.update(p.as_posix() for p in Path(name).parents if p.as_posix() != '.')
    entries = [(name, stat.S_IFDIR | 0o755, b'')
               for name in sorted(parents, key=lambda s: (s.count('/'), s))]
    entries += [(name, stat.S_IFREG | 0o644, data) for name, data in sorted(payload.items())]
    overlay = newc(entries)
    changes = {}
    for reference in validation['initramfs_references']:
        source = resolve_iso_path(tree, reference)
        relative = source.relative_to(tree).as_posix()
        if relative in changes:
            continue
        listing = sandbox.run(['lsinitramfs', '/initrd'], reads={source: '/initrd'})
        paths = {line.removeprefix('./') for line in listing.splitlines()}
        if not any(p.endswith(f'/plymouth/{module}.so') for p in paths):
            raise BuilderError(f'Source live initramfs lacks Plymouth module {module}; '
                               'cannot apply the selected bootsplash.')
        destination = stage / 'boot-splash' / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        with source.open('rb') as src, destination.open('wb') as dst:
            shutil.copyfileobj(src, dst)
            dst.write(overlay)
        changes[relative] = destination
    return changes, {'theme': theme, 'initramfs': sorted(changes),
                     'assets_sha256': {name: hashlib.sha256(data).hexdigest()
                                       for name, data in sorted(payload.items())}}


def update_pelandukos_boot_menu(root, tree, stage, sandbox, report):
    """Brand the supported PelandukOS GRUB/ISOLINUX menu before Plymouth starts."""
    settings = appearance_settings(root)
    if settings['grub_mode'] == 'original':
        return {}
    if settings['grub_mode'] == 'custom':
        return stage_custom_grub(root, tree, stage, settings, report)
    logo = safe_path(root, settings['logo'])
    if not logo.exists() and not safe_path(root, 'etc/pelandukos-iso-builder/boot-appearance.json').exists():
        return {}
    if not logo.is_file():
        raise BuilderError('PelandukOS bootsplash logo is missing.')
    output = stage / 'boot-menu'
    output.mkdir()
    # Decode imported images only in the offline sandbox.
    render = '''import sys
from PIL import Image
logo = Image.open(sys.argv[1]).convert('RGBA')
logo.thumbnail((112,112), Image.Resampling.LANCZOS)
for name,w,h in [('bios',640,480),('uefi',800,600)]:
    canvas = Image.new('RGB',(w,h),BACKGROUND)
    canvas.paste(logo,((w-logo.width)//2,int(h*.23)-logo.height//2),logo)
    canvas.save(sys.argv[2]+'/'+name+'.png')
'''
    render = render.replace('BACKGROUND', repr(settings['background']))
    sandbox.run(['python3', '-c', render, '/logo', '/out'], reads={logo: '/logo'}, writes={output: '/out'})
    changes = {}
    if safe_path(tree, 'boot/grub/live-theme/theme.txt').is_file():
        theme = output / 'theme.txt'
        theme.write_text('''desktop-image: "../splash.png"
desktop-color: "#ffffff"
title-text: "PelandukOS"
title-color: "#18364d"
title-font: "Unifont Regular 16"
message-font: "Unifont Regular 16"
terminal-font: "Unifont Regular 16"
+ boot_menu {
 left = 12%
 width = 76%
 top = 48%
 height = 38%
 item_color = "#456078"
 selected_item_color = "#ed7615"
 item_font = "Unifont Regular 16"
 selected_item_font = "Unifont Regular 16"
 item_height = 24
 item_spacing = 6
 icon_width = 0
 icon_height = 0
 item_icon_space = 0
}
+ label {
 top = 92%
 left = 0
 width = 100%
 height = 20
 text = "@KEYMAP_SHORT@"
 align = "center"
 color = "#60758a"
 font = "Unifont Regular 16"
}
''')
        changes.update({'boot/grub/splash.png': output / 'uefi.png',
                        'boot/grub/live-theme/theme.txt': theme})
    if safe_path(tree, 'isolinux/stdmenu.cfg').is_file():
        config = output / 'stdmenu.cfg'
        config.write_text('''menu background splash.png
menu color screen * #ff18364d #00ffffff none
menu color border * #00000000 #00000000 none
menu color title * #ff18364d #00ffffff none
menu color unsel * #ff456078 #00ffffff none
menu color hotkey * #ff18364d #00ffffff none
menu color sel * #ff18364d #ffe1f3ff none
menu color hotsel * #ff18364d #ffe1f3ff none
menu color tabmsg * #ff60758a #00ffffff none
menu color help * #ff60758a #00ffffff none
menu color disabled * #ff8c98a5 #00ffffff none
menu vshift 12
menu rows 9
menu helpmsgrow 23
menu cmdlinerow 24
menu timeoutrow 25
menu tabmsgrow 27
menu tabmsg ENTER: start PelandukOS    TAB: boot options
''')
        changes.update({'isolinux/splash.png': output / 'bios.png',
                        'isolinux/stdmenu.cfg': config})
    for relative, path in changes.items():
        if relative.endswith(('.txt', '.cfg')):
            text = path.read_text()
            for old, new in [('#ffffff', settings['background']), ('#18364d', settings['foreground']),
                             ('#456078', settings['foreground']), ('#ed7615', settings['accent']),
                             ('#ff18364d', '#ff'+settings['foreground'][1:]),
                             ('#ff456078', '#ff'+settings['foreground'][1:]),
                             ('#00ffffff', '#00'+settings['background'][1:])]:
                text = text.replace(old, new)
            path.write_text(text)
    if report is not None:
        report['boot_menu_assets'] = sorted(changes)
        report['appearance'] = settings
    return changes


def custom_grub_assets(root, directory):
    folder = safe_path(root, directory)
    theme = safe_path(root, directory + '/theme.txt')
    if not theme.is_file():
        raise BuilderError('Custom GRUB directory must contain theme.txt.')
    text = read_text(theme)
    if '+ boot_menu' not in text and not re.search(r'\+\s+boot_menu\s*\{', text):
        raise BuilderError('Custom GRUB theme must contain a boot_menu component.')
    if re.search(r'^\s*(menuentry|linux|initrd|source|configfile|insmod|set)\s', text, re.M):
        raise BuilderError('Select a GRUB theme.txt, not a boot configuration script.')
    stripped = re.sub(r'"[^"\n]*"|#[^\n]*', '', text)
    depth = 0
    for c in stripped:
        depth += (c == '{') - (c == '}')
        if depth < 0:
            raise BuilderError('Unbalanced GRUB theme braces.')
    if depth:
        raise BuilderError('Unbalanced GRUB theme braces.')
    assets = {}; total = 0
    for path in sorted(folder.rglob('*')):
        path = safe_path(root, path.relative_to(root).as_posix())
        if path.is_dir(): continue
        if not stat.S_ISREG(path.lstat().st_mode):
            raise BuilderError('Custom GRUB assets must be regular files.')
        total += path.stat().st_size
        if total > 64 * 1024**2 or len(assets) >= 4096:
            raise BuilderError('Custom GRUB theme exceeds the asset limit.')
        assets[path.relative_to(folder).as_posix()] = path
    import fnmatch
    for value in re.findall(r'"([^"\n]*)"', text):
        if re.search(r'\.(png|jpg|jpeg|tga|pf2)$', value, re.I):
            if value.startswith('/') or '..' in Path(value).parts or '\\' in value:
                raise BuilderError('GRUB theme images must stay inside its theme directory.')
            if not any(fnmatch.fnmatch(name, value) for name in assets):
                raise BuilderError(f'Missing custom GRUB asset: {value}')
    return assets


def stage_custom_grub(root, tree, stage, settings, report):
    assets = custom_grub_assets(root, settings['grub_directory'])
    config = safe_path(tree, 'boot/grub/config.cfg')
    if not config.is_file() or not re.search(r'^\s*source\s+/boot/grub/theme.cfg\s*$', read_text(config), re.M):
        raise BuilderError('This ISO does not expose the supported GRUB theme configuration hook.')
    output = stage / 'custom-grub'; output.mkdir()
    changes = {}
    for relative, source in assets.items():
        dest = output / relative; dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, dest)
        changes['boot/grub/pelandukos-custom/' + relative] = dest
    cfg = output / 'builder-theme.cfg'
    cfg.write_text('set theme=/boot/grub/pelandukos-custom/theme.txt\n')
    changes['boot/grub/theme.cfg'] = cfg
    if report is not None:
        report['boot_menu_assets'] = sorted(changes)
        report['appearance'] = settings
    return changes


def apply_plymouth_defaults(root):
    """Keep installed-system visuals consistent with the live initramfs."""
    settings = appearance_settings(root)
    if settings['plymouth_mode'] != 'managed' and not settings['plymouth_theme']:
        return
    selected = theme_payload(root)
    if selected is None:
        raise BuilderError('The project has no Plymouth daemon configuration.')
    import os, tempfile
    for relative, data in selected[2].items():
        path = safe_path(root, relative)
        if path.exists() and path.read_bytes() == data:
            continue
        if path.exists():
            backup = safe_path(root, 'var/backups/pelandukos-boot-appearance/' + relative)
            backup.parent.mkdir(parents=True, exist_ok=True)
            if not backup.exists():shutil.copy2(path, backup)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix='.boot-appearance-', dir=path.parent)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(data);stream.flush();os.fsync(stream.fileno())
            os.chmod(temporary, 0o644)
            os.replace(temporary,path)
        finally:
            if os.path.exists(temporary):os.unlink(temporary)
