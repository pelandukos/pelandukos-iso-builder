"""Trusted preset worker; executed only inside the package transaction VM.

Keep this module standalone (standard library only) so the installed helper can
send its source to the guest without exposing any host directory.
"""
from contextlib import contextmanager
import json
import os
import re
import subprocess
import sys
from pathlib import Path

PRESETS = {'minimal-gnome': 'Minimal GNOME + PelandukOS apps',
           'pelandukos-apps': 'Update PelandukOS Settings, Software and installer'}
CORE = ['gnome-control-center', 'gnome-control-center-data', 'pelandukos-software', 'pelandukos-installer']
RUNTIMES = ['snapd', 'flatpak', 'libfuse2t64', 'fuse3', 'apparmor']
COMPIZ = ['compiz', 'compiz-core', 'compiz-gnome', 'compiz-plugins',
          'compiz-plugins-main', 'compiz-plugins-extra', 'compizconfig-settings-manager']
KEEP = set('''gnome-shell gnome-session gnome-session-bin gnome-settings-daemon
 gnome-control-center gnome-control-center-data gdm3 nautilus gnome-terminal
 gnome-text-editor gnome-disk-utility gnome-system-monitor file-roller
 network-manager network-manager-gnome wpasupplicant wireless-regdb bluez
 pipewire pipewire-pulse wireplumber gnome-keyring gnome-shell-extensions
 sudo locales initramfs-tools live-boot live-config live-config-systemd
 live-boot-initramfs-tools live-tools systemd systemd-sysv apt dpkg dbus polkitd
 xserver-xorg xwayland xdg-desktop-portal-gnome xdg-desktop-portal-gtk gvfs
 gvfs-backends udisks2 upower fonts-noto-core fonts-noto-cjk fonts-dejavu-core
 ca-certificates curl wget python3 cryptsetup e2fsprogs dosfstools btrfs-progs
 rsync compiz-core libcompizconfig0 libcanberra-pulse glib-networking'''.split()) | set(CORE + RUNTIMES + COMPIZ)
OPTIONAL = set('''appimagelauncher baobab blender cheese evolution firefox-esr chromium
 gimp gnome-calculator gnome-calendar gnome-characters gnome-clocks
 gnome-connections gnome-contacts gnome-font-viewer gnome-logs gnome-maps
 gnome-remote-desktop gnome-shell-extension-manager gnome-snapshot
 gnome-sound-recorder gnome-tour gnome-tweaks gnome-user-docs gnome-user-share
 gnome-weather gnome-software gnome-software-common grub-customizer handbrake
 inkscape libreoffice libreoffice-common loupe marco marco-common mate-themes
 mlterm obs-studio papers ptyxis rhythmbox rygel seahorse shotwell showtime
 simple-scan software-properties-gtk steam-launcher steam-installer
 synaptic thunderbird totem transmission-gtk vlc yelp filezilla
 fonts-noto-cjk-extra fonts-noto-extra fonts-noto-ui-extra
 build-essential gcc g++ make cmake meson ninja-build debhelper dh-python
 dpkg-dev autoconf automake libtool'''.split())
PROTECTED_PREFIXES = ('linux-', 'firmware-', 'grub-', 'shim-', 'pelandukos-', 'pelanduk-',
                      'compiz', 'libcompiz', 'python3-compiz')
APT = ['apt-get', '-o', 'Dpkg::Use-Pty=0',
       '-o', 'Dpkg::Options::=--force-confold', '--no-install-recommends']
CLEANUP = APT + ['-o', 'APT::Solver=internal']


def run(args):
    result = subprocess.run(args, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            env=dict(os.environ, SYSTEMD_OFFLINE='1'))
    print(result.stdout, end='', flush=True)
    if result.returncode:
        raise RuntimeError('Command failed: ' + args[0])
    return result.stdout


def inventory():
    text = subprocess.check_output(['dpkg-query', '-W',
        '-f=${Package}\t${db:Status-Status}\t${Essential}\t${Priority}\t${Version}\t${Section}\n'], text=True)
    return {row[0]: row for line in text.splitlines() if len(row := line.split('\t')) == 6
            and row[1] == 'installed'}


def library_dependencies(packages):
    """Reclassify libraries, never applications or dynamically discovered plugins."""
    keep = protected(packages)
    return sorted(name for name, row in packages.items()
                  if name not in keep and name.startswith('lib') and len(row) > 5
                  and row[5].split('/')[-1] in ('libs', 'libdevel', 'oldlibs')
                  and '-plugin' not in name and not name.startswith('libnss-'))


def configure_runtimes():
    run(['flatpak', 'remote-add', '--system', '--if-not-exists', 'flathub',
         'https://dl.flathub.org/repo/flathub.flatpakrepo'])
    run(['systemctl', 'enable', 'snapd.socket', 'snapd.apparmor.service'])
    link = Path('/snap')
    if not link.exists() and not link.is_symlink():
        link.symlink_to('var/lib/snapd/snap')


def protected(packages):
    return {name for name, row in packages.items()
            if name in KEEP or name.startswith(PROTECTED_PREFIXES)
            or row[2] == 'yes' or row[3] in ('required', 'important')
            or name.startswith('gnome-shell-extension-') and name != 'gnome-shell-extension-manager'}


def check_removal(plan, keep):
    if re.search(r'^Inst ', plan, re.M):
        raise RuntimeError('Cleanup would install other packages; stopped.')
    removed = {n.split(':')[0] for n in re.findall(r'^(?:Remv|Purg)\s+(\S+)', plan, re.M)}
    if blocked := removed & keep:
        raise RuntimeError('Cleanup would remove protected packages: ' + ', '.join(sorted(blocked)))
    return sorted(removed)


def require_candidates():
    chosen = []
    for name in CORE:
        policy = run(['apt-cache', 'policy', name])
        match = re.search(r'^\s*Candidate:\s+(\S+)', policy, re.M)
        version = match[1] if match else '(none)'
        if version == '(none)':
            raise RuntimeError('Missing repository package: ' + name)
        if name != 'pelandukos-installer' and '+pelandukos' not in version:
            raise RuntimeError('Repository candidate is not the PelandukOS build: ' + name)
        if name == 'pelandukos-installer':
            if subprocess.call(['dpkg', '--compare-versions', version, 'ge', '3.1.3']):
                raise RuntimeError('Installer 3.1.3 or newer is required for Desktop/Server choices.')
        chosen.append(name + '=' + version)
    return chosen


@contextmanager
def preserve_live_initramfs(root=Path('/')):
    """Keep the imported live boot pair intact during package triggers."""
    config = root / 'etc/initramfs-tools/update-initramfs.conf'
    saved = config.with_name(config.name + '.pelandukos-builder-backup')
    if saved.exists() or saved.is_symlink():
        raise RuntimeError('An initramfs configuration backup already exists.')
    existed = config.exists() or config.is_symlink()
    config.parent.mkdir(parents=True, exist_ok=True)
    if existed:
        config.rename(saved)
    try:
        config.write_text('update_initramfs=no\n')
        yield
    finally:
        config.unlink(missing_ok=True)
        if existed:
            saved.rename(config)


def main(preset, apply=False):
    if preset not in PRESETS:
        raise ValueError('Unknown PelandukOS preset')
    if not re.search(r'^ID=[\"\']?pelandukos[\"\']?$', Path('/etc/os-release').read_text(), re.M):
        raise RuntimeError('These presets require a PelandukOS target image.')
    packages = inventory()
    if preset == 'minimal-gnome' and not {'gnome-shell', 'gdm3', 'gnome-session'} <= packages.keys():
        raise RuntimeError('Minimal GNOME requires an existing GNOME live image.')
    if apply:
        run(['apt-get', 'update', '--error-on=any', '-o', 'Acquire::Retries=2',
             '-o', 'Acquire::http::Timeout=30', '-o', 'Acquire::https::Timeout=30'])
    else:
        print('Preview uses cached repository metadata; apply refreshes and rechecks it.')
    candidates = require_candidates()
    candidates += RUNTIMES + COMPIZ
    if preset == 'minimal-gnome':
        candidates += [name + '=' + packages[name][4] if name in packages else name
                       for name in ('nautilus', 'gnome-terminal', 'gnome-text-editor', 'dconf-cli')]
    run(APT + (['-y'] if apply else ['--simulate']) + ['--no-remove', 'install', *candidates])
    if apply:
        # This file is part of the installer providing the profile page and jobs.
        catalog = Path('/usr/share/pelandukos-installer/software-catalog.json')
        if not catalog.is_file():
            raise RuntimeError('Installer software catalog is missing; target not committed.')
        configure_runtimes()
    if preset == 'minimal-gnome':
        packages = inventory()
        keep = protected(packages)
        remove = sorted((OPTIONAL & packages.keys()) - keep)
        if apply:
            run(['apt-mark', 'manual', *sorted(keep)])
            libraries = library_dependencies(packages)
            if libraries:
                run(['apt-mark', 'auto', *libraries])
        if remove:
            plan = run(CLEANUP + ['--simulate', '--no-upgrade', 'purge', *remove])
            check_removal(plan, keep)
            if apply:
                run(CLEANUP + ['-y', '--no-upgrade', 'purge', *remove])
        if apply:
            plan = run(CLEANUP + ['--simulate', '--no-upgrade', 'autoremove', '--purge'])
            check_removal(plan, keep)
            run(CLEANUP + ['-y', '--no-upgrade', 'autoremove', '--purge'])
            run(['flatpak', 'uninstall', '--system', '--unused', '--noninteractive'])
            favorites = "['org.gnome.Nautilus.desktop', 'org.gnome.Terminal.desktop', " \
                        "'org.gnome.TextEditor.desktop', 'pelandukos-software.desktop', " \
                        "'org.gnome.Settings.desktop', 'com.pelandukos.Installer.desktop']"
            defaults = Path('/etc/dconf/db/local.d/91-pelandukos-software')
            defaults.parent.mkdir(parents=True, exist_ok=True)
            defaults.write_text('[org/gnome/shell]\nfavorite-apps=' + favorites + '\n')
            run(['dconf', 'update'])
        else:
            print('Unused dependencies will be checked after removal; protected packages cannot be removed.')
    if apply:
        run(['apt-mark', 'manual', *CORE, *RUNTIMES, *COMPIZ])
        run(['apt-get', 'check'])
        if run(['dpkg', '--audit']).strip():
            raise RuntimeError('Package audit failed.')
        actual = inventory()
        for name in CORE + RUNTIMES + COMPIZ:
            if name not in actual:
                raise RuntimeError('Required package missing after preset: ' + name)
        run(['apt-get', 'clean'])
        report = Path('/var/lib/pelandukos/iso-builder-preset.json')
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(json.dumps({'preset': preset, 'packages': {n: actual[n][4] for n in CORE + RUNTIMES + COMPIZ}}, indent=2)+'\n')
    print('PelandukOS preset ' + ('applied' if apply else 'preview complete'))


if __name__ == '__main__':
    if sys.argv[2] == 'apply':
        with preserve_live_initramfs():
            main(sys.argv[1], True)
    else:
        main(sys.argv[1], False)
