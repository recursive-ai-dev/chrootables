"""Shared, unprivileged release staging and publication for every package format."""
from __future__ import annotations

import ast
from datetime import datetime, timezone
import fcntl
import gzip
import hashlib
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_EPOCH = 1793059200  # 2026-10-27 00:00:00 UTC
APPLICATION_ID = "io.github.chrootables.Desktop"
EXECUTABLES = {"usr/lib/chrootables/chrootable-manager",
               "usr/lib/chrootables/chrootables-desktop"}


def upstream_version(root=ROOT):
    """Read metadata without importing Qt or executing application code."""
    tree = ast.parse((root / "chrootables/__init__.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == "__version__"
                for target in node.targets):
            value = ast.literal_eval(node.value)
            if isinstance(value, str) and re.fullmatch(r"[0-9][0-9A-Za-z.+~]*", value):
                return value
    raise ValueError("chrootables/__init__.py must define a valid __version__.")


def build_metadata(revision):
    if not isinstance(revision, str) or not re.fullmatch(r"[0-9][0-9A-Za-z.+~]*", revision):
        raise ValueError("--revision must start with a digit and contain only letters, digits, ., + or ~.")
    value = os.environ.get("SOURCE_DATE_EPOCH", str(DEFAULT_EPOCH))
    if not re.fullmatch(r"[0-9]+", value):
        raise ValueError("SOURCE_DATE_EPOCH must be a nonnegative integer timestamp.")
    try:
        epoch = int(value)
        date = datetime.fromtimestamp(epoch, timezone.utc)
    except (OverflowError, OSError, ValueError) as error:
        raise ValueError("SOURCE_DATE_EPOCH is outside the supported timestamp range.") from error
    return upstream_version(), epoch, date


def require_tool(name, configured=None, hint=""):
    """Resolve only installed or explicitly supplied tools; never install/download."""
    selected = os.fspath(configured) if configured else name
    resolved = shutil.which(selected)
    if not resolved:
        raise ValueError(f"{name} is required. {hint}".strip())
    return str(Path(resolved).resolve())


def compressed_copy(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("wb") as output:
        with gzip.GzipFile(filename="", mode="wb", fileobj=output,
                           compresslevel=9, mtime=0) as compressed:
            compressed.write(source.read_bytes())


def license_identifier(root=ROOT):
    metadata = root / "packaging/license.spdx"
    if not metadata.is_file():
        return "LicenseRef-Proprietary"
    expression = metadata.read_text(encoding="utf-8").strip()
    if not expression or not re.fullmatch(r"[A-Za-z0-9.+() -]+", expression):
        raise ValueError("packaging/license.spdx must contain a single SPDX license expression.")
    if not any((root / name).is_file() for name in ("LICENSE", "LICENSE.txt", "COPYING")):
        raise ValueError("License metadata requires an upstream LICENSE, LICENSE.txt or COPYING file.")
    return expression


def normalize_tree(stage, epoch, extra_executables=()):
    executable_paths = EXECUTABLES | set(extra_executables)
    for path in [stage, *sorted(stage.rglob("*"))]:
        if not path.is_symlink():
            relative = path.relative_to(stage).as_posix()
            path.chmod(0o755 if path.is_dir() or relative in executable_paths else 0o644)
        os.utime(path, (epoch, epoch), follow_symlinks=False)


def stage_payload(stage, epoch, root=ROOT):
    """Use the same relocatable app/CLI/helper layout in DEB, RPM and AppImage."""
    app = stage / "usr/lib/chrootables"
    app.mkdir(parents=True)
    shutil.copytree(root / "chrootables", app / "chrootables",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"))
    shutil.copytree(root / "assets", app / "assets")
    for name in ("chrootable-manager", "chrootables-desktop"):
        source = (root / name).read_text(encoding="utf-8")
        interpreter = "/usr/bin/python3 -I" if name.endswith("desktop") else "/bin/bash"
        (app / name).write_text(f"#!{interpreter}\n" + source.split("\n", 1)[1], encoding="utf-8")
        link = stage / "usr/bin" / name
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(f"../lib/chrootables/{name}")
    for source, destination in (
        (f"packaging/{APPLICATION_ID}.desktop", f"usr/share/applications/{APPLICATION_ID}.desktop"),
        (f"assets/{APPLICATION_ID}.svg", f"usr/share/icons/hicolor/scalable/apps/{APPLICATION_ID}.svg"),
    ):
        target = stage / destination
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / source, target)
    docs = stage / "usr/share/doc/chrootables"
    compressed_copy(root / "README.md", docs / "README.md.gz")
    compressed_copy(root / "CHANGELOG.md", docs / "changelog.gz")
    for name in ("chrootables-desktop", "chrootable-manager"):
        compressed_copy(root / f"packaging/{name}.1", stage / f"usr/share/man/man1/{name}.1.gz")
    # Include the actual upstream terms. An absent license never becomes MIT.
    license_identifier(root)
    for name in ("LICENSE", "LICENSE.txt", "COPYING"):
        if (root / name).is_file():
            shutil.copyfile(root / name, docs / "copyright")
            break
    if (root / "NOTICE").is_file():
        shutil.copyfile(root / "NOTICE", docs / "NOTICE")
    normalize_tree(stage, epoch)
    return stage


def source_archive(stage, target, epoch):
    """A normalized payload archive, independent of staging paths and the umask."""
    with target.open("wb") as output:
        with gzip.GzipFile(filename="", mode="wb", fileobj=output, mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w", format=tarfile.GNU_FORMAT) as archive:
                for path in sorted(stage.rglob("*")):
                    info = archive.gettarinfo(str(path), path.relative_to(stage).as_posix())
                    info.uid = info.gid = 0
                    info.uname = info.gname = "root"
                    info.mtime = epoch
                    if info.isfile():
                        with path.open("rb") as stream:
                            archive.addfile(info, stream)
                    else:
                        archive.addfile(info)


def run_tool(command, *, env=None, cwd=None):
    try:
        return subprocess.run(command, env=env, cwd=cwd, text=True,
                              capture_output=True, check=True)
    except subprocess.CalledProcessError as error:
        details = "\n".join(part.strip() for part in (error.stdout, error.stderr) if part)
        raise ValueError(f"{Path(command[0]).name} failed ({error.returncode}):\n{details}") from error


def file_digest(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def publish_artifact(staged, destination, *, force=False, executable=False):
    """Check both outputs before publishing either, with a shared directory lock."""
    if not stat.S_ISREG(staged.lstat().st_mode):
        raise ValueError("The build did not produce a regular artifact.")
    digest = file_digest(staged)
    checksum = destination.with_suffix(destination.suffix + ".sha256")
    content = f"{digest}  {destination.name}\n"
    staged_checksum = staged.parent / checksum.name
    staged_checksum.write_text(content, encoding="ascii")
    staged_checksum.chmod(0o644)
    staged.chmod(0o755 if executable else 0o644)
    descriptor = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        for path in (destination, checksum):
            try:
                info = path.lstat()
            except FileNotFoundError:
                continue
            if not stat.S_ISREG(info.st_mode):
                raise ValueError(f"Refusing to replace a non-regular output: {path}")
            matches = (file_digest(path) == digest if path == destination
                       else path.read_bytes() == content.encode("ascii"))
            if not matches and not force:
                raise ValueError(f"{path} already exists with different contents; use --force to replace it.")
        # A successful rename alone does not make the package contents durable.
        # Flush both staged files before exposing either final name.
        for path in (staged, staged_checksum):
            with path.open("rb") as stream:
                os.fsync(stream.fileno())
        if force or not destination.exists():
            os.replace(staged, destination)
        if force or not checksum.exists():
            os.replace(staged_checksum, checksum)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return destination
