#!/usr/bin/python3
"""Build a Debian binary package using only Python and dpkg-deb; no root needed."""
import argparse
from pathlib import Path
import shutil
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parent.parent


def build(output):
    version = "3.1.0-1"
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    package = output / f"chrootables_{version}_all.deb"
    with tempfile.TemporaryDirectory(prefix="chrootables-deb-") as temporary:
        stage = Path(temporary)
        stage.chmod(0o755)
        app = stage / "usr/lib/chrootables"
        app.mkdir(parents=True)
        shutil.copytree(ROOT / "chrootables", app / "chrootables",
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        shutil.copytree(ROOT / "assets", app / "assets")
        for name in ("chrootable-manager", "chrootables-desktop"):
            target = app / name
            source = (ROOT / name).read_text()
            # Use distro interpreters regardless of Homebrew/venv PATH settings.
            interpreter = "/usr/bin/python3 -I" if name.endswith("desktop") else "/bin/bash"
            target.write_text(f"#!{interpreter}\n" + source.split("\n", 1)[1])
            target.chmod(0o755)
            link = stage / "usr/bin" / name
            link.parent.mkdir(parents=True, exist_ok=True)
            link.symlink_to(f"../lib/chrootables/{name}")
        files = {
            "packaging/io.github.chrootables.Desktop.desktop": "usr/share/applications/io.github.chrootables.Desktop.desktop",
            "assets/io.github.chrootables.Desktop.svg": "usr/share/icons/hicolor/scalable/apps/io.github.chrootables.Desktop.svg",
            "README.md": "usr/share/doc/chrootables/README.md",
            "CHANGELOG.md": "usr/share/doc/chrootables/CHANGELOG.md",
        }
        for source, destination in files.items():
            target = stage / destination
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / source, target)
        # Normalize permissions independent of the builder's umask.
        for path in stage.rglob("*"):
            if path.is_symlink():
                continue
            path.chmod(0o755 if path.is_dir() or path.name in
                       ("chrootable-manager", "chrootables-desktop") else 0o644)
        size = sum(p.stat().st_size for p in stage.rglob("*") if p.is_file())
        control = stage / "DEBIAN/control"
        control.parent.mkdir(mode=0o755)
        control.write_text(f"""Package: chrootables
Version: {version}
Section: devel
Priority: optional
Architecture: all
Maintainer: Chrootables contributors <chrootables@users.noreply.github.com>
Installed-Size: {(size + 1023) // 1024}
Depends: python3 (>= 3.10), python3-pyside6.qtcore (>= 6.8), python3-pyside6.qtgui (>= 6.8), python3-pyside6.qtwidgets (>= 6.8), bash, coreutils, util-linux, mount, e2fsprogs, debootstrap, debian-archive-keyring, ca-certificates, dpkg, findutils, gpgv, gzip, hostname, iproute2, mawk | gawk, pkexec, sudo, tar, wget, whiptail, xz-utils
Recommends: polkitd, x-terminal-emulator
Description: Desktop workbench for local Debian image environments
 Create Debian workspaces, open terminals, use disposable sessions,
 and manage snapshots using a native Qt desktop or a Bash interface.
""")
        control.chmod(0o644)
        subprocess.run(["dpkg-deb", "--root-owner-group", "--build", str(stage), str(package)], check=True)
    return package


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    print(build(args.output))
