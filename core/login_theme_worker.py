"""Trusted host helper; run inside Sandbox, never execute imported image code."""
import re
import subprocess
from pathlib import Path, PurePosixPath
import xml.etree.ElementTree as ET


def build():
    names = subprocess.check_output(['gresource', 'list', '/input']).decode().splitlines()
    if len(names) > 4096: raise ValueError('Too many theme resources')
    xml = ET.Element('gresources'); group = ET.SubElement(xml, 'gresource', prefix='/')
    css = Path('/assets/theme.css').read_bytes()
    background = '/org/gnome/shell/theme/pelandukos-login-background.svg'
    names = list(dict.fromkeys(names + [background]))
    total = changed = 0
    for index, name in enumerate(names):
        if not name.startswith('/') or '..' in PurePosixPath(name).parts:
            raise ValueError('Invalid resource path')
        data = (Path('/assets/background.svg').read_bytes() if name == background else
                subprocess.check_output(['gresource', 'extract', '/input', name]))
        total += len(data)
        if total > 128 * 1024**2: raise ValueError('Theme resources exceed 128 MiB')
        if name.endswith('.css') and 'high-contrast' not in name.lower():
            data = re.sub(rb'/\* PELANDUKOS_LOGIN_THEME_BEGIN \*/.*?/\* PELANDUKOS_LOGIN_THEME_END \*/', b'', data, flags=re.S)
            data = data.rstrip() + b'\n' + css
            changed += 1
        filename = 'resource-'+str(index)
        Path('/work', filename).write_bytes(data)
        ET.SubElement(group, 'file', alias=name.lstrip('/')).text = filename
    if not changed: raise ValueError('No supported login CSS in GNOME theme')
    ET.ElementTree(xml).write('/work/theme.xml', encoding='utf-8')
    subprocess.run(['glib-compile-resources', '/work/theme.xml', '--sourcedir=/work', '--target=/work/theme.gresource'], check=True)


if __name__ == '__main__': build()
