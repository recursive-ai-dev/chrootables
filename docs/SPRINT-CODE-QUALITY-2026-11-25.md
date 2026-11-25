# Chrootables code-quality sprint — 2026-11-25

Baseline: `04edbf8` / v1.3.0 (SURVEY.md). Scope: the P1/P2/P3 code-quality items
from SURVEY.md, executed as pure refactors with no behavior or architecture
change. The Linux ownership boundaries (CLI, privileged broker, Python backend,
Qt presentation) are untouched, and no validation was weakened.

## Result

388 unit tests pass before and after (the 6 skips are environment-specific).
`scripts/check.py` steps were run individually: syntax checks, compileall,
unittest, `scripts/benchmark.py` (fresh inventory 60 ms / p95 64 ms against the
80 ms budget, confirming the `_inventory` decomposition did not regress
performance), demo dashboard and the desktop demo screenshot. Type annotations
(SURVEY P2 #10) remain a deferred, separate effort per the survey's own risk
note.

## P1 — mechanical deduplication

1. **Bash `.meta` parsing (was 3 full + 2 partial copies).** New `meta_load`
   fills a caller's associative array with the known fields only, keeps caller
   defaults for empty values, and carries the legacy `.mode` fallback.
   `copy_metadata`, `image_json`, `image_details`, `workspace_summary` and
   `dashboard` now use it. The per-site defaults (e.g. `origin=none` versus
   `origin=unknown`) stay at the call sites, exactly as before.
2. **Bash image enumeration and stat batches.** New `collect_images`,
   `image_stat_lines` and `store_snapshot_count` helpers replace the repeated
   `*.img` loops and `stat … | mapfile` idioms in `list_images`,
   `workspace_summary`, `dashboard` and `select_chrootable`; the existing
   `snapshot_count` now backs `image_json` and the dashboard per-image count.
3. **backend build_command create/import validation.** Extracted
   `_lifecycle_profile_suite(params)` returning `(mode, profile, suite)`; both
   branches call it.
4. **Qt dialog widget construction.** Module-level `profile_combo()`,
   `suite_combo()` and `lifecycle_combo()` factories, in the style of the
   existing `label`/`button`/`box` helpers, are shared by `CreateDialog` and
   `ImportDialog`.
5. **Long functions decomposed as pure moves.**
   `app.py Window.build_ui` → `build_sidebar`, `build_header`, `build_notice`,
   `build_task_panel`, `build_footer`; `Window.build_detail_card` → session,
   metadata, notes and action section builders. `backend Store._inventory` →
   `_scan_images`, `_scan_snapshots`, `_read_activity` (caching unchanged).
   `terminal VtScreen._apply_csi` → `_csi_dispatch` over `_csi_move`,
   `_csi_erase`, `_csi_scroll`, `_csi_report`; `_apply_sgr` → `_sgr_colon_group`
   and `_sgr_numeric`; `feed` → per-state `_feed_ground`, `_feed_escape`,
   `_feed_csi`, `_feed_string`; `paintEvent` → `_paint_cells`,
   `_paint_selection`, `_paint_cursor`. The embedded export writer program
   moved to the `_EXPORT_WRITER_SCRIPT` module constant. The privileged
   `worker_main` case branches became `worker_create_guest`,
   `worker_export_guest`, `worker_import_guest` and `worker_enter_session`,
   and `doctor` became `doctor_check_host`, `doctor_check_store`,
   `doctor_check_store_directories`, `doctor_check_capacity` and
   `doctor_print_json`.

## P2 — clear, low-risk improvements

6. **Dead code.** Removed the uncalled `clear(layout)` in app.py and the
   unused `Style.replaced()` in terminal.py (verified by search and by the
   suite). `select_workspace(item, _previous=None)` and
   `open_terminal(_checked=False, …)` mark the Qt-arity parameters as
   intentional.
7. **Magic numbers → named constants.** The CLI defines `MIB_BYTES`,
   `SECTOR_BYTES`, `MIN_SIZE_MIB`, `MAX_SIZE_MIB`, `MAX_SIZE_BYTES` and
   `MAX_ARCHIVE_ENTRIES`, and all validation, arithmetic, error, UI and help
   texts derive from them (the `--capabilities` JSON and the usage page keep
   their literal contract text, with a comment tying them to the constants).
   Protocol limits moved to `backend.MAX_REQUEST_BYTES` and
   `backend.MAX_MESSAGE_ID`, used by both client and broker; the broker store
   default references `backend.DEFAULT_STORE`, and the CLI default carries a
   comment tying it to the same value. app.py names `NOTES_MAX_CHARS`,
   `NOTES_PREVIEW_CHARS`, `WORKSPACE_ROW_HEIGHT`, `OUTPUT_BLOCK_LIMIT`,
   `POLL_INTERVAL_MS`, `REFRESH_DELAY_MS` and `CAPTURE_DELAY_MS`; client.py
   names its shutdown grace periods; backend names `READ_CHUNK_BYTES`,
   `LOG_TAIL_POLL_BYTES` and `OPERATION_STATE_MAX_BYTES`.
8. **Broad/swallowed exceptions.** The broker's deliberate top-level guard now
   prints the traceback to stderr (which the stdio protocol never parses), and
   the intentional `except OSError` handlers in `_staged_bytes` and
   `_scan_archives` carry explanatory comments. The non-fatal resolver
   fallback in the CLI's resolv.conf handling is documented as intentional.
9. **Docstrings.** One-line docstrings added across backend validators,
   `Store` and its public methods, `metadata`, `terminal_command`, client
   `valid_message`/`BackendError`/`Client` and its methods, broker `serve`/
   `main`, and the app helpers where the purpose was not already obvious.
10. **Type annotations.** Deferred as a separate, incremental effort, exactly
    as the survey recommends.
11. **CLI ↔ backend drift.** `backend.DEFAULT_SUITE` replaces the three
    hard-coded `"trixie"` literals and is documented: the desktop always sends
    a suite from the advertised capabilities, while the interactive CLI keeps
    the moving `stable` alias so terminal users follow the current stable
    release (that policy is now written down at both sites). `load_capabilities`
    documents which keys it validates and which are informational, and the
    advertised `operation_events` prefix and id-environment name are now
    consumed by `Store.operate` with safe fallbacks to the current contract.
    The capacity error message is derived from the advertised limits instead of
    repeating "512 MiB and 1 TiB" by hand.
12. **Smells.** The long parameter lists (`session_command`/`terminal_command`,
    `ValueDialog`) were left alone: they are validated public API surface, and
    the deep nesting the survey flagged disappears with the decompositions in
    P1 #5. A shared `validate_image_arguments` helper replaces the repeated
    create/import CLI argument checks and their per-site error strings.

## P3 — polish

- The repeated CLI failure texts are centralized (`ERR_SIZE_RANGE`,
  `ERR_LIFECYCLE`, shared profile message) so every entry point states the
  same bounds.
- `chrootables/app.py` restores alphabetical `PySide6.QtCore` import order.
- backend read/log chunk sizes are named constants (see P2 #7).

## Compatibility notes

- CLI, JSON, broker and desktop contracts are unchanged: capabilities,
  `--list --json`, doctor report, operation records, log tailing and export
  delivery behave identically, covered by the unchanged test suite.
- The desktop now falls back to the advertised `operation_events` contract
  values only when an older CLI omits them; today's CLI advertises exactly the
  previously hard-coded strings, so there is no observable change.
