import base64
import json
import subprocess
import xml.etree.ElementTree as ET

import pytest

from core.desktop_manager import DesktopManager, SCHEMAS, OVERRIDE, XFCONF, PLASMA, STATUS_LIMIT, STATUS_LINE_LIMIT
from core.errors import BuilderError
from core.filesystem_manager import FilesystemManager
from core.util import digest
from services.commands import CommandRunner, Sandbox
from services.storage_bridge import engine_call

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j5xkAAAAASUVORK5CYII=")


def desktop_fixture(root, desktop="GNOME"):
    package = {"GNOME": "gnome-shell", "KDE Plasma": "plasma-desktop", "XFCE": "xfce4-session"}[desktop]
    for directory in ("var/lib/dpkg", SCHEMAS, "usr/share/themes/Test Theme/gtk-3.0",
                      "usr/share/icons/Test Icons/cursors", PLASMA, "etc/xdg", XFCONF):
        (root / directory).mkdir(parents=True, exist_ok=True)
    (root / "usr/share/icons/Test Icons/index.theme").write_text("[Icon Theme]\nName=Test Icons\n")
    with (root / "var/lib/dpkg/status").open("a") as stream:
        stream.write(f"\n\nPackage: {package}\nStatus: install ok installed\nArchitecture: amd64\nVersion: 1\n\n")
    (root / SCHEMAS / "fixture.gschema.xml").write_text("""<schemalist>
    <schema id="org.gnome.desktop.interface" path="/org/gnome/desktop/interface/">
      <key name="gtk-theme" type="s"><default>'Original'</default></key>
      <key name="icon-theme" type="s"><default>'Original'</default></key>
      <key name="cursor-theme" type="s"><default>'Original'</default></key>
      <key name="font-name" type="s"><default>'Sans 10'</default></key>
    </schema>
    <schema id="org.gnome.desktop.background" path="/org/gnome/desktop/background/">
      <key name="picture-uri" type="s"><default>''</default></key>
      <key name="picture-uri-dark" type="s"><default>''</default></key>
    </schema></schemalist>""")
    return DesktopManager(root, Sandbox(CommandRunner()))


def project_dirs(tmp_path):
    project = tmp_path / "project"
    (project / "snapshots").mkdir(parents=True)
    return project


def large_status(root):
    """A desktop ISO database larger than the editor cap, with the desktop last."""
    path = root / "var/lib/dpkg/status"
    tail = path.read_bytes()
    with path.open("wb") as stream:
        for index in range(3000):
            stream.write(f"Package: fixture-{index}\nStatus: install ok installed\n"
                         "Architecture: amd64\nVersion: 1\nDescription: metadata fixture\n".encode())
            stream.write(b" " + b"x" * 900 + b"\n\n")
        stream.write(tail.rstrip() + b"\n\nPackage: plasma-desktop\nStatus: deinstall ok config-files\n"
                     b"Description: continuation fields are not package headers\n"
                     b" Package: xfce4-session\n Status: install ok installed")
    assert path.stat().st_size > 2 * 1024**2


def test_large_package_database_is_metadata_not_editor_text(tmp_path):
    project = project_dirs(tmp_path)
    manager = desktop_fixture(project / "rootfs")
    large_status(manager.root)
    assert set(manager.capabilities()["desktops"]) == {"GNOME"}
    assert manager.installed_desktops() == {"gnome-shell"}
    with pytest.raises(BuilderError, match="text editor limit"):
        FilesystemManager(manager.root).read("var/lib/dpkg/status")
    manager.apply(project, {"desktop": "GNOME", "font": "Sans 12"})
    assert "Sans 12" in (manager.root / SCHEMAS / OVERRIDE).read_text()


def test_package_database_bounds_and_final_record(tmp_path):
    manager = desktop_fixture(tmp_path / "root")
    path = manager.root / "var/lib/dpkg/status"
    path.write_bytes(b"Package: gnome-shell\r\nStatus: install ok installed")
    assert manager.installed_desktops() == {"gnome-shell"}
    for data in (b"x" * (STATUS_LINE_LIMIT + 1), b"Package: gnome-shell\0\n"):
        path.write_bytes(data)
        with pytest.raises(BuilderError, match="Invalid target package database"):
            manager.installed_desktops()
    with path.open("wb") as stream:
        stream.truncate(STATUS_LIMIT + 1)
    with pytest.raises(BuilderError, match="64 MiB metadata limit"):
        manager.installed_desktops()
    path.unlink()
    outside = tmp_path / "host-status"
    outside.write_text("host")
    path.symlink_to(outside)
    with pytest.raises(BuilderError, match="Symlink"):
        manager.installed_desktops()
    assert outside.read_text() == "host"


def test_gnome_compiled_defaults_and_backup(tmp_path):
    project = project_dirs(tmp_path)
    root = project / "rootfs"
    manager = desktop_fixture(root)
    original = root / SCHEMAS / OVERRIDE
    original.write_text("[org.gnome.desktop.interface]\nfont-name='Sans 9'\n")
    original.chmod(0o640)
    result = manager.apply(project, {"desktop": "GNOME", "icon_theme": "Test Icons", "font": "Noto Sans 11"}, PNG)
    assert result["settings"]["wallpaper"].startswith("file:///usr/share/backgrounds/")
    assert original.stat().st_mode & 0o777 == 0o640
    backups = json.loads((project / result["backup"] / "files.json").read_text())
    assert "Sans 9" in (project / result["backup"] / backups[SCHEMAS + "/" + OVERRIDE]).read_text()
    for schema, key, expected in (
        ("org.gnome.desktop.interface", "font-name", "Noto Sans 11"),
        ("org.gnome.desktop.interface", "icon-theme", "Test Icons"),
        ("org.gnome.desktop.background", "picture-uri-dark", result["settings"]["wallpaper"]),
    ):
        actual = subprocess.run(["gsettings", "--schemadir", str(root / SCHEMAS), "get", schema, key],
                                env={"PATH": "/usr/bin:/bin", "GSETTINGS_BACKEND": "memory"},
                                check=True, capture_output=True, text=True).stdout
        assert actual.strip() == repr(expected)
    wallpaper = root / result["settings"]["wallpaper"].removeprefix("file:///")
    assert wallpaper.read_bytes() == PNG
    assert wallpaper.parent.stat().st_mode & 0o777 == 0o755


@pytest.mark.parametrize("desktop", ["KDE Plasma", "XFCE"])
def test_desktop_specific_config_preserves_other_settings(tmp_path, desktop):
    project = project_dirs(tmp_path)
    manager = desktop_fixture(project / "rootfs", desktop)
    root = manager.root
    if desktop == "KDE Plasma":
        (root / "etc/xdg/kdeglobals").write_text("[General]\nUnrelated=keep\n[Icons]\nTheme=old\n")
        result = manager.apply(project, {"desktop": desktop, "icon_theme": "Test Icons"}, PNG)
        assert "Unrelated=keep" in (root / "etc/xdg/kdeglobals").read_text()
        script = (root / PLASMA / "updates/zz-pelandukos-builder-wallpaper-00000001.js").read_text()
        assert '["Wallpaper", "org.kde.image", "General"]' in script
        assert result["settings"]["wallpaper"] in script
        manager.apply(project, {"desktop": desktop}, PNG)
        assert (root / PLASMA / "updates/zz-pelandukos-builder-wallpaper-00000002.js").exists()
        assert "gtk_theme" not in manager.capabilities()["desktops"][desktop]["fields"]
    else:
        (root / XFCONF / "xsettings.xml").write_text(
            '<channel name="xsettings" version="1.0"><property name="Unrelated" type="string" value="keep"/></channel>')
        manager.apply(project, {"desktop": desktop, "gtk_theme": "Test Theme", "font": "Sans 12"})
        doc = ET.parse(root / XFCONF / "xsettings.xml")
        assert doc.find("./property[@name='Unrelated']").get("value") == "keep"
        assert doc.find("./property[@name='Gtk']/property[@name='FontName']").get("value") == "Sans 12"
        assert "wallpaper" not in manager.capabilities()["desktops"][desktop]["fields"]
    assert not (root / SCHEMAS / OVERRIDE).exists()


def test_capability_validation_and_symlink_protection(tmp_path, monkeypatch):
    project = project_dirs(tmp_path)
    manager = desktop_fixture(project / "rootfs")
    for values in ({"desktop": "GNOME", "font": "Sans 11\n[evil]"},
                   {"desktop": "GNOME", "icon_theme": "../../host"},
                   {"desktop": "XFCE", "font": "Sans 11"}):
        with pytest.raises(BuilderError):
            manager.apply(project, values)
    with pytest.raises(BuilderError, match="PNG or JPEG"):
        manager.apply(project, {"desktop": "GNOME"}, b"<svg>not allowed</svg>")
    monkeypatch.setattr("core.desktop_manager.shutil.which", lambda tool: None)
    assert manager.capabilities()["desktops"]["GNOME"]["fields"] == []
    monkeypatch.undo()
    outside = tmp_path / "outside"
    outside.mkdir()
    (manager.root / "usr/share/backgrounds").symlink_to(outside)
    with pytest.raises(BuilderError, match="Symlink"):
        manager.apply(project, {"desktop": "GNOME"}, PNG)
    assert list(outside.iterdir()) == []


def test_compile_failure_and_partial_write_rollback(tmp_path, monkeypatch):
    project = project_dirs(tmp_path)
    manager = desktop_fixture(project / "rootfs")
    def fail(*args, **kwargs):
        raise BuilderError("injected compiler failure")
    monkeypatch.setattr(manager.sandbox, "run", fail)
    with pytest.raises(BuilderError, match="compiler"):
        manager.apply(project, {"desktop": "GNOME", "font": "Sans 11"})
    assert not (manager.root / SCHEMAS / OVERRIDE).exists()
    monkeypatch.undo()
    manager = desktop_fixture(project / "rootfs", "KDE Plasma")
    original = manager.root / "etc/xdg/kdeglobals"
    original.write_text("[Icons]\nTheme=Original\n")
    original.chmod(0o640)
    write = manager.write
    calls = []
    def fail_second(target, data):
        calls.append(target)
        if len(calls) == 2:
            raise OSError("injected write failure")
        write(target, data)
    monkeypatch.setattr(manager, "write", fail_second)
    with pytest.raises(OSError, match="write"):
        manager.apply(project, {"desktop": "KDE Plasma", "icon_theme": "Test Icons", "cursor_theme": "Test Icons"})
    assert original.read_text() == "[Icons]\nTheme=Original\n"
    assert original.stat().st_mode & 0o777 == 0o640


@pytest.mark.integration
@pytest.mark.parametrize("confined", [False, True])
def test_desktop_apply_rebuild_and_reopen(engine, fixture_iso, tmp_path, confined):
    def call(request):
        return engine_call(engine.store, engine.user_files, request, lambda event: None) if confined else engine.handle(request)
    project = call({"operation": "create", "name": "Desktop customization"})
    pid = project["id"]
    call({"operation": "import", "project_id": pid, "source": str(fixture_iso)})
    root = engine.store.path(pid) / "rootfs"
    desktop_fixture(root)
    large_status(root)
    image = tmp_path / "wallpaper.png"
    image.write_bytes(PNG)
    assert "GNOME" in call({"operation": "desktop_inspect", "project_id": pid})["desktops"]
    request = {"operation": "desktop_apply", "project_id": pid, "source": str(image),
               "values": {"desktop": "GNOME", "font": "Sans 12"}, "confirmed": True}
    with pytest.raises(BuilderError, match="Confirm"):
        call(request | {"confirmed": False})
    result = call(request)
    assert result["history"][-1]["operation"] == "desktop_apply"
    assert call({"operation": "open", "project_id": pid})["desktop_customization"] == result["desktop_customization"]
    checksum = digest(fixture_iso)
    built = call({"operation": "build", "project_id": pid, "filename": "customized.iso"})
    assert built["last_build"]["validation"]["payload_checksum"]
    assert digest(fixture_iso) == checksum
    assert (root / SCHEMAS / "gschemas.compiled").is_file()
    iso = engine.store.path(pid) / "outputs" / built["last_build"]["build_id"] / "customized.iso"
    squashfs = tmp_path / "rebuilt.squashfs"
    subprocess.run(["xorriso", "-osirrox", "on", "-indev", str(iso), "-extract",
                    "/live/filesystem.squashfs", str(squashfs)], check=True, capture_output=True)
    override = subprocess.run(["unsquashfs", "-cat", str(squashfs), SCHEMAS + "/" + OVERRIDE],
                              check=True, capture_output=True).stdout
    assert b"font-name='Sans 12'" in override
    image_path = result["desktop_customization"]["settings"]["wallpaper"].removeprefix("file:///")
    restored = subprocess.run(["unsquashfs", "-cat", str(squashfs), image_path],
                              check=True, capture_output=True).stdout
    assert restored == PNG
