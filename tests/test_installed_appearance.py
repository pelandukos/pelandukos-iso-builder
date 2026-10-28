from pathlib import Path
import shutil
import pytest
from core.installed_appearance import appearance_only, copy_tree, prepare_installed_appearance, compile_db, dump_database
from core.errors import BuilderError
from services.commands import Sandbox, CommandRunner


def test_appearance_excludes_history_accounts_and_input_devices():
    settings = appearance_only("[org/gnome/shell]\nenabled-extensions=['crystal']\ncommand-history=['private']\n[org/gnome/desktop/input-sources]\nsources=[]\n[org/gnome/desktop/background]\npicture-uri='file:///wallpaper'\n")
    assert settings['org/gnome/shell']['enabled-extensions'] == "['crystal']"
    assert 'command-history' not in settings['org/gnome/shell']
    assert not settings.has_section('org/gnome/desktop/input-sources')


def test_live_extension_link_replaces_old_skeleton_copy(tmp_path):
    root = tmp_path
    source = root/'home/user/extensions'; source.mkdir(parents=True)
    (root/'usr/share/gnome-shell/extensions/dock').mkdir(parents=True)
    (source/'dock').symlink_to('/usr/share/gnome-shell/extensions/dock')
    old = root/'etc/skel/extensions/dock';old.mkdir(parents=True);(old/'old.js').write_text('old')
    copy_tree(root,'home/user/extensions','etc/skel/extensions')
    assert old.is_symlink()
    assert str(old.readlink()) == '/usr/share/gnome-shell/extensions/dock'
    copy_tree(root,'home/user/extensions','etc/skel/extensions')
    (source/'private').symlink_to('/etc/shadow')
    with pytest.raises(BuilderError):copy_tree(root,'home/user/extensions','etc/skel/extensions')


@pytest.mark.skipif(not shutil.which('dconf') or not shutil.which('bwrap'), reason='dconf/bwrap unavailable')
def test_new_account_and_installed_grub_use_edited_live_settings(tmp_path):
    root=tmp_path/'root';tree=tmp_path/'iso';stage=tmp_path/'stage'
    (root/'usr/bin').mkdir(parents=True);(root/'usr/bin/gnome-shell').touch()
    (root/'etc/skel').mkdir(parents=True);stage.mkdir()
    (tree/'boot/grub/live-theme').mkdir(parents=True)
    (tree/'boot/grub/live-theme/theme.txt').write_text('desktop-image: "../splash.png"\n+ boot_menu {}\n')
    (tree/'boot/grub/splash.png').write_bytes(b'old')
    (stage/'splash.png').write_bytes(b'current')
    sandbox=Sandbox(CommandRunner())
    compile_db(root,'home/user/.config/dconf/user',"[org/gnome/shell]\nenabled-extensions=['crystal']\ncommand-history=['private']\n",sandbox)
    compile_db(root,'etc/skel/.config/dconf/user',"[org/gnome/shell]\nenabled-extensions=['old']\n",sandbox)
    report=prepare_installed_appearance(root,tree,{'boot/grub/splash.png':stage/'splash.png'},sandbox)
    text=dump_database(root/'etc/skel/.config/dconf/user',sandbox)
    assert "['crystal']" in text and 'private' not in text and "['old']" not in text
    assert (root/'usr/share/grub/themes/pelandukos-live/splash.png').read_bytes()==b'current'
    assert '../splash.png' not in (root/'usr/share/grub/themes/pelandukos-live/theme.txt').read_text()
    assert report['grub_theme'] in (root/'etc/default/grub.d/99-pelandukos-appearance.cfg').read_text()


def test_gdm_logo_has_unlock_sized_viewport(tmp_path):
    from core.installed_appearance import prepare_login_logo
    import xml.etree.ElementTree as ET
    source=tmp_path/'usr/share/pelandukos/branding/logo.svg'
    source.parent.mkdir(parents=True)
    source.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="1024" height="1024" viewBox="0 0 1024 1024"><path d="M0 0"/></svg>')
    rel=prepare_login_logo(tmp_path)
    assert (tmp_path/rel).read_text().startswith('<svg ')
    logo=ET.parse(tmp_path/rel).getroot()
    assert logo.get('width')==logo.get('height')=='160'
    assert logo.get('viewBox')=='0 0 1024 1024'
    assert rel in (tmp_path/'etc/dconf/db/gdm.d/99-pelandukos-brand-logo').read_text()
    assert 'width="1024"' in source.read_text()
