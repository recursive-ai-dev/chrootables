#!/usr/bin/python3
"""Build a host-runtime AppImage; target hosts need Python 3.10+ and PySide6 6.8+."""
import argparse
import os
from pathlib import Path
import platform
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
from package_support import (APPLICATION_ID, ROOT, build_metadata, normalize_tree,
                             publish_artifact, require_tool, run_tool, stage_payload,
                             upstream_version)

APPRUN = '''#!/bin/bash
set -eu
APPDIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# Root helpers cannot reliably read a user's FUSE mount. Re-execute through the
# runtime's extraction path before starting Python or authenticating with polkit.
if [[ -n "${APPIMAGE:-}" && -z "${CHROOTABLE_APPIMAGE_EXTRACTED:-}" && -z "${APPIMAGE_EXTRACT_AND_RUN:-}" ]]; then
    export CHROOTABLE_APPIMAGE_EXTRACTED=1
    exec "$APPIMAGE" --appimage-extract-and-run "$@"
fi
if [[ ! -x /usr/bin/python3 ]] || ! /usr/bin/python3 -I -c '
import sys
if sys.version_info < (3, 10):
    raise SystemExit(1)
import PySide6
if PySide6.__version_info__ < (6, 8):
    raise SystemExit(1)
'; then
    printf '%s\\n' 'Chrootables requires host /usr/bin/python3 3.10+ and PySide6 6.8+.' >&2
    printf '%s\\n' 'Install your distribution Python/Qt packages; see the bundled README.' >&2
    exit 1
fi
exec "$APPDIR/usr/bin/chrootables-desktop" "$@"
'''


def architecture():
    machine = platform.machine().lower()
    aliases = {"amd64": "x86_64", "arm64": "aarch64"}
    machine = aliases.get(machine, machine)
    if machine not in ("x86_64", "aarch64"):
        raise ValueError(f"Unsupported AppImage runtime architecture: {machine}")
    return machine


def stage_appimage(stage, epoch):
    stage_payload(stage, epoch)
    (stage / "AppRun").write_text(APPRUN, encoding="utf-8")
    desktop = (ROOT / f"packaging/{APPLICATION_ID}.desktop").read_text(encoding="utf-8")
    desktop = desktop.replace("Exec=chrootables-desktop", "Exec=AppRun")
    (stage / f"{APPLICATION_ID}.desktop").write_text(desktop, encoding="utf-8")
    (stage / f"{APPLICATION_ID}.svg").symlink_to(
        f"usr/share/icons/hicolor/scalable/apps/{APPLICATION_ID}.svg")
    (stage / ".DirIcon").symlink_to(f"{APPLICATION_ID}.svg")
    if (stage / "usr/share/doc/chrootables/copyright").is_file():
        (stage / "LICENSE").symlink_to("usr/share/doc/chrootables/copyright")
    normalize_tree(stage, epoch, {"AppRun"})
    return stage


def build(output, revision="1", force=False, appimagetool=None, runtime_file=None):
    version, epoch, _ = build_metadata(revision)
    tool = require_tool("appimagetool", appimagetool or os.environ.get("APPIMAGETOOL"),
                        "Supply an installed tool with --appimagetool or APPIMAGETOOL; builds never download tools.")
    runtime_file = runtime_file or os.environ.get("APPIMAGE_RUNTIME_FILE")
    if not runtime_file or not Path(runtime_file).is_file():
        raise ValueError("A pinned runtime is required. Set APPIMAGE_RUNTIME_FILE or --runtime-file; "
                         "scripts/fetch-build-tools.py downloads and verifies the release tools explicitly.")
    runtime = Path(runtime_file).resolve()
    machine = architecture()
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    package = output / f"Chrootables-{version}-{revision}-{machine}.AppImage"
    with tempfile.TemporaryDirectory(prefix=".chrootables-appimage-", dir=output) as temporary:
        work = Path(temporary)
        stage = stage_appimage(work / "AppDir", epoch)
        staged = work / package.name
        environment = {**os.environ, "ARCH": machine,
                       "APPIMAGE_EXTRACT_AND_RUN": "1", "NO_APPSTREAM": "1", "TZ": "UTC"}
        # appimagetool adds timestamp options of its own when this variable is
        # set, conflicting with some bundled mksquashfs versions. Set both times
        # explicitly instead, after normalizing the complete AppDir above.
        environment.pop("SOURCE_DATE_EPOCH", None)
        # Fixed filesystem time, owner, xattrs, compression and worker count avoid
        # host-specific SquashFS metadata and CPU-count-dependent build failures.
        options = ["-all-root", "-no-xattrs", "-processors", "1",
                   "-mkfs-time", str(epoch), "-all-time", str(epoch)]
        command = [tool, "--no-appstream", "--comp", "zstd", "--runtime-file", str(runtime)]
        for option in options:
            command += ["--mksquashfs-opt", option]
        run_tool([*command, str(stage), str(staged)], env=environment)
        if not staged.exists():
            raise ValueError("appimagetool did not create the requested AppImage.")
        return publish_artifact(staged, package, force=force, executable=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist", help="artifact directory (default: dist)")
    parser.add_argument("--revision", default="1", help="revision appended to the application version")
    parser.add_argument("--force", action="store_true", help="replace an existing AppImage whose contents differ")
    parser.add_argument("--appimagetool", help="appimagetool executable or AppImage (default: APPIMAGETOOL or PATH)")
    parser.add_argument("--runtime-file", help="verified AppImage runtime (default: APPIMAGE_RUNTIME_FILE)")
    args = parser.parse_args()
    try:
        print(build(args.output, args.revision, args.force, args.appimagetool, args.runtime_file))
    except (ValueError, OSError) as error:
        print(f"Build failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
