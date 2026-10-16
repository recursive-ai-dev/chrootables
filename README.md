# Chrootables

A native Linux desktop workbench for Debian image environments. Create a workspace,
open a terminal, experiment in a disposable session, and return to a saved recovery
point. The Qt desktop and the Bash terminal interface use the same image store.

## Launch

```bash
./chrootables-desktop
```

Requires Python 3.10+ and PySide6 6.8+. On Fedora, install the desktop dependency
with `sudo dnf install python3-pyside6`. Alternatively, use a virtual environment
and `python -m pip install -r requirements.txt`, then run the launcher from that
activated environment.

Add Chrootables to your application menu:

```bash
python3 scripts/install-desktop.py
```

The menu entry points to this checkout. Run the installer again after moving the
project. It installs only a per-user launcher and icon; it does not copy the code
or require administrator access. To remove it, delete
`~/.local/share/applications/io.github.chrootables.Desktop.desktop` and the matching
icon under `~/.local/share/icons/hicolor/scalable/apps/`.

For a clearly labeled, read-only tour with sample data:

```bash
./chrootables-desktop --demo
```

## Desktop features

- Search and filter workspaces by profile, release, lifecycle, favorites, or usage.
- Create Debian 13 (Trixie) or Debian 12 (Bookworm) images from minimal, C/C++,
  Python, and networking profiles.
- Open a shell in Konsole, GNOME Terminal, or xterm. Choose a disposable session
  without changing the image's default lifecycle, and optionally isolate networking.
- Share a host folder at `/shared`, optionally read-only. The desktop remembers
  your folder and access setting for each workspace and store.
- Save snapshots, restore with an automatic before-restore recovery point, and
  recover workspaces whose original images were deleted.
- Clone, expand, change lifecycle, repair ext4 filesystems, and export `.tar.gz`
  archives to a chosen location. Existing destination files are never overwritten.
- Watch operation output, inspect history and logs, and check host prerequisites.
- Use `Ctrl+N` to create, `Ctrl+F` to search, `Ctrl+Enter` to open a terminal, and
  `F5` to refresh. Workspace status refreshes every six seconds.

The desktop currently manages Debian images. It does not implement an OCI app
store, graphical application exports, or an embedded terminal emulator. Sessions
use your native terminal's complete terminal emulation.

## First run and administrator access

Open **Host health** and choose **Install missing dependencies** if needed. Fedora
uses `dnf`; Debian/Ubuntu uses `apt-get`. Setup includes debootstrap, Debian archive
signing keys, ext4 tools, namespace tools, and the terminal UI dependencies.

The desktop window runs as your user. **Unlock workspace store** opens a polkit
administrator prompt and starts a separate, headless helper connected only to the
window through standard input/output. It accepts a fixed set of validated
operations and does not listen on a socket. Access lasts until you lock the store
or close the app. Terminal sessions authenticate separately with `sudo`.

Write operations are serialized by the desktop helper. Image locks also prevent
conflicts with other terminals and managers. Closing the desktop is blocked while
an operation is active, so an image copy or filesystem repair can finish. If the
window disappears unexpectedly, the helper finishes the running operation before
exiting. Successful operations and failures are recorded in the store's journal.

Exports remain in the protected store and are copied to your chosen destination by
a child process running as your normal user. The privileged helper opens the
source; the user process creates the destination. The destination is never written
with administrator privileges.

## Images and trust model

The default store is `/var/lib/chrootable-manager`:

```
images/       ext4 .img files and adjacent metadata
snapshots/    offline recovery images, grouped by workspace
exports/      compressed filesystem archives
logs/         operation diagnostics
activity.tsv  completed operation history
```

Use a different store with `./chrootables-desktop --store /srv/chrootables`, or set
`CHROOTABLE_HOME` for the CLI. The store must be owned by root for mutations and
must not be writable by other users. Images are fixed-capacity ext4 files; initial
creation checks free space and allocates capacity. Clones and snapshots use
reflinks when the host filesystem supports them.

Persistent sessions write directly to the image. Disposable sessions use a
read-only image with a temporary OverlayFS upper layer in RAM. Their filesystem
changes disappear when the session exits. Closing an external terminal ends its
session; busy images cannot be maintained until the session releases its lock.

To exchange files with the host, select a workspace and choose **Choose…**
under **Shared folder · /shared** before opening a terminal. Choose **Read-only
shared folder** to prevent ordinary writes through that mount, or **Clear** to
stop sharing in future sessions. These settings apply to both regular and
disposable terminals. The folder is mounted only for that session; snapshots and
exports do not include its contents. Host changes survive disposable sessions,
and files created from the workspace belong to root. Folder preferences are saved
for your desktop user and are not copied with cloned images or recovery points.

Sessions use private mount, UTS, IPC and PID namespaces. Optional network isolation
adds a private network namespace with only loopback. `/sys` is not mounted.
**This is a tool for trusted development, not a security sandbox for hostile code:**
it runs as host root and exposes host devices through `/dev`.

Debian bootstrap uses HTTPS and explicitly requires Release signature verification
against the installed Debian archive keyring. Chrootables does not depend on cpak's
signed application catalog. Distribution packages still need network access, valid
archive keys, and a working Debian mirror.

## Terminal interface

The original terminal workflow remains available:

```bash
sudo ./chrootable-manager                    # interactive dashboard
sudo ./chrootable-manager --create compiler 4096 persistent build
sudo ./chrootable-manager --enter compiler
sudo ./chrootable-manager --try compiler
sudo ./chrootable-manager --exec compiler -- gcc --version
sudo ./chrootable-manager --enter compiler --share "$HOME/projects"
sudo ./chrootable-manager --try compiler --share "$HOME/projects" --read-only -- ls /shared
sudo ./chrootable-manager --snapshot compiler clean
sudo ./chrootable-manager --restore compiler clean --yes
sudo ./chrootable-manager --list --json
./chrootable-manager --help
```

CLI configuration:

```bash
sudo CHROOTABLE_HOME=/srv/chrootables ./chrootable-manager
sudo CHROOTABLE_DEBIAN_SUITE=trixie ./chrootable-manager --create lab 2048 persistent
```

Use `--share /absolute/host/folder` with `--enter`, `--enter-isolated`, `--exec`,
or `--try` to mount one existing directory at `/shared`. Add `--read-only` for a
read-only mount. Session options go before the `--` command separator and can be
combined with `--isolated` for `--exec` and `--try`. The CLI shares only when the
option is supplied; it does not use the desktop's saved preferences. The host root
directory cannot be shared, and a guest `/shared` symlink or non-directory is
rejected. As with the rest of the session, read-only sharing is a convenience for
trusted development; privileged guest code can change mounts.

The CLI can automatically install missing host packages. Set
`CHROOTABLE_AUTO_INSTALL=0` to require explicit setup. The desktop uses that setting
for all operations except its dedicated dependency installation action.

## Development and verification

No web server, Node build, or downloaded frontend dependencies are required.

```bash
python3 -m unittest discover -s tests -p 'test_*.py' -v
bash -n chrootable-manager
QT_QPA_PLATFORM=offscreen ./chrootables-desktop --demo --screenshot /tmp/chrootables.png
```

Tests cover command validation, real CLI inventory and lock state, protected
exports, stdio broker behavior, Qt navigation, creation validation, and a real
asynchronous helper. Shared folder checks cover session arguments, mount setup,
and desktop preferences. These tests run without root and use temporary fixtures.

The opt-in integration test downloads a signed Debian root and uses temporary
images and loop mounts. Allow a few GiB of temporary free space:

```bash
sudo ./chrootable-manager --setup
sudo tests/integration.sh
sudo tests/integration.sh python  # also verifies profile installation and Python
```

It checks persistent writes, disposable rollback, network isolation, shared folder
writes and read-only access, cloning,
snapshots, automatic recovery on restore, expansion, repair, export, deletion and
restoring a deleted workspace. Its temporary store is removed afterward.
