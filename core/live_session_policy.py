"""Session inhibition is restricted to a boot=live kernel command line."""
from core.util import safe_path

SCRIPT = """#!/bin/sh
case " $(cat /proc/cmdline) " in
  *" boot=live "*) ;;
  *) exit 0 ;;
esac
set_key() {
    gsettings writable "$1" "$2" 2>/dev/null | grep -qx true &&
        gsettings set "$1" "$2" "$3" 2>/dev/null || :
}
set_key org.gnome.desktop.screensaver lock-enabled false
set_key org.gnome.desktop.lockdown disable-lock-screen true
set_key org.gnome.desktop.session idle-delay 0
set_key org.gnome.settings-daemon.plugins.power sleep-inactive-ac-type nothing
set_key org.gnome.settings-daemon.plugins.power sleep-inactive-battery-type nothing
set_key org.gnome.settings-daemon.plugins.power idle-dim false
exec gnome-session-inhibit --app-id=pelandukos-live-session --reason='Keep the live installer awake' --inhibit=idle --inhibit=suspend --inhibit-only
"""
DESKTOP = """[Desktop Entry]
Type=Application
Name=PelandukOS Live Session
Exec=/usr/lib/pelandukos/live-session-policy
OnlyShowIn=GNOME;
NoDisplay=true
X-GNOME-Autostart-enabled=true
"""
FILES = {'usr/lib/pelandukos/live-session-policy': (SCRIPT, 0o755),
         'etc/xdg/autostart/pelandukos-live-session.desktop': (DESKTOP, 0o644)}


def prepare_live_session(root):
    for relative, (content, mode) in FILES.items():
        path = safe_path(root, relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content); path.chmod(mode)
    return {'scope': 'boot=live', 'automatic_lock': False, 'idle_suspend': False}


def bootstrap_policy():
    # Paths are fixed trusted constants, executed inside the VM after its overlay.
    result = ''
    for relative, (content, mode) in FILES.items():
        parent = str(safe_path('/target', relative).parent)
        result += f"mkdir -p {parent}\ncat > /target/{relative} <<'PELANDUK_LIVE_POLICY'\n{content}PELANDUK_LIVE_POLICY\nchmod {mode:o} /target/{relative}\n"
    return result
