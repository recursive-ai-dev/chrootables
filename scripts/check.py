#!/usr/bin/env python3
"""Run local syntax, tests, demos, and an optional Debian package build."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--require-desktop-integration", action="store_true",
                        help="fail unless Gio and desktop-file-validate are available")
    parser.add_argument("--build-deb", action="store_true",
                        help="require dpkg-deb and build a package in a temporary directory")
    args = parser.parse_args()
    if args.require_desktop_integration:
        try:
            import gi
        except ImportError:
            parser.error("Desktop integration requires PyGObject in this Python environment.")
        if not shutil.which("desktop-file-validate"):
            parser.error("Desktop integration requires desktop-file-utils.")
    if args.build_deb and not shutil.which("dpkg-deb"):
        parser.error("The Debian build requires dpkg-deb.")

    environment = {**os.environ, "QT_QPA_PLATFORM": "offscreen"}

    def run(*command):
        print("+ " + " ".join(map(str, command)), flush=True)
        subprocess.run(command, cwd=ROOT, env=environment, check=True)

    run("bash", "-n", "chrootable-manager")
    run("bash", "-n", "tests/integration.sh")
    run(sys.executable, "-m", "compileall", "-q", "chrootables", "scripts", "tests")
    run(sys.executable, "-m", "py_compile", "chrootables-desktop")
    run(sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py", "-v")
    run(sys.executable, "scripts/benchmark.py")
    run(str(ROOT / "chrootable-manager"), "--demo", "--dashboard")
    with tempfile.TemporaryDirectory(prefix="chrootables-check-") as directory:
        screenshot = Path(directory) / "desktop.png"
        run(sys.executable, "chrootables-desktop", "--demo", "--screenshot", str(screenshot))
        if not screenshot.is_file() or not screenshot.stat().st_size:
            raise RuntimeError("Desktop demo did not produce a screenshot.")
        if args.build_deb or shutil.which("dpkg-deb"):
            run(sys.executable, "scripts/build-deb.py", "--output", directory)
        else:
            print("SKIP: Debian package build (dpkg-deb is unavailable).")
    print("Verification completed.")


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as error:
        sys.exit(error.returncode)
