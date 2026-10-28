# PelandukOS ISO Builder

**Developer: Tunnelbiz Studio Sdn Bhd**

A working, experimental desktop application for importing, customizing and
rebuilding a narrowly detected Debian Live ISO layout. Python 3.11+, PySide6/Qt 6,
Linux amd64. The GUI runs as your desktop user. A separate Polkit helper preserves
Linux metadata inside private project storage.

**This is not a production-qualified Cubic replacement yet.** Synthetic ISO
round trips are tested; a real Debian Live/PelandukOS desktop and installer ISO
must still pass the acceptance checklist. See [DEVELOPMENT_STATUS.md](DEVELOPMENT_STATUS.md)
for the exact boundary between working, experimental and absent features.

## Quick start

Build the Debian package without root:

```bash
python3 scripts/build_deb.py
```

Install it only when you choose to modify the host:

```bash
sudo apt install ./dist/pelandukos-iso-builder_0.2.3_amd64.deb
pelandukos-iso-builder
```

On Debian 13, the package recommends the distribution's PySide6 modules. Debian
12 users need the user-local PySide6 virtual environment described in
[installation instructions](docs/INSTALL.md). The package never installs Python
wheels or modifies repositories automatically.

Development launch:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python main.py --check-dependencies
.venv/bin/python main.py
```

Install the package once to supply the root-owned helper and Polkit policy.
Running from source alone allows GUI and dependency inspection, but does not
grant filesystem privileges. Rebuild/reinstall the package after changing helper
code; the development GUI talks to the **installed** helper.

On exFAT, put the virtual environment on a Linux filesystem instead of `.venv`.
Source code and exported ISOs may live on exFAT. Extracted root filesystems must
live on a filesystem supporting Linux modes, ownership, symlinks and xattrs.

## Workflow

The sidebar follows seven steps, with **Back / Next** navigation. The builder uses
your PC's Qt system palette and fonts, including light/dark palette changes.
Activity logs are collapsible; the progress bar stays visible.

1. **Source ISO**: create or reopen a project, enter a release version, then import
   an existing ISO. The project summary displays the detected distribution,
   desktop and boot support. Import unlocks desktop editing and building.
2. **Desktop**: detect available settings, choose a wallpaper or installed themes,
   and click **Apply desktop settings**. Confirm the listed changes.
   **Open live desktop · no ISO needed** boots the extracted filesystem directly
   in a VM. Changes persist in the project. Shut down the session before building.
3. **Packages**: list installed packages, search the target APT cache, inspect
   dependencies, review install/remove/selected-upgrade plans and apply them.
   Refresh metadata after changing target repositories. APT runs only in a VM.
4. **Files · optional**: select **Go** to browse `/`. Double-click directories or
   UTF-8 files. Save text edits before building. Copy/import/move/delete and
   numeric ownership/mode changes operate only in the target root filesystem.
5. **Release**: set the distribution label, version and notes, then save.
   These labels do not perform a Debian upgrade or edit `/etc/os-release`.
6. **Build & export**: check **Release version** and the output filename, then build.
   The suggested filename follows your version until you enter a custom filename.
   **Export last build** saves the ISO, SHA256 file, release JSON and build log.
   Existing host files are never overwritten.
7. **Test ISO**: boot the result separately under BIOS and UEFI if detected.
   **Build current project and preview** builds and exports a temporary ISO.

Reopen using **Source ISO → Saved projects** or a project reference file.
Completed operations are persisted. Text requires **Save file**; desktop choices
require **Apply desktop settings**. A project reference JSON contains metadata and the
project ID; it is **not** a portable archive of the large project trees.

### Live editing before building an ISO

Open **Desktop → Open live desktop · no ISO needed** after extraction.
The builder starts QEMU with a trusted host kernel and an initramfs that mounts
only the target rootfs over virtio-9p. The desktop runs inside the guest, including
its X11 or Wayland compositor. No host X11/Wayland sockets, clipboard, home or
desktop D-Bus session are shared. Input and screenshots pass through a restricted
helper protocol; the GUI remains unprivileged.

Live Desktop defaults to 16 GiB RAM, four virtual CPUs and a VirtIO VGA display with
a preferred resolution of 1920×1080 and a 1 GiB graphics resource limit.
It probes EGL/VirGL for accelerated 3D and falls back to software VirtIO graphics
when unavailable. This is a virtual GPU, not PCI GPU passthrough or dedicated VRAM.
Restart inside the desktop reboots the same VM and retains project edits;
use Shut down & save when you want to finish editing.

This is experimental and depends on the target init system, display manager and
available login/session configuration. The session uses a host kernel, so it
does **not** validate the ISO's own kernel or BIOS/UEFI boot chain. Personal
preferences remain in the target user's home; they do not automatically become
defaults for every new user. Use the offline Desktop editor for those defaults.
User files, logs, accounts and package changes in this VM are real project edits.
There is no automatic rollback for an interactive session. Use **Shut down & save**
before building; force stop can leave incomplete edits. See [live editing](docs/LIVE_EDIT.md).

The runtime requires `qemu-system-x86`, `busybox-static`, `kmod`, `file`, a readable
host `/boot/vmlinuz-*` and its matching kernel modules; `zstd` is needed for
zstd-compressed modules. Settings reports missing capabilities. No host dependencies
are installed automatically. KVM is used when accessible, otherwise software
emulation is available.

### Package transactions

APT search/details/simulation run against the target read-only. Changes run
against a complete metadata-preserving rootfs copy. A successful operation,
dependency audit and unchanged kernel inventory are required before an atomic
rootfs exchange. The previous filesystem stays available through **Restore snapshot**.
**Discard snapshot** removes that rollback copy and permits another transaction.
An interrupted, uncommitted copy can only be discarded. Required free space is
approximately another uncompressed rootfs; exFAT container capacity must cover it.

Package names are explicit; arbitrary command strings and major distribution
upgrades are not accepted. Repository files can be edited in Files. Guest network
access is explicit and includes the local network through QEMU NAT.

### Desktop customization

The editor detects installed target packages and available configuration support:

| Target desktop | Enabled settings |
| --- | --- |
| GNOME | PNG/JPEG wallpaper, GTK theme, icons, cursor, default font where schemas provide those keys |
| KDE Plasma | Icons, cursor, wallpaper where the Plasma desktop shell package is present |
| XFCE | GTK theme, icons, cursor and default font |

Only themes already present in the target are offered. GNOME compilation requires
`glib-compile-schemas` from `libglib2.0-bin` on the builder PC. No packages are
installed automatically. XFCE wallpaper, login backgrounds, theme installation,
startup applications and desktop shortcuts remain unimplemented.

Desktop detection streams the target package database separately from the file
editor, so databases larger than 2 MiB are supported without loading the entire
file into memory. Metadata is bounded at 64 MiB with a 64 KiB per-line limit.

Settings are defaults inside the ISO. Existing user preferences, dconf policies
or distribution startup scripts can take precedence. A wallpaper thumbnail is a
visual preview; verify the rebuilt desktop in **Test ISO**. See
[desktop mechanisms and limitations](docs/DESKTOP_CUSTOMIZATION.md).

### Job progress

The bottom status bar shows the current stage, its measured percentage and
elapsed job time. File copying, container allocation and checksumming also show
processed/total GiB. Formatting reports inode-table progress; extraction,
compression and ISO writing use tool-reported percentages where available.
Older squashfs-tools versions without percentage output remain supported.

Percentages apply to **the current stage**, not the whole job; they restart when
the next stage begins. Disk flushes and checks without measurable progress show
an animated bar and “Percentage unavailable for this stage”. No completion time
is guessed. “100% — complete” appears only after the helper finishes successfully.
After updating the application, finish any running job before reopening the GUI
to use the new progress display.

### Choose project storage

In **New project**, use **Project storage folder → Browse…** to select a drive
with enough free space. The dialog displays capacity and filesystem type.
**Settings → Default storage folder for new projects** saves your preference.
To relocate an existing project, open it and select **Source ISO → Move storage…**.
The helper copies and verifies the entire project before switching
its reference and removing the old copy. Failed copies retain the original.

Choose ext4, XFS, Btrfs **or exFAT**. For exFAT, the app creates an ext4 container
file in the chosen folder; the external drive is not reformatted. Choose its
capacity in the dialog (default **128 GiB**, reserved up front). Existing
containers in that folder are reused. Creating a large container can take several
minutes, and it cannot safely be cancelled. The entire project, including its
extracted Linux filesystem, lives inside that container.

FAT/NTFS remain unsupported as working storage; input/output ISO files may
remain on those drives. tmpfs is accepted for temporary testing only and its
projects disappear after reboot. The GUI remains unprivileged. Project folders
are private and accessed through the application's file manager.

Container capacity is currently fixed. Empty containers remain allocated for
reuse; automatic resizing/removal is not implemented. Keep their `.ext4` files
at the registered path and use **Move current project…** to relocate projects.
Back up containers while the application is idle. Container support is
experimental; see [storage validation](docs/TEST_RESULTS.md).

## Supported candidate layout

- Debian or PelandukOS `ID` in `/etc/os-release`.
- Exactly one `/live/filesystem.squashfs`.
- amd64 dpkg package database; multiarch targets are currently rejected.
- Recognized GRUB or ISOLINUX/SYSLINUX tree, static kernel/initramfs references,
  and xorriso-replayable El Torito boot data.
- Source BIOS/UEFI capability and MBR/GPT presence must survive rebuild.
- The ISOLINUX MBR/GPT/APM hybrid is supported when all three partition maps
  identify the same EFI boot image and xorriso provides the corresponding replay
  settings. Rebuild checks retain the APM mapping and EFI payload bytes.
- Original kernel and modules remain unchanged. At build time, the selected
  Plymouth theme and daemon settings from the project are appended to each live
  initramfs as a data archive. This updates the ISO bootsplash while preserving
  early microcode, drivers and live-boot scripts. The required Plymouth module
  must already exist in the source initramfs. Imported programs are never run.

Ubuntu/casper, Mint, layered live filesystems, other architectures, other APM layouts and
unrecognized boot configurations are rejected. Secure Boot, persistence,
installer behavior, custom signed-image verification, and hybrid USB boot are
**not qualified** by the current tests. Boot success in a CD-ROM VM does not
prove those behaviors.

## Tests

```bash
.venv/bin/python -m pip install pytest
.venv/bin/python -m pytest -q
```

The integration suite uses actual xorriso, SquashFS and bubblewrap commands on
small generated ISO fixtures. It skips optional integrations if tools are
missing. Normal pytest workspaces are temporary; no host mounts, package installs or
host service changes are performed. Target VM tests also boot a tiny generated
rootfs without an ISO, save an edit, and receive a real framebuffer.
The optional root-only `test_real_apt_install_and_remove_offline` test creates a
local `.deb` repository and exercises APT entirely inside the guest.

The separate opt-in `scripts/test_container_storage.py` exercises real ext4
containers through Polkit in a private mount namespace. It creates and removes
only its own small fixture container, verifies numeric ownership, import/build/
export/reopen and bidirectional relocation, and checks mount/loop cleanup.

Optional BIOS/UEFI synthetic boot tests require a readable x86_64 kernel, static
`/usr/bin/busybox`, ISOLINUX/SYSLINUX, GRUB EFI tools, QEMU and OVMF:

```bash
PELANDUKOS_BOOT_KERNEL=/path/to/readable/vmlinuz \
  .venv/bin/python -m pytest -q tests/test_boot_smoke.py
```

These tests boot a tiny test initramfs and require a guest serial success marker.
They do not boot a Debian desktop or installer. See [validation](docs/VALIDATION.md)
for real ISO qualification and automated serial-marker testing.

## Architecture and security

- `gui/`: unprivileged PySide6 pages and framebuffer console.
- `core/`: manifests, filesystem operations, inspection, extraction, validation,
  SquashFS and ISO builds.
- `adapters/`: explicit Debian Live layout rules.
- `services/`: allowlisted helper requests, sandboxed subprocesses, Qt jobs, QEMU.
- `packaging/`, `scripts/`: desktop integration, Polkit policy and package builder.
- `tests/`: unit, real-tool integration and opt-in VM smoke tests.

By default projects live at `/var/lib/pelandukos-iso-builder/<uid>/<project-id>/`.
Native selected storage uses `<chosen-folder>/PelandukOS-Projects-<uid>-<token>/<project-id>/`.
exFAT uses `<chosen-folder>/PelandukOS-<id>.ext4`, containing the project trees.
A root-owned registry under the default store remembers both kinds.
Native stores are root-owned and mode 0700; exFAT contents are accessed through
a confined worker because exFAT cannot enforce those protections.
Original input is copied and checksummed;
the user-supplied ISO is never opened for writing. The root helper exposes no
shell, arbitrary package-command, arbitrary mount or host-execution endpoint.

Read [security model](docs/SECURITY.md), [rebuild details](docs/ISO_REBUILD.md),
[troubleshooting](docs/TROUBLESHOOTING.md), and [installation](docs/INSTALL.md).

GPL-3.0-or-later. See [LICENSE](LICENSE) and [COPYING](COPYING).

Live Desktop hardware can be changed in **Desktop appearance**: RAM (1–64 GiB),
virtual CPUs (1–64), preferred resolution (up to 2560×1440), graphics resource
limit (128–4096 MiB), and automatic/software/required accelerated 3D mode.
Settings are saved on this PC and apply when starting the next session; choose
resources that leave sufficient RAM and CPU capacity for the host. Required 3D
mode reports an error if unavailable; automatic mode falls back to software.
These controls configure the editing VM, not the hardware of the installed OS.

### Saved bootsplash and GRUB appearance

Open **Build ISO → Bootsplash & GRUB…**. Settings are stored inside the project
at `/etc/pelandukos-iso-builder/boot-appearance.json` and survive reopening,
container moves and reboot. Asset paths refer to the imported filesystem;
use File Manager to import a PNG logo or a theme directory first.

- **Use current project theme** copies the selected Plymouth theme at build time.
- **PelandukOS colours and logo** generates the configured background, text,
  progress bar and logo. Plymouth assets are saved in the root filesystem as
  well as the live initramfs so installation retains the appearance.
- **Custom GRUB theme directory** copies `theme.txt` and its local assets using
  the supported GRUB theme hook. It preserves boot entries and the BIOS menu.
- **Keep source ISO menu** preserves the source menu appearance.

Builds also clean the recognized legacy PelandukOS GRUB installer aliases:
one main Live/install entry, fail-safe mode, speech synthesis and Utilities
remain. The obsolete graphical/text/expert installer menus all booted the same
Live payload. Custom menus and genuine Debian installer configurations are
preserved. Cleanup is staged for the output ISO without editing the source.

Invalid colours, missing assets, symlinks and escaping paths are rejected.
Existing Plymouth files are backed up under `/var/backups/pelandukos-boot-appearance`.
Kernel and module protection remains enabled. A known legacy PelandukOS
live-config theme hook is backed up and retired when customized desktop
defaults are present, so it cannot overwrite the saved wallpaper or extensions.
Unrecognized hooks are left intact.

References: [GRUB theme format](https://www.gnu.org/software/grub/manual/grub/html_node/Theme-file-format.html),
[Linux initramfs format](https://www.kernel.org/doc/html/latest/driver-api/early-userspace/buffer-format.html).

## PelandukOS presets (0.2.3)

In **Packages**, select a preset and click **Preview preset**. Review the package
plan, then apply. Network access is required. The existing package VM and full
rollback snapshot protect the original project until the transaction succeeds.

- **Minimal GNOME + PelandukOS apps** keeps the GNOME session, Files, Terminal,
  settings and basic utilities, PelandukOS Software, installer, networking,
  firmware and boot packages. It removes the curated optional application list
  and unused dependencies; unknown third-party applications are left for manual
  review. Compiz, its plugins and settings manager are retained. Snap, Flatpak and
  AppImage compatibility (FUSE 2 alongside FUSE 3) are installed and protected.
- **Update PelandukOS Settings, Software and installer** refreshes repository
  metadata and installs the current PelandukOS builds without removing apps.

Both presets require PelandukOS repository candidates for Settings and Software
and installer 3.1.0 or newer. They use the target's configured signed repositories;
missing packages or non-PelandukOS Settings candidates stop the transaction.
The installer offers Desktop GNOME or Server CLI/XFCE, with optional applications
downloaded online. PelandukOS Software stays available after installation.

Preview uses cached metadata; apply refreshes it and rechecks every cleanup plan.
Essential packages and kernel/firmware/boot/network components are protected.
Package script failures or kernel changes reject the transaction; use the job
log to diagnose the failing package. No imported program executes on the host.
The report is saved inside the target at
`/var/lib/pelandukos/iso-builder-preset.json`.

Build ISO also removes the known duplicate legacy GRUB aliases automatically,
while preserving custom menu entries and the speech installer.

The 0.2.3 preset reclassifies library packages as automatic dependencies, while
protecting essential packages, application engines, Compiz and dynamically loaded
plugins. APT then removes only unused dependencies. Flatpak removes only unused
system runtimes and keeps installed apps. Flathub is configured and snapd socket
activation is enabled; no Snap apps are preinstalled. Existing user app data is
not deleted. AppImage Pool, when already installed, remains available.

Runtime references: [Snap on Debian](https://snapcraft.io/docs/tutorials/install-the-daemon/debian/),
[Flathub setup](https://flathub.org/en/setup/Debian),
[AppImage FUSE compatibility](https://docs.appimage.org/user-guide/troubleshooting/fuse.html).

Builds now synchronize the edited live GNOME appearance into new accounts, retain GDM theme assets, and carry the live GRUB theme into installed systems. Only appearance settings are exported; live command history and device settings are excluded.
