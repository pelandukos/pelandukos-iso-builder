"""Retire the known legacy installer aliases that actually boot PelandukOS Live."""
import hashlib

from core.util import read_text, safe_path


LEGACY_INSTALLERS = {
    'install.cfg': '6f7b1b3341a358ecfbfbeed4b9952d1856790917b0504d95a33ec6cb39709be6',
    'install_start.cfg': 'd2e7ed493ff5248c2aafbecc2f43c782af6f29de9a42852b84e6c8f4bff4cf4a',
}
LEGACY_BLOCK = '''# Installer (if any)
if true; then

source\t/boot/grub/install_start.cfg

submenu 'Advanced install options ...' --hotkey=a {

\tsource /boot/grub/theme.cfg

\tsource\t/boot/grub/install.cfg

}
fi'''


def clean_live_grub_menu(tree, stage, report):
    """Stage a fix only for the recognized legacy menu, preserving custom menus.

    These installer templates point at /live, not a Debian installer payload.
    Keep the distinct speech boot arguments; the main live entry already starts
    the graphical installer. Fingerprints prevent removing real installers or
    user-customized rescue commands based on their display labels alone.
    """
    for name, expected in LEGACY_INSTALLERS.items():
        path = safe_path(tree, 'boot/grub/' + name)
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            return {}
    config = safe_path(tree, 'boot/grub/grub.cfg')
    if not config.is_file():
        return {}
    text = read_text(config)
    if (text.count(LEGACY_BLOCK) != 1 or
            'linux\t/live/vmlinuz boot=live components quiet splash findiso=${iso_path}' not in text or
            'menuentry "Install PelandukOS system (amd64)"' not in text):
        return {}
    start = read_text(safe_path(tree, 'boot/grub/install_start.cfg'))
    speech = start[start.index("menuentry 'PelandukOS installer with speech synthesis'"):].strip()
    cleaned = text.replace(LEGACY_BLOCK, '# Accessible live boot\n' + speech)
    output = stage / 'clean-grub'
    output.mkdir(exist_ok=True)
    destination = output / 'grub.cfg'
    destination.write_text(cleaned)
    report['grub_menu_cleanup'] = 'legacy_live_installer_aliases_removed'
    return {'boot/grub/grub.cfg': destination}
