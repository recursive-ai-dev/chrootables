# Chrootables 1.2.2 engineering audit

Audit date: 2026-11-03. Baseline: `8c0a816` (1.2.1).

## Architecture and scope

The Bash CLI owns the store, image locks, durable operation records and Linux
namespace workers. The Python backend validates fixed operations, reads store
metadata and manages subprocess cancellation. A bounded stdio broker connects the
unprivileged Qt desktop to an explicitly authenticated helper. The terminal owns
its VT screen state, PTY descriptors, process lifecycle and input queue. Packaging
shares one normalized payload across DEB, RPM and host-runtime AppImage builders.

The sprint preserves these boundaries and public commands. It adds private import
staging, atomic normal-user export publication, consistent store path handling and
bounded terminal/request input. No new third-party dependency was added. TODO.md
contains 16 findings with acceptance criteria and evidence; the previous TODO is
preserved in TODO-1.2.1-ARCHIVE.md.

## Adversarial review

Independent review found and corrected a FIFO-blocking flaw in the first archive
copy implementation, missing cancellation support for the new import stages, an
empty parent path for archives located directly under `/`, and newline truncation
in guest symlink resolution. Import snapshotting
uses a nonrecursive tar stream into private store staging: it never reads FIFO or
device contents and never descends into substituted directories. Validation and
extraction consume the same private regular file. The copy requires temporary
space and a full streaming pass; no import speedup is claimed.

Tests exercise source replacement and in-place mutation, substituted special files,
copy failure, abrupt termination and discard, guest symlink resolution, unsafe
snapshot ownership/modes, malformed operation records, protocol delimiters, export
publication races and disk errors, framing boundaries, PTY backpressure and
fragmented terminal sequences. Randomized terminal testing uses seed `20261103`.

## Dependency and supply-chain checks

`pip install --dry-run --ignore-installed --require-hashes --only-binary=:all:
-r requirements.txt` successfully resolved the pinned PySide6-Essentials 6.11.2
and shiboken6 6.11.2 wheels with matching hashes. OSV API exact-version queries for
both packages returned no advisories on the audit date. This is an advisory lookup,
not a claim that the packages or host distribution are vulnerability-free. The
application uses distribution Qt in native packages. AppImage build tool and
runtime downloads passed the repository's SHA-256 pins. Existing tool-cache and
isolated Python helper security regressions remain enabled.

## Performance

A synthetic 400,000-combining-mark stream took 4.760 seconds and retained 400,001
codepoints in one cell before the change; after the cap it took 0.184 seconds and
retained 32 codepoints (25.9 times faster for this adversarial input). A 200,000-mark
stream changed from 1.462 to 0.106 seconds. These are local measurements, not a
claim about ordinary application speed. Normal accents and wide cells retain
coverage. Inventory/render measurements are recorded with final verification.

## Distribution and limitations

Supported release targets remain architecture-independent DEB and RPM packages
and an x86_64 host-runtime AppImage. Python/Qt are not bundled in the AppImage.
No Windows, macOS or ARM64 execution is claimed. Chroot sessions run as trusted
host-root development environments, not security sandboxes. A SIGKILL or host
crash during user export may leave a recognizable hidden staging file; incomplete
bytes are never published at the requested destination. Existing archives and
source files are preserved on failure.

## Final verification

- Full local check: **343 tests passed, zero skipped**, Bash/Python syntax checks,
  both demos and DEB smoke build. No remaining local test failures.
- Privileged integration: minimal and Python profiles passed, covering signed
  bootstrap, namespaces, mounts, shared folders, snapshot/restore, ephemeral
  sessions, expansion/repair, export/import and durable records. No test mounts
  remained afterward. The version-tag CI repeats integration on the final commit.
- Fresh inventory median 38.968 ms (budget 80); cached inventory 1.181 ms
  (budget 10); unchanged rendering 0.836 ms (budget 2).
- DEB, RPM and AppImage rebuilt byte-identically across output directories and
  umasks 022/077. All final artifacts passed SHA-256, extracted CLI/capabilities,
  diagnostics, helper and offscreen desktop checks. The AppImage also launched
  directly with --version and --demo --screenshot.
- Distribution sizes: DEB 100,240 bytes; RPM 108,031 bytes; AppImage 1,055,224 bytes.
- Build host: Ubuntu 26.04.1 x86_64, Linux 7.0.0-42-generic, Python 3.14.4,
  PySide6 6.10.2, Bash 5.3.9, dpkg-deb 1.23.7, RPM 6.1.0, glibc 2.43.
  SOURCE_DATE_EPOCH=1793059200; AppImage tool/runtime use repository hash pins.
- Three independent review passes plus final diff/whitespace and secret-pattern
  inspection completed. No known critical regressions remain.
- Publication is pending final tag CI; its result is recorded after publication.
