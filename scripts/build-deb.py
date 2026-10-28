#!/usr/bin/python3
"""Build a reproducible Debian package with Python and dpkg-deb; no root needed."""
import argparse
from email.utils import format_datetime
import hashlib
import os
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
from package_support import (ROOT, build_metadata, compressed_copy, normalize_tree,
                             publish_artifact, require_tool, run_tool, stage_payload,
                             upstream_version)


def build(output, revision="1", force=False):
    version, epoch, date = build_metadata(revision)
    tool = require_tool("dpkg-deb", hint="On Debian/Ubuntu install the dpkg package.")
    version = f"{version}-{revision}"
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    package = output / f"chrootables_{version}_all.deb"
    with tempfile.TemporaryDirectory(prefix=".chrootables-build-", dir=output) as temporary:
        work = Path(temporary)
        stage = stage_payload(work / "stage", epoch)
        changelog = stage / "usr/share/doc/chrootables/changelog.Debian"
        changelog.write_text(
            f"chrootables ({version}) unstable; urgency=medium\n\n"
            "  * Package the native desktop and CLI with distribution dependencies.\n"
            "  * Include manual pages and reproducible build metadata.\n\n"
            f" -- Chrootables contributors <chrootables@users.noreply.github.com>  {format_datetime(date)}\n",
            encoding="utf-8")
        compressed_copy(changelog, changelog.with_suffix(".Debian.gz"))
        changelog.unlink()
        payload = sorted(path for path in stage.rglob("*")
                         if path.is_file() and not path.is_symlink())
        size = sum((path.stat().st_size + 1023) // 1024 for path in payload)
        control = stage / "DEBIAN"
        control.mkdir()
        (control / "control").write_text(f"""Package: chrootables
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
        (control / "md5sums").write_text("".join(
            f"{hashlib.md5(path.read_bytes(), usedforsecurity=False).hexdigest()}  {path.relative_to(stage)}\n"
            for path in payload), encoding="utf-8")
        normalize_tree(stage, epoch)
        staged = work / package.name
        run_tool([tool, "--root-owner-group", "--uniform-compression", "-Zxz", "-z6",
                  "--threads-max=1", "--build", str(stage), str(staged)],
                 env={**os.environ, "SOURCE_DATE_EPOCH": str(epoch)})
        return publish_artifact(staged, package, force=force)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist", help="artifact directory (default: dist)")
    parser.add_argument("--revision", default="1", help="Debian revision appended to the application version")
    parser.add_argument("--force", action="store_true", help="replace an existing package whose contents differ")
    args = parser.parse_args()
    try:
        print(build(args.output, args.revision, args.force))
    except (ValueError, OSError) as error:
        print(f"Build failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
