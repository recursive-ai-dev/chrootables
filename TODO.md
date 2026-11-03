# Chrootables 1.3.0 release mitigation TODO

Baseline: `e704e57` / v1.2.2. Previous sprint archived in `docs/TODO-1.2.2-ARCHIVE.md`.

Nine meaningful follow-up improvements were identified from actual remaining limitations; no extra findings are invented to reach twelve. Scope preserves Linux architecture and prioritizes security, lifecycle, distribution portability and measured efficiency.

## REL-01 — Guest APT sources and offline archives

- Priority / severity: **P1 / High**
- Status: **VERIFIED**
- Affected components: CLI guest bootstrap, APT and archive cache.
- Problem / evidence: Official-mirror creation omits security updates; cache index identity depends on sources.
- Implementation: Configure signed main/updates/security sources for supported official Debian suites and retain exact sources in offline archives; keep custom mirrors compatible.
- Acceptance: Guest apt sources include security; fresh and offline installs use matching signed indices.
- Required tests: Source selection, supported suite aliases, custom mirrors, legacy/new cache and real privileged integration.
- Dependencies: None
- Verification evidence: 110 CLI tests passed. Real minimal and cached Python integration passed on x86_64 with signed sources, certificates, security upgrades, and network-disabled cached creation.

## REL-07 — Offline cache trust validation

- Priority / severity: **P1 / High**
- Status: **VERIFIED**
- Affected components: CLI offline cache validation and publication.
- Problem / evidence: archive_has_profile accepts base.tgz/mirror by size without validating nested cache ownership, permissions or file types; offline debootstrap executes that cached base as root.
- Implementation: Validate consumed cache trees before reuse, reject unsafe writable/foreign-owned/symlink/special entries, and publish safe owner/mode metadata.
- Acceptance: Unsafe caches fail closed before extraction or guest command execution; generated and safe legacy caches remain usable.
- Required tests: Nested directory/file permissions and ownership, links, FIFOs, missing/incomplete records and real cached creation.
- Dependencies: REL-01
- Verification evidence: 110 CLI tests passed, including recursive owner/mode/link/FIFO and failed-scan rejection. Real cache publication/consumption integration passed with safe metadata.

## REL-08 — Host/guest temporary directory boundary

- Priority / severity: **P1 / High**
- Status: **VERIFIED**
- Affected components: CLI chroot execution, integration harness.
- Problem / evidence: A real integration run with host TMPDIR on SSD failed ca-certificates configuration because that absolute host path does not exist inside the guest.
- Implementation: Normalize temporary-directory environment at the chroot boundary while retaining explicit operation settings; let the test harness place large images on an appropriate host filesystem.
- Acceptance: Host TMPDIR may point outside /tmp without breaking guest bootstrap, APT, cached creation or interactive commands.
- Required tests: Guest environment regression tests and real minimal/offline Python integration with an external TMPDIR.
- Dependencies: REL-01
- Verification evidence: 110 CLI tests and 12 session/shared-folder tests passed. Poisoned environment and real mktemp regressions passed; both privileged integrations passed with host TMPDIR on SSD outside /tmp.

## REL-02 — User export lifecycle

- Priority / severity: **P2 / Medium**
- Status: **VERIFIED**
- Affected components: Python backend export writer and helper launch.
- Problem / evidence: Named staging survives uncatchable writer termination.
- Implementation: Prefer anonymous O_TMPFILE, atomically link completed contents; preserve a safe filesystem fallback with explicit residual-file guidance.
- Acceptance: SIGKILL before publication leaves no file on supporting filesystems; fallback never exposes partial final contents.
- Required tests: Anonymous/fallback SIGKILL, unsupported-feature handling, permission/disk/fsync/link faults, races.
- Dependencies: None
- Verification evidence: 72 backend tests passed, including real anonymous/fallback SIGKILL, fsync/link/disk errors and destination races. Independent backend review passed.

## REL-03 — AppImage runtime distribution

- Priority / severity: **P2 / Medium**
- Status: **VERIFIED**
- Affected components: AppImage builder, runtime manifest, fetcher and portable checks.
- Problem / evidence: The existing AppImage requires host Python and PySide6, limiting portability.
- Implementation: Bundle pinned CPython and locked Qt wheels with provenance/licenses and isolated relocated helper launches.
- Acceptance: AppImage CLI/helper/desktop run without host Python or Qt; builds are reproducible and downloads hash-checked.
- Required tests: Malformed runtime/cache input, no-host-runtime smoke, reproducibility, GUI/plugin checks and helper isolation.
- Dependencies: None
- Verification evidence: 388 tests passed with zero skips. Both architecture CI AppImage runtime tests passed; pinned source/license audit passed. Final x86_64 bundle passed actual FUSE/default and no-host offscreen/Xvfb/Wayland launch, helper/export and no-bytecode checks. Local builds reproduce across directories and umasks.

## REL-04 — ARM64 distribution coverage

- Priority / severity: **P2 / Medium**
- Status: **VERIFIED**
- Affected components: CI matrix and AppImage tool pins.
- Problem / evidence: No native ARM64 tests or verified architecture-specific AppImage tools exist.
- Implementation: Add native ARM64 Debian tests/install/integration, pinned ARM AppImage tools and bundled ARM artifacts.
- Acceptance: Native ARM CI exercises runtime and privileged operations; only verified artifacts are published.
- Required tests: Native unit/demo/package install/upgrade/removal and minimal/Python privileged integration.
- Dependencies: REL-03
- Verification evidence: Native ARM64 Debian unit/demo/install/upgrade/removal, minimal/offline Python integration, bundled and compatibility AppImage reproducibility/startup, no-host helper/export/Xvfb/Wayland checks passed in CI run 41719520388.

## REL-05 — Import staging performance and capacity

- Priority / severity: **P3 / Low**
- Status: **VERIFIED**
- Affected components: CLI import staging.
- Problem / evidence: Private tar copy always duplicates compressed input, even on reflink-capable stores.
- Implementation: Safely open/validate a regular source descriptor before optional reflink copy; retain safe dependency-free fallback and capacity checks.
- Acceptance: Original source mutation cannot change staged validation bytes; special files fail without blocking; reflink avoids duplicate allocation when supported.
- Required tests: File replacement, FIFO/device/directory races, fallback, allocation and measured reflink/copy behavior.
- Dependencies: None
- Verification evidence: 110 CLI tests passed, including source replacement/special files/copy failure/Perl startup isolation. Real XFS 128 MiB benchmark: tar median 0.37515 s vs reflink 0.02531 s; checksum, shared extents and mutation isolation checked.

## REL-09 — Portable extraction isolation and cleanup

- Priority / severity: **P2 / Medium**
- Status: **VERIFIED**
- Affected components: AppImage launch and portable verification harness.
- Problem / evidence: A real root portable check left a root-owned upstream extraction directory in shared /tmp; the next unprivileged launch of the same artifact failed with permission denied before reaching Python.
- Implementation: Isolate extraction temporary directories and clean owned fixtures deterministically; retain interrupted runtimes and detached terminal handoffs with explicit cleanup guidance; suppress privileged bytecode writes; retain safe permissions and cover supported launch paths.
- Acceptance: Root verification cannot block a subsequent normal-user launch; invocations never use another user's extraction cache.
- Required tests: Repeated/overlapping launches, signal handling, detached authentication/handoff, cleanup and real root-to-normal-user startup.
- Dependencies: REL-03
- Verification evidence: Six launcher regressions, 50 desktop tests and 72 backend tests passed within the 388-test full suite. Actual root helper/export created no bytecode and the invoking user removed its runtime. Final root portable check followed by normal-user launches passed; normal exits left no private extraction directories.

## REL-06 — Release preparation and verification

- Priority / severity: **P2 / Medium**
- Status: **VERIFIED**
- Affected components: Version metadata, docs, artifacts and GitHub release.
- Problem / evidence: New mitigation work needs final release evidence beyond 1.2.2.
- Implementation: Prepare 1.3.0 with full regression, independent review, supported artifacts, checksums/provenance and release publication.
- Acceptance: All feasible tasks verified; final CI and downloaded release assets pass; limitations documented precisely.
- Required tests: Full local suite, native CI, reproducible builds and post-publication checksum verification.
- Dependencies: REL-01 through REL-05, REL-07, REL-08, REL-09
- Verification evidence: 388 local tests passed with zero skips. All seven exact-tag CI jobs passed (run 41720548404) on be038d6. Release v1.3.0 is public/latest with 16 expected assets; every asset was downloaded and matched the verified original, and all checksum files passed. Nine engineering improvements are VERIFIED; ENV-01 remains an external local-cleanup blocker.

## ENV-01 — Remove obsolete root-owned verification fixture

- Priority / severity: **P3 / Low** (local environment cleanup, not an application defect)
- Status: **BLOCKED**
- Affected component: `/tmp/appimage_extracted_568014dbdd34b32f7cc0bd9b7d744d5d` from an earlier root AppImage check.
- Problem / evidence: The old upstream runtime retained this root-owned extraction. Final artifacts use different contents and the corrected harness owns and cleans its own temporary paths. Both cleanup attempts returned `sudo: interactive authentication is required` after cached authorization expired.
- Proposed action: Renew sudo authentication, then remove only that obsolete directory; no credentials are stored or bypass attempted.
- Acceptance: The exact directory no longer exists; unrelated temporary directories remain intact.
- Required test: Confirm no process uses the old fixture, remove it, verify absence.
- Dependencies: External sudo authentication. Release verification and publication can proceed independently.
