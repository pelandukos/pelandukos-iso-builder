# Live rootfs editing and package operations

The Desktop page can start an extracted project before any ISO build. The
separate Test ISO page still tests an existing/built ISO and discards VM changes.
These are different workflows.

## Runtime

The helper creates a private bootstrap from a trusted host kernel, matching host
modules and static BusyBox. QEMU exports only the project rootfs over virtio-9p,
preserving numeric file ownership. The bootstrap switches root into the target
init system. Applications, package scripts and desktop compositors execute under
the guest kernel, never under a host chroot. Native and ext4-container projects
use the same workflow. exFAT itself never stores the extracted Linux tree.

QEMU has no host disk, home-directory share, X11 socket, Wayland socket, clipboard
or arbitrary QMP endpoint available to the GUI. The helper restricts controls to
validated input events, graceful shutdown and force stop. Screenshots are returned
as bounded framebuffer messages. The project lock is held for the complete session.
Guest network access is an explicit option; QEMU NAT allows Internet/local-network
connections and is not an outbound destination filter.

Runtime packages: `qemu-system-x86 busybox-static kmod file zstd`. A readable
`/boot/vmlinuz-*` with matching `/usr/lib/modules/<version>` is also needed.
Install missing host dependencies only when you choose to:

```bash
sudo apt install qemu-system-x86 busybox-static kmod file zstd
```

Debian live-config is requested through `boot=live` with the explicit components
`hostname,hosts,user-setup,sudo,gdm3,lightdm,sddm`. This is a minimal editor login
setup: distribution-specific ISO startup hooks are skipped for this session.
For example, the inspected PelandukOS `0040-pelandukos` branding hook waits on
early-session `gsettings`/D-Bus before the normal desktop services start.
The original scripts remain unchanged and will run when the built ISO boots.
Target configuration determines accounts and login behavior. Existing display-manager settings
are respected. Selecting X11/Wayland, where offered, happens in the target login
screen; the builder does not install a missing session automatically.

## Persistence and limits

- Interactive sessions write directly to the project. They do not provide an
  automatic undo snapshot. User accounts, files, caches and logs also persist.
- Personal desktop settings are not automatically converted into `/etc/skel` or
  system defaults. Use Desktop defaults for release-wide appearance.
- No target kernel is replaced. This host-kernel session cannot establish the
  rebuilt ISO's own kernel, initramfs, installer, BIOS, UEFI or USB compatibility.
- Kernel/initramfs/module changes made interactively cause the build guard to
  reject the project until the original artifacts are restored.
- Use the desktop's Shutdown action or **Shut down & save**. If shutdown does not
  complete within 120 seconds, the VM is forcibly stopped; the project history
  records this. Forced stop can leave partial application/package writes.
- Live Desktop defaults to 16 GiB RAM, four vCPUs, 1920×1080 and a 1 GiB VirtIO GPU
  resource limit. EGL/VirGL hardware acceleration is probed before boot, with
  software rendering as a fallback. This does not allocate dedicated physical VRAM.
- Restart reboots the VM without ending the editing session. The kernel command
  line enables the target Plymouth splash, including with serial diagnostics.
- No clipboard, audio or international keymap protocol is implemented.
  The framebuffer viewer has basic US-keyboard and mouse input.
- Read-only live qualification uses an in-guest RAM overlay. This internal test
  mode does not change the user's project and is separate from persistent editing.

## APT

The Packages page lists installed packages directly from bounded target metadata.
Repository search, details, dependency simulation and audit run in an isolated
read-only target VM. Refresh/install/remove/selected-upgrade create a full rootfs
copy first. Maintainer scripts execute inside the VM; a temporary guest
`policy-rc.d` prevents ordinary service startup during package transactions.
The prior DNS and policy files are restored before commit.

An atomic Linux `renameat2(RENAME_EXCHANGE)` commits a successful, audited result.
The prior rootfs remains in `snapshots/package-rootfs`. The transaction record
stores the original root inode so interrupted copies and committed rollback
snapshots can be distinguished after restart. Restoring swaps the two roots,
retaining the replaced filesystem as the alternate snapshot.

Only one package rollback copy is retained. Discard it explicitly before starting
the next transaction. Reflinks are attempted where available; otherwise a full
copy and its disk-space requirement are unavoidable. Package scripts that depend
on unsupported hardware/services can fail; a failed transaction keeps the original.
No host package database or host repository file is used.

Repository management currently uses the target File Manager for
`etc/apt/sources.list` and `etc/apt/sources.list.d/`. There is no repository/key
wizard, whole-distribution upgrade workflow or automatic kernel-to-ISO synchronization.

Live Desktop hardware can be changed in **Desktop appearance**: RAM (1–64 GiB),
virtual CPUs (1–64), preferred resolution (up to 2560×1440), graphics resource
limit (128–4096 MiB), and automatic/software/required accelerated 3D mode.
Settings are saved on this PC and apply when starting the next session; choose
resources that leave sufficient RAM and CPU capacity for the host. Required 3D
mode reports an error if unavailable; automatic mode falls back to software.
These controls configure the editing VM, not the hardware of the installed OS.

Accelerated sessions read RGB frames through QEMU’s private Unix RFB socket.
QMP still carries keyboard, mouse and power commands. This avoids a stalled
QMP screendump after the guest switches from BIOS VGA to a VirGL desktop.
The socket lives inside the private VM runtime directory; no TCP VNC port is opened.
Frames are bounded to four megapixels.

The viewer maps absolute tablet coordinates using the displayed image’s logical
size, including HiDPI scaling and letterboxing. Pointer motion is coalesced at
16 ms; clicks carry position and button in one QMP request. Updated viewers
negotiate lossless zlib-compressed PPM regions at a 100 ms polling interval;
only the bounding region of changed pixels is sent after the initial frame;
older viewers retain the uncompressed protocol. Actual frame rate depends on
rendering and transport time.

The temporary bootstrap preloads the host kernel’s matching `autofs4` module
before starting target userspace. This supplies systemd automount support even
when the imported ISO contains modules for a different kernel.
