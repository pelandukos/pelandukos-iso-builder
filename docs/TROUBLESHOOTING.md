# Troubleshooting

## Live desktop before ISO creation

After extraction, use **Desktop → Open live desktop · no ISO needed**.
**Test ISO** is the separate built-image preview and still needs an ISO.
If bootstrap dependencies are missing, inspect **Settings → Check dependencies**:
the live editor needs static BusyBox, QEMU, kmod and a readable host kernel with
matching modules. The GUI must remain unprivileged; authorize only the helper.

The editor performs minimal live user/login setup. Distribution-specific
live-config hooks are skipped in this session; the original ISO scripts remain
unchanged. Target login/session availability still depends on the source desktop.
Startup diagnostics from live-config/display-manager are included in the job log.

Shut down the desktop before building or switching projects. Force-stop can leave
partial edits. Reopen recovers an interrupted session state but cannot undo its
file changes. A package rollback snapshot, if available, restores the entire
filesystem to that point, including any later desktop edits.

## Packages and rollback space

Use **Packages → Installed** to refresh the installed list. Repository search uses
the target's cached APT indexes; refresh metadata after editing target repository
files. Package operations run inside a VM. A snapshot copy requires roughly the
uncompressed rootfs size in free project storage, in addition to the original.
Discard a previous rollback copy explicitly before another transaction.
If a transaction fails, the original rootfs is retained; inspect its log.
An interrupted candidate copy can be discarded but cannot be restored.

**“File exceeds the text editor limit (2048 KiB)” when detecting/applying desktop
settings.** Earlier packages incorrectly read the target's package database
through the text editor. Install the updated package, reopen the existing project
and choose **2 Desktop → Detect desktop settings**. Reimporting the ISO is
unnecessary. The new metadata reader handles databases above 2 MiB with bounded
memory. The interactive file editor retains its own 2 MiB limit.

**Desktop Customizer is disabled in the old interface.** Install the current
package and reopen the GUI after any running job finishes. The interface now
uses six numbered steps. Import a supported ISO to unlock **2 Desktop**, then
detect settings. Options depend on installed target packages/schemas. GNOME
requires the host `libglib2.0-bin` tool. XFCE wallpaper and KDE GTK/font editing
remain unavailable; supported options still work.

**Desktop appearance did not change after rebuilding.** Confirm that you clicked
**Apply desktop settings**, then built and exported a new ISO. Existing target
user preferences, dconf policy or startup scripts may override the defaults.
The wallpaper thumbnail does not represent a booted system. See
[desktop customization](DESKTOP_CUSTOMIZATION.md) and test the exported ISO.

**The builder does not follow the PC theme.** The new UI uses the Qt platform
palette/font and refreshes styled controls on palette changes. An older running
GUI must be reopened. Qt platform integrations differ between packaged Qt and
Python wheels; check that your Qt installation receives the desktop palette.

**The GUI imports PySide6 but QtWidgets is missing.** Some distributions split
bindings into separate packages. Run `python3 main.py --check-dependencies`.
Install QtWidgets and QtNetwork modules, or use the isolated Qt environment in
INSTALL.md. Importing the top-level namespace alone is not a sufficient check.

**Helper not installed.** Build and install the Debian package. Development
source is deliberately not executable as root through the app. Do not run the
whole GUI with sudo.

**Polkit authorization fails.** Use an active graphical session with a Polkit
agent and an administrator account. Verify the package's policy and helper are
installed. A cancelled authorization leaves the project unchanged.

**bubblewrap fails.** Check that the host permits mount/PID namespaces and that
security policy permits the installed helper. There is no unsafe fallback to
unsandboxed extraction or a host chroot.

**Permission, xattr or symlink failure.** Project trees need Linux filesystem
semantics. Selecting exFAT now creates an ext4 container automatically. A strict
unsquashfs metadata error still aborts import instead of losing metadata.

**Unsupported layout / dynamic boot reference.** The first adapter is
intentionally narrow. Save the ISO analysis and log, add an adapter and fixture
tests before enabling a new layout. Do not bypass the guard to force a build.

**“Unsupported system-area layout” on a PelandukOS MBR/GPT/APM hybrid.** Earlier
0.1.0 packages rejected every APM. Rebuild and reinstall the current package to
get support for the verified ISOLINUX hybrid EFI mapping. Reimport into the
existing failed project; a new project is unnecessary. Other Apple partition
layouts still fail with a specific diagnostic. Updating the source checkout
alone does not update the installed helper.

**Large image / output limit during filesystem sizing.** The current extractor
reduces numeric SquashFS listings incrementally. It can handle more than the
command runner's normal 16 MiB capture limit without retaining that listing.
Normal command capture remains bounded and failures retain their diagnostic tail.

**Insufficient project-store space.** The failed PelandukOS input inspected on
2026-10-20 has an estimated rootfs size of 27 GiB. Its current conservative check
requires about 57 GiB free before ISO-tree extraction, including reserved build
space. Open the project and select **Source ISO → Move storage…**,
then choose a larger ext4, XFS, Btrfs or exFAT filesystem. Retry the import/build after
the move finishes. New projects can select a folder in their creation dialog;
**Settings** also stores a default. Free space on the external exFAT source
drive is used only after the project has been moved there. For exFAT choose a
container large enough for the source, extracted tree and build workspace; the
128 GiB default is intended to provide room for the inspected PelandukOS input.

**exFAT container creation is slow.** Capacity is reserved up front, so creation
can take minutes on an external HDD. Wait for completion. An allocation/format
failure may leave an unregistered incomplete `.ext4` file; the log gives its
name. Remove only that failed file if no project was created in it.

**Container full.** Current containers have fixed capacity. Choose a different
folder on the drive, select a larger capacity, and move the project there.
Moving all projects out does not automatically remove the old container.

**Loop/mount failure.** Check that the kernel supports loop devices and ext4,
and that host policy allows the installed helper's private mount namespace.
Containers are never mounted with `exec`, `suid` or device-node access. Do not
manually mount/edit their contents while the builder is using them.

**Project storage unavailable.** Reconnect its drive at the original mount path.
The project remains listed with an error tooltip. Do not replace the private
storage folder or its `.storage-id` marker; the helper refuses substituted paths.

**Interrupted project move.** The original remains authoritative until the copy
is verified and the registry switch completes. Retry the move to discard its
incomplete staging copy. A crash after the switch can leave an old redundant
copy; the new location remains authoritative. Do not manually remove either copy
without checking the current project's displayed storage path.

**Interrupted operation.** Reopen through **Source ISO → Saved projects**. Recovery removes staging,
retains completed edits and records the interruption. Failed imports can be
retried if a root filesystem was not committed. Source reference files are
never edited by recovery.

**Export destination already exists.** Choose an empty directory or a new build
filename. Exclusive host creation is intentional. A failed export may leave
partial files, which you should review before removing.

**Kernel changed.** Phase 1 refuses changes to `/boot` or modules. Restore the
original files, or reimport into a new project. Kernel package and initramfs
synchronization need the later kernel manager.

**Black preview / QEMU failure.** Inspect the visible QEMU log. UEFI requires a
matching OVMF CODE/VARS pair; Secure Boot is not enabled by this preview.
Without access to `/dev/kvm`, software emulation is selected automatically and
desktop boot can take minutes. An existing but unusable KVM device may require
host-specific configuration; errors are shown instead of silently claiming a
successful preview.

**No graphical output in an automated boot test.** The smoke script requires a
serial success marker. Arbitrary desktop ISOs usually need console configuration
or manual GUI observation. Process startup or timeout is not counted as a pass.

Full build logs stay in private project storage and are exported with each
successful build. The on-screen log keeps a bounded recent history.
