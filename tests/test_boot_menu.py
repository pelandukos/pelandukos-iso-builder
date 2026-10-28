from pathlib import Path
import shutil
import subprocess

from core.boot_menu import clean_live_grub_menu


def test_legacy_aliases_removed_and_distinct_boot_modes_preserved(tmp_path):
    tree = tmp_path / 'tree'
    config = tree / 'boot/grub'
    shutil.copytree(Path(__file__).parent / 'fixtures/grub-legacy', config)
    original = (config / 'grub.cfg').read_bytes()
    report = {}
    changes = clean_live_grub_menu(tree, tmp_path, report)
    output = changes['boot/grub/grub.cfg']
    text = output.read_text()
    assert text.count('menuentry "Install PelandukOS system (amd64)"') == 1
    assert 'source\t/boot/grub/install' not in text
    assert 'Advanced install options' not in text
    assert 'fail-safe mode' in text
    assert 'speakup.synth=soft' in text
    assert 'UEFI Firmware Settings' in text
    assert 'verify-checksums' in text
    assert 'findiso=${iso_path}' in text
    assert (config / 'grub.cfg').read_bytes() == original
    if shutil.which('grub-script-check'):
        subprocess.run(['grub-script-check', str(output)], check=True)
    shutil.copyfile(output, config / 'grub.cfg')
    assert clean_live_grub_menu(tree, tmp_path, {}) == {}


def test_custom_or_real_installer_is_not_removed(tmp_path):
    config = tmp_path / 'boot/grub'
    shutil.copytree(Path(__file__).parent / 'fixtures/grub-legacy', config)
    start = config / 'install_start.cfg'
    start.write_text(start.read_text().replace('/live/vmlinuz', '/install/vmlinuz'))
    assert clean_live_grub_menu(tmp_path, tmp_path, {}) == {}


def test_missing_grub_is_unchanged(tmp_path):
    assert clean_live_grub_menu(tmp_path, tmp_path, {}) == {}
