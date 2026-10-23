# Code Stabilization Report

## Changes Applied

This pass inspected the Bash CLI, Python stdio broker/backend, PySide6 desktop, terminal implementation, CI, packaging, and existing tests. Pre-existing worktree changes were preserved. The follow-up extends the existing terminal process-group termination logic to handle children surviving their session leader. The historical report in `docs/` was left intact.

| Priority | Location | Defect | Fix | Regression Test |
|---|---|---|---|---|
| P1 | `chrootables/app.py`, `Window.open_terminal` | The external-terminal callback read the live library selection. Selecting another workspace after opening an embedded terminal redirected its external-terminal action to that other workspace. | Capture the original workspace identity and use it for both terminal commands and the window title. | `test_external_terminal_keeps_original_workspace_after_selection_changes` |
| P2 | `chrootables/terminal.py`, `VtScreen.resize` | Resizing allocated rows using the old column count. Widening could cause out-of-range writes; narrowing left stale cells outside the declared screen. | Allocate every row at the requested width and preserve only cells within the new bounds. | `test_resize_updates_every_row_width_and_accepts_output` exercises widening, narrowing, added rows, and writing at the new bottom-right corner. |
| P2 | `chrootables/terminal.py`, `VtScreen.resize` | Resizing during an alternate-screen session left the saved primary screen and scroll region at their old dimensions. Returning from a full-screen program restored inconsistent state. | Resize the saved primary buffer too, constrain its cursor, and reset its scroll region to the resized screen. | `test_resize_alternate_screen_also_resizes_saved_primary` covers growth, shrinkage, content retention, and scrolling after restoring the primary screen. |
| P2 | `chrootables/terminal.py`, `PtyProcess.__init__`; `chrootables/app.py`, `Window.open_terminal` | PTY setup or process launch failures leaked both descriptors and propagated uncaught from the desktop action. | Close both descriptors on failed initialization, preserve the exception, and display launch errors without registering a terminal window. | `test_failed_start_closes_both_pty_descriptors` covers ioctl and spawn failures; `test_terminal_launch_failure_is_reported_without_registering_window` checks desktop error reporting. |

| P2 | `chrootables/terminal.py`, PTY EOF and shutdown | EOF used a blocking wait on the GUI thread; closed descriptor numbers remained available to input/resize; window dismissal could discard an unreaped child. | Poll child exit using a Qt timer after EOF, invalidate the descriptor, flush the UTF-8 decoder, and emit completion once. Keep terminal windows owned until output ends and the child is reaped, including Escape dismissal. | `test_eof_reaps_without_waiting_and_invalidates_descriptor`; `test_close_and_escape_keep_window_owned_until_child_is_reaped`; existing round-trip and close tests. |
| P2 | `chrootables/terminal.py`, `PtyProcess.terminate` | A session leader could exit while its children retained the PTY. Checking only leader liveness prevented those children from receiving the close signal. | Signal the private process group while session completion is pending, even after the leader exits. | `test_close_terminates_children_after_session_leader_exits`. |
| P2 | `chrootables/client.py` | Malformed helper envelopes/payloads could raise inside Qt callbacks; invalid JSON and helper error events were silently ignored, leaving pending requests unresolved. | Validate message fields and result data before dispatch; surface protocol errors and reject pending requests. Validate request serialization and handle failed writes. | `tests/test_client.py`: malformed envelopes and data, invalid JSON, explicit helper errors, partial messages, serialization errors, failed writes. |
| P2 | `chrootables/client.py` | Duplicate error/finished signals repeated failure handling, and callbacks that reconnected could allow remaining old messages to alter the new connection. | Fail each connection once; reject late responses after failure; recheck process identity after callback/signal dispatch. | `test_disconnect_fails_all_pending_requests_once`; `test_reconnection_from_callback_ignores_remaining_old_messages`. |
| P3 | `tests/test_terminal.py` | The pane test left a fixture reader open and dropped its process before asynchronous cleanup completed. | Use a managed file read and wait for PTY completion before releasing the fixture. | Full suite completes without the previous ResourceWarnings. |
| P3 | `scripts/check.py`, `README.md`, `.github/workflows/ci.yml` | Local checks were separate commands, the pinned Python compatibility range was undocumented, and optional launcher/package integrations could be skipped in CI. | Add one check command for syntax, tests, demos, and available packaging; document Python 3.10–3.14 for pinned dependencies. CI installs desktop integration dependencies and requires those checks; Debian also requires the package build. | Local strict check command ran successfully with Gio, desktop-file-validate, and dpkg-deb. Remote CI jobs were not executed in this session. |
| P3 | `tests/test_backend.py` | Export cleanup and transient inventory/helper failures lacked focused failure-injection coverage. | Add tests for partial export writes failing with ENOSPC or interruption, snapshot disappearance during scanning, cache publication after failed inventory, and subprocess launch errors reaching the client while the broker continues serving. Existing behavior passed; no backend production changes were needed. | `test_failed_export_copy_removes_partial_destination_and_retains_source`; `test_snapshot_disappearing_during_scan_is_reported_and_next_refresh_recovers`; `test_inventory_failure_does_not_publish_a_new_cache_signature`; `test_broker_reports_spawn_failure_and_keeps_serving`. |

## Verification Results

Commands ran from the repository root. `PY` below denotes `/tmp/chrootables-stabilization-venv/bin/python`, a temporary Python 3.14 environment using the exact pinned requirements. Logs are in `/tmp/chrootables-{baseline,regression-before,focused,full}.log`.

| Command | Result | Scope |
|---|---|---|
| `QT_QPA_PLATFORM=offscreen /usr/bin/python3 -m unittest discover -s tests -p 'test_*.py'` | Failed: 61 tests/load attempts, two import errors because PySide6 was absent. | Initial baseline. |
| `.venv/bin/python -c 'import PySide6; print(PySide6.__version__)'` | Failed: PySide6 absent. | Existing virtual environment probe. |
| `.venv/bin/python -m pip install -r requirements.txt` | Failed: existing environment uses Python 3.15; pinned Qt packages require Python below 3.15. | Dependency setup; requirements unchanged. |
| `uv venv --python /usr/bin/python3 /tmp/chrootables-stabilization-venv` | Passed. | Isolated verification environment. |
| `uv pip install --python /tmp/chrootables-stabilization-venv/bin/python -r requirements.txt` | Passed: PySide6-Essentials and shiboken6 6.11.2 installed. | Pinned dependencies. |
| `QT_QPA_PLATFORM=offscreen "$PY" -m unittest tests.test_terminal.VtScreenTests.test_resize_updates_every_row_width_and_accepts_output tests.test_terminal.VtScreenTests.test_resize_alternate_screen_also_resizes_saved_primary tests.test_terminal.PtyProcessTests.test_failed_start_closes_both_pty_descriptors tests.test_desktop.DesktopTests.test_external_terminal_keeps_original_workspace_after_selection_changes tests.test_desktop.DesktopTests.test_terminal_launch_failure_is_reported_without_registering_window` | Before fixes: five tests reproduced six subtest/test failures and one error. | Demonstrates all new regressions against the original implementation. |
| `QT_QPA_PLATFORM=offscreen "$PY" -m unittest tests.test_terminal tests.test_desktop` | Passed: 48 tests. | Focused verification after fixes. |
| `QT_QPA_PLATFORM=offscreen "$PY" -m unittest discover -s tests -p 'test_*.py' -v` | Passed: 107 tests total, 106 passed, one skipped. | Full suite; the Gio desktop launcher integration was skipped because PyGObject is unavailable. |
| `bash -n chrootable-manager tests/integration.sh` | Passed for manager syntax; Bash treats the second filename as an argument. | Manager syntax. |
| `bash -n tests/integration.sh` | Passed. | Integration script syntax, separately verified. |
| `"$PY" -m compileall -q chrootables chrootables-desktop scripts tests` | Passed. | Python modules, scripts, and tests. |
| `"$PY" -m py_compile chrootables-desktop` | Passed. | Explicit check of extensionless launcher. |
| `/usr/bin/python3 scripts/build-deb.py --output /tmp/chrootables-stabilization-build` | Passed; produced `chrootables_3.1.0-1_all.deb`. | Full available package build, without overwriting repository build artifacts. |
| `./chrootable-manager --demo --dashboard` | Passed. | CLI demo inventory and dashboard. |
| `QT_QPA_PLATFORM=offscreen "$PY" ./chrootables-desktop --demo --screenshot /tmp/chrootables-stabilization.png` | Passed, exit 0; nonempty PNG verified. | Desktop startup and sample-data rendering. |
| `git diff --check` | Passed. | Worktree whitespace validation. |

The focused and full suites emitted ResourceWarnings from the existing terminal pane test (an unclosed fixture reader and a process still running when its object was destroyed), plus the offscreen Qt plugin's size-hint notice. These were not suppressed. No lint or static type-check configuration exists; `shellcheck`, `ruff`, and `mypy` were unavailable, so no lint/type-check success is claimed.

The root-only integration suite was not run: this session is unprivileged, and that suite requires Debian downloads, loop mounts, and namespaces. Real privileged image operations remain unverified by this pass.

### Follow-up verification

The temporary Python 3.14 environment was given access to system site packages to use the installed PyGObject bindings. Its pinned Qt dependencies remain unchanged. No repository virtual environment or system packages were modified.

| Command | Result | Scope |
|---|---|---|
| `QT_QPA_PLATFORM=offscreen "$PY" -m unittest tests.test_terminal tests.test_desktop -v` | Passed: 50 tests at that point. | Initial lifecycle changes and existing desktop behavior; no ResourceWarnings. |
| `QT_QPA_PLATFORM=offscreen "$PY" -m unittest tests.test_client -v` | Passed: seven tests. | Protocol validation, failures, buffering, and reconnection. |
| `QT_QPA_PLATFORM=offscreen "$PY" -m unittest tests.test_backend -v` | Passed: 35 tests. | Existing backend behavior plus injected export/inventory/spawn failures. |
| `"$PY" scripts/check.py --build-deb` | Passed: 120 tests total, one Gio integration skipped before enabling system site packages; syntax, demos, and package build passed. | First complete run of the new check command. |
| `"$PY" scripts/check.py --require-desktop-integration --build-deb` | **Passed: all 121 tests, no skips or ResourceWarnings.** Both Bash syntax checks, Python compilation, CLI demo, desktop screenshot, and Debian package build passed. | Final complete run, including the session-leader-exits regression and real Gio launcher integration. Log: `/tmp/chrootables-followup-strict.log`. Temporary build/screenshot artifacts were removed by the check command. |
| `git diff --check` | Passed. | Final whitespace validation. |

The earlier missing-Gio skip and terminal test cleanup warnings above describe the initial pass; they are resolved in the final verification. The Qt offscreen size-hint notice remains benign. No remote Fedora/Debian CI run, privileged mount integration, lint, or static type-check success is claimed.

## Deferred Risks

The previously deferred PTY EOF/reaping and window-ownership defects are fixed and regression-tested. No additional high-confidence defect remains deferred from this pass. Terminal closure retains cooperative SIGTERM behavior: a command that ignores termination can keep the terminal open until it exits; this pass does not introduce forced killing during filesystem cleanup. Verification limitations are listed above.
