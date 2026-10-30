# Changelog

The current release is numbered `1.2.0`. Earlier development release numbers
are retained below as historical records.

## v1.2.0 — 2026-10-30

- Add workspace archive import (`--import NAME ARCHIVE.tar.gz [SIZE_MIB] [persistent|ephemeral] [PROFILE]`)
  in both the CLI and desktop workbench, validating tar archives against path
  traversal, absolute paths and device/FIFO nodes before extracting into a fresh
  sparse ext4 image.
- Surface interrupted staged-publication operations in the desktop Recovery view
  with a one-click **Recover publication** action (`--recover-operation ID --yes`).
- Add safe operation cancellation (`Cancel` in the desktop task panel and broker
  `cancel` action) that terminates in-flight bootstraps/copies while refusing to
  interrupt atomic `publish-image` / `publish-metadata` commits.
- Add store retention maintenance: `--prune-logs [KEEP_COUNT] --yes`,
  `--delete-export FILENAME --yes`, retained export re-import/deletion in the
  desktop Recovery view, and automatic tail compaction of `activity.tsv` when
  the journal exceeds `CHROOTABLE_ACTIVITY_MAX_BYTES`.
- Add desktop offline archive cache status and **Sync offline archive** action
  (`--archive-sync SUITE`) in Host Health.
- Support sub-GiB (`512–1048576 MiB`) capacity selection in the desktop Create
  dialog alongside `GiB`, matching CLI capability bounds.
- Update interactive TUI creation (`create_chrootable`) to offer all 7 profiles
  (`minimal`, `build`, `python`, `network`, `rust`, `go`, `web`), profile-aware
  recommended sizes, and accurate sparse-allocation messaging.
- Add inline duplicate-name and duplicate-label collision validation in Clone
  and Save Snapshot dialogs, and distinct profile badges (`Rs`, `Go`, `JS`, `Min`)
  in workspace rows.
- Expand external terminal detection (`ptyxis`, `kgx`, `alacritty`, `kitty`,
  `wezterm`, `foot`, `xfce4-terminal`, `mate-terminal`, `tilix`), and add SGR
  dim/strikethrough, `CSI b` (`REP`), `Ctrl+Arrow` navigation, and scrollback
  search (`Ctrl+Shift+F`) plus scrollback clearing in the embedded terminal.

## v1.1.0 — 2026-10-27

- Add durable operation IDs/stages, isolated per-operation log streaming and
  explicit recovery of interrupted image/metadata publication. Validate retained
  file identities before recovery; identify interrupted work in host diagnostics.
- Describe image capacity accurately as sparse, without a reservation guarantee;
  check free host space before expansion and image copying.
- Show operation phases and elapsed time, preserve log reading position when
  output following is disabled, and keep concurrent workspace output separate.
- Search recovery/history views, retain selected checkpoint/log identities across
  filtering and refresh, show allocated snapshot/export/log storage, and preview
  the scope and size of checkpoint deletion.
- Support smaller displays, scrolling compact layouts, keyboard navigation and
  persisted 100/125/150% text scaling.
- Render ANSI 256/RGB colors, hidden cursors and text attributes correctly. Handle
  combining accents and East Asian wide cells in terminal editing, wrapping,
  selection, copying and resizing; full emoji grapheme shaping remains unsupported.
- Enforce inventory/render latency budgets with a realistic rootless benchmark;
  avoid redundant shell lock-probe subprocesses while preserving live busy state.
- Read desktop profiles, supported releases and capacity limits from the CLI's
  versioned capability response.
- Share reproducible package staging and protected artifact publication across
  DEB, RPM and host-runtime AppImage builds. Never install/download build tools
  implicitly; explicitly fetched AppImage tools and runtime are checksum-pinned.
- Add native package installation/upgrade/removal checks and AppImage runtime
  checks in CI; correct Debian's split Qt dependency names and run privileged
  minimal/Python integration on release branches.
- License the project and packaged application under Apache-2.0.

## v1.0.0 — 2026-10-27

- Search personal workspace notes alongside names, profiles and releases.
  Notes are saved per user, workspace and store; editing during a background
  refresh keeps the original target. The tour never saves notes.
- Sort by favorites, name, recent use, creation time, capacity or allocated
  space. Remember filter/sort choices per store, window geometry, splitter
  position, and per-workspace network/device settings. Clearing a shared
  folder preserves the other workspace preferences.
- Add 2,000 lines of terminal scrollback, mouse selection, Ctrl+Shift+C/V,
  wheel and Shift+PageUp/PageDown navigation, and bracketed paste. Queue PTY
  input without blocking the desktop when a guest stops reading.
- Wait for the embedded session to release its PTY before opening an external
  terminal, avoiding a race with the workspace lock. Handoff failures remain
  visible and retryable; closing the session cancels a pending handoff.
- Expand read-only host diagnostics with structured `--doctor --json` output,
  actionable remedies, store ownership/access checks and capacity reporting.
  Distinguish blocking errors from kernel/device warnings; only offer package
  installation when dependency checks fail.
- Build reproducible versioned Debian archives with normalized ownership,
  modes and timestamps, payload checksums, compressed manuals/documentation,
  atomic output and SHA256 sidecars. Add revision/overwrite controls and CI
  package artifacts; synchronize desktop and CLI release versions.

- Harden embedded terminal escape parsing against unsupported and oversized
  sequences, and keep RGB values within the renderer's range. Fix saved cursor
  restoration after shrinking the window and clearing text before the cursor.
- Stop the embedded terminal's process group when closing a session, so child
  commands do not keep the PTY open after Ubuntu's shell exits.
- Add a rootless `.deb` builder, system menu entry, and Ubuntu/Debian runtime
  dependencies. Packaged launchers use system interpreters so Homebrew Python
  and virtual environments do not interfere with distribution Qt bindings.
- Offline creation: `--archive-sync SUITE [PROFILE...]` downloads the
  minbase system (as a debootstrap tarball), the Debian-signed apt lists, and
  every profile deb into `archive/` in the store. Creates for that suite then
  bootstrap and install profiles with no network; profiles not synced still
  fall back to the mirror. Set `CHROOTABLE_ARCHIVE=0` to bypass the archive.
- Embedded terminal: workspace sessions now open in a pty-backed terminal
  pane inside the desktop window, with an option to hand the session to an
  external terminal. Covers a compact VT/ANSI subset (cursor addressing,
  erase, insert/delete, SGR colors, alt-screen, scroll regions).
- Opt-in restricted devices: `--restricted-dev` (CLI) and a "Restrict
  devices" checkbox (desktop) give a session a minimal `/dev` without host
  device nodes. The default trust model is unchanged.
- New workspace profiles: `rust` (rustc, cargo), `go` (golang-go), and `web`
  (Node.js, npm), available in both the CLI and the desktop create dialog.
- Host dependency auto-install now supports Arch (`pacman`) and openSUSE
  (`zypper`) alongside `dnf` and `apt-get`, with distro-specific package
  name mappings. The dependency log path is overridable with
  `CHROOTABLE_DEP_LOG` for non-root verification.

## v3.1.0 — 2026-10-19

Landed the stabilization and audit passes and added the project roadmap.

- Stabilization fixes across the CLI, desktop, helper, and installer, each
  with a regression test: resize capacity re-check after lock acquisition,
  unterminated metadata records, workspace-action target pinning during
  refresh, selection identity tracking, stat failure propagation, operation
  cleanup, inventory cache locking, nonblocking FIFO lock probes, log
  streaming offsets, control restoration after failed operations, client
  teardown ordering, and desktop-entry escaping.
- Performance: batched `stat` calls and render caching; 24-workspace
  inventory latency reduced from 628 ms to 22 ms, dashboard from 314 ms to
  17 ms, background poll render cost to near zero.
- Dependency security: `requirements.txt` pinned and hash-locked to
  `PySide6-Essentials==6.11.2` / `shiboken6==6.11.2`.
- Resource efficiency audit applied.
- Added `ROADMAP.md`, GitHub Actions CI (Fedora + Debian containers, opt-in
  root integration job), this CHANGELOG, and a README support matrix.
- Audit reports moved under `docs/` as historical baselines.

## v3.0.0 — 2026-09-16

Desktop release with shared folder support.

- Qt desktop workbench: workspace library, recovery, activity, host health.
- Shared host folders at `/shared` with read-only option, per-workspace
  desktop preferences.
- Rendering optimization with caching and incremental updates.

## Earlier

Initial terminal workflow (`chrootable-manager`), image store, snapshots,
disposable sessions, and the stdio privilege broker.
