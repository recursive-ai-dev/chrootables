# Archived: 1.2.0 improvement list (superseded)

> Superseded by the audit in the root `TODO.md`, written 2026-10-30. The checkmarks below were
> written before that audit and several were not accurate: safe cancellation did not stop work,
> import did not reject device nodes, and 7 tests failed. Kept as a historical record only.

# Chrootables Improvement Roadmap & Engineering TODO

This document catalogues **18 prioritized engineering improvements** for Chrootables, ordered strictly from **most negatively affecting the application** (data/recovery blind spots, CLI/TUI/Desktop contract drift, missing archive restoration, unbounded storage growth, and uncancellable operations) to **barely a concern** (visual glyph completeness, demo dataset fidelity, and documentation polish).

Every item requires complete implementation across the relevant stack layers, strict adherence to zero-shell-interpolation security invariants, CLI/Desktop parity, and automated regression test coverage.

---

## Tier 1 — Critical Data Lifecycle, Recovery & Contract Integrity

- [x] **1. Staged Operation Inspection & Recovery in Desktop Workbench (`chrootables/backend.py`, `chrootables/broker.py`, `chrootables/client.py`, `chrootables/app.py`, `chrootable-manager`)**
  - **Impact**: High — When a workspace creation, clone, snapshot restore, resize, or repair is interrupted during its staged publication window (`publish-*`), the CLI supports `--operations --json` and `--recover-operation ID --yes`, and `Store.inventory()` already emits `"operations"` in the JSON payload. However, `chrootables/backend.py:normalize_inventory()` strips `"operations"`, leaving desktop users with no visibility into interrupted operations and no way to recover or roll forward/back staged state without dropping to a terminal.
  - **Solution**:
    - Preserve and normalize `"operations"` in `chrootables/backend.py:normalize_inventory()`, exposing operation ID, kind, target workspace, state, interruptibility, recoverability, timestamp, and detail.
    - Add `recover_operation(op_id)` to `StoreBackend`, `BrokerServer`, and `StoreClient`.
    - Surface interrupted/recoverable operations in the Desktop Workbench (`chrootables/app.py`) with a dedicated recovery banner/action and Maintenance view integration so users can safely recover interrupted publications with one click.

- [x] **2. Workspace Archive Import & Rehydration (`chrootable-manager`, `chrootables/backend.py`, `chrootables/broker.py`, `chrootables/client.py`, `chrootables/app.py`)**
  - **Impact**: High — Users can export workspaces to `.tar.gz` archives (`--export NAME [OUTPUT.tar.gz]`) and exports are preserved when workspaces are deleted, yet there is no `--import` capability in either the CLI or Desktop Workbench to rehydrate a `.tar.gz` export back into a live managed `.img` workspace.
  - **Solution**:
    - Implement `import_chrootable` (`--import NAME ARCHIVE.tar.gz [SIZE_MIB] [persistent|ephemeral] [PROFILE]`) in `chrootable-manager` with strict path validation, sparse ext4 image allocation, safe tar extraction into the mounted staging filesystem (`tar -xzpf` with `--numeric-owner` and traversal rejection), metadata creation/reconciliation (`origin: "import:<archive>"`), and atomic staged publication (`publish-import`).
    - Expose `import_image` across `StoreBackend`, `BrokerServer`, `StoreClient`, and the Desktop Workbench (`Import Archive...` dialog with file picker, workspace name validation, capacity, mode, and profile selection).
    - Register `"import"` in `CAPABILITIES["features"]`.

- [x] **3. Interactive TUI (`create_chrootable`) Profile & Capacity Contract Drift (`chrootable-manager`)**
  - **Impact**: High — The interactive TUI wizard in `chrootable-manager:create_chrootable()` only offers 4 profiles (`minimal`, `build`, `python`, `network`), completely omitting `rust`, `go`, and `web`. Furthermore, its prompt falsely claims `"The image is fully allocated when created"` (images are sparse via `truncate -s`) and `"Enter a whole number from 1 to 1048576 MiB"` (whereas `valid_size_mib` rejects sizes below `512` MiB), and it hardcodes `2048` or `3072` MiB defaults instead of consulting `recommended_size_mib()`.
  - **Solution**:
    - Update the TUI profile menu in `create_chrootable()` to offer all 7 supported profiles (`minimal`, `build`, `python`, `network`, `rust`, `go`, `web`) and default size recommendations via `recommended_size_mib(profile)`.
    - Correct the TUI capacity prompt to accurately describe sparse image allocation and enforce the `512` to `1048576` MiB bounds consistently.

- [x] **4. Safe Cancellation for Long-Running Operations (`chrootables/backend.py`, `chrootables/broker.py`, `chrootables/client.py`, `chrootables/app.py`)**
  - **Impact**: High — Long-running operations (`create`, `clone`, `import`, `export`, `repair`, `archive-sync`) run inside the privilege broker without a cancellation mechanism in the Desktop Workbench. Users who accidentally trigger a large workspace bootstrap or archive export cannot cancel it from the UI.
  - **Solution**:
    - Track the active operation child process in `StoreBackend` / `BrokerServer` and implement a thread-safe `cancel_task()` RPC method that checks operation stage safety (refusing or deferring during atomic `publish-*` cutovers while sending `SIGTERM` to interruptible bootstrap/copy/archive phases).
    - Add a `Cancel Operation` button to the Desktop Workbench task panel wired to `client.cancel_task()`.

- [x] **5. Export & Operation Log Retention / Pruning (`chrootable-manager`, `chrootables/backend.py`, `chrootables/broker.py`, `chrootables/client.py`, `chrootables/app.py`)**
  - **Impact**: Medium-High — `$STORE_DIR/exports` (`.tar.gz` archives) and `$STORE_DIR/logs` (`.log` files) accumulate indefinitely on disk with no CLI or Desktop mechanism to prune obsolete operation logs or remove old exported archives, leading to silent disk exhaustion.
  - **Solution**:
    - Add `--prune-logs [KEEP_COUNT]` and `--delete-export FILE_OR_NAME` to `chrootable-manager` (ensuring logs referenced by active/interrupted operations in `$STORE_DIR/operations` are never deleted, and export paths are strictly confined to `$STORE_DIR/exports`).
    - Expose `prune_logs` and `delete_export` through `StoreBackend`, `BrokerServer`, `StoreClient`, and the Desktop Workbench (Storage/Maintenance actions and Export list management).

---

## Tier 2 — Desktop / CLI Parity & Validation Safeguards

- [x] **6. Desktop Offline Suite Archive Sync (`chrootables/backend.py`, `chrootables/broker.py`, `chrootables/client.py`, `chrootables/app.py`)**
  - **Impact**: Medium-High — Offline base suite tarballs (`$STORE_DIR/archives/<suite>.tar.gz`) allow instant workspace creation without network rounds, and `Store.inventory()` already reports `"archives"` (`suite`, `path`, `size_bytes`, `updated`). However, `normalize_inventory()` drops `"archives"` and `--archive-sync` is only accessible from the CLI.
  - **Solution**:
    - Preserve and normalize `"archives"` in `chrootables/backend.py:normalize_inventory()`.
    - Add `archive_sync(suite="")` to `StoreBackend`, `BrokerServer`, and `StoreClient`.
    - Add an `Offline Cache Sync` action in the Desktop Workbench Maintenance/System panel displaying cached suite status and size.

- [x] **7. Sub-GiB Capacity Granularity in Desktop Create & Import Dialogs (`chrootables/app.py`)**
  - **Impact**: Medium — `CAPABILITIES["limits"]["min_size_mib"]` is `512` MiB, and CLI `--create` / Desktop `Resize` both support MiB granularity down to `512` MiB. However, `CreateDialog` uses a `QSpinBox` restricted to whole GiB (`1` to `1024 GiB`), making it impossible to create a `512 MiB` minimal workspace or fine-tune MiB capacity from the desktop UI.
  - **Solution**:
    - Upgrade `CreateDialog` (and the new `ImportDialog`) capacity selector to operate in `MiB` (`512` to `1048576` MiB, with convenient `512 MiB` step increments and human-readable GiB equivalence hint) while preserving one-click profile preset buttons (`2 GiB`, `3 GiB`, `4 GiB`, `6 GiB`, plus `512 MiB`).

- [x] **8. Inline Target Name Collision Validation in Clone Dialog (`chrootables/app.py`)**
  - **Impact**: Medium — `CreateDialog` validates inline that a workspace name does not already exist in `self.existing_names`, disabling the submit button and explaining the collision immediately. `Window.clone()` uses `ValueDialog` without passing existing workspace names, allowing collisions to be submitted to the backend before failing.
  - **Solution**:
    - Enhance `ValueDialog` to accept a `forbidden_values` set and custom collision message, and pass existing workspace names when opening the `Clone Workspace` dialog so name collisions are caught inline in real time.

- [x] **9. Inline Snapshot Label Collision Validation in Snapshot Dialog (`chrootables/app.py`)**
  - **Impact**: Medium — `Window.snapshot()` opens `ValueDialog` for a snapshot label without checking if that snapshot label already exists on the selected workspace (`row["snapshots"]`), resulting in an avoidable backend failure round-trip.
  - **Solution**:
    - Pass the selected workspace's existing snapshot names as `forbidden_values` to `ValueDialog` in `Window.snapshot()` so duplicate snapshot names are blocked inline with a clear diagnostic message.

- [x] **10. Activity Journal (`activity.tsv`) Automatic Compaction (`chrootable-manager`)**
  - **Impact**: Medium — Every store mutation appends to `$STORE_DIR/activity.tsv`, which grows without bound over the lifetime of the host. While `Store.inventory()` seeks to the tail `256 KiB`, the file itself is never compacted.
  - **Solution**:
    - Implement atomic tail compaction in `log_activity()` when `activity.tsv` exceeds `1 MiB` (retaining the most recent `500` structured events via atomic temporary file replacement under file lock or store serialization).

---

## Tier 3 — Built-in Terminal Emulation & External Terminal Parity

- [x] **11. Modern Linux Terminal Emulator Discovery in `terminal_command()` (`chrootables/backend.py`)**
  - **Impact**: Medium — When falling back to an external host terminal emulator, `terminal_command()` only checks `konsole`, `gnome-terminal`, and `xterm`. Modern Linux distributions default to `ptyxis` (Ubuntu/Fedora), `kgx` (GNOME Console), `xfce4-terminal`, `alacritty`, `kitty`, `foot`, or `wezterm`, causing external terminal launch to fail on systems without legacy terminals installed.
  - **Solution**:
    - Expand `terminal_command()` to support `ptyxis`, `kgx`, `alacritty`, `kitty`, `wezterm`, `foot`, `xfce4-terminal`, `mate-terminal`, `tilix`, `konsole`, `gnome-terminal`, and `xterm` with their exact argument conventions.

- [x] **12. Terminal `VtScreen` ECMA-48 / ANSI Completeness (`chrootables/terminal.py`)**
  - **Impact**: Medium-Low — `VtScreen` in `chrootables/terminal.py` lacks support for `CSI b` (`REP` — repeat preceding graphic character, frequently emitted by `ncurses`/`htop`/`vim`), `SGR 2` (dim/faint), `SGR 9` / `SGR 29` (strikethrough), and modified arrow keys (`Ctrl+Left` / `Ctrl+Right` word movement sequences `\x1b[1;5D` / `\x1b[1;5C`).
  - **Solution**:
    - Add `dim` and `strike` attributes to `Cell`, implement SGR `2`, `9`, `22` (resets bold and dim), and `29` in `VtScreen._sgr()`, implement `CSI b` (`REP`) tracking the last printed graphic character, render `dim` and `strike` in `TerminalDisplay.paintEvent()`, and support modified cursor keys in `key_to_bytes()`.

- [x] **13. Built-in Terminal Scrollback Search & Clear Actions (`chrootables/terminal.py`)**
  - **Impact**: Low-Medium — `TerminalWindow` provides Copy, Paste, Reset Size, and Detach, but lacks a Find/Search bar to locate text across scrollback + screen lines and a `Clear Scrollback` action.
  - **Solution**:
    - Add a toggleable search bar (`Ctrl+Shift+F` / Find button) to `TerminalWindow` that searches across scrollback and live screen lines, highlights matches in `TerminalDisplay`, and jumps to matching scrollback offsets, plus a `Clear Scrollback` action.

---

## Tier 4 — Diagnostics, UI Polish, Demo Fidelity & Documentation

- [x] **14. CLI `--inspect` Origin, Recovery Hints & Export Count (`chrootable-manager`)**
  - **Impact**: Low — `image_details()` (`--inspect NAME` human-readable output) omits the workspace's `origin` lineage field (which is present in `--inspect NAME --json`), associated export archive count, and any interrupted operation warnings targeting that workspace.
  - **Solution**:
    - Include `Origin:`, `Exports:`, and any active/interrupted `Pending Op:` diagnostics in `image_details()` human-readable output.

- [x] **15. Distinct WorkspaceRow Profile Badges for `rust`, `go`, and `web` (`chrootables/app.py`)**
  - **Impact**: Low — `WorkspaceRow.update_row()` only maps `{"python": "Py", "build": "C+", "network": "~"}` and falls back to `"box"` for `rust`, `go`, and `web` workspaces.
  - **Solution**:
    - Map `"rust": "Rs"`, `"go": "Go"`, `"web": "JS"`, and `"minimal": "Min"` in `WorkspaceRow.update_row()` so every profile has an immediately recognizable visual badge in the workspace list.

- [x] **16. Export Archive Listing & Direct Export Deletion in Desktop Workbench (`chrootables/app.py`, `chrootables/backend.py`)**
  - **Impact**: Low — While the Overview tab shows the path of the latest export for a workspace, users cannot view all retained `.tar.gz` exports across the store (including orphaned exports from deleted workspaces) or delete individual exports from the UI.
  - **Solution**:
    - Include store-wide export archives (`"exports"` list with filename, path, size_bytes, updated) in `Store.inventory()` and `normalize_inventory()`, and surface them in the Desktop Workbench so users can re-import or delete retained export archives.

- [x] **17. Demo Dataset (`assets/demo.json` & `run_demo`) Schema & Profile Parity (`assets/demo.json`, `chrootables/app.py`)**
  - **Impact**: Barely a concern — `assets/demo.json` and `run_demo()` predate the `rust`/`go`/`web` profiles, `storage` breakdown summary, `archives` cache list, `exports` list, and `operations` list.
  - **Solution**:
    - Update `assets/demo.json` and `run_demo()` to include modern profile coverage (`rust`), complete `storage` telemetry, `archives`, `exports`, `operations`, and support for the new client actions in demo mode.

- [x] **18. Version Release Bump (`1.2.0`), Man Pages, Capabilities & Documentation Sync (`chrootables/__init__.py`, `chrootable-manager`, `packaging/*.1`, `README.md`, `ROADMAP.md`, `CHANGELOG.md`)**
  - **Impact**: Barely a concern — All new CLI flags (`--import`, `--prune-logs`, `--delete-export`), capabilities schema additions, and Desktop Workbench capabilities must be documented in man pages, `README.md`, `ROADMAP.md`, and `CHANGELOG.md` for the `1.2.0` release.
  - **Solution**:
    - Bump version to `1.2.0`, update man pages (`packaging/chrootable-manager.1`, `packaging/chrootables-desktop.1`), `README.md`, `ROADMAP.md`, and `CHANGELOG.md`, verify all benchmarks and test suites pass with 0 skips, and build all release binaries (`.deb`, `.rpm`, `.AppImage`, `SHA256SUMS`).
