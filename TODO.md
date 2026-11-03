# Chrootables 1.2.2 engineering TODO

Audit baseline: `8c0a816`, 2026-11-03. Prior completed sprint: `docs/TODO-1.2.1-ARCHIVE.md`.

Findings follow traced execution paths, with security and data integrity first, then reliability and release correctness. Priority considers harm, likelihood, scope and dependencies. Status transitions: PENDING → IN_PROGRESS → VERIFIED; BLOCKED requires an external dependency.

## SPR-01 — CLI import

- Priority / severity: **P1 / High**
- Status: **VERIFIED**
- Problem / evidence: The archive is validated by pathname and later reopened after image allocation; a user can replace or modify it between validation and extraction.
- Implementation: Copy into private operation storage before validation and use only that copy, with deterministic cleanup.
- Acceptance: Replacing the original archive after validation cannot change imported data; failures remove staging.
- Required tests: Real tar input, injected replacement, copy failure and cleanup.
- Dependencies: None
- Verification evidence: 91 CLI and 70 backend tests passed. Coverage includes mutable archive replacement, special-file substitution, copy/validation faults, abrupt termination/discard, guest-link newline and executable validation, nested snapshot mode/ownership checks, malformed record inventory and store-path normalization. bash -n and diff whitespace checks passed. Final aggregate regression: 343 tests passed, zero skipped.

## SPR-02 — CLI snapshots/recovery

- Priority / severity: **P1 / High**
- Status: **VERIFIED**
- Problem / evidence: Top-level store permissions are checked, but snapshots/<workspace> ownership and write permissions are not.
- Implementation: Apply store-directory rules to nested snapshot paths before use.
- Acceptance: Unsafe ownership/mode/type fails before snapshot mutation or restore.
- Required tests: Unsafe nested directories, normal save/restore, recovery.
- Dependencies: None
- Verification evidence: 91 CLI and 70 backend tests passed. Coverage includes mutable archive replacement, special-file substitution, copy/validation faults, abrupt termination/discard, guest-link newline and executable validation, nested snapshot mode/ownership checks, malformed record inventory and store-path normalization. bash -n and diff whitespace checks passed. Final aggregate regression: 343 tests passed, zero skipped.

## SPR-03 — CLI import worker

- Priority / severity: **P1 / High**
- Status: **VERIFIED**
- Problem / evidence: An accepted FIFO at etc/hostname blocks the privileged hostname write indefinitely.
- Implementation: Replace hostname safely without opening a guest special file.
- Acceptance: FIFO and directory inputs terminate safely; normal hostname is correct.
- Required tests: FIFO, symlink, directory and regular hostname cases.
- Dependencies: SPR-01
- Verification evidence: 91 CLI and 70 backend tests passed. Coverage includes mutable archive replacement, special-file substitution, copy/validation faults, abrupt termination/discard, guest-link newline and executable validation, nested snapshot mode/ownership checks, malformed record inventory and store-path normalization. bash -n and diff whitespace checks passed. Final aggregate regression: 343 tests passed, zero skipped.

## SPR-04 — CLI import worker

- Priority / severity: **P2 / Medium**
- Status: **VERIFIED**
- Problem / evidence: The /usr/bin/env check follows host path semantics and accepts dangling symlinks.
- Implementation: Resolve required executable inside the guest and validate the resulting regular file.
- Acceptance: Missing/dangling/escaping executables cannot publish an import.
- Required tests: Guest absolute/relative links, broken chains and nonregular targets.
- Dependencies: SPR-01
- Verification evidence: 91 CLI and 70 backend tests passed. Coverage includes mutable archive replacement, special-file substitution, copy/validation faults, abrupt termination/discard, guest-link newline and executable validation, nested snapshot mode/ownership checks, malformed record inventory and store-path normalization. bash -n and diff whitespace checks passed. Final aggregate regression: 343 tests passed, zero skipped.

## SPR-05 — CLI operation recovery

- Priority / severity: **P2 / Medium**
- Status: **VERIFIED**
- Problem / evidence: list_operations skips damaged records while lock acquisition treats those records as blockers.
- Implementation: Expose damaged operation records with explicit discard-only recovery status.
- Acceptance: Every blocking damaged record appears in text/JSON recovery inventory; records with invalid filenames explicitly require manual repair.
- Required tests: Malformed records, JSON validity, discard and compatibility with valid records.
- Dependencies: None
- Verification evidence: 91 CLI and 70 backend tests passed. Coverage includes mutable archive replacement, special-file substitution, copy/validation faults, abrupt termination/discard, guest-link newline and executable validation, nested snapshot mode/ownership checks, malformed record inventory and store-path normalization. bash -n and diff whitespace checks passed. Final aggregate regression: 343 tests passed, zero skipped.

## SPR-06 — CLI/backend store configuration

- Priority / severity: **P2 / Medium**
- Status: **VERIFIED**
- Problem / evidence: Store paths can contain tabs/newlines that break activity.tsv; unresolved .. and ancestor symlinks disagree with canonical CLI export paths and can resolve to the filesystem root.
- Implementation: Reject control delimiters consistently and canonicalize backend store paths while rejecting root and final symlinks.
- Acceptance: Invalid paths fail before store creation; ordinary spaced/Unicode paths work.
- Required tests: CLI and backend control-character path rejection.
- Dependencies: None
- Verification evidence: 91 CLI and 70 backend tests passed. Coverage includes mutable archive replacement, special-file substitution, copy/validation faults, abrupt termination/discard, guest-link newline and executable validation, nested snapshot mode/ownership checks, malformed record inventory and store-path normalization. bash -n and diff whitespace checks passed. Final aggregate regression: 343 tests passed, zero skipped.

## SPR-15 — Export delivery

- Priority / severity: **P2 / Medium**
- Status: **VERIFIED**
- Problem / evidence: deliver_export writes directly to the final destination; interruption exposes an incomplete archive under its requested name.
- Implementation: Write and fsync a private sibling, then publish with Linux no-replace rename (hard-link fallback) and sync the directory.
- Acceptance: Final filename appears only for a complete archive; existing competing files survive.
- Required tests: Write/fsync/link failures, competing destination, permissions and successful content.
- Dependencies: None
- Verification evidence: 68 backend and 14 client tests passed, including native/fallback atomic publication, competing destination, ENOSPC, interruption, fsync failure, abrupt writer death, FIFO input, byte-limit boundary and unrelated callback preservation. Independent review found and corrected missing cancellation stages for import.

## SPR-16 — Desktop request framing

- Priority / severity: **P2 / Medium**
- Status: **VERIFIED**
- Problem / evidence: Client sends oversized requests that exceed the broker 64 KiB line bound and disconnect unrelated work.
- Implementation: Reject encoded oversized requests locally before callback registration or transport writes.
- Acceptance: Only the oversized request fails; connection and other pending callbacks remain usable.
- Required tests: Boundary lengths, multibyte inputs and concurrent pending callback preservation.
- Dependencies: None
- Verification evidence: 68 backend and 14 client tests passed, including native/fallback atomic publication, competing destination, ENOSPC, interruption, fsync failure, abrupt writer death, FIFO input, byte-limit boundary and unrelated callback preservation. Independent review found and corrected missing cancellation stages for import.

## SPR-07 — Terminal screen

- Priority / severity: **P2 / Medium**
- Status: **VERIFIED**
- Problem / evidence: Combining characters append without limit to a single cell despite bounded screen/scrollback size.
- Implementation: Bound combining text per cell while preserving normal accents.
- Acceptance: Adversarial combining streams have bounded cell storage and rendering continues.
- Required tests: Long combining streams, ordinary combining accents and wide cells.
- Dependencies: None
- Verification evidence: 81 terminal tests and 46 desktop tests passed; randomized stream seed 20261103, cancellation across split boundaries, blocked PTY and DSR floods, real child TERM checks. 400,000 combining marks: 4.760 s / 400,001 codepoints before, 0.184 s / 32 codepoints after. Independent review and final 343-test regression passed.

## SPR-08 — Terminal parser

- Priority / severity: **P2 / Medium**
- Status: **VERIFIED**
- Problem / evidence: CAN/SUB inside OSC leaves the parser swallowing future output; cancellation handling differs across escape states.
- Implementation: Handle cancellation consistently in string and charset states.
- Acceptance: Output resumes immediately after cancelled sequences, including fragmented feeds.
- Required tests: OSC/OSC_ESC/STRING_ESC/CHARSET cancellation and split boundaries.
- Dependencies: None
- Verification evidence: 81 terminal tests and 46 desktop tests passed; randomized stream seed 20261103, cancellation across split boundaries, blocked PTY and DSR floods, real child TERM checks. 400,000 combining marks: 4.760 s / 400,001 codepoints before, 0.184 s / 32 codepoints after. Independent review and final 343-test regression passed.

## SPR-09 — PTY input queue

- Priority / severity: **P2 / Medium**
- Status: **VERIFIED**
- Problem / evidence: Blocked PTY writes allow repeated pastes or terminal replies to grow the input queue without bound.
- Implementation: Bound queued input; reject entire excess writes and report rejected user input.
- Acceptance: Queue remains bounded with a nonreading child; accepted bytes retain order.
- Required tests: Blocked writes, repeated paste/replies, recovery and bracketed-paste integrity.
- Dependencies: None
- Verification evidence: 81 terminal tests and 46 desktop tests passed; randomized stream seed 20261103, cancellation across split boundaries, blocked PTY and DSR floods, real child TERM checks. 400,000 combining marks: 4.760 s / 400,001 codepoints before, 0.184 s / 32 codepoints after. Independent review and final 343-test regression passed.

## SPR-10 — PTY environment

- Priority / severity: **P3 / Low**
- Status: **VERIFIED**
- Problem / evidence: Inherited TERM=dumb or a parent-specific TERM is passed to the embedded emulator.
- Implementation: Default TERM to the emulator contract while honoring an explicit caller override.
- Acceptance: Child sees xterm-256color regardless of unrelated parent TERM.
- Required tests: Inherited TERM and explicit override.
- Dependencies: None
- Verification evidence: 81 terminal tests and 46 desktop tests passed; randomized stream seed 20261103, cancellation across split boundaries, blocked PTY and DSR floods, real child TERM checks. 400,000 combining marks: 4.760 s / 400,001 codepoints before, 0.184 s / 32 codepoints after. Independent review and final 343-test regression passed.

## SPR-11 — Package publication

- Priority / severity: **P3 / Low**
- Status: **VERIFIED**
- Problem / evidence: Existing checksum sidecars are read completely into memory although their expected content is small.
- Implementation: Compare size then read a bounded checksum payload.
- Acceptance: Oversized sidecars fail without a large read or replacing either output.
- Required tests: Sparse oversized sidecar and forced/normal publication.
- Dependencies: None
- Verification evidence: `tests/test_packages.py`: 18 tests passed, including real DEB/RPM/AppImage contract coverage, sparse 1 TiB sidecar rejection, identical executable-mode repair, and timestamp boundary cases (2026-11-03).

## SPR-12 — Package publication

- Priority / severity: **P3 / Low**
- Status: **VERIFIED**
- Problem / evidence: An identical AppImage with lost executable permission is accepted without restoring executable mode.
- Implementation: Normalize final artifact permissions even on an identical rebuild.
- Acceptance: Successful AppImage publication leaves an executable artifact.
- Required tests: Identical existing artifact with mode 0644, checksum unchanged.
- Dependencies: None
- Verification evidence: `tests/test_packages.py`: 18 tests passed, including real DEB/RPM/AppImage contract coverage, sparse 1 TiB sidecar rejection, identical executable-mode repair, and timestamp boundary cases (2026-11-03).

## SPR-13 — Build metadata

- Priority / severity: **P3 / Low**
- Status: **VERIFIED**
- Problem / evidence: SOURCE_DATE_EPOCH=4294967296 passes metadata validation but fails in SquashFS after staging.
- Implementation: Validate the timestamp against the shared distribution format range before staging.
- Acceptance: All builders reject out-of-range timestamps cleanly before output creation.
- Required tests: Boundary values 0, 4294967295 and 4294967296; malformed integers.
- Dependencies: None
- Verification evidence: `tests/test_packages.py`: 18 tests passed, including real DEB/RPM/AppImage contract coverage, sparse 1 TiB sidecar rejection, identical executable-mode repair, and timestamp boundary cases (2026-11-03).

## SPR-14 — Release verification/documentation

- Priority / severity: **P2 / Medium**
- Status: **VERIFIED**
- Problem / evidence: The next release needs updated metadata, complete regression evidence and distribution artifacts from the changed source.
- Implementation: Prepare 1.2.2, run regression/privileged integration, independent diff review, build reproducibly, checksum, commit/push main and publish with gh.
- Acceptance: Release contains verified DEB/RPM/x86_64 AppImage and checksums; limitations and exact commit are recorded.
- Required tests: Full check suite, package smoke/reproducibility, root integration and remote release asset verification.
- Dependencies: SPR-01 through SPR-13, SPR-15, SPR-16
- Verification evidence: all local checks passed: 343 tests, both privileged integration profiles, independent audit, reproducible DEB/RPM/AppImage builds, SHA-256 and extracted/direct startup checks. Release commit `7ad336042a7a15007646655b11eb4937723028ed` pushed to main; all four [tag CI jobs](https://github.com/recursive-ai-dev/chrootables/actions/runs/41713867116) passed. [v1.2.2](https://github.com/recursive-ai-dev/chrootables/releases/tag/v1.2.2) published with eight assets, downloaded again and matched byte-for-byte against local verified files.

## Baseline verification

Initial local check: 191 discovered tests, 2 import errors because system PySide6.QtTest was absent, 2 AppImage checks skipped because tool paths were unset. These were environment gaps, not application regressions. After installing the missing test binding and fetching the repository-checksummed tools, the initial full check passed 309 tests with no skips, both demos and all benchmark budgets. The final expanded-suite results are recorded below.

## Final local verification

343 tests passed with no skips. Both privileged integration profiles passed.
Syntax checks, demos, benchmark budgets, three-format reproducibility and package
startup passed. See `docs/SPRINT-1.2.2.md` for measurements and environment.
All 16 improvements are VERIFIED. No blocked or incomplete sprint tasks remain.
Release artifacts and checksums were independently downloaded and verified after publication.
