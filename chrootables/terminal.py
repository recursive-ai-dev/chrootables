"""Embedded terminal: a pty session with a compact VT/ANSI screen model.

The pane is not a security boundary; it renders whatever the session prints
and forwards keystrokes. Emulation targets the sequences common in Debian
tooling: shell prompts, apt/dpkg progress, compilers and editors.
"""
from __future__ import annotations

import codecs
import fcntl
import os
import signal
import struct
import subprocess
import termios

from PySide6.QtCore import QObject, QSocketNotifier, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter
from PySide6.QtWidgets import QDialog, QHBoxLayout, QMessageBox, QPushButton, QVBoxLayout, QWidget

DEFAULT_ROWS = 24
DEFAULT_COLUMNS = 80

BOLD = 1 << 0
ITALIC = 1 << 1
UNDERLINE = 1 << 2
REVERSE = 1 << 3

# XTerm palette, indices 0-15; None follows the widget theme.
PALETTE = {
    0: None, 1: (197, 34, 43), 2: (42, 161, 78), 3: (196, 160, 23),
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

# Parser states.
_GROUND, _ESC, _CSI, _OSC, _OSC_ESC, _CHARSET = range(6)


class VtScreen:
    """A minimal VT100/xterm screen model.

    Tracks text, colors, cursor and scroll region for a fixed grid. Alt-screen
    switching saves and restores the primary buffer. Unknown sequences are
    consumed and ignored so broken output degrades to plain text.
    """

    def __init__(self, rows=DEFAULT_ROWS, columns=DEFAULT_COLUMNS):
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

    def _blank_line(self):
        return [(" ", DEFAULT_STYLE)] * self.columns

    # -- grid operations ---------------------------------------------------

    def _clamp_cursor(self):
        self.row = max(0, min(self.rows - 1, self.row))
        self.column = max(0, min(self.columns - 1, self.column))

    def _scroll_up(self, count=1):
        for _ in range(min(count, self._bottom - self._top + 1)):
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
        if self.column >= self.columns:
            self.column = 0
            self._line_feed()
        self.lines[self.row][self.column] = (character, self.style)
        self.column += 1

    def _erase(self, line, start, end):
        self.lines[line][start:end] = [(" ", DEFAULT_STYLE)] * (end - start)

    def _insert_blanks(self, count):
        line = self.lines[self.row]
        count = min(count, self.columns - self.column)
        line[self.column:self.column] = [(" ", self.style)] * count
        del line[self.columns:]

    def _delete_cells(self, count):
        line = self.lines[self.row]
        count = min(count, self.columns - self.column)
        del line[self.column:self.column + count]
        line.extend([(" ", DEFAULT_STYLE)] * count)

    # -- sequence handling ---------------------------------------------------

    def _param(self, index, default=1):
        chunks = self._params.split(";") if self._params else []
        value = chunks[index] if index < len(chunks) and chunks[index] else None
        return max(0, int(value)) if value is not None else default

    def _param_count(self):
        return len(self._params.split(";")) if self._params else 0

    def _apply_csi(self, final):
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
            if mode == 0:
                self._erase(self.row, self.column, self.columns)
                for line in range(self.row + 1, self.rows):
                    self._erase(line, 0, self.columns)
            elif mode == 1:
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
            elif value == 3:
                flags |= ITALIC
            elif value == 4:
                flags |= UNDERLINE
            elif value == 7:
                flags |= REVERSE
            elif value in (21, 22):
                flags &= ~BOLD
            elif value == 23:
                flags &= ~ITALIC
            elif value == 24:
                flags &= ~UNDERLINE
            elif value == 27:
                flags &= ~REVERSE
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
            return (params[index + 1], params[index + 2], params[index + 3]), index + 3
        return None, index

    def _apply_mode(self, enable):
        private = self._params.startswith("?")
        values = [int(chunk) if chunk.isdigit() else 0 for chunk in self._params.lstrip("?").split(";")] if self._params else []
        for value in values:
            if not private:
                continue
            if value == 25:
                self.cursor_visible = enable
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
        self.rows, self.columns = self.rows, self.columns
        self.lines = [self._blank_line() for _ in range(self.rows)]
        self.row = self.column = 0
        self._top, self._bottom = 0, self.rows - 1
        self.style = DEFAULT_STYLE

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
                    elif character == "D":
                        self._line_feed()
                    elif character == "M":
                        self._reverse_line_feed()
                    elif character == "E":
                        self._line_feed()
                        self.column = 0
                    elif character == "c":
                        self._reset()
            elif state == _CSI:
                if character.isdigit() or character == ";" or character == "?":
                    self._params += character
                else:
                    self._state = _GROUND
                    if character.isalpha() or character in "@`~":
                        self._apply_csi(character)
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


def key_to_bytes(key, text, ctrl=False, alt=False):
    """Translate a Qt key event into the bytes sent to the session."""
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
        except OSError:
            data = b""
        if not data:
            self._notifier.setEnabled(False)
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
        try:
            os.write(self.master, payload)
        except OSError:
            pass

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

    def __init__(self, parent=None):
        super().__init__(parent)
        self.screen = VtScreen()
        self.process = None
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
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
        self.screen.feed(text)
        self.update()

    def _on_finished(self, code):
        self.screen.feed(f"\n[session exited with code {code}]")
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
        if self.process is not None:
            self.process.resize(rows, columns)
        super().resizeEvent(event)

    def _brush(self, color, default):
        if color is None:
            return QColor(default)
        if isinstance(color, int):
            rgb = PALETTE.get(color % 16)
            return QColor(default) if rgb is None else QColor(*rgb)
        return QColor(*color)

    def paintEvent(self, event):
        painter = QPainter(self)
        background = QColor("#1c1c1c")
        foreground = QColor("#e6e6e2")
        painter.fillRect(self.rect(), background)
        painter.setFont(self.font)
        for row, line in enumerate(self.screen.lines):
            y = row * self.cell_height
            run_start = 0
            previous = line[0][1]
            for column in range(1, len(line) + 1):
                style = line[column][1] if column < len(line) else None
                if style != previous:
                    flags = previous.flags
                    fill = previous.bg
                    text_color = previous.fg
                    if flags & REVERSE:
                        fill, text_color = text_color, fill
                    painter.fillRect(run_start * self.cell_width, y,
                                     (column - run_start) * self.cell_width,
                                     self.cell_height,
                                     self._brush(fill, "#1c1c1c"))
                    painter.setPen(self._brush(text_color, "#e6e6e2"))
                    text = "".join(line[index][0] for index in range(run_start, column))
                    painter.drawText(run_start * self.cell_width,
                                     y + self._metrics.ascent(), text)
                    run_start = column
                    previous = style
        if self._cursor_shown and self.process is not None and self.process.running:
            painter.fillRect(self.screen.column * self.cell_width,
                             self.screen.row * self.cell_height,
                             self.cell_width, self.cell_height, foreground)

    def keyPressEvent(self, event):
        if self.process is None or not self.process.running:
            super().keyPressEvent(event)
            return
        modifiers = event.modifiers()
        payload = key_to_bytes(
            event.key(), event.text(),
            bool(modifiers & Qt.KeyboardModifier.ControlModifier),
            bool(modifiers & Qt.KeyboardModifier.AltModifier),
        )
        if payload:
            self.process.write(payload)

    def paste(self, text):
        if self.process is not None and self.process.running and text:
            self.process.write(text.encode("utf-8"))


class TerminalWindow(QDialog):
    """A modeless window hosting one embedded session."""

    def __init__(self, command, title, external=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(760, 480)
        self._external = external
        self._closing = False
        layout = QVBoxLayout(self)
        self.pane = TerminalPane()
        self.process = PtyProcess(command, parent=self)
        self.process.finished.connect(self._session_finished)
        self.pane.attach(self.process)
        layout.addWidget(self.pane, 1)
        buttons = QHBoxLayout()
        if external is not None:
            external_button = QPushButton("Open in external terminal")
            external_button.clicked.connect(self._open_external)
            buttons.addWidget(external_button)
        buttons.addStretch(1)
        close_button = QPushButton("Close session")
        close_button.clicked.connect(self.close)
        buttons.addWidget(close_button)
        layout.addLayout(buttons)

    def _open_external(self):
        try:
            self._external()
        except (RuntimeError, ValueError) as error:
            QMessageBox.warning(self, "Chrootables", str(error))
            return
        self.close()

    def closeEvent(self, event):
        # Closing the window ends the session, matching the CLI's rule that
        # closing the terminal releases the image lock.
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
        if self._begin_close():
            super().reject()

    def _session_finished(self, code):
        if self._closing:
            self.close()
