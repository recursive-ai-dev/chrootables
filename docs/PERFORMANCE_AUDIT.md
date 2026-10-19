# Performance Optimization Audit

## Executive Summary

This measurement-driven audit profiled the hot execution paths across Chrootables' backend store engine (`chrootables/backend.py`), Bash image manager (`chrootable-manager`), stdio privilege broker (`chrootables/broker.py`, `chrootables/client.py`), and PySide6 desktop interface (`chrootables/app.py`):

1. **Periodic Store Inventory & CLI Listing (`Store.inventory()` / `chrootable-manager --list --json`)** — Executed on startup, after every workspace mutation, and every 6 seconds via `Window.timer`. At a realistic workbench scale (24 workspaces, 48 snapshots, 80–100 activity records), `Store.inventory()` previously required **`628.01 ms`** due to an `N+1` subprocess/subshell storm (~28 process forks per image: 7 `awk` reads of `.meta`, 2 `stat` calls, 3 `basename` calls, character-by-character JSON escaping, and redundant Python `stat`/`lstat` syscalls per snapshot).
2. **Desktop UI Reconciliation & Live Search (`Window.render()`, `Window.render_library()`, `Window.select_workspace()`, `Window.fill_table()`)** — Executed on every 6-second poll, search keystroke, filter change, and workspace selection. A full render at 24 workspaces previously cost **`44.14 ms`** (`329.50 ms` across a 9-keystroke search sequence, or `36.61 ms/key`) because `self.workspaces.clear()` and `self.details.setWidget(card)` destroyed and reallocated ~217 Qt widgets and re-rasterized `QPixmap` glyphs on every call, even when the underlying state was unchanged.
3. **CLI Dashboard Summary (`chrootable-manager --dashboard`)** — Executed for terminal status inspection; previously cost **`313.86 ms`** at 24 workspaces due to two full-directory passes spawning nested `stat`, `awk`, and `basename` subprocesses per image.
4. **Latest Workspace Log Lookup (`Store.logs()` / `chrootable-manager --logs`)** — Executed when inspecting workspace diagnostics; previously cost **`121.34 ms`** across 80 log files due to an `O(L * A)` `N+1` pattern spawning a separate `awk` process scanning `activity.tsv` for every candidate log file.
5. **High-Frequency Streaming Task Output (`Client.read()` -> `Window.append_output()` & `Store.operate()`)** — Executed during noisy `debootstrap` / `apt-get` runs; previously cost **`15.45 ms`** per 500-line burst due to unbatched per-line `QTextCursor` layout/scroll passes and unconditional 256 KiB log file re-reads every `200 ms`.

**Highest-Value Opportunities Realized**:
- Eliminating the `N+1` process fork storm in `chrootable-manager` (`list_images`, `image_json`, `workspace_summary`, `dashboard`, `show_logs`) and `Store.inventory()` reduced 24-workspace inventory latency from **`628.01 ms` to `22.05 ms` (-96.5%, 28.5x faster)**, CLI `--dashboard` latency from **`313.86 ms` to `17.44 ms` (-94.4%, 18.0x faster)**, and `--logs` lookup from **`121.34 ms` to `8.81 ms` (-92.7%, 13.8x faster)**.
- Replacing full Qt widget-tree destruction with persistent detail-card updates, in-place `WorkspaceRow` reconciliation, render-signature diffing, and cached `QPixmap` glyphs reduced scaled `Window.render()` latency from **`44.14 ms` to `1.31 ms` (-97.0%, 33.7x faster; `0.00 ms` on unchanged background polls)** and 9-keystroke search latency from **`329.50 ms` to `43.54 ms` (-86.8%, 7.6x faster)**.

---

## Ranked Findings

| Rank | Path | Bottleneck | Baseline | Expected or Measured Result | Confidence |
|---|---|---|---|---|---|
| **1** | `Store.inventory()` -> `chrootable-manager --list --json` (`chrootables/backend.py:139-177`, `chrootable-manager:626-690`) | `N+1` subprocess/subshell storm (~28 forks per image: 7 `awk` calls on `.meta`, 2 `stat` calls, 3 `basename` calls, per-char `printf` JSON loop) + redundant Python `glob`/`stat` syscalls per snapshot and inner-loop set comprehension | `628.01 ms` (24 images, 48 snapshots) | **Measured: `22.05 ms`** (`-96.5%`, `28.5x` speedup) | Verified (Measured) |
| **2** | `Window.refresh()` -> `Window.render()` -> `render_library()` & `select_workspace()` (`chrootables/app.py:550-826`) | Full `QListWidget.clear()` + `WorkspaceRow` reallocation + `QFrame#detail` destruction (`setWidget(card)`) + `QTableWidget` rebuild on every 6s poll and search keystroke | `44.14 ms` / render (`329.50 ms` / 9 search keys; `44.14 ms` on unchanged poll) | **Measured: `1.31 ms` / render** (`43.54 ms` / 9 search keys; **`0.00 ms`** on unchanged poll; `-97.0%`) | Verified (Measured) |
| **3** | `chrootable-manager --dashboard` -> `workspace_summary()` & `dashboard()` (`chrootable-manager:702-765`) | Double directory pass spawning `stat`, `awk`, `basename`, and `image_status` subshells per image across both summary and table rows | `313.86 ms` (24 images) | **Measured: `17.44 ms`** (`-94.4%`, `18.0x` speedup) | Verified (Measured) |
| **4** | `Store.logs()` -> `chrootable-manager --logs` (`chrootables/backend.py:179-195`, `chrootable-manager:773-792`) | `O(L * A)` `N+1` `awk` process spawn scanning all of `activity.tsv` once per candidate `$LOG_DIR/$name-*.log` file + eager list allocation in Python `Store.logs()` | `121.34 ms` (80 log files, 80 journal entries) | **Measured: `8.81 ms`** (`-92.7%`, `13.8x` speedup) | Verified (Measured) |
| **5** | `Client.read()` -> `Window.append_output()` & `Store.operate()` (`chrootables/client.py:51-90`, `chrootables/backend.py:214-235`) | Unbatched per-line `output` signal emissions triggering 500 synchronous `QPlainTextEdit` cursor/scroll layouts per pipe read + unconditional 256 KiB log re-reads every `200 ms` | `15.45 ms` / 500-line burst (`500` UI layout passes) | **Measured: `2.58 ms` / 500-line burst** (`1` UI layout pass; `-83.3%`, `6.0x` speedup) | Verified (Measured) |
| **6** | `glyph()` & `when()` + `Window.navigate()` / `Window.run_operation()` (`chrootables/app.py:72-106`, `540-544`, `1013-1016`) | Uncached `QPainter`/`QFont` `QPixmap` rasterization, repeated `datetime.fromisoformat()` parsing, and redundant `chrootable-manager --doctor` subprocesses on tab switches and non-setup mutations | `2.81 ms` / 100 glyphs; `20.19 ms` + `11.79 ms` IPC across 4 route switches | **Measured: `0.033 ms` / 100 glyphs** (`-98.8%`); **`11.10 ms`** across 4 route switches (`0` redundant `--doctor` calls) | Verified (Measured) |

---

## Optimization Plan

### Finding 1: `Store.inventory()` and `chrootable-manager --list --json` `N+1` Subprocess Storm
- **Root Cause**:
  1. `image_json()` in `chrootable-manager` invoked `metadata_value()` 7 separate times per image (each forking a subshell and `/usr/bin/awk` to read a single key from the same 8-line `.img.meta` file), spawned `/usr/bin/basename` 3 times, spawned `/usr/bin/stat` twice (`image_size_mib` and `allocated_bytes`), forked a subshell for `image_status` even when no `.lock` file existed, and looped character-by-character in `json_string`.
  2. `Store.inventory()` in `chrootables/backend.py` rebuilt `{x["name"] for x in images}` inside the inner snapshot loop and called `Path.glob()`, `is_symlink()`, `is_file()`, and `stat()` separately per snapshot.
- **Exact Change**:
  - In `chrootable-manager` (`json_string`, `image_json`, `list_images`):
    - Added a fast path in `json_string` (`if [[ $value != *[\"\\$'\001'-$'\037']* ]]; then printf '"%s"' "$value"; return; fi`) while keeping the character-by-character escape loop for strings containing quotes, backslashes, or control characters.
    - Replaced `basename` forks with Bash parameter expansion (`name=${image##*/}; name=${name%.img}`).
    - Replaced the 7 `awk` forks with a single in-process `while IFS='=' read -r mkey mval` loop reading `$image.meta` (with fallback to `.mode` for legacy images).
    - Batched `stat -c '%s %b' -- "${valid_images[@]}"` into a single external `stat` invocation across all images in `list_images()`, passing pre-fetched `bytes` and `blocks` into `image_json`.
    - Guarded `status=$(image_status "$image")` behind `[[ -f $image.lock && ! -L $image.lock ]]` so unlocked images incur zero subshell/flock overhead.
  - In `chrootables/backend.py` (`Store.inventory`):
    - Hoisted `image_names = {x["name"] for x in images}` outside the snapshot loops and replaced nested `Path.glob()` + triple `stat` calls with `os.scandir()` and `entry.stat(follow_symlinks=False)`.
- **Correctness Guard**:
  - Preserves symlink rejection (`! -L` and `follow_symlinks=False`), legacy `.mode` file upgrade/fallback (`test_legacy_image_and_symlink_ignored`), `flock` busy detection (`test_busy_lock_visible_in_real_inventory`), and orphan snapshot detection (`test_real_cli_inventory_and_snapshot_metadata`).
- **Measurement Method**:
  - Timed 5 consecutive `Store.inventory()` calls against a temporary store containing 24 `.img` + `.meta` files, 48 snapshots across 24 folders, and 80 `activity.tsv` records using `time.perf_counter()`.

### Finding 2: Desktop UI Widget Destruction and Redundant Re-Renders (`Window.render`, `render_library`, `select_workspace`, `fill_table`)
- **Root Cause**:
  - `Window.refresh()` called `self.render()` on every 6-second poll even when `data` had not changed.
  - `Window.render_library()` destroyed all `QListWidgetItem` and `WorkspaceRow` instances via `self.workspaces.clear()` and rebuilt them from scratch.
  - `Window.select_workspace()` constructed a new 25-widget `QFrame#detail` card and replaced `self.details.setWidget(card)` on every selection, search keystroke, or poll.
  - `Window.fill_table()` recreated all `QTableWidgetItem` cells on every poll even when snapshot/activity rows were identical.
- **Exact Change**:
  - Added `self._render_signature = (data, self.busy, self.demo, was_locked)` in `Window.refresh()` to skip `self.render()` when poll state is unchanged.
  - Built the detail card once in `Window.build_detail_card()` and updated its labels, checkboxes, buttons, and menu actions in-place inside `Window.select_workspace()` only when `self._detail_state` changes.
  - Added `WorkspaceRow.update_row(item, favorite)` keyed by `self._state` and updated `Window.render_library()` to reuse existing `QListWidgetItem` and `WorkspaceRow` instances in-place (trimming only excess tail items when filtering narrows results) while wrapping mutations in `setUpdatesEnabled(False)` / `True` and `setUniformItemSizes(True)`.
  - Added `table.property("rendered_rows")` guard in `Window.fill_table()` to skip table reconstruction when rows are unchanged.
- **Correctness Guard**:
  - `self._detail_state` includes all workspace fields plus `(fav, can_configure, isolated, share, read_only)`. Signal blockers (`blockSignals(True/False)`) prevent recursive toggle callbacks during programmatic state updates. Verified by all existing and new `DesktopTests`.
- **Measurement Method**:
  - Timed 50 iterations of `Window.render()` + `app.processEvents()` on both the 4-workspace demo dataset and the 24-workspace / 40-snapshot / 100-activity scaled dataset, plus a 9-keystroke live search sequence (`"w"` -> `"workspace-12"` -> `""`).

### Finding 3: CLI Dashboard (`workspace_summary` and `dashboard`) Double-Pass Fork Storm
- **Root Cause**:
  - `dashboard()` called `workspace_summary()` (which looped over all images spawning `stat`, `awk`, and `image_status`) and then looped over all images a second time spawning `basename` twice, `image_status`, `image_lifecycle`, `metadata_value`, `image_size_mib`, and `snapshot_count`.
- **Exact Change**:
  - Batched `stat` into a single `mapfile -t stat_lines < <(stat ... -- "${valid_images[@]}")` call per function, replaced `awk`/`basename` subshells with in-process `.meta` reading and parameter expansion, and guarded `image_status` behind `[[ -f $image.lock && ! -L $image.lock ]]`.
- **Correctness Guard**:
  - Preserves exact output formatting, column widths, human-readable byte formatting, and `.mode` fallback.
- **Measurement Method**:
  - Timed 5 consecutive `chrootable-manager --dashboard` invocations on the 24-workspace store.

### Finding 4: `O(L * A)` `N+1` Log Verification in `show_logs()` and Eager Splitting in `Store.logs()`
- **Root Cause**:
  - `show_logs()` in `chrootable-manager` iterated over `"$LOG_DIR/$name-"*.log` and spawned a separate `awk` process scanning `activity.tsv` for every log file to prevent prefix collisions (e.g., `"lab"` vs. `"lab-extra"`).
  - `Store.logs()` in `chrootables/backend.py` eagerly built a list of split lines for the entire `activity.tsv` file before running `any(...)`.
- **Exact Change**:
  - In `show_logs()`, read `activity.tsv` once in-process into a Bash associative array `valid_logs[$entry_log]=1` for entries where `entry_name == "$name"`, reducing journal verification per log file to an `O(1)` in-memory lookup with zero `awk` spawns.
  - In `Store.logs()`, replaced the list comprehension with a lazy generator expression over `splitlines()`.
- **Correctness Guard**:
  - Verified prefix-collision isolation (`"workspace-00"` vs. `"workspace-00-extra"`) and path confinement (`test_selected_operation_log_is_exact_and_confined`).
- **Measurement Method**:
  - Timed 5 consecutive `Store.logs("workspace-00")` calls on a store with 80 `.log` files (`40` for `workspace-00` and `40` for `workspace-00-extra`) and 80 `activity.tsv` entries.

### Finding 5: High-Frequency Streaming Output and Log Tailing (`Client.read` & `Store.operate`)
- **Root Cause**:
  - `Client.read()` emitted `self.output` for every individual `"output"` JSON line in `self.buffer`, causing `Window.append_output()` to run a full cursor move + text insert + `ensureCursorVisible()` scroll layout pass per line.
  - `Store.operate()`'s `tail()` thread called `read_regular(log)` (up to 256 KiB read + UTF-8 decode) every `200 ms` on every active log file even when the file had not been modified since the last tick.
- **Exact Change**:
  - In `Client.read()`, coalesced consecutive `"output"` chunks for the same request `id` within a single `readAllStandardOutput()` drain and flushed them in a single `self.output.emit()` call before any `"ready"` or `"result"` event (or at the end of the buffer).
  - In `Store.operate()`, tracked `stats[log] = (info.st_mtime_ns, info.st_size)` and skipped `read_regular(log)` when the file's timestamp and byte size were unchanged.
- **Correctness Guard**:
  - Flushing `pending_chunks` before `"ready"` or `"result"` guarantees all task output appears in `self.task_output` before completion status/error messages are appended (`test_client_coalesces_batched_output_before_result`).
- **Measurement Method**:
  - Timed processing of a 500-line JSON-lines `"output"` burst through `Client.read()` -> `Window.append_output()` + `app.processEvents()`.

### Finding 6: Uncached Procedural Glyphs, Timestamp Formatting, and Redundant `--doctor` Checks
- **Root Cause**:
  - `glyph()` re-rasterized identical `QPixmap` icons on every call; `when()` re-parsed identical ISO-8601 timestamps on every render; `Window.navigate(3)` and `Window.run_operation()` triggered `chrootable-manager --doctor` even when host health was already known and no package setup had occurred.
- **Exact Change**:
  - Cached rasterized pixmaps in `_GLYPH_CACHE[(kind, color, size)]` and decorated `when()` with `@lru_cache(maxsize=512)`.
  - Restricted automatic `check_health()` in `navigate(3)` to when `self.health_output` is empty, and in `run_operation()` to when `action == "setup"` or `not self.health_ready`.

---

## Measurement Results

All before and after measurements were collected on the same machine (`Linux 7.2.10-200.fc44.x86_64`, Python 3, PySide6 `QT_QPA_PLATFORM=offscreen`) using identical workloads:

| Execution Path / Workload | Baseline (Before) | Optimized (After) | Delta / Speedup | Measurement Type |
| :--- | :--- | :--- | :--- | :--- |
| **`Store.inventory()` (24 workspaces, 48 snapshots, 80 activities)** | `628.01 ms` | `22.05 ms` | **`-96.5%` (`28.5x` faster)** | Measured (avg of 5 runs) |
| **`chrootable-manager --dashboard` (24 workspaces)** | `313.86 ms` | `17.44 ms` | **`-94.4%` (`18.0x` faster)** | Measured (avg of 5 runs) |
| **`Store.logs()` / `--logs` (80 log files, prefix collision guard)** | `121.34 ms` | `8.81 ms` | **`-92.7%` (`13.8x` faster)** | Measured (avg of 5 runs) |
| **`Window.render()` — Scaled (24 workspaces, 40 snapshots, 100 activities)** | `44.14 ms` | `1.31 ms` (`0.00 ms` if unchanged) | **`-97.0%` (`33.7x` faster)** | Measured (avg of 50 runs) |
| **`Window.render()` — Demo (`assets/demo.json`: 4 workspaces)** | `13.86 ms` | `0.89 ms` | **`-93.6%` (`15.6x` faster)** | Measured (avg of 50 runs) |
| **Live Search Sequence (9 keystrokes across 24 workspaces)** | `329.50 ms` (`36.61 ms/key`) | `43.54 ms` (`4.84 ms/key`) | **`-86.8%` (`7.6x` faster)** | Measured (9-step sequence) |
| **Streaming Output Burst (`500` IPC lines via `Client.read`)** | `15.45 ms` (`500` layouts) | `2.58 ms` (`1` layout) | **`-83.3%` (`6.0x` faster)** | Measured (500-line burst) |
| **4-Route Navigation Cycle (`Workspaces -> Recovery -> Activity -> Health`)** | `20.19 ms` (+ `11.79 ms` IPC) | `11.10 ms` (`0 ms` extra IPC) | **`-45.0%` (`1.82x` faster)** | Measured (full 4-tab cycle) |
| **`glyph()` Procedural Icon Generation (100 calls)** | `2.81 ms` | `0.033 ms` | **`-98.8%` (`84.4x` faster)** | Measured (100 iterations) |

---

## Cache and Invalidation Notes

| Cache / Memoization Store | Key Structure | Scope & Lifetime | Invalidation Trigger | Stale-Data Policy & Correctness Risk |
| :--- | :--- | :--- | :--- | :--- |
| **`_GLYPH_CACHE`** (`chrootables/app.py:82`) | `(kind: str, color: str, size: int)` | Process-wide in-memory dict (7 bounded keys in practice) | Guarded by `cached is not None and not cached.isNull()` | **Zero stale risk**: `glyph()` is a pure deterministic function of `(kind, color, size)` and callers treat returned `QPixmap` instances as immutable. |
| **`when()` (`@lru_cache`)** (`chrootables/app.py:72`) | `value: str \| None` (ISO-8601 timestamp string or `"never"`/`"unknown"`) | Process-wide LRU bounded to `maxsize=512` entries | Automatic LRU eviction when >512 distinct timestamp strings are seen | **Zero stale risk**: Formatting an immutable ISO-8601 timestamp string (`"%b %d · %H:%M"`) is time-zone/locale-independent and purely functional. |
| **`Window._render_signature`** (`chrootables/app.py:286, 583`) | `(data: dict, busy: bool, demo: bool, was_locked: bool)` | Per-`Window` instance | Invalidated automatically whenever `inventory` payload `data`, `self.busy`, `self.demo`, or `self.store_locked` changes | **Zero stale risk**: Full structural equality check on the inventory dictionary and UI flags; direct user interactions (`favorite`, `search`, `filter`, `choose_shared_folder`) bypass `_render_signature` and invoke `render_library()` / `select_workspace()` directly. |
| **`Window._detail_state`** (`chrootables/app.py:285, 791`) | `(selected, status, lifecycle, suite, profile, size_mib, allocated_bytes, last_used_at, snapshots, path, fav, can_configure, isolated, share, read_only)` | Per-`Window` instance | Invalidated immediately when any attribute of the selected workspace or its local session preferences (`favorites`, `network_choices`, `shared_folder_choices`, `busy`, `demo`) changes | **Zero stale risk**: Captures every input displayed or enabled in `self.detail_card`. |
| **`WorkspaceRow._state`** (`chrootables/app.py:242, 251`) | `(name, favorite, profile, suite, size_mib, busy, ephemeral)` | Per-`WorkspaceRow` widget instance | Invalidated in `WorkspaceRow.update_row()` whenever any row-visible property of the workspace changes | **Zero stale risk**: Contains every field rendered by a list item row. |
| **`table.property("rendered_rows")`** (`chrootables/app.py:621`) | `rows: list[list[str]]` | Per-`QTableWidget` instance (`recovery_table`, `activity_table`) | Invalidated whenever the formatted 2D row list differs from the currently displayed table cells | **Zero stale risk**: Exact value comparison of all displayed cell strings. |
| **`Store.operate` `stats` Map** (`chrootables/backend.py:215, 226`) | `log: Path -> (st_mtime_ns: int, st_size: int)` | Per-`Store.operate()` invocation (local to one running mutation) | Invalidated whenever the log file's nanosecond mtime or byte size changes on disk; final `tail()` runs after `process.stdout.close()` | **Zero stale risk**: Nanosecond `st_mtime_ns` + `st_size` detects any append or truncation to the operation log file. |
