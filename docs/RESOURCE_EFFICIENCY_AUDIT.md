# Resource Efficiency Audit

## Baseline and Unit Economics

### System Scope and Resource Topology
Chrootables (`chrootable-manager`, `chrootables/backend.py`, `chrootables/broker.py`, `chrootables/client.py`, and `chrootables/app.py`) is a local-first Debian image workbench backed by a root-owned filesystem store (`/var/lib/chrootable-manager` by default, residing on a `929 GiB` LUKS-encrypted Btrfs volume on `Linux 7.2.10-200.fc44.x86_64`).

Rather than evaluating aggregate host spend in isolation, resource efficiency is governed by **six primary unit-cost dimensions**:
1. **Compute & Syscall Cost per Desktop Poll (`Cost / Poll` & `Cost / Active Idle Hour`)**:
   - The Qt desktop (`Window.timer` in `chrootables/app.py:292-294`) polls `Store.inventory()` every `6,000 ms` (`10 polls/min`, `600 polls/hr`, `14,400 polls/day`).
   - **Baseline Unit Cost**: At a scaled workbench size (`24` workspaces, `48` snapshots, `80` activity records), each poll required **`22.02 ms`** of helper wall time (`440.36 ms` per 20 polls), spawning **`3` OS subprocesses per poll** (`bash chrootable-manager --list --json`, `readlink -f`, and `stat -c '%s %b'`), opening and reading **`73` files per poll** (`24` `.img.meta` files in Bash, `48` snapshot `.img.meta` files in Python, and `1` `activity.tsv` journal), and serializing the full inventory over stdio IPC—even when zero files in the store had changed (`1,800` subprocess spawns/hr and `43,800` file reads/hr per open desktop session) and even when the desktop window was minimized.
2. **Physical Storage & Export Cost per Provisioned Workspace (`GiB Allocated / Workspace` & `MiB / Export`)**:
   - Workspaces are provisioned in four profiles (`minimal` at `2 GiB` suggested capacity, `network` at `2 GiB`, `build` at `4 GiB`, `python` at `4 GiB`, expandable up to `1 TiB`).
   - **Baseline Unit Cost**:
     - `allocate_image()` (`chrootable-manager:254-262`) invoked `fallocate -l "${size_mib}M"` before `truncate -s`, preallocating `100%` of the nominal capacity (`4,096.0 MiB` of physical extents for a `4 GiB` workspace in `5.75 ms`), only for `mkfs.ext4` on the very next line (`chrootable-manager:282`) to issue `FALLOC_FL_PUNCH_HOLE` (`discard`) across the entire image (`9.39 ms`, `15.14 ms` total), immediately deallocating `98.4%` of those extents down to `67.2 MiB` (or leaving `100%` thick overprovisioning on hosts with `mke2fs` discard disabled).
     - In `worker_main` `create` (`chrootable-manager:567-576`), `chroot "$WORKER_ROOT" apt-get clean` was guarded inside `if [[ -n $packages ]]; then`, so the default `minimal` profile (`packages=""`) never cleaned `/var/cache/apt/archives/*.deb` after `debootstrap`. Every `minimal` workspace retained **`~58.0 MiB`** of redundant, already-installed `.deb` archives (`234.81 MiB` vs. `176.81 MiB` rootfs payload, `+32.8%` storage overhead), inflating every downstream snapshot and inflating `--export` `.tar.gz` archives from **`50.83 MiB` to `108.86 MiB` (`+114.2%` size bloat)** while burning **`1,994.66 ms`** vs. **`801.59 ms`** of `gzip` CPU time per export.
3. **Storage & I/O Cost per Desktop Archive Export (`Bytes Written & Blocks Allocated / Export`)**:
   - Exporting a workspace from the desktop (`Window.export` -> `Store.operate("export")` -> `deliver_export()` in `chrootables/backend.py:84-115`) writes `$STORE_DIR/exports/<name>-<ts>-<token>.tar.gz` as root and then copies the archive to the user's destination path in an unprivileged child process.
   - **Baseline Unit Cost**: `deliver_export()` used `shutil.copyfileobj(archive, output)` with default `64 KiB` user-space Python buffers, requiring **`13.50 ms`** and `2,048` user-space `read`/`write` syscalls per `64 MiB` archive and allocating a full second physical copy on disk (`2.0x` physical storage footprint = `128.0 MiB` allocated per `64 MiB` export) even when `/var/lib/chrootable-manager` and `/home` reside on the same Btrfs filesystem.
4. **Disk Read I/O Cost per Streaming Mutation (`MiB Read / Operation`)**:
   - During long-running mutations (`create`, `clone`, `repair`, `export`, `setup`), `Store.operate()` (`chrootables/backend.py:230-257`) polls `$STORE_DIR/logs/*.log` every `200 ms`.
   - **Baseline Unit Cost**: Whenever the active operation log grew, `tail()` called `read_regular(log)` from byte `0` (up to `256 KiB`), decoded the full file, stored the full string in `offsets[log]`, and sliced `data[len(previous):]`. Across a `200`-tick (`40 s`) operation producing a `210.9 KiB` log with `40` historical logs in `logs/`, `tail()` read **`20.70 MiB`** from disk (**`100.5x` read amplification**) and spent **`27.43 ms`** in Python log-scanning CPU.
5. **Compute & Syscall Cost per CLI Inspection and Lifecycle Mutation (`Forks & ms / CLI Command`)**:
   - **Baseline Unit Cost**:
     - `chrootable-manager --inspect <name>` (`image_details()`) required **`24.86 ms`** and spawned **`12` subprocesses** (`basename`, `image_status`, `image_lifecycle`, `5x metadata_value` via `awk`, `snapshot_count`, `2x stat`, `human_bytes`) to inspect one image.
     - `chrootable-manager --dashboard` required **`18.42 ms`** at `24` workspaces because `dashboard()` invoked `workspace_summary()` (Pass 1 over all images and snapshots) and then iterated over all images and snapshots a second time (Pass 2).
     - `clone_image()` metadata propagation (`copy_metadata` + `3x metadata_set`) required **`28.74 ms`**, spawning **`23` subprocesses** (`8x awk`/`stat` in `copy_metadata`, `3x awk` in `metadata_set`, `4x mktemp`, `4x mv`, `basename`, `date`) and rewriting `$PENDING.meta` **`4` consecutive times**.
6. **Network Egress/Ingress per Workspace Creation (`MiB Downloaded / Create`)**:
   - Creating a workspace downloads signed Debian release indices (`~10.5 MiB` compressed `InRelease` + `Packages.xz`) and `.deb` packages (`~58 MiB` for `minbase`; `~95–210 MiB` for `network`, `python`, and `build` profiles) over HTTPS (`https://deb.debian.org/debian`).
   - Subsequent sessions using `--enter-isolated` or `--try --isolated` run inside an isolated network namespace (`unshare --net` with only loopback `lo` active), consuming **`0 B`** of network egress/ingress.

### Workload Seasonality and Hard Constraints
- **Seasonality Profile**:
  - **Interactive Burst Phase**: Developer onboarding, compiler/test runs, snapshot creation before risky upgrades, and archive exports generate bursty disk I/O, loop mounts, and network downloads (`create` / `setup`).
  - **Long Idle Tail**: Desktop windows frequently remain open in the background or minimized for hours while developers work in external terminal emulators (`konsole`, `gnome-terminal`, `xterm`). During this tail (`>95%` of wall-clock runtime), the `6,000 ms` polling loop dominates cumulative CPU wakeups, process forks, and filesystem metadata reads.
- **Hard Reliability, Retention, and Compliance Constraints**:
  - **Cryptographic Verification**: `debootstrap` must enforce `--force-check-gpg --keyring=/usr/share/keyrings/debian-archive-keyring.gpg` over HTTPS (`chrootable-manager:10, 564`).
  - **Crash-Safe Atomic Mutations & Pre-Restore Backups**: All image/metadata mutations write to `.partial` / `.meta.XXXXXX` files and publish via atomic `mv -T` (`chrootable-manager:67, 231-235`). Restoring a workspace (`restore_image`, `chrootable-manager:405-408`) must automatically save a `before-restore-<timestamp>-<pid>` snapshot before overwriting the active image.
  - **Retention on Workspace Deletion**: Deleting a workspace (`delete_image`, `chrootable-manager:358-365`) must retain its snapshots (`$SNAPSHOT_DIR/<name>/`) and exports (`$EXPORT_DIR/`).
  - **Privilege Separation & Confinement**: Store directories must be `0700` owned by the invoking user/root (`init_store`), symlinks must be rejected (`O_NOFOLLOW`, `follow_symlinks=False`, `[[ ! -L ... ]]`), and desktop exports must be written to the user's destination by an unprivileged child process (`deliver_export`).

---

## Waste Attribution

| Resource Domain | Component / Consumer | Verified Waste Mechanism | Unit Waste Metric (Baseline) | Root Cause Classification |
| :--- | :--- | :--- | :--- | :--- |
| **Compute & Syscalls (Idle Polling)** | `Store.inventory()` (`chrootables/backend.py:139-191`) & `Window.timer` (`chrootables/app.py:292-294`) | Every 6s poll spawns `chrootable-manager --list --json`, re-reads all `48` snapshot `.meta` files, and re-parses `activity.tsv` even when zero store files changed; continues polling while window is minimized. | `22.02 ms/poll`, `3` forks/poll, `73` file reads/poll (`13.2 s` CPU & `1,800` forks/hr idle) | Duplicate work & inefficient schedule |
| **Storage & Export CPU (`minimal` Profile)** | `worker_main create` (`chrootable-manager:567-576`) | `chroot "$WORKER_ROOT" apt-get clean` only executes when `[[ -n $packages ]]`, leaving `~58 MiB` of downloaded `.deb` archives in `/var/cache/apt/archives/` inside every `minimal` workspace image. | `+58.0 MiB` (`+32.8%`) per `minimal` image & snapshot; `+58.03 MiB` (`+114.2%`) & `+1,193 ms` CPU per `.tar.gz` export | Storage bloat & duplicate compression work |
| **Storage & I/O (Export Delivery)** | `deliver_export()` (`chrootables/backend.py:84-115`) | Copies exported `.tar.gz` from `$STORE_DIR/exports/` to user destination via `64 KiB` user-space `shutil.copyfileobj` loop instead of kernel `os.copy_file_range` CoW reflink / splice. | `2.0x` physical disk blocks (`+64 MiB` duplicate storage per `64 MiB` export) & `13.50 ms` copy time | Duplicate storage allocation & user-space copy bounce |
| **Disk Read I/O (Log Tailing)** | `Store.operate()` `tail()` (`chrootables/backend.py:230-257`) | Re-reads active `.log` file from byte `0` (up to `256 KiB`) on every `200 ms` tick and constructs `Path` + `lstat()` for all historical logs in `$STORE_DIR/logs/`. | `20.70 MiB` read for a `210.9 KiB` log (`100.5x` read amplification) & `27.43 ms` CPU across `200` ticks | Quadratic `O(T * S)` read amplification |
| **Storage Metadata Churn (Image Provisioning)** | `allocate_image()` (`chrootable-manager:254-262`) | Calls `fallocate -l "${size_mib}M"` before `truncate -s`, allocating `4,096 MiB` of physical extents that `mkfs.ext4` immediately hole-punches (`discard`) 5 ms later (or overprovisions `100%` if discard is disabled). | `4,096 MiB` transient extent churn (or `94.6%` overprovisioning without discard) & `5.75 ms` vs `0.74 ms` alloc latency | Overprovisioning & redundant extent churn |
| **Compute (CLI Metadata & Inspection)** | `metadata_value`, `metadata_set`, `copy_metadata`, `clone_image`, `image_details`, `dashboard`, `activity` (`chrootable-manager`) | Spawns external `awk`, `stat`, and `basename` subprocesses for 8-line `.meta` files; rewrites `$PENDING.meta` `4` times in `clone_image`; runs two full directory passes in `dashboard()`. | `28.74 ms` (`23` forks) per clone metadata update; `24.86 ms` (`12` forks) per `--inspect`; `18.42 ms` (double pass) per `--dashboard` | Subprocess fork storm & redundant file rewrites |

---

## Ranked Opportunities

Opportunities are ranked by **durable unit-cost reduction**, **operational safety**, and **reversibility**:

| Rank | Opportunity | Target Location | Unit-Cost Impact | Operational Risk | Reversibility | Status |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **1** | **Stat-Signature Inventory Caching, In-Process `flock` Probing, and Minimized-Window Poll Suppression** | `chrootables/backend.py:90-108, 150-266`, `chrootables/app.py:292-294, 1117-1125` | Cuts steady-state poll cost from **`22.02 ms` (`3` forks, `73` file reads) to `0.708 ms` (`0` forks, `0` file reads; `-96.8%`)** and **`0.00 ms` (`-100%`)** when window is minimized. | **Low**: Validates directory non-symlink invariants on every poll and probes `.img.lock` live via `fcntl.flock`. | Instant (stateless in-memory cache per `Store` instance). | **Implemented & Verified** |
| **2** | **Unconditional `apt-get clean` Across All Profiles in `worker_main create`** | `chrootable-manager:594-604` | Reclaims **`58.0 MiB` (`-24.7%`)** per `minimal` rootfs & snapshot; cuts `minimal` `.tar.gz` export size by **`-53.3%` (`108.86 MiB -> 50.83 MiB`)** and export CPU time by **`-59.8%` (`1,994.66 ms -> 801.59 ms`)**. | **Low**: Matches existing cleanup behavior of `build`, `python`, and `network` profiles. | Instant (1-line change in `worker_main`). | **Implemented & Verified** |
| **3** | **Kernel `os.copy_file_range` CoW Reflink / Zero-Copy in `deliver_export()`** | `chrootables/backend.py:110-137` | Eliminates **`100%` of duplicate physical block allocation** for user export copies on same-volume Btrfs/XFS (`2.0x -> 1.0x` physical storage) and cuts `64 MiB` export delivery latency by **`-99.1%` (`13.50 ms -> 0.12 ms`)**. | **Low**: Automatic fallback to `shutil.copyfileobj(archive, output, 1024 * 1024)` on cross-filesystem `EXDEV` or unsupported kernels. | Instant (self-contained in `deliver_export`). | **Implemented & Verified** |
| **4** | **Incremental Byte-Offset Log Tailing & `os.scandir` Filtering in `Store.operate()`** | `chrootables/backend.py:75-87, 311-345` | Eliminates `O(T * S)` log re-reading, reducing cumulative disk reads for a `210.9 KiB` operation log over `200` ticks from **`20.70 MiB` to `210.9 KiB` (`-99.0%`, `100.5x -> 1.0x` read amplification)** and cutting `tail()` CPU time by **`-58.1%` (`27.43 ms -> 11.49 ms`)**. | **Low**: Handles file truncation (`info.st_size < prev_offset`) and enforces `256 KiB` cap and `O_NOFOLLOW`. | Instant (drop-in `offset` parameter on `read_regular`). | **Implemented & Verified** |
| **5** | **Sparse-First Allocation (`truncate -s` before `fallocate -l`) in `allocate_image()`** | `chrootable-manager:274-281` | Eliminates `4,096 MiB` of redundant extent preallocation + immediate `mkfs.ext4` hole-punching, cutting `4 GiB` image init latency by **`-36.7%` (`15.14 ms -> 9.58 ms`)** and preventing `94.6%` physical overprovisioning when `mke2fs` discard is disabled. | **Low**: Retains strict pre-flight free-space admission check (`df -PB1 "$STORE_DIR" >= size_mib`). | Instant (reorders `truncate` before `fallocate`). | **Implemented & Verified** |
| **6** | **In-Process Bash Metadata Operations, Single-Pass `--dashboard`, and `--inspect` Optimization** | `chrootable-manager:48-110, 198-211, 310-325, 444-460, 732-850` | Cuts clone metadata update latency by **`-83.7%` (`28.74 ms -> 4.68 ms`, `4` file writes -> `1`)**, `--inspect` latency by **`-58.2%` (`24.86 ms -> 10.38 ms`)**, and `--dashboard` latency by **`-13.4%` (`18.42 ms -> 15.96 ms`)**. | **Low**: Preserves legacy `.mode` upgrade path, atomic `mktemp` + `mv -T`, and exact CLI output format. | Instant (pure Bash in-process functions). | **Implemented & Verified** |

---

## Reliability and Compliance Trade-offs

1. **Thin/Sparse Provisioning (`truncate -s`) vs. Capacity Admission Control (`df -PB1`)**:
   - *Trade-off*: Pure thin provisioning without admission control risks overcommitting host disk space, whereas thick preallocation via `fallocate` wastes physical blocks (or causes pointless extent allocation + immediate `mkfs.ext4` discard churn).
   - *Decision*: Keep the hard pre-flight capacity admission check in `create_image()` (`(( available >= size * 1048576 ))` via `df -PB1 "$STORE_DIR"`, ensuring peak workspace capacity + host headroom exists at creation time) while using `truncate -s` for sparse block allocation so physical disk blocks are only consumed as data is written (`67.2 MiB` initial ext4 metadata + actual rootfs size).
2. **Metadata Caching vs. Real-Time Lock & Mutation Consistency**:
   - *Trade-off*: Caching `Store.inventory()` purely by time (TTL) could hide an external CLI session acquiring `.img.lock` or a workspace mutation, violating UI state accuracy.
   - *Decision*: Never use blind TTL caching. Instead, validate the `(st_ino, st_mtime_ns, st_size, st_blocks)` signature of all `.img`, `.meta`, `.mode` files, snapshot folders, and `activity.tsv` on every poll, and **always probe existing `.img.lock` files live in-process via `fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)`**. This achieves `0` subprocess forks on unchanged polls with **zero stale-lock or stale-mutation window**.
3. **Retained Store Exports vs. Physical Storage Deduplication**:
   - *Trade-off*: Deleting `$STORE_DIR/exports/*.tar.gz` after `deliver_export()` would violate the documented durability contract (`Exports remain in the protected store... Snapshots and exports were retained`).
   - *Decision*: Retain both the store archive in `$STORE_DIR/exports/` and the user's destination file, but use `os.copy_file_range()` inside the unprivileged delivery child process. On Btrfs/XFS, both paths share the exact same physical disk extents via CoW reflink (`1.0x` physical storage cost instead of `2.0x`), preserving full retention and privilege separation at zero extra physical storage cost.
4. **In-Image Package Cache Cleanup vs. Cryptographic Audit Trail**:
   - *Trade-off*: Cleaning `/var/cache/apt/archives/*.deb` in `minimal` images must not remove `/var/lib/dpkg/status`, `/var/log/bootstrap.log`, or GPG keyring verification.
   - *Decision*: Run `chroot "$WORKER_ROOT" apt-get clean` after `debootstrap` (matching `build`, `python`, and `network` profiles), which removes only redundant downloaded `.deb` archives and transient `pkgcache.bin`/`srcpkgcache.bin` while preserving `/var/lib/dpkg/*`, `/var/log/bootstrap.log`, and `$STORE_DIR/logs/*.log`.

---

## Rollout and Guardrails

### Phased Rollout Plan
1. **Phase 1 — Automated Verification & Regression Gate**:
   - Run static syntax check (`bash -n chrootable-manager`) and the full unit/integration test suite (`python3 -m unittest discover -s tests -p 'test_*.py' -v`, `52` tests covering CLI inventory parity, live `flock` busy detection, cache invalidation on `.meta` mutation, bounded offset log reading, export delivery, shared folder security boundaries, and minimized-window poll suppression).
2. **Phase 2 — Offscreen UI & Telemetry Verification**:
   - Verify offscreen rendering (`QT_QPA_PLATFORM=offscreen ./chrootables-desktop --demo --screenshot /tmp/chrootables.png`) and confirm that background poll latency, `Store.operate()` log streaming, and `deliver_export()` meet target budgets.
3. **Phase 3 — Production Opt-In Integration Check**:
   - Run `sudo tests/integration.sh` on target hosts to validate end-to-end signed Debian bootstrap, snapshot/restore safety nets, and archive export integrity.

### Budget Guardrails & Anomaly Detection
To prevent future regressions and ensure cost optimization never masks operational failures:

| Guardrail Metric | Target Budget | Warning Threshold | Hard Limit / Fail Gate | Enforcement Mechanism |
| :--- | :--- | :--- | :--- | :--- |
| **Idle Poll Latency (`Store.inventory()`, 24 images, cached)** | `<= 1.0 ms` | `> 2.5 ms` | `> 10.0 ms` | Benchmark harness & `test_inventory_cache_reuses_unchanged_state_and_tracks_locks_and_mutations` |
| **Idle Poll Subprocess Spawns (unchanged store)** | `0 forks / poll` | `> 0 forks / poll` | `> 1 fork / poll` | Enforced by `mocked_run.call_count == 1` across repeated polls + lock transitions in `test_backend.py` |
| **Minimized Window Background Poll Rate** | `0 polls / min` | `> 0 polls / min` | `> 1 poll / min` | Enforced by `test_minimized_window_skips_timer_poll_and_refreshes_on_restore` in `test_desktop.py` |
| **Streaming Log Tail Read Amplification (`Store.operate`)** | `1.0x` (`<= 1.05x`) | `> 1.5x` | `> 2.0x` (or `> 256 KiB` single read) | Enforced by `read_regular(log, offset=prev_offset)` and `test_safe_bounded_log_reads` |
| **Minimal Workspace Export Size Ratio (`export_gz / raw_rootfs`)** | `<= 0.35x` (`~51 MiB`) | `> 0.45x` | `> 0.50x` (indicates uncleaned `.deb` cache) | Enforced by unconditional `chroot "$WORKER_ROOT" apt-get clean` in `worker_main create` |
| **Host Free-Space Admission Gate (`create_image`)** | `available >= size_mib` | `available < 1.25 * size_mib` | `available < size_mib` (hard abort) | Enforced by `df -PB1 "$STORE_DIR"` check in `chrootable-manager:293-294` |

### Rollback Plan
All optimizations are backwards-compatible with existing `/var/lib/chrootable-manager` store layouts (`.img`, `format=2` `.img.meta`, legacy `.img.mode`, `.img.lock`, and `activity.tsv`) and require zero schema migrations:
- **Backend Inventory Cache / Incremental Log Tail / CoW Export (`chrootables/backend.py`)**: Can be rolled back independently via `git revert` without restarting active workspace sessions.
- **Minimized Poll Suppression (`chrootables/app.py`)**: Reconnects `self.timer.timeout` directly to `self.refresh` if any window-manager state-change quirk is observed.
- **Bash Manager Optimizations (`chrootable-manager`)**: `allocate_image`, `copy_metadata`, `metadata_value`, `metadata_set`, and `dashboard` preserve identical CLI flags, exit codes, lock semantics, and JSON/TSV output schemas.

---

## Measured Savings

All before and after measurements were collected on the target host (`Linux 7.2.10-200.fc44.x86_64`, Btrfs on LUKS, Python 3, PySide6 `QT_QPA_PLATFORM=offscreen`) across reproducible workloads:

| Workload / Efficiency Unit | Baseline (Before) | Optimized (After) | Measured Unit Savings | Verification Method |
| :--- | :--- | :--- | :--- | :--- |
| **Steady-State `Store.inventory()` Poll (24 workspaces, 48 snapshots, 80 activities)** | `22.02 ms / poll` (`440.36 ms` / 20 polls) | **`0.708 ms / poll`** (`14.15 ms` / 20 polls) | **`-96.8%` latency (`31.1x` faster; `-99.9%` vs original `628.01 ms`)** | Measured across 20 consecutive polls after initial warmup |
| **Subprocess Spawns & File Reads per Idle Hour (600 polls @ 6s interval)** | `1,800` process forks/hr, `43,800` file reads/hr | **`0` process forks/hr, `0` file reads/hr** | **`-100.0%` background process forks & file reads** | Verified via `subprocess.run` spy (`test_backend.py`) & `os.scandir` stat signatures |
| **Minimized Desktop Window Poll Overhead** | `600` IPC polls/hr (`13.2 s` helper CPU/hr) | **`0` IPC polls/hr (`0.0 ms` CPU/hr)** + instant refresh on restore | **`-100.0%` idle minimized compute & IPC** | Verified via `test_minimized_window_skips_timer_poll_and_refreshes_on_restore` |
| **Operation Log Tailing I/O (`Store.operate` across 200 ticks, `210.9 KiB` log)** | `20.70 MiB` disk reads (`100.5x` amplification), `27.43 ms` CPU | **`210.9 KiB` disk reads (`1.0x` amplification), `11.49 ms` CPU** | **`-99.0%` disk read I/O (`100.5x` reduction) & `-58.1%` CPU** | Measured 200-tick incremental log append with 40 historical logs |
| **Desktop Export Delivery (`deliver_export`, `64 MiB` `.tar.gz` on Btrfs)** | `13.50 ms`, `+64.0 MiB` duplicate physical blocks (`2.0x` storage) | **`0.12 ms`, `+0.0 MiB` duplicate physical blocks (`1.0x` CoW reflink)** | **`-99.1%` delivery time (`112.5x` faster) & `-50.0%` total export storage** | Measured on host `/var/tmp` Btrfs via `os.copy_file_range` vs `shutil.copyfileobj` |
| **`minimal` Workspace Rootfs Storage & `.tar.gz` Export (`apt-get clean`)** | `234.81 MiB` rootfs, `108.86 MiB` `.tar.gz`, `1,994.66 ms` export CPU | **`176.81 MiB` rootfs, `50.83 MiB` `.tar.gz`, `801.59 ms` export CPU** | **`-58.0 MiB` (`-24.7%`) image/snapshot storage, `-53.3%` export size, `-59.8%` export CPU** | Measured with `tar --numeric-owner --acls --xattrs --one-file-system -czf` |
| **Image Provisioning Init (`allocate_image` + `mkfs.ext4`, `4,096 MiB` image)** | `15.14 ms` (`5.75 ms` alloc + `9.39 ms` mkfs), `4,096 MiB` extent churn | **`9.58 ms` (`0.74 ms` alloc + `8.84 ms` mkfs), `67.2 MiB` blocks** | **`-36.7%` init latency (`-87.1%` alloc step) & `-98.4%` peak extent reservation** | Measured on host `/var/tmp` Btrfs for `4,096 MiB` workspace image |
| **Clone Metadata Propagation (`copy_metadata` + overrides)** | `28.74 ms / op` (`23` forks, `4` atomic `.meta` writes) | **`4.68 ms / op` (`3` forks, `1` atomic `.meta` write)** | **`-83.7%` latency (`6.1x` faster) & `-75.0%` metadata file writes** | Measured across 20 runs on a 24-workspace store |
| **CLI Single-Workspace Inspection (`chrootable-manager --inspect`)** | `24.86 ms / call` (`12` subprocess forks) | **`10.38 ms / call` (`2` subprocess forks)** | **`-58.2%` latency (`2.4x` faster) & `-83.3%` subprocess forks** | Measured across 20 runs of `chrootable-manager --inspect workspace-00` |
| **CLI Dashboard Summary (`chrootable-manager --dashboard`, 24 workspaces)** | `18.42 ms / call` (`2` directory/stat passes) | **`15.96 ms / call` (`1` directory/stat pass, `-94.9%` vs original `313.86 ms`)** | **`-13.4%` latency (`-50.0%` image `stat`/`.meta` passes)** | Measured across 10 runs of `chrootable-manager --dashboard` |
