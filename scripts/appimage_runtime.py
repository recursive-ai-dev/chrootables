"""Pinned portable Python/Qt inputs, safe extraction, and source redistribution."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import tarfile
import tempfile
from urllib.request import Request, urlopen
import zipfile

from package_support import ROOT, normalize_tree, publish_artifact, source_archive

MANIFEST_PATH = ROOT / "packaging/appimage-runtime.json"
PYTHON_SERIES = "3.13"
MAX_EXPANDED_BYTES = 1024 * 1024 * 1024
QT_LIBRARIES = {f"libQt6{name}.so.6" for name in (
    "Core", "Gui", "Widgets", "DBus", "OpenGL", "WaylandClient",
    "WlShellIntegration", "XcbQpa", "Svg")}
QT_PLATFORMS = {"libqoffscreen.so", "libqminimal.so", "libqxcb.so", "libqwayland.so"}


def manifest():
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def assets_for(architecture, *, sources_only=False):
    data = manifest()
    if architecture not in data["architectures"]:
        raise ValueError(f"No pinned portable runtime for architecture: {architecture}")
    return ([] if sources_only else data["architectures"][architecture]) + data["sources"]


@contextmanager
def verified_asset(cache, asset):
    """Validate and consume the same descriptor; never follow a cache symlink."""
    path = Path(cache) / asset["name"]
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as error:
        raise ValueError(f"Missing or unsafe runtime input: {path}. Run scripts/fetch-appimage-runtime.py first.") from error
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size != asset["size"]:
            raise ValueError(f"Runtime input has an unexpected type or size: {path}")
        digest = hashlib.sha256()
        count = 0
        for chunk in iter(lambda: stream.read(min(1024 * 1024, asset["size"] + 1 - count)), b""):
            count += len(chunk)
            if count > asset["size"]:
                raise ValueError(f"Runtime input grew beyond its pinned size: {path}")
            digest.update(chunk)
        if digest.hexdigest() != asset["sha256"]:
            raise ValueError(f"Runtime input failed SHA256 validation: {path}")
        stream.seek(0)
        yield stream


def fetch(cache, architecture):
    cache = Path(cache)
    cache.mkdir(parents=True, exist_ok=True)
    for asset in assets_for(architecture):
        destination = cache / asset["name"]
        if os.path.lexists(destination):
            with verified_asset(cache, asset):
                pass
            print(destination, flush=True)
            continue
        with tempfile.NamedTemporaryFile(prefix=".runtime-download-", dir=cache, delete=False) as output:
            staged = Path(output.name)
            try:
                request = Request(asset["url"], headers={"User-Agent": "Chrootables-release-builder"})
                with urlopen(request, timeout=60) as response:
                    digest, count = hashlib.sha256(), 0
                    while chunk := response.read(1024 * 1024):
                        count += len(chunk)
                        if count > asset["size"]:
                            raise ValueError(f"Runtime download exceeded its pinned size: {asset['name']}")
                        digest.update(chunk)
                        output.write(chunk)
                if count != asset["size"] or digest.hexdigest() != asset["sha256"]:
                    raise ValueError(f"Runtime download failed SHA256 validation: {asset['name']}")
                output.flush()
                os.fchmod(output.fileno(), 0o644)
                os.fsync(output.fileno())
                try:
                    os.link(staged, destination)
                except FileExistsError:
                    with verified_asset(cache, asset):
                        pass
                directory = os.open(cache, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
                print(destination, flush=True)
            finally:
                staged.unlink(missing_ok=True)


def safe_path(root, name):
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or "\x00" in name or not path.parts:
        raise ValueError(f"Unsafe runtime archive path: {name!r}")
    destination = root.joinpath(*path.parts)
    if not destination.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"Runtime archive path escapes its destination: {name!r}")
    return destination


def python_member(name):
    parts = PurePosixPath(name).parts
    if not parts or parts[0] != "python":
        raise ValueError("Portable Python archive has an unexpected layout.")
    relative = PurePosixPath(*parts[1:]).as_posix()
    if relative in ("bin", "bin/python3", f"bin/python{PYTHON_SERIES}", "lib"):
        return True
    if not relative.startswith("lib/"):
        return False
    if any(part in {"site-packages", "__pycache__", "test", "tests", "idlelib", "tkinter",
                    "turtledemo", "ensurepip", "pkgconfig"} or part.startswith("config-")
           for part in parts[1:]):
        return False
    return not (parts[-1].startswith("_test") or relative.endswith((".pyc", ".pyo", ".a", ".h")))


def extract_python(stream, destination):
    destination.mkdir(parents=True)
    total = 0
    with tarfile.open(fileobj=stream, mode="r:gz") as archive:
        for member in archive:
            if not python_member(member.name):
                continue
            relative = PurePosixPath(*PurePosixPath(member.name).parts[1:]).as_posix()
            target = safe_path(destination, relative)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif member.issym():
                if PurePosixPath(member.linkname).is_absolute():
                    raise ValueError("Portable Python contains an absolute symbolic link.")
                resolved = (target.parent / member.linkname).resolve()
                if not resolved.is_relative_to(destination.resolve()):
                    raise ValueError("Portable Python symbolic link escapes its runtime.")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.symlink_to(member.linkname)
            elif member.isfile():
                total += member.size
                if total > MAX_EXPANDED_BYTES:
                    raise ValueError("Portable Python exceeds the extraction size limit.")
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.extractfile(member) as source, target.open("xb") as output:
                    shutil.copyfileobj(source, output)
            else:
                raise ValueError("Portable Python contains a special file or hard link.")


def wheel_member(name):
    parts = PurePosixPath(name).parts
    if not parts:
        return False
    if ".dist-info" in parts[0]:
        return parts[-1] in {"METADATA", "WHEEL"} or "licenses" in parts
    if parts[0] == "shiboken6":
        return len(parts) == 2 and (name.endswith(".py") or ".so" in parts[-1])
    if parts[0] != "PySide6":
        return False
    if len(parts) == 2:
        return (name.endswith(".py") or parts[-1] in {"QtCore.abi3.so", "QtGui.abi3.so", "QtWidgets.abi3.so"}
                or parts[-1].startswith("libpyside6.abi3.so"))
    if parts[1:3] == ("Qt", "lib"):
        return parts[-1] in QT_LIBRARIES or parts[-1].startswith("libicu")
    if parts[1:3] == ("Qt", "plugins"):
        if len(parts) < 5:
            return False
        if parts[3] == "platforms":
            return parts[-1] in QT_PLATFORMS
        if parts[3] == "imageformats":
            # PDF is supplied by Qt Addons, and unused image-format extensions
            # need additional source modules. Keep the Base/SVG formats we use.
            return parts[-1] in {"libqgif.so", "libqico.so", "libqjpeg.so", "libqsvg.so"}
        if parts[3] == "platforminputcontexts":
            return parts[-1] in {"libcomposeplatforminputcontextplugin.so", "libibusplatforminputcontextplugin.so"}
        if parts[3] == "platformthemes":
            return parts[-1] == "libqxdgdesktopportal.so"
        return parts[3] in {"wayland-graphics-integration-client", "wayland-shell-integration"}
    return False


def extract_wheel(stream, destination):
    total = 0
    with zipfile.ZipFile(stream) as archive:
        for member in archive.infolist():
            if not wheel_member(member.filename):
                continue
            target = safe_path(destination, member.filename)
            mode = member.external_attr >> 16
            if stat.S_ISLNK(mode) or member.is_dir():
                raise ValueError("Runtime wheel contains an unexpected directory or symbolic link.")
            total += member.file_size
            if total > MAX_EXPANDED_BYTES:
                raise ValueError("Runtime wheel exceeds the extraction size limit.")
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as source, target.open("xb") as output:
                shutil.copyfileobj(source, output)


def extract_licenses(stream, destination):
    """Keep upstream notices, license texts, and third-party attribution records."""
    with tarfile.open(fileobj=stream, mode="r:*") as archive:
        for member in archive:
            parts = PurePosixPath(member.name).parts
            basename = parts[-1].lower() if parts else ""
            in_license_directory = any(part.lower() in {"license", "licenses"} for part in parts[:-1])
            if not member.isfile() or not ("license" in basename or "copying" in basename
                                           or basename == "qt_attribution.json" or in_license_directory):
                continue
            if member.size > 2 * 1024 * 1024:
                raise ValueError("Unexpectedly large third-party license text.")
            target = safe_path(destination, member.name)
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.extractfile(member) as source, target.open("xb") as output:
                shutil.copyfileobj(source, output)


def stage_runtime(stage, cache, architecture):
    assets = assets_for(architecture)
    # Validate all inputs before extracting or executing any runtime code.
    for asset in assets:
        with verified_asset(cache, asset):
            pass
    application = stage / "usr/lib/chrootables"
    runtime = application / "runtime"
    licenses = stage / "usr/share/doc/chrootables/third-party"
    for asset in assets:
        with verified_asset(cache, asset) as stream:
            if asset["role"] == "python":
                extract_python(stream, runtime)
            elif asset["role"] == "wheel":
                extract_wheel(stream, runtime / f"lib/python{PYTHON_SERIES}/site-packages")
            else:
                extract_licenses(stream, licenses)
    shutil.copyfile(MANIFEST_PATH, application / "runtime-manifest.json")
    shutil.copyfile(ROOT / "packaging/APPIMAGE-RUNTIME-NOTICE.txt", licenses / "NOTICE.txt")
    return {f"usr/lib/chrootables/runtime/bin/python{PYTHON_SERIES}"}


def build_sources(output, cache, version, epoch, *, force=False):
    """Publish unchanged upstream sources beside the binaries on every release."""
    package = output / f"Chrootables-{version}-runtime-sources.tar.gz"
    with tempfile.TemporaryDirectory(prefix=".chrootables-sources-", dir=output) as directory:
        work = Path(directory)
        stage = work / "sources"
        stage.mkdir()
        for asset in manifest()["sources"]:
            with verified_asset(cache, asset) as source, (stage / asset["name"]).open("xb") as target:
                shutil.copyfileobj(source, target)
        shutil.copyfile(MANIFEST_PATH, stage / "runtime-manifest.json")
        shutil.copyfile(ROOT / "packaging/APPIMAGE-RUNTIME-NOTICE.txt", stage / "NOTICE.txt")
        normalize_tree(stage, epoch)
        staged = work / package.name
        source_archive(stage, staged, epoch)
        return publish_artifact(staged, package, force=force)
