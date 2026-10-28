import json
from pathlib import Path
import pytest
from core.boot_appearance import DEFAULTS, CONFIG, load, save, validate
from core.boot_splash import stage_custom_grub, theme_payload
from core.errors import BuilderError
from core.live_desktop_defaults import preserve_desktop_defaults, LEGACY, REPLACEMENT


def test_settings_persist_after_engine_reopen(engine, fixture_iso):
    p=engine.handle({'operation':'create','name':'Appearance'})
    request={'project_id':p['id']}
    engine.handle(request|{'operation':'import','source':str(fixture_iso)})
    settings=DEFAULTS|{'background':'#102030','grub_mode':'original'}
    engine.handle(request|{'operation':'boot_appearance_save','values':settings})
    engine.handle(request|{'operation':'open'})
    assert engine.handle(request|{'operation':'boot_appearance_get'})==settings
    release=engine.handle(request|{'operation':'build','filename':'appearance.iso'})['last_build']
    assert release['validation']['boot_capabilities_preserved']


def test_invalid_save_leaves_previous_settings_intact(tmp_path):
    previous=DEFAULTS|{'grub_mode':'original'}
    save(tmp_path,previous)
    for update in [{'background':'white; reboot'}, {'logo':'../../etc/passwd'},
                   {'grub_mode':'custom','grub_directory':'missing'}, {'schema_version':2}]:
        with pytest.raises(BuilderError):save(tmp_path,previous|update)
        assert load(tmp_path)==previous


def test_custom_grub_assets_copied_without_boot_entry_edits(tmp_path):
    root=tmp_path/'root';tree=tmp_path/'tree';stage=tmp_path/'stage';stage.mkdir()
    theme=root/'usr/share/grub/themes/melaka';theme.mkdir(parents=True)
    (theme/'theme.txt').write_text('desktop-image: "wall.png"\n+ boot_menu { item_color = "#ffffff" }\n')
    (theme/'wall.png').write_bytes(b'fixture')
    cfg=tree/'boot/grub/config.cfg';cfg.parent.mkdir(parents=True)
    cfg.write_text('source /boot/grub/theme.cfg\n')
    entries=cfg.parent/'grub.cfg';entries.write_text('linux /live/vmlinuz boot=live\ninitrd /live/initrd.xz\n')
    before=entries.read_bytes()
    settings=DEFAULTS|{'grub_mode':'custom','grub_directory':'usr/share/grub/themes/melaka'}
    save(root,settings)
    report={};changes=stage_custom_grub(root,tree,stage,load(root),report)
    assert 'boot/grub/pelandukos-custom/wall.png' in changes
    assert entries.read_bytes()==before
    assert 'boot/grub/grub.cfg' not in changes
    assert changes['boot/grub/theme.cfg'].read_text().strip()=='set theme=/boot/grub/pelandukos-custom/theme.txt'
    (theme/'wall.png').unlink()
    with pytest.raises(BuilderError,match='Missing custom GRUB asset'):save(root,settings)


def test_known_live_hook_no_longer_resets_desktop_or_spawns_dbus(tmp_path):
    hook=tmp_path/LEGACY;hook.parent.mkdir(parents=True)
    content=(Path(__file__).parent/'fixtures/legacy-pelandukos-theme').read_bytes()
    hook.write_bytes(content)
    defaults=tmp_path/'etc/dconf/db/local.d/90-pelandukos-crystal';defaults.parent.mkdir(parents=True)
    defaults.write_text('saved wallpaper and crystal panel')
    result=preserve_desktop_defaults(tmp_path)
    assert (tmp_path/result['backup']).read_bytes()==content
    assert hook.read_text()==REPLACEMENT
    assert defaults.read_text()=='saved wallpaper and crystal panel'
    assert preserve_desktop_defaults(tmp_path)['legacy_theme_hook']=='project_defaults_preserved'


def test_unknown_live_hook_is_preserved(tmp_path):
    hook=tmp_path/LEGACY;hook.parent.mkdir(parents=True);hook.write_text('# custom distribution hook\n')
    assert preserve_desktop_defaults(tmp_path)['legacy_theme_hook']=='unrecognized_preserved'
    assert hook.read_text()=='# custom distribution hook\n'


def test_managed_plymouth_colours_survive_install_without_touching_kernel(tmp_path):
    import base64
    from core.boot_splash import apply_plymouth_defaults
    from core.validator import kernel_inventory_digest
    root=tmp_path;theme=root/'usr/share/plymouth/themes/pelandukos';theme.mkdir(parents=True)
    (root/'etc/plymouth').mkdir(parents=True)
    (root/'etc/plymouth/plymouthd.conf').write_text('[Daemon]\nTheme=pelandukos\n')
    (theme/'pelandukos.plymouth').write_text('[Plymouth Theme]\nModuleName=script\n')
    (theme/'pelandukos.script').write_text('old script')
    (theme/'pelandukos.png').write_bytes(base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aX1sAAAAASUVORK5CYII='))
    (root/'boot').mkdir();(root/'boot/vmlinuz-test').write_bytes(b'protected kernel')
    before=kernel_inventory_digest(root)
    save(root,DEFAULTS|{'plymouth_mode':'managed','background':'#000000','accent':'#123456'})
    apply_plymouth_defaults(root)
    assert 'Window.SetBackgroundTopColor(0.0, 0.0, 0.0)' in (theme/'pelandukos.script').read_text()
    assert (theme/'crystal-fill.png').read_bytes().startswith(b'\x89PNG')
    assert kernel_inventory_digest(root)==before
    assert (root/'var/backups/pelandukos-boot-appearance/usr/share/plymouth/themes/pelandukos/pelandukos.script').read_text()=='old script'
    apply_plymouth_defaults(root)
    assert kernel_inventory_digest(root)==before


def test_plymouth_daemon_key_case_is_preserved(tmp_path):
    theme=tmp_path/'usr/share/plymouth/themes/pelandukos';theme.mkdir(parents=True)
    (tmp_path/'etc/plymouth').mkdir(parents=True)
    (tmp_path/'etc/plymouth/plymouthd.conf').write_text('[Daemon]\nTheme=pelandukos\nShowDelay=0\n')
    (theme/'pelandukos.plymouth').write_text('[Plymouth Theme]\nModuleName=script\n')
    payload=theme_payload(tmp_path)[2]['etc/plymouth/plymouthd.conf']
    assert b'Theme = pelandukos' in payload
    assert b'ShowDelay = 0' in payload
    assert b'\ntheme =' not in payload


def test_container_worker_maps_boot_renderer_paths(tmp_path):
    import base64
    from services.storage_worker import ConfinedTools
    from services.commands import CommandRunner
    from core.boot_splash import update_pelandukos_boot_menu
    root=tmp_path/'root';tree=tmp_path/'tree';stage=tmp_path/'stage';stage.mkdir()
    logo=root/DEFAULTS['logo'];logo.parent.mkdir(parents=True)
    logo.write_bytes(base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aX1sAAAAASUVORK5CYII='))
    cfg=tree/'isolinux/stdmenu.cfg';cfg.parent.mkdir(parents=True);cfg.write_text('old')
    changes=update_pelandukos_boot_menu(root,tree,stage,ConfinedTools(CommandRunner()),{'theme':'pelandukos'})
    assert changes['isolinux/splash.png'].read_bytes().startswith(b'\x89PNG')


def test_settings_roundtrip_through_confined_storage_worker(engine, fixture_iso):
    from services.storage_bridge import engine_call
    project=engine.handle({'operation':'create','name':'Container appearance'})
    pid=project['id']
    engine.handle({'operation':'import','project_id':pid,'source':str(fixture_iso)})
    def call(operation,**values):
        return engine_call(engine.store,engine.user_files,{'operation':operation,'project_id':pid,**values},lambda event:None)
    settings=DEFAULTS|{'grub_mode':'original','background':'#223344'}
    assert call('boot_appearance_save',values=settings)==settings
    assert call('boot_appearance_get')==settings
    built=call('build',filename='container-appearance.iso')
    assert built['last_build']['validation']['payload_checksum']
    assert call('boot_appearance_get')==settings
