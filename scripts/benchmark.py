#!/usr/bin/env python3
"""Check the audited inventory and unchanged-render latency budgets.

The temporary workload contains 24 sparse images, 48 snapshots, 80 journal
entries and real image lock files, including one held lock. Images are inventory
fixtures, not mounted filesystems. No administrator access or downloads are used.

"Fresh inventory" creates a new Store for every sample, so application caches
are empty; filesystem/binary caches are warmed before measurement. This does not
claim to flush the operating system's disk cache. Render timing covers the
synchronous Window.render reconciliation; Qt paints are drained between samples.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import math
import os
from pathlib import Path
import statistics
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def make_fixture(path):
    for part in ("images", "snapshots", "exports", "logs"):
        (path / part).mkdir()
    profiles = ("minimal", "python", "build", "network")
    for index in range(24):
        name = f"workspace-{index:02d}"
        image = path / "images" / (name + ".img")
        meta = (f"lifecycle={'persistent' if index % 2 else 'ephemeral'}\n"
                f"suite=trixie\narchitecture=amd64\nprofile={profiles[index % 4]}\n"
                "created_at=2026-10-01T10:00:00\nlast_used_at=never\norigin=none\n")
        with image.open("wb") as stream:
            stream.truncate(1024 * 1048576)
        Path(str(image) + ".meta").write_text(meta, encoding="utf-8")
        Path(str(image) + ".lock").touch()
        folder = path / "snapshots" / name
        folder.mkdir()
        for number in range(2):
            snapshot = folder / f"checkpoint-{number}.img"
            with snapshot.open("wb") as stream:
                stream.truncate(1024 * 1048576)
            Path(str(snapshot) + ".meta").write_text(
                meta + f"snapshot_at=2026-10-{number + 2:02d}T10:00:00\n", encoding="utf-8")
    activity = []
    for index in range(80):
        log = path / "logs" / f"operation-{index:02d}.log"
        log.write_text("Benchmark operation completed.\n", encoding="utf-8")
        activity.append(f"2026-10-20T10:{index // 60:02d}:{index % 60:02d}\t"
                        f"snapshot\tworkspace-{index % 24:02d}\tok\t{log}\n")
    (path / "activity.tsv").write_text("".join(activity), encoding="utf-8")


def measure(callback, samples, *, between=None):
    timings = []
    for _ in range(samples):
        started = time.perf_counter_ns()
        callback()
        timings.append((time.perf_counter_ns() - started) / 1_000_000)
        if between:
            between()
    ordered = sorted(timings)
    return {"median_ms": statistics.median(timings),
            "p95_ms": ordered[math.ceil(len(ordered) * .95) - 1], "samples": samples}


def benchmark(directory):
    # Isolate QSettings before Qt is imported; no personal settings are read or
    # written, and the ordinary demo window still follows its normal code path.
    os.environ["XDG_CONFIG_HOME"] = str(directory / "config")
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PySide6.QtWidgets import QApplication
    from chrootables.app import Window
    from chrootables.backend import Store

    path = directory / "store"
    path.mkdir()
    make_fixture(path)
    with (path / "images" / "workspace-00.img.lock").open("rb") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        for _ in range(3):
            Store(path).inventory()
        metrics = {"fresh_inventory": {**measure(lambda: Store(path).inventory(), 9),
                                        "limit_ms": 80.0}}
        store = Store(path)
        data = store.inventory()
        counts = {key: len(data[key]) for key in ("images", "snapshots", "activity")}
        if counts != {"images": 24, "snapshots": 48, "activity": 80}:
            raise RuntimeError(f"Benchmark fixture was not fully inventoried: {counts}")
        if next(item for item in data["images"] if item["name"] == "workspace-00")["status"] != "busy":
            raise RuntimeError("Inventory failed to observe the fixture's live lock.")
        for _ in range(3):
            store.inventory()
        metrics["cached_inventory"] = {**measure(store.inventory, 25), "limit_ms": 10.0}

        app = QApplication.instance() or QApplication([])
        window = Window(path, demo=True)
        try:
            window.timer.stop()
            window.task_timer.stop()
            window.data = data
            window.selected = "workspace-00"
            window.render()
            window.show()
            for _ in range(5):
                window.render()
                app.processEvents()
            metrics["unchanged_render"] = {
                **measure(window.render, 51, between=app.processEvents), "limit_ms": 2.0}
        finally:
            window.close()
            app.processEvents()

        # Cached inventory must continue probing live locks. Verify the normal
        # implementation sees this transition; never replace it with test data.
        fcntl.flock(lock, fcntl.LOCK_UN)
        released = store.inventory()
        if next(item for item in released["images"] if item["name"] == "workspace-00")["status"] != "ready":
            raise RuntimeError("Cached inventory failed to observe a released lock.")
    for result in metrics.values():
        result["passed"] = result["median_ms"] <= result["limit_ms"]
    return {"schema_version": 1, "fixture": counts, "metrics": metrics,
            "passed": all(result["passed"] for result in metrics.values())}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="print machine-readable measurements")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="chrootables-benchmark-") as temporary:
        report = benchmark(Path(temporary))
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print("24 workspaces · 48 snapshots · 80 activities · live image locks")
        print("Fresh Store caches; warmed OS cache. Render excludes queued paint events.")
        for key, result in report["metrics"].items():
            print(f"{'PASS' if result['passed'] else 'FAIL'} {key}: "
                  f"median {result['median_ms']:.3f} ms, p95 {result['p95_ms']:.3f} ms "
                  f"(budget {result['limit_ms']:g} ms; {result['samples']} samples)")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:
        print(f"Benchmark failed: {error}", file=sys.stderr)
        sys.exit(1)
