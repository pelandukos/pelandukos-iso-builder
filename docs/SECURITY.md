# Security boundary

Desktop customization uses allowlisted settings and fixed target paths. It
compiles GNOME schemas with the trusted host compiler inside the tool sandbox,
without launching a target desktop or accessing a host session bus. Wallpaper
inputs are opened with caller permissions and explicitly granted to container
workers. Configuration backups precede writes; handled write failures roll back
completed changes. This is not a crash-atomic snapshot transaction. See
[desktop customization](DESKTOP_CUSTOMIZATION.md) for scope and limits.

The GUI is never root. The installed Polkit helper is root-owned code at a fixed
path, invoked with isolated Python (`-I`) and one bounded JSON request. Caller
identity comes from `PKEXEC_UID`. There is no API for arbitrary commands,
environment variables, arbitrary mounts, adoption of arbitrary storage roots or host package management.

The action requires active-session administrator authentication. Polkit may
cache that authorization (`auth_admin_keep`); all requests still pass path and
ownership validation. The broad action covers private project edits and builds,
not arbitrary root execution.

## Files

- The source ISO is opened using the caller's effective UID/GID, with
  supplementary groups temporarily removed. The helper cannot use its root
  authority to read an arbitrary host input.
- Exports are created as the caller using exclusive creation and no-follow
  flags. They never replace existing host files. On a failed export a partial
  caller-owned file may remain; inspect/remove it explicitly before retrying.
- Private roots and manifest paths come only from validated IDs in root-owned,
  mode-0700 storage. Per-project advisory locks serialize operations.
- Selected parents are opened and checked for writability as the caller. The
  helper opens a new caller-owned empty directory, seals it as root-owned 0700,
  and records a private marker in its root-owned registry. Existing arbitrary
  folders are never adopted. Each job verifies and pins its registered root with
  a directory descriptor. Sandbox bind descriptors pin individual inputs/outputs.
  Per-user registry locking serializes routing and relocation.
- Moves copy in the offline sandbox and verify contents, ownership, modes,
  hardlinks, symlinks and xattrs before atomically switching the registry.
  Source cleanup happens after that commit; failed cleanup is reported.
- All editable paths are relative, reject parent traversal and reject symlink
  ancestors. Symlink leaf operations are allowed only for appropriate actions
  such as rename/delete/chown. Symlinks are shown, never followed by the editor.
- Text reads reject devices, sockets, pipes, binary data and files over 2 MiB.
  Text saves are atomic and preserve ownership, mode and supported xattrs.
  Hard-linked text edits are refused to avoid silently breaking the link set.
- Destructive GUI deletion requires confirmation. There is no undo/snapshot UI.

## exFAT containers

exFAT cannot enforce root-only file permissions. Its container contents are
therefore processed by a separate bubblewrap worker. The broker supplies only
the selected project, read-only application/runtime code, caller-opened input
descriptors and exclusively created output descriptors. The host registry,
home directories and host `/etc` are absent. Project/staging directories are
pinned with no-follow directory descriptors before binding. Projects migrated
out of a container continue using this worker.

Only newly created, registered ext4 images are accepted; no API imports an
arbitrary image or chooses a block device. Identity/size checks detect accidental
replacement; they are **not cryptographic authentication**. The helper uses a
private mount namespace, fixed `ext4` type and `nodev,nosuid,noexec` flags.
Loop devices use kernel `LO_FLAGS_AUTOCLEAR`, and normal jobs flush/unmount before
returning. Mount failure cleanup never recursively deletes a mounted tree.
Stored mount options and external journal references in the
[ext4 superblock](https://www.kernel.org/doc/html/latest/filesystems/ext4/super.html)
are rejected before mounting.
The image is advisory-locked while used. Uncooperative external writers can still
corrupt it; do not mount or edit its raw data concurrently.

The host kernel parses ext4 metadata, so container isolation is not protection
against kernel filesystem vulnerabilities. Do not substitute externally supplied
or maliciously modified containers. Independent review and crash/power-loss
qualification remain required before production support.

## Untrusted ISO parsers

xorriso, unsquashfs and mksquashfs run in fresh mount, PID, network, IPC and UTS
namespaces via bubblewrap. Host runtime files under `/usr` are read-only.
Native projects bind named inputs and output directories, with bubblewrap's normal
`nodev,nosuid` bind semantics; the service never uses `--dev-bind` for project
trees. No host `/home`, writable
host root, package repositories, host network, target executables or inherited
shell commands are exposed. Sandbox children receive only metadata-related
capabilities needed by the tools.
For container projects the entire engine and its tool subprocesses share one
confined worker; tool path aliases are mapped inside that worker instead of
creating nested privileged mount namespaces.

Root operation uses the host user namespace to preserve arbitrary numeric
ownership. This is therefore **not** equivalent to a hardware VM or a complete
defense against kernel vulnerabilities. Device nodes can exist in the extracted
tree but are never opened by the GUI or executed. Third-party parser and kernel
vulnerabilities remain relevant; keep host tools patched.

No persistent mounts are created. Namespace mounts disappear with their worker.
Staging directories are cleaned on normal failures and recovered on reopen after
a crash. Failed jobs remain in history. Source copying uses a partial file until
commit.

Large/malicious inputs can exhaust disk or CPU despite estimates and timeouts.
Use trusted base images and dedicated storage quotas. Formal sandbox review,
resource quotas and a broader privileged device/ACL/capability integration suite
remain release gates. Numeric ownership, modes, user xattrs and links have been
exercised in the privileged exFAT container test.

## Package management

APT and interactive live editing execute imported code only in a QEMU guest.
Chroot alone is **not a security boundary**. The batch guest uses chroot within
the VM to address its mounted target; no host chroot executes imported code.
systemd-nspawn was evaluated: its user namespace and UID mapping require additional
care to retain arbitrary target ownership, and it shares the host kernel. The
implemented backend uses a guest kernel plus the existing bubblewrap confinement.
This still depends on QEMU, 9p server and host kernel security.

Package mutations operate on a full rootfs copy with service startup suppression.
Only explicit package names and allowlisted APT actions are accepted. Simulation
is read-only; success requires an audit and unchanged kernel inventory. Atomic
exchange commits the result and retains the old rootfs. Interrupted candidate
copies are distinguishable from completed rollback snapshots.

Interactive sessions instead expose the current project rootfs writable, after
confirmation. They have no automatic rollback. The GUI receives screenshots and
can send only validated keyboard/mouse events, shutdown or force-stop requests.
The privileged QMP socket is private. Guest network access is explicit; offline
operations retain a separate network namespace. Online QEMU NAT can reach the
Internet and local network. VM workers can access `/dev/kvm`; host `/boot` is
read-only for bootstrap generation. Live Desktop additionally probes EGL/VirGL
using one DRM render node and, for NVIDIA, `/dev/nvidia0`, `/dev/nvidiactl` and
`/dev/nvidia-uvm`. Its renderer reads host `/sys` for GPU discovery. These are
available to QEMU, not mounted inside the guest. GPU acceleration adds the host
graphics driver and VirGL renderer to the isolation boundary. Card/modeset nodes
are not exposed. Package workers retain their original device restrictions.
Host display sockets, clipboard, desktop bus and home are never shared.

## Built-ISO preview

QEMU runs unprivileged, with no networking, no host directory shares, no guest
disk and a temporary writable OVMF variable store. It uses a private Unix QMP
socket, QEMU's sandbox option, and a read-only CD-ROM image. VM changes are never
imported into the project. The UI polls framebuffer images; it is functional
but slower than a native SPICE client and supports a basic US-keyboard mapping.

See [bubblewrap's Debian manual](https://manpages.debian.org/bookworm/bubblewrap/bwrap.1.en.html)
and the [QEMU QMP reference](https://www.qemu.org/docs/master/interop/qemu-qmp-ref.html)
for the primitives used here.

Accelerated Live Desktop uses an internal Unix-domain RFB connection for
framebuffer readback (no network listener). The decoder bounds dimensions,
rectangle coordinates and server metadata before copying RGB pixel data.
