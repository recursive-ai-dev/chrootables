"""Asynchronous Qt client for the local stdio helper."""
import json
import sys
from PySide6.QtCore import QObject, QProcess, Signal
from .backend import ROOT


def valid_result(action, data):
    """Check fields consumed by the desktop before invoking its callbacks."""
    def fields(value, strings=(), integers=()):
        return (isinstance(value, dict)
                and all(isinstance(value.get(key), str) for key in strings)
                and all(type(value.get(key)) is int and value[key] >= 0 for key in integers))

    if action == "logs":
        return isinstance(data, str)
    if action == "doctor":
        return fields(data, ("output",)) and type(data.get("ready")) is bool
    if action == "inventory":
        if not fields(data, integers=("free_bytes", "total_bytes")):
            return False
        schemas = {
            "images": (("name", "status", "lifecycle", "suite", "profile", "architecture",
                        "created_at", "last_used_at", "origin"),
                       ("size_mib", "allocated_bytes", "snapshots")),
            "snapshots": (("name", "label", "created_at"), ("size_mib",)),
            "activity": (("time", "action", "name", "status", "log"), ()),
        }
        return all(isinstance(data.get(key), list)
                   and all(fields(item, *schema) for item in data[key])
                   for key, schema in schemas.items())
    return fields(data, integers=("exit_code",))


def valid_message(message):
    if not isinstance(message, dict):
        return False
    event = message.get("event")
    if event == "ready":
        return type(message.get("writable")) is bool
    if event == "error":
        return isinstance(message.get("error"), str)
    identifier = message.get("id")
    if type(identifier) is not int or not 0 < identifier <= 2147483647:
        return False
    if event == "output":
        return isinstance(message.get("text"), str)
    if event == "result" and type(message.get("ok")) is bool:
        return ("data" in message if message["ok"] else
                isinstance(message.get("error"), str)
                and (message.get("code") is None or isinstance(message["code"], str)))
    return False


class BackendError(RuntimeError):
    def __init__(self, message, code=None):
        super().__init__(message)
        self.code = code


class Client(QObject):
    ready = Signal(bool)
    disconnected = Signal(str)
    output = Signal(int, str)

    def __init__(self, store, parent=None):
        super().__init__(parent)
        self.store = str(store)
        self.process = None
        self.callbacks = {}
        self.counter = 0
        self.buffer = b""
        self.writable = False
        self.is_ready = False
        self._failed = False

    def start(self, writable=False):
        self.stop()
        self.buffer = b""
        self._failed = False
        process = QProcess(self)
        self.process = process
        args = ["-I", "-S", str(ROOT / "chrootables" / "broker.py"), "--store", self.store]
        if writable:
            program, args = "pkexec", [sys.executable, *args, "--writable"]
        else:
            program = sys.executable
        process.readyReadStandardOutput.connect(lambda: self.read(process))
        process.errorOccurred.connect(lambda _: self.failure("Could not start the helper. Check that Python and polkit are installed.") if process is self.process else None)
        process.finished.connect(lambda code, status: self.failure("Administrator access was cancelled or the helper closed." if writable else "The workspace helper stopped.") if process is self.process else None)
        process.start(program, args)

    def failure(self, message):
        if self._failed:
            return
        self._failed = True
        self.buffer = b""
        self.is_ready = self.writable = False
        process = self.process
        callbacks, self.callbacks = self.callbacks, {}
        for callback in callbacks.values():
            callback(False, message)
        if process is self.process:
            self.disconnected.emit(message)

    def read(self, process):
        if process is not self.process or self._failed:
            return
        self.buffer += bytes(process.readAllStandardOutput())
        pending_id = None
        pending_chunks = []

        def flush_output():
            nonlocal pending_id, pending_chunks
            if pending_chunks:
                identifier, value = pending_id, "".join(pending_chunks)
                pending_id = None
                pending_chunks = []
                if process is self.process and not self._failed:
                    self.output.emit(identifier, value)

        while process is self.process and not self._failed and b"\n" in self.buffer:
            line, self.buffer = self.buffer.split(b"\n", 1)
            try:
                message = json.loads(line)
            except (ValueError, UnicodeDecodeError):
                self.failure("Invalid response from the workspace helper. Reconnect and try again.")
                return
            if not valid_message(message):
                self.failure("Invalid response from the workspace helper. Reconnect and try again.")
                return
            event = message.get("event")
            if event == "output":
                msg_id = message.get("id")
                if pending_chunks and pending_id != msg_id:
                    flush_output()
                    if process is not self.process or self._failed:
                        return
                pending_id = msg_id
                pending_chunks.append(message.get("text", ""))
                continue
            flush_output()
            if process is not self.process or self._failed:
                return
            if event == "ready":
                self.writable = message["writable"]
                self.is_ready = True
                self.ready.emit(self.writable)
            elif event == "result":
                callback = self.callbacks.pop(message.get("id"), None)
                if callback:
                    callback(message["ok"], message.get("data") if message["ok"] else BackendError(message.get("error"), message.get("code")))
            elif event == "error":
                self.failure(message["error"])
                return
        flush_output()

    def request(self, action, params, callback):
        if not self.is_ready:
            callback(False, "The workspace helper is not connected.")
            return -1
        self.counter += 1
        identifier = self.counter
        try:
            payload = (json.dumps({"id": identifier, "action": action, "params": params}) + "\n").encode()
        except (TypeError, ValueError) as error:
            callback(False, BackendError("Invalid request: " + str(error)))
            return -1

        def checked(ok, data):
            if ok and not valid_result(action, data):
                callback(False, BackendError("Invalid " + action + " response from the workspace helper."))
            else:
                callback(ok, data)

        self.callbacks[identifier] = checked
        if self.process.write(payload) == -1:
            self.failure("Could not send the request to the workspace helper.")
        return identifier

    def stop(self):
        self.is_ready = self.writable = False
        if self.process:
            process, self.process = self.process, None
            process.closeWriteChannel()
            if not process.waitForFinished(1500):
                process.terminate()
                process.waitForFinished(1000)
            process.deleteLater()
        callbacks, self.callbacks = self.callbacks, {}
        for callback in callbacks.values():
            callback(False, "Connection changed. Refresh and try again.")
