"""Main window, tray icon and app start-up."""

import os
import shutil
import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import (
    QApplication, QLabel, QMainWindow, QMenu, QMessageBox, QSplitter, QSystemTrayIcon, QTabWidget,
)

from . import config, prefs
from .activity import ActivityPanel
from .client import Session, run_async
from .download_tab import DownloadTab
from .jobs import Job, JobRunner
from .library import LibraryIndex
from .health_tab import HealthTab
from .library_tab import LibraryTab, library_folder
from .search_tab import SearchTab
from .settings_tab import SettingsTab
from .setup_dialog import SetupDialog
from .tools_tab import SyncTab, ToolsTab

APP_NAME = "Qobuz-DL"
ICON = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon.svg")


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(QIcon(ICON))
        self.session = Session()
        self.runner = JobRunner(self)
        self.index = None
        self._quitting = False

        self.status_label = QLabel()
        self.statusBar().addWidget(self.status_label, 1)

        self.download = DownloadTab(self.runner)
        self.download.index_provider = lambda: self.index
        self.download.session = self.session
        self.search = SearchTab(self.session, self.download, self.show_status, lambda: self.index)
        self.library = LibraryTab(self.show_status)
        self.health = HealthTab(self.session, self.download, self.show_status)
        self.sync = SyncTab(self.runner)
        self.tools = ToolsTab(self.runner)
        self.settings = SettingsTab(self.session, self.show_status)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.addTab(self.search, "Search")
        self.tabs.addTab(self.download, "Download")
        self.tabs.addTab(self.library, "Library")
        self.tabs.addTab(self.health, "Library health")
        self.tabs.addTab(self.sync, "Playlist sync")
        self.tabs.addTab(self.tools, "Library tools")
        self.tabs.addTab(self.settings, "Settings")

        self.activity = ActivityPanel(self.runner)
        self.split = QSplitter(Qt.Vertical)
        self.split.addWidget(self.tabs)
        self.split.addWidget(self.activity)
        self.split.setStretchFactor(0, 3)
        self.split.setStretchFactor(1, 2)
        self.setCentralWidget(self.split)

        self._setup_tray()

        self.settings.saved.connect(self._settings_saved)
        self.runner.authProblem.connect(self._auth_problem)
        self.runner.jobChanged.connect(self._job_changed)
        self.runner.jobAdded.connect(lambda _j: self._save_queue())
        self.library.librarySent.connect(self.rebuild_index)
        self.tabs.currentChanged.connect(self._tab_changed)

        store = prefs.store()
        geometry = store.value("geometry")
        if geometry is not None:
            self.restoreGeometry(geometry)
        else:
            self.resize(1280, 900)
        split_state = store.value("split")
        if split_state is not None:
            self.split.restoreState(split_state)

        if not shutil.which("ffmpeg"):
            self.show_status("ffmpeg was not found — install it (sudo pacman -S ffmpeg) for segmented "
                             "downloads to work.", error=True)
        else:
            self.show_status(f"Ready. Config: {config.CONFIG_FILE}")
        self.rebuild_index()
        self.tools.check_updates(on_newer=self._update_available)
        self.tools.check_gui_updates(on_newer=self._gui_update_available)

    def _update_available(self, info):
        self.show_status(f"qobuz-dl {info['latest']} is available on GitHub — update it from Library tools.")
        self.notify("qobuz-dl update available",
                    f"Version {info['latest']} is on GitHub (you have {info.get('installed', '?')}).")

    def _gui_update_available(self, info):
        self.show_status(f"Qobuz-DL GUI {info['latest']} is available — update it from Library tools.")
        self.notify("Qobuz-DL GUI update available", f"Version {info['latest']} is on GitHub.")

    # ---- tray and notifications ---------------------------------------------
    def _setup_tray(self):
        self.tray = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.tray = QSystemTrayIcon(QIcon(ICON), self)
        self.tray.setToolTip(APP_NAME)
        menu = QMenu()
        show = QAction("Show Qobuz-DL", menu)
        show.triggered.connect(self.bring_to_front)
        stop = QAction("Stop all downloads", menu)
        stop.triggered.connect(self.runner.stop_all)
        quit_ = QAction("Quit", menu)
        quit_.triggered.connect(self.quit_app)
        menu.addAction(show)
        menu.addAction(stop)
        menu.addSeparator()
        menu.addAction(quit_)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._tray_clicked)
        self._tray_menu = menu
        self.tray.show()

    def _tray_clicked(self, reason):
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            if self.isVisible() and self.isActiveWindow():
                self.hide()
            else:
                self.bring_to_front()

    def bring_to_front(self):
        self.show()
        self.setWindowState(self.windowState() & ~Qt.WindowMinimized)
        self.raise_()
        self.activateWindow()

    def notify(self, title, text, error=False):
        if self.tray is not None and (not self.isVisible() or not self.isActiveWindow()):
            icon = QSystemTrayIcon.Warning if error else QSystemTrayIcon.Information
            self.tray.showMessage(title, text, icon, 6000)
        elif not self.isActiveWindow():
            QApplication.alert(self)

    # ---- state ---------------------------------------------------------------
    def show_status(self, text, error=False):
        self.status_label.setText(f"<span style='color:#e5534b'>{text}</span>" if error else text)

    def rebuild_index(self):
        lib = library_folder()

        def work():
            from . import navidrome
            if navidrome.configured():
                try:
                    albums = navidrome.Navidrome.from_settings().all_albums()
                    return LibraryIndex.from_navidrome(albums, config.QOBUZ_DB)
                except Exception:
                    pass   # fall back to the folder names
            return LibraryIndex.build(lib, config.QOBUZ_DB)

        def done(idx):
            self.index = idx
            self.search.refresh_marks()
        run_async(work, done, lambda _m: None)

    def _settings_saved(self):
        for tab in (self.download, self.sync, self.tools, self.library):
            tab.reload_settings()
        self.search.prefer_explicit.setChecked(prefs.get_bool(prefs.PREFER_EXPLICIT, True))
        self.rebuild_index()

    def _tab_changed(self, index):
        widget = self.tabs.widget(index)
        if widget is self.library and not self.library.busy():
            self.library.refresh()
        elif widget is self.settings:
            # the Search tab can change this one too
            self.settings.app_explicit.setChecked(prefs.get_bool(prefs.PREFER_EXPLICIT, True))

    def _auth_problem(self, _text):
        self.show_status("Qobuz rejected the auth token — paste a fresh one in Settings.", error=True)
        self.notify("Qobuz login problem", "The auth token was rejected. Paste a fresh one in Settings.", True)
        self.session.reset()

    def _save_queue(self):
        if not self._quitting:
            prefs.set_json(prefs.SAVED_QUEUE, self.runner.unfinished())

    def _job_changed(self, job):
        self._save_queue()
        if job.program and job.status in ("done", "failed"):
            if job.status == "done":
                self.tools.show_update_info(prefs.get_json(prefs.UPDATE_INFO, None))
                QMessageBox.information(self, "qobuz-dl updated",
                                        f"qobuz-dl {self._installed()} is installed. Downloads already use it; "
                                        "restart Qobuz-DL so search and settings use it too.")
            else:
                self.show_status("The qobuz-dl update failed — see the log.", error=True)
            return
        if job.status == "failed":
            self.show_status(f"Failed: {job.title} — see the log.", error=True)
            self.notify("Download failed", job.title, error=True)
        elif job.status == "done":
            self.rebuild_index()
            if self.tabs.currentWidget() is self.library:
                self.library.refresh()
            if not self.runner.pending() and not self.runner.busy():
                self.show_status("All jobs finished.")
                self.notify("Downloads finished", f"Last: {job.title}")

    @staticmethod
    def _installed():
        from . import updates
        return updates.installed_version()

    def offer_saved_queue(self):
        saved = prefs.get_json(prefs.SAVED_QUEUE, []) or []
        if not saved:
            return
        names = "\n".join(f"• {j.get('title', '?')}" for j in saved[:8])
        more = f"\n… and {len(saved) - 8} more" if len(saved) > 8 else ""
        answer = QMessageBox.question(
            self, "Unfinished downloads",
            f"{len(saved)} job{'s' if len(saved) != 1 else ''} didn't finish last time:\n\n{names}{more}\n\n"
            "Queue them again? Tracks already on disk are skipped.")
        if answer == QMessageBox.Yes:
            for j in saved:
                self.runner.enqueue(Job(j.get("title", "Resumed job"), list(j.get("args", [])),
                                        env=dict(j.get("env") or {}), success_text=j.get("success_text", "")))
        else:
            prefs.set_json(prefs.SAVED_QUEUE, [])

    # ---- closing -------------------------------------------------------------
    def closeEvent(self, event):
        busy = self.runner.busy() or self.runner.pending() or self.library.busy()
        to_tray = self.tray is not None and prefs.get_bool(prefs.MINIMIZE_TO_TRAY, True)
        if not self._quitting and to_tray and busy:
            event.ignore()
            self.hide()
            self.tray.showMessage(APP_NAME, "Still working in the background. Use the tray icon to "
                                  "show the window or quit.", QSystemTrayIcon.Information, 4000)
            return
        if not self._quitting and busy:
            if QMessageBox.question(self, "Quit", "Downloads are still running. Stop them and quit? "
                                    "They'll be offered again next time.") != QMessageBox.Yes:
                event.ignore()
                return
        self._shutdown()
        super().closeEvent(event)
        QApplication.quit()

    def quit_app(self):
        if (self.runner.busy() or self.runner.pending() or self.library.busy()) and \
                QMessageBox.question(self, "Quit", "Downloads are still running. Stop them and quit? "
                                     "They'll be offered again next time.") != QMessageBox.Yes:
            return
        self._quitting = True
        self.close()

    def _shutdown(self):
        self._quitting = True
        prefs.set_json(prefs.SAVED_QUEUE, self.runner.unfinished())
        self.runner.stop_all()
        if self.runner.proc:
            self.runner.proc.waitForFinished(6000)
        if self.library.busy():
            self.library.worker.stop()
            self.library.worker.wait(10000)
        if self.health.busy():
            self.health.worker.stop()
            self.health.worker.wait(15000)
        store = prefs.store()
        store.setValue("geometry", self.saveGeometry())
        store.setValue("split", self.split.saveState())
        if self.tray:
            self.tray.hide()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("qobuz-dl-gui")
    app.setApplicationDisplayName(APP_NAME)
    app.setDesktopFileName("qobuz-dl-gui")
    app.setWindowIcon(QIcon(ICON))
    app.setQuitOnLastWindowClosed(False)  # the tray keeps the app alive; quitting is explicit

    try:
        import qobuz_dl  # noqa: F401
    except ImportError:
        QMessageBox.critical(None, APP_NAME, "qobuz-dl is not installed in this Python environment.\n"
                                             "Run install.sh again.")
        return 1

    if not config.exists():
        if not SetupDialog().exec():  # 0 = cancelled
            return 0

    win = MainWindow()
    win.show()
    win.offer_saved_queue()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
