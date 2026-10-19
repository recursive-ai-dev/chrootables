# Chrootables Roadmap

Status as of 2026-10-19. v3.0.0 desktop is released: Qt desktop and Bash CLI share
one image store, Debian 12/13 images with profiles, snapshots and recovery
points, disposable OverlayFS sessions, shared host folders, polkit-gated
privileged helper. Five audit passes are complete (stabilization, backend
performance, frontend performance, dependency security, resource efficiency)
with regression tests. 69 unit tests, plus an opt-in root integration suite.

## Phase 0 — Land the current work (now)

- Commit the uncommitted stabilization/audit changes; the five audit reports at
  the repo root are untracked.
- Keep the audit findings as regression baselines: any change must keep the
  measured latency budgets (24-workspace inventory <= 80 ms, per-render
  main-thread work <= 2 ms on background polls).
- Tag the next release (v3.1.0) once the working tree is clean and tests pass.

## Phase 1 — Release engineering

- CI: GitHub Actions running `python3 -m unittest discover -s tests`,
  `bash -n chrootable-manager`, and the offscreen `--demo --screenshot` run on
  Fedora and Debian containers, with the root integration suite as an
  opt-in manual job.
- Packaging: publish to COPR (Fedora) and a PPA or OBS (Debian/Ubuntu) so the
  app installs without a checkout; keep `scripts/install-desktop.py` as the
  source-checkout path.
- Release hygiene: audit reports live under `docs/` as historical baselines;
  maintain the CHANGELOG for each release.
- Support matrix: document tested host distros and Python/PySide6 versions.

## Phase 2 — Reach

- Host distros beyond dnf/apt: Arch, openSUSE in `package_for_command` /
  `check_prerequisites`, with per-distro test coverage.
- ARM64 images (aarch64 debootstrap is already suite-driven; verify loop
  mount, OverlayFS, and terminal paths on arm64 hosts).
- Profiles: Rust, Go, and a web-dev profile, following the existing profile
  pattern in `chrootable-manager` and `chrootables/backend.py`.

## Phase 3 — Product features (currently stated non-goals)

Evaluate before building; these are the README's explicit "does not implement"
items, so each needs a decision, not just code:

- Embedded terminal widget (QTerminal/libvte via PySide6) to remove the
  Konsole/GNOME Terminal/xterm dependency — highest user-visible value of the
  three.
- Ubuntu and derivatives as additional suites alongside Debian.
- OCI app store / graphical application export — largest scope; only after
  the embedded terminal and packaging land.

## Phase 4 — Security model (long term)

- The trust model is explicit: trusted development, host root, full `/dev`.
  Any hardening track must not blur that promise silently. Candidates:
  - Optional restricted `/dev` mode (no direct disk/device access) per session.
  - Rootless operation via user namespaces and fuse-overlayfs as a separate,
  clearly labeled mode — not a swap of the existing model.

## Standing rules

- Every fix ships with a regression test, as the stabilization pass did.
- The CLI and desktop must keep sharing one image store and stay in behavioral
  parity; new session features land in both.
- Performance budgets from the audits are release gates, not suggestions.
