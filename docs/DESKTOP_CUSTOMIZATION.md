# Offline desktop customization

Step 2 inspects the extracted target's installed package database. GNOME controls
also require the corresponding source schema keys and the host schema compiler.
An unsupported option is disabled with an explanation. The GUI never runs as root;
inspection/apply use the installed Polkit helper and the existing project lock.

## Implemented mechanisms

- GNOME: writes `usr/share/glib-2.0/schemas/99_pelandukos-builder.gschema.override`
  and recompiles `gschemas.compiled`. Schema sources are copied into an isolated
  temporary directory and compiled with the trusted host tool before target
  writes begin. Both normal and dark wallpaper keys are set when present.
  This uses [GSettings vendor defaults](https://docs.gtk.org/gio/class.Settings.html),
  rather than opening the host user's dconf session.
- KDE Plasma: updates the `Icons/Theme` setting in `/etc/xdg/kdeglobals` and
  `Mouse/cursorTheme` in `/etc/xdg/kcminputrc`. Wallpaper uses a generated, numbered
  shell update script under the existing Plasma desktop shell package. Each
  successive revision sorts after earlier builder revisions and has a new
  filename so previously configured users can receive the update. Plasma
  [runs these scripts after loading a desktop](https://develop.kde.org/docs/plasma/scripting/);
  the generated script uses the [wallpaper configuration API](https://develop.kde.org/docs/plasma/scripting/api/).
- XFCE: updates `/etc/xdg/xfce4/xfconf/xfce-perchannel-xml/xsettings.xml`, retaining
  unrelated properties. The Net/ThemeName, Net/IconThemeName, Gtk/CursorThemeName
  and Gtk/FontName fields follow the
  [upstream xsettings defaults](https://github.com/xfce-mirror/xfce4-settings/blob/master/xfsettingsd/xsettings.xml).
  Display-specific wallpaper configuration is not yet implemented.

The image importer accepts PNG/JPEG up to 32 MiB. The GUI decodes a bounded
thumbnail; the backend checks size and signature without decoding image data as
root. Images are stored by content hash under `/usr/share/backgrounds/pelandukos-builder/`.
Theme names must come from detected target assets. Symlinked theme directories
and markers are excluded. Font family/size can be entered, but the matching font
must already be installed in the target.

## Preservation and failure handling

Apply requires confirmation. Generated paths are fixed by the adapter; caller
text cannot select arbitrary host paths or commands. The container broker opens
wallpaper inputs with caller permissions and exposes only that file to its worker.
No target executable is started during customization.

Before changing files, the manager validates the plan, rejects symlink traversal
and hard-linked configuration files, then saves originals and a file map under
`snapshots/desktop-<id>/`. Existing ownership/modes/xattrs are retained; new
configuration files use 0644 and new directories 0755. Handled write failures
restore already changed files. Backups remain for inspection.

These are configuration backups, not complete project snapshots. A power failure
between writes can leave a partially applied set; there is no automatic restore
UI or crash-atomic multi-file transaction. Unrelated INI values/XML properties
are retained, while comments/formatting may be normalized.

## Validation boundary

Tests compile real GNOME schema defaults and query them using a memory GSettings
backend. Native and confined import/apply/build/reopen tests verify persistence
and ISO payload readback. KDE and XFCE configuration output is tested separately.
No actual desktop session or installer is qualified by those tests.

Existing user settings, dconf policies, later vendor overrides and distribution
startup scripts may take precedence over these defaults. Libadwaita apps may
ignore GTK theme selection. A rebuilt ISO and VM boot are needed to assess the
actual result; the thumbnail does not simulate a running desktop.

## Builder appearance

The builder retains the Qt platform palette, fonts and widget style and refreshes
palette-dependent styling when the PC theme changes. This follows
[Qt's system palette mechanism](https://doc.qt.io/qt-6/qguiapplication.html#palette).
The installed Qt platform integration determines which system appearance settings
are available. Builder appearance is independent of the ISO desktop settings.
