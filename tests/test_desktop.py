import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import tempfile
import unittest
import sys
from pathlib import Path
from unittest.mock import patch
from PySide6.QtWidgets import QApplication, QDialog
from PySide6.QtTest import QTest
from PySide6.QtCore import Qt, QSettings
from chrootables.app import Window, CreateDialog
from chrootables.style import STYLE


class DesktopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setStyle("Fusion")
        cls.app.setStyleSheet(STYLE)

    def setUp(self):
        self.signal_errors = []
        self.original_hook = sys.excepthook
        sys.excepthook = lambda *args: self.signal_errors.append(args)
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.settings = QSettings(str(Path(self.directory.name) / "desktop.ini"), QSettings.Format.IniFormat)
        with patch("chrootables.app.QSettings", return_value=self.settings):
            self.window = Window(demo=True)
        self.window.timer.stop()
        self.window.show()
        self.app.processEvents()

    def tearDown(self):
        self.window.close()
        self.app.processEvents()
        sys.excepthook = self.original_hook
        self.assertEqual(self.signal_errors, [], "Unhandled exception from a Qt callback")

    def test_search_and_filters(self):
        self.assertEqual(self.window.workspaces.count(), 4)
        QTest.keyClicks(self.window.search, "python")
        self.assertEqual(self.window.workspaces.count(), 1)
        self.assertEqual(self.window.selected, "python-playground")
        self.window.search.clear()
        self.window.filter.setCurrentIndex(2)
        self.assertEqual(self.window.workspaces.count(), 2)
        self.window.search.setText("missing")
        self.assertEqual(self.window.library_stack.currentIndex(), 1)
        self.assertEqual(self.window.empty_title.text(), "No matching workspaces.")

    def test_recovery_and_activity_navigation(self):
        QTest.mouseClick(self.window.nav[1], Qt.MouseButton.LeftButton)
        self.assertEqual(self.window.pages.currentIndex(), 1)
        self.assertEqual(self.window.recovery_table.rowCount(), 3)
        self.window.recovery_table.selectRow(0)
        self.assertFalse(self.window.restore_button.isEnabled())  # Tour must never mutate.
        QTest.mouseClick(self.window.nav[2], Qt.MouseButton.LeftButton)
        self.assertEqual(self.window.activity_table.rowCount(), 3)

    def test_network_choice_survives_background_refresh(self):
        self.window.offline.setChecked(True)
        self.window.render_library()
        self.assertEqual(self.window.selected, "python-playground")
        self.assertTrue(self.window.offline.isChecked())

    def test_workspace_details_keep_scroll_on_refresh_and_reset_on_selection(self):
        self.window.resize(1180, 780)
        self.app.processEvents()
        scroll = self.window.details.verticalScrollBar()
        self.assertGreater(scroll.maximum(), 0)
        scroll.setValue(scroll.maximum())
        position = scroll.value()
        self.window.render_library()
        self.app.processEvents()
        self.assertEqual(scroll.value(), position)
        for row in range(self.window.workspaces.count()):
            item = self.window.workspaces.item(row)
            if item.data(Qt.ItemDataRole.UserRole)["name"] == "clean-slate":
                self.window.workspaces.setCurrentItem(item)
                break
        self.app.processEvents()
        self.assertEqual(self.window.selected, "clean-slate")
        self.assertEqual(scroll.value(), 0)

    def enable_shared_folder_controls(self, window=None):
        window = window or self.window
        window.demo = False
        window.shared_folder_choices.clear()
        window.render_library()

    def shared_directory(self, name="host project"):
        folder = Path(self.directory.name) / name
        folder.mkdir()
        return str(folder)

    def another_demo_window(self, store=None):
        self.settings.sync()
        settings = QSettings(self.settings.fileName(), QSettings.Format.IniFormat)
        with patch("chrootables.app.QSettings", return_value=settings):
            window = Window(self.window.store if store is None else store, demo=True)
        window.timer.stop()
        self.addCleanup(window.close)
        return window

    def test_shared_folder_selection_survives_refresh_and_can_be_cleared(self):
        self.enable_shared_folder_controls()
        folder = self.shared_directory()
        with patch("chrootables.app.QFileDialog.getExistingDirectory", return_value=folder):
            self.window.share_choose.click()
        self.assertEqual(self.window.share_path.text(), folder)
        self.assertTrue(self.window.share_clear.isEnabled())
        self.assertTrue(self.window.share_read_only.isEnabled())
        self.window.share_read_only.setChecked(True)
        self.window.render_library()
        self.assertEqual(self.window.share_path.text(), folder)
        self.assertTrue(self.window.share_read_only.isChecked())
        self.window.share_clear.click()
        self.assertEqual(self.window.share_path.text(), "")
        self.assertFalse(self.window.share_clear.isEnabled())
        self.assertFalse(self.window.share_read_only.isEnabled())
        self.window.shared_folder_choices.clear()
        self.assertEqual(self.window.shared_folder_choice("python-playground"), ("", False))

    def test_shared_folder_preferences_are_saved_per_workspace_and_store(self):
        self.enable_shared_folder_controls()
        folder = self.shared_directory()
        self.window.save_shared_folder("python-playground", folder, True)
        self.window.selected = "clean-slate"
        self.window.render_library()
        self.assertEqual(self.window.share_path.text(), "")
        other_folder = self.shared_directory("other project")
        with patch("chrootables.app.QFileDialog.getExistingDirectory", return_value=other_folder):
            self.window.share_choose.click()
        self.window.selected = "python-playground"
        self.window.render_library()
        self.assertEqual(self.window.share_path.text(), folder)
        self.assertTrue(self.window.share_read_only.isChecked())

        reopened = self.another_demo_window()
        self.enable_shared_folder_controls(reopened)
        self.assertEqual(reopened.shared_folder_choice("python-playground"), (folder, True))
        self.assertEqual(reopened.shared_folder_choice("clean-slate"), (other_folder, False))
        different_store = self.another_demo_window(Path(self.directory.name) / "other-store")
        self.enable_shared_folder_controls(different_store)
        self.assertEqual(different_store.shared_folder_choice("python-playground"), ("", False))
        different_store.save_shared_folder("python-playground", other_folder)
        reopened.shared_folder_choices.clear()
        self.assertEqual(reopened.shared_folder_choice("python-playground"), (folder, True))

    def test_cancelled_or_invalid_folder_chooser_keeps_previous_selection(self):
        self.enable_shared_folder_controls()
        folder = self.shared_directory()
        self.window.save_shared_folder("python-playground", folder, True)
        self.window.render_library()
        with patch("chrootables.app.QFileDialog.getExistingDirectory", return_value=""):
            self.window.share_choose.click()
        self.assertEqual(self.window.share_path.text(), folder)
        self.assertTrue(self.window.share_read_only.isChecked())
        with patch("chrootables.app.QFileDialog.getExistingDirectory", return_value=folder + "/missing"), \
                patch.object(self.window, "show_error") as show_error:
            self.window.share_choose.click()
        show_error.assert_called_once()
        self.assertEqual(self.window.shared_folder_choice("python-playground"), (folder, True))

    def test_terminal_and_disposable_session_receive_shared_folder_choices(self):
        self.enable_shared_folder_controls()
        folder = self.shared_directory()
        self.window.save_shared_folder("python-playground", folder, True)
        self.window.render_library()
        self.window.offline.setChecked(True)
        with patch("chrootables.app.terminal_command", return_value=["terminal", "argument"]) as command, \
                patch("chrootables.app.QProcess.startDetached", return_value=(True, 123)) as start, \
                patch("chrootables.app.QTimer.singleShot"):
            for disposable in (False, True):
                with self.subTest(disposable=disposable):
                    self.window.open_terminal(disposable=disposable)
                    command.assert_called_with("python-playground", self.window.store, disposable=disposable,
                                               isolated=True, share=folder, read_only=True)
            self.window.clear_shared_folder()
            self.window.open_terminal()
            command.assert_called_with("python-playground", self.window.store, disposable=False,
                                       isolated=True, share=None, read_only=False)
        self.assertEqual(start.call_count, 3)

    def test_unavailable_saved_folder_prevents_terminal_launch(self):
        self.enable_shared_folder_controls()
        folder = self.shared_directory()
        self.window.save_shared_folder("python-playground", folder)
        Path(folder).rmdir()
        with patch("chrootables.app.QProcess.startDetached") as start, \
                patch.object(self.window, "show_error") as show_error:
            self.window.open_terminal()
        start.assert_not_called()
        show_error.assert_called_once()
        self.assertEqual(self.window.shared_folder_choice("python-playground"), (folder, False))

    def test_demo_ignores_saved_shared_folders_and_does_not_write_preferences(self):
        self.enable_shared_folder_controls()
        folder = self.shared_directory()
        self.window.save_shared_folder("python-playground", folder, True)
        demo = self.another_demo_window()
        self.assertEqual(demo.shared_folder_choice("python-playground"), ("", False))
        self.assertFalse(demo.share_choose.isEnabled())
        self.assertFalse(demo.share_clear.isEnabled())
        self.assertFalse(demo.share_read_only.isEnabled())
        with patch("chrootables.app.QFileDialog.getExistingDirectory") as choose:
            demo.choose_shared_folder()
            demo.clear_shared_folder()
        choose.assert_not_called()
        self.window.shared_folder_choices.clear()
        self.assertEqual(self.window.shared_folder_choice("python-playground"), (folder, True))

    def test_create_validation_and_profile_defaults(self):
        dialog = CreateDialog({"exists"}, self.window)
        dialog.name_input.setText("../invalid")
        dialog.validate()
        self.assertNotEqual(dialog.result(), QDialog.DialogCode.Accepted)
        self.assertTrue(dialog.error.text())
        dialog.name_input.setText("exists")
        dialog.validate()
        self.assertIn("already", dialog.error.text())
        dialog.name_input.setText("new-python")
        dialog.profile.setCurrentIndex(2)
        self.assertEqual(dialog.size_input.value(), 4)
        dialog.validate()
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
        self.assertEqual(dialog.parameters()["profile"], "python")

    def test_actual_async_helper_and_empty_store(self):
        with tempfile.TemporaryDirectory() as directory:
            other = Window(Path(directory) / "store")
            other.show()
            try:
                for _ in range(100):
                    QTest.qWait(30)
                    if other.client.is_ready and other.metrics[0].text() == "0":
                        break
                self.assertTrue(other.client.is_ready)
                self.assertEqual(other.metrics[0].text(), "0")
                self.assertFalse(other.client.writable)
                self.assertEqual(other.library_stack.currentIndex(), 1)
            finally:
                other.close()

    def test_old_helper_output_is_ignored_after_disconnect(self):
        from PySide6.QtCore import QProcess
        previous = QProcess(self.window)
        self.window.client.read(previous)
        self.assertEqual(self.window.client.buffer, b"")

    def test_locked_store_is_normal_onboarding_not_an_error(self):
        from chrootables.client import BackendError
        window = self.window
        window.demo = False
        window.client.is_ready = True
        window.health_ready = True
        def request(action, params, callback):
            callback(False, BackendError("[Errno 13] Permission denied", "permission_required"))
        window.client.request = request
        window.refresh()
        self.assertTrue(window.store_locked)
        self.assertEqual(window.library_stack.currentIndex(), 1)
        self.assertTrue(window.notice.isHidden())
        self.assertEqual(window.empty_create.text(), "Unlock workspace store")
        window.search.setText("something")
        self.assertEqual(window.empty_title.text(), "Unlock your workspace library.")

    def test_widget_reuse_and_accessibility_metadata(self):
        detail_widget = self.window.details.widget()
        first_item = self.window.workspaces.item(0)
        first_row = self.window.workspaces.itemWidget(first_item)
        self.window.render()
        self.assertIs(self.window.details.widget(), detail_widget)
        self.assertIs(self.window.workspaces.itemWidget(self.window.workspaces.item(0)), first_row)
        self.assertIn("build-studio", first_item.data(Qt.ItemDataRole.AccessibleTextRole))
        self.assertIn("python-playground", self.window.workspaces.currentItem().data(Qt.ItemDataRole.AccessibleTextRole))
        self.assertEqual(self.window.search.accessibleName(), "Search workspaces")
        self.assertEqual(self.window.filter.accessibleName(), "Filter workspaces")
        self.assertEqual(self.window.refresh_button.accessibleName(), "Refresh workspaces")
        self.assertTrue(self.window.favorite_button.accessibleName())

    def test_client_coalesces_batched_output_before_result(self):
        from unittest.mock import MagicMock
        events = []
        self.window.client.output.connect(lambda ident, text: events.append(("output", ident, text)))
        self.window.client.callbacks[7] = lambda ok, data: events.append(("result", ok, data))
        proc = MagicMock()
        proc.readAllStandardOutput.return_value = (
            b'{"id":7,"event":"output","text":"line 1\\n"}\n'
            b'{"id":7,"event":"output","text":"line 2\\n"}\n'
            b'{"id":7,"event":"result","ok":true,"data":{"exit_code":0}}\n'
        )
        self.window.client.process = proc
        self.window.client.read(proc)
        self.assertEqual(events, [("output", 7, "line 1\nline 2\n"), ("result", True, {"exit_code": 0})])

    def test_minimized_window_skips_timer_poll_and_refreshes_on_restore(self):
        from PySide6.QtGui import QWindowStateChangeEvent
        with patch.object(self.window, "refresh") as refresh:
            with patch.object(self.window, "isMinimized", return_value=True):
                self.window._poll_timer()
                self.assertEqual(refresh.call_count, 0)
            with patch.object(self.window, "isMinimized", return_value=False):
                self.window._poll_timer()
                self.assertEqual(refresh.call_count, 1)
                event = QWindowStateChangeEvent(Qt.WindowState.WindowMinimized)
                self.window.changeEvent(event)
                self.assertEqual(refresh.call_count, 2)

    def test_client_starts_broker_in_isolated_no_site_mode(self):
        from PySide6.QtCore import QProcess
        with patch.object(QProcess, "start") as mock_start:
            self.window.client.start(writable=False)
            prog, args = mock_start.call_args[0]
            self.assertEqual(prog, sys.executable)
            self.assertEqual(args[:2], ["-I", "-S"])
            self.window.client.start(writable=True)
            prog, args = mock_start.call_args[0]
            self.assertEqual(prog, "pkexec")
            self.assertEqual(args[:3], [sys.executable, "-I", "-S"])
            self.assertEqual(args[-1], "--writable")

    def test_workspace_actions_keep_target_while_dialog_processes_refresh(self):
        images = list(self.window.data["images"])
        def refresh_selection(*args, **kwargs):
            self.window.data["images"] = [item for item in images if item["name"] != "python-playground"]
            self.window.render_library()
            return True

        for action, value, params in [
            ("snapshot", "checkpoint", {"target": "checkpoint"}),
            ("clone", "branch", {"target": "branch"}),
            ("resize_image", 8192, {"size_mib": 8192}),
        ]:
            with self.subTest(action=action):
                self.window.data["images"] = images
                self.window.selected = "python-playground"
                self.window.render_library()
                with patch("chrootables.app.ValueDialog") as dialog_class, \
                        patch.object(self.window, "perform") as perform:
                    dialog_class.return_value.exec.side_effect = refresh_selection
                    dialog_class.return_value.value.return_value = value
                    getattr(self.window, action)()
                perform.assert_called_once_with("resize" if action == "resize_image" else action,
                                                {"name": "python-playground", **params})

        self.window.data["images"] = images
        self.window.selected = "python-playground"
        with patch.object(self.window, "confirm", side_effect=refresh_selection), \
                patch.object(self.window, "perform") as perform:
            self.window.repair()
        perform.assert_called_once_with("repair", {"name": "python-playground", "confirmed": True})

        self.window.data["images"] = images
        self.window.selected = "python-playground"
        destination = str(Path(self.directory.name) / "export.tar.gz")
        def choose_destination(*args):
            refresh_selection()
            return destination, ""
        with patch("chrootables.app.QFileDialog.getSaveFileName", side_effect=choose_destination), \
                patch.object(self.window, "perform") as perform:
            self.window.export()
        perform.assert_called_once_with("export", {"name": "python-playground", "destination": destination})

    def test_failed_operation_restores_controls_without_inventory_changes(self):
        self.enable_shared_folder_controls()
        self.window.client.is_ready = True
        self.window.health_ready = True
        callbacks = {}
        def request(action, params, callback):
            if action == "inventory":
                callback(True, self.window.data)
            else:
                callbacks[action] = callback
        with patch.object(self.window.client, "request", side_effect=request):
            self.window.refresh()
            self.window.run_operation("snapshot", {"name": "python-playground", "target": "checkpoint"})
            self.assertFalse(self.window.terminal_button.isEnabled())
            callbacks["snapshot"](False, "Workspace is busy.")
        self.assertFalse(self.window.busy)
        self.assertTrue(self.window.terminal_button.isEnabled())
        self.assertTrue(self.window.save_snapshot_button.isEnabled())
        self.assertTrue(self.window.share_choose.isEnabled())

    def test_client_stop_rejects_requests_from_cancelled_callbacks(self):
        from unittest.mock import MagicMock
        client = self.window.client
        client.process = MagicMock()
        client.is_ready = client.writable = True
        results = []
        def cancelled(ok, data):
            results.append((ok, client.is_ready, client.writable))
            client.request("inventory", {}, lambda ok, data: results.append((ok, str(data))))
        client.callbacks[1] = cancelled
        client.stop()
        self.assertEqual(results[0], (False, False, False))
        self.assertEqual(results[1], (False, "The workspace helper is not connected."))
        self.assertEqual(client.callbacks, {})

    def test_recovery_selection_follows_snapshot_across_refresh(self):
        self.window.recovery_table.selectRow(1)
        selected = self.window.chosen_snapshot().copy()
        self.window.data["snapshots"].reverse()
        self.window.data["snapshots"].insert(0, {**selected, "label": "new-checkpoint"})
        self.window.render()
        self.assertEqual(self.window.chosen_snapshot(), selected)
        self.window.data["snapshots"].remove(selected)
        self.window.render()
        self.assertIsNone(self.window.chosen_snapshot())

    def test_activity_selection_follows_log_across_refresh(self):
        self.window.demo = False
        self.window.activity_table.selectRow(0)
        selected = self.window.data["activity"][0].copy()
        self.window.data["activity"].insert(0, {**selected, "log": "new-operation.log"})
        self.window.render()
        with patch.object(self.window, "show_log") as show_log:
            self.window.activity_log()
        show_log.assert_called_once_with(selected["name"], selected["log"])

        self.window.data["activity"][1] = {**selected, "log": "replacement-operation.log"}
        self.window.render()
        with patch.object(self.window, "show_log") as show_log:
            self.window.activity_log()
        show_log.assert_not_called()


if __name__ == "__main__":
    unittest.main()
