"""Build GDM-only styling, preserving resources and high contrast styles."""
import posixpath
import hashlib
import json
import tempfile
from pathlib import Path
from core.errors import BuilderError
from core.util import safe_path

THEMES = ('usr/share/gnome-shell/gnome-shell-theme.gresource',
          'usr/share/gnome-shell/gdm-theme.gresource')


def resolve_target(root, relative):
    # Absolute links belong to the imported image, never the host filesystem.
    for _ in range(16):
        path = safe_path(root, relative, leaf_symlink=True)
        if not path.is_symlink(): return path
        link = str(path.readlink())
        relative = posixpath.normpath(link if link.startswith('/') else '/'+str(Path(relative).parent)+'/'+link).lstrip('/')
    raise BuilderError('Login theme symbolic link loop.')


def prepare_login_theme(root, sandbox):
    from core.installed_appearance import write
    result = []
    base = Path(__file__).resolve().parent
    assets = base.parent/'resources/login'
    compatibility = json.loads((assets/'installer-compat.json').read_text())
    job = safe_path(root, compatibility['path'])
    replacement = (assets/'installer-appearance.py').read_bytes()
    if job.is_file() and job.read_bytes() != replacement:
        if hashlib.sha256(job.read_bytes()).hexdigest() not in compatibility['supported_sha256']:
            raise BuilderError('Update the live image to PelandukOS Installer 3.1.4 before applying the login theme; the existing appearance job is customized or unsupported.')
        backup = 'var/backups/pelandukos-iso-builder/installer-appearance.py'
        if not safe_path(root, backup).exists(): write(root, backup, job.read_bytes())
        write(root, compatibility['path'], replacement)
    # Read all inputs before replacing either logical theme path.
    inputs = []
    for rel in THEMES:
        source = resolve_target(root, rel)
        if not source.is_file(): continue
        if source.stat().st_size > 64 * 1024**2: raise BuilderError('Login theme exceeds 64 MiB.')
        inputs.append((rel, source.read_bytes()))
    for rel, data in inputs:
        with tempfile.TemporaryDirectory(prefix='pelandukos-login-') as directory:
            work = Path(directory); source = work/'input'; source.write_bytes(data)
            sandbox.run(['python3', '/worker.py'], reads={source: '/input',
                        base/'login_theme_worker.py': '/worker.py', base.parent/'resources/login': '/assets'},
                        writes={work: '/work'})
            backup = 'var/backups/pelandukos-iso-builder/login-theme/'+rel
            if not safe_path(root, backup).exists(): write(root, backup, data)
            destination = safe_path(root, rel, leaf_symlink=True)
            if destination.is_symlink(): destination.unlink()
            write(root, rel, (work/'theme.gresource').read_bytes())
        result.append(rel)
    return result
