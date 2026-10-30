"""Pinned build tools must never overwrite or read unsafe cache entries."""
import hashlib
import importlib.util
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("fetch_build_tools", ROOT / "scripts/fetch-build-tools.py")
FETCH = importlib.util.module_from_spec(spec)
spec.loader.exec_module(FETCH)


class BuildToolTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.payload = b"pinned executable bytes"
        self.expected = hashlib.sha256(self.payload).hexdigest()
        self.target = self.root / "tool"
        for name, value in (("TOOLS", {"tool": ("https://example.invalid/tool", self.expected)}),):
            patcher = patch.object(FETCH, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        machine = patch.object(FETCH.platform, "machine", return_value="x86_64")
        machine.start()
        self.addCleanup(machine.stop)

    def test_download_and_cached_reuse_validate_hash(self):
        with patch.object(FETCH, "urlopen", return_value=io.BytesIO(self.payload)) as download:
            FETCH.fetch(self.root)
            FETCH.fetch(self.root)
        self.assertEqual(download.call_count, 1)
        self.assertEqual(self.target.read_bytes(), self.payload)
        self.assertEqual(self.target.stat().st_mode & 0o777, 0o755)
        self.assertFalse(list(self.root.glob(".download-*")))

    def test_unsafe_cache_entries_are_rejected_without_downloading(self):
        outside = self.root / "outside"
        outside.write_bytes(self.payload)
        for kind in ("symlink", "dangling", "directory", "fifo", "corrupt", "oversized"):
            with self.subTest(kind=kind):
                if kind in ("symlink", "dangling"):
                    self.target.symlink_to(outside if kind == "symlink" else self.root / "absent")
                elif kind == "directory":
                    self.target.mkdir()
                elif kind == "fifo":
                    os.mkfifo(self.target)
                elif kind == "corrupt":
                    self.target.write_bytes(b"untrusted")
                else:
                    with self.target.open("wb") as stream:
                        stream.truncate(FETCH.MAX_TOOL_BYTES + 1)
                with patch.object(FETCH, "urlopen") as download:
                    with self.assertRaises(ValueError):
                        FETCH.fetch(self.root)
                    download.assert_not_called()
                if kind == "directory":
                    self.target.rmdir()
                else:
                    self.target.unlink()
        self.assertEqual(outside.read_bytes(), self.payload)

    def test_concurrent_destination_is_preserved(self):
        for competing in (self.payload, b"another writer's file"):
            with self.subTest(competing=competing):
                def download(*args, **kwargs):
                    self.target.write_bytes(competing)
                    return io.BytesIO(self.payload)
                with patch.object(FETCH, "urlopen", side_effect=download):
                    if competing == self.payload:
                        FETCH.fetch(self.root)
                    else:
                        with self.assertRaisesRegex(ValueError, "checksum"):
                            FETCH.fetch(self.root)
                self.assertEqual(self.target.read_bytes(), competing)
                self.assertFalse(list(self.root.glob(".download-*")))
                self.target.unlink()

    def test_bad_or_oversized_download_never_publishes(self):
        for payload, limit in ((b"wrong checksum", FETCH.MAX_TOOL_BYTES), (self.payload, 4)):
            with self.subTest(limit=limit), patch.object(FETCH, "MAX_TOOL_BYTES", limit), \
                    patch.object(FETCH, "urlopen", return_value=io.BytesIO(payload)):
                with self.assertRaises(ValueError):
                    FETCH.fetch(self.root)
                self.assertFalse(self.target.exists())
                self.assertFalse(list(self.root.glob(".download-*")))


if __name__ == "__main__":
    unittest.main()
