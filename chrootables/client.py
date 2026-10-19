"""Asynchronous Qt client for the local stdio helper."""
import json
import sys
from PySide6.QtCore import QObject, QProcess, Signal
from .backend import ROOT


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

    def start(self, writable=False):
        self.stop()
        self.buffer = b""
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
        self.is_ready = self.writable = False
        callbacks, self.callbacks = self.callbacks, {}
        for callback in callbacks.values():
            callback(False, message)
        self.disconnected.emit(message)

    def read(self, process):
        if process is not self.process:
            return
        self.buffer += bytes(process.readAllStandardOutput())
        pending_id = None
        pending_chunks = []

        def flush_output():
            nonlocal pending_id, pending_chunks
            if pending_chunks:
                self.output.emit(pending_id, "".join(pending_chunks))
                pending_id = None
                pending_chunks = []

        while b"\n" in self.buffer:
            line, self.buffer = self.buffer.split(b"\n", 1)
            try:
                message = json.loads(line)
            except (ValueError, UnicodeDecodeError):
                continue
            if not isinstance(message, dict):
                continue
            event = message.get("event")
            if event == "output":
                msg_id = message.get("id")
                if pending_chunks and pending_id != msg_id:
                    flush_output()
                pending_id = msg_id
                pending_chunks.append(message.get("text", ""))
                continue
            flush_output()
            if event == "ready":
                self.writable = message["writable"]
                self.is_ready = True
                self.ready.emit(self.writable)
            elif event == "result":
                callback = self.callbacks.pop(message.get("id"), None)
                if callback:
                    callback(message["ok"], message.get("data") if message["ok"] else BackendError(message.get("error"), message.get("code")))
        flush_output()

    def request(self, action, params, callback):
        if not self.is_ready:
            callback(False, "The workspace helper is not connected.")
            return -1
        self.counter += 1
        identifier = self.counter
        self.callbacks[identifier] = callback
        self.process.write((json.dumps({"id": identifier, "action": action, "params": params}) + "\n").encode())
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
