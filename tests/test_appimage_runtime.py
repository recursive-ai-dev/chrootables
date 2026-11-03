"""Portable-runtime pinning, hostile archive inputs, and dependency boundaries."""
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import appimage_runtime as runtime


class RuntimeInputsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.data = b"pinned runtime bytes"
        self.asset = {"name": "runtime.tar.gz", "url": "https://example.invalid/runtime.tar.gz",
                      "size": len(self.data), "sha256": hashlib.sha256(self.data).hexdigest(), "role": "python"}

    def test_manifest_pins_both_architectures_and_corresponding_sources(self):
        data = runtime.manifest()
        self.assertEqual(set(data["architectures"]), {"x86_64", "aarch64"})
        for architecture in data["architectures"]:
            assets = runtime.assets_for(architecture)
            self.assertEqual(len({asset["name"] for asset in assets}), len(assets))
            self.assertEqual(sum(asset["role"] == "python" for asset in assets), 1)
            self.assertEqual(sum(asset["role"] == "wheel" for asset in assets), 2)
            for asset in assets:
                self.assertRegex(asset["sha256"], r"^[a-f0-9]{64}$")
                self.assertTrue(asset["url"].startswith("https://"))
                self.assertGreater(asset["size"], 0)
                self.assertEqual(Path(asset["name"]).name, asset["name"])
        names = {asset["name"] for asset in data["sources"]}
        for component in ("qtbase-", "qtsvg-", "qtwayland-", "pyside-setup-", "Python-", "icu4c-", "python-build-standalone-"):
            self.assertTrue(any(name.startswith(component) for name in names), component)

    def test_cache_rejects_changed_bytes_symlinks_and_special_files(self):
        target = self.root / self.asset["name"]
        target.write_bytes(b"x" * len(self.data))
        with self.assertRaisesRegex(ValueError, "SHA256"):
            with runtime.verified_asset(self.root, self.asset):
                self.fail("corrupt cache accepted")
        target.unlink()
        other = self.root / "other"
        other.write_bytes(self.data)
        target.symlink_to(other)
        with self.assertRaises(ValueError):
            with runtime.verified_asset(self.root, self.asset):
                self.fail("symlink cache accepted")
        target.unlink()
        os.mkfifo(target)
        with self.assertRaises(ValueError):
            with runtime.verified_asset(self.root, self.asset):
                self.fail("FIFO cache accepted")

    def test_verified_descriptor_survives_path_replacement(self):
        target = self.root / self.asset["name"]
        target.write_bytes(self.data)
        with runtime.verified_asset(self.root, self.asset) as stream:
            target.unlink()
            target.write_bytes(b"replacement")
            self.assertEqual(stream.read(), self.data)

    def test_download_is_pinned_and_published_without_partial_or_overwrite(self):
        for content in (self.data + b"extra", b"x" * len(self.data)):
            with self.subTest(content=content), patch.object(runtime, "assets_for", return_value=[self.asset]), \
                    patch.object(runtime, "urlopen", return_value=io.BytesIO(content)):
                with self.assertRaises(ValueError):
                    runtime.fetch(self.root, "x86_64")
                self.assertEqual(list(self.root.iterdir()), [])
        with patch.object(runtime, "assets_for", return_value=[self.asset]), \
                patch.object(runtime, "urlopen", return_value=io.BytesIO(self.data)):
            runtime.fetch(self.root, "x86_64")
        self.assertEqual((self.root / self.asset["name"]).read_bytes(), self.data)
        self.assertEqual(len(list(self.root.iterdir())), 1)
        with patch.object(runtime, "assets_for", return_value=[self.asset]), patch.object(runtime, "urlopen") as network:
            runtime.fetch(self.root, "x86_64")
        network.assert_not_called()

    def python_tar(self, entries):
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode="w:gz") as archive:
            for name, payload, kind in entries:
                member = tarfile.TarInfo(name)
                if kind == "symlink":
                    member.type, member.linkname = tarfile.SYMTYPE, payload
                    archive.addfile(member)
                else:
                    member.size = len(payload)
                    archive.addfile(member, io.BytesIO(payload))
        stream.seek(0)
        return stream

    def test_python_extraction_rejects_traversal_and_external_links(self):
        for index, entry in enumerate((
                ("python/lib/../../escape", b"bad", "file"),
                ("python/lib/link", "../../outside", "symlink"),
                ("python/lib/link", "/etc/passwd", "symlink"))):
            with self.subTest(entry=entry), self.assertRaises(ValueError):
                runtime.extract_python(self.python_tar([entry]), self.root / f"runtime{index}")
        self.assertFalse((self.root / "escape").exists())

    def test_python_selection_keeps_runtime_and_omits_development_payload(self):
        entries = [(name, b"payload", "file") for name in (
            "python/bin/python3.13", "python/lib/python3.13/os.py", "python/lib/python3.13/LICENSE.txt",
            "python/lib/python3.13/test/test_os.py", "python/lib/python3.13/site-packages/pip/__init__.py",
            "python/include/python3.13/Python.h", "python/lib/python3.13/__pycache__/os.pyc")]
        destination = self.root / "runtime"
        runtime.extract_python(self.python_tar(entries), destination)
        files = {path.relative_to(destination).as_posix() for path in destination.rglob("*") if path.is_file()}
        self.assertEqual(files, {"bin/python3.13", "lib/python3.13/os.py", "lib/python3.13/LICENSE.txt"})

    def test_license_directories_preserve_lgpl_text_and_attributions(self):
        entries = [(name, b"notice", "file") for name in (
            "component/LICENSES/LGPL-3.0-only.txt", "component/COPYING", "component/LICENSE.third-party.txt",
            "component/src/COPYRIGHT.txt", "component/src/TERMS.txt", "component/src/CREDITS.txt")]
        entries += [("component/src/qt_attribution.json",
                     json.dumps({"LicenseFile": "TERMS.txt", "CopyrightFile": "CREDITS.txt"}).encode(), "file"),
                    ("component/src/implementation.c", b"code", "file")]
        destination = self.root / "notices"
        runtime.extract_licenses(self.python_tar(entries), destination)
        files = {path.relative_to(destination).as_posix() for path in destination.rglob("*") if path.is_file()}
        self.assertEqual(files, {entry[0] for entry in entries[:-1]})

    def test_attribution_references_cannot_escape_or_silently_go_missing(self):
        for index, reference in enumerate(("../../../outside", "/outside", "missing.txt")):
            record = json.dumps({"LicenseFile": reference}).encode()
            with self.subTest(reference=reference), self.assertRaises(ValueError):
                runtime.extract_licenses(self.python_tar([("component/qt_attribution.json", record, "file")]),
                                         self.root / f"notices{index}")

    def test_wheel_selection_is_bounded_to_used_modules_and_plugins(self):
        for name in ("PySide6/QtCore.abi3.so", "PySide6/Qt/plugins/platforms/libqxcb.so",
                     "PySide6/Qt/plugins/platforms/libqwayland.so", "PySide6/Qt/plugins/imageformats/libqsvg.so",
                     "PySide6/Qt/lib/libQt6DBus.so.6", "PySide6/Qt/lib/libicuuc.so.73"):
            self.assertTrue(runtime.wheel_member(name), name)
        for name in ("PySide6/Qt/plugins", "PySide6/Qt/plugins/", "PySide6/Qt/plugins/platforms/",
                     "PySide6/designer", "PySide6/QtQml.abi3.so", "PySide6/Qt/plugins/imageformats/libqpdf.so",
                     "PySide6/Qt/plugins/platforminputcontexts/libqtvirtualkeyboardplugin.so",
                     "PySide6/Qt/plugins/platformthemes/libqgtk3.so"):
            self.assertFalse(runtime.wheel_member(name), name)
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            archive.writestr("PySide6/Qt/lib/../../../../libQt6Core.so.6", b"bad")
        stream.seek(0)
        with self.assertRaises(ValueError):
            runtime.extract_wheel(stream, self.root)
        self.assertFalse((self.root.parent / "libQt6Core.so.6").exists())

    def test_expansion_limits_reject_archive_bombs(self):
        with patch.object(runtime, "MAX_EXPANDED_BYTES", 4):
            with self.assertRaisesRegex(ValueError, "size limit"):
                runtime.extract_python(self.python_tar([("python/lib/os.py", b"12345", "file")]), self.root / "runtime")


if __name__ == "__main__":
    unittest.main()
