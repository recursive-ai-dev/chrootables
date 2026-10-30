"""Embedded terminal: a pty session with a compact VT/ANSI screen model.

The pane is not a security boundary; it renders whatever the session prints
and forwards keystrokes. Emulation targets the sequences common in Debian
tooling: shell prompts, apt/dpkg progress, compilers and editors.
"""
from __future__ import annotations

import codecs
from collections import deque
import fcntl
import os
import signal
import struct
import subprocess
import termios
import unicodedata

from PySide6.QtCore import QObject, QSocketNotifier, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter
from PySide6.QtWidgets import (
    QApplication, QDialog, QHBoxLayout, QLineEdit, QMessageBox, QPushButton,
    QVBoxLayout, QWidget,
)

DEFAULT_ROWS = 24
DEFAULT_COLUMNS = 80
DEFAULT_SCROLLBACK = 2000

BOLD = 1 << 0
ITALIC = 1 << 1
UNDERLINE = 1 << 2
REVERSE = 1 << 3
DIM = 1 << 4
STRIKE = 1 << 5

# XTerm palette, indices 0-15; None follows the widget theme.
PALETTE = {
    0: (0, 0, 0), 1: (197, 34, 43), 2: (42, 161, 78), 3: (196, 160, 23),
    4: (48, 118, 215), 5: (161, 74, 168), 6: (56, 178, 197), 7: (211, 215, 207),
    8: (85, 87, 83), 9: (239, 41, 41), 10: (135, 208, 92), 11: (252, 233, 79),
    12: (114, 159, 207), 13: (173, 127, 168), 14: (52, 220, 229), 15: (238, 238, 236),
}

QT_KEY_ENTER = Qt.Key.Key_Return.value
QT_KEY_TAB = Qt.Key.Key_Tab.value
QT_KEY_BACKSPACE = Qt.Key.Key_Backspace.value
QT_KEY_LEFT = Qt.Key.Key_Left.value
QT_KEY_UP = Qt.Key.Key_Up.value
QT_KEY_RIGHT = Qt.Key.Key_Right.value
QT_KEY_DOWN = Qt.Key.Key_Down.value
QT_KEY_HOME = Qt.Key.Key_Home.value
QT_KEY_END = Qt.Key.Key_End.value
QT_KEY_INSERT = Qt.Key.Key_Insert.value
QT_KEY_DELETE = Qt.Key.Key_Delete.value
QT_KEY_PAGEUP = Qt.Key.Key_PageUp.value
QT_KEY_PAGEDOWN = Qt.Key.Key_PageDown.value


class Style:
    """Immutable character attributes; None colors follow the widget theme."""

    __slots__ = ("fg", "bg", "flags")

    def __init__(self, fg=None, bg=None, flags=0):
        self.fg = fg
        self.bg = bg
        self.flags = flags

    def __eq__(self, other):
        return (isinstance(other, Style) and self.fg == other.fg
                and self.bg == other.bg and self.flags == other.flags)

    def __hash__(self):
        return hash((self.fg, self.bg, self.flags))

    def replaced(self, fg=None, bg=None, flags=0):
        return Style(fg, bg, flags)


DEFAULT_STYLE = Style()


def cell_width(text):
    """Columns occupied by a cell's base character and combining marks.

    Ambiguous-width characters use one column, matching western locales.
    Empty text marks the second column of a wide character.
    """
    return max((0 if unicodedata.combining(character) or
                unicodedata.category(character) in ("Mn", "Me", "Cf") else
                2 if unicodedata.east_asian_width(character) in ("W", "F") else 1
                for character in text), default=0)


def normalize_cells(line):
    """Remove wide characters split by editing or clipping the cell grid."""
    for index, (text, style) in enumerate(line):
        if not text:
            if not index or cell_width(line[index - 1][0]) != 2:
                line[index] = (" ", DEFAULT_STYLE)
        elif cell_width(text) == 2 and (index + 1 == len(line) or line[index + 1][0]):
            line[index] = (" ", DEFAULT_STYLE)
    return line

# Parser states.
_GROUND, _ESC, _CSI, _OSC, _OSC_ESC, _CHARSET, _CSI_IGNORE = range(7)
MAX_CSI_PARAMS = 256


class VtScreen:
    """A minimal VT100/xterm screen model.

    Tracks text, colors, cursor and scroll region for a fixed grid. Alt-screen
    switching saves and restores the primary buffer. Unknown sequences are
    consumed and ignored so broken output degrades to plain text.
    """

    def __init__(self, rows=DEFAULT_ROWS, columns=DEFAULT_COLUMNS, scrollback=DEFAULT_SCROLLBACK):
        self.rows = rows
        self.columns = columns
        self.lines = [self._blank_line() for _ in range(rows)]
        self.row = 0
        self.column = 0
        self.cursor_visible = True
        self._saved_cursor = (0, 0)
        self._alt_active = False
        self._primary = None
        self._top = 0
        self._bottom = rows - 1
        self.style = DEFAULT_STYLE
        self._state = _GROUND
        self._params = ""
        self.history = deque(maxlen=max(0, scrollback))
        self.history_count = 0
        self.bracketed_paste = False
        self._last_char = ""

    def _blank_line(self):
        return [(" ", DEFAULT_STYLE)] * self.columns

    # -- grid operations ---------------------------------------------------

    def _clamp_cursor(self):
        self.row = max(0, min(self.rows - 1, self.row))
        self.column = max(0, min(self.columns - 1, self.column))

    def _scroll_up(self, count=1):
        for _ in range(min(count, self._bottom - self._top + 1)):
            if not self._alt_active and self._top == 0 and self._bottom == self.rows - 1:
                self.history.append(self.lines[0])
                self.history_count += 1
            del self.lines[self._top]
            self.lines.insert(self._bottom, self._blank_line())

    def _scroll_down(self, count=1):
        for _ in range(min(count, self._bottom - self._top + 1)):
            del self.lines[self._bottom]
            self.lines.insert(self._top, self._blank_line())

    def _line_feed(self):
        if self.row == self._bottom:
            self._scroll_up()
        elif self.row < self.rows - 1:
            self.row += 1

    def _reverse_line_feed(self):
        if self.row == self._top:
            self._scroll_down()
        elif self.row > 0:
            self.row -= 1

    def _put(self, character):
        width = cell_width(character)
        if not width:
            column = self.column - 1
            row = self.row
            if column < 0 and row:
                row, column = row - 1, self.columns - 1
            while column > 0 and not self.lines[row][column][0]:
                column -= 1
            if column >= 0:
                text, style = self.lines[row][column]
                self.lines[row][column] = (text + character, style)
            return
        if width > self.columns:
            character, width = "\ufffd", 1
        self._last_char = character
        if self.column >= self.columns or self.column + width > self.columns:
            self.column = 0
            self._line_feed()
        self._erase(self.row, self.column, self.column + width)
        self.lines[self.row][self.column] = (character, self.style)
        if width == 2:
            self.lines[self.row][self.column + 1] = ("", self.style)
        self.column += width

    def _erase(self, line, start, end):
        start, end = max(0, start), min(self.columns, end)
        if start >= end:
            return
        if start and not self.lines[line][start][0]:
            start -= 1
        if end < self.columns and not self.lines[line][end][0]:
            end += 1
        self.lines[line][start:end] = [(" ", DEFAULT_STYLE)] * (end - start)

    def _insert_blanks(self, count):
        line = self.lines[self.row]
        count = min(count, self.columns - self.column)
        line[self.column:self.column] = [(" ", self.style)] * count
        del line[self.columns:]
        normalize_cells(line)

    def _delete_cells(self, count):
        line = self.lines[self.row]
        count = min(count, self.columns - self.column)
        del line[self.column:self.column + count]
        line.extend([(" ", DEFAULT_STYLE)] * count)
        normalize_cells(line)

    # -- sequence handling ---------------------------------------------------

    def _param(self, index, default=1):
        chunks = self._params.split(";") if self._params else []
        value = chunks[index] if index < len(chunks) and chunks[index] else None
        return max(0, int(value)) if value is not None else default

    def _param_count(self):
        return len(self._params.split(";")) if self._params else 0

    def _apply_csi(self, final):
        # Only implement decimal parameters and the private modes we support.
        # Other CSI forms must not reach integer conversion or alter the screen.
        params = self._params
        if params.startswith("?") and final in ("h", "l"):
            params = params[1:]
        if any(character not in "0123456789;" for character in params):
            return
        count = lambda index=0: max(1, self._param(index))
        if final == "A":
            self.row -= count()
        elif final in ("B", "e"):
            self.row += count()
        elif final in ("C", "a"):
            self.column += count()
        elif final == "D":
            self.column -= count()
        elif final == "E":
            self.row += count()
            self.column = 0
        elif final == "F":
            self.row -= count()
            self.column = 0
        elif final in ("G", "`"):
            self.column = count() - 1
        elif final in ("H", "f"):
            self.row = self._param(0, 1) - 1
            self.column = self._param(1, 1) - 1
        elif final == "d":
            self.row = count() - 1
        elif final == "J":
            mode = self._param(0, 0) if self._param_count() else 0
            if mode == 3:
                self.history.clear()
            elif mode == 0:
                self._erase(self.row, self.column, self.columns)
                for line in range(self.row + 1, self.rows):
                    self._erase(line, 0, self.columns)
            elif mode == 1:
                for line in range(self.row):
                    self._erase(line, 0, self.columns)
                self._erase(self.row, 0, min(self.column + 1, self.columns))
            else:
                for line in range(self.rows):
                    self._erase(line, 0, self.columns)
        elif final == "K":
            mode = self._param(0, 0) if self._param_count() else 0
            if mode == 0:
                self._erase(self.row, self.column, self.columns)
            elif mode == 1:
                self._erase(self.row, 0, min(self.column + 1, self.columns))
            else:
                self._erase(self.row, 0, self.columns)
        elif final == "L":
            for _ in range(min(count(), self._bottom - self.row + 1)):
                del self.lines[self._bottom]
                self.lines.insert(self.row, self._blank_line())
        elif final == "M":
            for _ in range(min(count(), self._bottom - self.row + 1)):
                del self.lines[self.row]
                self.lines.insert(self._bottom, self._blank_line())
        elif final == "@":  # ICH
            self._insert_blanks(count())
        elif final == "P":  # DCH
            self._delete_cells(count())
        elif final == "S":
            self._scroll_up(count())
        elif final == "T":
            self._scroll_down(count())
        elif final == "X":  # ECH
            self._erase(self.row, self.column, min(self.columns, self.column + count()))
        elif final == "b":  # REP
            if self._last_char:
                for _ in range(min(count(), self.columns * self.rows)):
                    self._put(self._last_char)
        elif final == "r":  # DECSTBM
            top = self._param(0, 1) - 1
            bottom = self._param(1, self.rows) - 1 if self._param_count() > 1 else self.rows - 1
            if 0 <= top < bottom < self.rows:
                self._top, self._bottom = top, bottom
                self.row, self.column = 0, 0
        elif final == "s":
            self._saved_cursor = (self.row, self.column)
        elif final == "u":
            self.row, self.column = self._saved_cursor
        elif final == "m":
            self._apply_sgr()
        elif final in ("h", "l"):
            self._apply_mode(final == "h")
        self._clamp_cursor()

    def _apply_sgr(self):
        chunks = [chunk for chunk in self._params.split(";")] if self._params else ["0"]
        params = []
        for chunk in chunks:
            params.append(int(chunk) if chunk.isdigit() else 0)
        if not params:
            params = [0]
        fg, bg, flags = self.style.fg, self.style.bg, self.style.flags
        index = 0
        while index < len(params):
            value = params[index]
            if value == 0:
                fg, bg, flags = None, None, 0
            elif value == 1:
                flags |= BOLD
            elif value == 2:
                flags |= DIM
            elif value == 3:
                flags |= ITALIC
            elif value == 4:
                flags |= UNDERLINE
            elif value == 7:
                flags |= REVERSE
            elif value == 9:
                flags |= STRIKE
            elif value == 21:
                flags &= ~BOLD
            elif value == 22:
                flags &= ~(BOLD | DIM)
            elif value == 23:
                flags &= ~ITALIC
            elif value == 24:
                flags &= ~UNDERLINE
            elif value == 27:
                flags &= ~REVERSE
            elif value == 29:
                flags &= ~STRIKE
            elif 30 <= value <= 37:
                fg = value - 30
            elif value == 39:
                fg = None
            elif 40 <= value <= 47:
                bg = value - 40
            elif value == 49:
                bg = None
            elif 90 <= value <= 97:
                fg = value - 90 + 8
            elif 100 <= value <= 107:
                bg = value - 100 + 8
            elif value in (38, 48):
                color, next_index = self._extended_color(params, index + 1)
                if value == 38:
                    fg = color
                else:
                    bg = color
                index = next_index
            index += 1
        self.style = Style(fg, bg, flags)

    def _extended_color(self, params, index):
        mode = params[index] if index < len(params) else 5
        if mode == 5 and index + 1 < len(params):
            return params[index + 1] % 256, index + 1
        if mode == 2 and index + 3 < len(params):
            return tuple(min(255, value) for value in params[index + 1:index + 4]), index + 3
        return None, index

    def _apply_mode(self, enable):
        private = self._params.startswith("?")
        values = [int(chunk) if chunk.isdigit() else 0 for chunk in self._params.lstrip("?").split(";")] if self._params else []
        for value in values:
            if not private:
                continue
            if value == 25:
                self.cursor_visible = enable
            elif value == 2004:
                self.bracketed_paste = enable
            elif value in (1049, 1047, 47):
                self._switch_screen(enable, save_cursor=(value == 1049))

    def _switch_screen(self, active, save_cursor):
        if active == self._alt_active:
            return
        if active:
            self._primary = (self.lines, self.row, self.column, self._top, self._bottom)
            if save_cursor:
                self._saved_cursor = (self.row, self.column)
            self.lines = [self._blank_line() for _ in range(self.rows)]
        else:
            if self._primary is not None:
                self.lines, self.row, self.column, self._top, self._bottom = self._primary
            self._primary = None
            if save_cursor:
                self.row, self.column = self._saved_cursor
        self._alt_active = active
        self._clamp_cursor()

    def _reset(self):
        self.lines = [self._blank_line() for _ in range(self.rows)]
        self.row = self.column = 0
        self._top, self._bottom = 0, self.rows - 1
        self.style = DEFAULT_STYLE
        self.history.clear()
        self._primary = None
        self._alt_active = False
        self.bracketed_paste = False
        self.cursor_visible = True
        self._saved_cursor = (0, 0)
        self._last_char = ""

    # -- public interface ---------------------------------------------------

    def feed(self, text):
        for character in text:
            state = self._state
            if state == _GROUND:
                if character == "\x1b":
                    self._state = _ESC
                elif character == "\r":
                    self.column = 0
                elif character == "\n":
                    self._line_feed()
                elif character == "\t":
                    self.column = min(self.columns - 1, (self.column // 8 + 1) * 8)
                elif character == "\b":
                    self.column = max(0, self.column - 1)
                elif character >= " ":
                    self._put(character)
                # BEL, NUL and other C0 controls are ignored.
            elif state == _ESC:
                if character == "[":
                    self._state = _CSI
                    self._params = ""
                elif character == "]":
                    self._state = _OSC
                elif character in "()*+":
                    self._state = _CHARSET
                else:
                    self._state = _GROUND
                    if character == "7":
                        self._saved_cursor = (self.row, self.column)
                    elif character == "8":
                        self.row, self.column = self._saved_cursor
                        self._clamp_cursor()
                    elif character == "D":
                        self._line_feed()
                    elif character == "M":
                        self._reverse_line_feed()
                    elif character == "E":
                        self._line_feed()
                        self.column = 0
                    elif character == "c":
                        self._reset()
            elif state in (_CSI, _CSI_IGNORE):
                if character == "\x1b":
                    self._state = _ESC
                elif character in ("\x18", "\x1a"):
                    self._state = _GROUND
                elif "@" <= character <= "~":
                    self._state = _GROUND
                    if state == _CSI:
                        self._apply_csi(character)
                elif state == _CSI:
                    if " " <= character <= "?" and len(self._params) < MAX_CSI_PARAMS:
                        self._params += character
                    else:
                        # Consume the rest without retaining unbounded parameters.
                        self._state = _CSI_IGNORE
            elif state == _OSC:
                if character == "\x07":
                    self._state = _GROUND
                elif character == "\x1b":
                    self._state = _OSC_ESC
            elif state == _OSC_ESC:
                self._state = _GROUND
            elif state == _CHARSET:
                self._state = _GROUND
        if self._state in (_CSI,):
            self._clamp_cursor()

    def resize(self, rows, columns):
        rows = max(2, rows)
        columns = max(20, columns)
        if rows == self.rows and columns == self.columns:
            return
        def resized(lines):
            result = [[(" ", DEFAULT_STYLE)] * columns for _ in range(rows)]
            for index, line in enumerate(lines[:rows]):
                width = min(columns, len(line))
                result[index][:width] = line[:width]
                normalize_cells(result[index])
            return result

        self.lines = resized(self.lines)
        if self._primary is not None:
            lines, row, column, _, _ = self._primary
            self._primary = (resized(lines), min(row, rows - 1),
                             min(column, columns - 1), 0, rows - 1)
        self.rows, self.columns = rows, columns
        self._top, self._bottom = 0, rows - 1
        self._clamp_cursor()

    def plain(self):
        """Visible text, one line per row, trailing spaces trimmed."""
        return "\n".join("".join(cell[0] for cell in line).rstrip() for line in self.lines)

    def viewport(self, offset=0):
        """Return the visible grid, optionally looking back through primary history."""
        if not offset or self._alt_active:
            return self.lines
        offset = max(0, min(offset, len(self.history)))
        lines = list(self.history) + self.lines
        start = len(self.history) - offset
        return [normalize_cells((line[:self.columns] + self._blank_line())[:self.columns])
                for line in lines[start:start + self.rows]]


def key_to_bytes(key, text, ctrl=False, alt=False):
    """Translate a Qt key event into the bytes sent to the session."""
    ctrl_arrows = {
        QT_KEY_UP: b"\x1b[1;5A", QT_KEY_DOWN: b"\x1b[1;5B",
        QT_KEY_RIGHT: b"\x1b[1;5C", QT_KEY_LEFT: b"\x1b[1;5D",
    }
    if ctrl and key in ctrl_arrows:
        return (b"\x1b" if alt else b"") + ctrl_arrows[key]
    special = {
        QT_KEY_ENTER: b"\r", QT_KEY_TAB: b"\t", QT_KEY_BACKSPACE: b"\x7f",
        QT_KEY_LEFT: b"\x1b[D", QT_KEY_UP: b"\x1b[A", QT_KEY_RIGHT: b"\x1b[C",
        QT_KEY_DOWN: b"\x1b[B", QT_KEY_HOME: b"\x1b[H", QT_KEY_END: b"\x1b[F",
        QT_KEY_INSERT: b"\x1b[2~", QT_KEY_DELETE: b"\x1b[3~",
        QT_KEY_PAGEUP: b"\x1b[5~", QT_KEY_PAGEDOWN: b"\x1b[6~",
    }
    if key in special:
        return (b"\x1b" if alt else b"") + special[key]
    if ctrl:
        if ord("A") <= key <= ord("Z"):
            return bytes([key - ord("A") + 1])
        if key == QT_KEY_HOME:
            return b"\x1b[H"
        if key == QT_KEY_END:
            return b"\x1b[F"
        return b""
    if text:
        return (b"\x1b" if alt else b"") + text.encode("utf-8")
    return b""


class PtyProcess(QObject):
    """Runs one session command attached to a new pseudo-terminal."""

    output = Signal(str)
    finished = Signal(int)

    def __init__(self, argv, env=None, parent=None,
                 rows=DEFAULT_ROWS, columns=DEFAULT_COLUMNS):
        super().__init__(parent)
        self.master, slave = os.openpty()
        try:
            self._set_window_size(slave, rows, columns)
            os.set_blocking(self.master, False)
            environment = dict(os.environ)
            environment.setdefault("TERM", "xterm-256color")
            if env:
                environment.update(env)
            self.process = subprocess.Popen(
                argv, stdin=slave, stdout=slave, stderr=slave,
                start_new_session=True, env=environment, close_fds=True,
            )
        except BaseException:
            os.close(self.master)
            raise
        finally:
            os.close(slave)
        self._decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self._notifier = QSocketNotifier(self.master, QSocketNotifier.Type.Read, self)
        self._notifier.activated.connect(self._readable)
        self._pending_input = bytearray()
        self._write_notifier = QSocketNotifier(self.master, QSocketNotifier.Type.Write, self)
        self._write_notifier.setEnabled(False)
        self._write_notifier.activated.connect(self._flush_input)
        self._finished = False
        self._reaper = QTimer(self)
        self._reaper.setInterval(25)
        self._reaper.timeout.connect(self._check_finished)

    def _set_window_size(self, descriptor, rows, columns):
        winsize = struct.pack("HHHH", rows, columns, 0, 0)
        fcntl.ioctl(descriptor, termios.TIOCSWINSZ, winsize)

    def _readable(self):
        if self.master is None:
            return
        try:
            data = os.read(self.master, 65536)
        except (BlockingIOError, InterruptedError):
            return
        except OSError:
            data = b""
        if not data:
            self._notifier.setEnabled(False)
            self._write_notifier.setEnabled(False)
            self._pending_input.clear()
            descriptor, self.master = self.master, None
            os.close(descriptor)
            remaining = self._decoder.decode(b"", final=True)
            if remaining:
                self.output.emit(remaining)
            self._reaper.start()
            self._check_finished()
            return
        self.output.emit(self._decoder.decode(data))

    def _check_finished(self):
        code = self.process.poll()
        if code is not None and not self._finished:
            self._finished = True
            self._reaper.stop()
            self.finished.emit(code)

    def write(self, payload):
        if self.master is None:
            return
        if isinstance(payload, str):
            payload = payload.encode("utf-8")
        self._pending_input.extend(payload)
        self._flush_input()

    def _flush_input(self):
        """Drain queued input without blocking the UI on a busy or stopped guest."""
        if self.master is None:
            return
        try:
            written = os.write(self.master, self._pending_input[:65536]) if self._pending_input else 0
            del self._pending_input[:written]
        except (BlockingIOError, InterruptedError):
            pass
        except OSError:
            self._pending_input.clear()
        self._write_notifier.setEnabled(bool(self._pending_input))

    def resize(self, rows, columns):
        if self.master is None:
            return
        try:
            self._set_window_size(self.master, rows, columns)
        except OSError:
            pass

    def terminate(self):
        # start_new_session creates a private group. Stop its children too:
        # Ubuntu's /bin/sh can leave cat holding the PTY after the shell exits.
        if not self._finished:
            try:
                os.killpg(self.process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass

    @property
    def running(self):
        return self.process.poll() is None


class TerminalPane(QWidget):
    """Renders a VtScreen and forwards keyboard input to a PtyProcess."""

    find_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.screen = VtScreen()
        self.process = None
        self.scroll_offset = 0
        self._selection_start = None
        self._selection_end = None
        self._selecting = False
        self._search_cursor = None
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.setCursor(Qt.CursorShape.IBeamCursor)
        self.setToolTip("Select text to copy with Ctrl+Shift+C. Paste with Ctrl+Shift+V. "
                        "Scroll with the mouse wheel or Shift+PageUp / PageDown.")
        font = QFont("Monospace")
        font.setStyleHint(QFont.StyleHint.Monospace)
        font.setPointSize(10)
        self.font = font
        self._metrics = QFontMetrics(font)
        self.cell_width = max(1, self._metrics.horizontalAdvance("M"))
        self.cell_height = self._metrics.height()
        self._cursor_shown = True
        self._blink = QTimer(self)
        self._blink.timeout.connect(self._toggle_cursor)
        self._blink.start(600)

    def attach(self, process):
        self.process = process
        process.output.connect(self._on_output)
        process.finished.connect(self._on_finished)

    def _on_output(self, text):
        previous_count = self.screen.history_count
        previous_alt = self.screen._alt_active
        self.screen.feed(text)
        if self.screen._alt_active or previous_alt != self.screen._alt_active:
            self.scroll_offset = 0
        elif self.scroll_offset:
            # Keep the same historical output in view as more lines arrive.
            self.scroll_offset = min(len(self.screen.history), self.scroll_offset +
                                     self.screen.history_count - previous_count)
        self.clear_selection()
        self.update()

    def _on_finished(self, code):
        self._on_output(f"\r\n[session exited with code {code}]")
        self._blink.stop()
        self._cursor_shown = False
        self.update()

    def _toggle_cursor(self):
        if self.process is not None and self.process.running:
            self._cursor_shown = not self._cursor_shown
            self.update()

    def _cells(self):
        rows = max(2, self.height() // self.cell_height)
        columns = max(20, self.width() // self.cell_width)
        return rows, columns

    def resizeEvent(self, event):
        rows, columns = self._cells()
        self.screen.resize(rows, columns)
        self.clear_selection()
        if self.process is not None:
            self.process.resize(rows, columns)
        super().resizeEvent(event)

    def _brush(self, color, default):
        if color is None:
            return QColor(default)
        if isinstance(color, int):
            color = max(0, min(255, color))
            if color < 16:
                return QColor(*PALETTE[color])
            if color < 232:
                value = color - 16
                levels = (0, 95, 135, 175, 215, 255)
                return QColor(levels[value // 36], levels[(value // 6) % 6], levels[value % 6])
            level = 8 + (color - 232) * 10
            return QColor(level, level, level)
        return QColor(*color)

    def paintEvent(self, event):
        painter = QPainter(self)
        background = QColor("#1c1c1c")
        foreground = QColor("#e6e6e2")
        painter.fillRect(self.rect(), background)
        fonts = {}
        last_flags = None
        for row, line in enumerate(self.screen.viewport(self.scroll_offset)):
            y = row * self.cell_height
            column = 0
            while column < len(line):
                text, style = line[column]
                if not text:
                    column += 1
                    continue
                width = max(1, cell_width(text))
                # Batch ordinary monospaced text; wide and combining cells
                # keep explicit grid positions rather than font advances.
                if len(text) == 1 and text.isascii():
                    end = column + 1
                    while end < len(line):
                        next_text, next_style = line[end]
                        if next_style != style or len(next_text) != 1 or not next_text.isascii():
                            break
                        text += next_text
                        end += 1
                    width = end - column
                fill = self._brush(style.bg, "#1c1c1c")
                text_color = self._brush(style.fg, "#e6e6e2")
                if style.flags & REVERSE:
                    fill, text_color = text_color, fill
                if style.flags & DIM:
                    text_color = QColor(
                        int(text_color.red() * 0.65),
                        int(text_color.green() * 0.65),
                        int(text_color.blue() * 0.65),
                    )
                painter.fillRect(column * self.cell_width, y, width * self.cell_width,
                                 self.cell_height, fill)
                if style.flags != last_flags:
                    if style.flags not in fonts:
                        font = QFont(self.font)
                        font.setBold(bool(style.flags & BOLD))
                        font.setItalic(bool(style.flags & ITALIC))
                        font.setUnderline(bool(style.flags & UNDERLINE))
                        font.setStrikeOut(bool(style.flags & STRIKE))
                        fonts[style.flags] = font
                    painter.setFont(fonts[style.flags])
                    last_flags = style.flags
                painter.setPen(text_color)
                painter.drawText(column * self.cell_width, y + self._metrics.ascent(), text)
                column += width
        if self._selection_start is not None and self._selection_end is not None:
            start, end = sorted((self._selection_start, self._selection_end))
            for row in range(start[0], end[0] + 1):
                left = start[1] if row == start[0] else 0
                right = end[1] if row == end[0] else self.screen.columns
                painter.fillRect(left * self.cell_width, row * self.cell_height,
                                 (right - left) * self.cell_width, self.cell_height,
                                 QColor(100, 160, 220, 100))
        if (not self.scroll_offset and self._cursor_shown and self.screen.cursor_visible and self.process is not None
                and self.process.running):
            painter.fillRect(min(self.screen.column, self.screen.columns - 1) * self.cell_width,
                             self.screen.row * self.cell_height,
                             self.cell_width, self.cell_height, foreground)

    def keyPressEvent(self, event):
        modifiers = event.modifiers()
        shift = bool(modifiers & Qt.KeyboardModifier.ShiftModifier)
        ctrl = bool(modifiers & Qt.KeyboardModifier.ControlModifier)
        if ctrl and shift and event.key() == Qt.Key.Key_C:
            text = self.selected_text()
            if text:
                QApplication.clipboard().setText(text)
            event.accept()
            return
        if ctrl and shift and event.key() == Qt.Key.Key_V:
            self.paste(QApplication.clipboard().text())
            event.accept()
            return
        if ctrl and shift and event.key() == Qt.Key.Key_F:
            self.find_requested.emit()
            event.accept()
            return
        if shift and event.key() in (Qt.Key.Key_PageUp, Qt.Key.Key_PageDown):
            self.scroll((self.screen.rows - 1) *
                        (1 if event.key() == Qt.Key.Key_PageUp else -1))
            event.accept()
            return
        if self.process is None or not self.process.running:
            super().keyPressEvent(event)
            return
        payload = key_to_bytes(
            event.key(), event.text(),
            bool(modifiers & Qt.KeyboardModifier.ControlModifier),
            bool(modifiers & Qt.KeyboardModifier.AltModifier),
        )
        if payload:
            self.scroll_offset = 0
            self.clear_selection()
            self.process.write(payload)
            self.update()

    def scroll(self, lines):
        if not self.screen._alt_active:
            self.scroll_offset = max(0, min(len(self.screen.history), self.scroll_offset + lines))
            self.clear_selection()
            self.update()

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        lines = int(delta / 120 * 3) if delta else int(event.pixelDelta().y() / self.cell_height)
        self.scroll(lines)
        event.accept()

    def clear_selection(self):
        self._selection_start = self._selection_end = None
        self._selecting = False

    def _cell_at(self, position):
        return (max(0, min(self.screen.rows - 1, int(position.y()) // self.cell_height)),
                max(0, min(self.screen.columns, int(position.x()) // self.cell_width)))

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.setFocus()
            self._selection_start = self._selection_end = self._cell_at(event.position())
            self._selecting = True
            self.update()
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._selecting:
            self._selection_end = self._cell_at(event.position())
            self.update()
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._selecting:
            self._selection_end = self._cell_at(event.position())
            self._selecting = False
            self.update()
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def selected_text(self):
        if self._selection_start is None or self._selection_end is None:
            return ""
        start, end = sorted((self._selection_start, self._selection_end))
        if start == end:
            return ""
        lines = self.screen.viewport(self.scroll_offset)
        selected = []
        for row in range(start[0], end[0] + 1):
            left = start[1] if row == start[0] else 0
            right = end[1] if row == end[0] else self.screen.columns
            if left < len(lines[row]) and left and not lines[row][left][0]:
                left -= 1
            selected.append("".join(cell[0] for cell in lines[row][left:right]).rstrip())
        return "\n".join(selected)

    def paste(self, text):
        if self.process is not None and self.process.running and text:
            # Keep embedded escapes from terminating the bracketed block early.
            text = text.replace("\x1b", "").replace("\r\n", "\n").replace("\r", "\n")
            payload = text.replace("\n", "\r").encode("utf-8")
            if self.screen.bracketed_paste:
                payload = b"\x1b[200~" + payload + b"\x1b[201~"
            self.scroll_offset = 0
            self.clear_selection()
            self.process.write(payload)
            self.update()

    def clear_scrollback(self):
        self.screen.history.clear()
        self.scroll_offset = 0
        self._search_cursor = None
        self.clear_selection()
        self.update()

    def find_text(self, query, forward=True):
        needle = (query or "").strip().lower()
        if not needle:
            self.clear_selection()
            self.update()
            return False
        history = [] if self.screen._alt_active else list(self.screen.history)
        all_lines = history + self.screen.lines
        matches = []
        for abs_row, line in enumerate(all_lines):
            row_chars = []
            col_map = []
            for col, (cell_text, _) in enumerate(line):
                if cell_text:
                    for ch in cell_text:
                        row_chars.append(ch)
                        col_map.append((col, col + max(1, cell_width(cell_text))))
            haystack = "".join(row_chars).lower()
            pos = 0
            while True:
                idx = haystack.find(needle, pos)
                if idx < 0:
                    break
                start_col = col_map[idx][0] if idx < len(col_map) else 0
                end_idx = min(len(col_map) - 1, idx + len(needle) - 1)
                end_col = col_map[end_idx][1] if end_idx >= 0 and col_map else start_col + len(needle)
                matches.append((abs_row, start_col, end_col))
                pos = idx + 1
        if not matches:
            self.clear_selection()
            self.update()
            return False
        if self._search_cursor in matches:
            index = (matches.index(self._search_cursor) + (1 if forward else -1)) % len(matches)
        else:
            index = 0 if forward else len(matches) - 1
        abs_row, start_col, end_col = self._search_cursor = matches[index]
        if self.screen._alt_active or abs_row >= len(history):
            self.scroll_offset = 0
            view_row = abs_row - len(history)
        else:
            self.scroll_offset = min(len(history), max(0, len(history) - abs_row))
            start = len(history) - self.scroll_offset
            view_row = abs_row - start
        self._selection_start = (max(0, min(self.screen.rows - 1, view_row)), start_col)
        self._selection_end = (max(0, min(self.screen.rows - 1, view_row)), end_col)
        self.update()
        return True


class TerminalWindow(QDialog):
    """A modeless window hosting one embedded session."""

    def __init__(self, command, title, external=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(760, 480)
        self._external = external
        self._closing = False
        self._external_pending = False
        layout = QVBoxLayout(self)
        self.search_bar = QWidget(self)
        search_layout = QHBoxLayout(self.search_bar)
        search_layout.setContentsMargins(0, 0, 0, 0)
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Find in scrollback and screen…")
        self.search_input.returnPressed.connect(lambda: self.find_match(True))
        self.find_prev_button = QPushButton("Previous")
        self.find_prev_button.clicked.connect(lambda: self.find_match(False))
        self.find_next_button = QPushButton("Next")
        self.find_next_button.clicked.connect(lambda: self.find_match(True))
        search_layout.addWidget(self.search_input, 1)
        search_layout.addWidget(self.find_prev_button)
        search_layout.addWidget(self.find_next_button)
        self.search_bar.setVisible(False)
        layout.addWidget(self.search_bar)
        self.pane = TerminalPane()
        self.pane.find_requested.connect(self.toggle_search)
        self.process = PtyProcess(command, parent=self)
        self.process.finished.connect(self._session_finished)
        self.pane.attach(self.process)
        layout.addWidget(self.pane, 1)
        buttons = QHBoxLayout()
        if external is not None:
            external_button = QPushButton("Open in external terminal")
            external_button.clicked.connect(self._open_external)
            buttons.addWidget(external_button)
        self.find_button = QPushButton("Find…")
        self.find_button.clicked.connect(self.toggle_search)
        buttons.addWidget(self.find_button)
        self.clear_scrollback_button = QPushButton("Clear scrollback")
        self.clear_scrollback_button.clicked.connect(self.pane.clear_scrollback)
        buttons.addWidget(self.clear_scrollback_button)
        buttons.addStretch(1)
        close_button = QPushButton("Close session")
        close_button.clicked.connect(self.close)
        buttons.addWidget(close_button)
        layout.addLayout(buttons)

    def toggle_search(self):
        visible = not self.search_bar.isVisible()
        self.search_bar.setVisible(visible)
        if visible:
            self.search_input.setFocus()
            self.search_input.selectAll()
        else:
            self.pane.setFocus()

    def find_match(self, forward=True):
        return self.pane.find_text(self.search_input.text(), forward=forward)

    def _open_external(self):
        if self._closing or self._external is None:
            return
        self._external_pending = True
        if self._begin_close():
            self._complete_handoff()

    def _complete_handoff(self):
        # The image lock belongs to the embedded process. Wait until it has
        # exited and the PTY has closed before launching its replacement.
        self._external_pending = False
        self._closing = True
        try:
            self._external()
        except (RuntimeError, ValueError) as error:
            self._closing = False
            QMessageBox.warning(self, "Chrootables", str(error))
            return
        self.close()

    def closeEvent(self, event):
        # Closing the window ends the session, matching the CLI's rule that
        # closing the terminal releases the image lock.
        self._external_pending = False
        if self._begin_close():
            super().closeEvent(event)
        else:
            event.ignore()

    def _begin_close(self):
        if self.process._finished:
            return True
        if not self._closing:
            self._closing = True
            self.process.terminate()
        # Keep the window (and its process/notifier) owned until EOF and reaping.
        return False

    def reject(self):
        # Escape calls reject directly, bypassing closeEvent.
        self._external_pending = False
        if self._begin_close():
            super().reject()

    def _session_finished(self, code):
        if self._closing:
            if self._external_pending:
                self._complete_handoff()
            else:
                self.close()
