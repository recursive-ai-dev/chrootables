#!/usr/bin/env python3
"""Explicitly fetch checksum-pinned x86_64 AppImage tools; no installation."""
import argparse
import hashlib
import os
from pathlib import Path
import platform
import tempfile
from urllib.request import Request, urlopen

TOOLS = {
    "appimagetool-1.9.1-x86_64.AppImage": (
        "https://github.com/AppImage/appimagetool/releases/download/1.9.1/appimagetool-x86_64.AppImage",
        "ed4ce84f0d9caff66f50bcca6ff6f35aae54ce8135408b3fa33abfc3cb384eb0"),
    # Runtime built from fedfad51acc2860b2916a1eef32ba4a500ff31ef. The
    # checksum prevents a moving upstream 'continuous' asset changing our build.
    "runtime-x86_64": (
        "https://github.com/AppImage/type2-runtime/releases/download/continuous/runtime-x86_64",
        "617aa77314d57c8b8e4fae9c3f0b35af7b30d5529cb69e98f64f92510ed98a46"),
}


def fetch(output):
    if platform.machine() not in ("x86_64", "amd64"):
        raise ValueError("These pinned release tools support x86_64 hosts only.")
    output.mkdir(parents=True, exist_ok=True)
    for filename, (url, expected) in TOOLS.items():
        destination = output / filename
        if destination.exists():
            if destination.is_symlink() or hashlib.sha256(destination.read_bytes()).hexdigest() != expected:
                raise ValueError(f"Existing build tool failed checksum validation: {destination}")
            destination.chmod(0o755)
            print(destination)
            continue
        with tempfile.NamedTemporaryFile(prefix=".download-", dir=output, delete=False) as stream:
            staged = Path(stream.name)
            try:
                request = Request(url, headers={"User-Agent": "Chrootables-release-builder"})
                with urlopen(request, timeout=60) as response:
                    digest, size = hashlib.sha256(), 0
                    while chunk := response.read(1024 * 1024):
                        size += len(chunk)
                        if size > 64 * 1024 * 1024:
                            raise ValueError("Build tool exceeded the expected size limit.")
                        digest.update(chunk)
                        stream.write(chunk)
                if digest.hexdigest() != expected:
                    raise ValueError(f"Downloaded {filename} failed SHA256 validation; no tool was published.")
                stream.flush()
                os.fsync(stream.fileno())
                staged.chmod(0o755)
                os.replace(staged, destination)
                print(destination)
            finally:
                staged.unlink(missing_ok=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args()
    try:
        fetch(arguments.output.resolve())
    except (OSError, ValueError) as error:
        parser.exit(1, f"Build tool download failed: {error}\n")
