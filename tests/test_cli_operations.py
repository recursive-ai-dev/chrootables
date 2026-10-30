"""Operation recovery and capacity checks using temporary files, without mounts/root."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


MANAGER = Path(__file__).resolve().parents[1] / "chrootable-manager"
OPERATION_ID = "a1" * 16


class CliOperationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.store = Path(self.temporary.name) / "store"
        for directory in ("images", "logs", "exports", "snapshots/lab", "operations"):
            (self.store / directory).mkdir(parents=True)
        self.image = self.store / "images/lab.img"
        self.image.write_bytes(b"original image")
        self.image.with_suffix(".img.meta").write_text("lifecycle=persistent\norigin=original\n")

    def bash(self, script, *arguments, identifier=OPERATION_ID):
        env = {**os.environ, "CHROOTABLE_OPERATION_ID": identifier}
        return subprocess.run(
            ["bash", "-c", 'source "$1"\nSTORE_DIR=$2\nconfigure_paths\numask 077\nshift 2\n' + script,
             "cli-operation-test", str(MANAGER), str(self.store), *map(str, arguments)],
            env=env, capture_output=True, text=True, timeout=15,
        )

    def state(self):
        return dict(line.split("=", 1) for line in
                    (self.store / "operations" / (OPERATION_ID + ".state")).read_text().splitlines())

    def interrupt_publication(self, point="image", abrupt=False):
        failure = 'kill -KILL "$BASHPID"' if abrupt else "return 19"
        result = self.bash('''
begin_operation restore lab
PENDING="$IMAGE_DIR/.lab.ABC123.partial"
printf 'replacement image' >"$PENDING"
printf 'lifecycle=ephemeral\\norigin=replacement\\n' >"$PENDING.meta"
mv() {
    local source=${@: -2:1}
    if [[ $source == "$PENDING''' + (".meta" if point == "metadata" else "") + '''" ]]; then
        ''' + failure + '''
    fi
    command mv "$@"
}
publish_image "$PENDING" "$IMAGE_DIR/lab.img"
''')
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def test_completed_operation_has_deterministic_log_record_and_stage_events(self):
        result = self.bash('''
begin_operation clone lab
operation_stage copying
printf 'diagnostic output\\n' >>"$OP_LOG"
''')
        self.assertEqual(result.returncode, 0, result.stderr)
        state = self.state()
        self.assertEqual((state["id"], state["status"], state["stage"]),
                         (OPERATION_ID, "completed", "completed"))
        log = self.store / "logs" / (OPERATION_ID + ".log")
        self.assertIn("diagnostic output", log.read_text())
        self.assertEqual(log.stat().st_mode & 0o777, 0o600)
        events = [json.loads(line.removeprefix("CHROOTABLE_EVENT "))
                  for line in result.stderr.splitlines() if line.startswith("CHROOTABLE_EVENT ")]
        self.assertEqual([event["stage"] for event in events], ["starting", "copying", "completed"])
        self.assertTrue(all(event["id"] == OPERATION_ID and event["log"] == str(log) for event in events))
        logs = self.bash('show_logs lab')
        self.assertEqual(logs.returncode, 0, logs.stderr)
        self.assertIn("diagnostic output", logs.stdout)

    def test_operation_ids_are_validated_and_existing_logs_never_overwritten(self):
        result = self.bash('begin_operation clone lab\nprintf first >>"$OP_LOG"')
        self.assertEqual(result.returncode, 0, result.stderr)
        original = (self.store / "logs" / (OPERATION_ID + ".log")).read_bytes()
        duplicate = self.bash('begin_operation clone lab')
        self.assertNotEqual(duplicate.returncode, 0)
        self.assertEqual((self.store / "logs" / (OPERATION_ID + ".log")).read_bytes(), original)
        for invalid in ("../escape", "A" * 32, "$(id)", "a" * 33):
            with self.subTest(identifier=invalid):
                result = self.bash('begin_operation clone lab', identifier=invalid)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("32 lowercase hexadecimal", result.stderr)

    def test_recovery_completes_failure_before_metadata_publication(self):
        self.interrupt_publication("metadata")
        self.assertEqual(self.image.read_bytes(), b"original image")
        self.assertIn("origin=original", self.image.with_suffix(".img.meta").read_text())
        self.assertEqual((self.state()["status"], self.state()["stage"]),
                         ("interrupted", "publish-prepared"))
        self.assert_recovers_publication()

    def test_recovery_completes_failure_between_metadata_and_image_publication(self):
        self.interrupt_publication()
        self.assertEqual(self.image.read_bytes(), b"original image")
        self.assertIn("origin=replacement", self.image.with_suffix(".img.meta").read_text())
        self.assertEqual(self.state()["stage"], "publish-metadata")
        self.assert_recovers_publication()

    def assert_recovers_publication(self):
        blocked = self.bash('lock_image "$IMAGE_DIR/lab.img" descriptor')
        self.assertNotEqual(blocked.returncode, 0)
        self.assertIn("must be recovered first", blocked.stderr)
        result = self.bash('assert_offline() { :; }\nrecover_operation "$1"', OPERATION_ID)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.image.read_bytes(), b"replacement image")
        self.assertIn("origin=replacement", self.image.with_suffix(".img.meta").read_text())
        self.assertFalse((self.store / "images/.lab.ABC123.partial").exists())
        self.assertEqual(self.state()["status"], "recovered")
        self.assertEqual(self.bash('lock_image "$IMAGE_DIR/lab.img" descriptor').returncode, 0)

    def test_sigkill_is_visible_in_operations_and_read_only_doctor(self):
        result = self.interrupt_publication(abrupt=True)
        self.assertEqual(result.returncode, -9)
        self.assertEqual(self.state()["status"], "running")
        inventory = self.bash('list_operations json')
        self.assertEqual(inventory.returncode, 0, inventory.stderr)
        record = json.loads(inventory.stdout)[0]
        self.assertEqual((record["status"], record["active"]), ("interrupted", False))
        before = {path.relative_to(self.store): path.read_bytes() for path in self.store.rglob("*") if path.is_file()}
        doctor = self.bash('doctor json')
        report = json.loads(doctor.stdout)
        checks = {check["id"]: check for check in report["checks"]}
        check = checks["operation:" + OPERATION_ID]
        self.assertEqual(check["status"], "warning")
        self.assertIn("--recover-operation", check["remedy"])
        after = {path.relative_to(self.store): path.read_bytes() for path in self.store.rglob("*") if path.is_file()}
        self.assertEqual(before, after)
        self.assert_recovers_publication()

    def test_recovery_handles_image_already_published_before_record_update(self):
        result = self.bash('''
begin_operation restore lab
PENDING="$IMAGE_DIR/.lab.ABC123.partial"
printf 'replacement image' >"$PENDING"
printf 'origin=replacement\\n' >"$PENDING.meta"
operation_stage() {
    OP_STAGE=$1
    if [[ $1 == published ]]; then kill -KILL "$BASHPID"; fi
    write_operation_state
}
publish_image "$PENDING" "$IMAGE_DIR/lab.img"
''')
        self.assertEqual(result.returncode, -9)
        self.assertEqual(self.image.read_bytes(), b"replacement image")
        self.assert_recovers_publication()

    def test_recovery_refuses_live_operation_and_preserves_copying_partials(self):
        live = self.bash('begin_operation clone lab\nrecover_operation "$1"', OPERATION_ID)
        self.assertNotEqual(live.returncode, 0)
        self.assertIn("still running", live.stderr)
        for path in (self.store / "operations").iterdir():
            path.unlink()
        for path in (self.store / "logs").iterdir():
            path.unlink()
        killed = self.bash('''
begin_operation clone lab
PENDING="$IMAGE_DIR/.copy.ABC123.partial"
printf incomplete >"$PENDING"
OP_PENDING=${PENDING#"$STORE_DIR/"}
operation_stage copying
kill -KILL "$BASHPID"
''')
        self.assertEqual(killed.returncode, -9)
        result = self.bash('recover_operation "$1"', OPERATION_ID)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("partial data has been retained", result.stderr)
        self.assertEqual((self.store / "images/.copy.ABC123.partial").read_bytes(), b"incomplete")
        self.assertEqual(self.image.read_bytes(), b"original image")

    def test_recovery_rejects_unsafe_paths_symlinks_and_invalid_records(self):
        self.interrupt_publication()
        record_path = self.store / "operations" / (OPERATION_ID + ".state")
        original = record_path.read_text()
        outside = self.store.parent / "outside.img"
        outside.write_bytes(b"outside")
        for destination in ("../outside.img", "/tmp/outside.img", "images/../outside.img"):
            with self.subTest(destination=destination):
                record_path.write_text(original.replace("destination=images/lab.img", "destination=" + destination))
                result = self.bash('recover_operation "$1"', OPERATION_ID)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(outside.read_bytes(), b"outside")
                self.assertEqual(self.image.read_bytes(), b"original image")
        record_path.write_text(original)
        pending = self.store / "images/.lab.ABC123.partial"
        pending.unlink()
        pending.symlink_to(outside)
        result = self.bash('recover_operation "$1"', OPERATION_ID)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(outside.read_bytes(), b"outside")
        record_path.write_text(original + "$(touch marker)=unsafe\n")
        result = self.bash('lock_image "$IMAGE_DIR/lab.img" descriptor')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Invalid operation record", result.stderr)
        self.assertFalse((self.store / "marker").exists())

    def test_recovery_never_labels_an_old_image_as_an_already_completed_publish(self):
        self.interrupt_publication("metadata")
        (self.store / "images/.lab.ABC123.partial").unlink()
        (self.store / "images/.lab.ABC123.partial.meta").unlink()
        result = self.bash('assert_offline() { :; }\nrecover_operation "$1"', OPERATION_ID)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("missing or inconsistent", result.stderr)
        self.assertEqual(self.image.read_bytes(), b"original image")
        self.assertEqual(self.state()["status"], "interrupted")

    def test_sparse_allocation_and_resize_capacity_preflight(self):
        sparse = self.store / "sparse.img"
        result = self.bash('OP_LOG="$STORE_DIR/allocation.log"\nallocate_image "$1" 512', sparse)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(sparse.stat().st_size, 512 * 1048576)
        self.assertLess(sparse.stat().st_blocks * 512, 1048576)
        original = self.image.read_bytes()
        result = self.bash('''
assert_offline() { :; }
preflight_free_space() { fail 'Not enough free space'; }
begin_operation() { printf started >"$STORE_DIR/unexpected-operation"; }
resize_image "$IMAGE_DIR/lab.img" 512
''')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.image.read_bytes(), original)
        self.assertFalse((self.store / "unexpected-operation").exists())

    def test_failed_copy_removes_partial_and_never_publishes_destination(self):
        result = self.bash('''
assert_offline() { :; }
cp() { printf incomplete >"${@: -1}"; return 28; }
clone_image "$IMAGE_DIR/lab.img" copy
''')
        self.assertEqual(result.returncode, 28, result.stderr)
        self.assertEqual(self.image.read_bytes(), b"original image")
        self.assertFalse((self.store / "images/copy.img").exists())
        self.assertEqual(list((self.store / "images").glob("*.partial")), [])
        self.assertEqual(self.state()["status"], "failed")

    def test_copy_space_preflight_fails_before_copying_or_publishing(self):
        result = self.bash('''
assert_offline() { :; }
preflight_free_space() { fail 'Not enough free space'; }
cp() { printf copied >"$STORE_DIR/unexpected-copy"; }
clone_image "$IMAGE_DIR/lab.img" copy
''')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.store / "unexpected-copy").exists())
        self.assertFalse((self.store / "images/copy.img").exists())
        result = self.bash('''
df() { printf 'Filesystem 1-blocks Used Available Capacity Mounted on\\n/dev/example 100 99 1 99%% /store\\n'; }
preflight_free_space 2
''')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("need 2 bytes, have 1", result.stderr)

    def test_setup_uses_its_operation_log_for_dependency_output(self):
        result = self.bash('''
require_root() { :; }
check_prerequisites() { printf 'dependency install output\\n' >>"$CHROOTABLE_DEP_LOG"; }
main --setup
''')
        self.assertEqual(result.returncode, 0, result.stderr)
        log = self.store / "logs" / (OPERATION_ID + ".log")
        self.assertIn("dependency install output", log.read_text())
        self.assertEqual((self.state()["action"], self.state()["status"]), ("setup", "completed"))

    def test_restricted_devices_create_pts_inside_the_new_tmpfs(self):
        root = self.store / "guest"
        (root / "dev").mkdir(parents=True)
        result = self.bash('''
WORKER_ROOT=$1
mount() {
    local target=${@: -1}
    if [[ $2 == tmpfs ]]; then
        # A real tmpfs hides any pts directory made before mounting /dev.
        rmdir -- "$target/pts" 2>/dev/null || true
    else
        [[ -d $target ]] || return 32
    fi
}
mknod() { touch -- "$3"; }
mount_restricted_devices
''', root)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((root / "dev/pts").is_dir())
        self.assertTrue((root / "dev/ptmx").is_symlink())
        self.assertTrue((root / "dev/null").is_file())

    def test_capabilities_are_read_only_and_match_cli_validation(self):
        absent = self.store / "not-created"
        result = subprocess.run([str(MANAGER), "--capabilities", "--json"],
                                env={**os.environ, "CHROOTABLE_HOME": str(absent)},
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(absent.exists())
        capabilities = json.loads(result.stdout)
        self.assertEqual(capabilities["schema_version"], 1)
        self.assertEqual(capabilities["storage"], {"allocation": "sparse", "capacity_reserved": False})
        for profile in capabilities["profiles"]:
            with self.subTest(profile=profile["id"]):
                result = self.bash('validate_cli --create lab "$1" persistent "$2"',
                                   profile["recommended_size_mib"], profile["id"])
                self.assertEqual(result.returncode, 0, result.stderr)
        limits = capabilities["limits"]
        for size, valid in ((limits["min_size_mib"], True), (limits["max_size_mib"], True),
                            (limits["min_size_mib"] - 1, False), (limits["max_size_mib"] + 1, False),
                            ("9" * 100, False)):
            with self.subTest(size=size):
                result = self.bash('validate_cli --create lab "$1" persistent minimal', size)
                self.assertEqual(result.returncode == 0, valid)
        operations = set(capabilities["operations"])
        self.assertTrue({"import", "prune-logs", "delete-export", "recover-operation"}.issubset(operations))

    def test_import_image_validates_archive_and_publishes_metadata(self):
        archive = self.store / "exports/sample.tar.gz"
        root = self.store / "sample-root"
        (root / "etc").mkdir(parents=True)
        (root / "etc/debian_version").write_text("13.0\n")
        subprocess.run(["tar", "-C", str(root), "-czf", str(archive), "."], check=True)
        result = self.bash('''
validate_tar_archive "$1"
create_sparse_ext4() { printf 'ext4-image' >"$1"; }
unshare() { touch -- "$IMAGE_DIR/imported.img.import.${CHROOTABLE_OPERATION_ID}.partial"; }
import_image imported "$1" 2048 persistent python
''', archive)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.store / "images/imported.img").exists())
        meta = (self.store / "images/imported.img.meta").read_text()
        self.assertIn("origin=import:sample.tar.gz", meta)
        self.assertIn("profile=python", meta)
        self.assertEqual((self.state()["action"], self.state()["status"]), ("import", "completed"))

    def test_validate_tar_archive_rejects_traversal_and_device_nodes(self):
        bad_tar = self.store / "exports/traversal.tar.gz"
        import io
        import tarfile
        with tarfile.open(bad_tar, "w:gz") as tf:
            info = tarfile.TarInfo("../escaped")
            info.size = 4
            tf.addfile(info, io.BytesIO(b"data"))
        result = self.bash('validate_tar_archive "$1"', bad_tar)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Unsafe path", result.stderr)

    def test_prune_logs_and_delete_export(self):
        for idx in range(5):
            log_file = self.store / "logs" / f"old-{idx}.log"
            log_file.write_text(f"log {idx}\n")
        export_file = self.store / "exports/lab-20261015-120000.tar.gz"
        export_file.write_bytes(b"archive-bytes")
        result = self.bash('prune_logs 2 --yes && delete_export "lab-20261015-120000.tar.gz" --yes')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(export_file.exists())
        remaining = [p for p in (self.store / "logs").glob("*.log") if p.name != f"{OPERATION_ID}.log"]
        self.assertLessEqual(len(remaining), 2)


if __name__ == "__main__":
    unittest.main()
