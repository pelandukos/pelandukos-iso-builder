import pytest
from core import pelandukos_preset as preset
from core.errors import BuilderError
from core.package_manager import preset_command


def row(name, *, essential='', priority='optional'):
    return [name, 'installed', essential, priority, '1.0']


def test_minimal_protects_software_settings_network_and_boot():
    names = ['pelandukos-software', 'pelandukos-installer', 'gnome-control-center',
             'compiz-core', 'libcompizconfig0', 'network-manager', 'linux-image-test',
             'firmware-iwlwifi', 'grub-efi-amd64', 'gnome-shell', 'gnome-terminal']
    packages = {name: row(name) for name in names}
    packages['custom-essential'] = row('custom-essential', essential='yes')
    keep = preset.protected(packages)
    assert set(names) <= keep
    assert 'custom-essential' in keep
    assert not (keep & preset.OPTIONAL)
    for name in keep:
        with pytest.raises(RuntimeError, match='protected'):
            preset.check_removal(f'Remv {name}:amd64 [1.0]', keep)


def test_cleanup_rejects_installs_and_keeps_valid_optional_removal():
    with pytest.raises(RuntimeError, match='install'):
        preset.check_removal('Inst unexpected [1.0]', set())
    assert preset.check_removal('Purg vlc:amd64 [1.0]\nRemv gimp [1.0]', preset.KEEP) == ['gimp', 'vlc']


def test_preset_commands_are_fixed_and_network_required():
    with pytest.raises(BuilderError):
        preset_command({'preset': 'bad; touch /host'})
    with pytest.raises(BuilderError, match='network'):
        preset_command({'preset': 'minimal-gnome'})
    command = preset_command({'preset': 'minimal-gnome'}, simulate=True)
    assert command[:2] == ['/usr/bin/python3', '-c']
    assert command[-2:] == ['minimal-gnome', 'preview']
    compile(command[2], '<guest>', 'exec')


def test_repository_build_is_mandatory(monkeypatch):
    monkeypatch.setattr(preset, 'run', lambda args: '  Candidate: 1:51.0-1\n')
    with pytest.raises(RuntimeError, match='not the PelandukOS build'):
        preset.require_candidates()
    monkeypatch.setattr(preset, 'run', lambda args: '  Candidate: (none)\n')
    with pytest.raises(RuntimeError, match='Missing'):
        preset.require_candidates()


def test_installer_needs_profile_capable_version(monkeypatch):
    monkeypatch.setattr(preset, 'run', lambda args: '  Candidate: ' +
                        ('3.0.0' if args[-1] == 'pelandukos-installer' else '1+pelandukos1') + '\n')
    monkeypatch.setattr(preset.subprocess, 'call', lambda args: 1)
    with pytest.raises(RuntimeError, match='3.1.3'):
        preset.require_candidates()


def test_gui_preset_review_keeps_selected_profile(monkeypatch):
    pytest.importorskip("PySide6.QtWidgets")
    from PySide6.QtWidgets import QApplication
    from gui.package_page import PackagePage
    app = QApplication.instance() or QApplication([])
    class Window:
        after_job = []
        def submit(self, operation, callback, **kwargs):
            self.request = (operation, kwargs)
            callback({'text': 'Checked plan'})
    window = Window()
    page = PackagePage(window)
    reviewed = []
    monkeypatch.setattr(page, 'apply', lambda *a, **kw: reviewed.append(kw))
    page.preset.setCurrentIndex(1)
    page.preview_preset()
    page.preset.setCurrentIndex(0)
    window.after_job.pop()()
    assert window.request == ('package_query', {'action': 'preset', 'preset': 'pelandukos-apps'})
    assert reviewed[0]['preset'] == 'pelandukos-apps'
    page.close()


def test_runtime_engines_and_compiz_are_protected():
    names = preset.RUNTIMES + preset.COMPIZ + ['libcompizconfig0', 'python3-compizconfig']
    packages = {name: row(name) for name in names}
    assert set(names) <= preset.protected(packages)
    assert not (set(names) & preset.OPTIONAL)
    for name in names:
        with pytest.raises(RuntimeError, match='protected'):
            preset.check_removal('Purg ' + name + ' [1]', preset.protected(packages))


def test_library_auto_classification_preserves_apps_plugins_and_fuse():
    names = {'liborphan': 'libs', 'libsomething-dev': 'libdevel',
             'myapp': 'utils', 'libaudio-plugin': 'libs', 'libnss-myhostname': 'libs',
             'libfuse2t64': 'libs', 'libcompizconfig0': 'libs'}
    packages = {name: row(name) + [section] for name, section in names.items()}
    assert preset.library_dependencies(packages) == ['liborphan', 'libsomething-dev']


@pytest.mark.parametrize('kind', ['file', 'symlink', 'absent'])
def test_initramfs_config_restored_on_failure(tmp_path, kind):
    config = tmp_path / 'etc/initramfs-tools/update-initramfs.conf'
    config.parent.mkdir(parents=True)
    target = tmp_path / 'original'
    target.write_text('update_initramfs=yes\n')
    if kind == 'symlink':
        config.symlink_to(target)
    elif kind == 'file':
        config.write_text(target.read_text())
        config.chmod(0o640)
    with pytest.raises(ValueError):
        with preset.preserve_live_initramfs(tmp_path):
            assert config.read_text() == 'update_initramfs=no\n'
            raise ValueError('transaction failed')
    if kind == 'absent':
        assert not config.exists()
    else:
        assert config.read_text() == target.read_text()
        assert config.is_symlink() == (kind == 'symlink')
        if kind == 'file':
            assert config.stat().st_mode & 0o777 == 0o640
