#!/usr/bin/env python3
"""Install a per-user menu entry pointing at this checkout; no root required."""
import os
from pathlib import Path
import shutil
import sys

root = Path(__file__).resolve().parent.parent
configured_data = os.environ.get("XDG_DATA_HOME", "")
data = Path(configured_data) if configured_data and Path(configured_data).is_absolute() else Path.home() / ".local/share"
applications = data / "applications"
icons = data / "icons/hicolor/scalable/apps"
applications.mkdir(parents=True, exist_ok=True)
icons.mkdir(parents=True, exist_ok=True)

def desktop_quote(value):
    # Desktop Exec quoting differs from shell quoting; escape field-code percent signs.
    value = str(value).replace("%", "%%")
    for character in ('\\', '"', '`', '$'):
        value = value.replace(character, "\\" + character)
    # The desktop file parser unescapes the value before Exec parses its arguments.
    value = value.replace("\\", "\\\\").replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")
    return '"' + value + '"'

entry = applications / "io.github.chrootables.Desktop.desktop"
entry.write_text("\n".join([
    "[Desktop Entry]", "Version=1.0", "Type=Application", "Name=Chrootables",
    "Comment=Build, snapshot and explore local Debian workspaces",
    "Exec=" + desktop_quote(sys.executable) + " " + desktop_quote(root / "chrootables-desktop"),
    "Icon=io.github.chrootables.Desktop", "Terminal=false",
    "Categories=Development;", "Keywords=Linux;Debian;chroot;workspace;snapshot;container;",
    "StartupNotify=true", "Actions=Tour;", "", "[Desktop Action Tour]",
    "Name=Explore the desktop tour", "Exec=" + desktop_quote(sys.executable) + " " + desktop_quote(root / "chrootables-desktop") + " --demo", "",
]))
shutil.copyfile(root / "assets/io.github.chrootables.Desktop.svg", icons / "io.github.chrootables.Desktop.svg")
print(f"Installed menu entry: {entry}")
print(f"Launch: {root / 'chrootables-desktop'}")
