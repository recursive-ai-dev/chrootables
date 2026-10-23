"""Exercise the installed layout without installing or requiring root."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parent.parent


@unittest.skipUnless(shutil.which("dpkg-deb"), "requires Debian packaging tools")
class DebianPackageTests(unittest.TestCase):
    def test_installed_layout_and_read_only_helper(self):
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            subprocess.run(["/usr/bin/python3", str(ROOT / "scripts/build-deb.py"),
                            "--output", str(temporary)], check=True, capture_output=True)
            package, = temporary.glob("*.deb")
            listing = subprocess.check_output(["dpkg-deb", "--contents", str(package)], text=True)
            self.assertTrue(listing.startswith("drwxr-xr-x root/root"), listing)
            self.assertNotIn("__pycache__", listing)
            stage = temporary / "installed"
            subprocess.run(["dpkg-deb", "-x", str(package), str(stage)], check=True)
            launcher = stage / "usr/bin/chrootables-desktop"
            self.assertTrue(launcher.is_file())
            self.assertEqual(launcher.read_text().splitlines()[0], "#!/usr/bin/python3 -I")
            manager = stage / "usr/bin/chrootable-manager"
            subprocess.run([str(manager), "--help"], check=True, capture_output=True)
            broker = stage / "usr/lib/chrootables/chrootables/broker.py"
            result = subprocess.run(["/usr/bin/python3", "-I", "-S", str(broker),
                                     "--store", str(temporary / "store")],
                                    input="", text=True, capture_output=True, check=True)
            ready = json.loads(result.stdout.splitlines()[0])
            self.assertEqual(ready["event"], "ready")
            self.assertFalse(ready["writable"])


if __name__ == "__main__":
    unittest.main()
