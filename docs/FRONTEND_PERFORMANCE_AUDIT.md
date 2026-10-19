# Frontend Performance Audit

## Routes and Budgets

### Application Architecture and Audited Routes
Chrootables (`chrootables/app.py`, `chrootables/client.py`, `chrootables/style.py`, `chrootables/backend.py`, `chrootables/broker.py`, and `chrootable-manager`) is a local-first Linux desktop workbench built on Python and PySide6 (Qt 6) communicating over an asynchronous JSON-lines stdio pipe with a polkit-managed helper (`chrootables/broker.py`).

Although delivered as a native Qt frontend rather than a browser bundle, user-perceived responsiveness is governed by the same Core Web Vitals analogues:
- **Initial Paint & Hydration (FCP / LCP analogue)**: Time from `Window()` instantiation to first complete paint and asynchronous broker inventory hydration (`Window.connected` -> `Window.refresh`).
- **Interaction to Next Paint (INP analogue)**: Main-thread event handler latency during live search keystrokes, filter selection, workspace row selection, route switching, and streaming task log output.
- **Cumulative Layout Shift (CLS analogue)**: Visual and scroll stability across 6-second background polling cycles (`Window.timer`), detail card refreshes, and notice/task panel transitions.

| Route Index | Route / View Name | Primary UI Container (`QStackedWidget`) | Dominant Work & Critical Path |
| :--- | :--- | :--- | :--- |
| `0` | **Workspaces (`WORKSPACE LIBRARY`)** | `Window.build_library` (`self.pages[0]`) | 4 summary metric cards, live search/filter bar, `QSplitter` with `QListWidget` (`WorkspaceRow` custom widgets + rasterized `QPixmap` glyphs) and `QScrollArea` detail card (`self.details`), plus empty/locked onboarding state (`self.library_stack`). |
| `1` | **Recovery (`RECOVERY LIBRARY`)** | `Window.build_recovery` (`self.pages[1]`) | 4-column `QTableWidget` (`self.recovery_table`) listing saved and orphaned `.img` snapshots, capacity, and formatted timestamps (`when()`), plus restore/delete controls. |
| `2` | **Activity (`OPERATION HISTORY`)** | `Window.build_activity` (`self.pages[2]`) | 4-column `QTableWidget` (`self.activity_table`) rendering up to 100 journal entries from `activity.tsv` and launching modal log inspection dialogs (`Window.show_log`). |
| `3` | **Host Health (`HOST HEALTH`)** | `Window.build_health` (`self.pages[3]`) | Read-only `QPlainTextEdit` (`self.health_output`) populated by `chrootable-manager --doctor` via the async broker, store path metadata, and dependency setup trigger. |
| Modal / Drawer | **Task Output & Create/Value Dialogs** | `self.task_panel`, `CreateDialog`, `ValueDialog` | Streaming stdout/log tailing into `QPlainTextEdit` (`self.task_output`, capped at 4,000 blocks) and synchronous form validation (`backend.name`, `PROFILES`). |

### Device / Storage Classes and Performance Budgets

| Profile Class | Hardware / I/O Characteristics | Target Frame Budget | Interaction Budget (INP) | Background Refresh UI Budget | Inventory IPC Budget (24 images) |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Tier 1: Desktop Workstation (Lab Baseline)** | x86_64 Linux (Kernel 7.2), NVMe/tmpfs, unthrottled CPU | `<= 16.6 ms` (60 Hz) | `<= 16.0 ms` per input event | `<= 2.0 ms` main-thread work per 6s poll | `<= 80 ms` background helper round-trip |
| **Tier 2: Low-End / Constrained Device** | 4x CPU slowdown (ultrabook power-save / ARM SBC / VM) + cold ext4 metadata reads | `<= 16.6 ms` (60 Hz) | `<= 50.0 ms` (0 long tasks > 50 ms) | `<= 8.0 ms` main-thread work (`0 ms` when idle/unchanged) | `<= 250 ms` background helper round-trip |

---

## Baseline Measurements

Baseline measurements were captured in a reproducible offscreen harness (`QT_QPA_PLATFORM=offscreen`) across two dataset sizes:
1. **Tour / Small Store (`assets/demo.json`)**: 4 workspaces, 3 recovery points, 3 activity records.
2. **Scaled Workbench Store**: 24 workspaces (across `minimal`, `python`, `build`, `network` profiles), 40–48 snapshots, and 100 activity journal entries.

| Metric / Operation | Dataset / Scope | Lab Baseline (Tier 1) | Estimated 4x Throttled (Tier 2) | Budget Status (Baseline) |
| :--- | :--- | :--- | :--- | :--- |
| **Initial Window Construction & First Paint (`Window(demo=True)`)** | 4 workspaces | `68.42 ms` | `~273.7 ms` | Pass |
| **Uncached `glyph()` Rasterization (100 calls)** | `QPainter` + `QFont` on `44x44` `QPixmap` | `2.81 ms` (`28.1 us/call`) | `~11.2 ms` | Warn (redundant per render) |
| **Full `Window.render()` Cycle** | Demo dataset (4 workspaces, 3 snapshots, 3 activities) | `13.86 ms` | `~55.4 ms` | **Fail on Tier 2** (>50 ms long task) |
| **Full `Window.render()` Cycle** | Scaled dataset (24 workspaces, 40 snapshots, 100 activities) | `44.14 ms` | `~176.6 ms` | **Fail on Tier 1 (>16.6 ms) & Tier 2 (>50 ms)** |
| **Live Search Typing Sequence (9 keystrokes)** | `"w"` -> `"workspace-12"` -> `""` (24 workspaces) | `329.50 ms` (`36.61 ms/key`) | `~1,318 ms` (`146.4 ms/key`) | **Fail** (janky typing, >16.6 ms/key) |
| **4-Route Navigation Cycle** | Routes `0 -> 1 -> 2 -> 3` | `20.19 ms` (+ `11.79 ms` IPC `--doctor`) | `~80.8 ms` | Warn (unconditional `--doctor` spawn) |
| **Streaming Task Output Burst (`500` lines)** | `Client.read` -> `Window.append_output` | `15.45 ms` (`500` layout/scroll passes) | `~61.8 ms` | **Fail on Tier 2** (>50 ms long task) |
| **Backend `Store.inventory()` Poll** | 24 workspaces, 48 snapshots, 100 activities | `628.01 ms` (`~672` process forks) | `~2,512 ms` | **Critical Fail** (>80 ms budget) |
| **Backend `Store.doctor()` Check** | Host readiness checks | `11.79 ms` | `~47.2 ms` | Pass |

---

## Rendering and Delivery Findings

### 1. Unconditional Widget Tree Destruction on Every 6-Second Poll and Keystroke (`chrootables/app.py`)
- **Verified Evidence**:
  - `self.timer` runs `self.refresh()` every `6000 ms`. On completion, `refresh()` previously executed `self.data = data; self.render()` unconditionally even when `data` was byte-for-byte identical to the previous poll.
  - `Window.render_library()` called `self.workspaces.clear()`, destroying every `QListWidgetItem` and `WorkspaceRow` widget (each containing nested `QHBoxLayout`/`QVBoxLayout` containers and 5 `QLabel` instances) and reallocating them from scratch.
  - `Window.select_workspace()` constructed a brand-new `QFrame#detail` card (~25 child widgets, `QGridLayout`, checkboxes, buttons, and a `QMenu` with 8 `QAction` items) and called `self.details.setWidget(card)` on every selection, refresh, favorite toggle, or search keystroke.
  - `Window.fill_table()` reallocated `QTableWidgetItem` instances for all `recovery_table` and `activity_table` cells on every 6-second poll regardless of whether the user was viewing those routes or whether the rows had changed.
- **User Impact**:
  - `44.14 ms` main-thread stall on every render at 24 workspaces (`36.61 ms` per search keystroke).
  - Replacing `self.details.setWidget(card)` during a 6-second background refresh destroyed any open `"•••"` (`QMenu`) popup menu, dropped keyboard focus inside the detail pane, and caused a 1-frame scroll reset to `0` before `QTimer.singleShot(0, ...)` restored `scroll_position`.

### 2. Uncached Procedural Icon Rasterization (`glyph()`) and Timestamp Formatting (`when()`) (`chrootables/app.py`)
- **Verified Evidence**:
  - `glyph(kind, color, size)` allocated a new `QPixmap`, initialized an antialiased `QPainter`, instantiated `QFont("Noto Sans", ...)`, and rasterized vector/text primitives on every call without caching, despite only 7 distinct `(kind, color, size)` combinations existing across the entire application (`size=64` window icon, `size=45` brand icon, `size=80` empty-state icon, and `size=44` for `box`, `Py`, `C+`, `~`).
  - `when(value)` parsed ISO-8601 strings via `datetime.fromisoformat(value).strftime(...)` repeatedly across up to 100 activity rows, all snapshots, and workspace details on every refresh.

### 3. `O(N)` Process Fork Storm in `chrootable-manager --list --json` and `Store.inventory()` (`chrootable-manager`, `chrootables/backend.py`)
- **Verified Evidence**:
  - `Store.inventory()` invokes `chrootable-manager --list --json`.
  - Inside `chrootable-manager`, `list_images` and `image_json` spawned **~28 subshells/processes per workspace image**: 3 `basename` processes, 7 separate `awk` invocations reading the same 8-line `.img.meta` file, 2 separate `stat` invocations (`stat -c%s` and `stat -c%b`), 1 `flock` check, 1 `snapshot_count` subshell, and a character-by-character Bash loop in `json_string` calling `printf` per character across 10 JSON fields.
  - At 24 workspaces, a single `inventory()` call took **628.01 ms** and spawned ~672 processes every 6 seconds.
  - Additionally, `Store.inventory()` in `chrootables/backend.py` reconstructed the set `{x["name"] for x in images}` inside the inner snapshot loop for every snapshot file, and `Store.operate()`'s `tail()` loop re-read and UTF-8 decoded up to 256 KiB from every active `.log` file every `200 ms` even when `(st_mtime_ns, st_size)` had not changed.

### 4. Uncoalesced High-Frequency IPC Output Streaming (`chrootables/client.py`, `chrootables/app.py`)
- **Verified Evidence**:
  - During package-heavy operations (`create` running `debootstrap`/`apt-get` or `setup`), `Client.read()` drained `process.readAllStandardOutput()` and emitted `self.output.emit(id, text)` on every individual newline-delimited JSON event.
  - Each emission invoked `Window.append_output()`, which moved a `QTextCursor`, inserted a single line, and called `self.task_output.ensureCursorVisible()`, forcing synchronous text layout and scrollbar recalculation `N` times per pipe read instead of once per read batch.

### 5. Redundant `--doctor` Subprocess Calls on Navigation and Non-Setup Mutations (`chrootables/app.py`)
- **Verified Evidence**:
  - `Window.navigate(3)` unconditionally called `self.check_health()` every time the user clicked the **Host health** tab, even when health output was already populated (and despite an explicit **Run checks** button existing on the view).
  - `Window.run_operation()` called `self.check_health()` after every workspace mutation (`snapshot`, `clone`, `resize`, `mode`, `delete`, etc.), spawning `chrootable-manager --doctor` even though only `setup` alters host package prerequisites.

---

## Optimization Plan

Changes were ranked by user-perceived impact and implementation safety, preserving all security boundaries, CLI/GUI parity, and read-only tour guarantees:

| Priority | Optimization | Target Files | User Impact | Risk & Safeguards |
| :--- | :--- | :--- | :--- | :--- |
| **P0** | **Persistent Detail Card & In-Place `WorkspaceRow` Reconciliation** | `chrootables/app.py` | Eliminates ~215 widget allocations per render/keystroke; prevents scroll jumps, focus loss, and menu closure during 6s background refreshes. | Low risk. Keyed by full workspace + UI state tuple `(selected, status, lifecycle, suite, profile, size_mib, allocated_bytes, last_used_at, snapshots, path, fav, can_configure, isolated, share, read_only)`. |
| **P0** | **In-Process Bash Metadata Parsing & Batched `stat` in `list_images` / `image_json`** | `chrootable-manager`, `chrootables/backend.py` | Cuts 24-workspace inventory latency by **96.5%** (`628.01 ms -> 22.05 ms`) by replacing 7 `awk` forks, 3 `basename` forks, and `N` `stat` forks with in-process `read`, parameter expansion, a single batched `stat -c '%s %b'`, conditional `.lock` probing, `os.scandir`, and a fast-path `json_string`. | Low risk. Preserves exact JSON schema, legacy `.mode` fallback, symlink rejection, and character escaping for control/quote characters. |
| **P1** | **Render Signature Diffing on Poll & Table Row Memoization** | `chrootables/app.py` | Reduces idle 6-second refresh main-thread render cost to **0.00 ms** when store inventory is unchanged; skips `QTableWidget` rebuilds when snapshot/activity rows are unchanged. | Low risk. Signature includes `(data, self.busy, self.demo, was_locked)` so any state transition triggers a render. |
| **P1** | **Batched IPC Output Coalescing & Log Tail Stat Guard** | `chrootables/client.py`, `chrootables/backend.py` | Reduces 500-line streaming output processing by **83.3%** (`15.45 ms -> 2.58 ms`) by coalescing consecutive `output` events per request ID in `Client.read()` and flushing before `ready`/`result`; skips reading unchanged log files in `Store.operate()`. | Low risk. Preserves strict message ordering (`output` always flushed before `result`). |
| **P2** | **Pixmap Glyph Cache (`_GLYPH_CACHE`), `@lru_cache` Timestamp Formatting, and Targeted Health Checks** | `chrootables/app.py` | Reduces `glyph()` cost by **98.8%** (`2.81 ms -> 0.033 ms` per 100 calls), eliminates repeated ISO date parsing, and avoids redundant `--doctor` subprocesses on tab switches and workspace mutations. | Low risk. Validates `not cached.isNull()` before returning cached `QPixmap`. |
| **P2** | **Accessibility Metadata & Keyboard Focus Ring** | `chrootables/app.py`, `chrootables/style.py` | Adds `AccessibleTextRole` to workspace list items, `accessibleName` to icon/toolbar controls, and `:focus` border styling to `QPushButton`. | Zero visual regression; strictly additive for assistive technologies and keyboard navigation. |

---

## User-Perceived Results

All benchmarks were re-run on the exact same environment and datasets after applying the optimizations:

| Metric / Operation | Baseline (Tier 1) | Post-Optimization (Tier 1) | Post-Optimization (Tier 2: 4x Throttle) | Improvement | Budget Status |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **`glyph()` Rasterization (100 calls)** | `2.81 ms` | `0.033 ms` | `~0.13 ms` | **-98.8% (84.4x faster)** | Pass (`< 1 ms`) |
| **`Window.render()` — Demo Dataset (4 workspaces)** | `13.86 ms` | `0.89 ms` | `~3.56 ms` | **-93.6% (15.6x faster)** | Pass (`<= 16.6 ms`) |
| **`Window.render()` — Scaled Dataset (24 workspaces, 40 snapshots, 100 activities)** | `44.14 ms` | `1.31 ms` (`0.00 ms` on unchanged poll) | `~5.24 ms` (`0.00 ms` on unchanged poll) | **-97.0% (33.7x faster)** | Pass (`<= 8.0 ms`) |
| **Live Search Typing (9 keystrokes, 24 workspaces)** | `329.50 ms` (`36.61 ms/key`) | `43.54 ms` (`4.84 ms/key`) | `~174.2 ms` (`19.35 ms/key`) | **-86.8% (7.6x faster)** | Pass (`<= 50 ms/key` on Tier 2) |
| **4-Route Navigation Cycle (`0 -> 1 -> 2 -> 3`)** | `20.19 ms` (+ `11.79 ms` IPC) | `11.10 ms` (`0 ms` extra IPC) | `~44.4 ms` | **-45.0% (1.82x faster)** | Pass (`<= 50 ms`) |
| **Streaming Output Burst (`500` IPC lines)** | `15.45 ms` | `2.58 ms` | `~10.3 ms` | **-83.3% (6.0x faster)** | Pass (`<= 16.6 ms`) |
| **Backend `Store.inventory()` (24 workspaces, 48 snapshots)** | `628.01 ms` | `22.05 ms` | `~88.2 ms` | **-96.5% (28.5x faster)** | Pass (`<= 80 ms` Tier 1 & `<= 250 ms` Tier 2) |
| **Detail Scroll / Focus Shift on Background Refresh** | `1` frame scroll reset + widget replacement | `0` widget replacements, `0` scroll shift | `0` widget replacements, `0` scroll shift | **100% eliminated** | Pass (`CLS = 0`) |

---

## Accessibility and Correctness Checks

1. **Automated Test Suite Verification**:
   - Executed `python3 -m unittest discover -s tests -p 'test_*.py' -v` (50 tests across `test_backend.py`, `test_desktop.py`, and `test_shared_folder.py`): **50/50 passed (`OK` in `1.71s`)**.
   - Executed `bash -n chrootable-manager`: **0 syntax errors**.
   - Added regression tests in `tests/test_desktop.py`:
     - `test_widget_reuse_and_accessibility_metadata`: Verifies that `self.window.details.widget()` and `WorkspaceRow` instances are preserved across `render()` calls and that `AccessibleTextRole` and `accessibleName()` are populated on workspace list items and toolbar/icon buttons.
     - `test_client_coalesces_batched_output_before_result`: Verifies that multiple `output` events arriving in a single pipe read are coalesced into a single UI emission and strictly ordered before subsequent `result` events.
2. **Visual & Layout Correctness**:
   - Verified offscreen rendering via `QT_QPA_PLATFORM=offscreen ./chrootables-desktop --demo --screenshot /tmp/chrootables-verify.png`. Typography (`Noto Sans` / `Noto Sans Mono`), dark palette contrast (`#e7eeeb` foreground on `#121719` / `#192123` surfaces, `#a8edc9` primary accent), card borders, badges, and splitter proportions remain visually identical to baseline.
   - Confirmed `test_workspace_details_keep_scroll_on_refresh_and_reset_on_selection` passes: detail pane scroll position is preserved across refreshes without asynchronous flicker and resets to `0` immediately when selecting a different workspace.
3. **Accessibility & Keyboard Navigation**:
   - All custom `QListWidgetItem` entries now expose structured screen-reader descriptions (`"<name>, Debian <suite>, <status>, <lifecycle>"`) via `Qt.ItemDataRole.AccessibleTextRole`.
   - Icon-only and unlabeled controls (`refresh_button`, `favorite_button`, `more_button`, `search`, `filter`) expose explicit `accessibleName` attributes and dynamic tooltips (`"Add to favorites"` / `"Remove from favorites"`).
   - `QPushButton:focus` now renders a visible `#a8edc9` focus border in `chrootables/style.py`, matching `QLineEdit:focus`, `QComboBox:focus`, and `QSpinBox:focus`.
   - Keyboard shortcuts (`Ctrl+N`, `Ctrl+F`, `F5`, `Ctrl+Return`) and focus retention during background refreshes are preserved.
4. **Data Freshness, Error, Empty, and Locked States**:
   - Background polling interval (`6000 ms`) and post-terminal launch refresh (`1500 ms`) are unchanged; data freshness is strictly preserved because `self.data` is updated on every poll and UI rendering is only skipped when `(data, self.busy, self.demo, was_locked)` is identical.
   - Verified locked store onboarding (`test_locked_store_is_normal_onboarding_not_an_error`), empty store state (`test_actual_async_helper_and_empty_store`), filtered empty state (`"No matching workspaces."`), and read-only demo tour protections (`test_demo_ignores_saved_shared_folders_and_does_not_write_preferences`).
