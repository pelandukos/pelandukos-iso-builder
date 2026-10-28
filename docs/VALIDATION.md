# Validation and release gates

The ordinary pytest suite covers manifest persistence/locking, recovery,
filesystem boundaries, shell-free command execution, boot configuration,
unsupported layouts, metadata preservation, real SquashFS extraction/build,
ISO readback, checksums, source immutability, and offscreen GUI construction.

`tests/test_boot_smoke.py` creates a tiny hybrid BIOS/UEFI ISO with a supplied
Linux kernel, BusyBox initramfs and synthetic Debian package metadata. It
imports, edits and rebuilds that image, then boots the rebuilt image in QEMU.
Success requires `PELANDUKOS_SYNTHETIC_BOOT_OK` from the guest.

This proves the tested boot chain reaches the test initramfs. It does not prove
the Debian live root is mounted, the edited SquashFS is used by the guest,
the desktop launches, or an installer works. SquashFS readback is separately
verified by the integration test.

Run a marker-driven smoke test against any prepared ISO:

```bash
python3 scripts/smoke_boot.py output.iso --mode BIOS \
  --marker 'YOUR_GUEST_SUCCESS_MARKER' --report bios.json
python3 scripts/smoke_boot.py output.iso --mode UEFI \
  --marker 'YOUR_GUEST_SUCCESS_MARKER' --report uefi.json
```

Reports include ISO SHA256, firmware mode, acceleration, serial log and pass/fail.
Do not use a generic bootloader banner as the success marker for desktop testing.

## Required real-ISO acceptance

A real PelandukOS ISO was subsequently inspected to diagnose the hybrid-APM
rejection. Its boot metadata now has a regression fixture, but its full
extract/rebuild/desktop/installer workflow has not been qualified. Before a
stable release, record the exact image version, source URL/checksum, host versions and:

The opt-in integration below invokes the **installed** helper through Polkit and
retains its created project for inspection. It may request administrator
authentication; do not run it on arbitrary input without reviewing the security
model. The ordinary suite skips it:

```bash
PELANDUKOS_TEST_ISO=/path/to/debian-live-amd64.iso \
  .venv/bin/python -m pytest -q tests/test_real_iso.py
```

Set `PELANDUKOS_TEST_BOOT_MARKER` only if the supplied image emits a known guest
success marker to its serial console. Otherwise this integration validates
rebuilding/readback and the following boot acceptance items remain manual:

- Import through the installed Polkit helper in an active unprivileged session.
- Verify arbitrary numeric ownership, permissions, hardlinks, symlinks,
  device nodes, POSIX ACLs and file capabilities against the source rootfs.
- Edit a visible live-session file; save, close and reopen the application.
- Rebuild, export, and independently run `sha256sum -c`.
- Boot BIOS and UEFI separately, reaching the live desktop and observing edits.
- Run the original installer workflow in a disposable VM disk.
- Verify hybrid USB behavior using a disposable virtual disk copy.
- Test persistence and Secure Boot separately if either is claimed.
- Exercise insufficient disk, namespace denial, failed extraction and crash
  recovery through the installed helper.
- Test both Debian 12 and Debian 13 host package installations.

Never write a test ISO to a host block device as an automated test. The app does
not implement a host USB flashing function.
