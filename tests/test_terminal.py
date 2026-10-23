"""Terminal emulation, key mapping, and pty round-trip tests. No root required."""
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest

from chrootables.terminal import (
    QT_KEY_BACKSPACE, QT_KEY_DELETE, QT_KEY_DOWN, QT_KEY_END,
    QT_KEY_ENTER, QT_KEY_HOME, QT_KEY_LEFT, QT_KEY_PAGEUP, QT_KEY_RIGHT,
    QT_KEY_TAB, QT_KEY_UP, PtyProcess, TerminalPane, TerminalWindow, VtScreen,
    key_to_bytes,
)


def pump(application, condition, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            return False
        application.processEvents()
        time.sleep(0.005)
    return True


class VtScreenTests(unittest.TestCase):
    def test_plain_text_cursor_and_wrap(self):
        screen = VtScreen(4, 10)
        screen.feed("hello")
        self.assertEqual(screen.plain().splitlines()[0], "hello")
        self.assertEqual((screen.row, screen.column), (0, 5))
        screen.feed("x" * 12)
        self.assertEqual((screen.row, screen.column), (1, 7))

    def test_carriage_return_line_feed_and_backspace(self):
        screen = VtScreen(3, 20)
        screen.feed("one\r\ntwo")
        self.assertEqual(screen.plain().splitlines()[:2], ["one", "two"])
        screen.feed("\b\b!")
        self.assertEqual(screen.plain().splitlines()[1], "t!o")

    def test_tab_moves_to_next_stop(self):
        screen = VtScreen(2, 40)
        screen.feed("a\tb")
        self.assertEqual(screen.plain().splitlines()[0], "a       b")

    def test_scrolling_drops_top_line(self):
        screen = VtScreen(3, 20)
        for line in range(5):
            screen.feed(f"L{line}\r\n")
        self.assertEqual(screen.plain().splitlines(), ["L3", "L4"])

    def test_cursor_addressing_and_erase(self):
        screen = VtScreen(4, 20)
        screen.feed("abcdef\r\x1b[K")
        self.assertEqual(screen.plain().splitlines()[0], "")
        screen.feed("one two\x1b[1G")
        self.assertEqual((screen.row, screen.column), (0, 0))
        screen.feed("\x1b[2;3Hxyz")
        self.assertEqual((screen.row, screen.column), (1, 5))
        self.assertTrue(screen.plain().splitlines()[1].startswith("  xyz"))
        screen.feed("\x1b[2J")
        self.assertEqual(screen.plain(), "\n\n\n")

    def test_arrow_movement_stops_at_edges(self):
        screen = VtScreen(4, 10)
        screen.feed("\x1b[A\x1b[D")
        self.assertEqual((screen.row, screen.column), (0, 0))

    def test_colors_and_attributes_apply_and_reset(self):
        screen = VtScreen(2, 20)
        screen.feed("\x1b[1;31mred\x1b[0m plain")
        line = screen.lines[0]
        self.assertEqual((line[0][1].fg, line[0][1].flags & 1), (1, 1))
        self.assertEqual(line[4][1].fg, None)

    def test_extended_256_color(self):
        screen = VtScreen(2, 20)
        screen.feed("\x1b[38;5;196mX")
        self.assertEqual(screen.lines[0][0][1].fg, 196)

    def test_alt_screen_saves_and_restores_primary(self):
        screen = VtScreen(4, 20)
        screen.feed("primary text")
        screen.feed("\x1b[?1049h")
        self.assertEqual(screen.plain(), "\n\n\n")
        screen.feed("alternate")
        screen.feed("\x1b[?1049l")
        self.assertEqual(screen.plain().splitlines()[0], "primary text")

    def test_scroll_region_confines_scrolling(self):
        screen = VtScreen(5, 20)
        screen.feed("\x1b[1;2r")
        screen.feed("a\r\nb\r\nc")
        self.assertEqual(screen.plain().splitlines()[:3], ["b", "c", ""])

    def test_insert_and_delete_lines(self):
        screen = VtScreen(4, 20)
        screen.feed("1\r\n2\r\n3")
        screen.feed("\x1b[1;1H\x1b[1L")
        self.assertEqual(screen.plain().splitlines()[:3], ["", "1", "2"])
        screen.feed("\x1b[1;1H\x1b[2M")
        self.assertEqual(screen.plain().splitlines()[:3], ["2", "3", ""])

    def test_osc_titles_and_unknown_sequences_are_ignored(self):
        screen = VtScreen(2, 40)
        screen.feed("\x1b]0;window title\x07after\x1b[9999999ztail")
        self.assertEqual(screen.plain().splitlines()[0], "aftertail")

    def test_partial_escape_sequence_kept_across_feeds(self):
        screen = VtScreen(2, 20)
        screen.feed("a\x1b[")
        screen.feed("2D")
        # 2D moves the cursor left twice; it does not erase.
        self.assertEqual(screen.plain().splitlines()[0], "a")
        self.assertEqual((screen.row, screen.column), (0, 0))

    def test_resize_updates_every_row_width_and_accepts_output(self):
        screen = VtScreen(3, 20)
        screen.feed("kept")
        for rows, columns in ((5, 40), (2, 25)):
            screen.resize(rows, columns)
            self.assertEqual(len(screen.lines), rows)
            self.assertTrue(all(len(line) == columns for line in screen.lines))
            screen.feed(f"\x1b[{rows};{columns}HX")
            self.assertEqual(screen.lines[-1][-1][0], "X")
        self.assertTrue(screen.plain().startswith("kept"))

    def test_resize_alternate_screen_also_resizes_saved_primary(self):
        for rows, columns in ((5, 40), (2, 20)):
            with self.subTest(rows=rows, columns=columns):
                screen = VtScreen(3, 30)
                screen.feed("primary\x1b[?1049h")
                screen.resize(rows, columns)
                screen.feed("\x1b[?1049l")
                self.assertEqual(len(screen.lines), rows)
                self.assertTrue(all(len(line) == columns for line in screen.lines))
                self.assertTrue(screen.plain().startswith("primary"))
                screen.feed(f"\x1b[{rows};1H\nend")
                self.assertIn("end", screen.plain())

    def test_resize_preserves_text(self):
        screen = VtScreen(4, 20)
        screen.feed("hello")
        screen.resize(6, 30)
        self.assertEqual(screen.plain().splitlines()[0], "hello")
        self.assertEqual((screen.rows, screen.columns), (6, 30))


class KeyMappingTests(unittest.TestCase):
    def test_common_keys(self):
        self.assertEqual(key_to_bytes(0x51, "q", 0), b"q")
        self.assertEqual(key_to_bytes(QT_KEY_ENTER, "", 0), b"\r")
        self.assertEqual(key_to_bytes(QT_KEY_TAB, "", 0), b"\t")
        self.assertEqual(key_to_bytes(QT_KEY_BACKSPACE, "", 0), b"\x7f")
        self.assertEqual(key_to_bytes(QT_KEY_UP, "", 0), b"\x1b[A")
        self.assertEqual(key_to_bytes(QT_KEY_DOWN, "", 0), b"\x1b[B")
        self.assertEqual(key_to_bytes(QT_KEY_RIGHT, "", 0), b"\x1b[C")
        self.assertEqual(key_to_bytes(QT_KEY_LEFT, "", 0), b"\x1b[D")
        self.assertEqual(key_to_bytes(QT_KEY_HOME, "", 0), b"\x1b[H")
        self.assertEqual(key_to_bytes(QT_KEY_END, "", 0), b"\x1b[F")
        self.assertEqual(key_to_bytes(QT_KEY_DELETE, "", 0), b"\x1b[3~")
        self.assertEqual(key_to_bytes(QT_KEY_PAGEUP, "", 0), b"\x1b[5~")

    def test_modifiers(self):
        self.assertEqual(key_to_bytes(0x43, "c", ctrl=True), b"\x03")
        self.assertEqual(key_to_bytes(0x41, "a", alt=True), b"\x1ba")
        self.assertEqual(key_to_bytes(QT_KEY_LEFT, "", alt=True), b"\x1b\x1b[D")


class PtyProcessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_failed_start_closes_both_pty_descriptors(self):
        for failure in ("spawn", "window_size"):
            with self.subTest(failure=failure):
                master, slave = os.openpty()
                try:
                    failing_call = ("chrootables.terminal.subprocess.Popen" if failure == "spawn"
                                    else "chrootables.terminal.PtyProcess._set_window_size")
                    with patch("chrootables.terminal.os.openpty", return_value=(master, slave)), \
                            patch(failing_call, side_effect=OSError("launch failed")):
                        with self.assertRaisesRegex(OSError, "launch failed"):
                            PtyProcess(["missing-command"])
                    for descriptor in (master, slave):
                        with self.assertRaises(OSError):
                            os.fstat(descriptor)
                finally:
                    for descriptor in (master, slave):
                        try:
                            os.close(descriptor)
                        except OSError:
                            pass

    def test_eof_reaps_without_waiting_and_invalidates_descriptor(self):
        child = Mock()
        child.poll.return_value = None
        child.wait.side_effect = AssertionError("GUI thread must not wait")
        with patch("chrootables.terminal.subprocess.Popen", return_value=child):
            process = PtyProcess(["fake-child"])
        finished = []
        output = []
        process.finished.connect(finished.append)
        process.output.connect(output.append)
        process._decoder.decode(b"\xe2")
        process._readable()  # The slave is closed, but the child has not exited.
        self.assertIsNone(process.master)
        self.assertEqual(finished, [])
        self.assertEqual(output, ["\ufffd"])
        with patch("chrootables.terminal.os.write") as write, \
                patch.object(process, "_set_window_size") as resize:
            process.write("late input")
            process.resize(24, 80)
        write.assert_not_called()
        resize.assert_not_called()
        child.poll.return_value = 7
        self.assertTrue(pump(self.app, lambda: bool(finished)))
        process._readable()
        process._check_finished()
        self.assertEqual(finished, [7])
        child.wait.assert_not_called()

    def test_round_trip_and_finish(self):
        collected = []
        process = PtyProcess(["/bin/sh", "-c", "echo READY; cat"])
        process.output.connect(collected.append)
        code = []
        process.finished.connect(code.append)
        self.assertTrue(pump(self.app, lambda: "READY" in "".join(collected)))
        process.write("ping\n")
        self.assertTrue(pump(self.app, lambda: "ping" in "".join(collected)))
        process.terminate()
        self.assertTrue(pump(self.app, lambda: bool(code)))

    def test_split_utf8_output_is_reassembled(self):
        collected = []
        payload = "héllo"
        process = PtyProcess(["/bin/sh", "-c", "printf h\\xc3\\xa9llo; sleep 1"])
        process.output.connect(collected.append)
        finished = []
        process.finished.connect(finished.append)
        # Feed the process output in two chunks to the decoder directly.
        raw = payload.encode("utf-8")
        process.output.emit(process._decoder.decode(raw[:2]))
        process.output.emit(process._decoder.decode(raw[2:]))
        self.assertEqual("".join(collected), "héllo")
        process.terminate()
        self.assertTrue(pump(self.app, lambda: bool(finished)))


class TerminalPaneTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_pane_renders_output_and_forwards_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            target = os.path.join(directory, "typed.txt")
            pane = TerminalPane()
            pane.resize(400, 300)
            process = PtyProcess(["/bin/sh", "-c", f"printf 'PROMPT> '; cat > '{target}'"])
            pane.attach(process)
            pane.show()
            self.assertTrue(pump(self.app,
                                 lambda: "PROMPT>" in pane.screen.plain()))
            QTest.keyClicks(pane, "hello")
            QTest.keyClick(pane, Qt.Key.Key_Return)
            self.assertTrue(pump(self.app,
                                 lambda: os.path.exists(target) and
                                 Path(target).read_text().strip() == "hello"))
            process.terminate()
            self.assertTrue(pump(self.app, lambda: process._finished))
            pane.close()


class TerminalWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_close_terminates_children_after_session_leader_exits(self):
        script = "import subprocess; subprocess.Popen(['/bin/sleep', '30']); print('READY', flush=True)"
        window = TerminalWindow([sys.executable, "-c", script], "session")
        window.show()
        self.assertTrue(pump(self.app, lambda: "READY" in window.pane.screen.plain()
                             and window.process.process.poll() is not None))
        self.assertFalse(window.process._finished)
        window.close()
        self.assertTrue(pump(self.app, lambda: window.process._finished))
        self.assertFalse(window.isVisible())

    def test_close_and_escape_keep_window_owned_until_child_is_reaped(self):
        script = ("import os, signal, time; "
                  "signal.signal(signal.SIGTERM, lambda *args: (time.sleep(.15), os._exit(0))); "
                  "print('READY', flush=True); time.sleep(30)")
        for escape in (False, True, False):
            with self.subTest(escape=escape):
                window = TerminalWindow([sys.executable, "-c", script], "session")
                window.show()
                finished = []
                window.finished.connect(finished.append)
                self.assertTrue(pump(self.app, lambda: "READY" in window.pane.screen.plain()))
                if escape:
                    QTest.keyClick(window, Qt.Key.Key_Escape)
                else:
                    window.close()
                self.assertEqual(finished, [])
                self.assertTrue(window.isVisible())
                self.assertTrue(pump(self.app, lambda: bool(finished)))
                self.assertFalse(window.isVisible())
                self.assertTrue(window.process._finished)
                self.assertIsNone(window.process.master)
                self.assertEqual(window.process.process.returncode, 0)
                self.assertEqual(len(finished), 1)

    def test_window_hosts_session_and_close_terminates_it(self):
        window = TerminalWindow(["/bin/sh", "-c", "printf 'READY'; sleep 30"],
                                "Chrootables · lab")
        self.assertEqual(window.windowTitle(), "Chrootables · lab")
        window.show()
        self.assertTrue(pump(self.app, lambda: "READY" in window.pane.screen.plain()))
        self.assertTrue(window.process.running)
        window.close()
        self.assertTrue(pump(self.app, lambda: window.process._finished and not window.isVisible()))


if __name__ == "__main__":
    unittest.main()
