#!/usr/bin/env python3
"""Build a Debian binary package without installing anything on the host."""
import argparse
import os
import shutil
import subprocess
import tempfile
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("dist"))
    parser.add_argument("--stage", type=Path, help="Stage payload for a Debian source build")
    args = parser.parse_args()
    source = Path(__file__).resolve().parents[1]
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="pelandukos-deb-") as temporary:
        root = Path(temporary) / "package"
        app = root / "usr/lib/pelandukos-iso-builder/app"
        app.mkdir(parents=True)
        for directory in ("core", "services", "gui", "adapters", "resources"):
            shutil.copytree(source / directory, app / directory,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        shutil.copyfile(source / "main.py", app / "main.py")
        files = {
            "packaging/control": "DEBIAN/control",
            "packaging/helper": "usr/lib/pelandukos-iso-builder/helper",
            "packaging/launcher": "usr/bin/pelandukos-iso-builder",
            "packaging/org.tunnelbiz.pelandukos.manage.policy":
                "usr/share/polkit-1/actions/org.tunnelbiz.pelandukos.manage.policy",
            "packaging/pelandukos-iso-builder.desktop":
                "usr/share/applications/pelandukos-iso-builder.desktop",
            "resources/pelandukos-iso-builder.svg":
                "usr/share/icons/hicolor/scalable/apps/pelandukos-iso-builder.svg",
            "README.md": "usr/share/doc/pelandukos-iso-builder/README.md",
            "docs/INSTALL.md": "usr/share/doc/pelandukos-iso-builder/INSTALL.md",
            "docs/SECURITY.md": "usr/share/doc/pelandukos-iso-builder/SECURITY.md",
            "LICENSE": "usr/share/doc/pelandukos-iso-builder/copyright",
            "COPYING": "usr/share/doc/pelandukos-iso-builder/COPYING",
        }
        for original, target in files.items():
            target = root / target
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source / original, target)
        documentation = root / "usr/share/doc/pelandukos-iso-builder"
        shutil.copytree(source / "docs", documentation / "docs")
        shutil.copyfile(source / "DEVELOPMENT_STATUS.md", documentation / "DEVELOPMENT_STATUS.md")
        store = root / "var/lib/pelandukos-iso-builder"
        store.mkdir(parents=True)
        for base, directories, filenames in os.walk(root):
            Path(base).chmod(0o755)
            for name in filenames:
                (Path(base) / name).chmod(0o644)
        store.chmod(0o700)
        for relative in ("usr/bin/pelandukos-iso-builder", "usr/lib/pelandukos-iso-builder/helper"):
            (root / relative).chmod(0o755)
        if args.stage:
            shutil.copytree(root, args.stage.resolve(), dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns("DEBIAN"))
            return
        version = next(line.split(": ", 1)[1] for line in (source / "packaging/control").read_text().splitlines() if line.startswith("Version: "))
        destination = output / f"pelandukos-iso-builder_{version}_amd64.deb"
        subprocess.run(["dpkg-deb", "--root-owner-group", "--build", str(root), str(destination)], check=True)
        print(destination)


if __name__ == "__main__":
    main()
