import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from chrootables.backend import Store, build_command, name, read_regular, shared_folder, terminal_command, deliver_export, MANAGER
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

    def test_unknown_operations_rejected(self):
        for action in ("exec", "worker", "bash", "--help"):
            with self.assertRaises(ValueError):
                build_command(action, {})

    def test_terminal_flags_preserve_disposable_and_network_choice(self):
        with patch("chrootables.backend.shutil.which", return_value="/usr/bin/konsole"):
            result = terminal_command("lab", Path("/var/lib/a store"), disposable=True, isolated=True)
        self.assertEqual(result[-3:], ["--try", "lab", "--isolated"])
        self.assertIn("CHROOTABLE_HOME=/var/lib/a store", result)

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
        link = self.path / "link"
        link.symlink_to(log)
        with self.assertRaises(OSError):
            read_regular(link)

    def test_missing_store_is_empty_without_creating_it(self):
        missing = self.path / "missing"
        self.assertEqual(Store(missing).inventory()["images"], [])
        self.assertFalse(missing.exists())

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

    def test_operation_failure_is_reported_and_streamed(self):
        fake = self.path / "fake-manager"
        fake.write_text("#!/bin/sh\nprintf 'diagnostic output\\n'\nexit 17\n")
        fake.chmod(0o755)
        output = []
        with patch("chrootables.backend.MANAGER", fake):
            code = self.store.operate("clone", {"name": "lab", "target": "copy"}, output.append)
        self.assertEqual(code, 17)
        self.assertIn("diagnostic output", "".join(output))

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


if __name__ == "__main__":
    unittest.main()
