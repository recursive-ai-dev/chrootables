#!/usr/bin/python3
"""Build Debian and RPM packages, or select individual formats / --all for AppImage."""
import argparse
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist", help="artifact directory (default: dist)")
    parser.add_argument("--revision", default="1", help="revision appended to the application version")
    parser.add_argument("--force", action="store_true", help="replace existing artifacts whose contents differ")
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--deb-only", action="store_const", dest="formats", const=("deb",))
    selection.add_argument("--rpm-only", action="store_const", dest="formats", const=("rpm",))
    selection.add_argument("--appimage-only", action="store_const", dest="formats", const=("appimage",))
    selection.add_argument("--all", action="store_const", dest="formats", const=("deb", "rpm", "appimage"))
    parser.set_defaults(formats=("deb", "rpm"))
    args = parser.parse_args()
    failures = []
    for kind in args.formats:
        command = [sys.executable, str(ROOT / f"scripts/build-{kind}.py"),
                   "--output", str(args.output), "--revision", args.revision]
        if args.force:
            command.append("--force")
        print(f"Building {kind}...", flush=True)
        if subprocess.run(command).returncode:
            failures.append(kind)
    if failures:
        print("Failed package formats: " + ", ".join(failures), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
