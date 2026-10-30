"""A stdio-only privilege helper. Started explicitly through polkit; no network socket."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import sys
import threading

# Works when invoked as an absolute script by pkexec with a clean environment.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from chrootables.backend import Store, MUTATIONS


def serve(store, input_stream=sys.stdin, output_stream=sys.stdout, *, writable=False):
    output_lock = threading.Lock()
    mutation_lock = threading.Lock()
    def send(message):
        with output_lock:
            try:
                output_stream.write(json.dumps(message) + "\n")
                output_stream.flush()
            except (BrokenPipeError, OSError):
                pass  # Finish filesystem operations even if the desktop disconnects.

    def handle(request):
        identifier = request.get("id")
        action = request.get("action")
        params = request.get("params", {})
        try:
            if action == "inventory":
                data = store.inventory()
            elif action == "doctor":
                data = store.doctor()
            elif action == "logs":
                data = store.logs(params.get("name"), params.get("log"))
            elif action == "cancel":
                if not writable:
                    raise PermissionError("Unlock the workspace store to make changes.")
                data = store.cancel_operation()
            elif action in MUTATIONS:
                if not writable:
                    raise PermissionError("Unlock the workspace store to make changes.")
                if not mutation_lock.acquire(blocking=False):
                    raise RuntimeError("Another operation is running. Wait for it to finish.")
                try:
                    code = store.operate(
                        action, params,
                        lambda value: send({"id": identifier, "event": "output", "text": value}),
                        progress=lambda value: send({"id": identifier, "event": "phase", "text": value}))
                    if code:
                        raise RuntimeError(f"{action.replace('-', ' ').capitalize()} failed (exit {code}). See the task output for details.")
                    data = {"exit_code": code}
                finally:
                    mutation_lock.release()
            else:
                raise ValueError("Unsupported desktop request.")
            send({"id": identifier, "event": "result", "ok": True, "data": data})
        except Exception as error:
            send({"id": identifier, "event": "result", "ok": False, "error": str(error),
                  "code": "permission_required" if isinstance(error, PermissionError) else "operation_failed"})

    send({"event": "ready", "writable": writable})
    with ThreadPoolExecutor(max_workers=4) as pool:
        while True:
            line = input_stream.readline(65537)
            if not line:
                break
            if len(line) > 65536:
                send({"event": "error", "error": "Request too large."})
                break
            try:
                request = json.loads(line)
                if not isinstance(request, dict) or not isinstance(request.get("id"), int):
                    raise ValueError("Invalid request.")
                pool.submit(handle, request)
            except (ValueError, TypeError) as error:
                send({"event": "error", "error": str(error)})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--store", default="/var/lib/chrootable-manager")
    parser.add_argument("--writable", action="store_true")
    args = parser.parse_args()
    if args.writable and os.geteuid() != 0:
        parser.error("Write access requires administrator authentication.")
    os.umask(0o077)
    serve(Store(args.store), writable=args.writable)


if __name__ == "__main__":
    main()
