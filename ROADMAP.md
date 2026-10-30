# Chrootables Roadmap

Status as of 2026-10-30. v1.2.0 adds validated workspace archive import
(`--import`), desktop interrupted-publication recovery, safe operation
cancellation, log/export retention management and journal compaction, desktop
offline archive syncing, sub-GiB (`512 MiB+`) desktop creation, full 7-profile
interactive TUI parity, embedded terminal search (`Ctrl+Shift+F`) and extended
VT/SGR sequences, and broadened external terminal support. The project is Apache-2.0.
Qt desktop and Bash CLI continue to share one image store and the trusted-root
execution model. Historical audit reports remain under `docs/`.

## Release checks

- Keep the audit findings as regression baselines. `scripts/benchmark.py` gates
  median fresh inventory <= 80 ms, cached inventory <= 10 ms and unchanged
  synchronous background rendering <= 2 ms on 24 workspaces with live lock files;
  p95 latency is reported alongside the gate results.
- Run `scripts/check.py --require-desktop-integration --build-deb`, the package
  installation simulation, and the opt-in root integration suite before publishing.
- Keep Apache-2.0 metadata and the upstream license text in every package.
- Commit and tag releases only when requested by the maintainer.

## Phase 1 — Release engineering

- Implemented: GitHub Actions checks on Fedora and Debian; isolated package
  installation/upgrade/removal checks; DEB/RPM/AppImage builds and SHA256
  artifacts; explicitly pinned AppImage tools/runtime; root minimal/Python
  integration on release branches and manual runs.
- Next: signed package repositories (COPR, PPA or OBS), source packages,
  signed release provenance, and a separately evaluated self-contained AppImage.
  Keep `scripts/install-desktop.py` as the source-checkout path.
- Release hygiene: audit reports live under `docs/` as historical baselines;
  maintain the CHANGELOG for each release.
- Support matrix: document tested host distros and Python/PySide6 versions.

## Phase 2 — Reach

- Implemented: best-effort Arch/openSUSE dependency mappings and Rust, Go,
  and Node.js development profiles.
- ARM64 images (aarch64 debootstrap is already suite-driven; verify loop
  mount, OverlayFS, and terminal paths on arm64 hosts).
- Next: additional host coverage beyond the Debian/Fedora CI matrix.

## Phase 3 — Product features (currently stated non-goals)

Evaluate before building; these are the README's explicit "does not implement"
items, so each needs a decision, not just code:

- Implemented: embedded PTY terminal with ANSI colors/text attributes, combining
  marks and East Asian wide-cell support. Full emoji grapheme shaping and complete
  full-screen terminal fidelity remain a separate track.
- Ubuntu and derivatives as additional suites alongside Debian.
- OCI app store / graphical application export — requires a separate product
  decision and execution model.

## Next product work

- Storage retention policies and export cleanup previews after validating a
  predictable recovery/retention contract; existing checkpoint deletion remains
  explicit and scoped to the selected snapshot.
- Operation-specific safe cancellation and resumable bootstraps. Interrupted
  publication can be recovered now; earlier-stage partials are retained for
  inspection rather than automatically resumed or deleted.
- Workspace import/portable backup bundles, with format validation and
  round-trip recovery tests before exposing imports in the desktop.

## Phase 4 — Security model (long term)

- The trust model is explicit: trusted development, host root, full `/dev`.
  Any hardening track must not blur that promise silently. Candidates:
  - Implemented: optional restricted `/dev` mode per session.
  - Rootless operation via user namespaces and fuse-overlayfs as a separate,
  clearly labeled mode — not a swap of the existing model.

## Standing rules

- Every fix ships with a regression test, as the stabilization pass did.
- The CLI and desktop must keep sharing one image store and stay in behavioral
  parity; new session features land in both.
- Performance budgets from the audits are release gates, not suggestions.
