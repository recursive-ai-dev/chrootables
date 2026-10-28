"""Verify the real Debian archive without installing or requiring root."""
import gzip
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location("build_deb", ROOT / "scripts/build-deb.py")
BUILDER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILDER)


@unittest.skipUnless(shutil.which("dpkg-deb"), "requires Debian packaging tools")
class DebianPackageTests(unittest.TestCase):
    def build(self, output, *arguments, umask=0o022, environment=None, check=True):
        return subprocess.run(
            ["/usr/bin/python3", str(ROOT / "scripts/build-deb.py"), "--output", str(output), *arguments],
            env={**os.environ, "SOURCE_DATE_EPOCH": "1793059200", **(environment or {})},
            umask=umask, check=check, capture_output=True, text=True)

    def test_installed_layout_and_read_only_helper(self):
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            self.build(temporary, umask=0o077)
            package, = temporary.glob("*.deb")
            self.assertEqual(package.name, f"chrootables_{BUILDER.upstream_version()}-1_all.deb")
            listing = subprocess.check_output(["dpkg-deb", "--contents", str(package)], text=True)
            self.assertTrue(listing.startswith("drwxr-xr-x root/root"), listing)
            self.assertNotIn("__pycache__", listing)
            self.assertNotIn(".pyc", listing)
            stage = temporary / "installed"
            subprocess.run(["dpkg-deb", "--raw-extract", str(package), str(stage)], check=True)
            launcher = stage / "usr/bin/chrootables-desktop"
            self.assertTrue(launcher.is_file())
            self.assertEqual(launcher.read_text().splitlines()[0], "#!/usr/bin/python3 -I")
            manager = stage / "usr/bin/chrootable-manager"
            self.assertEqual(manager.read_text().splitlines()[0], "#!/bin/bash")
            subprocess.run([str(manager), "--help"], check=True, capture_output=True)
            broker = stage / "usr/lib/chrootables/chrootables/broker.py"
            result = subprocess.run(["/usr/bin/python3", "-I", "-S", "-B", str(broker),
                                     "--store", str(temporary / "store")],
                                    input="", text=True, capture_output=True, check=True)
            ready = json.loads(result.stdout.splitlines()[0])
            self.assertEqual(ready["event"], "ready")
            self.assertFalse(ready["writable"])
            for line in (stage / "DEBIAN/md5sums").read_text().splitlines():
                expected, relative = line.split("  ", 1)
                self.assertEqual(hashlib.md5((stage / relative).read_bytes(),
                                            usedforsecurity=False).hexdigest(), expected, relative)
            for path in stage.rglob("*"):
                if not path.is_symlink():
                    expected = 0o755 if path.is_dir() or path.name in (
                        "chrootables-desktop", "chrootable-manager") else 0o644
                    self.assertEqual(path.stat().st_mode & 0o777, expected, str(path))
            docs = stage / "usr/share/doc/chrootables"
            self.assertEqual(gzip.decompress((docs / "changelog.gz").read_bytes()),
                             (ROOT / "CHANGELOG.md").read_bytes())
            self.assertIn(f"({BUILDER.upstream_version()}-1)", gzip.decompress(
                (docs / "changelog.Debian.gz").read_bytes()).decode())
            for name in ("chrootables-desktop", "chrootable-manager"):
                manual = stage / f"usr/share/man/man1/{name}.1.gz"
                self.assertIn(".SH SYNOPSIS", gzip.decompress(manual.read_bytes()).decode())
            checksum = package.with_suffix(".deb.sha256").read_text()
            self.assertEqual(checksum, f"{hashlib.sha256(package.read_bytes()).hexdigest()}  {package.name}\n")

    def test_reproducible_across_build_directories_and_umasks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.build(root / "first", umask=0o022)
            self.build(root / "second", umask=0o077)
            first, = (root / "first").glob("*.deb")
            second, = (root / "second").glob("*.deb")
            self.assertEqual(first.read_bytes(), second.read_bytes())
            self.assertEqual(first.stat().st_mode & 0o777, 0o644)
            self.assertEqual(second.stat().st_mode & 0o777, 0o644)

    def test_existing_different_package_is_preserved_unless_forced(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.build(root)
            package, = root.glob("*.deb")
            package.write_bytes(b"existing artifact")
            result = self.build(root, check=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("--force", result.stderr)
            self.assertEqual(package.read_bytes(), b"existing artifact")
            self.assertFalse(list(root.glob(".chrootables-build-*")))
            self.build(root, "--force")
            subprocess.run(["dpkg-deb", "--info", str(package)], check=True, capture_output=True)
            # Repeating an identical build is safe without --force.
            self.build(root)

    def test_revision_and_input_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.build(root, "--revision", "2~test1")
            package, = root.glob("*.deb")
            version = subprocess.check_output(["dpkg-deb", "--field", str(package), "Version"], text=True).strip()
            self.assertEqual(version, f"{BUILDER.upstream_version()}-2~test1")
            for args, environment, expected in (
                (("--revision", "../unsafe"), {}, "--revision"),
                ((), {"SOURCE_DATE_EPOCH": "invalid"}, "SOURCE_DATE_EPOCH"),
                ((), {"SOURCE_DATE_EPOCH": "9" * 50}, "SOURCE_DATE_EPOCH"),
                ((), {"PATH": ""}, "dpkg-deb is required"),
            ):
                result = self.build(root, *args, environment=environment, check=False)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(expected, result.stderr)
                self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
