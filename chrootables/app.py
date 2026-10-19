"""Native desktop workspace, recovery and diagnostics interface."""
from __future__ import annotations

import argparse
from datetime import datetime
from functools import lru_cache
from hashlib import sha256
import json
from pathlib import Path
import sys

from PySide6.QtCore import Qt, QEvent, QSettings, QSize, QTimer, QProcess
from PySide6.QtGui import QColor, QFont, QIcon, QKeySequence, QPainter, QPen, QPixmap, QShortcut, QTextCursor
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QFrame, QHBoxLayout, QVBoxLayout,
    QLabel, QPushButton, QLineEdit, QComboBox, QSpinBox, QCheckBox,
    QListWidget, QListWidgetItem, QStackedWidget, QSplitter, QScrollArea,
    QDialog, QDialogButtonBox, QFormLayout, QGridLayout, QMessageBox, QPlainTextEdit,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QProgressBar, QMenu, QFileDialog,
)
from . import __version__
from .backend import DEFAULT_STORE, ROOT, PROFILES, name, shared_folder, session_command, terminal_command
from .client import Client
from .style import STYLE
from .terminal import TerminalWindow


def label(text, role=None, wrap=False):
    widget = QLabel(text)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    if role:
        widget.setObjectName(role)
    widget.setWordWrap(wrap)
    return widget


def button(text, callback=None, role=None):
    widget = QPushButton(text)
    if role:
        widget.setObjectName(role)
    if callback:
        widget.clicked.connect(callback)
    widget.setCursor(Qt.CursorShape.PointingHandCursor)
    return widget


def box(horizontal=False, margins=0, spacing=12):
    widget = QWidget()
    layout = QHBoxLayout(widget) if horizontal else QVBoxLayout(widget)
    layout.setContentsMargins(margins, margins, margins, margins)
    layout.setSpacing(spacing)
    return widget, layout


def clear(layout):
    while layout.count():
        item = layout.takeAt(0)
        if item.widget():
            item.widget().deleteLater()
        elif item.layout():
            clear(item.layout())


def capacity(mib):
    return f"{mib / 1024:g} GiB" if mib >= 1024 else f"{mib} MiB"


def byte_size(value):
    return capacity(round(value / 1048576))


@lru_cache(maxsize=512)
def when(value):
    if not value or value in ("unknown", "never"):
        return "Not yet" if value == "never" else "Unknown"
    try:
        return datetime.fromisoformat(value).strftime("%b %d · %H:%M")
    except ValueError:
        return str(value)


_GLYPH_CACHE = {}


def glyph(kind="box", color="#a8edc9", size=40):
    key = (kind, color, size)
    cached = _GLYPH_CACHE.get(key)
    if cached is not None and not cached.isNull():
        return cached
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(QColor(color), max(1.5, size / 22)))
    painter.setBrush(QColor("#21332e"))
    painter.drawRoundedRect(2, 2, size - 4, size - 4, size / 4, size / 4)
    if kind == "box":
        painter.drawLine(int(size * .28), int(size * .38), int(size * .48), int(size * .53))
        painter.drawLine(int(size * .48), int(size * .53), int(size * .28), int(size * .69))
        painter.drawLine(int(size * .55), int(size * .68), int(size * .74), int(size * .68))
    else:
        painter.setFont(QFont("Noto Sans", int(size * .29), QFont.Weight.Bold))
        painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, kind)
    painter.end()
    _GLYPH_CACHE[key] = pixmap
    return pixmap


class CreateDialog(QDialog):
    def __init__(self, existing, parent=None):
        super().__init__(parent)
        self.existing = existing
        self.setWindowTitle("Create a workspace")
        self.setMinimumWidth(550)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 28, 28, 28)
        layout.setSpacing(16)
        layout.addWidget(label("A fresh place to build.", "title"))
        layout.addWidget(label("A real Debian filesystem, with a lifecycle you control.", "muted"))
        form = QFormLayout()
        form.setSpacing(14)
        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("e.g. python-playground")
        self.name_input.setObjectName("workspaceName")
        self.profile = QComboBox()
        for key, (title, description, suggested) in PROFILES.items():
            self.profile.addItem(title, key)
        self.profile_description = label(PROFILES["minimal"][1], "muted", True)
        self.suite = QComboBox()
        self.suite.addItem("Debian 13 · Trixie", "trixie")
        self.suite.addItem("Debian 12 · Bookworm", "bookworm")
        self.size_input = QSpinBox()
        self.size_input.setRange(1, 1024)
        self.size_input.setValue(2)
        self.size_input.setSuffix(" GiB")
        self.lifecycle = QComboBox()
        self.lifecycle.addItem("Persistent — keep my changes", "persistent")
        self.lifecycle.addItem("Disposable — reset after every session", "ephemeral")
        form.addRow("Workspace name", self.name_input)
        form.addRow("Starting profile", self.profile)
        form.addRow("", self.profile_description)
        form.addRow("Distribution", self.suite)
        form.addRow("Image capacity", self.size_input)
        form.addRow("Session lifecycle", self.lifecycle)
        layout.addLayout(form)
        layout.addWidget(label("Debian packages are verified by debootstrap. Creation downloads a base system and reserves the image capacity on disk.", "muted", True))
        self.error = label("", "banner", True)
        layout.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.create_button = buttons.addButton("Create workspace", QDialogButtonBox.ButtonRole.AcceptRole)
        self.create_button.setObjectName("primary")
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.validate)
        layout.addWidget(buttons)
        self.profile.currentIndexChanged.connect(self.select_profile)
        self.name_input.textChanged.connect(lambda: self.error.clear())
        self.name_input.setFocus()

    def select_profile(self):
        data = PROFILES[self.profile.currentData()]
        self.profile_description.setText(data[1])
        self.size_input.setValue(data[2])

    def validate(self):
        try:
            value = name(self.name_input.text().strip())
            if value in self.existing:
                raise ValueError("That workspace name is already in use.")
        except ValueError as error:
            self.error.setText(str(error))
            return
        self.accept()

    def parameters(self):
        return {"name": self.name_input.text().strip(), "size_mib": self.size_input.value() * 1024,
                "profile": self.profile.currentData(), "suite": self.suite.currentData(),
                "lifecycle": self.lifecycle.currentData()}


class ValueDialog(QDialog):
    def __init__(self, title, description, field="Name", value="", minimum=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(460)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)
        layout.addWidget(label(title, "detailTitle"))
        layout.addWidget(label(description, "muted", True))
        layout.addWidget(label(field))
        self.input = QSpinBox() if minimum is not None else QLineEdit()
        if minimum is not None:
            self.input.setRange(minimum, 1048576)
            self.input.setValue(minimum)
            self.input.setSuffix(" MiB")
        else:
            self.input.setText(value)
        layout.addWidget(self.input)
        self.error = label("", "banner", True)
        layout.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setObjectName("primary")
        buttons.accepted.connect(self.validate)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def validate(self):
        if isinstance(self.input, QLineEdit):
            try:
                name(self.input.text().strip())
            except ValueError as error:
                self.error.setText(str(error))
                return
        self.accept()

    def value(self):
        return self.input.value() if isinstance(self.input, QSpinBox) else self.input.text().strip()


class WorkspaceRow(QWidget):
    def __init__(self, item, favorite=False):
        super().__init__()
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setStyleSheet("background: transparent;")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(18, 15, 18, 15)
        self.icon_label = label("")
        layout.addWidget(self.icon_label)
        text, column = box(spacing=5)
        self.title_label = label("", "section")
        self.meta_label = label("", "muted")
        column.addWidget(self.title_label)
        column.addWidget(self.meta_label)
        layout.addWidget(text, 1)
        state, status = box(spacing=5)
        self.status_label = label("", "badge")
        self.mode_label = label("", "muted")
        self.mode_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        status.addWidget(self.status_label)
        status.addWidget(self.mode_label)
        layout.addWidget(state)
        self._state = None
        self.update_row(item, favorite)

    def update_row(self, item, favorite=False):
        profile = item.get("profile", "minimal")
        suite = item.get("suite", "unknown")
        size_mib = item["size_mib"]
        busy = item["status"] == "busy"
        ephemeral = item["lifecycle"] == "ephemeral"
        state = (item["name"], favorite, profile, suite, size_mib, busy, ephemeral)
        if state == self._state:
            return
        self._state = state
        self.icon_label.setPixmap(glyph({"python": "Py", "build": "C+", "network": "~"}.get(profile, "box"), size=44))
        self.title_label.setText(("★  " if favorite else "") + item["name"])
        self.meta_label.setText(f"Debian {suite}  ·  {PROFILES.get(profile, (profile,))[0]}  ·  {capacity(size_mib)}")
        self.status_label.setText("●  In use" if busy else "●  Ready")
        self.mode_label.setText("Disposable" if ephemeral else "Persistent")


class Window(QMainWindow):
    def __init__(self, store=DEFAULT_STORE, demo=False):
        super().__init__()
        self.setWindowTitle("Chrootables")
        self.setWindowIcon(QIcon(glyph(size=64)))
        self.resize(1360, 920)
        self.setMinimumSize(1180, 780)
        self.store = Path(store)
        self.demo = demo
        self.settings = QSettings("Chrootables", "Desktop")
        self.favorites = set(self.settings.value("favorites", [], type=list))
        self.data = {"images": [], "snapshots": [], "activity": [], "free_bytes": 0}
        self.selected = None
        self.network_choices = {}
        self.restricted_choices = {}
        self.shared_folder_choices = {}
        self.session_settings_prefix = "sessions/" + sha256(str(self.store.resolve()).encode()).hexdigest()
        self.busy = False
        self.refreshing = False
        self.pending_action = None
        self.last_task = ""
        self.health_ready = False
        self.store_locked = False
        self.current_view = 0
        self._detail_state = None
        self._render_signature = None
        self.terminals = []
        self.client = Client(store, self)
        self.client.ready.connect(self.connected)
        self.client.disconnected.connect(self.disconnected)
        self.client.output.connect(lambda identifier, text: self.append_output(text))
        self.build_ui()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._poll_timer)
        self.timer.start(6000)
        for keys, callback in [("Ctrl+N", self.create), ("Ctrl+F", self.focus_search), ("F5", self.refresh), ("Ctrl+Return", self.open_terminal)]:
            shortcut = QShortcut(QKeySequence(keys), self)
            shortcut.activated.connect(callback)
        if demo:
            self.load_demo()
        else:
            QTimer.singleShot(0, self.client.start)

    def build_ui(self):
        central, horizontal = box(horizontal=True, spacing=0)
        self.setCentralWidget(central)
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(218)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(20, 30, 20, 24)
        side.setSpacing(9)
        logo = label("")
        logo.setPixmap(glyph(size=45))
        side.addWidget(logo)
        side.addSpacing(6)
        side.addWidget(label("chrootables", "brand"))
        side.addWidget(label("A little space. No limits.", "muted"))
        side.addSpacing(35)
        side.addWidget(label("WORKBENCH", "eyebrow"))
        self.nav = []
        for index, title in enumerate(["▦   Workspaces", "◷   Recovery", "≡   Activity", "＋   Host health"]):
            control = button(title, lambda checked=False, i=index: self.navigate(i), "nav")
            control.setCheckable(True)
            self.nav.append(control)
            side.addWidget(control)
        self.nav[0].setChecked(True)
        side.addStretch()
        side.addWidget(label("LOCAL ENGINE", "eyebrow"))
        side.addWidget(label("Debian image workspaces", "muted"))
        self.access_label = label("Connecting…", "muted")
        side.addWidget(self.access_label)
        self.unlock_button = button("Unlock workspace store", self.unlock)
        side.addWidget(self.unlock_button)
        side.addSpacing(14)
        side.addWidget(label(f"DESKTOP {__version__}", "eyebrow"))
        horizontal.addWidget(sidebar)

        content, self.content_layout = box(margins=30, spacing=18)
        horizontal.addWidget(content, 1)
        top, row = box(horizontal=True)
        headings, titles = box(spacing=6)
        self.eyebrow = label("WORKSPACE LIBRARY", "eyebrow")
        self.heading = label("Your Linux, on your terms.", "title")
        self.subtitle = label("Build, break, rewind. Keep your host out of the experiment.", "subtitle")
        titles.addWidget(self.eyebrow)
        titles.addWidget(self.heading)
        titles.addWidget(self.subtitle)
        row.addWidget(headings, 1)
        self.refresh_button = button("↻", self.refresh)
        self.refresh_button.setToolTip("Refresh · F5")
        self.refresh_button.setAccessibleName("Refresh workspaces")
        row.addWidget(self.refresh_button)
        self.new_button = button("＋  New workspace", self.create, "primary")
        row.addWidget(self.new_button)
        self.content_layout.addWidget(top)

        self.notice = QFrame()
        self.notice.setObjectName("notice")
        notice_row = QHBoxLayout(self.notice)
        self.notice_text = label("", "banner", True)
        notice_row.addWidget(self.notice_text, 1)
        self.notice_action = button("View host health", lambda: self.navigate(3))
        notice_row.addWidget(self.notice_action)
        self.notice.hide()
        self.content_layout.addWidget(self.notice)

        self.pages = QStackedWidget()
        self.content_layout.addWidget(self.pages, 1)
        self.build_library()
        self.build_recovery()
        self.build_activity()
        self.build_health()

        self.task_panel, task_layout = box(spacing=8)
        task_top, task_row = box(horizontal=True)
        self.task_title = label("Task output", "section")
        task_row.addWidget(self.task_title, 1)
        task_row.addWidget(button("Copy output", lambda: QApplication.clipboard().setText(self.task_output.toPlainText()), "quiet"))
        self.hide_task = button("Hide", self.task_panel.hide, "quiet")
        task_row.addWidget(self.hide_task)
        task_layout.addWidget(task_top)
        self.task_progress = QProgressBar()
        self.task_progress.setRange(0, 0)
        self.task_progress.setTextVisible(False)
        task_layout.addWidget(self.task_progress)
        self.task_output = QPlainTextEdit()
        self.task_output.setReadOnly(True)
        self.task_output.setMaximumBlockCount(4000)
        self.task_output.setFixedHeight(165)
        task_layout.addWidget(self.task_output)
        self.task_panel.hide()
        self.content_layout.addWidget(self.task_panel)
        footer, footer_row = box(horizontal=True)
        self.status = label("Reading local workspaces…", "muted")
        footer_row.addWidget(self.status, 1)
        self.task_reopen = button("Task output ↗", self.task_panel.show, "quiet")
        self.task_reopen.hide()
        footer_row.addWidget(self.task_reopen)
        footer_row.addWidget(label("Ctrl N  create    /    Ctrl F  search", "muted"))
        self.content_layout.addWidget(footer)

    def build_library(self):
        page, layout = box(spacing=20)
        metrics, row = box(horizontal=True, spacing=12)
        self.metrics = []
        for title, subtitle in [("WORKSPACES", "Your local environments"), ("IN USE", "Sessions or operations"), ("RECOVERY POINTS", "Room to experiment"), ("HOST FREE", "Available for images")]:
            card = QFrame()
            card.setObjectName("card")
            column = QVBoxLayout(card)
            column.setContentsMargins(17, 16, 17, 16)
            column.addWidget(label(title, "eyebrow"))
            value = label("—", "metric")
            self.metrics.append(value)
            column.addWidget(value)
            column.addWidget(label(subtitle, "muted"))
            row.addWidget(card, 1)
        layout.addWidget(metrics)
        tools, row = box(horizontal=True)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search workspaces, profiles or releases…")
        self.search.setAccessibleName("Search workspaces")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.render_library)
        row.addWidget(self.search, 1)
        self.filter = QComboBox()
        self.filter.setAccessibleName("Filter workspaces")
        self.filter.addItems(["All workspaces", "Persistent", "Disposable", "Favorites", "In use"])
        self.filter.currentIndexChanged.connect(self.render_library)
        row.addWidget(self.filter)
        layout.addWidget(tools)
        self.library_stack = QStackedWidget()
        layout.addWidget(self.library_stack, 1)
        self.splitter = QSplitter()
        self.splitter.setHandleWidth(16)
        self.workspaces = QListWidget()
        self.workspaces.setMinimumWidth(390)
        self.workspaces.setUniformItemSizes(True)
        self.workspaces.currentItemChanged.connect(self.select_workspace)
        self.workspaces.itemDoubleClicked.connect(lambda _: self.open_terminal())
        self.splitter.addWidget(self.workspaces)
        self.details = QScrollArea()
        self.details.setWidgetResizable(True)
        self.details.setMinimumWidth(320)
        self.build_detail_card()
        self.splitter.addWidget(self.details)
        self.splitter.setSizes([620, 350])
        self.library_stack.addWidget(self.splitter)
        empty, empty_layout = box(spacing=18)
        empty_layout.addStretch()
        icon = label("")
        icon.setPixmap(glyph(size=80))
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(icon)
        self.empty_title = label("Make room for your next idea.", "title")
        self.empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(self.empty_title)
        self.empty_text = label("Start with a clean Debian image or a ready-to-build profile.\nKeep your changes, or start fresh every time.", "muted")
        self.empty_text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(self.empty_text)
        self.empty_create = button("Create your first workspace", lambda: self.unlock() if self.store_locked else self.create(), "primary")
        empty_layout.addWidget(self.empty_create, 0, Qt.AlignmentFlag.AlignCenter)
        empty_layout.addStretch()
        self.library_stack.addWidget(empty)
        self.pages.addWidget(page)

    def make_table(self, columns):
        table = QTableWidget(0, len(columns))
        table.setHorizontalHeaderLabels(columns)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        table.verticalHeader().hide()
        table.verticalHeader().setDefaultSectionSize(54)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setShowGrid(False)
        table.setAlternatingRowColors(False)
        return table

    def build_recovery(self):
        page, layout = box(spacing=16)
        layout.addWidget(label("Restore with a safety net", "section"))
        layout.addWidget(label("A restore first saves your current image. Recovery points survive workspace deletion.", "muted", True))
        self.recovery_table = self.make_table(["Workspace", "Recovery point", "Capacity", "Saved"])
        layout.addWidget(self.recovery_table, 1)
        self.recovery_empty = label("No recovery points yet. Select a workspace and choose Save snapshot.", "muted")
        layout.addWidget(self.recovery_empty)
        actions, row = box(horizontal=True)
        row.addStretch()
        self.drop_snapshot_button = button("Delete recovery point", self.drop_snapshot, "danger")
        self.restore_button = button("Restore workspace", self.restore, "primary")
        row.addWidget(self.drop_snapshot_button)
        row.addWidget(self.restore_button)
        layout.addWidget(actions)
        self.recovery_table.itemSelectionChanged.connect(self.update_controls)
        self.pages.addWidget(page)

    def build_activity(self):
        page, layout = box(spacing=16)
        layout.addWidget(label("The story of your workspaces", "section"))
        layout.addWidget(label("Completed operations, session history and their full diagnostic output.", "muted"))
        self.activity_table = self.make_table(["When", "Operation", "Workspace", "Result"])
        self.activity_table.itemDoubleClicked.connect(lambda _: self.activity_log())
        layout.addWidget(self.activity_table, 1)
        self.activity_empty = label("Your first operation will appear here.", "muted")
        layout.addWidget(self.activity_empty)
        row_widget, row = box(horizontal=True)
        row.addStretch()
        row.addWidget(button("View operation log", self.activity_log))
        layout.addWidget(row_widget)
        self.pages.addWidget(page)

    def build_health(self):
        page, layout = box(spacing=16)
        header, row = box(horizontal=True)
        row.addWidget(label("Ready before you press Create", "section"), 1)
        row.addWidget(button("Run checks", self.check_health))
        self.setup_button = button("Install missing dependencies", self.setup, "primary")
        row.addWidget(self.setup_button)
        layout.addWidget(header)
        layout.addWidget(label("Checks host commands, filesystem support and Linux namespaces. Mount permissions are tested when a workspace starts.", "muted", True))
        self.health_output = QPlainTextEdit()
        self.health_output.setReadOnly(True)
        layout.addWidget(self.health_output, 1)
        layout.addWidget(label("WORKSPACE STORE", "eyebrow"))
        layout.addWidget(label(str(self.store), "muted"))
        layout.addWidget(label("Trust model: workspaces run as root and share host devices. Use them for trusted development and testing; they are not a security boundary for hostile software.", "muted", True))
        self.pages.addWidget(page)

    def navigate(self, index):
        self.current_view = index
        self.pages.setCurrentIndex(index)
        titles = [
            ("WORKSPACE LIBRARY", "Your Linux, on your terms.", "Build, break, rewind. Keep your host out of the experiment."),
            ("RECOVERY LIBRARY", "Go ahead. Try the idea.", "Your checkpoints are here when you need a fresh start."),
            ("OPERATION HISTORY", "Nothing happens in the dark.", "A clear trail from the first image to the last session."),
            ("HOST HEALTH", "A solid place to start.", "Understand your machine before starting a workspace."),
        ]
        for widget, text in zip((self.eyebrow, self.heading, self.subtitle), titles[index]):
            widget.setText(text)
        for i, control in enumerate(self.nav):
            control.setChecked(i == index)
        if index == 3 and not self.health_output.toPlainText():
            self.check_health()

    def connected(self, writable):
        self.access_label.setText("●  Administrator connected" if writable else "●  Read-only connection")
        self.unlock_button.setText("Lock workspace store" if writable else "Unlock workspace store")
        self.unlock_button.setEnabled(True)
        self.refresh()
        self.check_health()
        if writable and self.pending_action:
            action, params = self.pending_action
            self.pending_action = None
            self.run_operation(action, params)

    def disconnected(self, error):
        self.pending_action = None
        self.access_label.setText("○  Disconnected")
        self.unlock_button.setEnabled(True)
        self.unlock_button.setText("Reconnect / unlock")
        self.show_notice(error)
        self.status.setText(error)

    def unlock(self):
        if self.demo or self.busy:
            return
        if self.client.writable:
            self.client.start(False)
        else:
            self.unlock_button.setEnabled(False)
            self.access_label.setText("Awaiting authentication…")
            self.client.start(True)

    def refresh(self):
        if self.demo or self.refreshing or not self.client.is_ready:
            return
        self.refreshing = True
        def done(ok, data):
            self.refreshing = False
            if ok:
                was_locked = self.store_locked
                self.store_locked = False
                self.empty_create.setText("Create your first workspace")
                signature = (data, self.busy, self.demo, was_locked)
                self.data = data
                if signature != self._render_signature:
                    self._render_signature = signature
                    self.render()
                self.status.setText(f"{len(data['images'])} workspaces  ·  {self.store}")
                if self.client.writable and self.health_ready:
                    self.notice.hide()
            elif getattr(data, "code", None) == "permission_required" and not self.client.writable:
                self.store_locked = True
                self.library_stack.setCurrentIndex(1)
                self.empty_title.setText("Unlock your workspace library.")
                self.empty_text.setText("Your images and recovery points are stored with administrator permissions.\nAuthenticate once to view and manage them in this window.")
                self.empty_create.setText("Unlock workspace store")
                self.empty_create.show()
                self.notice.hide()
                for metric in self.metrics:
                    metric.setText("—")
                self.status.setText("Workspace store locked · unlock to view and manage your images.")
            else:
                self.show_notice("Could not read the workspace store: " + str(data))
                self.status.setText("Store unavailable. See the message above.")
        self.client.request("inventory", {}, done)

    def render(self):
        images = self.data["images"]
        values = [str(len(images)), str(sum(x["status"] == "busy" for x in images)), str(len(self.data["snapshots"])), byte_size(self.data["free_bytes"])]
        for widget, value in zip(self.metrics, values):
            widget.setText(value)
        self.render_library()
        self.fill_table(self.recovery_table, [[x["name"] + (" · deleted" if x.get("orphan") else ""), x["label"], capacity(x["size_mib"]), when(x["created_at"])] for x in self.data["snapshots"]],
                        [[x["name"], x["label"]] for x in self.data["snapshots"]])
        self.recovery_empty.setVisible(not self.data["snapshots"])
        self.fill_table(self.activity_table, [[when(x["time"]), x["action"].replace("-", " ").capitalize(), x["name"], "Completed" if x["status"] == "ok" else "Failed"] for x in self.data["activity"]],
                        [[x["time"], x["action"], x["name"], x["log"]] for x in self.data["activity"]])
        self.activity_empty.setVisible(not self.data["activity"])
        self.update_controls()

    def fill_table(self, table, rows, keys):
        if table.property("rendered_rows") == [rows, keys]:
            return
        table.setProperty("rendered_rows", [rows, keys])
        current = table.item(table.currentRow(), 0)
        selected = current.data(Qt.ItemDataRole.UserRole) if current else None
        table.setUpdatesEnabled(False)
        blocked = table.blockSignals(True)
        table.clearSelection()
        table.setCurrentItem(None)
        table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c, value in enumerate(row):
                item = QTableWidgetItem(str(value))
                if value == "Failed":
                    item.setForeground(QColor("#efa79d"))
                if c == 0:
                    item.setData(Qt.ItemDataRole.UserRole, keys[r])
                table.setItem(r, c, item)
        if selected in keys:
            table.selectRow(keys.index(selected))
        table.blockSignals(blocked)
        table.setUpdatesEnabled(True)

    def build_detail_card(self):
        card = QFrame()
        card.setObjectName("detail")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)
        top, row = box(horizontal=True)
        top.setStyleSheet("background: transparent;")
        row.addWidget(label("WORKSPACE", "eyebrow"), 1)
        self.favorite_button = button("☆", self.favorite, "quiet")
        row.addWidget(self.favorite_button)
        layout.addWidget(top)
        self.detail_title = label("", "detailTitle", True)
        layout.addWidget(self.detail_title)
        self.detail_lifecycle = label("", "muted")
        layout.addWidget(self.detail_lifecycle)
        self.terminal_button = button("›_  Open terminal", self.open_terminal, "primary")
        self.terminal_button.setToolTip("Open in your native terminal · Ctrl Return")
        layout.addWidget(self.terminal_button)
        self.offline = QCheckBox("Isolate network for this session")
        self.offline.setToolTip("Only loopback is available; internet and host networking are disconnected.")
        self.offline.toggled.connect(lambda checked: self.network_choices.__setitem__(self.selected, checked) if self.selected else None)
        layout.addWidget(self.offline)
        self.restricted = QCheckBox("Restrict devices for this session")
        self.restricted.setToolTip("The session gets a minimal /dev without host device nodes. Fewer tools will work; this is still not a security sandbox.")
        self.restricted.toggled.connect(lambda checked: self.restricted_choices.__setitem__(self.selected, checked) if self.selected else None)
        layout.addWidget(self.restricted)
        layout.addWidget(label("SHARED FOLDER · /shared", "eyebrow"))
        self.share_path = QLineEdit()
        self.share_path.setObjectName("sharedFolderPath")
        self.share_path.setReadOnly(True)
        self.share_path.setPlaceholderText("No host folder shared")
        share_actions, share_row = box(horizontal=True, spacing=8)
        self.share_choose = button("Choose…", self.choose_shared_folder)
        self.share_choose.setToolTip("Choose a host folder to share at /shared.")
        self.share_clear = button("Clear", self.clear_shared_folder)
        share_row.addWidget(self.share_path, 1)
        share_row.addWidget(self.share_choose)
        layout.addWidget(share_actions)
        self.share_read_only = QCheckBox("Read-only shared folder")
        self.share_read_only.toggled.connect(lambda checked: self.set_shared_folder_read_only(self.selected, checked) if self.selected else None)
        access, access_row = box(horizontal=True, spacing=8)
        access_row.addWidget(self.share_read_only, 1)
        access_row.addWidget(self.share_clear)
        layout.addWidget(access)
        layout.addWidget(label("Shared files stay on the host, even after a disposable session. New files belong to root.", "muted", True))
        self.trial_button = button("Try a disposable session", lambda: self.open_terminal(disposable=True))
        layout.addWidget(self.trial_button)
        layout.addSpacing(2)
        metadata = QWidget()
        metadata.setStyleSheet("background: transparent;")
        grid = QGridLayout(metadata)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(16)
        self.meta_labels = {}
        for title, r, c, span in [("DISTRIBUTION", 0, 0, 1), ("PROFILE", 0, 1, 1), ("STORAGE", 1, 0, 2), ("LAST SESSION", 2, 0, 1), ("RECOVERY", 2, 1, 1)]:
            field, field_layout = box(spacing=4)
            field.setStyleSheet("background: transparent;")
            field_layout.addWidget(label(title, "eyebrow"))
            val_label = label("", None, True)
            self.meta_labels[title] = val_label
            field_layout.addWidget(val_label)
            grid.addWidget(field, r, c, 1, span)
        layout.addWidget(metadata)
        layout.addStretch()
        actions, row = box(horizontal=True, spacing=8)
        self.save_snapshot_button = button("Save snapshot", self.snapshot)
        row.addWidget(self.save_snapshot_button, 1)
        self.more_button = button("•••")
        self.more_button.setToolTip("Clone, resize, export and maintenance")
        self.more_button.setAccessibleName("More workspace actions")
        menu = QMenu(self.more_button)
        self.mutating_actions = []
        self.mode_action = None
        for title, callback in [("Clone workspace…", self.clone), ("Expand capacity…", self.resize_image),
                                ("Switch to disposable", self.change_mode),
                                ("Export filesystem archive…", self.export), ("View latest log", self.workspace_log),
                                ("Copy image path", lambda: QApplication.clipboard().setText((self.selected_data() or {}).get("path", ""))),
                                ("Check and repair…", self.repair), ("Delete workspace…", self.delete)]:
            action = menu.addAction(title)
            action.triggered.connect(callback)
            if callback == self.change_mode:
                self.mode_action = action
            if title not in ("View latest log", "Copy image path"):
                self.mutating_actions.append(action)
        self.more_button.setMenu(menu)
        row.addWidget(self.more_button)
        layout.addWidget(actions)
        self.detail_card = card
        self.details.setWidget(card)

    def render_library(self):
        if self.store_locked:
            return
        query = self.search.text().casefold().strip()
        mode = self.filter.currentIndex()
        images = self.data["images"]
        if query:
            images = [x for x in images if query in f"{x.get('name', '')} {x.get('profile', '')} {x.get('suite', '')}".casefold()]
        else:
            images = list(images)
        if mode == 1:
            images = [x for x in images if x["lifecycle"] == "persistent"]
        elif mode == 2:
            images = [x for x in images if x["lifecycle"] == "ephemeral"]
        elif mode == 3:
            images = [x for x in images if x["name"] in self.favorites]
        elif mode == 4:
            images = [x for x in images if x["status"] == "busy"]
        images.sort(key=lambda x: (x["name"] not in self.favorites, x["name"].casefold()))
        selected = self.selected
        self.workspaces.setUpdatesEnabled(False)
        self.workspaces.blockSignals(True)
        while self.workspaces.count() > len(images):
            row = self.workspaces.count() - 1
            old_item = self.workspaces.item(row)
            old_widget = self.workspaces.itemWidget(old_item)
            self.workspaces.takeItem(row)
            if old_widget:
                old_widget.deleteLater()
        selected_item = None
        for index, data in enumerate(images):
            fav = data["name"] in self.favorites
            if index < self.workspaces.count():
                item = self.workspaces.item(index)
                item.setData(Qt.ItemDataRole.UserRole, data)
                row_widget = self.workspaces.itemWidget(item)
                if isinstance(row_widget, WorkspaceRow):
                    row_widget.update_row(data, fav)
                else:
                    self.workspaces.setItemWidget(item, WorkspaceRow(data, fav))
            else:
                item = QListWidgetItem()
                item.setData(Qt.ItemDataRole.UserRole, data)
                item.setSizeHint(QSize(0, 93))
                self.workspaces.addItem(item)
                self.workspaces.setItemWidget(item, WorkspaceRow(data, fav))
            item.setData(
                Qt.ItemDataRole.AccessibleTextRole,
                f"{data['name']}, Debian {data.get('suite', 'unknown')}, {data['status']}, {data['lifecycle']}",
            )
            if data["name"] == selected:
                selected_item = item
        self.workspaces.blockSignals(False)
        self.workspaces.setUpdatesEnabled(True)
        self.library_stack.setCurrentIndex(0 if images else 1)
        if images:
            target = selected_item or self.workspaces.item(0)
            if self.workspaces.currentItem() is target:
                self.select_workspace(target)
            else:
                self.workspaces.setCurrentItem(target)
        else:
            self.selected = None
            filtered = bool(self.data["images"])
            self.empty_title.setText("No matching workspaces." if filtered else "Make room for your next idea.")
            self.empty_text.setText("Try a different search or filter." if filtered else "Start with a clean Debian image or a ready-to-build profile.\nKeep your changes, or start fresh every time.")
            self.empty_create.setVisible(not filtered)

    def select_workspace(self, item, previous=None):
        if item is None:
            return
        data = item.data(Qt.ItemDataRole.UserRole)
        same_selection = self.selected == data["name"]
        scroll_position = self.details.verticalScrollBar().value() if same_selection else 0
        self.selected = data["name"]
        fav = self.selected in self.favorites
        isolated = self.network_choices.get(self.selected, False)
        restricted = self.restricted_choices.get(self.selected, False)
        share, read_only = self.shared_folder_choice(self.selected)
        can_configure = not self.demo and not self.busy and data["status"] != "busy"
        state = (
            self.selected, data.get("status"), data.get("lifecycle"), data.get("suite"),
            data.get("profile"), data.get("size_mib"), data.get("allocated_bytes"),
            data.get("last_used_at"), data.get("snapshots"), data.get("path"),
            fav, can_configure, isolated, share, read_only,
        )
        if state != self._detail_state:
            self._detail_state = state
            self.favorite_button.setText("★" if fav else "☆")
            fav_label = "Remove from favorites" if fav else "Add to favorites"
            self.favorite_button.setToolTip(fav_label)
            self.favorite_button.setAccessibleName(fav_label)
            self.detail_title.setText(self.selected)
            lifecycle = data["lifecycle"]
            self.detail_lifecycle.setText("A fresh start, every session." if lifecycle == "ephemeral" else "Your changes stay with you.")
            self.terminal_button.setEnabled(can_configure)
            self.offline.blockSignals(True)
            self.offline.setChecked(isolated)
            self.offline.blockSignals(False)
            self.restricted.blockSignals(True)
            self.restricted.setChecked(restricted)
            self.restricted.blockSignals(False)
            self.share_path.setText(share)
            self.share_path.setToolTip(share or "Choose a host folder to make it available at /shared.")
            self.share_choose.setEnabled(can_configure)
            self.share_clear.setEnabled(can_configure and bool(share))
            self.share_read_only.blockSignals(True)
            self.share_read_only.setChecked(read_only)
            self.share_read_only.blockSignals(False)
            self.share_read_only.setEnabled(can_configure and bool(share))
            self.trial_button.setEnabled(can_configure)
            profile = data.get("profile", "minimal")
            self.meta_labels["DISTRIBUTION"].setText(f"Debian {data.get('suite', 'unknown')}")
            self.meta_labels["PROFILE"].setText(PROFILES.get(profile, (profile,))[0])
            self.meta_labels["STORAGE"].setText(f"{capacity(data['size_mib'])} capacity · {byte_size(data['allocated_bytes'])} allocated")
            self.meta_labels["LAST SESSION"].setText(when(data.get("last_used_at")))
            self.meta_labels["RECOVERY"].setText(f"{data['snapshots']} snapshots")
            self.save_snapshot_button.setEnabled(can_configure)
            self.mode_action.setText("Switch to " + ("persistent" if lifecycle == "ephemeral" else "disposable"))
            for action in self.mutating_actions:
                action.setEnabled(can_configure)
        if not same_selection or self.details.verticalScrollBar().value() != scroll_position:
            self.details.verticalScrollBar().setValue(scroll_position)

    def selected_data(self):
        return next((x for x in self.data["images"] if x["name"] == self.selected), None)

    def shared_folder_choice(self, target):
        if target not in self.shared_folder_choices:
            key = self.session_settings_prefix + "/" + target
            path = "" if self.demo else self.settings.value(key + "/folder", "", type=str)
            read_only = False if self.demo else self.settings.value(key + "/read_only", False, type=bool)
            self.shared_folder_choices[target] = (path, bool(path) and read_only)
        return self.shared_folder_choices[target]

    def save_shared_folder(self, target, path, read_only=False):
        read_only = bool(path) and read_only
        self.shared_folder_choices[target] = (path, read_only)
        if not self.demo:
            key = self.session_settings_prefix + "/" + target
            if path:
                self.settings.setValue(key + "/folder", path)
                self.settings.setValue(key + "/read_only", read_only)
            else:
                self.settings.remove(key)

    def choose_shared_folder(self):
        target = self.selected
        if not target or self.demo or self.busy:
            return
        previous, read_only = self.shared_folder_choice(target)
        path = QFileDialog.getExistingDirectory(self, "Share a host folder at /shared", previous or str(Path.home()))
        if path:
            try:
                path = shared_folder(path)
            except ValueError as error:
                self.show_error(str(error))
                return
            self.save_shared_folder(target, path, read_only)
            self.select_workspace(self.workspaces.currentItem())

    def clear_shared_folder(self):
        if self.selected and not self.demo and not self.busy:
            self.save_shared_folder(self.selected, "")
            self.select_workspace(self.workspaces.currentItem())

    def set_shared_folder_read_only(self, target, checked):
        path, _ = self.shared_folder_choice(target)
        self.save_shared_folder(target, path, checked)

    def favorite(self):
        if self.selected in self.favorites:
            self.favorites.remove(self.selected)
        elif self.selected:
            self.favorites.add(self.selected)
        if not self.demo:
            self.settings.setValue("favorites", sorted(self.favorites))
        self.render_library()

    def focus_search(self):
        self.navigate(0)
        self.search.setFocus()
        self.search.selectAll()

    def create(self):
        if self.busy:
            return
        dialog = CreateDialog({x["name"] for x in self.data["images"]}, self)
        if dialog.exec():
            self.perform("create", dialog.parameters())

    def snapshot(self):
        target = self.selected
        if not target:
            return
        dialog = ValueDialog("Save a recovery point", "Capture this workspace before your next experiment. The workspace must be offline.", "Snapshot label", datetime.now().strftime("checkpoint-%Y%m%d-%H%M"), parent=self)
        if dialog.exec():
            self.perform("snapshot", {"name": target, "target": dialog.value()})

    def clone(self):
        target = self.selected
        if not target:
            return
        dialog = ValueDialog("Branch into a new workspace", "An independent copy of the filesystem and profile. The original stays intact.", "New workspace name", target[:58] + "-copy", parent=self)
        if dialog.exec():
            self.perform("clone", {"name": target, "target": dialog.value()})

    def resize_image(self):
        data = self.selected_data()
        if not data or data["size_mib"] >= 1048576:
            return
        dialog = ValueDialog("Give it more room", f"Current capacity: {capacity(data['size_mib'])}. Images can expand up to 1 TiB.", "New capacity", minimum=min(data["size_mib"] + 1024, 1048576), parent=self)
        if dialog.exec():
            self.perform("resize", {"name": data["name"], "size_mib": dialog.value()})

    def change_mode(self):
        data = self.selected_data()
        if data:
            self.perform("mode", {"name": self.selected, "lifecycle": "persistent" if data["lifecycle"] == "ephemeral" else "ephemeral"})

    def confirm(self, title, text):
        return QMessageBox.question(self, title, text, QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Cancel) == QMessageBox.StandardButton.Yes

    def delete(self):
        if not self.selected:
            return
        target = self.selected
        dialog = ValueDialog("Delete workspace", f"Permanently remove {target}'s image. Saved snapshots and exports remain available. Type the workspace name to continue.", "Confirm workspace name", parent=self)
        if dialog.exec():
            if dialog.value() != target:
                self.show_error("The name did not match. Nothing was deleted.")
                return
            self.perform("delete", {"name": target, "confirmed": True})

    def repair(self):
        target = self.selected
        if target and self.confirm("Repair filesystem", "Forced ext4 repair can change filesystem data. Save a snapshot first if you need a recovery point. Continue?"):
            self.perform("repair", {"name": target, "confirmed": True})

    def export(self):
        target = self.selected
        if not target:
            return
        destination, _ = QFileDialog.getSaveFileName(self, "Export filesystem", str(Path.home() / (target + ".tar.gz")), "Compressed archive (*.tar.gz)")
        if destination:
            if Path(destination).exists():
                self.show_error("Choose a new filename. Exports never overwrite an existing file.")
                return
            self.perform("export", {"name": target, "destination": destination})

    def chosen_snapshot(self):
        row = self.recovery_table.currentRow()
        return self.data["snapshots"][row] if 0 <= row < len(self.data["snapshots"]) else None

    def restore(self):
        item = self.chosen_snapshot()
        if item and self.confirm("Restore workspace", f"Restore {item['name']} from {item['label']}?\n\nThe current image, if present, will be saved as a before-restore recovery point."):
            self.perform("restore", {"name": item["name"], "target": item["label"], "confirmed": True})

    def drop_snapshot(self):
        item = self.chosen_snapshot()
        if item and self.confirm("Delete recovery point", f"Permanently delete {item['label']} for {item['name']}? This cannot be undone."):
            self.perform("drop-snapshot", {"name": item["name"], "target": item["label"], "confirmed": True})

    def perform(self, action, params):
        if self.demo:
            self.show_error("This is a read-only tour. Launch Chrootables without --demo to manage real workspaces.")
            return
        if self.busy:
            return
        if not self.client.writable:
            self.pending_action = (action, params)
            self.unlock()
            return
        self.run_operation(action, params)

    def run_operation(self, action, params):
        self.busy = True
        self.last_task = action.replace("-", " ").capitalize() + (" · " + params["name"] if params.get("name") else "")
        self.task_title.setText(self.last_task)
        self.task_output.clear()
        self.task_panel.show()
        self.task_reopen.show()
        self.task_progress.show()
        self.update_controls()
        self.render_library()
        def done(ok, result):
            self.busy = False
            self.task_progress.hide()
            self.task_title.setText(("✓  " if ok else "!  ") + self.last_task + (" completed" if ok else " failed"))
            if not ok:
                self.append_output("\n" + str(result) + "\n")
                self.task_panel.show()
            self.render_library()
            if ok and action == "create":
                self.selected = params["name"]
                self.navigate(0)
            self.update_controls()
            self.refresh()
            if action == "setup" or not self.health_ready:
                self.check_health()
        self.client.request(action, params, done)

    def update_controls(self):
        self.new_button.setEnabled(not self.busy)
        self.empty_create.setEnabled(not self.busy)
        self.unlock_button.setEnabled(not self.busy and not self.demo)
        self.setup_button.setEnabled(not self.busy and not self.demo and not self.health_ready)
        has_snapshot = self.chosen_snapshot() is not None
        self.restore_button.setEnabled(has_snapshot and not self.busy and not self.demo)
        self.drop_snapshot_button.setEnabled(has_snapshot and not self.busy and not self.demo)

    def append_output(self, text):
        cursor = self.task_output.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(text)
        self.task_output.setTextCursor(cursor)
        self.task_output.ensureCursorVisible()

    def open_terminal(self, checked=False, disposable=False):
        data = self.selected_data()
        if not data or self.demo or self.busy or data["status"] == "busy":
            return
        try:
            share, read_only = self.shared_folder_choice(self.selected)
            keyword = dict(disposable=disposable, isolated=self.offline.isChecked(),
                           restricted_dev=self.restricted.isChecked(),
                           share=share or None, read_only=read_only)
            command = session_command(self.selected, self.store, **keyword)
        except ValueError as error:
            self.show_error(str(error))
            return

        def open_external():
            external = terminal_command(self.selected, self.store, **keyword)
            started, _ = QProcess.startDetached(external[0], external[1:])
            if not started:
                raise RuntimeError("Could not start the terminal.")

        window = TerminalWindow(command, f"Chrootables · {self.selected}", external=open_external)
        self.terminals.append(window)
        window.finished.connect(lambda: self.terminals.remove(window))
        window.show()
        self.status.setText("Embedded terminal opened. Administrator authentication happens in the terminal.")
        QTimer.singleShot(1500, self.refresh)

    def check_health(self):
        if self.demo or not self.client.is_ready:
            return
        def done(ok, data):
            if ok:
                self.health_ready = data["ready"]
                self.health_output.setPlainText(data["output"])
                if not self.health_ready:
                    self.show_notice("A few host dependencies are missing. Open Host health to install them before creating a workspace.")
                elif self.client.writable:
                    self.notice.hide()
                self.update_controls()
            else:
                self.health_output.setPlainText(str(data))
        self.client.request("doctor", {}, done)

    def setup(self):
        self.perform("setup", {})

    def workspace_log(self):
        if self.selected:
            self.show_log(self.selected)

    def activity_log(self):
        row = self.activity_table.currentRow()
        if 0 <= row < len(self.data["activity"]):
            entry = self.data["activity"][row]
            self.show_log(entry["name"], entry["log"])

    def show_log(self, target, selected_log=None):
        if self.demo:
            return
        def done(ok, data):
            if not ok:
                self.show_error(str(data))
                return
            dialog = QDialog(self)
            dialog.setWindowTitle("Operation log · " + target)
            dialog.resize(850, 560)
            layout = QVBoxLayout(dialog)
            output = QPlainTextEdit()
            output.setReadOnly(True)
            output.setPlainText(data)
            layout.addWidget(output)
            layout.addWidget(button("Copy log", lambda: QApplication.clipboard().setText(data)))
            dialog.exec()
        self.client.request("logs", {"name": target, "log": selected_log}, done)

    def show_notice(self, message):
        self.notice_text.setText(message)
        self.notice.show()

    def show_error(self, message):
        QMessageBox.warning(self, "Chrootables", message)

    def load_demo(self):
        self.data = json.loads((ROOT / "assets" / "demo.json").read_text())
        self.access_label.setText("○  Sample data · read-only")
        self.unlock_button.setEnabled(False)
        self.health_output.setPlainText("This is the desktop tour. Launch without --demo to inspect your host and manage real workspaces.")
        self.show_notice("READ-ONLY TOUR · Sample workspaces. No images are created or modified.")
        self.notice_action.hide()
        self.status.setText("Sample data · launch without --demo to use your own workspaces")
        self.selected = "python-playground"
        self.render()

    def _poll_timer(self):
        if not self.isMinimized():
            self.refresh()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange and (event.oldState() & Qt.WindowState.WindowMinimized) and not self.isMinimized():
            self.refresh()

    def closeEvent(self, event):
        if self.busy:
            self.show_error("An operation is still running. Wait for it to finish before closing Chrootables.")
            event.ignore()
            return
        self.timer.stop()
        self.client.stop()
        event.accept()


def main():
    parser = argparse.ArgumentParser(description="Chrootables desktop workbench")
    parser.add_argument("--store", type=Path, default=DEFAULT_STORE)
    parser.add_argument("--demo", action="store_true", help="Explore a read-only tour with clearly labeled sample data")
    parser.add_argument("--screenshot", type=Path, help="Save a window capture and exit (for UI checks)")
    parser.add_argument("--version", action="version", version=__version__)
    args = parser.parse_args()
    if not args.store.is_absolute() or args.store == Path("/"):
        parser.error("--store must be an absolute directory other than /.")
    app = QApplication(sys.argv[:1])
    app.setApplicationName("Chrootables")
    app.setDesktopFileName("io.github.chrootables.Desktop")
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    window = Window(args.store, args.demo)
    window.show()
    if args.screenshot:
        def capture():
            args.screenshot.parent.mkdir(parents=True, exist_ok=True)
            window.grab().save(str(args.screenshot))
            window.close()
        QTimer.singleShot(1800, capture)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
