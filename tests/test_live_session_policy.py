import subprocess
from pathlib import Path
from core.live_session_policy import SCRIPT, prepare_live_session, bootstrap_policy
from core.installed_appearance import appearance_only


def test_installed_boot_does_not_change_settings(tmp_path):
    cmdline = tmp_path/'cmdline'; cmdline.write_text('root=UUID=abc quiet splash')
    script = SCRIPT.replace('/proc/cmdline', str(cmdline))
    result = subprocess.run(['/bin/sh', '-c', script], env={'PATH': '/usr/bin:/bin'}, capture_output=True)
    assert result.returncode == 0 and result.stderr == b''


def test_live_boot_sets_policy_and_holds_inhibitor(tmp_path):
    cmdline = tmp_path/'cmdline'; cmdline.write_text('quiet boot=live splash')
    log = tmp_path/'calls'
    for name, text in {
        'gsettings': f'#!/bin/sh\necho "$@" >> "{log}"\n[ "$1" != writable ] || echo true\n',
        'gnome-session-inhibit': f'#!/bin/sh\necho "$@" >> "{log}"\n',
    }.items():
        p = tmp_path/name; p.write_text(text); p.chmod(0o755)
    result = subprocess.run(['/bin/sh', '-c', SCRIPT.replace('/proc/cmdline', str(cmdline))], env={'PATH':str(tmp_path)+':/usr/bin:/bin'})
    assert result.returncode == 0
    calls = log.read_text()
    assert 'set org.gnome.desktop.screensaver lock-enabled false' in calls
    assert '--inhibit=idle --inhibit=suspend --inhibit-only' in calls
    prepare_live_session(tmp_path/'root')
    assert 'boot=live' in (tmp_path/'root/usr/lib/pelandukos/live-session-policy').read_text()
    assert 'boot=live' in bootstrap_policy()


def test_installed_appearance_excludes_live_lock_policy():
    result = appearance_only('[org/gnome/desktop/screensaver]\nlock-enabled=false\npicture-uri=\'file:///wallpaper\'\n[org/gnome/desktop/session]\nidle-delay=0\n[org/gnome/desktop/lockdown]\ndisable-lock-screen=true\n')
    assert 'lock-enabled' not in result['org/gnome/desktop/screensaver']
    assert result['org/gnome/desktop/screensaver']['picture-uri']
    assert not result.has_section('org/gnome/desktop/lockdown')
