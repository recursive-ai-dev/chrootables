"""Failure and reconnection coverage for the local helper protocol."""
import json
import time
import os
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication
from chrootables.client import Client, valid_result


class ClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.client = Client("/tmp/test-store")
        self.process = Mock()
        self.process.write.return_value = 1
        self.client.process = self.process
        self.client.is_ready = True
        self.addCleanup(self.client.stop)

    def read(self, *messages):
        self.process.readAllStandardOutput.return_value = b"".join(
            (json.dumps(message) + "\n").encode() for message in messages)
        self.client.read(self.process)

    def test_malformed_envelopes_fail_pending_requests_once(self):
        invalid = [None, [], {}, {"event": "ready"},
                   {"event": "ready", "writable": "false"},
                   {"event": "output", "id": 1, "text": None},
                   {"event": "phase", "id": 1, "text": []},
                   {"event": "output", "id": [], "text": "bad"},
                   {"event": "output", "id": True, "text": "bad"},
                   {"event": "output", "id": 2**40, "text": "bad"},
                   {"event": "result", "id": 1, "ok": 1},
                   {"event": "result", "id": 1, "ok": False, "error": []}]
        for message in invalid:
            with self.subTest(message=message):
                self.client._failed = False
                self.client.is_ready = True
                callback = Mock()
                self.client.request("logs", {}, callback)
                self.read(message, {"event": "ready", "writable": True})
                self.client.failure("helper exited")
                callback.assert_called_once()
                self.assertFalse(callback.call_args.args[0])
                self.assertFalse(self.client.is_ready)
                self.assertFalse(self.client.writable)
                self.assertEqual(self.client.callbacks, {})

    def test_invalid_json_and_helper_error_are_visible(self):
        for payload in (b"invalid json\n", b'{"event":"error","error":"Request too large."}\n'):
            self.client._failed = False
            self.client.is_ready = True
            callback = Mock()
            self.client.request("logs", {}, callback)
            self.process.readAllStandardOutput.return_value = payload
            self.client.read(self.process)
            callback.assert_called_once()
            self.assertFalse(callback.call_args.args[0])
            self.assertTrue(str(callback.call_args.args[1]))

    def test_malformed_result_data_does_not_reach_ui_as_success(self):
        inventory = json.loads((Path(__file__).resolve().parents[1] / "assets/demo.json").read_text())
        inventory["images"][0]["profile"] = []
        for action, data in (("inventory", None), ("inventory", inventory),
                             ("doctor", {"ready": "yes", "output": "ok"}),
                             ("logs", {}), ("create", {"exit_code": False})):
            with self.subTest(action=action):
                callback = Mock()
                identifier = self.client.request(action, {}, callback)
                self.read({"event": "result", "id": identifier, "ok": True, "data": data})
                callback.assert_called_once()
                self.assertFalse(callback.call_args.args[0])
                self.assertIn("Invalid", str(callback.call_args.args[1]))

    def test_disconnect_fails_all_pending_requests_once(self):
        callbacks = [Mock(), Mock()]
        for callback in callbacks:
            self.client.request("inventory", {}, callback)
        disconnected = Mock()
        self.client.disconnected.connect(disconnected)
        self.client.failure("helper exited")
        self.client.failure("helper exited again")
        for callback in callbacks:
            callback.assert_called_once_with(False, "helper exited")
        disconnected.assert_called_once_with("helper exited")

    def test_doctor_nested_data_and_counts_are_checked_before_callback(self):
        invalid = [
            {"checks": None}, {"checks": [{}]},
            {"checks": [{"id": "command:tar", "status": []}]},
            {"checks": [{"id": "command:tar", "status": "unknown"}]},
            {"checks": [{"id": "command:tar", "status": "error"}]},
            {"checks": [{"id": "command:tar", "status": "ok", "remedy": []}]},
            {"checks": [{"id": "same", "status": "ok"}] * 2},
            {"errors": False}, {"warnings": -1}, {"errors": "0"},
            {"checks": [], "warnings": 1}, {"errors": 1},
        ]
        for extra in invalid:
            with self.subTest(extra=extra):
                callback = Mock()
                identifier = self.client.request("doctor", {}, callback)
                self.read({"event": "result", "id": identifier, "ok": True,
                           "data": {"ready": True, "output": "Host health", **extra}})
                callback.assert_called_once()
                self.assertFalse(callback.call_args.args[0])
                self.assertIn("Invalid doctor response", str(callback.call_args.args[1]))
        self.assertTrue(valid_result("doctor", {"ready": False, "output": "Missing tar", "errors": 1,
            "warnings": 0, "checks": [{"id": "command:tar", "status": "error"}]}))
        self.assertTrue(valid_result("doctor", {"ready": True, "output": "Ready"}))

    def test_inventory_optional_fields_match_desktop_consumers(self):
        base = json.loads((Path(__file__).resolve().parents[1] / "assets/demo.json").read_text())
        base["exports"], base["archives"] = [], []
        fixture = json.dumps(base)
        operation = {"id": "a" * 32, "action": "clone", "name": "lab", "status": "interrupted",
                     "stage": "copy", "log": "/tmp/log", "active": False, "recoverable": False}
        for key, value in (("discardable", "yes"), ("invalid", "yes"), ("reclaimable_bytes", []),
                           ("reclaimable_bytes", True), ("reclaimable_bytes", -1)):
            with self.subTest(key=key, value=value):
                inventory = json.loads(fixture)
                inventory["operations"] = [{**operation, key: value}]
                self.assertFalse(valid_result("inventory", inventory))
        inventory = json.loads(fixture)
        inventory["operations"] = [{**operation, "discardable": True, "reclaimable_bytes": 1024}]
        self.assertTrue(valid_result("inventory", inventory))
        inventory["snapshots"][0]["orphan"] = "false"
        self.assertFalse(valid_result("inventory", inventory))
        inventory = json.loads(fixture)
        inventory["images"][0]["path"] = []
        self.assertFalse(valid_result("inventory", inventory))

    def test_reconnection_from_callback_ignores_remaining_old_messages(self):
        received = []
        old = self.process
        identifier = self.client.request("logs", {}, lambda *args: self.client.start())
        self.client.ready.connect(received.append)
        with patch("chrootables.client.QProcess") as process_class:
            self.read({"event": "result", "id": identifier, "ok": True, "data": "log"},
                      {"event": "ready", "writable": True})
            self.assertIs(self.client.process, process_class.return_value)
        self.assertEqual(received, [])
        self.assertFalse(self.client.is_ready)
        old.readAllStandardOutput.return_value = b'{"event":"ready","writable":true}\n'
        self.client.read(old)
        self.assertFalse(self.client.is_ready)

    def test_partial_messages_are_buffered(self):
        callback = Mock()
        identifier = self.client.request("logs", {}, callback)
        payload = (json.dumps({"event": "result", "id": identifier, "ok": True, "data": "log"}) + "\n").encode()
        self.process.readAllStandardOutput.return_value = payload[:15]
        self.client.read(self.process)
        callback.assert_not_called()
        self.process.readAllStandardOutput.return_value = payload[15:]
        self.client.read(self.process)
        callback.assert_called_once_with(True, "log")

    def test_phase_updates_do_not_consume_operation_callback(self):
        callback, phase = Mock(), Mock()
        self.client.phase.connect(phase)
        identifier = self.client.request("clone", {"name": "lab", "target": "copy"}, callback)
        self.read({"event": "phase", "id": identifier, "text": "Copying image"})
        phase.assert_called_once_with(identifier, "Copying image")
        callback.assert_not_called()
        self.read({"event": "result", "id": identifier, "ok": True, "data": {"exit_code": 0}})
        callback.assert_called_once_with(True, {"exit_code": 0})

    def test_write_failure_and_unserializable_request_do_not_leak_callbacks(self):
        callback = Mock()
        self.client.request("logs", {"invalid": object()}, callback)
        self.assertFalse(callback.call_args.args[0])
        self.process.write.assert_not_called()
        self.assertEqual(self.client.callbacks, {})
        callback.reset_mock()
        self.process.write.return_value = -1
        self.client.request("logs", {}, callback)
        self.assertFalse(callback.call_args.args[0])
        self.assertEqual(self.client.callbacks, {})
        self.assertFalse(self.client.is_ready)


class ClientLineHandlingTests(unittest.TestCase):
    def setUp(self):
        self.client = Client("/tmp/store")
        self.process = Mock()
        self.client.process = self.process
        self.client.is_ready = True

    def test_nested_read_from_a_callback_keeps_line_order(self):
        order = []
        chunks = [b'{"event":"phase","id":1,"text":"first"}\n{"event":"phase","id":1,"text":"second"}\n{"event":"phase","id":1,"text":"thi',
                  b'rd"}\n']

        def phase(_identifier, text):
            order.append(text)
            if text == "first":
                # A dialog's nested event loop delivers the next chunk here.
                self.process.readAllStandardOutput.return_value = chunks[1]
                self.client.read(self.process)

        self.client.phase.connect(phase)
        self.process.readAllStandardOutput.return_value = chunks[0]
        self.client.read(self.process)
        self.assertEqual(order, ["first", "second", "third"])
        self.assertEqual(self.client.buffer, b"")

    def test_partial_line_waits_for_its_remainder_on_large_bursts(self):
        seen = []
        self.client.output.connect(lambda _identifier, text: seen.append(text))
        message = json.dumps({"event": "output", "id": 1, "text": "x"}).encode() + b"\n"
        self.process.readAllStandardOutput.return_value = message * 200000 + message[:5]
        started = time.monotonic()
        self.client.read(self.process)
        self.assertLess(time.monotonic() - started, 20)
        self.assertEqual(self.client.buffer, message[:5])
        self.assertEqual("".join(seen), "x" * 200000)
