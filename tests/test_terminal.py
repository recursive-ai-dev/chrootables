"""Terminal emulation, key mapping, and pty round-trip tests. No root required."""
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest

from chrootables.terminal import (
    BOLD, DIM, STRIKE,
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

    def test_unicode_combining_and_wide_characters_keep_cell_positions(self):
        screen = VtScreen(3, 6)
        screen.feed("Ae")
        screen.feed("\u0301界B")
        self.assertEqual(screen.lines[0][1][0], "e\u0301")
        self.assertEqual(screen.lines[0][2][0], "界")
        self.assertEqual(screen.lines[0][3][0], "")
        self.assertEqual(screen.column, 5)
        screen.feed("界")
        self.assertEqual((screen.row, screen.column), (1, 2))
        self.assertEqual(screen.plain().splitlines()[:2], ["Ae\u0301界B", "界"])

    def test_wide_overwrite_erase_and_edit_do_not_leave_half_characters(self):
        screen = VtScreen(3, 20)
        screen.feed("A界B\x1b[1;3Hx")
        self.assertEqual(screen.plain().splitlines()[0], "A xB")
        screen.feed("\rA界B\x1b[1;3H\x1b[X")
        self.assertEqual(screen.plain().splitlines()[0], "A  B")
        screen.feed("\rA界B\x1b[1;3H\x1b[@")
        self.assertNotIn("界", screen.plain())
        screen.feed("\rA界B\x1b[1;3H\x1b[P")
        self.assertNotIn("界", screen.plain())

    def test_resize_clips_entire_wide_character_and_preserves_combining_text(self):
        screen = VtScreen(3, 30)
        screen.feed("e\u0301" + "x" * 18 + "界")
        screen.resize(3, 20)
        self.assertEqual(screen.lines[0][0][0], "e\u0301")
        self.assertEqual(screen.lines[0][-1][0], " ")
        self.assertNotIn("界", screen.plain())

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

    def test_scrollback_is_bounded_and_viewport_preserves_order(self):
        screen = VtScreen(3, 20, scrollback=4)
        for line in range(8):
            screen.feed(f"L{line}\r\n")
        self.assertEqual(len(screen.history), 4)
        self.assertEqual(screen.history_count, 6)
        visible = lambda offset: ["".join(cell[0] for cell in line).rstrip()
                                  for line in screen.viewport(offset)]
        self.assertEqual(visible(0), ["L6", "L7", ""])
        self.assertEqual(visible(2), ["L4", "L5", "L6"])
        self.assertEqual(visible(999), ["L2", "L3", "L4"])

    def test_scrollback_excludes_alt_screen_and_partial_scroll_regions(self):
        screen = VtScreen(3, 20)
        screen.feed("original\r\n\r\n\r\n")
        self.assertEqual(len(screen.history), 1)
        screen.feed("\x1b[?1049h" + "alternate\r\n" * 6)
        self.assertEqual(len(screen.history), 1)
        self.assertIs(screen.viewport(1), screen.lines)
        screen.feed("\x1b[?1049l\x1b[1;2r\x1b[2;1H\n\n")
        self.assertEqual(len(screen.history), 1)

    def test_history_resizes_for_display_and_clear_scrollback_keeps_screen(self):
        screen = VtScreen(3, 40)
        screen.feed("x" * 35 + "\r\nkept\r\n\r\n")
        screen.resize(3, 20)
        self.assertTrue(all(len(line) == 20 for line in screen.viewport(1)))
        screen.resize(3, 50)
        self.assertEqual("".join(cell[0] for cell in screen.viewport(1)[0]).rstrip(), "x" * 35)
        before = screen.plain()
        screen.feed("\x1b[3J")
        self.assertFalse(screen.history)
        self.assertEqual(screen.plain(), before)

    def test_bracketed_paste_modes_and_reset(self):
        screen = VtScreen(3, 20)
        self.assertFalse(screen.bracketed_paste)
        screen.feed("\x1b[?2004h")
        self.assertTrue(screen.bracketed_paste)
        screen.feed("\x1b[?2004l")
        self.assertFalse(screen.bracketed_paste)
        screen.feed("\x1b[?2004h\x1b[?1049htext\x1bc")
        self.assertFalse(screen.bracketed_paste)
        self.assertFalse(screen._alt_active)
        self.assertIsNone(screen._primary)

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

    def test_dim_strikethrough_sgr22_and_rep_csi_b(self):
        screen = VtScreen(2, 20)
        screen.feed("\x1b[1;2;9mA\x1b[22mB\x1b[29mC\x1b[3b")
        line = screen.lines[0]
        self.assertEqual(line[0][1].flags & (BOLD | DIM | STRIKE), BOLD | DIM | STRIKE)
        self.assertEqual(line[1][1].flags & (BOLD | DIM | STRIKE), STRIKE)
        self.assertEqual(line[2][1].flags & (BOLD | DIM | STRIKE), 0)
        self.assertEqual(screen.plain().splitlines()[0], "ABCCCC")

    def test_extended_256_color(self):
        screen = VtScreen(2, 20)
        screen.feed("\x1b[38;5;196mX")
        self.assertEqual(screen.lines[0][0][1].fg, 196)

    def test_true_color_channels_stay_within_renderable_range(self):
        screen = VtScreen(2, 20)
        screen.feed("\x1b[38;2;99999999999999999999;128;0mX")
        self.assertEqual(screen.lines[0][0][1].fg, (255, 128, 0))

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

    def test_saved_cursor_restore_after_shrink(self):
        for save, restore in (("\x1b7", "\x1b8"), ("\x1b[s", "\x1b[u")):
            with self.subTest(restore=restore):
                screen = VtScreen(8, 40)
                screen.feed("\x1b[8;40H" + save)
                screen.resize(2, 20)
                screen.feed(restore + "X")
                self.assertEqual(screen.lines[-1][-1][0], "X")

    def test_unsupported_csi_is_consumed_without_changing_screen(self):
        for params, final in (("?2", "J"), ("1?2", "A"), ("²", "A"),
                              ("38:2:1:2:3", "m"), ("9" * 5000, "A"),
                              ("9" * 5000, "m"), ("?" + "9" * 5000, "h")):
            with self.subTest(params=params[:30], final=final):
                screen = VtScreen(2, 20)
                screen.feed("kept\x1b[")
                # Exercise parameters split across PTY reads too.
                for start in range(0, len(params), 100):
                    screen.feed(params[start:start + 100])
                    self.assertLessEqual(len(screen._params), 256)
                screen.feed(final + "tail")
                self.assertEqual(screen.plain().splitlines()[0], "kepttail")
                self.assertIsNone(screen.lines[0][4][1].fg)

    def test_erase_to_start_of_display_clears_previous_rows(self):
        screen = VtScreen(4, 20)
        screen.feed("first\r\nsecond\r\nthird\r\nlast")
        screen.feed("\x1b[3;3H\x1b[1J")
        self.assertEqual(screen.plain().splitlines(), ["", "", "   rd", "last"])

    def test_cancelled_csi_recovers_for_text_and_new_sequences(self):
        for params in ("12", "9" * 5000):
            for ending in ("\x18", "\x1a", "\x1b[0m"):
                with self.subTest(length=len(params), ending=ending):
                    screen = VtScreen(2, 20)
                    screen.feed("kept\x1b[" + params)
                    screen.feed(ending + "tail")
                    self.assertEqual(screen.plain().splitlines()[0], "kepttail")


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
        self.assertEqual(key_to_bytes(QT_KEY_LEFT, "", ctrl=True), b"\x1b[1;5D")
        self.assertEqual(key_to_bytes(QT_KEY_RIGHT, "", ctrl=True), b"\x1b[1;5C")
        self.assertEqual(key_to_bytes(QT_KEY_UP, "", ctrl=True), b"\x1b[1;5A")
        self.assertEqual(key_to_bytes(QT_KEY_DOWN, "", ctrl=True), b"\x1b[1;5B")


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

    def test_partial_writes_and_backpressure_retain_input_in_order(self):
        child = Mock()
        child.poll.return_value = None
        with patch("chrootables.terminal.subprocess.Popen", return_value=child):
            process = PtyProcess(["fake-child"])
        with patch("chrootables.terminal.os.write", side_effect=[2, BlockingIOError(), 4]) as write:
            process.write(b"abc")
            self.assertEqual(process._pending_input, b"c")
            process.write(b"def")
            self.assertEqual(process._pending_input, b"cdef")
            process._flush_input()
            self.assertEqual(process._pending_input, b"")
            self.assertEqual([bytes(call.args[1]) for call in write.call_args_list],
                             [b"abc", b"cdef", b"cdef"])
            self.assertFalse(process._write_notifier.isEnabled())
        process._readable()  # No slave: close the mock session's descriptor.
        process._reaper.stop()

    def test_large_paste_does_not_block_gui_when_guest_stops_reading(self):
        process = PtyProcess(["/bin/sh", "-c", "printf READY; sleep 30"])
        output = []
        process.output.connect(output.append)
        self.assertTrue(pump(self.app, lambda: "READY" in "".join(output)))
        started = time.monotonic()
        process.write(b"x" * 500000)
        self.assertLess(time.monotonic() - started, .5)
        self.assertTrue(process._pending_input)
        process.terminate()
        self.assertTrue(pump(self.app, lambda: process._finished))
        self.assertFalse(process._pending_input)

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

    def test_256_color_palette_and_hidden_cursor_are_rendered(self):
        pane = TerminalPane()
        self.addCleanup(pane.close)
        self.assertEqual(pane._brush(0, "#ffffff").getRgb()[:3], (0, 0, 0))
        self.assertEqual(pane._brush(196, "#ffffff").getRgb()[:3], (255, 0, 0))
        self.assertEqual(pane._brush(46, "#ffffff").getRgb()[:3], (0, 255, 0))
        self.assertEqual(pane._brush(232, "#ffffff").getRgb()[:3], (8, 8, 8))
        self.assertEqual(pane._brush(255, "#ffffff").getRgb()[:3], (238, 238, 238))
        pane.resize(400, 100)
        pane.show()
        self.app.processEvents()
        pane.process = Mock(running=True)
        pane._cursor_shown = True
        pane.screen.feed("abc")
        x = pane.screen.column * pane.cell_width
        self.assertEqual(pane.grab().toImage().pixelColor(x, 0).name(), "#e6e6e2")
        pane.screen.feed("\x1b[?25l")
        self.assertEqual(pane.grab().toImage().pixelColor(x, 0).name(), "#1c1c1c")
        pane.screen.feed("\r\x1b[7m ")
        self.assertEqual(pane.grab().toImage().pixelColor(0, 0).name(), "#e6e6e2")

    def test_attributes_reach_painter_and_wide_selection_copies_once(self):
        pane = TerminalPane()
        self.addCleanup(pane.close)
        pane.screen.feed("\x1b[1;2;3;4;9m界e\u0301")
        with patch("chrootables.terminal.QPainter") as painter:
            pane.paintEvent(None)
        fonts = [call.args[0] for call in painter.return_value.setFont.call_args_list]
        self.assertTrue(any(font.bold() and font.italic() and font.underline() and font.strikeOut() for font in fonts))
        pens = [call.args[0] for call in painter.return_value.setPen.call_args_list]
        self.assertTrue(any(pen.red() < 230 for pen in pens))
        pane._selection_start, pane._selection_end = (0, 0), (0, 3)
        self.assertEqual(pane.selected_text(), "界e\u0301")
        pane._selection_start, pane._selection_end = (0, 1), (0, 2)
        self.assertEqual(pane.selected_text(), "界")

    def test_scrollback_search_and_clear_scrollback(self):
        pane = TerminalPane()
        self.addCleanup(pane.close)
        pane.screen = VtScreen(3, 20, scrollback=10)
        pane._on_output("alpha needle\r\nbeta\r\ngamma\r\ndelta\r\nepsilon needle\r\n")
        self.assertTrue(pane.find_text("needle", forward=True))
        self.assertGreater(pane.scroll_offset, 0)
        self.assertEqual(pane.selected_text(), "needle")
        self.assertTrue(pane.find_text("needle", forward=True))
        self.assertEqual(pane.scroll_offset, 0)
        self.assertEqual(pane.selected_text(), "needle")
        self.assertFalse(pane.find_text("nonexistent"))
        pane.clear_scrollback()
        self.assertEqual(len(pane.screen.history), 0)
        self.assertEqual(pane.scroll_offset, 0)

    def test_history_navigation_after_exit_and_anchor_during_output(self):
        pane = TerminalPane()
        pane.screen = VtScreen(3, 20, scrollback=5)
        pane._on_output("".join(f"L{index}\r\n" for index in range(6)))
        QTest.keyClick(pane, Qt.Key.Key_PageUp, Qt.KeyboardModifier.ShiftModifier)
        self.assertEqual(pane.scroll_offset, 2)
        before = pane.screen.viewport(pane.scroll_offset)[0]
        pane._on_output("next\r\n")
        self.assertEqual(pane.scroll_offset, 3)
        self.assertEqual(pane.screen.viewport(pane.scroll_offset)[0], before)
        QTest.keyClick(pane, Qt.Key.Key_PageDown, Qt.KeyboardModifier.ShiftModifier)
        self.assertEqual(pane.scroll_offset, 1)
        pane._on_output("\x1b[?1049h")
        self.assertEqual(pane.scroll_offset, 0)
        pane.scroll(5)
        self.assertEqual(pane.scroll_offset, 0)
        pane.close()

    def test_selection_copy_and_shortcuts_preserve_ctrl_c(self):
        pane = TerminalPane()
        pane.screen = VtScreen(3, 20)
        pane._on_output("first line\r\nsecond line")
        start = QPoint(0, pane.cell_height // 2)
        end = QPoint(6 * pane.cell_width, pane.cell_height + pane.cell_height // 2)
        QTest.mousePress(pane, Qt.MouseButton.LeftButton, pos=start)
        QTest.mouseMove(pane, end)
        QTest.mouseRelease(pane, Qt.MouseButton.LeftButton, pos=end)
        self.assertEqual(pane.selected_text(), "first line\nsecond")
        QTest.keyClick(pane, Qt.Key.Key_C,
                       Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
        self.assertEqual(self.app.clipboard().text(), "first line\nsecond")
        process = Mock(running=True)
        pane.process = process
        QTest.keyClick(pane, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
        process.write.assert_called_once_with(b"\x03")
        pane.close()

    def test_bracketed_paste_and_clipboard_newline_normalization(self):
        pane = TerminalPane()
        process = Mock(running=True)
        pane.process = process
        pane.paste("one\r\ntwo\n")
        process.write.assert_called_with(b"one\rtwo\r")
        pane.screen.feed("\x1b[?2004h")
        self.app.clipboard().setText("héllo\nworld\x1b[201~")
        QTest.keyClick(pane, Qt.Key.Key_V,
                       Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
        process.write.assert_called_with(b"\x1b[200~h\xc3\xa9llo\rworld[201~\x1b[201~")
        pane.close()


class TerminalWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_external_handoff_waits_for_reaping_and_only_launches_once(self):
        handoffs = []
        window = TerminalWindow(["/bin/sh", "-c", "printf READY; sleep 30"],
                                "session", external=lambda: handoffs.append(
                                    (window.process._finished, window.process.master)))
        window.show()
        self.assertTrue(pump(self.app, lambda: "READY" in window.pane.screen.plain()))
        window._open_external()
        window._open_external()
        self.assertFalse(handoffs)
        self.assertTrue(pump(self.app, lambda: bool(handoffs)))
        self.assertEqual(handoffs, [(True, None)])
        self.assertFalse(window.isVisible())
        self.assertFalse(window.pane._blink.isActive())
        window._open_external()
        self.assertEqual(len(handoffs), 1)

    def test_close_or_escape_cancels_pending_external_handoff(self):
        for escape in (False, True):
            with self.subTest(escape=escape):
                external = Mock()
                window = TerminalWindow(["/bin/sh", "-c", "printf READY; sleep 30"],
                                        "session", external=external)
                window.show()
                self.assertTrue(pump(self.app, lambda: "READY" in window.pane.screen.plain()))
                window._open_external()
                if escape:
                    window.reject()
                    window.reject()
                else:
                    window.close()
                    window.close()
                self.assertTrue(pump(self.app, lambda: window.process._finished))
                external.assert_not_called()
                self.assertFalse(window.isVisible())

    def test_external_handoff_after_exit_and_failure_remains_visible(self):
        external = Mock(side_effect=RuntimeError("terminal unavailable"))
        window = TerminalWindow(["/bin/sh", "-c", "printf READY"], "session", external=external)
        window.show()
        self.assertTrue(pump(self.app, lambda: window.process._finished))
        with patch("chrootables.terminal.QMessageBox.warning") as warning:
            window._open_external()
        external.assert_called_once_with()
        warning.assert_called_once()
        self.assertTrue(window.isVisible())
        self.assertFalse(window._closing)
        external.side_effect = None
        window._open_external()
        self.assertEqual(external.call_count, 2)
        self.assertFalse(window.isVisible())

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
        self.assertFalse(window.search_bar.isVisible())
        QTest.keyClick(window.pane, Qt.Key.Key_F,
                       Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
        self.assertTrue(window.search_bar.isVisible())
        window.search_input.setText("READY")
        self.assertTrue(window.find_match(True))
        self.assertEqual(window.pane.selected_text(), "READY")
        self.assertTrue(window.process.running)
        window.close()
        self.assertTrue(pump(self.app, lambda: window.process._finished and not window.isVisible()))


if __name__ == "__main__":
    unittest.main()
