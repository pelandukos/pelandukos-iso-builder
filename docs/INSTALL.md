# Installation

Target: Linux amd64, Debian 12/13, Python >= 3.11. Desktop authorization requires
a running Polkit agent (normally provided by GNOME, KDE Plasma or XFCE).

## System dependencies

The following commands modify the host and are instructions for the operator;
the builder never runs them automatically:

```bash
sudo apt install python3 python3-venv xorriso squashfs-tools bubblewrap pkexec polkitd \
  qemu-system-x86 ovmf
```

GNOME customization also needs `glib-compile-schemas` from `libglib2.0-bin`.
The app detects this optional dependency and explains how to enable GNOME editing
if it is missing. It never installs the dependency automatically.

Debian 13 provides the GUI bindings:

```bash
sudo apt install python3-pyside6.qtwidgets python3-pyside6.qtnetwork
```

Debian 12: use a user-owned environment for Qt. This also works on Debian 13
when using wheels instead of the distribution modules:

```bash
python3 -m venv "$HOME/.local/share/pelandukos-iso-builder/venv"
"$HOME/.local/share/pelandukos-iso-builder/venv/bin/python" -m pip install 'PySide6>=6.4,<7'
```

Qt's wheel may also require system display libraries such as `libgl1`,
`libegl1`, `libxkbcommon0`, `libxcb-cursor0` and `libxcb-icccm4`. Install missing
libraries reported by the Qt platform plugin diagnostic, or use Debian 13's
packaged bindings.

## Build and install

```bash
python3 scripts/build_deb.py
sudo apt install ./dist/pelandukos-iso-builder_0.2.3_amd64.deb
```

The package installs source, a desktop launcher, SVG icon, a fixed root-owned
helper, and the Polkit action. It has no installation script that downloads
content, edits APT repositories or runs imported images.

The installed launcher first checks system Qt modules, then the user environment
under `$XDG_DATA_HOME/pelandukos-iso-builder/venv` (default `~/.local/share`).

## Development

```bash
python3 -m venv /path/on/linux-filesystem/pelandukos-venv
/path/on/linux-filesystem/pelandukos-venv/bin/python -m pip install -r requirements.txt pytest
/path/on/linux-filesystem/pelandukos-venv/bin/python main.py
```

The GUI must never be launched with sudo. Privileged operations require the
installed helper even in development. Never point Polkit at a writable checkout.
The package installs an independent code copy: rebuild and reinstall to test
backend changes with the GUI.

## Storage

Projects default to `/var/lib/pelandukos-iso-builder/<caller-uid>/<project-id>`.
Choose another folder using **New project → Browse…** or save a default under
**Settings**. The helper creates a private root-owned directory beneath the
selected folder. Use **Source ISO → Move storage…** to relocate an
existing project; changing the default alone does not move anything.
Reserve ample space on ext4, XFS or Btrfs. The extractor estimates uncompressed
space, and the builder checks room for output plus ISO readback. The conservative
checks do not reserve capacity against other applications or prevent a malicious
decompression bomb; use a dedicated quota-limited filesystem for untrusted ISOs.

Project references can be stored on any filesystem; they contain metadata only.
Project relocation preserves and verifies content, numeric ownership, modes,
links and xattrs before removing the original. Backups still require preserving
that metadata and the private storage registry. A portable archive UI is not
implemented. Reconnect external storage at its original mount path to reopen it.

exFAT is supported through an app-created ext4 container, requiring `mkfs.ext4`
from `e2fsprogs`, Linux loop devices and permission to create a private mount
namespace. These operations run in the Polkit helper; the GUI stays unprivileged.
No change to `/etc/fstab` or the external drive's format is made.
Select **New exFAT container capacity** (default 128 GiB) when creating/moving a
project. The capacity is allocated up front, with an additional 1 GiB free-space
margin on the drive. Existing containers are reused without resizing.
Container paths display as `PelandukOS-<id>.ext4::/<project-id>` in the GUI.
The root-owned registry on the system drive is still required to reopen them.

The sandbox requires `--bind-fd` support, available in updated Debian 12
`bubblewrap >= 0.8.0-2+deb12u1` and Debian 13. This pins selected storage before
namespace setup. See the [Debian backport announcement](https://lists.debian.org/debian-changes/2024/08/msg00049.html).

PySide6 package availability is documented by the
[Debian package index](https://packages.debian.org/search?keywords=python3-pyside6.qtwidgets).
