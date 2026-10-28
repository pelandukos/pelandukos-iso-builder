import subprocess
import shutil
from pathlib import Path
import pytest
from core.login_theme import prepare_login_theme
from services.commands import Sandbox, CommandRunner


@pytest.mark.skipif(not shutil.which('bwrap'), reason='sandbox unavailable')
def test_theme_preserves_contrast_resources_and_is_idempotent(tmp_path):
    root = tmp_path/'root'; work = tmp_path/'work'; work.mkdir()
    (work/'normal.css').write_text('stage { color: white; }')
    (work/'high-contrast.css').write_text('stage { color: yellow; }')
    (work/'theme.xml').write_text('<gresources><gresource prefix="/org/gnome/shell/theme"><file>normal.css</file><file>high-contrast.css</file></gresource></gresources>')
    target = root/'usr/share/gnome-shell/gnome-shell-theme.gresource'; target.parent.mkdir(parents=True)
    subprocess.run(['glib-compile-resources', str(work/'theme.xml'), '--sourcedir='+str(work), '--target='+str(target)], check=True)
    original = target.read_bytes()
    link = target.with_name('gdm-theme.gresource'); link.symlink_to('/usr/share/gnome-shell/gnome-shell-theme.gresource')
    sandbox = Sandbox(CommandRunner())
    result = prepare_login_theme(root, sandbox)
    first = target.read_bytes()
    assert len(result) == 2 and not link.is_symlink()
    prepare_login_theme(root, sandbox)
    assert target.read_bytes() == first
    css = subprocess.check_output(['gresource', 'extract', str(target), '/org/gnome/shell/theme/normal.css']).decode()
    assert css.count('PELANDUKOS_LOGIN_THEME_BEGIN') == 1
    assert 'login-dialog' in css
    assert subprocess.check_output(['gresource', 'extract', str(target), '/org/gnome/shell/theme/high-contrast.css']) == b'stage { color: yellow; }'
    assert (root/'var/backups/pelandukos-iso-builder/login-theme/usr/share/gnome-shell/gnome-shell-theme.gresource').read_bytes() == original


def test_custom_installer_module_is_not_overwritten(tmp_path):
    import json
    from core.errors import BuilderError
    compatibility=json.loads((Path(__file__).resolve().parents[1]/'resources/login/installer-compat.json').read_text())
    job=tmp_path/compatibility['path'];job.parent.mkdir(parents=True);job.write_text('custom job')
    with pytest.raises(BuilderError, match='customized or unsupported'):
        prepare_login_theme(tmp_path, Sandbox(CommandRunner()))
    assert job.read_text() == 'custom job'
