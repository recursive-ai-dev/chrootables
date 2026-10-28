# Chrootables

A native Linux desktop workbench for Debian image environments. Create a workspace,
open a terminal, experiment in a disposable session, and return to a saved recovery
point. The Qt desktop and the Bash terminal interface use the same image store.

## Launch

### Ubuntu / Debian package

On Ubuntu 26.04, install the locally built package with:

```bash
sudo apt install ./dist/chrootables_1.0.0-1_all.deb
chrootables-desktop
```

APT installs the Qt bindings and workspace prerequisites. The package adds an
application-menu entry and the `chrootable-manager` CLI. Its desktop launcher uses
`/usr/bin/python3`, so a Homebrew Python or activated virtual environment does not
override Ubuntu's Python and Qt packages. Launch the desktop as your normal user;
workspace operations request administrator access when needed.

Build the package without root or pip dependencies:

```bash
/usr/bin/python3 scripts/build-deb.py
```

The build writes the `.deb` and a `.deb.sha256` checksum to `dist/`. Verify it:

```bash
(cd dist && sha256sum -c chrootables_1.0.0-1_all.deb.sha256)
```

Builds are reproducible across directories and umasks. `SOURCE_DATE_EPOCH`
controls timestamps (default: 2026-10-27 00:00 UTC). Use `--revision 2` for a
new Debian revision, or `--output /path/to/artifacts` for another destination.
An identical rebuild is accepted; replacing an existing package with changed
contents requires `--force`. CI publishes the package and checksum as artifacts.
Installed help is available through `man chrootables-desktop` and
`man chrootable-manager`.

The build requires `dpkg-deb` (from `dpkg`). The package contains portable Python
and Bash code (`Architecture: all`); the verified host architecture is amd64.
It requires distro PySide6 6.8 or newer, so older Ubuntu/Debian releases may not
provide suitable dependencies. Remove it with `sudo apt remove chrootables`;
workspace images in `/var/lib/chrootable-manager` remain intact.

The repository currently has no upstream license file. The builder does not
invent one; choose and add a license before publishing packages for redistribution.

### From a checkout

```bash
./chrootables-desktop
```

Requires Python 3.10+ and PySide6 6.8+. On Fedora, install the desktop dependency
with `sudo dnf install python3-pyside6`. Alternatively, use a virtual environment
and `python -m pip install -r requirements.txt`, then run the launcher from that
activated environment.

On Ubuntu 26.04, use `sudo apt install python3-pyside6.qtwidgets` and launch with
`/usr/bin/python3 ./chrootables-desktop` to use the distribution's Python bindings.

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
- Sort by favorites, name, recent use, creation time, capacity, or allocated disk
  space. Filters and sort order are remembered separately for each store.
- Save searchable personal notes for each workspace. Notes and session preferences
  belong to your desktop user and store; they are not included in image exports.
- Create Debian 13 (Trixie) or Debian 12 (Bookworm) images from minimal, C/C++,
  Python, networking, Rust, Go, and web (Node.js) profiles.
- Open a shell in the embedded terminal pane, or hand the session to Konsole,
  GNOME Terminal, or xterm. Choose a disposable session without changing the
  image's default lifecycle, optionally isolate networking, and optionally
  restrict devices.
- Share a host folder at `/shared`, optionally read-only. The desktop remembers
  your folder and access setting for each workspace and store.
- Save snapshots, restore with an automatic before-restore recovery point, and
  recover workspaces whose original images were deleted.
- Clone, expand, change lifecycle, repair ext4 filesystems, and export `.tar.gz`
  archives to a chosen location. Existing destination files are never overwritten.
- Watch operation output, inspect history and logs, and check host prerequisites.
- Scroll through 2,000 lines of terminal history with the wheel or
  `Shift+PageUp`/`Shift+PageDown`. Select text with the mouse, copy with
  `Ctrl+Shift+C`, and paste with `Ctrl+Shift+V`. Shells that request bracketed
  paste receive it; large pastes do not block the desktop.
- Use `Ctrl+N` to create, `Ctrl+F` to search, `Ctrl+Enter` to open a terminal, and
  `F5` to refresh. Workspace status refreshes every six seconds.

Window size and pane widths are restored on launch. Network isolation and device
restriction choices are remembered per workspace and store. The read-only tour
does not save these settings.

The desktop currently manages Debian images. It does not implement an OCI app
store or graphical application exports. The embedded terminal pane implements a
compact VT/ANSI subset; for full terminal fidelity use the external terminal
button in the session window.

## Support matrix

| Layer | Supported | Notes |
|---|---|---|
| Host distro | Fedora, Debian, Ubuntu; Arch and openSUSE best-effort | Auto-install uses `dnf`, `apt-get`, `pacman`, or `zypper`; other distros must install the prerequisites manually |
| Host runtime | Python 3.10+, PySide6 6.8+ | CI runs the test suite on `fedora:latest` and `debian:trixie` |
| Guest images | Debian 13 (Trixie), Debian 12 (Bookworm) | minimal, C/C++, Python, networking, Rust, Go, and web (Node.js) profiles |
| Architecture | x86_64 | ARM64 is on the roadmap but not yet verified |

## First run and administrator access

Open **Host health** and choose **Install missing dependencies** if needed. Fedora
uses `dnf`; Debian/Ubuntu uses `apt-get`; Arch uses `pacman` and openSUSE uses
`zypper` (both best-effort — package names occasionally differ, and `CHROOTABLE_AUTO_INSTALL=0`
lets you install them yourself). Setup includes debootstrap, Debian archive
signing keys, ext4 tools, namespace tools, and the terminal UI dependencies.

Host health also inspects store ownership, free disk space, kernel filesystem
support and namespace availability. Each issue includes a suggested fix. Missing
commands or signing keys enable the dependency installer; store configuration
issues require the indicated store fix. Kernel module and loop-device checks are
warnings because a read-only check cannot prove mount access. For automation,
`chrootable-manager --doctor --json` returns a versioned report with individual
checks, remedies, blocking/warning counts and storage byte counts. It exits 1
when blocking checks fail, and never installs packages or creates the store.

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
archive/      optional offline package archives (--archive-sync)
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
it runs as host root and exposes host devices through `/dev`. The opt-in
`--restricted-dev` session option replaces `/dev` with a minimal tmpfs holding
only `null`, `zero`, `full`, `random`, `urandom`, `tty`, and a fresh `devpts`
instance; no host device nodes are exposed, at the cost of tools that expect
more devices.

Debian bootstrap uses HTTPS and explicitly requires Release signature verification
against the installed Debian archive keyring. Chrootables does not depend on cpak's
signed application catalog. Distribution packages still need network access, valid
archive keys, and a working Debian mirror, unless an offline archive is synced.

### Offline creation

```bash
sudo ./chrootable-manager --archive-sync trixie            # all profiles
sudo ./chrootable-manager --archive-sync bookworm build go  # selected profiles
```

This verifies and downloads, while online, everything a create needs into
`archive/<suite>-<arch>/` in the store: a debootstrap tarball of the minbase
system, the Debian-signed apt lists, and every profile deb. After that, creates
for that suite run without network access. Synced profiles are installed with
`apt-get --no-download`, so each deb is still checked against the signed
lists. Profiles that were not synced still download from the mirror. Run the
command again to refresh the archive, and set `CHROOTABLE_ARCHIVE=0` to skip
it. Sync the suite name you create with (the desktop uses `trixie` and
`bookworm`; the CLI default is `stable`). Installing packages later inside a
session still needs the network.

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
read-only mount, and `--restricted-dev` for a minimal `/dev` without host device
nodes. Session options go before the `--` command separator and can be
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
python3 scripts/check.py
```

Tests require QtTest as well as the runtime Qt modules. Debian/Ubuntu users
with split distro bindings can install `python3-pyside6.qttest`. For isolated
testing with the pinned dependencies and the distribution interpreter:

```bash
/usr/bin/python3 -m venv .venv-test
.venv-test/bin/python -m pip install --require-hashes -r requirements.txt
.venv-test/bin/python scripts/check.py --build-deb
```

Use Python 3.10–3.14 with the pinned `requirements.txt` dependencies; those
pins do not support Python 3.15. Distribution-provided Qt bindings may support
additional Python versions. Run the checks with the same interpreter used to
install the bindings (for example, `.venv/bin/python scripts/check.py`).

The check command runs syntax checks, the full unit suite, both demos, and a
Debian package build when `dpkg-deb` is available. Build artifacts are temporary.
Use `--build-deb` to require packaging tools and
`--require-desktop-integration` to require PyGObject and `desktop-file-validate`.
On Debian, the latter dependencies are provided by `python3-gi`,
`gir1.2-glib-2.0`, and `desktop-file-utils`; use the distribution Python or a
virtual environment with access to system site packages. Missing optional
integrations are reported as skipped tests. CI requires desktop integration on
both distributions and the package build on Debian.

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
