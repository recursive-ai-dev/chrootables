# Chrootables 1.2.1 engineering TODO

Audit of commit `9cda3dc`, 2026-10-30. Work is ordered from the greatest
potential harm to minor polish. Completion requires implementation and relevant
regression coverage. The previous completed audit is preserved in
`docs/TODO-1.2.0-STABILIZATION-ARCHIVE.md`.

## Critical: data integrity and safe store access

- [x] **1. Preserve interrupted publication evidence.** Discard currently succeeds
  after metadata publication when the staged image has disappeared, leaving old
  image data paired with new metadata. Refuse discard after metadata publication
  regardless of whether the staged image still exists; retain the blocking record.
- [x] **2. Protect the entire resize repair phase.** `e2fsck -f -p` can modify the
  live filesystem before resize enables signal protection. Protect it from CLI
  signals and refuse desktop cancellation throughout this phase.
- [x] **3. Validate every managed store directory.** Root ownership and permissions
  are checked on the store root but not its children, and the archive directory
  can be a symlink. Reject unsafe types, ownership and group/other write access;
  make host diagnostics report the same rules.
- [x] **4. Prevent import-capacity arithmetic overflow.** Extremely large logical
  archive sizes can wrap Bash integer arithmetic and produce a 512 MiB estimate.
  Bound payload/entry counts before arithmetic, reserve ext4's initial inodes,
  and reject unsupported capacity.

## High: blocked workflows and reliable control

- [x] **5. Expose damaged operation records in Recovery.** Strictly validate records
  against the CLI schema, show damaged records as discardable, and make recovery
  eligibility agree with CLI operation status instead of silently hiding blockers.
- [x] **6. Bound helper work and prioritize cancellation.** An unlimited executor
  queue can consume memory and place Cancel behind slow reads. Bound outstanding
  requests, return explicit overload errors and process cancellation promptly.
- [x] **7. Keep malformed terminal colors from breaking rendering.** Mixed scalar
  and colon-form SGR parameters currently raise `TypeError`. Ignore unsupported
  or malformed color forms safely and continue processing subsequent output.
- [x] **8. Validate complete desktop response contracts.** Malformed doctor checks
  reach the UI and cause `KeyError`; optional recovery fields are unchecked.
  Reject invalid nested data before callbacks consume it.
- [x] **9. Preserve normal-user group access when saving exports.** The privileged
  helper discards supplementary groups, preventing saves to shared writable
  folders. Restore the invoking user's groups when dropping privileges.

## Medium: output correctness and release reliability

- [x] **10. Stream every operation-log byte.** A burst larger than 256 KiB jumps
  to the file tail, losing diagnostics. Consume bounded chunks from the previous
  offset and drain remaining output when the operation completes.
- [x] **11. Decode streamed UTF-8 across chunk boundaries.** Split multibyte
  characters become replacement glyphs. Keep an incremental decoder per log,
  including defined truncation and final-flush behavior.
- [x] **12. Preserve terminal pending wrap.** Formatting, queries and incomplete
  escape sequences clear the right-margin wrap state and overwrite the last cell.
  Preserve wrap across non-moving sequences and chunk boundaries.
- [x] **13. Respect terminal scrolling margins.** Insert/delete-line sequences
  outside the scroll region currently remove unrelated rows. Ignore them outside
  the region and preserve surrounding rows inside it.
- [x] **14. Harden build-tool cache publication.** Cache checks follow some unsafe
  file types and downloads can replace a concurrently created destination.
  Reject symlinks/non-regular files, use bounded checksum reads and publish without
  overwriting another writer's file.
- [x] **15. Flush artifacts before publication.** Package and checksum renames
  currently omit file/directory synchronization. Flush staged contents before
  publication and sync the destination directory before reporting success.
- [x] **16. Gate release tags on privileged integration.** CI runs root integration
  only for release branches/manual dispatch, not version tags. Include version-tag
  builds and require successful integration before publishing this release.

## Low: preferences and installation polish

- [x] **17. Scope favorites to their workspace store.** Same-named workspaces in
  different stores share a global favorites list. Use the existing per-store
  preference namespace, migrate legacy default-store favorites and isolate demos.
- [x] **18. Honor XDG data-directory defaults.** Empty or relative `XDG_DATA_HOME`
  currently installs desktop files into the working directory. Use the standard
  home fallback for invalid values and preserve valid absolute destinations.

## Barely a concern: release documentation

- [x] **19. Prepare coherent 1.2.1 release metadata and artifacts.** Update CLI/desktop versions,
  changelog, installation examples, roadmap and manual metadata; build DEB, RPM
  and x86_64 host-runtime AppImage artifacts with SHA256 checksums and verify them.

After these checks, delivery proceeds through a commit and push to `main`,
successful privileged integration on the version tag, and publication of the
GitHub release with the verified artifacts.

## Validation

All 19 implementation and preparation items are complete. The full check command
passed 309 tests with no skips, syntax checks, both demos and all performance
budgets. DEB, RPM and AppImage tests verified reproducibility across directories
and umasks. The final artifacts passed checksum verification and extracted-package
smoke checks. Privileged integration runs in CI before release publication.
