# Changelog

## Unreleased

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
