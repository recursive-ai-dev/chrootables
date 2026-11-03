# Chrootables 1.3.0 engineering audit

Audit date: 2026-11-03. Baseline: `e704e57` / v1.2.2.

## Scope and architecture

Nine evidence-backed follow-up improvements are tracked in TODO.md; the prior
sprint is archived in TODO-1.2.2-ARCHIVE.md. CLI/store ownership, the bounded
privileged broker, Python backend and Qt presentation boundaries are preserved.

Official Debian guests now use signed main, updates and security sources. Fresh
creation applies available security upgrades, including the minimal profile.
Offline archives preserve the exact source configuration and downloaded upgrades;
consumption validates the entire cache tree before any privileged extraction or
execution. Custom mirror policy and safe legacy caches remain compatible.

Normal-user exports prefer anonymous O_TMPFILE staging and atomically publish
completed, flushed content. Named staging remains the explicit filesystem fallback
with cleanup guidance after uncatchable interruption. Imported archives keep a
validated regular descriptor while attempting a reflink; safe sparse copy and
interpreter-free tar streaming remain available fallbacks.

The default AppImage bundles pinned CPython 3.13.18 and PySide6/Qt 6.11.2. Native
packages retain distribution runtimes. The explicitly selected host-runtime
AppImage variant remains supported. Runtime inputs and source archives have exact
SHA-256/size pins, bounded extraction, provenance and license records. Dynamically
linked libraries remain replaceable; matching upstream source archives accompany
the release. The source bundle also includes ICU 73.2 and Python Build Standalone
build scripts. New native ARM64 CI covers Debian packages, bundled AppImage and
privileged operations.

## Adversarial findings

Independent reviews caught and corrected unsafe nested cache entries, optional Qt
plugins with unbundled dependencies, malformed wheel paths and insufficient
host-runtime masking. A headless Wayland run also exposed a missing client window
decoration plugin and shared temporary extraction interference after root testing.
Review of the corrective launcher caught detached terminals waiting for
authentication after GUI exit; a narrowly scoped retention marker prevents
removing their runtime before the command starts. Helper and export
interpreter launches disable bytecode writes so root-owned caches cannot prevent
the invoking user from removing a completed runtime. The portable test masks host Python executables, PySide and
Qt libraries inside a private mount namespace. It checks dependency resolution,
privileged-helper invocation, normal-user export and actual offscreen/Xvfb startup.

Real integration first encountered the production import capacity guard on a
RAM-backed /tmp. Moving fixtures to SSD then exposed host TMPDIR leaking into
guest package scripts. The guest environment boundary is covered separately from
the host test directory. Neither capacity checks nor security checks were weakened.

## Performance

A local XFS test copied a 128 MiB input three times, including synchronization.
Median safe tar staging took 0.37515 seconds; descriptor-based reflink staging took
0.02531 seconds (14.82 times faster for this synthetic operation). Shared extents,
SHA-256 equality and post-copy mutation isolation were checked. This is not a claim
about whole-application speed; filesystem, cache and system load affect results.

## Verification and release gates

- 110 CLI tests, 72 backend tests and 12 session/shared-folder tests passed.
- Real privileged minimal and offline Python integrations passed on x86_64,
  including security upgrades, cache publication/consumption, network-disabled
  creation, namespace/mount behavior, snapshots, restore/recovery, shared folders,
  expansion/repair, export/import and durable operation records. Host TMPDIR was
  on SSD outside /tmp; both fixture directories and loop mounts were cleaned.
- Final runtime input tests cover bounded/malformed archives, cache links/FIFOs,
  digest/size corruption and missing/escaping attribution references.
- Independent binary audit checked all 275 selected notices byte-for-byte against
  pinned archives, all 58 unique attribution targets (84 references), LGPL/GPL
  texts, IJG acknowledgment and all seven source archive SHA-256/size pairs.
- DEB and RPM rebuilt identically across output paths and umasks 022/077; extracted
  CLI, diagnostics, helper and desktop smoke checks passed.
- Final full local check: **388 tests passed, zero skipped**, syntax checks,
  both demos and required DEB build. Final inventory medians: fresh 42.561 ms
  (80 ms budget), cached 1.365 ms (10 ms budget), unchanged render 0.850 ms
  (2 ms budget). Corresponding p95 values: 56.161, 2.144 and 0.966 ms.
- Final x86_64 bundled and compatibility AppImages launched normally through FUSE
  as the user after the root check. Private launch directories were removed.
  The bundled artifact also passed with host Python/PySide/Qt masked: helper,
  normal-user export, no bytecode mutation, offscreen, Xvfb/xcb and headless
  Weston/pixman Wayland rendering with client decorations.
- [Release-validation CI](https://github.com/recursive-ai-dev/chrootables/actions/runs/41719520388) passed all seven jobs on `3e99b6d`: native x86_64/ARM64
  tests, installation/upgrade/removal, privileged integration and both AppImage
  variants. Final tag/publication evidence is recorded below.
- Local DEB, RPM, bundled/compatibility x86_64 AppImages and runtime source bundle
  reproduced byte-for-byte across directories and umasks 022/077. The release RPM
  is selected from Fedora CI, where that exact package passed installation tests;
  its code/assets/permissions and decompressed documentation match the local RPM.
  Compressed documentation and RPM metadata differ between the build environments.
- Build host: Ubuntu 26.04.1 x86_64, Linux 7.0.0-42-generic, Python 3.14.4,
  PySide6 6.10.2, Bash 5.3.9, dpkg-deb 1.23.7, RPM 6.1.0 and glibc 2.43.
  SOURCE_DATE_EPOCH=1793059200; AppImage tools/runtime and bundled input pins are
  recorded in repository manifests. BUILDINFO.txt accompanies release assets.

## Remaining platform limits

Supported binaries target Linux: architecture-independent DEB/RPM and bundled
x86_64/aarch64 AppImages. The bundle still requires normal host graphics libraries
and glibc 2.34+ (x86_64) or 2.39+ (ARM64). Offscreen, X11/Xvfb and
headless Weston/Wayland execution are checked; physical desktop/GPU coverage is
not claimed. No Windows/macOS execution is
claimed. Guests run as trusted host-root environments, not security sandboxes.
Offline caches require refresh to receive future security fixes. Filesystems
without anonymous staging can retain a recognizable hidden export staging file
following SIGKILL or a crash; final destination publication remains atomic.
Private AppImage runtimes are retained after abnormal termination or detached
terminal handoff; remove them only after operations, helpers and sessions finish.
Explicit upstream extract-and-run mode needs a private TMPDIR to avoid sharing
content-named extraction paths across users.

An obsolete root-owned extraction from an earlier verification run remains at
`/tmp/appimage_extracted_568014dbdd34b32f7cc0bd9b7d744d5d`: cached sudo authentication
expired before cleanup. This is ENV-01 in TODO.md, isolated from the release and
repository. The corrected harness and final default launches clean their own
paths. No credentials or authentication bypass were used.

## Publication verification

- [Exact-tag CI](https://github.com/recursive-ai-dev/chrootables/actions/runs/41720548404)
  passed all seven jobs on `be038d6db2123fabeb518ba882acbdc2ab45f17b`.
  This tag adds only audit/TODO documentation to the locally tested application
  source `3e99b6d1be22bd80eb082a8ab4a12838d28015a9`.
- [Release v1.3.0](https://github.com/recursive-ai-dev/chrootables/releases/tag/v1.3.0)
  was published on 2026-11-03 and verified as the latest public release. It has
  exactly 16 expected assets. Every asset was downloaded after publication and
  matched the verified original byte-for-byte; SHA256SUMS and all seven individual
  checksum sidecars passed. The x86_64 CI AppImages match local builds, and the
  architecture-independent DEB matches both native CI architectures exactly.
- Nine engineering improvements are VERIFIED. ENV-01 remains blocked solely by
  expired local sudo authentication; it does not affect any release artifact.
- A subsequent documentation-only commit records this publication evidence;
  the release tag continues to identify the tested source and distributed payload.

| Artifact | Bytes | Execution validation |
| --- | ---: | --- |
| chrootables_1.3.0-1_all.deb | 104852 | Debian trixie, native x86_64 and ARM64 |
| chrootables-1.3.0-1.noarch.rpm | 113525 | Fedora, native x86_64 |
| Chrootables-1.3.0-1-x86_64.AppImage | 59271672 | Ubuntu 26.04 local/native CI; no-host GUI/helper/export |
| Chrootables-1.3.0-1-aarch64.AppImage | 53611016 | Ubuntu 26.04 native ARM64 CI; no-host GUI/helper/export |
| Chrootables-1.3.0-1-x86_64-host-runtime.AppImage | 1059320 | Local and native x86_64 CI |
| Chrootables-1.3.0-1-aarch64-host-runtime.AppImage | 1051144 | Native ARM64 CI |
| Chrootables-1.3.0-runtime-sources.tar.gz | 120703170 | All pinned source hashes/licenses; identical across architectures |

Each binary/source archive has a checksum sidecar. BUILDINFO.txt and SHA256SUMS
complete the 16-asset release. No Windows/macOS or physical GPU validation is claimed.
