"""Offline desktop defaults. Never starts a target program or host desktop bus."""
import configparser
import hashlib
import io
import json
import os
import re
import shutil
import tempfile
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

from core.errors import BuilderError
from core.util import atomic_json, read_text, regular_file, safe_path

SCHEMAS = "usr/share/glib-2.0/schemas"
OVERRIDE = "99_pelandukos-builder.gschema.override"
XFCONF = "etc/xdg/xfce4/xfconf/xfce-perchannel-xml"
PLASMA = "usr/share/plasma/shells/org.kde.plasma.desktop/contents"
PACKAGES = {"GNOME": "gnome-shell", "KDE Plasma": "plasma-desktop", "XFCE": "xfce4-session"}
STATUS_LIMIT = 64 * 1024**2
STATUS_LINE_LIMIT = 64 * 1024
GNOME_KEYS = {
    "gtk_theme": ("org.gnome.desktop.interface", "gtk-theme"),
    "icon_theme": ("org.gnome.desktop.interface", "icon-theme"),
    "cursor_theme": ("org.gnome.desktop.interface", "cursor-theme"),
    "font": ("org.gnome.desktop.interface", "font-name"),
    "wallpaper": ("org.gnome.desktop.background", "picture-uri"),
}


def xml_document(text):
    if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
        raise BuilderError("Desktop XML must not contain document types or entities.")
    try:
        return ET.fromstring(text)
    except ET.ParseError as error:
        raise BuilderError(f"Invalid desktop XML: {error}") from error


def settings_file(text=""):
    parser = configparser.RawConfigParser(interpolation=None, strict=True)
    parser.optionxform = str
    try:
        parser.read_string(text)
    except configparser.Error as error:
        raise BuilderError(f"Cannot safely edit this desktop configuration: {error}") from error
    return parser


def ini_bytes(parser):
    stream = io.StringIO()
    parser.write(stream, space_around_delimiters=False)
    return stream.getvalue().encode()


def ini_set(parser, group, key, value):
    if not parser.has_section(group):
        parser.add_section(group)
    parser.set(group, key, value)


class DesktopManager:
    def __init__(self, root, sandbox):
        self.root, self.sandbox = Path(root), sandbox

    def path(self, relative):
        return safe_path(self.root, relative)

    def text(self, relative):
        path = self.path(relative)
        return read_text(path) if path.exists() else ""

    def schema_keys(self):
        result = {}
        directory = self.path(SCHEMAS)
        if directory.is_dir():
            for path in directory.glob("*.gschema.xml"):
                document = xml_document(read_text(self.path(str(path.relative_to(self.root)))))
                for schema in document.findall("schema"):
                    result.setdefault(schema.get("id"), set()).update(
                        key.get("name") for key in schema.findall("key"))
        return result

    def installed_names(self, directory, marker):
        base = self.path(directory)
        if not base.is_dir():
            return []
        names = []
        for child in base.iterdir():
            if child.is_symlink() or not child.is_dir():
                continue
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 ._+-]{0,119}", child.name):
                continue
            candidate = child / marker
            if not candidate.is_symlink() and candidate.exists():
                names.append(child.name)
        return sorted(names, key=str.casefold)

    def installed_desktops(self):
        """Read package metadata independently of the interactive editor's limit."""
        path = self.path("var/lib/dpkg/status")
        if not path.exists():
            return set()
        regular_file(path)
        installed = set()
        candidates = {package.encode("ascii") for package in PACKAGES.values()}
        fields = {}
        def finish_record():
            package = fields.get(b"Package")
            if package in candidates and fields.get(b"Status") == b"install ok installed":
                installed.add(package.decode("ascii"))
            fields.clear()
        with path.open("rb") as stream:
            if os.fstat(stream.fileno()).st_size > STATUS_LIMIT:
                raise BuilderError("Target package database /var/lib/dpkg/status exceeds the 64 MiB metadata limit.")
            total = 0
            while line := stream.readline(STATUS_LINE_LIMIT + 1):
                total += len(line)
                if total > STATUS_LIMIT:
                    raise BuilderError("Target package database /var/lib/dpkg/status exceeds the 64 MiB metadata limit.")
                if len(line) > STATUS_LINE_LIMIT or b"\0" in line:
                    raise BuilderError("Invalid target package database /var/lib/dpkg/status: "
                                       "lines must be at most 64 KiB and contain no NUL bytes.")
                line = line.rstrip(b"\r\n")
                if not line:
                    finish_record()
                elif not line[:1].isspace():
                    name, separator, value = line.partition(b":")
                    if separator and name in (b"Package", b"Status"):
                        fields[name] = value.strip()
            finish_record()
        return installed

    def capabilities(self):
        installed = self.installed_desktops()
        desktops = {}
        for desktop, package in PACKAGES.items():
            if package not in installed:
                continue
            fields, reason = [], ""
            if desktop == "GNOME":
                if not shutil.which("glib-compile-schemas"):
                    reason = "Install libglib2.0-bin on the builder PC to compile GNOME defaults."
                else:
                    keys = self.schema_keys()
                    fields = [field for field, (schema, key) in GNOME_KEYS.items()
                              if key in keys.get(schema, set())]
                    if not fields:
                        reason = "GNOME source schemas are missing from this ISO."
            elif desktop == "KDE Plasma":
                fields = ["icon_theme", "cursor_theme"]
                if self.path(PLASMA).is_dir():
                    fields.append("wallpaper")
                reason = "KDE icons/cursors and Plasma wallpaper; GTK themes and fonts are not configured here."
            else:
                fields = ["gtk_theme", "icon_theme", "cursor_theme", "font"]
                reason = "XFCE appearance defaults. Wallpaper needs display-specific configuration and is not enabled."
            desktops[desktop] = {"fields": fields, "note": reason}
        return {"desktops": desktops,
                "gtk_theme": self.installed_names("usr/share/themes", "gtk-3.0"),
                "icon_theme": self.installed_names("usr/share/icons", "index.theme"),
                "cursor_theme": self.installed_names("usr/share/icons", "cursors")}

    def plan(self, values, wallpaper=None):
        if not isinstance(values, dict) or set(values) - {"desktop", *GNOME_KEYS}:
            raise BuilderError("Unknown desktop setting.")
        desktop = values.get("desktop")
        caps = self.capabilities()
        capability = caps["desktops"].get(desktop)
        if not capability or not capability["fields"]:
            raise BuilderError("This target desktop does not have supported customization capabilities.")
        selected = {key: value for key, value in values.items() if key != "desktop" and value}
        if wallpaper is not None:
            selected["wallpaper"] = True
        if not selected:
            raise BuilderError("Choose at least one desktop setting to apply.")
        if set(selected) - set(capability["fields"]):
            raise BuilderError("A selected setting is unsupported by this target desktop.")
        for key, value in selected.items():
            if key in ("gtk_theme", "icon_theme", "cursor_theme") and value not in caps[key]:
                raise BuilderError(f"Selected {key.replace('_', ' ')} is not installed in the ISO.")
            if key == "font" and (not isinstance(value, str) or not re.fullmatch(
                    r"[A-Za-z0-9][A-Za-z0-9 ._+-]{0,100} [1-9][0-9]?", value)):
                raise BuilderError("Use a font family and size, for example Noto Sans 11.")
        changes = {}
        if "wallpaper" in selected:
            if not isinstance(wallpaper, bytes) or not 8 <= len(wallpaper) <= 32 * 1024**2:
                raise BuilderError("Choose a PNG or JPEG wallpaper up to 32 MiB.")
            extension = "png" if wallpaper.startswith(b"\x89PNG\r\n\x1a\n") else (
                "jpg" if wallpaper.startswith(b"\xff\xd8\xff") else None)
            if not extension:
                raise BuilderError("Wallpaper must be a PNG or JPEG image.")
            relative = f"usr/share/backgrounds/pelandukos-builder/{hashlib.sha256(wallpaper).hexdigest()}.{extension}"
            changes[relative] = wallpaper
            selected["wallpaper"] = Path("/" + relative).as_uri()
        if desktop == "GNOME":
            self.gnome_plan(selected, changes)
        elif desktop == "KDE Plasma":
            for key, relative, group, option in (
                ("icon_theme", "etc/xdg/kdeglobals", "Icons", "Theme"),
                ("cursor_theme", "etc/xdg/kcminputrc", "Mouse", "cursorTheme"),
            ):
                if key in selected:
                    parser = settings_file(self.text(relative))
                    ini_set(parser, group, option, selected[key])
                    changes[relative] = ini_bytes(parser)
            if "wallpaper" in selected:
                # Plasma runs shell-package update scripts once per user, after layout creation.
                updates = self.path(PLASMA + "/updates")
                previous = [int(match[1]) for path in updates.iterdir()
                            if (match := re.fullmatch(r"zz-pelandukos-builder-wallpaper-(\d{8})\.js", path.name))
                            ] if updates.is_dir() else []
                revision = max(previous, default=0) + 1
                if revision > 99999999:
                    raise BuilderError("Plasma wallpaper update sequence exhausted.")
                changes[PLASMA + f"/updates/zz-pelandukos-builder-wallpaper-{revision:08}.js"] = (
                    "// Generated by PelandukOS ISO Builder\n"
                    "var screens = desktops();\n"
                    "for (var i = 0; i < screens.length; i++) {\n"
                    '  screens[i].wallpaperPlugin = "org.kde.image";\n'
                    '  screens[i].currentConfigGroup = ["Wallpaper", "org.kde.image", "General"];\n'
                    f'  screens[i].writeConfig("Image", {json.dumps(selected["wallpaper"])});\n'
                    "}\n").encode()
        else:
            relative = XFCONF + "/xsettings.xml"
            text = self.text(relative)
            document = xml_document(text) if text else ET.Element("channel", name="xsettings", version="1.0")
            if document.tag != "channel" or document.get("name") != "xsettings":
                raise BuilderError("Unexpected XFCE xsettings channel.")
            for key, group, option in (("gtk_theme", "Net", "ThemeName"), ("icon_theme", "Net", "IconThemeName"),
                                       ("cursor_theme", "Gtk", "CursorThemeName"), ("font", "Gtk", "FontName")):
                if key not in selected:
                    continue
                parent = document.find(f"./property[@name='{group}']")
                if parent is None:
                    parent = ET.SubElement(document, "property", name=group, type="empty")
                item = parent.find(f"./property[@name='{option}']")
                if item is None:
                    item = ET.SubElement(parent, "property", name=option)
                item.set("type", "string")
                item.set("value", selected[key])
            changes[relative] = ET.tostring(document, encoding="utf-8", xml_declaration=True)
        return changes, selected

    def gnome_plan(self, selected, changes):
        parser = settings_file(self.text(SCHEMAS + "/" + OVERRIDE))
        for key, value in selected.items():
            schema, option = GNOME_KEYS[key]
            ini_set(parser, schema, option, "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'")
        if "wallpaper" in selected and "picture-uri-dark" in self.schema_keys().get("org.gnome.desktop.background", set()):
            ini_set(parser, "org.gnome.desktop.background", "picture-uri-dark", "'" + selected["wallpaper"] + "'")
        changes[SCHEMAS + "/" + OVERRIDE] = ini_bytes(parser)
        # Compile a private copy before touching the target. Never execute target GLib.
        with tempfile.TemporaryDirectory(prefix="pelandukos-schemas-") as temporary:
            directory = Path(temporary)
            total = 0
            for source in self.path(SCHEMAS).iterdir():
                if source.name.endswith((".gschema.xml", ".gschema.override")):
                    text = read_text(self.path(str(source.relative_to(self.root))))
                    total += len(text.encode())
                    if total > 64 * 1024**2:
                        raise BuilderError("Target schema sources exceed 64 MiB.")
                    (directory / source.name).write_text(text)
            (directory / OVERRIDE).write_bytes(changes[SCHEMAS + "/" + OVERRIDE])
            self.sandbox.run(["glib-compile-schemas", "--strict", "/schemas"], writes={directory: "/schemas"})
            changes[SCHEMAS + "/gschemas.compiled"] = (directory / "gschemas.compiled").read_bytes()

    def ensure_parent(self, target):
        current = self.root
        for part in target.parent.relative_to(self.root).parts:
            current /= part
            if not current.exists():
                current.mkdir(mode=0o755)
                current.chmod(0o755)  # helper umask is deliberately 0077

    def write(self, target, data):
        self.ensure_parent(target)
        info = target.stat() if target.exists() else None
        fd, temporary = tempfile.mkstemp(prefix=".desktop-", dir=target.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            if info:
                os.chown(temporary, info.st_uid, info.st_gid)
                shutil.copystat(target, temporary, follow_symlinks=False)
            else:
                os.chmod(temporary, 0o644)
            os.replace(temporary, target)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def apply(self, project, values, wallpaper=None):
        changes, selected = self.plan(values, wallpaper)
        for relative in changes:
            path = self.path(relative)
            if path.exists():
                regular_file(path)
                if path.stat().st_nlink != 1:
                    raise BuilderError("Refusing to replace a hard-linked desktop configuration.")
        backup = project / "snapshots" / ("desktop-" + uuid.uuid4().hex)
        backup.mkdir(mode=0o700)
        originals = {}
        for index, relative in enumerate(changes):
            target = self.path(relative)
            originals[relative] = str(index) if target.exists() else None
            if target.exists():
                shutil.copyfile(target, backup / str(index), follow_symlinks=False)
                info = target.stat()
                os.chown(backup / str(index), info.st_uid, info.st_gid)
                shutil.copystat(target, backup / str(index), follow_symlinks=False)
        atomic_json(backup / "files.json", originals)
        written = []
        try:
            for relative, data in changes.items():
                target = self.path(relative)
                self.write(target, data)
                written.append(relative)
        except Exception:
            for relative in reversed(written):
                target = self.path(relative)
                if originals[relative] is None:
                    target.unlink()
                else:
                    os.replace(backup / originals[relative], target)
            raise
        return {"desktop": values["desktop"], "settings": selected, "files": list(changes),
                "backup": str(backup.relative_to(project))}
