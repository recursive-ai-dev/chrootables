#!/usr/bin/env python3
"""Smoke-test an installed or extracted package using only temporary sample data."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile


def check(prefix):
    application = prefix / "lib/chrootables"
    manager = prefix / "bin/chrootable-manager"
    desktop = prefix / "bin/chrootables-desktop"
    for executable in (manager, desktop):
        if not executable.is_file() or not os.access(executable, os.X_OK):
            raise RuntimeError(f"Missing package executable: {executable}")
    with tempfile.TemporaryDirectory(prefix="chrootables-installed-") as temporary:
        directory = Path(temporary)
        store = directory / "store"
        environment = {**os.environ, "QT_QPA_PLATFORM": "offscreen",
                       "CHROOTABLE_HOME": str(store), "CHROOTABLE_AUTO_INSTALL": "0"}

        def run(*command, **options):
            return subprocess.run(list(map(str, command)), env=environment,
                                  capture_output=True, text=True, timeout=30, **options)

        cli_version = run(manager, "--version", check=True).stdout.strip().split()[-1]
        desktop_version = run(desktop, "--version", check=True).stdout.strip()
        if cli_version != desktop_version:
            raise RuntimeError("Installed desktop and CLI versions disagree.")
        capabilities = json.loads(run(manager, "--capabilities", "--json", check=True).stdout)
        if capabilities["schema_version"] != 1 or not capabilities["profiles"]:
            raise RuntimeError("Installed CLI capabilities are invalid.")
        report = run(manager, "--doctor", "--json")
        if report.returncode not in (0, 1) or json.loads(report.stdout)["schema_version"] != 1:
            raise RuntimeError("Installed host diagnostics failed.")
        broker = run("/usr/bin/python3", "-I", "-S", application / "chrootables/broker.py",
                     "--store", store,
                     input='{"id":1,"action":"inventory"}\n{"id":2,"action":"doctor"}\n', check=True)
        messages = [json.loads(line) for line in broker.stdout.splitlines()]
        results = {message["id"]: message for message in messages if message.get("event") == "result"}
        result = results[1]
        if not result["ok"] or result["data"]["images"]:
            raise RuntimeError("Installed helper cannot read an empty temporary store.")
        if not results[2]["ok"] or results[2]["data"]["schema_version"] != 1:
            raise RuntimeError("Installed helper cannot locate host diagnostics.")
        if store.exists():
            raise RuntimeError("Read-only package checks created a store.")
        screenshot = directory / "desktop.png"
        run(desktop, "--demo", "--store", store, "--screenshot", screenshot, check=True)
        if not screenshot.is_file() or screenshot.stat().st_size == 0:
            raise RuntimeError("Installed desktop demo did not render.")
        print(f"Installed package {cli_version}: CLI, capabilities, diagnostics, helper and desktop passed.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix", type=Path, default=Path("/usr"))
    arguments = parser.parse_args()
    try:
        check(arguments.prefix.resolve())
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        parser.exit(1, f"Installed package check failed: {error}\n")
