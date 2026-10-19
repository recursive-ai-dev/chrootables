"""Exercise CLI failure paths using temporary images and no privileged operations."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest


MANAGER = Path(__file__).resolve().parents[1] / "chrootable-manager"


class CliStabilityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.store = Path(self.temporary.name)
        for directory in ("images", "logs", "exports", "snapshots/lab"):
            (self.store / directory).mkdir(parents=True)
        self.image = self.store / "images/lab.img"
        with self.image.open("wb") as image:
            image.truncate(1024 * 1024)

    def bash(self, script, *arguments):
        return subprocess.run(
            ["bash", "-c", 'source "$1"\nSTORE_DIR=$2\nconfigure_paths\nshift 2\n' + script,
             "cli-stability-test", str(MANAGER), str(self.store), *map(str, arguments)],
            capture_output=True,
            text=True,
            timeout=10,
        )

    def test_resize_rechecks_capacity_after_acquiring_image_lock(self):
        # Simulate another expansion finishing between initial resolution and
        # lock acquisition. No filesystem utilities or real mounts are used.
        result = self.bash('''
lock_image() { command truncate -s 3M "$1"; }
assert_offline() { :; }
begin_operation() { OP_LOG="$STORE_DIR/operation.log"; }
fsck_preflight() { printf fsck >>"$STORE_DIR/filesystem-operations"; }
resize2fs() { printf resize >>"$STORE_DIR/filesystem-operations"; }
resize_image "$IMAGE_DIR/lab.img" 2
''')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("larger than 3 MiB", result.stderr)
        self.assertEqual(self.image.stat().st_size, 3 * 1024 * 1024)
        self.assertFalse((self.store / "filesystem-operations").exists())

    def test_inventory_propagates_partial_stat_failure(self):
        # stat can fail for one image removed after globbing while continuing
        # to print measurements for later images. They must not be reassigned.
        with (self.store / "images/other.img").open("wb") as image:
            image.truncate(2 * 1024 * 1024)
        for name in ("lab", "other"):
            (self.store / "snapshots/lab" / (name + ".img")).touch()
        script = '''
stat() {
    local format=$2 path status=0
    shift 3
    for path in "$@"; do
        if [[ $path == */lab.img ]]; then
            printf 'simulated stat failure\\n' >&2
            status=25
        else
            command stat -c "$format" -- "$path" || return
        fi
    done
    return "$status"
}
"$@"
'''
        for command in (("list_images", "json"), ("list_snapshots", "lab"),
                        ("workspace_summary",), ("dashboard",)):
            with self.subTest(command=command):
                result = self.bash(script, *command)
                self.assertEqual(result.returncode, 25, result.stdout + result.stderr)
                self.assertIn("simulated stat failure", result.stderr)

    def test_unterminated_metadata_record_preserves_ephemeral_lifecycle(self):
        self.image.with_suffix(".img.meta").write_text("suite=trixie\nlifecycle=ephemeral")
        commands = {
            "value": 'metadata_value "$IMAGE_DIR/lab.img" lifecycle persistent',
            "lifecycle": 'image_lifecycle "$IMAGE_DIR/lab.img"',
            "json": 'image_json "$IMAGE_DIR/lab.img"',
            "copy": 'copy_metadata "$IMAGE_DIR/lab.img" "$IMAGE_DIR/copy.img"\n'
                    'metadata_value "$IMAGE_DIR/copy.img" lifecycle persistent',
            "details": 'image_details "$IMAGE_DIR/lab.img"',
            "summary": "workspace_summary",
            "dashboard": "dashboard",
        }
        for name, command in commands.items():
            with self.subTest(reader=name):
                result = self.bash(command)
                self.assertEqual(result.returncode, 0, result.stderr)
                if name == "json":
                    self.assertEqual(json.loads(result.stdout)["lifecycle"], "ephemeral")
                elif name in ("summary", "dashboard"):
                    self.assertIn("0 persistent   1 disposable", result.stdout)
                else:
                    self.assertIn("ephemeral", result.stdout)

    def test_operation_cleanup_removes_resolver_artifacts_and_preserves_status(self):
        for status in (0, 17):
            with self.subTest(status=status):
                work = self.store / "work"
                for directory in ("root", "lower", "overlay"):
                    (work / directory).mkdir(parents=True, exist_ok=True)
                for name in ("started", "resolv.conf", "resolv.conf.tmp"):
                    (work / name).write_text("temporary content")
                result = self.bash('''
begin_operation session lab
WORK=$1
exit "$2"
''', work, status)
                self.assertEqual(result.returncode, status, result.stderr)
                self.assertFalse(work.exists())
                self.assertIn("\tfailed\t" if status else "\tok\t",
                              (self.store / "activity.tsv").read_text())


if __name__ == "__main__":
    unittest.main()
