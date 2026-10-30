# Chrootables

A native Linux desktop workbench for Debian image environments. Create a workspace,
open a terminal, experiment in a disposable session, and return to a saved recovery
point. The Qt desktop and the Bash terminal interface use the same image store.

## Install version 1.2.0

Download packages and SHA256 checksums from the
[1.2.0 release](https://github.com/recursive-ai-dev/chrootables/releases/tag/v1.2.0).
The native packages install Python/Qt and workspace prerequisites through your
host package manager. Launch the desktop as your normal user; workspace changes
request administrator access when needed.

### Ubuntu / Debian

```bash
sudo apt install ./chrootables_1.2.0-1_all.deb
chrootables-desktop
```

The verified Ubuntu host is 26.04. Debian Trixie and newer hosts need distro
PySide6 6.8+; older releases may not provide suitable bindings. Packaged launchers
use `/usr/bin/python3`, so activated virtual environments and Homebrew Python do
not override distribution Qt packages. The package also installs the CLI, an
application-menu entry and manual pages.

### Fedora

```bash
sudo dnf install ./chrootables-1.2.0-1.noarch.rpm
chrootables-desktop
```

The RPM uses the same application/helper layout as the Debian package. Fedora is
the tested RPM target; compatibility with RHEL derivatives is not yet verified.

### AppImage · x86_64

```bash
chmod +x Chrootables-1.2.0-1-x86_64.AppImage
./Chrootables-1.2.0-1-x86_64.AppImage
```

This is a **host-runtime AppImage**: it requires `/usr/bin/python3` 3.10+ and
system PySide6 6.8+; it does not bundle Python or Qt. On Ubuntu 26.04 install
`python3-pyside6.qtwidgets`; on Fedora install `python3-pyside6`. The launcher
extracts its payload before authenticating its helper, allowing administrator
operations to find the application outside a user-only FUSE mount. Workspace
prerequisites are still needed on the host. Use `--demo` for a read-only tour.

Each artifact has a `.sha256` sidecar. Verify it in the download directory, for
example:

```bash
sha256sum -c chrootables_1.2.0-1_all.deb.sha256
```

Removing a native package leaves `/var/lib/chrootable-manager` intact. Installed
help is available through `man chrootables-desktop` and `man chrootable-manager`.

### Build packages

Builds run without root and never install or download prerequisites implicitly.
Install `dpkg-deb` for Debian packages or `rpmbuild` for RPM packages, then run:

```bash
/usr/bin/python3 scripts/build-deb.py
/usr/bin/python3 scripts/build-rpm.py
/usr/bin/python3 scripts/build-packages.py  # Debian and RPM
```

AppImage tools are fetched only through a separate explicit command. Both the
builder and runtime downloads are pinned by SHA256:

```bash
/usr/bin/python3 scripts/fetch-build-tools.py --output "$HOME/.cache/chrootables/build-tools"
export APPIMAGETOOL="$HOME/.cache/chrootables/build-tools/appimagetool-1.9.1-x86_64.AppImage"
export APPIMAGE_RUNTIME_FILE="$HOME/.cache/chrootables/build-tools/runtime-x86_64"
/usr/bin/python3 scripts/build-appimage.py
/usr/bin/python3 scripts/build-packages.py --all
```

Artifacts and checksums go into `dist/`. Use `--output /path/to/artifacts` or
`--revision 2` to choose another destination/revision. An identical rebuild is
accepted; changed output requires `--force`. A conflicting package or checksum
is preserved when `--force` is absent. Debian and AppImage builds are checked
across output directories and umasks; reproducibility assumes the same pinned
build tools. `SOURCE_DATE_EPOCH` controls timestamps, with a stable default of
2026-10-27 00:00 UTC. `--deb-only`, `--rpm-only`, and `--appimage-only` select one
format; those flags and `--all` are mutually exclusive.

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
- Follow operation stages and elapsed time; pause output following to read earlier
  diagnostics while work continues. Each task streams only its own operation log.
- Search recovery points and activity by workspace, checkpoint, date or action;
  filter failed operations and keep the selected record across refreshes.
- Inspect allocated snapshot/export/log storage and preview the exact recovery
  point and its allocated size before deleting it.
- Choose 100%, 125% or 150% text scaling. Compact layouts keep workspace and
  recovery actions reachable on 1024×768 displays.
- Inspect history and logs, and check host prerequisites.
- Scroll through 2,000 lines of terminal history with the wheel or
  `Shift+PageUp`/`Shift+PageDown`. Select text with the mouse, copy with
  `Ctrl+Shift+C`, and paste with `Ctrl+Shift+V`. Shells that request bracketed
  paste receive it; large pastes do not block the desktop. ANSI colors, text
  attributes, cursor visibility, combining accents and East Asian wide characters
  are supported.
- Use `Ctrl+N` to create, `Ctrl+F` to search, `Ctrl+Enter` to open a terminal, and
  `F5` to refresh. Workspace status refreshes every six seconds.

Window size and pane widths are restored on launch. Network isolation and device
restriction choices are remembered per workspace and store. The read-only tour
does not save these settings.

The desktop currently manages Debian images. It does not implement an OCI app
store or graphical application exports. The embedded terminal pane implements a
compact VT/ANSI subset; full emoji grapheme shaping and complete terminal
emulation remain outside its scope. For full terminal fidelity use the external
terminal button in the session window.

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
or close the app. Terminal sessions authenticate separately with `sudo`, or with
`pkexec` on hosts without it.

Write operations are serialized by the desktop helper. Image locks also prevent
conflicts with other terminals and managers. Closing the desktop is blocked while
an operation is active, so an image copy or filesystem repair can finish. **Cancel**
stops work that can be abandoned, such as a bootstrap, copy, export or import, and
removes its staged files. Steps that change live data cannot be cancelled and always
run to completion: publication, expansion, repair, deletion and package installation.
If the
window disappears unexpectedly, the helper finishes the running operation before
exiting. Successful operations and failures are recorded in the store's journal.
Durable operation records retain the current stage and identify incomplete
publication after an abrupt interruption. Use the read-only commands:

```bash
chrootable-manager --operations --json
chrootable-manager --doctor --json
```

A root-protected store requires `sudo` for the operation listing.
When diagnostics identify a recoverable staged publication, complete that
specific transaction with administrator access:

```bash
sudo chrootable-manager --recover-operation OPERATION_ID --yes
```

Recovery validates the recorded file identities and refuses active operations or
unsafe paths. Incomplete work before publication remains available for inspection;
recovery does not automatically delete partial files or resume a bootstrap.

To abandon interrupted work instead, which also removes its staged files or sets
aside a damaged record:

```bash
sudo chrootable-manager --discard-operation OPERATION_ID --yes
```

A publication that has already replaced workspace metadata must be recovered. The
desktop's Recovery view offers both actions. Finished operation records and logs are
trimmed automatically to the newest 200 (`CHROOTABLE_OPERATION_KEEP`).

`--import NAME ARCHIVE.tar.gz [SIZE_MIB] [persistent|ephemeral] [PROFILE]` turns an
export back into a workspace. The archive is checked before anything is written:
path traversal, block devices and non-standard character devices are refused, and it
must contain a root filesystem. Leave `SIZE_MIB` empty (`""`) to fit capacity to the
contents with room to work. Import preserves file capabilities and reads the release
and architecture from the archive; profile and lifecycle are not stored in exports.
Only import archives you trust: their contents run as root in later sessions.

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
operations/   durable operation stages and interrupted-publication records
activity.tsv  completed operation history
```

Use a different store with `./chrootables-desktop --store /srv/chrootables`, or set
`CHROOTABLE_HOME` for the CLI. The store must be owned by root for mutations and
must not be writable by other users. Images are fixed-capacity ext4 files backed by **sparse host files**. Creation,
expansion and copying check available host space conservatively, but do not
reserve the entire image capacity; monitor host free space as workspaces grow.
Clones and snapshots use reflinks when the host filesystem supports them.
Storage totals count allocated file blocks; shared CoW extents can appear in
multiple totals, so they do not predict exact bytes reclaimed. Export/log totals
refresh on directory changes and within 30 seconds of existing-file growth.

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
only `null`, `zero`, `full`, `random`, `urandom`, `tty`, a fresh `devpts` instance
and a private `/dev/shm`; no host device nodes are exposed, at the cost of tools
that expect more devices. `/proc` and `/run` are mounted as in any other session.

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

The CLI is the capability authority: `chrootable-manager --capabilities --json`
exposes profiles, releases, size limits and session options. The desktop reads it
once at startup, and parity tests protect both interfaces from drifting.

The check command runs syntax checks, the full unit suite, performance gates,
both demos, and a
Debian package build when `dpkg-deb` is available. Build artifacts are temporary.
Use `--build-deb` to require packaging tools and
`--require-desktop-integration` to require PyGObject and `desktop-file-validate`.
On Debian, the latter dependencies are provided by `python3-gi`,
`gir1.2-glib-2.0`, and `desktop-file-utils`; use the distribution Python or a
virtual environment with access to system site packages. Missing optional
integrations are reported as skipped tests. CI requires desktop integration on
both distributions and the package build on Debian. CI also builds RPM and
AppImage artifacts, checks fresh native-package installation/upgrade/removal,
and verifies that package removal preserves workspace data. Release branches and
manual workflow runs execute the privileged integration suite for both minimal
and Python profiles. `scripts/check-installed.py --prefix /path/to/extracted/usr`
checks a packaged CLI, capabilities, diagnostics, helper and desktop demo without
creating a real store.

The rootless benchmark uses 24 workspaces, 48 snapshots, 80 history entries and
real lock files. It gates median fresh inventory at 80 ms, cached inventory at
10 ms and unchanged synchronous rendering at 2 ms, and also reports p95 latency.
Fresh application caches are tested with warmed OS caches; queued Qt painting
is outside the render timing. Run `python3 scripts/benchmark.py --json` for the
measurements. Live lock acquisition/release must remain visible while caching.

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

## License

Chrootables is licensed under [Apache-2.0](LICENSE). Native packages and the
AppImage include the license text. Python, Qt and other host dependencies retain
their own licenses.
