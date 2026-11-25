"""A stdio-only privilege helper. Started explicitly through polkit; no network socket."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import sys
import threading
import traceback

# Works when invoked as an absolute script by pkexec with a clean environment.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from chrootables.backend import DEFAULT_STORE, MAX_MESSAGE_ID, MAX_REQUEST_BYTES, Store, MUTATIONS, OperationCancelled

MAX_PENDING_REQUESTS = 16


def serve(store, input_stream=sys.stdin, output_stream=sys.stdout, *, writable=False):
    """Serve desktop requests on stdio until the input stream closes."""
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
            # Deliberately broad: any failure must still answer the desktop, and
            # the traceback goes to stderr, which the protocol never parses.
            traceback.print_exc(file=sys.stderr)
            code = ("permission_required" if isinstance(error, PermissionError) else
                    "cancelled" if isinstance(error, OperationCancelled) else "operation_failed")
            send({"id": identifier, "event": "result", "ok": False, "error": str(error), "code": code})

    send({"event": "ready", "writable": writable})
    pending = threading.BoundedSemaphore(MAX_PENDING_REQUESTS)
    with ThreadPoolExecutor(max_workers=4) as pool:
        while True:
            line = input_stream.readline(MAX_REQUEST_BYTES + 1)
            if not line:
                break
            if len(line) > MAX_REQUEST_BYTES:
                send({"event": "error", "error": "Request too large."})
                break
            try:
                request = json.loads(line)
                if (not isinstance(request, dict) or type(request.get("id")) is not int
                        or not 0 < request["id"] <= MAX_MESSAGE_ID
                        or not isinstance(request.get("action"), str)
                        or not isinstance(request.get("params", {}), dict)):
                    raise ValueError("Invalid request.")
                # Cancellation only inspects/signals the active process. Handle
                # it on the reader thread so queued disk reads cannot delay it.
                if request["action"] == "cancel":
                    handle(request)
                elif not pending.acquire(blocking=False):
                    send({"id": request["id"], "event": "result", "ok": False,
                          "error": "The workspace helper is busy. Wait for pending requests and try again.",
                          "code": "overloaded"})
                else:
                    try:
                        future = pool.submit(handle, request)
                    except BaseException:
                        pending.release()
                        raise
                    future.add_done_callback(lambda _: pending.release())
            except (ValueError, TypeError) as error:
                send({"event": "error", "error": str(error)})


def main():
    """Parse arguments and serve one store as a stdio helper process."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--store", default=str(DEFAULT_STORE))
    parser.add_argument("--writable", action="store_true")
    args = parser.parse_args()
    if args.writable and os.geteuid() != 0:
        parser.error("Write access requires administrator authentication.")
    os.umask(0o077)
    serve(Store(args.store), writable=args.writable)


if __name__ == "__main__":
    main()
