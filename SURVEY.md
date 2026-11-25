Code-Quality Survey — chrootables
P1 — High-value, safe/mechanical
1. Duplicated .meta parsing (Bash) — 3 full copies + 2 partial
- chrootable-manager:108-122 (copy_metadata), 1849-1863 (image_json), 1911-1925 (image_details) contain the identical while IFS='=' read -r mkey mval … case $mkey in lifecycle|suite|architecture|created_at|last_used_at|profile|origin … block.
- Partial copies: 1965-1971 (workspace_summary, lifecycle only) and 2007-2016 (dashboard, lifecycle+profile).
- Fix: add one helper, e.g. meta_load <file> <assoc-array-name> that fills a local declare -A, plus meta_get with default. Replace all five sites. Mechanical, low risk (behavior identical, incl. the legacy .mode fallback at 120-122 / 1861-1863 / 1923-1925 / 1969-1971 / 2014-2016 which must move into the helper).
2. Duplicated image enumeration + stat/mapfile (Bash)
- 1884-1893 (list_images), 1953-1960 (workspace_summary), 1989-1996 (dashboard), 2358-2363 (select_chrootable) repeat for image in "$IMAGE_DIR/"*.img; do [[ -f … && ! -L … ]] || continue; … and the stat -c '%s %b' … | mapfile -t stat_lines idiom.
- Snapshot counting for file in "$SNAPSHOT_DIR/$name/"*.img … repeats at 159-163, 1868, 1978, 2020, 2024.
- Fix: collect_images (fills a global array) + image_stat_lines helper; count_snapshots <name> helper. Low risk.
3. build_command create/import validation duplicated (Python)
- chrootables/backend.py:171-178 (create) and 179-188 (import) repeat the same mode/lifecycle/profile/suite membership checks and env["CHROOTABLE_DEBIAN_SUITE"] = suite.
- Fix: extract _lifecycle_profile_suite(params) returning (mode, profile, suite) or raising; call from both branches. Pure refactor.
4. Dialog widget construction duplicated (Python/Qt)
- chrootables/app.py:129-155 (CreateDialog) and 247-273 (ImportDialog) duplicate profile QComboBox, suite QComboBox, and the two-item lifecycle QComboBox with identical strings.
- Fix: module-level profile_combo(), suite_combo(), lifecycle_combo() factories (like existing label/button/box). Low risk.
5. Overly long functions that should be decomposed
Report (name, range, length):
- app.py Window.build_ui 534-670 (~137 lines) — split into sidebar/header/task-panel/footer builders.
- backend.py Store._inventory 498-606 (~110) — extract _scan_images, _scan_snapshots, _read_activity.
- terminal.py VtScreen._apply_csi 274-381 (~109) — split cursor-movement vs erase vs scroll vs mode branches.
- app.py Window.build_detail_card 1066-1164 (~100).
- chrootable-manager doctor 2095-2224 (~130) and worker_main 1691-1818 (~128).
- backend.py deliver_export 301-398 (~100; ~70 lines are an embedded Python script string — extract to a module constant).
- terminal.py feed 539-624 (~87), _apply_sgr 383-448 (66), paintEvent 978-1042 (66).
- Fix: mechanical extraction of cohesive blocks into helpers; no behavior change. (Slightly larger than the others but still low-risk if done as pure moves.)
P2 — Clear improvements, low risk
6. Dead code
- chrootables/app.py:59-65 clear(layout) is never called (only self-recursive at 65). Remove.
- chrootables/terminal.py:99-100 Style.replaced() appears unused (_apply_sgr builds Style(...) directly at 448). Verify then remove.
- app.py:1244 select_workspace(self, item, previous=None) — previous is unused in the body; it exists only to absorb Qt's currentItemChanged(current, previous) arity. Keep but rename to _previous to signal intent (or document).
7. Magic numbers / strings → named constants
- chrootable-manager: 1048576 appears 28× (e.g. 31, 991, 1051, 1128-1149, 1288-1290), plus 512, 1099511627776 (1 TiB bytes, 1135/1143/1179/1189), 67108848 (1135/1189). Add MAX_SIZE_MIB=1048576, MIN_SIZE_MIB=512, MAX_SIZE_BYTES=1099511627776, MAX_ENTRIES=67108848 near the top and replace.
- Protocol limits duplicated without sharing: client.py:8 MAX_REQUEST_BYTES=65536 vs broker.py:72,75 literal 65537/65536; 2147483647 in client.py:103 and broker.py:81. Hoist to one shared constant module (or backend.py).
- Default store path hard-coded in 3 places: backend.py:24, broker.py:106, chrootable-manager:8. Reference backend.DEFAULT_STORE from broker; keep CLI env default but add a comment tying them.
- app.py literals: 4000 (notes limit, 450/658/1316/1323), 240 (preview, 1283), 93 (row height, 1219), 1500/1000 (timeouts 263-265 client + 1761), 1800 (1885), 6000 (522). Name them (e.g. NOTES_MAX_CHARS, ROW_HEIGHT).
8. Broad / swallowed exceptions (mostly documented — tighten where cheap)
- broker.py:63 except Exception as error: — top-level request guard; acceptable but very broad. Consider enumerating expected types and re-raising truly unexpected ones, or at least log the traceback.
- backend.py:274, 691, 705, 739, 748 except OSError: pass — intentional (log may be absent/pruned) and commented; keep but ensure each has the explanatory comment (274 does; 691/705/739/748 are terse).
- terminal.py:777, 831, 840 except OSError: — intentional pty/IO handling; fine.
- chrootable-manager:1604 target=$(guest_resolve …) || return 0 swallows a resolver failure and returns success — add a comment (it is non-fatal by design) to avoid looking like a bug.
9. Docstring gaps (public API)
- backend.py: name (87), size (93), operation_id (100), export_name (106), keep_count (112), metadata (401), terminal_command (1027) lack docstrings; Store class (448) and methods environment/inventory/logs/doctor/cancel_operation/operate lack them.
- client.py: valid_message (94), BackendError (114), Client (120) and its methods lack docstrings.
- broker.py: serve (19), main (104) lack docstrings.
- app.py: Window (456) and most public methods lack docstrings; helpers when/glyph (77/89) lack them.
- Fix: add one-line docstrings; purely additive.
10. Type-annotation gap (systemic)
- The package uses from __future__ import annotations (backend/terminal/app/broker/scripts) but no function or attribute annotations anywhere. This is a codebase-wide gap, not a local fix. Recommend a separate, incremental effort (annotate public API first: backend validators, client, broker). Flag as larger/risky to do mechanically — do not bundle with the polish pass.
11. CLI ↔ backend drift
- Default suite mismatch: CLI defaults DEBIAN_SUITE=stable (chrootable-manager:9), but backend.build_command hard-codes "trixie" (backend.py:162, 174, 182). Capabilities only advertise trixie/bookworm (chrootable-manager:742), so the desktop can never send stable. Align the default (pick one policy) and document.
- Advertised-but-unused capabilities: CLI emits session_options, features, storage, operation_events (chrootable-manager:744-747) but load_capabilities (backend.py:48-77) validates only schema_version/limits/profiles/suites. Either consume or drop the extra keys.
- Hard-coded limit text: backend.py:96 message says "between 512 MiB and 1 TiB" while the real bounds come from CAPABILITIES["limits"] — derive the message from capabilities to avoid future drift.
12. Long parameter lists / deep nesting (smells)
- backend.py:1000 session_command(... 7 params) and 1027 terminal_command(... 7 params) — consider a small options dataclass (behavior-neutral).
- app.py:321 ValueDialog.__init__ (8 params) — acceptable for a generic dialog but borderline.
- Deep nesting: backend.py _inventory (498-606) and terminal.py feed (539-624) — improves naturally once decomposed (see P1 #5).
P3 — Minor / polish
- Import ordering: mostly stdlib-then-third-party and alphabetical, but a few out-of-order names (e.g. app.py:15 QProcess last; terminal.py:20-25 fine). Cosmetic only.
- Repeated error strings (Bash): "Size must be a whole number from 512 to 1048576 MiB." at 991/1262 (and UI variants 2288/2390); "Lifecycle must be persistent or ephemeral." at 992/1260/1386; "Unknown profile: …" at 858/993/1261. Centralize as err_* variables or a validate_create_args helper.
- app.py:1715 open_terminal(self, checked=False, …) — checked is only there for the clicked signal; rename _checked to mark intentional.
- backend.py:204/225 default limit=256*1024 and _OperationLogTail 4*256*1024/256*1024 (266/268) — name the chunk-size constants.
- style.py is clean and self-contained; scaled_style (66) is the only logic and is fine.
- scripts/*.py import hygiene is clean (no unused imports found); package_support.py/appimage_runtime.py are well-factored and docstringed.
Already clean (do not touch)
- No mutable default arguments anywhere (checked all def signatures).
- No unused imports in chrootables/*.py or scripts/*.py (verified each imported symbol is referenced).
- No TODO/FIXME/XXX/HACK markers (all XXXXXX hits are mktemp templates).
- No bare except: (all handlers name exception types).
- broker.py, client.py, style.py, package_support.py, appimage_runtime.py are tightly scoped and well-commented.
- Comments throughout are explanatory (not commented-out code); no commented-out blocks to remove.
Risk notes
- Lowest risk (do first): P1 #1–#4, P2 #6–#9, P3 items — pure moves/additions.
- Moderate (still safe but larger diffs): P1 #5 decompositions, P2 #11 drift alignment (changes a default — confirm intended policy before changing).
- Defer / separate effort: P2 #10 (type annotations) — large, cross-cutting; not a mechanical polish item.
