import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

try:
    import gi
    try:
        gi.require_version("GioUnix", "2.0")
        from gi.repository import GioUnix as desktop_api
    except (ImportError, ValueError):
        from gi.repository import Gio as desktop_api
except ImportError:
    desktop_api = None


ROOT = Path(__file__).resolve().parent.parent


class DesktopInstallerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / 'checkout "quotes" $dollar `ticks` \\backslash %percent\nnewline\ttab'
        (self.root / "scripts").mkdir(parents=True)
        (self.root / "assets").mkdir()
        shutil.copyfile(ROOT / "scripts/install-desktop.py", self.root / "scripts/install-desktop.py")
        shutil.copyfile(ROOT / "assets/io.github.chrootables.Desktop.svg",
                        self.root / "assets/io.github.chrootables.Desktop.svg")
        # Record real desktop launches without opening the application or touching a store.
        (self.root / "chrootables-desktop").write_text(
            "import json, pathlib, sys\n"
            "output = pathlib.Path(__file__).with_name('launch.json')\n"
            "temporary = output.with_suffix('.tmp')\n"
            "temporary.write_text(json.dumps(sys.argv[1:]))\n"
            "temporary.replace(output)\n"
        )
        data = Path(temporary.name) / "xdg-data"
        subprocess.run([sys.executable, str(self.root / "scripts/install-desktop.py")],
                       env={**os.environ, "XDG_DATA_HOME": str(data)},
                       capture_output=True, text=True, check=True)
        self.entry = data / "applications/io.github.chrootables.Desktop.desktop"

    def test_empty_relative_and_absolute_xdg_data_home(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            for index, configured in enumerate((None, "", "relative-data", str(base / "absolute-data"))):
                with self.subTest(configured=configured):
                    user_home = base / str(index)
                    user_home.mkdir()
                    environment = {**os.environ, "HOME": str(user_home)}
                    environment.pop("XDG_DATA_HOME", None)
                    if configured is not None:
                        environment["XDG_DATA_HOME"] = configured
                    subprocess.run([sys.executable, str(self.root / "scripts/install-desktop.py")],
                                   env=environment, cwd=user_home, capture_output=True, check=True)
                    expected = Path(configured) if configured and Path(configured).is_absolute() else user_home / ".local/share"
                    self.assertTrue((expected / "applications/io.github.chrootables.Desktop.desktop").is_file())
                    self.assertTrue((expected / "icons/hicolor/scalable/apps/io.github.chrootables.Desktop.svg").is_file())
                    self.assertFalse((user_home / "applications").exists())
                    self.assertFalse((user_home / "relative-data").exists())

    @unittest.skipUnless(shutil.which("desktop-file-validate"), "desktop-file-validate is unavailable")
    def test_special_checkout_path_produces_valid_desktop_entry(self):
        result = subprocess.run(["desktop-file-validate", str(self.entry)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    @unittest.skipIf(desktop_api is None, "PyGObject is unavailable")
    def test_main_and_tour_actions_launch_from_special_checkout_path(self):
        application = desktop_api.DesktopAppInfo.new_from_filename(str(self.entry))
        self.assertIsNotNone(application)
        output = self.root / "launch.json"
        for action, expected in ((None, []), ("Tour", ["--demo"])):
            with self.subTest(action=action):
                if action is None:
                    self.assertTrue(application.launch([], None))
                else:
                    # Older Gio typelibs expose this as a static function;
                    # passing the instance explicitly also works with GioUnix.
                    desktop_api.DesktopAppInfo.launch_action(application, action, None)
                deadline = time.monotonic() + 5
                while not output.exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertTrue(output.exists(), "The desktop action did not start the launcher.")
                self.assertEqual(json.loads(output.read_text()), expected)
                output.unlink()


if __name__ == "__main__":
    unittest.main()
