#!/usr/bin/python3
"""Build a reproducible Debian package with Python and dpkg-deb; no root needed."""
import argparse
import ast
from datetime import datetime, timezone
from email.utils import format_datetime
import gzip
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
# A stable release date makes builds reproducible even outside a Git checkout.
DEFAULT_EPOCH = 1793059200  # 2026-10-27 00:00:00 UTC


def upstream_version():
    """Read the version without importing Qt or executing source code."""
    tree = ast.parse((ROOT / "chrootables/__init__.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == "__version__"
                for target in node.targets):
            value = ast.literal_eval(node.value)
            if isinstance(value, str) and re.fullmatch(r"[0-9][0-9A-Za-z.+~]*", value):
                return value
    raise ValueError("chrootables/__init__.py must define a valid __version__.")


def compressed_copy(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("wb") as output:
        # No original filename or wall-clock timestamp in the gzip header.
        with gzip.GzipFile(filename="", mode="wb", fileobj=output,
                           compresslevel=9, mtime=0) as compressed:
            compressed.write(source.read_bytes())


def build(output, revision="1", force=False):
    if not shutil.which("dpkg-deb"):
        raise ValueError("dpkg-deb is required. On Debian/Ubuntu install the dpkg package.")
    if not re.fullmatch(r"[0-9][0-9A-Za-z.+~]*", revision):
        raise ValueError("--revision must start with a digit and contain only letters, digits, ., + or ~.")
    epoch_text = os.environ.get("SOURCE_DATE_EPOCH", str(DEFAULT_EPOCH))
    if not re.fullmatch(r"[0-9]+", epoch_text):
        raise ValueError("SOURCE_DATE_EPOCH must be a nonnegative integer timestamp.")
    epoch = int(epoch_text)
    try:
        date = format_datetime(datetime.fromtimestamp(epoch, timezone.utc))
    except (OverflowError, OSError, ValueError) as error:
        raise ValueError("SOURCE_DATE_EPOCH is outside the supported timestamp range.") from error
    version = f"{upstream_version()}-{revision}"
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    package = output / f"chrootables_{version}_all.deb"
    checksum = package.with_suffix(package.suffix + ".sha256")
    # Stage on the output filesystem so publishing the finished package is atomic.
    with tempfile.TemporaryDirectory(prefix=".chrootables-build-", dir=output) as temporary:
        work = Path(temporary)
        stage = work / "stage"
        app = stage / "usr/lib/chrootables"
        app.mkdir(parents=True)
        shutil.copytree(ROOT / "chrootables", app / "chrootables",
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"))
        shutil.copytree(ROOT / "assets", app / "assets")
        for name in ("chrootable-manager", "chrootables-desktop"):
            target = app / name
            source = (ROOT / name).read_text(encoding="utf-8")
            interpreter = "/usr/bin/python3 -I" if name.endswith("desktop") else "/bin/bash"
            target.write_text(f"#!{interpreter}\n" + source.split("\n", 1)[1], encoding="utf-8")
            link = stage / "usr/bin" / name
            link.parent.mkdir(parents=True, exist_ok=True)
            link.symlink_to(f"../lib/chrootables/{name}")
        files = {
            "packaging/io.github.chrootables.Desktop.desktop": "usr/share/applications/io.github.chrootables.Desktop.desktop",
            "assets/io.github.chrootables.Desktop.svg": "usr/share/icons/hicolor/scalable/apps/io.github.chrootables.Desktop.svg",
        }
        for source, destination in files.items():
            target = stage / destination
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / source, target)
        compressed_copy(ROOT / "README.md", stage / "usr/share/doc/chrootables/README.md.gz")
        compressed_copy(ROOT / "CHANGELOG.md", stage / "usr/share/doc/chrootables/changelog.gz")
        for name in ("chrootables-desktop", "chrootable-manager"):
            compressed_copy(ROOT / f"packaging/{name}.1", stage / f"usr/share/man/man1/{name}.1.gz")
        debian_changelog = stage / "usr/share/doc/chrootables/changelog.Debian"
        debian_changelog.write_text(
            f"chrootables ({version}) unstable; urgency=medium\n\n"
            "  * Package the native desktop and CLI with distribution dependencies.\n"
            "  * Include manual pages and reproducible build metadata.\n\n"
            f" -- Chrootables contributors <chrootables@users.noreply.github.com>  {date}\n",
            encoding="utf-8")
        compressed_copy(debian_changelog, debian_changelog.with_suffix(".Debian.gz"))
        debian_changelog.unlink()
        # Include a real upstream license if the project gains one; never invent one.
        for filename in ("LICENSE", "LICENSE.txt", "COPYING"):
            if (ROOT / filename).is_file():
                shutil.copyfile(ROOT / filename, stage / "usr/share/doc/chrootables/copyright")
                break
        payload = sorted(path for path in stage.rglob("*")
                         if path.is_file() and not path.is_symlink())
        size = sum((path.stat().st_size + 1023) // 1024 for path in payload)
        control_dir = stage / "DEBIAN"
        control_dir.mkdir()
        (control_dir / "control").write_text(f"""Package: chrootables
Version: {version}
Section: devel
Priority: optional
Architecture: all
Maintainer: Chrootables contributors <chrootables@users.noreply.github.com>
Installed-Size: {size}
Depends: python3 (>= 3.10), python3-pyside6.qtcore (>= 6.8), python3-pyside6.qtgui (>= 6.8), python3-pyside6.qtwidgets (>= 6.8), bash, coreutils, util-linux, mount, e2fsprogs, debootstrap, debian-archive-keyring, ca-certificates, dpkg, findutils, gpgv, gzip, hostname, iproute2, mawk | gawk, pkexec, sudo, tar, wget, whiptail, xz-utils
Recommends: polkitd, x-terminal-emulator
Description: Desktop workbench for local Debian image environments
 Create Debian workspaces, open terminals, use disposable sessions,
 and manage snapshots using a native Qt desktop or a Bash interface.
""", encoding="utf-8")
        (control_dir / "md5sums").write_text("".join(
            f"{hashlib.md5(path.read_bytes(), usedforsecurity=False).hexdigest()}  {path.relative_to(stage)}\n"
            for path in payload), encoding="utf-8")
        # Normalize all permissions and timestamps, including symlinks/directories.
        for path in [stage, *stage.rglob("*")]:
            if not path.is_symlink():
                path.chmod(0o755 if path.is_dir() or path.name in
                           ("chrootable-manager", "chrootables-desktop") else 0o644)
            os.utime(path, (epoch, epoch), follow_symlinks=False)
        staged_package = work / package.name
        environment = {**os.environ, "SOURCE_DATE_EPOCH": str(epoch)}
        subprocess.run(["dpkg-deb", "--root-owner-group", "--uniform-compression",
                        "-Zxz", "-z6", "--threads-max=1", "--build", str(stage),
                        str(staged_package)], env=environment, check=True)
        digest = hashlib.sha256(staged_package.read_bytes()).hexdigest()
        if package.exists() and not force:
            if hashlib.sha256(package.read_bytes()).hexdigest() != digest:
                raise ValueError(f"{package} already exists with different contents; use --force to replace it.")
        else:
            staged_package.chmod(0o644)
            os.replace(staged_package, package)
        staged_checksum = work / checksum.name
        staged_checksum.write_text(f"{digest}  {package.name}\n", encoding="ascii")
        staged_checksum.chmod(0o644)
        os.replace(staged_checksum, checksum)
    return package


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist", help="artifact directory (default: dist)")
    parser.add_argument("--revision", default="1", help="Debian revision appended to the application version")
    parser.add_argument("--force", action="store_true", help="replace an existing package whose contents differ")
    args = parser.parse_args()
    try:
        print(build(args.output, args.revision, args.force))
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        print(f"Build failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
