import gzip
import os
import stat
import subprocess
from pathlib import Path

import pytest

from core.boot_splash import update_live_splash
from core.errors import BuilderError
from core.vm_bootstrap import newc
from services.commands import CommandRunner, Sandbox


def project(tmp_path):
    root, tree, stage = [tmp_path / name for name in ('root', 'tree', 'stage')]
    theme = root / 'usr/share/plymouth/themes/pelandukos'
    theme.mkdir(parents=True)
    (root / 'etc/plymouth').mkdir(parents=True)
    (root / 'etc/plymouth/plymouthd.conf').write_text('[Daemon]\nTheme=pelandukos\n')
    (theme / 'pelandukos.plymouth').write_text('[Plymouth Theme]\nModuleName=script\n')
    (theme / 'pelandukos.script').write_text('NEW_WHITE_SPLASH')
    (tree / 'live').mkdir(parents=True)
    stage.mkdir()
    # Include a previous version of the same asset: the kernel must overwrite it.
    init = b'''#!/bin/busybox sh
/bin/busybox cat /usr/share/plymouth/themes/pelandukos/pelandukos.script
/bin/busybox poweroff -f
'''
    entries = [(name, stat.S_IFDIR | 0o755, b'') for name in
               ('bin', 'dev', 'usr', 'usr/lib', 'usr/lib/plymouth', 'usr/share',
                'usr/share/plymouth', 'usr/share/plymouth/themes',
                'usr/share/plymouth/themes/pelandukos')]
    entries += [('usr/lib/plymouth/script.so', stat.S_IFREG | 0o644, b'fixture'),
                ('usr/share/plymouth/themes/pelandukos/pelandukos.script',
                 stat.S_IFREG | 0o644, b'OLD_SPLASH'),
                ('bin/busybox', stat.S_IFREG | 0o755, Path('/usr/bin/busybox').read_bytes()),
                ('init', stat.S_IFREG | 0o755, init)]
    (tree / 'live/initrd').write_bytes(newc(entries))
    return root, tree, stage


def test_theme_updates_live_initramfs_without_mutating_source(tmp_path):
    root, tree, stage = project(tmp_path)
    source = (tree / 'live/initrd').read_bytes()
    changes, report = update_live_splash(root, tree, stage,
        {'initramfs_references': ['live/initrd']}, Sandbox(CommandRunner()))
    output = changes['live/initrd'].read_bytes()
    assert output[:len(source)] == source
    assert (tree / 'live/initrd').read_bytes() == source
    assert b'NEW_WHITE_SPLASH' in gzip.decompress(output[len(source):])
    assert report['theme'] == 'pelandukos'
    assert 'usr/share/plymouth/themes/pelandukos/pelandukos.script' in report['assets_sha256']


def test_missing_plymouth_module_refuses_silent_old_splash(tmp_path):
    root, tree, stage = project(tmp_path)
    (tree / 'live/initrd').write_bytes(newc([]))
    with pytest.raises(BuilderError, match='lacks Plymouth module'):
        update_live_splash(root, tree, stage, {'initramfs_references': ['live/initrd']},
                           Sandbox(CommandRunner()))


def test_theme_symlink_cannot_read_host_file(tmp_path):
    root, tree, stage = project(tmp_path)
    (root / 'usr/share/plymouth/themes/pelandukos/leak').symlink_to('/etc/passwd')
    with pytest.raises(BuilderError, match='Symlink traversal'):
        update_live_splash(root, tree, stage, {'initramfs_references': ['live/initrd']},
                           Sandbox(CommandRunner()))


def test_kernel_applies_appended_theme_over_old_asset(tmp_path):
    kernel = os.environ.get('PELANDUKOS_BOOT_KERNEL')
    if not kernel:
        pytest.skip('Set PELANDUKOS_BOOT_KERNEL for real kernel extraction test')
    root, tree, stage = project(tmp_path)
    changes, _ = update_live_splash(root, tree, stage,
        {'initramfs_references': ['live/initrd']}, Sandbox(CommandRunner()))
    result = subprocess.run(['qemu-system-x86_64', '-accel', 'kvm', '-m', '512',
        '-kernel', kernel, '-initrd', str(changes['live/initrd']), '-append',
        'console=ttyS0 rdinit=/init panic=1', '-nographic', '-no-reboot', '-nic', 'none'],
        capture_output=True, timeout=60)
    assert b'NEW_WHITE_SPLASH' in result.stdout, result.stdout[-4000:]
    assert b'OLD_SPLASH' not in result.stdout


def test_pelandukos_boot_menus_receive_current_logo(tmp_path):
    import base64
    from core.boot_splash import update_pelandukos_boot_menu
    root, tree, stage = project(tmp_path)
    (root/'usr/share/plymouth/themes/pelandukos/pelandukos.png').write_bytes(base64.b64decode(
        'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aX1sAAAAASUVORK5CYII='))
    for rel in ('isolinux/stdmenu.cfg', 'boot/grub/live-theme/theme.txt'):
        path=tree/rel;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('old theme')
    report={'theme':'pelandukos'}
    changes=update_pelandukos_boot_menu(root,tree,stage,Sandbox(CommandRunner()),report)
    assert len(changes)==4
    assert changes['isolinux/splash.png'].read_bytes().startswith(b'\x89PNG')
    assert 'menu color unsel' in changes['isolinux/stdmenu.cfg'].read_text()
    assert '#ffffff' in changes['boot/grub/live-theme/theme.txt'].read_text()
