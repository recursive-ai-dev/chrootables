# Chrootables engineering TODO

Audit of the unreleased 1.2.0 tree, written 2026-10-30. Items run from **most damaging to the
app** to **barely a concern**, and are worked in that order. "Verified" means the defect was
reproduced with a probe; "by inspection" means it follows directly from the code. The earlier
list is archived at `docs/TODO-1.2.0-ARCHIVE.md`.

All items are complete. Items 6, 7, 10 and 25 change root-only mount paths, so they are
covered by stubbed unit tests here and by `tests/integration.sh`, which needs root and
runs on the CI `integration` job.

## Critical: core workflows are broken or can damage data

- [x] **1. Embedded terminal cannot authenticate (verified).** `PtyProcess` starts a new session
  but never acquires a controlling terminal, so `sudo` fails with "A terminal is required to
  authenticate". Ctrl+C and SIGWINCH cannot reach the guest either (`tty_nr=0`, `tpgid=-1`).
  Fix: give the session the pty as its controlling terminal.
- [x] **2. Embedded terminal keyboard is unusable (verified).** Tab moves widget focus instead of
  completing in the shell. Initial focus lands on a button, so Enter can trigger "Open in external
  terminal". F-keys, Shift+Tab and Ctrl+symbol send nothing. Arrow keys ignore DECCKM, which
  ncurses apps enable.
- [x] **3. Cancel does not cancel (verified).** SIGTERM kills only the outer shell. The subshell
  and its children finish and publish while the desktop reports failure. Signal the whole process
  group, keep the main shell alive to report the true exit status, and show "Cancelled".
- [x] **4. Interrupting an expansion or repair can corrupt a filesystem (by inspection).** Only
  `publish-*` is protected, so Cancel or Ctrl+C can kill `resize2fs` or `e2fsck -y` mid-write.
  Use an allowlist of cancellable stages and ignore signals during critical sections.
- [x] **5. "Close session" can hang forever (by inspection).** `os.killpg` raises an unhandled
  `PermissionError` against root `sudo`, and a guest ignoring SIGTERM is never escalated. Fall back
  to a pty hangup after a grace period.
- [x] **6. Restricted-device sessions have no `/proc` or `/run` (by inspection).**
  `mount_restricted_devices` never mounts them, so `/dev/fd`, `ps` and process substitution all
  fail. `/dev/shm` is also missing.
- [x] **7. Guest symlinks reach the host during session setup (by inspection).** An absolute
  `/etc/resolv.conf` symlink makes `touch` create a file on the host. The `/run` tmpfs is mounted
  afterwards and hides resolver targets, breaking DNS. `/proc`, `/dev` and `/run` mountpoints also
  follow guest symlinks.
- [x] **8. Re-importing a retained export always fails (by inspection).** The unprivileged desktop
  validates a path inside the root-only store and gets "Archive file is unavailable". Reference
  managed exports by filename and resolve them in the broker.
- [x] **9. Import validation is weaker than documented (verified: 2 failing tests).** Device nodes
  are not rejected despite the changelog claim, `tar -t` failures are ignored, and capacity is never
  checked, so a large archive fails only after a long extraction. Reject unsafe entries and compute
  a rigorous minimum capacity, with automatic sizing when none is given.
- [x] **10. Imported workspaces lose data and report wrong metadata (by inspection).** Extraction
  restores only `user.*` xattrs, dropping file capabilities. `suite` is copied from the environment
  rather than the archive, and foreign-architecture or non-root-filesystem archives are not detected.
- [x] **11. Session teardown can lose guest data (by inspection).** Guest daemons get 50 ms between
  SIGTERM and SIGKILL, so a database in a persistent workspace can be killed mid-write. Wait with
  a bounded, polled grace period.
- [x] **12. Interrupted operations can block a workspace permanently (by inspection).** An
  unrecoverable publication or a damaged record blocks locking with no remedy, and partial images
  are never reclaimed. Add `--discard-operation ID --yes` plus desktop visibility of all interrupted
  work, not only the first recoverable one.
- [x] **13. The release gate is red (verified).** 3 failures and 4 errors in tests shipped with the
  1.2.0 work, written against APIs that do not exist. Align them with the real contracts and add
  the missing behaviour they describe.

## High: reliability and data lifecycle

- [x] **14. Operation records grow forever (by inspection).** Every session and mutation adds a
  state file and a log. `lock_image`, the doctor and each desktop poll scan all of them, so every
  operation slows down over the life of the host. Add automatic retention.
- [x] **15. Offline archive sync races with creates (by inspection).** A concurrent create can read
  an archive being swapped out, and an interrupted swap loses the archive and strands `.old`
  directories. Add a shared/exclusive lock and roll back a failed swap.
- [x] **16. Cancelling the polkit prompt disconnects the app (by inspection).** The read-only helper
  has already been stopped, so the desktop stays stale until the user authenticates. Reconnect
  read-only automatically.
- [x] **17. One non-UTF-8 metadata value breaks the desktop inventory (by inspection).** `--list`
  output is decoded strictly, so a Latin-1 archive name in `origin=` stops the whole library loading.
- [x] **18. Terminal renders full-screen apps incorrectly (by inspection).** DEC line drawing is
  discarded, so whiptail and mc boxes show `lqqqk`. DCS/APC payloads print as text, DSR/DA queries go
  unanswered, and IRM and `?7l` autowrap are ignored.
- [x] **19. Export count merges similarly named workspaces (by inspection).** `foo` also counts
  `foo-bar-*.tar.gz` in `--inspect`.
- [x] **20. Exports are published without being flushed (by inspection).** A crash just after the
  rename can leave a truncated archive under its final name. The desktop can also save a file
  without the `.tar.gz` suffix, which import then refuses.

## Medium: robustness and parity

- [x] **21. Terminal resize discards the cursor line (by inspection).** Shrinking keeps the top rows
  and drops the prompt; those rows should move to scrollback. DECSC should also save attributes.
- [x] **22. Sessions require `sudo` (by inspection).** Hosts with only polkit cannot open a shell.
  Fall back to `pkexec`, preserving `TERM` and `LANG`.
- [x] **23. TUI lacks import and recovery (by inspection).** The standing rule is CLI/desktop/TUI
  parity, but the menu has no import, interrupted-operation handling or log pruning.
- [x] **24. A broken manager gives a raw traceback (by inspection).** The launcher only catches
  `ImportError`, while capability loading raises `RuntimeError` at import time.
- [x] **25. Integration suite skips the new features (by inspection).** `tests/integration.sh` does
  not cover the export/import round trip, restricted `/proc`, or discard.

## Low: polish

- [x] **26. Host prerequisites are incomplete (by inspection).** `grep`, `sort`, `cut`, `touch` and
  `sleep` are used but neither checked nor reported by the doctor.
- [x] **27. Terminal details (by inspection).** Find misaligns highlights when lowercasing changes
  length, colon-form SGR colors are ignored, and the wheel does nothing on the alternate screen.
- [x] **28. Client buffer parsing is quadratic (by inspection).** `Client.read` re-splits the buffer
  per line, which is wasteful on large output bursts.
- [x] **29. Dropping a snapshot leaves empty directories (by inspection).** Empty snapshot folders
  build up in the store after the last recovery point is removed.

## Barely a concern: documentation

- [x] **30. Release documentation (by inspection).** Correct changelog claims and update the man
  pages, README and roadmap for new flags and behaviour.
