# Dependency Security Audit

## Dependency Inventory

### 1. Manifests, Lockfiles, Package Sources, and Build/Install Scripts
Chrootables (`v3.0.0`) is a hybrid Python/Qt6 desktop workbench and Bash/Linux namespace image manager (`chrootable-manager`, `chrootables-desktop`, `chrootables/{__init__,app,backend,broker,client,style}.py`, and `scripts/install-desktop.py`) audited on `Linux 7.2.10-200.fc44.x86_64` (`Python 3.14.8`).

| Artifact / Layer | File Path / Location | Registry / Package Source | Version / Range (Baseline) | Lockfile & Provenance State |
| :--- | :--- | :--- | :--- | :--- |
| **Python Dependency Manifest** | `requirements.txt:1` | PyPI (`https://pypi.org/simple`) or Fedora RPM (`python3-pyside6`) | **Baseline**: `PySide6>=6.8,<7` (floating range)<br>**Remediated**: `PySide6-Essentials==6.11.2` + `shiboken6==6.11.2` with 10 SHA-256 wheel digests | Baseline lacked a lockfile or `--hash=sha256:` pins and resolved dynamically to `PySide6==6.12.0` (`2026-10-08`, 8 days old) while pulling in `PySide6-Addons` (`166.95 MiB`). Now pinned and hash-locked to `6.11.2` (`2026-08-18`, 59 days vetted). |
| **Host RPM / APT Runtime Packages** | `chrootable-manager:13-15, 144-192` (`REQUIRED_COMMANDS`, `package_for_command`, `check_prerequisites`) | Fedora 44 `dnf` (`RSA/SHA256`, Key ID `dbfcf71c6d9f90a6`) / Debian `apt-get` (`debian-archive-keyring.gpg`) | System packages (`bash-5.3.9`, `coreutils-9.10`, `util-linux-2.41.5`, `e2fsprogs-1.47.3`, `debootstrap-1.0.140`, `debian-keyring-2025.1`, `gnupg2-2.4.9`, `iproute-6.17.0`, `tar-1.35`, `gzip-1.14`, `gawk-5.3.2`, `newt-0.52.25`, `polkit-127`, `python3-pyside6-6.11.2`) | Verified by distribution package manager GPG signatures (`rpm -qi` / `apt-get`), but host auto-installer (`check_prerequisites`) previously omitted `--setopt=install_weak_deps=False` / `--no-install-recommends` and did not check `/var/log/chrootable-manager-dependencies.log` for symlinks. |
| **Guest Rootfs Base & Profile Packages** | `chrootable-manager:9-10, 257-265, 588-604`, `chrootables/backend.py:18-23, 55-59` | Debian Archive (`https://deb.debian.org/debian`, suites `trixie` [Debian 13], `bookworm` [Debian 12], or CLI `stable`) | Rolling Debian suite index verified via `--force-check-gpg --keyring=/usr/share/keyrings/debian-archive-keyring.gpg` (`minbase` + `minimal`, `build`, `python`, `network` profiles) | Cryptographically verified against `debian-archive-keyring.gpg` (Ed25519 + RSA-4096 keys for Bullseye/Bookworm/Trixie), but `CHROOTABLE_DEBIAN_MIRROR` and `CHROOTABLE_DEBIAN_SUITE` lacked validation against HTTP downgrade or `debootstrap` flag injection, and `debootstrap` only writes `<suite> main` (omitting `<suite>-security`) to guest `/etc/apt/sources.list`. |
| **Desktop Launcher Installer** | `scripts/install-desktop.py:1-34` | Local checkout (stdlib `os`, `pathlib`, `shutil`, `sys`) | In-tree script (`3.0.0`) | Writes per-user `.desktop` entry (`~/.local/share/applications/io.github.chrootables.Desktop.desktop`) and SVG icon (`assets/io.github.chrootables.Desktop.svg`); zero network or third-party dependencies. |
| **Container Bases / Bundled Artifacts** | `assets/demo.json`, `assets/io.github.chrootables.Desktop.svg` | In-tree static assets | Static JSON & SVG (`0` external URLs, `0` scripts) | No `Dockerfile`, OCI container base, Node/npm bundle, or precompiled binary blobs in repository. |

### 2. Direct and Transitive Dependency Graph, Provenance, and Licenses

| Dependency | Scope & Role | Direct vs. Transitive | Installed / Locked Version | License | Provenance & Digest / Signature Evidence |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **`PySide6-Essentials`** (replaces `PySide6` meta-package in `requirements.txt`) | Unprivileged GUI (`QtCore`, `QtGui`, `QtWidgets`) & unit tests (`QtTest`) | **Direct** (Python `venv` / Host RPM `python3-pyside6`) | `6.11.2` (`1.fc44` RPM / PyPI wheel `cp310-abi3-manylinux_2_34_x86_64`) | `LGPL-3.0-only OR GPL-3.0-only WITH Qt-GPL-exception-1.0` | PyPI wheel `sha256:aaf9f25f0f324874085fa5b26a610318db8a8e243cf85bb3e5400595191c7778` (`76.40 MiB`); Fedora RPM signed `RSA/SHA256` Key `dbfcf71c6d9f90a6` (`2026-08-25`). |
| **`shiboken6`** | C++/Python binding runtime required by `PySide6-Essentials` | **Transitive** (pinned directly in `requirements.txt`) | `6.11.2` | `LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only` | PyPI wheel `sha256:7a7a0a72a9ed26c9bf77d42246b1c736486befb8f31aa2fb29957ea4cdd1c1c2` (`0.26 MiB`); Fedora RPM `python3.14dist(shiboken6) = 6.11.2`. |
| **`PySide6-Addons`** *(Removed from `requirements.txt`)* | Bundled `QtWebEngine` (Chromium), `QtMultimedia`, `Qt3D`, `QtPdf`, `QtHttpServer`, `QtBluetooth`, `QtWebSockets` | **Transitive via `PySide6` meta-package** (Unnecessary) | `6.11.2` (Removed in `requirements.txt`) | `LGPL-3.0-only OR GPL-3.0-only` (plus Chromium third-party licenses) | `166.95 MiB` wheel (`sha256:a8b00956925fbdeffc7052cf933506391289f35ac4f3f71a0a167ec195cd47b4`); **0% reachable** in Chrootables. |
| **`debootstrap`** | Guest Debian rootfs bootstrap (`worker_main create`) | **Direct Host CLI** | `1.0.140-6.fc44.noarch` | `MIT` | Fedora RPM signed `RSA/SHA256` Key `dbfcf71c6d9f90a6`. Transitively requires `wget`, `xz`, `tar`, `gzip`, `dpkg`, `sha256sum`, `gpgv`, and `perl-interpreter` (`pkgdetails`). |
| **`debian-keyring`** / **`debian-archive-keyring`** | Trusted OpenPGP root of trust for Debian `InRelease` verification | **Direct Host Data** | `2025.1-4.fc44.noarch` | `LicenseRef-Fedora-Public-Domain` | Contains active Ed25519 & RSA-4096 keys for Debian 12 Bookworm (`4D64FEC1...`, `B8B80B5B...`, `05AB9034...`) and Debian 13 Trixie (`41587F7D...`, `04B54C3C...`, `5E04A1E3...`). |
| **`polkit` (`pkexec`) & `sudo`** | Privilege elevation for `chrootables/broker.py --writable` and terminal sessions | **Direct Host OS** | `polkit-127-2.fc44.2.x86_64` | `LGPL-2.0-or-later` | Host RPM (`RSA/SHA256` Key `dbfcf71c6d9f90a6`). |
| **`util-linux` (`unshare`, `mount`, `umount`, `mountpoint`, `losetup`, `flock`)** | Namespace isolation, loop device attachment, bind mounts, and image locks | **Direct Host OS** | `2.41.5-1.fc44.x86_64` | `GPL-2.0-or-later AND LGPL-2.1-or-later` | Host RPM (`RSA/SHA256` Key `dbfcf71c6d9f90a6`). |
| **`e2fsprogs` (`mkfs.ext4`, `e2fsck`, `resize2fs`)** | Workspace `.img` filesystem formatting, pre-resize check, and repair | **Direct Host OS** | `1.47.3-4.fc44.x86_64` | `GPL-2.0-or-later` | Host RPM (`RSA/SHA256` Key `dbfcf71c6d9f90a6`). |
| **`coreutils`, `tar`, `gzip`, `gnupg2` (`gpgv`), `iproute`, `gawk`, `newt` (`whiptail`)** | Image allocation, archive export, signature checking, loopback bring-up, and TUI | **Direct Host OS** | `coreutils-9.10`, `tar-1.35`, `gzip-1.14`, `gnupg2-2.4.9`, `iproute-6.17.0`, `gawk-5.3.2`, `newt-0.52.25` | `GPL-3.0-or-later` / `GPL-2.0-or-later` / `LGPL-2.0-or-later` | Host RPMs (`RSA/SHA256` Key `dbfcf71c6d9f90a6`); `0` open security advisories in `dnf updateinfo list --installed`. |

---

## Reachability and Exposure

### 1. Privilege Boundary vs. Dependency Reachability Architecture
Chrootables operates across **three distinct privilege and execution planes**. Correlating dependency imports and external binary invocations against these planes shows where third-party code is actually reachable:

1. **Plane 1 — Unprivileged Desktop UI (`chrootables-desktop`, `chrootables/app.py`, `chrootables/client.py`, `chrootables/style.py`)**:
   - **Execution Context**: Runs as the unprivileged desktop user (`UID != 0`).
   - **Reachable Third-Party Code**:
     - `PySide6.QtCore` (`QObject`, `QProcess`, `Signal`, `Qt`, `QEvent`, `QSettings`, `QSize`, `QTimer`)
     - `PySide6.QtGui` (`QColor`, `QFont`, `QIcon`, `QKeySequence`, `QPainter`, `QPen`, `QPixmap`, `QShortcut`, `QTextCursor`)
     - `PySide6.QtWidgets` (`QApplication`, `QMainWindow`, `QWidget`, `QFrame`, `QDialog`, `QFileDialog`, `QListWidget`, `QTableWidget`, `QPlainTextEdit`, etc.)
   - **Unreachable Code Surface**:
     - `QtNetwork`, `QtWebEngineCore`, `QtWebEngineWidgets` (Chromium), `QtMultimedia`, `QtPdf`, `QtSvg` (note: window/row icons are procedurally drawn in `glyph()` via `QPainter` on `QPixmap`; `assets/io.github.chrootables.Desktop.svg` is only copied to `~/.local/share/icons/` by `scripts/install-desktop.py` for the desktop shell), `QtSql`, `QtQml`, `QtQuick`, `Qt3D`, `QtHttpServer`, `QtWebSockets`, `QtRemoteObjects`, `QtBluetooth`.
     - **Key Exposure Finding**: Declaring the `PySide6` meta-package in `requirements.txt` installed `PySide6-Addons` (`166.95 MiB` wheel), introducing Chromium/QtWebEngine and multimedia codecs into `.venv` even though **0% of `PySide6-Addons` is ever imported**. Furthermore, `Window.build_ui()` and `label()` (`chrootables/app.py:28-34`) explicitly set `widget.setTextFormat(Qt.TextFormat.PlainText)` and use `QPlainTextEdit` for logs, preventing rich-text/HTML/SVG parser reachability even when rendering untrusted guest log output.

2. **Plane 2 — Privileged Root Stdio Broker & Backend (`chrootables/broker.py`, `chrootables/backend.py`)**:
   - **Execution Context**: Spawned via `pkexec` as `root` (`UID 0`) when the store is unlocked (`Client.start(writable=True)` in `chrootables/client.py:35-36`), communicating exclusively over an anonymous stdin/stdout pipe (no network or Unix domain socket).
   - **Reachable Code**:
     - Imports **only Python standard library modules** (`argparse`, `concurrent.futures`, `fcntl`, `json`, `os`, `pathlib`, `pwd`, `re`, `shutil`, `stat`, `subprocess`, `sys`, `threading`, `time`).
     - **Zero `PySide6` or third-party Python packages are imported in Plane 2.**
   - **Baseline Exposure Flaw (Fixed)**:
     - Previously, `Client.start()` (`chrootables/client.py:34-36`) invoked `pkexec [sys.executable, broker.py, --store, ..., --writable]` **without `-I -S`**.
     - When a user ran `./chrootables-desktop` from an activated virtual environment (`.venv/bin/python3`, as instructed in `README.md:14-16`), `sys.executable` pointed to the user-writable `.venv`. Even though `broker.py` never imports third-party packages, Python's default startup automatically imports `site`, which processes all `.pth` files and `sitecustomize.py` inside `.venv/lib/python3.x/site-packages/` (and user site-packages `~/.local/lib/...` when `-I` is omitted) **with `root` privileges (`UID 0`)**!
     - Any compromised or malicious package installed in the unprivileged user's `.venv` (or `.pth` persistence in `~/.local`) gained immediate silent `root` code execution upon clicking **Unlock workspace store**.
     - Passing `"-I", "-S"` (`chrootables/client.py:34` and `chrootables/backend.py:139`) disables both `site` initialization and user/env paths, restricting `sys.path` in the privileged helper strictly to the system standard library and `ROOT`.

3. **Plane 3 — Privileged Bash Image Manager & Guest Bootstrap (`chrootable-manager`)**:
   - **Execution Context**: Runs as `root` (`UID 0`) with `PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin` and `umask 077` (`chrootable-manager:1245-1247`), executing filesystem/namespace workers inside `unshare --mount --uts --ipc --pid --fork --kill-child` (`chrootable-manager:250, 472-474`).
   - **Reachable External Inputs & Supply-Chain Touchpoints**:
     - `debootstrap` downloads `InRelease`, `Packages.xz`, and `.deb` archives from `$DEBIAN_MIRROR` (`CHROOTABLE_DEBIAN_MIRROR`) for `$DEBIAN_SUITE` (`CHROOTABLE_DEBIAN_SUITE`) using `wget`, verifies signatures via `gpgv` against `/usr/share/keyrings/debian-archive-keyring.gpg`, verifies package digests via `sha256sum` (unless `sha256sum` is missing or `SHA_SIZE` is overridden, where `/usr/sbin/debootstrap:610-613` silently falls back to `SHA-1`), and extracts `.deb` payloads onto the loop-mounted ext4 image.
     - `worker_main export` mounts the image `loop,ro,noload` and runs `tar --numeric-owner --acls --xattrs --one-file-system -C "$WORKER_ROOT" -czf "$1" .` (`chrootable-manager:606-607`).
     - `deliver_export()` (`chrootables/backend.py:109-145`) opens the `.tar.gz` archive as `root` (`O_RDONLY | O_NOFOLLOW`) and spawns `/usr/bin/python3 -I -S -c ...` after dropping privileges (`user=uid, group=gid, extra_groups=[]`) to create the destination file with `O_CREAT | O_EXCL | O_NOFOLLOW`.

### 2. Build-Time vs. Runtime Exposure & Install Scripts
- **Python Install Time (`pip install -r requirements.txt`)**:
  - `PySide6-Essentials` and `shiboken6` distribute prebuilt `abi3` binary wheels (`manylinux_2_34_x86_64`, `manylinux_2_39_aarch64`). When installed with `--require-hashes --only-binary :all:`, `pip` verifies SHA-256 digests before extraction and **never executes `setup.py` or arbitrary build-backend code**.
- **Host Setup Time (`chrootable-manager --setup` / `check_prerequisites`)**:
  - Invokes `dnf install -y` or `apt-get install -y` as `root`. Distribution packages execute maintainer scripts (`%pre`/`%post` in RPM, `preinst`/`postinst` in DEB) as `root`.
  - Without `--setopt=install_weak_deps=False` (`dnf`) and `--no-install-recommends` (`apt-get`), installing `debootstrap` or `gnupg2` pulls in weak/recommended auxiliary packages and runs their maintainer scripts unnecessarily.
- **Guest Provisioning Time (`chrootable-manager --create`)**:
  - `debootstrap` second stage and `chroot "$WORKER_ROOT" apt-get install -y --no-install-recommends` execute Debian package maintainer scripts inside the guest `chroot` (`unshare --mount --uts --ipc --pid --fork --kill-child`).

---

## Ranked Findings

Findings are ranked by **exploitability, privilege impact, and reachability** rather than raw advisory count:

### Finding 1 (High — Privilege Boundary / Supply-Chain Execution): Privileged `pkexec` Broker Evaluates Third-Party `site-packages` and `.pth` Hooks as `root`
- **Location**: `chrootables/client.py:34-39`, `chrootables/backend.py:139`
- **Verified Evidence**:
  - `Client.start(writable=True)` launched `pkexec [sys.executable, str(ROOT / "chrootables" / "broker.py"), "--store", self.store, "--writable"]` without `-I` (isolated mode) or `-S` (disable `import site`).
  - While `broker.py` and `backend.py` require **zero third-party packages**, launching `./chrootables-desktop` from a user-writable virtual environment (`.venv`, documented in `README.md:14-16`) or with user site-packages (`~/.local/lib/python3.x/site-packages`) caused the `root` Python process to import `site` at startup and execute any `.pth` executable lines or `sitecustomize.py` installed in `.venv` or `~/.local`.
- **Exploitability & Prerequisites**:
  - Reachable whenever a developer uses the documented `venv` workflow or has a compromised/typosquatted PyPI package in their user environment and clicks **Unlock workspace store**.
- **Status**: **Remediated & Verified** — Added `"-I", "-S"` to `Client.start()` (`chrootables/client.py:34`) and `deliver_export()` (`chrootables/backend.py:139`), verified by `test_client_starts_broker_in_isolated_no_site_mode`.

### Finding 2 (High — Transport Integrity & Option Injection): Unvalidated `CHROOTABLE_DEBIAN_MIRROR` and `CHROOTABLE_DEBIAN_SUITE` Allow HTTP Downgrade, `debootstrap` Flag Injection, and Metadata Injection
- **Location**: `chrootable-manager:9-10, 32-34, 64-72, 297-300, 599-603, 1178`
- **Verified Evidence**:
  - `DEBIAN_MIRROR` (`CHROOTABLE_DEBIAN_MIRROR`) and `DEBIAN_SUITE` (`CHROOTABLE_DEBIAN_SUITE`) were passed directly to `debootstrap` (`chrootable-manager:591`) without validation, while `usage()` (`chrootable-manager:1167`) explicitly documented `CHROOTABLE_DEBIAN_MIRROR=http://deb.debian.org/debian` (plaintext HTTP), contradicting `README.md:117`.
  - Inspection of `/usr/sbin/debootstrap:154-455` confirmed that `debootstrap` parses any argument starting with `-` in its option loop before positional arguments. Setting `CHROOTABLE_DEBIAN_SUITE="--no-check-certificate"` or `"--unpack-tarball=..."` injects options directly into `debootstrap`.
  - Furthermore, `write_metadata()` (`chrootable-manager:64-72`) did not reject `\n` or `\r` in positional parameters (unlike `metadata_set()` on line 75), allowing newline-crafted `CHROOTABLE_DEBIAN_SUITE` values to inject arbitrary key-value lines into `<image>.img.meta`.
- **Exploitability & Prerequisites**:
  - Reachable via CLI environment variables (`CHROOTABLE_DEBIAN_MIRROR`, `CHROOTABLE_DEBIAN_SUITE`). Note that the desktop GUI (`chrootables/backend.py:55-59`) already restricted `suite` to `("trixie", "bookworm")` and did not expose mirror overrides.
  - Plaintext `http://` mirrors expose `debootstrap` and subsequent guest `apt-get` runs to MITM replay/freeze attacks (`debootstrap` does not enforce `InRelease` `Valid-Until` timestamps) and traffic tampering.
- **Status**: **Remediated & Verified** — Added `valid_suite()` (`^[a-z0-9][a-z0-9.-]{0,31}$`), `valid_mirror()` (`^https://[A-Za-z0-9._-]+(:[0-9]+)?(/[A-Za-z0-9._/-]*)?$`), newline/CR guards in `write_metadata()`, enforced validation in `create_image()` and `worker_main create`, updated `usage()` to `https://deb.debian.org/debian`, and verified with `test_suite_and_mirror_supply_chain_validation`.

### Finding 3 (Medium — Reproducibility, Over-Scoping & Attack Surface): Unpinned Floating Range (`PySide6>=6.8,<7`) Without Hashes Pulls Unnecessary `166.95 MiB` `PySide6-Addons` (Chromium / `QtWebEngine`) and Unvetted Releases
- **Location**: `requirements.txt:1`
- **Verified Evidence**:
  - `requirements.txt` specified `PySide6>=6.8,<7` with no lockfile or `--hash` digests.
  - Querying PyPI confirmed `PySide6==6.12.0` was published on `2026-10-08T09:11:15Z` (only 8 days old at audit time, violating the >=7-day release vetting threshold relative to the stability of `6.11.2` published `2026-08-18` and matching Fedora 44's `python3-pyside6-6.11.2-1.fc44`).
  - On PyPI, `PySide6` is a meta-package (`requires_dist: ['shiboken6==6.11.2', 'PySide6_Essentials==6.11.2', 'PySide6_Addons==6.11.2']`). Installing `PySide6` downloads `PySide6-Addons` (`166.95 MiB` wheel on `x86_64`), which bundles `libQt6WebEngineCore` (Chromium), `QtMultimedia`, `QtHttpServer`, `QtPdf`, `QtRemoteObjects`, and `QtBluetooth`.
  - Codebase-wide AST/import analysis verified Chrootables only imports `QtCore`, `QtGui`, `QtWidgets`, and `QtTest`—all 100% contained in `PySide6-Essentials` (`76.40 MiB` wheel).
- **Status**: **Remediated & Verified** — Replaced `PySide6>=6.8,<7` in `requirements.txt` with `PySide6-Essentials==6.11.2` and `shiboken6==6.11.2` pinned with all 10 official platform wheel `--hash=sha256:` digests, removing `PySide6-Addons` (`-68.5%` wheel download size, `0` Chromium/WebEngine binaries) and enabling `pip install --require-hashes --only-binary :all: -r requirements.txt`. Verified by `test_requirements_hash_locked_and_minimal_scope`.

### Finding 4 (Medium — Cryptographic Downgrade & Transitive Prerequisite Gap): `debootstrap` Silent Fallback to `SHA-1` When `sha256sum` Is Missing or `SHA_SIZE` Is Polluted, Plus Unchecked `wget`/`xz` Prerequisites
- **Location**: `chrootable-manager:13-15, 144-162, 603` and `/usr/sbin/debootstrap:499-500, 607-614`
- **Verified Evidence**:
  - Inspection of `/usr/sbin/debootstrap:607-614` revealed:
    ```sh
    if [ -z "${SHA_SIZE-}" ]; then SHA_SIZE=256; fi
    if ! in_path "sha${SHA_SIZE}sum" && ! in_path "sha${SHA_SIZE}"; then
        warning SHA_SIZE "Cannot find binary for checking sha%s checksums, falling back to sha1" "${SHA_SIZE}"
        SHA_SIZE=1
    fi
    ```
  - If `SHA_SIZE=1` was present in the environment or `sha256sum` was missing, `debootstrap` silently downgraded `.deb` package checksum verification from `SHA256` to collision-vulnerable `SHA1`.
  - Furthermore, `REQUIRED_COMMANDS` (`chrootable-manager:13-15`) did not check for `sha256sum`, `wget`, or `xz`, even though `debootstrap` hard-depends on `wget` (`NEEDWGET`) and `xz` to download and unpack Debian archives.
- **Status**: **Remediated & Verified** — Added `sha256sum`, `wget`, and `xz` to `REQUIRED_COMMANDS` and `package_for_command()` (`xz` on `dnf`, `xz-utils` on `apt-get`), and explicitly enforced `SHA_SIZE=256` on the `debootstrap` invocation in `worker_main create` (`chrootable-manager:603`).

### Finding 5 (Low/Medium — Host Install Script Surface & Log Symlink Check): Host Auto-Installer (`check_prerequisites`) Pulls Weak/Recommended Dependencies and Appends to Unchecked `/var/log/chrootable-manager-dependencies.log`
- **Location**: `chrootable-manager:177-198`
- **Verified Evidence**:
  - `check_prerequisites()` executed `dnf install -y "${packages[@]}"` without `--setopt=install_weak_deps=False` and `apt-get install -y "${packages[@]}"` without `--no-install-recommends`, expanding the set of packages and root `%post`/`.postinst` maintainer scripts executed on the host during `--setup` or CLI auto-install.
  - Output was redirected (`>>`) as `root` to `/var/log/chrootable-manager-dependencies.log` without verifying `[[ ! -L /var/log/chrootable-manager-dependencies.log ]]`.
- **Status**: **Remediated & Verified** — Added symlink refusal (`[[ ! -L $dep_log ]]`), `--setopt=install_weak_deps=False` for `dnf`, and `--no-install-recommends` for `apt-get` in `check_prerequisites()`.

### Finding 6 (Low — Guest Image Security Update Channel & Floating `stable` Alias): `debootstrap` Configures Only `<suite> main` in Guest `/etc/apt/sources.list` Without `<suite>-security`
- **Location**: `chrootable-manager:9, 599-610` (`/usr/share/debootstrap/functions:1198-1213`)
- **Verified Evidence**:
  - `debootstrap`'s `setup_apt_sources()` writes only `deb https://deb.debian.org/debian <suite> main` to `$WORKER_ROOT/etc/apt/sources.list`.
  - Guest images created for `bookworm` or `trixie` do not have `deb https://deb.debian.org/debian-security <suite>-security main` configured by default, meaning packages installed during `profile` provisioning (`build-essential`, `git`, `curl`, `python3`, `openssh-client`, `dnsutils`) or later manual `apt-get upgrade` runs inside the guest only see point-release packages unless `<suite>-security` is added.
  - Additionally, the CLI default `CHROOTABLE_DEBIAN_SUITE=${CHROOTABLE_DEBIAN_SUITE:-stable}` (`chrootable-manager:9`) uses the floating `stable` alias (whereas the desktop GUI explicitly passes `trixie` or `bookworm`).
- **Compensating Controls & Operational Trade-off**:
  - Adding `<suite>-security` during `create` requires either a second network mirror metadata fetch (`dists/<suite>-security/InRelease`, `+2–4 s` creation latency and fails if the user points `CHROOTABLE_DEBIAN_MIRROR` to a local/intranet mirror that only mirrors `debian/` and not `debian-security/`). Documented in the Remediation Matrix below with a safe opt-in/default-mirror check.

---

## Remediation Matrix

| Rank & Finding | Target File(s) | Action Taken / Recommended | Compatibility & Runtime Impact | Rollback Procedure |
| :--- | :--- | :--- | :--- | :--- |
| **1. Isolate Privileged `broker.py` & `deliver_export` Python Interpreter (`-I -S`)** | `chrootables/client.py:34`<br>`chrootables/backend.py:139` | **Implemented**: Pass `"-I", "-S"` when spawning `broker.py` (both read-only and `pkexec --writable`) and in `deliver_export()`. | **100% compatible**. `broker.py` and `backend.py` use only stdlib and explicitly insert `ROOT` into `sys.path[0]`. Eliminates `site-packages` / `.pth` load overhead on helper startup. | Remove `"-I", "-S"` from `args` in `chrootables/client.py:34` and `"-S"` in `chrootables/backend.py:139`. |
| **2. Validate `DEBIAN_SUITE` & `DEBIAN_MIRROR` and Guard `.meta` Newlines** | `chrootable-manager:32-34, 66-70, 299-300, 601, 1179` | **Implemented**: Added `valid_suite` (`^[a-z0-9][a-z0-9.-]{0,31}$`), `valid_mirror` (`^https://...`), and newline/CR rejection in `write_metadata()`. Updated `--help` mirror example to `https://`. | **100% compatible** with all valid Debian suites (`trixie`, `bookworm`, `stable`, `sid`, etc.) and HTTPS mirrors. Blocks plaintext `http://` mirrors and `--option` injection into `debootstrap`. | Revert `valid_suite`/`valid_mirror` checks in `chrootable-manager` if an air-gapped plaintext HTTP mirror is temporarily required (prefer HTTPS local proxy instead). |
| **3. Replace Floating `PySide6>=6.8,<7` with Hash-Locked `PySide6-Essentials==6.11.2` + `shiboken6==6.11.2`** | `requirements.txt:1-12` | **Implemented**: Pinned `PySide6-Essentials==6.11.2` and `shiboken6==6.11.2` with SHA-256 hashes for all 5 wheel architectures (`manylinux_2_34_x86_64`, `manylinux_2_39_aarch64`, `macosx_13_0_universal2`, `win_amd64`, `win_arm64`). | **100% compatible** with all Chrootables imports (`QtCore`, `QtGui`, `QtWidgets`, `QtTest`). Cuts `venv` download size from `244.16 MiB` to `76.66 MiB` (`-68.6%`) and removes `QtWebEngine` (Chromium). System RPM installs (`python3-pyside6`) are unaffected. | Restore `PySide6==6.11.2` (with hashes) if an addon module is ever introduced in the future. |
| **4. Enforce `SHA_SIZE=256` in `debootstrap` & Verify `sha256sum`, `wget`, `xz` in `REQUIRED_COMMANDS`** | `chrootable-manager:13-15, 156-162, 603` | **Implemented**: Added `sha256sum`, `wget`, `xz` to `REQUIRED_COMMANDS` / `package_for_command()` and prefixed `debootstrap` with `SHA_SIZE=256`. | **100% compatible**. Prevents silent `debootstrap` fallback to `SHA-1` and surfaces missing `wget`/`xz` in `--doctor` before image creation fails. | Remove `SHA_SIZE=256` prefix and extra commands from `REQUIRED_COMMANDS`. |
| **5. Harden Host Dependency Auto-Installer (`check_prerequisites`)** | `chrootable-manager:183-198` | **Implemented**: Added `[[ ! -L $dep_log ]]` check on `/var/log/chrootable-manager-dependencies.log`, `--setopt=install_weak_deps=False` (`dnf`), and `--no-install-recommends` (`apt-get`). | **100% compatible**. Installs only required host packages without weak/recommended bloat. | Remove `--setopt=install_weak_deps=False` / `--no-install-recommends` if a distribution splits a required sub-binary into a weak dependency. |
| **6. Guest Debian Security Archive (`<suite>-security`) & Keyring Freshness Check** | `chrootable-manager` (`worker_main create`, `doctor`) | **Planned / Follow-Up**: When `DEBIAN_MIRROR` is `https://deb.debian.org/debian` and `DEBIAN_SUITE` is `bookworm` or `trixie`, append `deb https://deb.debian.org/debian-security ${DEBIAN_SUITE}-security main` to guest `/etc/apt/sources.list` and run `apt-get update && apt-get upgrade -y` during `create`; warn in `doctor()` if `debian-archive-keyring.gpg` keys are within 90 days of expiry. | Adds `~2–5 s` network fetch during `--create` when online; ensures newly provisioned workspaces include all current Debian Security Advisories (DSAs). | Guard behind `CHROOTABLE_SECURITY_UPDATES=${CHROOTABLE_SECURITY_UPDATES:-1}` so offline/custom mirror users can set `CHROOTABLE_SECURITY_UPDATES=0`. |

---

## Verification and Rollback

### 1. Verification Commands Executed
All implemented supply-chain remediations were verified against the project's static checks, unit/integration test suite (`55` tests), and host readiness probe:

```bash
# 1. Verify Bash syntax and host prerequisite checks (including sha256sum, wget, xz)
bash -n chrootable-manager
./chrootable-manager --doctor

# 2. Run the full unit and security regression suite (55 tests, including 3 new supply-chain tests)
python3 -m unittest discover -s tests -p 'test_*.py' -v

# 3. Verify isolated (-I -S) broker startup and offscreen desktop rendering
QT_QPA_PLATFORM=offscreen ./chrootables-desktop --demo --screenshot /tmp/chrootables-audit.png
```

### 2. Verified Test Results
- `test_requirements_hash_locked_and_minimal_scope` (`tests/test_backend.py`): Confirms `requirements.txt` pins `PySide6-Essentials==6.11.2` and `shiboken6==6.11.2` with 10 `--hash=sha256:` digests, contains no floating `>=` ranges, and excludes `PySide6-Addons`.
- `test_suite_and_mirror_supply_chain_validation` (`tests/test_backend.py`): Confirms `valid_suite`, `valid_mirror`, and `write_metadata` reject `--no-check-gpg` option injection, `http://` downgrade URLs, `file://` URLs, and embedded newlines while accepting valid suites (`trixie`, `bookworm`, `stable`) and `https://deb.debian.org/debian`.
- `test_client_starts_broker_in_isolated_no_site_mode` (`tests/test_desktop.py`): Confirms `Client.start()` passes `["-I", "-S"]` for both unprivileged and `pkexec --writable` invocations, and `test_actual_async_helper_and_empty_store` confirms live end-to-end IPC with the isolated helper.

### 3. Dependency Upgrade & Rollback Runbook
When bumping `PySide6-Essentials` and `shiboken6` in `requirements.txt`:
1. **Minimum Release Age Gate**: Confirm the target version on PyPI has been published for **at least 7 days** (and preferably >=14 days) with no yanked wheels or open regressions:
   ```bash
   python3 -m pip index versions PySide6-Essentials
   ```
2. **Fetch Official Wheel Digests**: Retrieve SHA-256 digests from PyPI's JSON API (`https://pypi.org/pypi/PySide6-Essentials/<version>/json` and `https://pypi.org/pypi/shiboken6/<version>/json`) and update `requirements.txt`.
3. **Test in an Ephemeral Virtual Environment**:
   ```bash
   python3 -m venv /tmp/chrootables-venv
   /tmp/chrootables-venv/bin/python -m pip install --require-hashes --only-binary :all: -r requirements.txt
   /tmp/chrootables-venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v
   QT_QPA_PLATFORM=offscreen /tmp/chrootables-venv/bin/python ./chrootables-desktop --demo --screenshot /tmp/chrootables.png
   rm -rf /tmp/chrootables-venv /tmp/chrootables.png
   ```
4. **Rollback**: `git checkout -- requirements.txt chrootables/client.py chrootables/backend.py chrootable-manager` and reinstall with `python3 -m pip install --require-hashes --only-binary :all: -r requirements.txt`.

---

## Ongoing Supply-Chain Controls

### 1. CI Gate Definitions
Enforce the following automated checks on every pull request and release tag:

1. **Hash-Locked Binary-Only Python Installation Gate**:
   ```bash
   python3 -m pip install --dry-run --require-hashes --only-binary :all: -r requirements.txt
   ```
   *Fails if any dependency in `requirements.txt` lacks `--hash=sha256:`, uses a floating range, or requires building a source distribution (`sdist` / `setup.py`).*
2. **Vulnerability & Advisory Audit Gate (`pip-audit` / `osv-scanner`)**:
   ```bash
   pip-audit -r requirements.txt --require-hashes --strict
   ```
   *Correlates exact wheel versions against PyPA/OSV advisories.*
3. **Static Import & Privilege Boundary Gate**:
   - Enforce via `tests/test_backend.py` and `tests/test_desktop.py` that:
     - `chrootables/broker.py` and `chrootables/backend.py` never import third-party modules (`PySide6` or `site-packages`).
     - `Client.start()` and `deliver_export()` always invoke Python with `"-I", "-S"`.
     - `chrootable-manager` enforces `valid_suite`, `valid_mirror` (`https://`), and `SHA_SIZE=256` on `debootstrap`.

### 2. Provenance & Keyring Lifecycle Controls
- **Debian Archive Keyring Verification**:
  - Host package `debian-keyring` (`2025.1-4.fc44`) / `debian-archive-keyring` provides `/usr/share/keyrings/debian-archive-keyring.gpg`.
  - Current keyring expiration dates verified via `gpg --show-keys --with-colons /usr/share/keyrings/debian-archive-keyring.gpg`:
    - **Debian 11 (Bullseye)**: Expires `2029-01-15` (`1863170316`) — note Bullseye LTS ended August 2026; Chrootables desktop already scopes creation to `trixie` (Debian 13) and `bookworm` (Debian 12).
    - **Debian 12 (Bookworm)**: Release & Archive keys expire `2031-01-18` / `2031-01-20` (`1926589461` / `1926780243`).
    - **Debian 13 (Trixie)**: Stable Release key (`41587F7DB8C774BCCF131416762F67A0B2C39DE4`) expires `2033-03-23` (`1995130581`); Archive Automatic Signing keys expire `2035-03-29` (`2058699029`).
- **Minimum Release Age Policy**:
  - Do not upgrade `PySide6-Essentials` / `shiboken6` to a release published fewer than **7 days** ago unless patching a confirmed, reachable security vulnerability in `QtCore`, `QtGui`, or `QtWidgets`.

### 3. Update Ownership and Exception Process
- **Update Cadence & Ownership**:
  - **Desktop / Python Maintainer**: Reviews `PySide6-Essentials` / `shiboken6` point releases monthly against Qt Security Advisories (`QtCore`, `QtGui`, `QtWidgets`) and updates `requirements.txt` hashes after the 7-day stabilization window.
  - **Runtime / OS Maintainer**: Reviews `debootstrap` and `debian-archive-keyring` updates across supported host distributions (Fedora `dnf` and Debian/Ubuntu `apt`) at each Debian point release.
- **Time-Bounded Exception Process**:
  - Any temporary exception (e.g., an unpatched low/medium transitive advisory in `QtCore`/`QtGui` with no upstream fix, or a local test mirror override) must be documented in `DEPENDENCY_SECURITY_AUDIT.md` with:
    1. **Advisory / Finding ID** and affected version.
    2. **Reachability justification** and active compensating control (e.g., `Qt.TextFormat.PlainText` enforcement, `-I -S` broker isolation, or `unshare --net`).
    3. **Approver** and a hard **expiry date (`<= 30 days`)** after which CI fails unless renewed or remediated.
