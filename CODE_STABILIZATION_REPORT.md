# Code Stabilization Report

Reviewed the Python/PySide6 desktop, stdio helper, Bash image manager, desktop installer, and existing tests. Preserved the uncommitted changes present when this pass began. The changes below are limited to this stabilization pass.

## Changes Applied

| Priority | Location | Defect | Fix | Regression Test |
|---|---|---|---|---|
| P1 | `chrootable-manager`: `resize_image` | Capacity was checked before acquiring the image lock. Another completed expansion could make the requested size smaller than the current image, causing destructive truncation. | Acquire the lock and check offline state before reading capacity and validating expansion. | `tests/test_cli_stability.py`: `test_resize_rechecks_capacity_after_acquiring_image_lock` simulates a competing expansion and verifies no shrink or filesystem utility runs. |
| P1 | `chrootable-manager`: six metadata readers | A final metadata record without a newline was discarded. An ephemeral lifecycle could consequently become persistent. | Process a populated final record even when `read` reaches EOF. | `test_unterminated_metadata_record_preserves_ephemeral_lifecycle` covers metadata lookup, lifecycle, JSON inventory, copying, details, summary, and dashboard. |
| P1 | `chrootables/app.py`: snapshot, clone, resize, repair, export | Background refresh during a modal dialog could change the selected workspace and apply the confirmed action to another image. | Capture the original workspace before entering the dialog's event loop. | `tests/test_desktop.py`: `test_workspace_actions_keep_target_while_dialog_processes_refresh` covers all five actions. |
| P1 | `chrootables/app.py`: `render`, `fill_table` | Recovery and activity selections followed row numbers when refreshed data moved or removed records. A different recovery point or log could become selected. | Preserve selection using workspace/snapshot or activity/log identity; clear selection when that record disappears. Include identity in render caching. | `test_recovery_selection_follows_snapshot_across_refresh`; `test_activity_selection_follows_log_across_refresh`. |
| P2 | `chrootable-manager`: `list_images`, `list_snapshots`, `workspace_summary`, `dashboard` | Process substitution hid batched `stat` failures. If an image disappeared during enumeration, later measurements could be assigned to the wrong image. | Capture and check `stat`'s exit status before mapping its output to files. | `tests/test_cli_stability.py`: `test_inventory_propagates_partial_stat_failure` covers all four callers. |
| P2 | `chrootable-manager`: `finish_operation` | Generated resolver files prevented temporary operation directories from being removed. | Remove the known resolver artifacts before removing the work directories, preserving the operation's exit status. | `test_operation_cleanup_removes_resolver_artifacts_and_preserves_status` covers success and failure. |
| P2 | `chrootables/backend.py`: `Store.inventory` | Concurrent broker requests shared caches whose signatures and values were published separately, allowing inconsistent cache state. | Serialize inventory reads with a per-store lock spanning cache lookup and publication. | `tests/test_backend.py`: `test_concurrent_inventory_requests_share_one_cache_refresh`. |
| P2 | `chrootables/backend.py`: `_probe_lock_status` | Cached inventory opened a FIFO lock file in blocking mode before checking its type, hanging refresh requests. | Open nonblocking and retain the existing regular-file check, matching the CLI's handling of nonregular lock paths. | `test_cached_inventory_does_not_block_on_fifo_lock` exercises two real inventory reads in a subprocess with a timeout. |
| P2 | `chrootables/backend.py`: bounded reads and operation log tailing | If a log grew between `stat` and reading, the reader emitted the new bytes but retained the older offset, duplicating output on the next poll. | Track the byte position actually consumed. Keep the existing bounded, symlink-resistant reads and publish the observed signature only after a successful read. | `test_operation_log_append_during_read_is_streamed_once` appends exactly between the stat and open. |
| P2 | `chrootables/app.py`: operation completion | Failed operations could leave detail controls disabled when unchanged inventory bypassed rendering. | Update workspace detail controls immediately after clearing the busy state. | `tests/test_desktop.py`: `test_failed_operation_restores_controls_without_inventory_changes`. |
| P2 | `chrootables/client.py`: `Client.stop` | Pending callbacks ran after the process was cleared but while the client still reported ready. Follow-up requests could call `write` on `None`. | Clear readiness and write access before process teardown and callback dispatch. | `test_client_stop_rejects_requests_from_cancelled_callbacks`. |
| P2 | `scripts/install-desktop.py`: `desktop_quote` | Special characters in checkout paths produced invalid desktop entries because argument quoting omitted the Desktop Entry value escaping layer. | Escape the stored value after quoting Exec arguments. | `tests/test_install_desktop.py`: validates a generated entry and launches both main and Tour actions from a temporary checkout containing quotes, dollar signs, backticks, backslashes, percent signs, newline, and tab. |

## Verification Results

All **69 tests passed**, with no skips, in the final full run. This includes **14 new regression test methods**. Tests use temporary fixtures; no real workspace was mutated and no actual menu installation was performed.

Repeated commands are grouped below, with their observed outcomes.

| Command | Result | Relevant scope |
|---|---|---|
| `python3 -m unittest discover -s tests -p 'test_*.py' -v` | Baseline: 55 passed. Final: 69 passed in 2.501 seconds. | Entire unittest suite, including real CLI inventory, asynchronous helper, Qt callbacks, shared-folder fixtures, and installer integrations. |
| `python3 -m unittest discover -s tests -p test_backend.py -v` | New regressions first reproduced two failures and one timeout error across 28 tests. After fixes: 28 passed; independent review rerun also passed. | Cache concurrency, FIFO handling, log append race, and existing backend behavior. |
| `python3 -m unittest discover -s tests -p test_cli_stability.py -v` | Before fixes: 14 failing subcases across four methods. After fixes: all four passed. | Resize race, partial stat failures, unterminated metadata, and cleanup. |
| `python3 -m unittest discover -s tests -p test_desktop.py -v` | Baseline: 18 passed. New regressions reproduced seven failures and one error across 23 tests. An intermediate edit also caused an import syntax error, which was corrected. Final: 23 passed. | Modal target identity, refreshed table selection, control state, disconnect callbacks, and existing desktop behavior. |
| `python3 -m unittest discover -s tests -p test_shared_folder.py -v` | 12 passed. | Existing argument, mount, cleanup, and command-failure fixtures; no real mounts. |
| `python3 -m unittest discover -s tests -p 'test_install_desktop.py' -v` | Failed before the escaping fix; both tests passed afterward. | `desktop-file-validate` and actual Gio launches of temporary dummy main/Tour launchers. Both optional integrations were available and ran. |
| `python3 -m compileall -q chrootables chrootables-desktop scripts tests` | Passed. | Python package, scripts, and tests syntax. |
| `python3 -m py_compile chrootables/broker.py chrootables-desktop scripts/install-desktop.py tests/test_install_desktop.py` | Passed. | Explicit compilation of the extensionless desktop launcher, broker, installer, and installer tests. |
| `bash -n chrootable-manager` | Passed. | Bash manager syntax. |
| `bash -n tests/integration.sh` | Passed. | Integration script syntax only. |
| `bash -n chrootable-manager tests/integration.sh` | Passed. | Manager syntax; the integration script was checked separately above because Bash treats subsequent filenames as arguments. |
| `./chrootable-manager --demo --dashboard` | Passed. | CLI demo inventory and dashboard output. |
| `QT_QPA_PLATFORM=offscreen ./chrootables-desktop --demo --screenshot /tmp/chrootables-stabilization.png` | Passed; screenshot created and visually inspected. | Desktop startup and rendering with sample data. |
| `git diff --check -- chrootable-manager tests/test_cli_stability.py` | Passed. | CLI edits. |
| `git diff --check -- chrootables/app.py chrootables/client.py tests/test_desktop.py` | Passed. | Desktop/client edits. |
| `git diff --check -- scripts/install-desktop.py` | Passed. | Installer edit. |
| `git diff --check -- chrootable-manager chrootables/app.py chrootables/client.py chrootables/backend.py scripts/install-desktop.py tests CODE_STABILIZATION_REPORT.md` | Passed. | Files changed by this pass. |
| `git diff --check` | Exit 2: `requirements.txt:13: new blank line at EOF`. | Pre-existing whitespace in the user's dependency edits; preserved. |

The repository has no configured build, lint, or static type-check command. ShellCheck, Ruff, and mypy were unavailable; no lint or type-check success is claimed. Python and Bash syntax checks and the documented desktop launch were used where applicable.

The opt-in `sudo tests/integration.sh` was **not run**: it requires administrator access, Debian downloads, loop devices, and real mounts. Privileged filesystem behavior remains unverified by this pass; the resize and cleanup regressions use controlled command fixtures.

## Deferred Risks

No additional high-confidence defect identified in this pass required a breaking change or remained deferred. Verification limitations are listed above.
