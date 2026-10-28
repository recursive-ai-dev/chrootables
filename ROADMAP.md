# Chrootables Roadmap

Status as of 2026-10-27. v1.0.0 adds searchable workspace notes, saved library
and session preferences, terminal scrollback/clipboard support, structured host
diagnostics, and reproducible Debian packages. Qt desktop and Bash CLI share one
image store. Debian 12/13 profiles, snapshots, disposable sessions, shared folders,
restricted devices, and offline bootstrap archives are implemented. Historical
audit reports remain under `docs/`; root integration is an opt-in test suite.

## Release checks

- Keep the audit findings as regression baselines: any change must keep the
  measured latency budgets (24-workspace inventory <= 80 ms, per-render
  main-thread work <= 2 ms on background polls).
- Run `scripts/check.py --require-desktop-integration --build-deb`, the package
  installation simulation, and the opt-in root integration suite before publishing.
- Choose an upstream license and add its metadata before public redistribution.
- Commit and tag releases only when requested by the maintainer.

## Phase 1 — Release engineering

- Implemented: GitHub Actions checks on Fedora and Debian, an opt-in root
  integration job, and downloadable `.deb`/SHA256 artifacts.
- Next: signed package repositories (COPR, PPA or OBS), source packages and
  automated installation/upgrade/removal verification in disposable systems.
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

- Implemented: embedded PTY terminal with a compact VT/ANSI subset. Improve
  Unicode cell widths and full-screen terminal fidelity as a separate track.
- Ubuntu and derivatives as additional suites alongside Debian.
- OCI app store / graphical application export — requires a separate product
  decision and execution model.

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
