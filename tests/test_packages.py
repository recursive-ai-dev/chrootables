"""Release artifact contracts shared by DEB, RPM and portable AppImages."""
import gzip
import importlib.util
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
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
PORTABLE_RUNTIME = os.environ.get("CHROOTABLE_APPIMAGE_RUNTIME")
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

    def test_oversized_checksum_is_rejected_without_reading_it(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "stage").mkdir()
            staged = root / "stage/application.rpm"
            staged.write_bytes(b"new package")
            destination = root / staged.name
            checksum = destination.with_suffix(".rpm.sha256")
            with checksum.open("wb") as stream:
                stream.truncate(1024 ** 4)
            original_open = Path.open

            def bounded_open(path, *args, **kwargs):
                if path == checksum:
                    self.fail("Oversized sidecar must not be read")
                return original_open(path, *args, **kwargs)

            with patch.object(Path, "open", bounded_open):
                with self.assertRaisesRegex(ValueError, "--force"):
                    support.publish_artifact(staged, destination)
            self.assertFalse(destination.exists())
            self.assertEqual(checksum.stat().st_size, 1024 ** 4)
            support.publish_artifact(staged, destination, force=True)
            self.assertEqual(destination.read_bytes(), b"new package")
            self.assertLess(checksum.stat().st_size, 256)

    def test_identical_publication_restores_executable_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "stage").mkdir()
            staged = root / "stage/application.AppImage"
            staged.write_bytes(b"package")
            destination = root / staged.name
            support.publish_artifact(staged, destination, executable=True)
            checksum = destination.with_suffix(".AppImage.sha256")
            expected = checksum.read_bytes()
            destination.chmod(0o644)
            staged.write_bytes(b"package")
            support.publish_artifact(staged, destination, executable=True)
            self.assertEqual(destination.stat().st_mode & 0o777, 0o755)
            self.assertEqual(destination.read_bytes(), b"package")
            self.assertEqual(checksum.read_bytes(), expected)

    def test_publication_flushes_contents_before_rename_and_directory_after(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "stage").mkdir()
            staged = root / "stage/application.rpm"
            staged.write_bytes(b"package")
            events = []
            original_sync, original_replace = os.fsync, os.replace

            def sync(descriptor):
                events.append("sync")
                original_sync(descriptor)

            def replace(source, target):
                events.append("rename")
                original_replace(source, target)

            with patch.object(support.os, "fsync", side_effect=sync), \
                    patch.object(support.os, "replace", side_effect=replace):
                support.publish_artifact(staged, root / staged.name)
            self.assertEqual(events, ["sync", "sync", "rename", "rename", "sync"])

    def test_flush_failure_keeps_existing_outputs_untouched(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "stage").mkdir()
            staged = root / "stage/application.rpm"
            staged.write_bytes(b"new package")
            destination = root / staged.name
            destination.write_bytes(b"old package")
            checksum = destination.with_suffix(".rpm.sha256")
            checksum.write_bytes(b"old checksum")
            with patch.object(support.os, "fsync", side_effect=OSError("disk failure")):
                with self.assertRaisesRegex(OSError, "disk failure"):
                    support.publish_artifact(staged, destination, force=True)
            self.assertEqual(destination.read_bytes(), b"old package")
            self.assertEqual(checksum.read_bytes(), b"old checksum")

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
                    ((), "4294967296", "SOURCE_DATE_EPOCH"),
                ):
                    result = subprocess.run(
                        ["/usr/bin/python3", str(ROOT / f"scripts/build-{kind}.py"),
                         "--output", str(output), *arguments],
                        env={**os.environ, "SOURCE_DATE_EPOCH": epoch}, text=True, capture_output=True)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(expected, result.stderr)
                    self.assertNotIn("Traceback", result.stderr)
                    self.assertFalse(output.exists())

    def test_distribution_timestamp_boundaries(self):
        for value in ("0", "4294967295"):
            with self.subTest(value=value), patch.dict(os.environ, SOURCE_DATE_EPOCH=value):
                self.assertEqual(support.build_metadata("1")[1], int(value))

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

    def test_bundled_appimage_requires_pinned_python_qt_before_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = root / "runtime"
            runtime.write_bytes(b"test runtime")
            environment = {**os.environ, "APPIMAGETOOL": "/bin/true", "APPIMAGE_RUNTIME_FILE": str(runtime)}
            environment.pop("CHROOTABLE_APPIMAGE_RUNTIME", None)
            output = root / "output"
            result = subprocess.run(
                [sys.executable, str(ROOT / "scripts/build-appimage.py"), "--output", str(output)],
                env=environment, text=True, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Python/Qt runtime cache is required", result.stderr)
            self.assertFalse(output.exists())

    def test_explicit_host_runtime_retains_version_checks(self):
        with tempfile.TemporaryDirectory() as directory:
            stage = APPIMAGE.stage_appimage(Path(directory) / "AppDir", support.DEFAULT_EPOCH)
            launcher = (stage / "AppRun").read_text()
            self.assertIn("sys.version_info < (3, 10)", launcher)
            self.assertIn("PySide6.__version_info__ < (6, 8)", launcher)
            self.assertFalse((stage / "usr/lib/chrootables/runtime").exists())

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


class AppImageLauncherTests(unittest.TestCase):
    def fixture(self, root):
        launcher = root / "AppRun"
        launcher.write_text(APPIMAGE.APPRUN)
        launcher.chmod(0o755)
        image = root / "fake image"
        image.write_text('''#!/usr/bin/python3
import os, pathlib, shutil, sys
assert sys.argv[1:] == ["--appimage-extract"]
if os.environ.get("EXTRACT_STATUS"):
    raise SystemExit(int(os.environ["EXTRACT_STATUS"]))
stage = pathlib.Path("squashfs-root")
(stage / "usr/bin").mkdir(parents=True)
shutil.copy(os.environ["APPRUN_TEMPLATE"], stage / "AppRun")
shutil.copy(os.environ["DESKTOP_TEMPLATE"], stage / "usr/bin/chrootables-desktop")
''')
        image.chmod(0o755)
        desktop = root / "desktop"
        desktop.write_text('''#!/usr/bin/python3
import json, os, pathlib, sys, time
work = pathlib.Path(__file__).resolve().parents[3]
print(json.dumps({"work":str(work), "session":os.environ.get("CHROOTABLE_APPIMAGE_SESSION_DIR"), "marker":os.environ.get("CHROOTABLE_APPIMAGE_EXTRACTED"), "args":sys.argv[1:]}), flush=True)
if os.environ.get("WAIT_TEST"):
    pathlib.Path(os.environ["READY_PATH"]).write_text(str(work))
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if os.environ.get("GATE_PATH") and pathlib.Path(os.environ["GATE_PATH"]).exists():
            break
        time.sleep(0.01)
if os.environ.get("OUTSIDE_TARGET"):
    (work / "outside").symlink_to(os.environ["OUTSIDE_TARGET"])
if os.environ.get("DETACHED_TEST"):
    pathlib.Path(os.environ["CHROOTABLE_APPIMAGE_SESSION_DIR"], ".retain-runtime").touch()
raise SystemExit(int(os.environ.get("DESKTOP_STATUS", "0")))
''')
        desktop.chmod(0o755)
        environment = {**os.environ, "APPIMAGE": str(image), "TMPDIR": str(root),
                       "APPRUN_TEMPLATE": str(launcher), "DESKTOP_TEMPLATE": str(desktop)}
        for key in ("CHROOTABLE_APPIMAGE_EXTRACTED", "APPIMAGE_EXTRACT_AND_RUN", "DESKTOP_STATUS",
                    "EXTRACT_STATUS", "WAIT_TEST", "READY_PATH", "GATE_PATH", "OUTSIDE_TARGET", "DETACHED_TEST"):
            environment.pop(key, None)
        return launcher, environment

    def test_repeated_launches_are_private_forward_arguments_and_clean_after_success(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            launcher, environment = self.fixture(root)
            unrelated = root / "unrelated"
            unrelated.write_text("keep")
            paths = []
            for temporary_path in (str(root), root.name):
                result = subprocess.run([str(launcher), "--store", "directory with spaces"],
                                        env={**environment, "TMPDIR": temporary_path}, cwd=root.parent,
                                        capture_output=True, text=True, check=True, timeout=10)
                data = json.loads(result.stdout)
                paths.append(data["work"])
                self.assertEqual(data["marker"], "1")
                self.assertEqual(data["session"], data["work"])
                self.assertEqual(data["args"], ["--store", "directory with spaces"])
                self.assertFalse(Path(data["work"]).exists())
            self.assertNotEqual(*paths)
            self.assertEqual(unrelated.read_text(), "keep")
            self.assertFalse(list(root.glob("chrootables-appimage.*")))

    def test_overlapping_launches_cannot_remove_each_others_runtime_or_symlink_targets(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            launcher, environment = self.fixture(root)
            outside = root / "keep"
            outside.mkdir()
            (outside / "sentinel").write_text("untouched")
            gate = root / "gate"
            ready = [root / f"ready{index}" for index in range(2)]
            processes = [subprocess.Popen([str(launcher)],
                         env={**environment, "WAIT_TEST": "1", "READY_PATH": str(path),
                              "GATE_PATH": str(gate), "OUTSIDE_TARGET": str(outside)},
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for path in ready]
            try:
                deadline = time.monotonic() + 5
                while not all(path.exists() for path in ready) and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertTrue(all(path.exists() for path in ready))
                paths = [Path(path.read_text()) for path in ready]
                self.assertNotEqual(*paths)
                self.assertTrue(all((path / "squashfs-root/AppRun").exists() for path in paths))
                gate.touch()
                for process in processes:
                    _, stderr = process.communicate(timeout=5)
                    self.assertEqual(process.returncode, 0, stderr)
                self.assertTrue(all(not path.exists() for path in paths))
                self.assertEqual((outside / "sentinel").read_text(), "untouched")
            finally:
                for process in processes:
                    if process.poll() is None:
                        process.kill()
                        process.communicate(timeout=5)

    def test_failed_extraction_cleans_only_its_own_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            launcher, environment = self.fixture(root)
            sentinel = root / "chrootables-appimage.keep"
            sentinel.mkdir()
            result = subprocess.run([str(launcher)], env={**environment, "EXTRACT_STATUS": "19"},
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 19)
            self.assertEqual(list(root.glob("chrootables-appimage.*")), [sentinel])

    def test_abnormal_frontend_exit_retains_runtime_for_disconnected_helper(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            launcher, environment = self.fixture(root)
            result = subprocess.run([str(launcher)], env={**environment, "DESKTOP_STATUS": "7"},
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 7)
            work = Path(json.loads(result.stdout)["work"])
            self.assertTrue((work / "squashfs-root/AppRun").is_file())
            self.assertIn(str(work), result.stderr)
            self.assertIn("administrator helper has finished", result.stderr)
            self.assertEqual(work.stat().st_mode & 0o777, 0o700)

    def test_signals_reach_child_preserve_status_and_retain_runtime(self):
        for number in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            with self.subTest(signal=number), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                launcher, environment = self.fixture(root)
                ready = root / "ready"
                process = subprocess.Popen([str(launcher)], env={**environment, "WAIT_TEST": "1", "READY_PATH": str(ready)},
                                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                try:
                    deadline = time.monotonic() + 5
                    while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
                        time.sleep(0.01)
                    self.assertTrue(ready.exists(), "Extracted frontend did not start.")
                    process.send_signal(number)
                    stdout, stderr = process.communicate(timeout=5)
                    self.assertEqual(process.returncode, 128 + number, stderr)
                    work = Path(json.loads(stdout)["work"])
                    self.assertTrue((work / "squashfs-root/AppRun").exists())
                    self.assertIn(str(work), stderr)
                finally:
                    if process.poll() is None:
                        process.kill()
                        process.communicate(timeout=5)

    def test_detached_terminal_marker_preserves_runtime_after_successful_gui_exit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            launcher, environment = self.fixture(root)
            result = subprocess.run([str(launcher)], env={**environment, "DETACHED_TEST": "1"},
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0)
            work = Path(json.loads(result.stdout)["work"])
            self.assertTrue((work / "squashfs-root/AppRun").exists())
            self.assertIn("for a detached terminal session", result.stderr)


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
            if self.kind == "appimage":
                first_source, = (root / "first").glob("*-runtime-sources.tar.gz")
                second_source, = (root / "second with spaces").glob("*-runtime-sources.tar.gz")
                self.assertEqual(support.file_digest(first_source), support.file_digest(second_source))
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


@unittest.skipUnless(APPIMAGETOOL and RUNTIME and PORTABLE_RUNTIME,
                     "requires APPIMAGETOOL, APPIMAGE_RUNTIME_FILE and CHROOTABLE_APPIMAGE_RUNTIME")
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
                           "APPIMAGE_EXTRACT_AND_RUN": "1", "TMPDIR": str(root)}
            version = subprocess.check_output([str(package), "--version"],
                                              env=environment, text=True, timeout=30).strip()
            self.assertEqual(version, support.upstream_version())
            subprocess.run([str(package), "--appimage-extract"], cwd=root, check=True, capture_output=True)
            stage = root / "squashfs-root"
            if (ROOT / "LICENSE").exists():
                self.assertEqual((stage / "LICENSE").read_bytes(), (ROOT / "LICENSE").read_bytes())
            bundled = stage / "usr/lib/chrootables/runtime/bin/python3"
            self.assertTrue(os.access(bundled, os.X_OK))
            notices = stage / "usr/share/doc/chrootables/third-party"
            for relative in (
                "qtbase-everywhere-src-6.11.2/LICENSES/LGPL-3.0-only.txt",
                "qtbase-everywhere-src-6.11.2/LICENSES/GPL-3.0-only.txt",
                "qtbase-everywhere-src-6.11.2/src/3rdparty/libjpeg/COPYRIGHT.txt",
                "qtbase-everywhere-src-6.11.2/src/3rdparty/pcre2/LICENCE.md",
                "qtbase-everywhere-src-6.11.2/src/3rdparty/wayland/protocols/appmenu/LGPL-2.1-or-later.txt",
                "qtwayland-everywhere-src-6.11.2/src/3rdparty/protocol/ivi/MIT.txt",
                "pyside-setup-everywhere-src-6.11.2/sources/shiboken6/libshiboken/bufferprocs_py37.h",
                "pyside-setup-everywhere-src-6.11.2/sources/shiboken6/shibokenmodule/files.dir/shibokensupport/signature/PSF-3.7.0.txt",
            ):
                self.assertTrue((notices / relative).is_file(), relative)
            self.assertIn("Independent JPEG Group", (notices / "NOTICE.txt").read_text())
            source, = root.glob("*-runtime-sources.tar.gz")
            with tarfile.open(source) as archive:
                sources = set(archive.getnames())
                self.assertIn("Python-3.13.18.tar.xz", sources)
                self.assertIn("qtbase-everywhere-src-6.11.2.tar.xz", sources)
                self.assertIn("icu4c-73_2-src.tgz", sources)
            subprocess.run([str(bundled), "-I", "-B", str(ROOT / "scripts/check-portable.py"), str(stage)],
                           env=environment, check=True, capture_output=True, timeout=45)
            subprocess.run(["/usr/bin/python3", str(ROOT / "scripts/check-installed.py"),
                            "--prefix", str(stage / "usr")], check=True, capture_output=True, timeout=45)
            screenshot = root / "runtime-demo.png"
            subprocess.run([str(package), "--demo", "--screenshot", str(screenshot)],
                           env=environment, check=True, capture_output=True, timeout=30)
            self.assertGreater(screenshot.stat().st_size, 0)


if __name__ == "__main__":
    unittest.main()
