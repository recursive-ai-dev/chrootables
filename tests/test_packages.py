"""Release artifact contracts shared by DEB, RPM and the host-runtime AppImage."""
import gzip
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import package_support as support


def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


APPIMAGE = module("build_appimage", "build-appimage.py")
PACKAGES = module("build_packages", "build-packages.py")
APPIMAGETOOL = os.environ.get("APPIMAGETOOL") or shutil.which("appimagetool")
RUNTIME = os.environ.get("APPIMAGE_RUNTIME_FILE")
RPM_TOOLS = all(shutil.which(name) for name in ("rpmbuild", "rpm", "rpm2cpio", "cpio"))


class SharedPackagingTests(unittest.TestCase):
    def test_canonical_payload_runs_helper_and_includes_real_license(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stage = support.stage_payload(root / "stage", support.DEFAULT_EPOCH)
            for name in ("chrootable-manager", "chrootables-desktop"):
                executable = stage / "usr/bin" / name
                self.assertTrue(executable.is_symlink())
                self.assertTrue(os.access(executable, os.X_OK))
                self.assertEqual(executable.resolve().parent, stage / "usr/lib/chrootables")
            subprocess.run([str(stage / "usr/bin/chrootable-manager"), "--help"],
                           check=True, capture_output=True)
            broker = stage / "usr/lib/chrootables/chrootables/broker.py"
            store = root / "absent-store"
            result = subprocess.run(
                ["/usr/bin/python3", "-I", "-S", "-B", str(broker), "--store", str(store)],
                input='{"id":1,"action":"doctor"}\n', text=True,
                capture_output=True, check=True, timeout=30)
            response = next(json.loads(line) for line in result.stdout.splitlines()
                            if json.loads(line).get("event") == "result")
            self.assertTrue(response["ok"], response)
            self.assertEqual(response["data"]["schema_version"], 1)
            self.assertFalse(store.exists())
            docs = stage / "usr/share/doc/chrootables"
            for name in ("LICENSE", "LICENSE.txt", "COPYING"):
                if (ROOT / name).is_file():
                    self.assertEqual((docs / "copyright").read_bytes(), (ROOT / name).read_bytes())
                    break
            else:
                self.assertFalse((docs / "copyright").exists())
            self.assertEqual(gzip.decompress((docs / "README.md.gz").read_bytes()),
                             (ROOT / "README.md").read_bytes())
            for path in stage.rglob("*"):
                if not path.is_symlink():
                    expected = 0o755 if path.is_dir() or path.relative_to(stage).as_posix() in support.EXECUTABLES else 0o644
                    self.assertEqual(path.stat().st_mode & 0o777, expected, str(path))

    def test_unknown_license_is_not_invented_and_metadata_needs_terms(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertEqual(support.license_identifier(root), "LicenseRef-Proprietary")
            (root / "packaging").mkdir()
            (root / "packaging/license.spdx").write_text("Apache-2.0\n")
            with self.assertRaisesRegex(ValueError, "requires an upstream LICENSE"):
                support.license_identifier(root)
            (root / "LICENSE").write_text("Actual upstream license terms")
            self.assertEqual(support.license_identifier(root), "Apache-2.0")

    def test_reproducible_payload_archive_preserves_links_modes_and_owners(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archives = []
            for name, mask in (("first", 0o022), ("second", 0o077)):
                previous = os.umask(mask)
                try:
                    stage = support.stage_payload(root / name, support.DEFAULT_EPOCH)
                    archive = root / f"{name}.tar.gz"
                    support.source_archive(stage, archive, support.DEFAULT_EPOCH)
                    archives.append(archive)
                finally:
                    os.umask(previous)
            self.assertEqual(archives[0].read_bytes(), archives[1].read_bytes())
            with tarfile.open(archives[0]) as archive:
                manager = archive.getmember("usr/lib/chrootables/chrootable-manager")
                self.assertEqual(manager.mode, 0o755)
                self.assertEqual((manager.uid, manager.gid), (0, 0))
                self.assertEqual(manager.mtime, support.DEFAULT_EPOCH)
                self.assertTrue(archive.getmember("usr/bin/chrootable-manager").issym())

    def test_publication_checks_both_outputs_before_replacing_either(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "stage").mkdir()
            staged = root / "stage/application.AppImage"
            staged.write_bytes(b"new package")
            destination = root / staged.name
            checksum = destination.with_suffix(".AppImage.sha256")
            checksum.write_text("existing checksum\n")
            with self.assertRaisesRegex(ValueError, "--force"):
                support.publish_artifact(staged, destination)
            self.assertFalse(destination.exists())
            self.assertEqual(checksum.read_text(), "existing checksum\n")
            support.publish_artifact(staged, destination, force=True, executable=True)
            expected = destination.read_bytes(), checksum.read_bytes()
            staged.write_bytes(b"changed package")
            with self.assertRaisesRegex(ValueError, "--force"):
                support.publish_artifact(staged, destination)
            self.assertEqual((destination.read_bytes(), checksum.read_bytes()), expected)
            staged.write_bytes(b"new package")
            support.publish_artifact(staged, destination, executable=True)
            self.assertEqual((destination.read_bytes(), checksum.read_bytes()), expected)
            self.assertEqual(destination.stat().st_mode & 0o777, 0o755)

    def test_publication_rejects_symlink_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "stage").mkdir()
            staged = root / "stage/application.rpm"
            staged.write_bytes(b"new package")
            outside = root / "untouched"
            outside.write_bytes(b"other file")
            destination = root / staged.name
            destination.symlink_to(outside)
            with self.assertRaisesRegex(ValueError, "non-regular"):
                support.publish_artifact(staged, destination, force=True)
            self.assertEqual(outside.read_bytes(), b"other file")
            self.assertTrue(destination.is_symlink())

    def test_missing_build_tools_fail_without_installing_or_creating_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "absent"
            for kind, tool in (("deb", "dpkg-deb"), ("rpm", "rpmbuild"), ("appimage", "appimagetool")):
                environment = {**os.environ, "PATH": ""}
                environment.pop("RPMBUILD", None)
                environment.pop("APPIMAGETOOL", None)
                result = subprocess.run(
                    ["/usr/bin/python3", str(ROOT / f"scripts/build-{kind}.py"), "--output", str(output)],
                    env=environment, text=True, capture_output=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(f"{tool} is required", result.stderr)
                self.assertNotIn("Traceback", result.stderr)
                self.assertFalse(output.exists())

    def test_builders_validate_metadata_before_creating_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "absent"
            for kind in ("deb", "rpm", "appimage"):
                for arguments, epoch, expected in (
                    (("--revision", "../unsafe"), str(support.DEFAULT_EPOCH), "--revision"),
                    ((), "invalid", "SOURCE_DATE_EPOCH"),
                    ((), "9" * 50, "SOURCE_DATE_EPOCH"),
                ):
                    result = subprocess.run(
                        ["/usr/bin/python3", str(ROOT / f"scripts/build-{kind}.py"),
                         "--output", str(output), *arguments],
                        env={**os.environ, "SOURCE_DATE_EPOCH": epoch}, text=True, capture_output=True)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(expected, result.stderr)
                    self.assertNotIn("Traceback", result.stderr)
                    self.assertFalse(output.exists())

    def test_appimage_requires_an_explicit_runtime_before_running_tools(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "absent"
            environment = {**os.environ, "APPIMAGETOOL": "/bin/true"}
            environment.pop("APPIMAGE_RUNTIME_FILE", None)
            result = subprocess.run(
                ["/usr/bin/python3", str(ROOT / "scripts/build-appimage.py"), "--output", str(output)],
                env=environment, text=True, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("pinned runtime is required", result.stderr)
            self.assertFalse(output.exists())

    def test_format_selection_defaults_and_conflicting_options(self):
        choices = (([], ("deb", "rpm")), (["--all"], ("deb", "rpm", "appimage")),
                   (["--appimage-only"], ("appimage",)), (["--rpm-only"], ("rpm",)),
                   (["--deb-only"], ("deb",)))
        for arguments, expected in choices:
            with patch.object(sys, "argv", ["build-packages.py", *arguments]), \
                    patch.object(PACKAGES.subprocess, "run") as run:
                run.return_value.returncode = 0
                self.assertEqual(PACKAGES.main(), 0)
                actual = tuple(Path(call.args[0][1]).name for call in run.call_args_list)
                self.assertEqual(actual, tuple(f"build-{kind}.py" for kind in expected))
        result = subprocess.run([sys.executable, str(ROOT / "scripts/build-packages.py"),
                                 "--deb-only", "--rpm-only"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)


class ArtifactTests:
    kind = None
    pattern = None

    def build(self, output, *arguments, umask=0o022, check=True):
        return subprocess.run(
            ["/usr/bin/python3", str(ROOT / f"scripts/build-{self.kind}.py"),
             "--output", str(output), *arguments],
            env={**os.environ, "SOURCE_DATE_EPOCH": str(support.DEFAULT_EPOCH)},
            umask=umask, check=check, capture_output=True, text=True)

    def test_reproducible_and_overwrite_protected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.build(root / "first", umask=0o022)
            self.build(root / "second with spaces", umask=0o077)
            first, = (root / "first").glob(self.pattern)
            second, = (root / "second with spaces").glob(self.pattern)
            self.assertEqual(first.read_bytes(), second.read_bytes())
            checksum = first.with_suffix(first.suffix + ".sha256")
            previous_checksum = checksum.read_bytes()
            first.write_bytes(b"previous artifact")
            result = self.build(root / "first", check=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("--force", result.stderr)
            self.assertEqual(first.read_bytes(), b"previous artifact")
            self.assertEqual(checksum.read_bytes(), previous_checksum)
            self.build(root / "first", "--force")
            self.assertEqual(first.read_bytes(), second.read_bytes())
            self.build(root / "first")
            self.assertFalse(list((root / "first").glob(".chrootables-*")))


@unittest.skipUnless(RPM_TOOLS, "requires rpmbuild, rpm, rpm2cpio and cpio")
class RpmPackageTests(ArtifactTests, unittest.TestCase):
    kind = "rpm"
    pattern = "*.rpm"

    def test_real_metadata_layout_and_installed_smoke(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.build(root)
            package, = root.glob(self.pattern)
            metadata = subprocess.check_output(
                ["rpm", "--dbpath", str(root / "rpmdb"), "-qp", "--queryformat",
                 "%{VERSION} %{ARCH} %{LICENSE} %{BUILDHOST} %{BUILDTIME}", str(package)], text=True)
            self.assertEqual(metadata, f"{support.upstream_version()} noarch {support.license_identifier()} reproducible {support.DEFAULT_EPOCH}")
            files = subprocess.check_output(["rpm2cpio", str(package)])
            stage = root / "installed"
            stage.mkdir()
            subprocess.run(["cpio", "-idm", "--quiet", "--no-absolute-filenames", "--no-preserve-owner"],
                           input=files, cwd=stage, check=True, capture_output=True)
            subprocess.run(["/usr/bin/python3", str(ROOT / "scripts/check-installed.py"),
                            "--prefix", str(stage / "usr")], check=True, capture_output=True, timeout=45)


@unittest.skipUnless(APPIMAGETOOL and RUNTIME, "requires APPIMAGETOOL and APPIMAGE_RUNTIME_FILE")
class AppImagePackageTests(ArtifactTests, unittest.TestCase):
    kind = "appimage"
    pattern = "*.AppImage"

    def test_real_extraction_apprun_and_installed_smoke(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.build(root)
            package, = root.glob(self.pattern)
            self.assertEqual(package.stat().st_mode & 0o777, 0o755)
            environment = {**os.environ, "QT_QPA_PLATFORM": "offscreen",
                           "APPIMAGE_EXTRACT_AND_RUN": "1"}
            version = subprocess.check_output([str(package), "--version"],
                                              env=environment, text=True, timeout=30).strip()
            self.assertEqual(version, support.upstream_version())
            subprocess.run([str(package), "--appimage-extract"], cwd=root, check=True, capture_output=True)
            stage = root / "squashfs-root"
            if (ROOT / "LICENSE").exists():
                self.assertEqual((stage / "LICENSE").read_bytes(), (ROOT / "LICENSE").read_bytes())
            subprocess.run(["/usr/bin/python3", str(ROOT / "scripts/check-installed.py"),
                            "--prefix", str(stage / "usr")], check=True, capture_output=True, timeout=45)
            screenshot = root / "runtime-demo.png"
            subprocess.run([str(package), "--demo", "--screenshot", str(screenshot)],
                           env=environment, check=True, capture_output=True, timeout=30)
            self.assertGreater(screenshot.stat().st_size, 0)


if __name__ == "__main__":
    unittest.main()
