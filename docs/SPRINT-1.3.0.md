# Chrootables 1.3.0 engineering audit

Audit date: 2026-11-03. Baseline: `e704e57` / v1.2.2.

## Scope and architecture

Eight evidence-backed follow-up improvements are tracked in TODO.md; the prior
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
host-runtime masking. The portable test masks host Python executables, PySide and
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

Final regression, native architecture CI, artifact reproducibility and publication
checks are in progress. Completed checks and exact release evidence will be
recorded here before closing REL-06.

## Remaining platform limits

Supported binaries target Linux: architecture-independent DEB/RPM and bundled
x86_64/aarch64 AppImages. The bundle still requires normal host graphics libraries
and glibc 2.34+ (x86_64) or 2.39+ (ARM64). Offscreen and X11/Xvfb execution are
checked; a physical Wayland desktop is not claimed. No Windows/macOS execution is
claimed. Guests run as trusted host-root environments, not security sandboxes.
Offline caches require refresh to receive future security fixes. Filesystems
without anonymous staging can retain a recognizable hidden export staging file
following SIGKILL or a crash; final destination publication remains atomic.
