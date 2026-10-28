import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock, patch

from chrootables.backend import Store, build_command, name, read_regular, shared_folder, session_command, terminal_command, deliver_export, MANAGER, PROFILES
from chrootables.broker import serve


class ValidationTests(unittest.TestCase):
    def test_rejects_path_traversal_and_shell_input(self):
        for value in ("../root", "/tmp/image", "-option", "a;b", "$(id)", "a\nb", "", "a" * 65, None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                name(value)

    def test_create_has_immutable_argument_boundaries(self):
        args, env = build_command("create", {"name": "python-lab", "size_mib": 4096, "lifecycle": "ephemeral", "profile": "python", "suite": "trixie"})
        self.assertEqual(args[1:], ["--create", "python-lab", "4096", "ephemeral", "python"])
        self.assertEqual(env, {"CHROOTABLE_DEBIAN_SUITE": "trixie"})

    def test_destructive_operations_require_explicit_confirmation(self):
        for action in ("delete", "repair", "restore", "drop-snapshot"):
            with self.subTest(action=action), self.assertRaises(ValueError):
                build_command(action, {"name": "lab", "target": "before"})
            args, _ = build_command(action, {"name": "lab", "target": "before", "confirmed": True})
            self.assertEqual(args[-1], "--yes")

    def test_invalid_profile_release_mode_size(self):
        base = {"name": "lab", "size_mib": 4096}
        for field, value in [("suite", "x;id"), ("profile", "untrusted"), ("lifecycle", "other"), ("size_mib", True), ("size_mib", 0), ("size_mib", 1048577), ("size_mib", "4096")]:
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                build_command("create", {**base, field: value})

    def test_all_advertised_profiles_are_accepted_for_create(self):
        for profile in PROFILES:
            with self.subTest(profile=profile):
                args, _ = build_command("create", {"name": "lab", "size_mib": 4096, "profile": profile})
                self.assertEqual(args[-1], profile)

    def test_unknown_operations_rejected(self):
        for action in ("exec", "worker", "bash", "--help"):
            with self.assertRaises(ValueError):
                build_command(action, {})

    def test_terminal_flags_preserve_disposable_and_network_choice(self):
        with patch("chrootables.backend.shutil.which", return_value="/usr/bin/konsole"):
            result = terminal_command("lab", Path("/var/lib/a store"), disposable=True, isolated=True)
        self.assertEqual(result[-3:], ["--try", "lab", "--isolated"])
        self.assertIn("CHROOTABLE_HOME=/var/lib/a store", result)

    def test_session_command_is_the_core_of_external_terminals(self):
        keyword = dict(disposable=True, isolated=True)
        with patch("chrootables.backend.shutil.which", return_value="/usr/bin/konsole"):
            wrapped = terminal_command("lab", Path("/var/lib/a store"), **keyword)
        core = session_command("lab", Path("/var/lib/a store"), **keyword)
        self.assertEqual(wrapped[wrapped.index(str(MANAGER)):], core[core.index(str(MANAGER)):])
        self.assertEqual(core[:3], ["sudo", "/usr/bin/env", "CHROOTABLE_AUTO_INSTALL=0"])
        self.assertIn("CHROOTABLE_HOME=/var/lib/a store", core)

    def test_session_command_restricts_devices_when_requested(self):
        core = session_command("lab", restricted_dev=True)
        self.assertIn("--restricted-dev", core)
        self.assertNotIn("--restricted-dev", session_command("lab"))
        for value in (1, "true", None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                session_command("lab", restricted_dev=value)

    def test_shared_folder_resolves_directory_aliases(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory) / "project files"
            folder.mkdir()
            alias = Path(directory) / "alias"
            alias.symlink_to(folder, target_is_directory=True)
            self.assertEqual(shared_folder(str(alias)), str(folder))
            alias.unlink()
            alias.symlink_to("/", target_is_directory=True)
            with self.assertRaises(ValueError):
                shared_folder(str(alias))
            alias.unlink()
            unusual = Path(directory) / "project\nfiles"
            unusual.mkdir()
            alias.symlink_to(unusual, target_is_directory=True)
            with self.assertRaises(ValueError):
                shared_folder(str(alias))

    def test_shared_folder_rejects_invalid_or_unavailable_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / "file"
            file.touch()
            missing = str(Path(directory) / "missing")
            for value in (None, True, "", "relative/folder", "/", missing, str(file),
                          directory + "\x00", directory + "\n", directory + "\r"):
                with self.subTest(value=value), self.assertRaises(ValueError):
                    shared_folder(value)

    def test_shared_folder_argument_boundaries_across_terminals_and_modes(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory) / "project files; $(echo unsafe),draft"
            folder.mkdir()
            modes = [(False, False, "--enter"), (False, True, "--enter-isolated"),
                     (True, False, "--try"), (True, True, "--try")]
            for terminal in ("konsole", "gnome-terminal", "xterm"):
                for disposable, isolated, action in modes:
                    with self.subTest(terminal=terminal, disposable=disposable, isolated=isolated):
                        with patch("chrootables.backend.shutil.which",
                                   side_effect=lambda candidate: "/usr/bin/" + candidate if candidate == terminal else None):
                            command = terminal_command("lab", disposable=disposable, isolated=isolated,
                                                       share=str(folder), read_only=True)
                        args = command[command.index(str(MANAGER)) + 1:]
                        expected = [action, "lab"]
                        if disposable and isolated:
                            expected.append("--isolated")
                        self.assertEqual(args, expected + ["--share", str(folder), "--read-only"])

    def test_terminal_rejects_read_only_without_folder_and_non_boolean_values(self):
        with self.assertRaises(ValueError):
            terminal_command("lab", read_only=True)
        for value in (1, "false", None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                terminal_command("lab", read_only=value)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        for folder in ("images", "logs", "snapshots"):
            (self.path / folder).mkdir()
        self.store = Store(self.path)

    def image(self, name="lab"):
        target = self.path / "images" / (name + ".img")
        with target.open("wb") as stream:
            stream.truncate(1024 * 1048576)
        Path(str(target) + ".meta").write_text("lifecycle=ephemeral\nsuite=trixie\narchitecture=amd64\nprofile=python\ncreated_at=2026-10-16T10:00:00\nlast_used_at=never\n")
        return target

    def test_doctor_preserves_structured_report_and_readiness(self):
        for ready in (True, False):
            report = {"schema_version": 1, "ready": ready, "output": "Host health",
                      "host": "Linux", "architecture": "x86_64", "version": "3.2.0",
                      "store": str(self.path), "errors": 0 if ready else 1, "warnings": 0,
                      "checks": [{"id": "archive-keys", "status": "ok" if ready else "error",
                                  "message": "Archive keys", "remedy": "Run --setup"}],
                      "storage": {"available": True, "free_bytes": 1024, "total_bytes": 2048}}
            result = subprocess.CompletedProcess([], 0 if ready else 1, json.dumps(report), "")
            with patch("chrootables.backend.subprocess.run", return_value=result) as run:
                self.assertEqual(self.store.doctor(), report)
            self.assertEqual(run.call_args.args[0][-2:], ["--doctor", "--json"])

    def test_doctor_rejects_invalid_or_inconsistent_cli_reports(self):
        reports = [(2, "", "doctor invocation failed"), (0, "not json", ""),
                   (0, json.dumps({"schema_version": 1, "ready": False,
                                   "output": "invalid", "checks": []}), "")]
        for code, output, error in reports:
            with self.subTest(code=code, output=output):
                result = subprocess.CompletedProcess([], code, output, error)
                with patch("chrootables.backend.subprocess.run", return_value=result):
                    with self.assertRaises(RuntimeError):
                        self.store.doctor()

    def test_doctor_rejects_bad_checks_counts_and_storage_fields(self):
        report = {"schema_version": 1, "ready": True, "output": "Host health", "errors": 0,
                  "warnings": 0, "host": "Linux", "architecture": "x86_64", "version": "3.2.0",
                  "store": str(self.path), "checks": [],
                  "storage": {"available": True, "free_bytes": 1024, "total_bytes": 2048}}
        valid_check = {"id": "a", "status": "ok", "message": "Ready", "remedy": ""}
        invalid = [{"checks": [None]}, {"checks": [{}]}, {"checks": ["text"]},
                   {"checks": [{**valid_check, "status": "unknown"}]},
                   {"checks": [{**valid_check, "remedy": []}]},
                   {"checks": [valid_check, valid_check]},
                   {"warnings": 1}, {"schema_version": True}, {"errors": False},
                   {"storage": {"available": 1, "free_bytes": 0, "total_bytes": 0}},
                   {"storage": {"available": True, "free_bytes": 3000, "total_bytes": 2000}}]
        for change in invalid:
            with self.subTest(change=change):
                result = subprocess.CompletedProcess([], 0, json.dumps({**report, **change}), "")
                with patch("chrootables.backend.subprocess.run", return_value=result):
                    with self.assertRaises(RuntimeError):
                        self.store.doctor()

    def test_real_cli_inventory_and_snapshot_metadata(self):
        image = self.image()
        folder = self.path / "snapshots/deleted-lab"
        folder.mkdir()
        (folder / "before.img").touch()
        (folder / "before.img.meta").write_text("snapshot_at=2026-10-16T10:00:00\n")
        (self.path / "activity.tsv").write_text("2026-10-16T10:00:00\tcreate\tlab\tok\t/log\n")
        data = self.store.inventory()
        self.assertEqual(data["images"][0]["size_mib"], 1024)
        self.assertEqual(data["images"][0]["lifecycle"], "ephemeral")
        self.assertTrue(data["snapshots"][0]["orphan"])
        self.assertEqual(data["activity"][0]["action"], "create")
        self.assertGreater(data["free_bytes"], 0)

    def test_busy_lock_visible_in_real_inventory(self):
        import fcntl
        image = self.image()
        with Path(str(image) + ".lock").open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertEqual(self.store.inventory()["images"][0]["status"], "busy")
        self.assertEqual(self.store.inventory()["images"][0]["status"], "ready")

    def test_legacy_image_and_symlink_ignored(self):
        image = self.image()
        Path(str(image) + ".meta").unlink()
        Path(str(image) + ".mode").write_text("ephemeral\n")
        (self.path / "images/alias.img").symlink_to(image)
        data = self.store.inventory()["images"]
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["lifecycle"], "ephemeral")

    def test_symlink_store_refused(self):
        alias = self.path / "alias"
        alias.symlink_to(self.path, target_is_directory=True)
        with self.assertRaises(ValueError):
            Store(alias).inventory()

    def test_safe_bounded_log_reads(self):
        log = self.path / "test.log"
        log.write_text("0123456789")
        self.assertEqual(read_regular(log, 4), "6789")
        self.assertEqual(read_regular(log, offset=6), "6789")
        self.assertEqual(read_regular(log, offset=10), "")
        link = self.path / "link"
        link.symlink_to(log)
        with self.assertRaises(OSError):
            read_regular(link)

    def test_inventory_cache_reuses_unchanged_state_and_tracks_locks_and_mutations(self):
        import fcntl
        import chrootables.backend as backend_mod
        image = self.image()
        real_run = subprocess.run
        with patch("chrootables.backend.subprocess.run", side_effect=real_run) as mocked_run:
            first = self.store.inventory()
            second = self.store.inventory()
            self.assertEqual(first["images"], second["images"])
            self.assertEqual(mocked_run.call_count, 1)
            with Path(str(image) + ".lock").open("w") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                busy = self.store.inventory()
                self.assertEqual(busy["images"][0]["status"], "busy")
                self.assertEqual(mocked_run.call_count, 1)
            ready = self.store.inventory()
            self.assertEqual(ready["images"][0]["status"], "ready")
            self.assertEqual(mocked_run.call_count, 1)
            Path(str(image) + ".meta").write_text("lifecycle=persistent\nsuite=bookworm\narchitecture=amd64\nprofile=minimal\ncreated_at=2026-10-16T10:00:00\nlast_used_at=never\n")
            updated = self.store.inventory()
            self.assertEqual(updated["images"][0]["lifecycle"], "persistent")
            self.assertEqual(updated["images"][0]["suite"], "bookworm")
            self.assertEqual(mocked_run.call_count, 2)

    def test_missing_store_is_empty_without_creating_it(self):
        missing = self.path / "missing"
        self.assertEqual(Store(missing).inventory()["images"], [])
        self.assertFalse(missing.exists())

    def test_concurrent_inventory_requests_share_one_cache_refresh(self):
        self.image()
        start = threading.Barrier(2)
        real_run = subprocess.run

        def slow_inventory(*args, **kwargs):
            time.sleep(0.05)
            return real_run(*args, **kwargs)

        def inventory():
            start.wait(timeout=5)
            return self.store.inventory()

        with patch("chrootables.backend.subprocess.run", side_effect=slow_inventory) as run:
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(inventory) for _ in range(2)]
                results = [future.result(timeout=5) for future in futures]
        self.assertEqual(results[0]["images"], results[1]["images"])
        self.assertEqual(run.call_count, 1)

    def test_cached_inventory_does_not_block_on_fifo_lock(self):
        image = self.image()
        os.mkfifo(str(image) + ".lock")
        script = """import sys
from chrootables.backend import Store
store = Store(sys.argv[1])
assert store.inventory()['images'][0]['status'] == 'ready'
assert store.inventory()['images'][0]['status'] == 'ready'
"""
        result = subprocess.run([sys.executable, "-c", script, str(self.path)],
                                capture_output=True, text=True, timeout=3)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_selected_operation_log_is_exact_and_confined(self):
        first = self.path / "logs/lab-create.log"
        second = self.path / "logs/lab-clone.log"
        first.write_text("creation details")
        second.write_text("clone details")
        (self.path / "activity.tsv").write_text(f"now\tcreate\tlab\tok\t{first}\nnow\tclone\tlab\tok\t{second}\n")
        self.assertIn("creation details", self.store.logs("lab", str(first)))
        self.assertNotIn("clone details", self.store.logs("lab", str(first)))
        with self.assertRaises(ValueError):
            self.store.logs("lab", "/etc/shadow")
        with self.assertRaises(ValueError):
            self.store.logs("different", str(first))

    def test_broker_read_only_rejects_mutations(self):
        messages = io.StringIO()
        serve(self.store, io.StringIO(json.dumps({"id": 1, "action": "delete", "params": {"name": "lab", "confirmed": True}}) + "\n"), messages)
        output = [json.loads(line) for line in messages.getvalue().splitlines()]
        self.assertFalse(output[-1]["ok"])
        self.assertIn("Unlock", output[-1]["error"])
        self.assertEqual(output[-1]["code"], "permission_required")

    def test_broker_inventory_protocol(self):
        self.image()
        messages = io.StringIO()
        serve(self.store, io.StringIO('{"id":1,"action":"inventory"}\n'), messages)
        output = [json.loads(line) for line in messages.getvalue().splitlines()]
        self.assertTrue(output[-1]["ok"])
        self.assertEqual(output[-1]["data"]["images"][0]["name"], "lab")

    @unittest.skipIf(os.getuid() == 0, "Tests the user-side export writer")
    def test_export_delivers_and_refuses_overwrite(self):
        source = self.path / "source.tar.gz"
        source.write_bytes(b"archive payload")
        target = self.path / "saved archive.tar.gz"
        deliver_export(source, target)
        self.assertEqual(target.read_bytes(), b"archive payload")
        with self.assertRaises(RuntimeError):
            deliver_export(source, target)
        self.assertEqual(target.read_bytes(), b"archive payload")

    @unittest.skipIf(os.getuid() == 0, "Tests the user-side export writer")
    def test_failed_export_copy_removes_partial_destination_and_retains_source(self):
        source = self.path / "source.tar.gz"
        source.write_bytes(b"original archive")
        target = self.path / "partial.tar.gz"
        real_run = subprocess.run
        for failure in ("OSError(28, 'No space left on device')", "KeyboardInterrupt()"):
            with self.subTest(failure=failure):
                def inject_copy_failure(argv, **kwargs):
                    injected = ("def failed_copy(source, destination, length):\n"
                                "    destination.write(b'partial')\n"
                                "    raise " + failure + "\n"
                                "def unsupported_copy(*args):\n"
                                "    raise OSError(18, 'Cross-device link')\n"
                                "os.copy_file_range = unsupported_copy\n"
                                "shutil.copyfileobj = failed_copy\n")
                    argv = list(argv)
                    argv[4] = argv[4].replace("source, destination =", injected + "source, destination =", 1)
                    return real_run(argv, **kwargs)
                with patch("chrootables.backend.subprocess.run", side_effect=inject_copy_failure):
                    with self.assertRaisesRegex(RuntimeError, "saving the chosen copy failed"):
                        deliver_export(source, target)
                self.assertFalse(target.exists())
                self.assertEqual(source.read_bytes(), b"original archive")

    def test_inventory_failure_does_not_publish_a_new_cache_signature(self):
        image = self.image()
        initial = self.store.inventory()
        signature = self.store._images_sig
        metadata_path = Path(str(image) + ".meta")
        metadata_path.write_text(metadata_path.read_text() + "origin=changed\n")
        with patch("chrootables.backend.subprocess.run",
                   return_value=Mock(returncode=1, stderr="image disappeared", stdout="")):
            with self.assertRaisesRegex(PermissionError, "image disappeared"):
                self.store.inventory()
        self.assertEqual(self.store._images_sig, signature)
        self.assertEqual(self.store._images_cache, initial["images"])
        self.assertEqual(self.store.inventory()["images"][0]["origin"], "changed")

    def test_snapshot_disappearing_during_scan_is_reported_and_next_refresh_recovers(self):
        folder = self.path / "snapshots/lab"
        folder.mkdir()
        snapshot = folder / "checkpoint.img"
        snapshot.touch()
        real_scandir = os.scandir

        class VanishingScan:
            def __enter__(self):
                self.scan = real_scandir(folder)
                return self.scan.__enter__()

            def __exit__(self, *args):
                self.scan.__exit__(*args)
                snapshot.unlink()

        def scandir(path):
            return VanishingScan() if Path(path) == folder else real_scandir(path)

        with patch("chrootables.backend.os.scandir", side_effect=scandir):
            with self.assertRaises(FileNotFoundError):
                self.store.inventory()
        self.assertEqual(self.store.inventory()["snapshots"], [])

    def test_broker_reports_spawn_failure_and_keeps_serving(self):
        request = json.dumps({"id": 1, "action": "clone", "params": {"name": "lab", "target": "copy"}})
        request += '\n{"id":2,"action":"doctor"}\n'
        output = io.StringIO()
        with patch("chrootables.backend.subprocess.Popen", side_effect=OSError("cannot launch manager")), \
                patch.object(self.store, "doctor", return_value={"ready": True, "output": "ready"}):
            serve(self.store, io.StringIO(request), output, writable=True)
        results = {message["id"]: message for line in output.getvalue().splitlines()
                   if (message := json.loads(line)).get("event") == "result"}
        self.assertFalse(results[1]["ok"])
        self.assertEqual(results[1]["code"], "operation_failed")
        self.assertIn("cannot launch manager", results[1]["error"])
        self.assertTrue(results[2]["ok"])

    def test_operation_failure_is_reported_and_streamed(self):
        fake = self.path / "fake-manager"
        fake.write_text("#!/bin/sh\nprintf 'diagnostic output\\n'\nexit 17\n")
        fake.chmod(0o755)
        output = []
        with patch("chrootables.backend.MANAGER", fake):
            code = self.store.operate("clone", {"name": "lab", "target": "copy"}, output.append)
        self.assertEqual(code, 17)
        self.assertIn("diagnostic output", "".join(output))

    def test_operation_log_append_during_read_is_streamed_once(self):
        log = self.path / "logs/lab-clone.log"
        log.write_text("first\n")
        process = Mock(stdout=io.StringIO(), returncode=0)
        process.poll.side_effect = [None, 0]
        real_open = os.open
        appended = False

        def append_before_open(path, flags, *args, **kwargs):
            nonlocal appended
            if Path(path) == log and not appended:
                appended = True
                with log.open("a") as stream:
                    stream.write("second\n")
            return real_open(path, flags, *args, **kwargs)

        output = []
        with patch("chrootables.backend.subprocess.Popen", return_value=process), \
             patch("chrootables.backend.time.time", return_value=0), \
             patch("chrootables.backend.time.sleep"), \
             patch("chrootables.backend.os.open", side_effect=append_before_open):
            code = self.store.operate("clone", {"name": "lab", "target": "copy"}, output.append)
        self.assertEqual(code, 0)
        self.assertEqual("".join(output), "first\nsecond\n")

    def test_resolver_setup_does_not_use_uninitialized_local(self):
        work = self.path / "work"
        root = self.path / "root"
        work.mkdir()
        (root / "etc").mkdir(parents=True)
        script = 'source "$1"; umask 077; mount() { return 0; }; setup_resolv_conf "$2" "$3"'
        result = subprocess.run(["bash", "-c", script, "resolver-test", str(MANAGER), str(work), str(root)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("nameserver", (work / "resolv.conf").read_text())
        self.assertTrue((root / "etc/resolv.conf").exists())
        self.assertEqual((work / "resolv.conf").stat().st_mode & 0o777, 0o644)

    def test_requirements_hash_locked_and_minimal_scope(self):
        req_text = (MANAGER.parent / "requirements.txt").read_text()
        self.assertIn("PySide6-Essentials==6.11.2", req_text)
        self.assertIn("shiboken6==6.11.2", req_text)
        self.assertNotIn("PySide6-Addons", req_text)
        self.assertNotIn(">=", req_text)
        self.assertEqual(req_text.count("--hash=sha256:"), 10)

    def test_suite_and_mirror_supply_chain_validation(self):
        script = '''
source "$1"
valid_suite trixie && valid_suite bookworm && valid_suite stable || exit 10
! valid_suite "--no-check-gpg" || exit 11
! valid_suite $'trixie\nlifecycle=ephemeral' || exit 12
valid_mirror "https://deb.debian.org/debian" || exit 13
! valid_mirror "http://deb.debian.org/debian" || exit 14
! valid_mirror "file:///tmp/mirror" || exit 15
! valid_mirror "--no-check-gpg" || exit 16
! write_metadata "$2/img" persistent $'trixie\norigin=injected' amd64 1024 now never minimal || exit 17
'''
        result = subprocess.run(["bash", "-c", script, "supply-chain-test", str(MANAGER), str(self.path)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, f"exit {result.returncode}: {result.stderr}")


if __name__ == "__main__":
    unittest.main()
