# Development status

Version: **0.2.3 — experimental ISO, live-rootfs editor and package workflows**
Developer: **Tunnelbiz Studio Sdn Bhd**

No production-support or complete Cubic-equivalence claim is made.

## Implemented

- Seven-step PySide6 workflow: source, desktop, packages, optional files, release, build/export,
  VM test. Readable project summary, Back/Next navigation, capability gating and
  collapsible logs. Application appearance follows the PC's Qt palette/font and
  updates on system palette changes.
- Live rootfs editing before any ISO build: QEMU boots a host-kernel bootstrap,
  mounts only the project rootfs through virtio-9p and switches to the target init.
  Embedded framebuffer/input, explicit persistence confirmation, optional guest
  network, shutdown/force-stop controls and project locking. GUI stays unprivileged.
- Target APT listing/search/details/audit, dependency simulation, metadata refresh,
  install/remove/selected-upgrade in a VM. Full-copy transaction, service suppression,
  kernel guard, atomic commit, rollback/discard UI and interrupted-copy detection.
  Repository files are editable through Files; no separate repository/key wizard.
- Initial offline desktop customization: GNOME schema defaults for wallpaper,
  GTK/icons/cursor/font; KDE icons/cursor and ordered Plasma wallpaper update scripts;
  XFCE xsettings appearance defaults. Changes use explicit confirmation, project-local
  configuration backups and rollback on handled write failures.
- Desktop detection streams `/var/lib/dpkg/status` with separate metadata bounds
  (64 MiB total / 64 KiB per line); the file editor's 2 MiB limit no longer blocks
  desktop inspection or apply on larger desktop ISOs.
- Measured current-stage percentages, processed/total GiB and elapsed job time.
  Container reservation/formatting, copying, hashes and supported tool progress
  stream through the privileged helper. Unmeasured stages remain indeterminate;
  no simulated overall percentage or completion-time estimate.
- Create/list/open projects; atomic JSON and history; project reference export.
- Custom release version input in project creation and Build ISO; validated,
  persisted versions, synchronized release editor and automatic filename suggestions
  that preserve a manually entered ISO filename.
- Selectable project folders, default-folder preference, capacity/filesystem
  display and verified relocation of existing projects. Private storage registry
  retains references across restarts and reports disconnected drives.
- Experimental exFAT storage using app-created ext4 containers, selectable
  capacity, private namespace mounts, autoclearing loop devices, confined
  project workers and verified relocation between native/container storage.
- Protected source copy and SHA256; source layout/boot/distribution/architecture/
  kernel/desktop inspection.
- Single-SquashFS Debian/PelandukOS Live adapter with conservative rejection.
- Validated ISOLINUX MBR/GPT/APM hybrid EFI mapping, with partition-range and
  unchanged-EFI-payload checks during rebuild; unrelated APM remains rejected.
- Streaming SquashFS size estimation for large file inventories.
- Offline sandboxed ISO and strict SquashFS extraction, disk-space estimates.
- Target file listing, filename search, folder creation, text editing, file
  import/copy/move/rename/delete, numeric ownership and mode changes.
- Symlink path guards; regular-file checks; hardlink-preserving copy; text
  metadata preservation; destructive confirmation.
- SquashFS rebuild; supported metadata refresh; original boot replay; output
  media read check, extracted-payload checksum verification and boot-path checks.
- SHA256, build log and release JSON export; guarded output creation.
- Root helper with Polkit policy, caller-scoped storage and allowlisted API.
- Embedded QEMU framebuffer/input; BIOS/UEFI choice; KVM detection/TCG fallback;
  temporary build-and-preview workflow; isolated temporary firmware variables.
- Release metadata and operation history, explicitly separate from distro upgrades.
- Debian binary package builder, launcher, SVG icon, installation/security/
  rebuild/troubleshooting documentation.

## Validation

- Live/package follow-up: full unprivileged suite **98 passed, 6 skipped** in
  41.18 seconds. Final package-metadata/GUI follow-up: **10 passed** in 0.81 seconds.
  New tests boot an actual no-ISO VM,
  verify persisted file contents/modes, receive framebuffer images, and exercise
  snapshot exchange/restore and failure cleanup.
- Privileged offline APT integration: **1 passed** (repeated after stream changes). A generated
  local `.deb` repository was refreshed in the guest; real target apt-get/dpkg
  installed and removed the fixture package, verified root ownership, and restored
  it through rollback. No host package or repository changes were made.
- Real PelandukOS extracted rootfs: GNOME/Wayland desktop reached without an ISO
  rebuild, using a read-only target export and in-guest RAM overlay. Mouse click
  dismissed the welcome window, Escape left overview, and QMP powerdown completed
  normally (`forced: false`, approximately 57 seconds including the test wait).
  The original project contents were not edited. [Framebuffer evidence](docs/evidence/live-rootfs-wayland.png).
  Synthetic writable sessions independently verify persistence, keyboard evdev
  events, confined helper streaming and recovery. X11 has not been separately qualified.

- Package-metadata regression follow-up: **27 tests passed** in 5.07 seconds.
  The previously installed reader reproduced the exact 2048 KiB error on a
  2.88 MB package database. Large-database inspect/apply/rebuild/reopen now pass
  in native and confined workers, with bounds, continuation records, removed
  packages, final-record parsing, symlink safety and editor-limit checks.
- Previous full suite with ISO boot tests enabled: **88 tests passed, 1 UEFI timeout, 1 real-ISO test skipped**.
  The unchanged timed-out ISO passed a repeat UEFI boot in 4.29 seconds; the
  intermittent timeout remains unexplained. The other three boot cases passed.
  Final desktop/GUI follow-up: **13 tests passed**, including compiled GNOME
  settings, rebuilt wallpaper/config readback, confined input grants, rollback,
  desktop-specific configuration, step gating and live palette changes.
  See [test evidence](docs/TEST_RESULTS.md) for the environment and exact scope.
- Unprivileged synthetic tests validate ownership for the test user's files,
  mode, user xattrs, symlinks and hardlinks. They do not substitute for a
  privileged arbitrary-owner/ACL/capability/device-node acceptance test.
- BIOS/UEFI synthetic boot tests passed under KVM/OVMF for both MBR/GPT and
  MBR/GPT/APM layouts. They remain opt-in and
  require host fixture tools; neither test establishes desktop/installer behavior.
- Offscreen GUI construction and live-editor controls are tested. The real
  read-only GNOME/Wayland session exercised helper-side framebuffer/input;
  a full operator walkthrough in the installed GUI remains separate acceptance.
- Real PelandukOS boot metadata, filesystem inventory and an extracted GNOME
  rootfs have been exercised. Full rebuilt-ISO BIOS/UEFI and installer acceptance
  remains pending; the host-kernel live editor does not substitute for that test.
- Privileged synthetic extraction/build/export and numeric ownership preservation
  have been exercised through an ext4 container on the actual exFAT drive.
  Full real-desktop ISO acceptance remains a separate gate.
- No production qualification for installer, Secure Boot, persistence or USB boot.

## Not yet implemented

- Phase 2 remainder: dedicated repository/key wizard, download percentage parsing,
  multi-transaction snapshot browser and distribution-upgrade workflow.
- Phase 3 remainder: XFCE wallpaper, KDE fonts/global themes, new theme installation,
  login backgrounds, startup apps, desktop shortcuts, Plymouth and boot branding.
- Phase 4: kernel/firmware changes, initramfs rebuild, service manager, boot editor.
- Phase 5 remainder: project cloning, general snapshots and snapshot comparison,
  portable project archives, automated real desktop/installer acceptance.
- Other adapters: Ubuntu/casper, Mint, multi-SquashFS/layered layouts,
  multiarch targets and other CPU architectures.

Unimplemented tools are omitted from the workflow and described in Settings.
Desktop controls are gated by the detected target capabilities.

## Known limits

- Selected storage supports ext4/XFS/Btrfs and exFAT via ext4 containers.
  FAT/NTFS remain rejected. Container resize/removal UI is not implemented.
  tmpfs works for temporary tests only. Reference JSON alone is not portable.
- File browsing refuses traversal even through safe-looking target symlinks.
  Open the concrete in-image path instead.
- Desktop defaults may be overridden by existing user settings/distribution
  policies. GTK themes do not necessarily affect libadwaita applications.
  GNOME settings are compiled and read back in tests; actual GNOME/KDE/XFCE
  desktop sessions still need acceptance testing. Configuration backups are
  retained, but there is no snapshot restore UI or crash-atomic multi-file commit.
- Existing host exports are never overwritten; a failed export can leave a
  partial caller-owned file. Choose a fresh directory when retrying.
- Long builds cannot be cancelled from the UI. Reopening recovers stale stages.
- Preview uses periodic QMP framebuffer capture with basic keyboard mapping.
  It is not a high-performance SPICE viewer and has no clipboard/host shares.
- Live rootfs editing shares only the selected target tree. Changes are immediate
  and there is no automatic live-session rollback. Personal preferences remain in
  that user's home. The editor skips distribution-specific live-config hooks and
  uses a host kernel, so final ISO boot testing remains necessary.
- Package transactions need free space for a full rootfs copy (reflinks where
  supported). Kernel/initramfs/module changes are refused before commit.
- Resource estimates do not impose hard quotas on malicious input.
- The root helper is security-sensitive and still needs independent review.
- Desktop/runtime packaging must be tested on both Debian target versions.
