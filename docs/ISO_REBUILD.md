# ISO import and rebuild

## Import

1. Open the user's input as the original caller, reject devices/FIFOs/final
   symlinks, copy it into private storage and calculate SHA256.
2. Ask xorriso for paths, PVD, plain El Torito, system-area and boot replay reports.
   Reports are stored as data. They are never evaluated as shell commands.
3. Require the single-SquashFS Debian Live layout before extraction. Recognize
   the ISOLINUX MBR/GPT/APM hybrid only when its APM EFI partition agrees with
   the El Torito, MBR and GPT byte ranges and replay settings. Unknown Apple
   partitions remain unsupported.
4. Extract the ISO in an offline bubblewrap sandbox with only the source ISO
   read-only and a staging directory writable.
5. Estimate rootfs size incrementally from the SquashFS listing, without keeping
   hundreds of thousands of lines in memory or the GUI log; extract with strict error
   handling and xattrs enabled into a separate writable staging directory.
6. Validate os-release, dpkg architecture, boot references and kernel inventory.
7. Commit the extracted trees and save the manifest. Failed staging is removed.

Numeric ownership preservation requires the installed privileged helper. Tests
run unprivileged with fixtures owned by the test user; they do not establish
arbitrary-UID restoration on a production rootfs.

## Build

1. Verify the protected source SHA256. Reject unsupported layout and changes
   to target `/boot` or kernel modules, including edits within an existing version.
2. Rebuild SquashFS with xz, xattrs, strict errors and two worker processes.
3. Refresh existing `live/filesystem.size` and `live/filesystem.packages`.
   Installer package-removal lists remain untouched because package editing is
   not provided yet.
4. Update checksums for changed payload/metadata in an existing `md5sum.txt`.
   Boot catalogs and El Torito boot images are excluded because xorriso can
   regenerate catalogs and patch boot-info tables. All retained MD5 entries are
   verified against the extracted output, including originally unchanged files.
5. Load the **original ISO** in xorriso; map only the new SquashFS and supported
   metadata paths. Apply `-boot_image any replay` after these changes. Original
   files, volume ID, bootloader files and replayable partition provisions are
   carried from the source. No universal handcrafted mkisofs recipe is used.
6. Inspect output boot capability, check MBR/GPT/APM presence, perform an ISO media
   read check, extract the result, compare every replaced payload checksum, and
   validate extracted kernel/initramfs references. For the supported hybrid APM,
   also validate its relocated partition ranges and compare the EFI image bytes
   with the source tree.
7. Save a new ISO, SHA256, release JSON and log under a unique build ID.

Release labels are release-manifest metadata. They do not rewrite distro identity
or perform a Debian upgrade. The ISO-tree browser and boot editor are not exposed
in this phase.

Editing initramfs configuration does not automatically rebuild an initramfs.
Changes that require regenerated early-boot artifacts need the future kernel
manager; this milestone preserves the source artifacts.

## Boot verification

Readback demonstrates payload integrity, not bootability. Test both firmware
modes, the live desktop, installer, persistence (if applicable), and USB hybrid
boot as separate acceptance items. The release manifest initially records
`bios_boot_test: not_run` and `uefi_boot_test: not_run`; opening the preview does
not silently change these statuses.

The replay sequence follows the
[GNU xorriso manual](https://www.gnu.org/software/xorriso/man_1_xorriso.html).
It preserves recognized source boot equipment but does not establish
compatibility with every distribution or custom integrity scheme.

The hybrid APM case corresponds to xorriso's `-isohybrid-apm-hfsplus`: an Apple
partition entry referring to an existing El Torito boot image, rather than a
separate Apple operating-system layout. See the
[xorrisofs manual](https://manpages.debian.org/trixie/xorriso/xorrisofs.1.en.html).
