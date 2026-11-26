"""Main window, tray icon and app start-up."""

import os
import shutil
import sys
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import (
    QApplication, QLabel, QMainWindow, QMenu, QMessageBox, QPushButton, QSplitter, QSystemTrayIcon, QTabWidget,
)

from . import automation, cache, config, doctor, prefs, qobuz_guard, shortcuts, theme
from .activity import ActivityPanel
from .client import Session, run_async
from .discover_tab import DiscoverTab
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
        self.auto_label = QLabel()
        self.auto_label.setEnabled(False)
        self.statusBar().addPermanentWidget(self.auto_label)
        self.qobuz_label = QLabel()
        self.qobuz_label.setTextFormat(Qt.RichText)
        self.btn_resume = QPushButton("I've been unblocked")
        self.btn_resume.setToolTip("Only press this once Qobuz has lifted the block: requests while blocked can "
                                   "make it last longer.")
        self.btn_resume.setVisible(False)
        self.statusBar().addPermanentWidget(self.qobuz_label)
        self.statusBar().addPermanentWidget(self.btn_resume)
        self.btn_resume.clicked.connect(self._resume_qobuz)
        self._auto_running = False
        self._auto_parts = 0
        self._auto_result = {}
        self._downloaded_since_send = False

        self.download = DownloadTab(self.runner)
        self.download.index_provider = lambda: self.index
        self.download.session = self.session
        self.search = SearchTab(self.session, self.download, self.show_status, lambda: self.index)
        self.library = LibraryTab(self.show_status)
        self.discover = DiscoverTab(self.session, self.download, self.show_status)
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
        self.tabs.addTab(self.discover, "Discover")
        self.tabs.addTab(self.settings, "Settings")

        self.activity = ActivityPanel(self.runner)
        self.split = QSplitter(Qt.Vertical)
        self.split.addWidget(self.tabs)
        self.split.addWidget(self.activity)
        self.split.setStretchFactor(0, 3)
        self.split.setStretchFactor(1, 2)
        self.setCentralWidget(self.split)

        self._setup_tray()
        shortcuts.install(self)
        self.setAcceptDrops(True)

        # one index rebuild for a burst of changes (several albums sent, settings saved …)
        self._index_timer = QTimer(self)
        self._index_timer.setSingleShot(True)
        self._index_timer.setInterval(2000)
        self._index_timer.timeout.connect(self._rebuild_index_now)
        # the background check (Settings → Automation) looks every minute whether it's due
        self.auto_timer = QTimer(self)
        self.auto_timer.setInterval(60 * 1000)
        self.auto_timer.timeout.connect(self._auto_tick)
        self.auto_timer.start()
        self.guard_timer = QTimer(self)                  # keeps the "Qobuz paused" banner current
        self.guard_timer.setInterval(5000)
        self.guard_timer.timeout.connect(self._refresh_qobuz_banner)
        self.guard_timer.start()
        self._apply_safe_mode()

        self.settings.saved.connect(self._settings_saved)
        self.runner.authProblem.connect(self._auth_problem)
        self.runner.blocked.connect(self._qobuz_blocked)
        self.runner.jobChanged.connect(self._job_changed)
        self.runner.jobAdded.connect(lambda _j: self._save_queue())
        self.library.librarySent.connect(self.rebuild_index)
        self.library.autoSendDone.connect(self._auto_sent)
        self.health.scanFinished.connect(self._scan_finished)
        self.health.scanFailed.connect(
            lambda why, quiet: self._auto_part_done(error=f"library scan: {why}") if quiet and self._auto_running
            else None)
        self.discover.browseArtist.connect(self._browse_artist)
        self.settings.runAutomationNow.connect(lambda: self.run_automation(manual=True))
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
        self._rebuild_index_now()
        run_async(lambda: cache.prune_files("covers", 90 * cache.DAY), None, lambda _m: None)
        QTimer.singleShot(0, self._auto_tick)
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
        self.status_label.setText(f"<span style='color:{theme.ERROR}'>{text}</span>" if error else text)

    def rebuild_index(self):
        self._index_timer.start()

    def _refresh_history(self):
        """After a download: only qobuz-dl's history changed (the files are still in the download folder)."""
        idx = self.index
        if idx is None:
            self.rebuild_index()
            return
        run_async(lambda: idx.refresh_history(config.QOBUZ_DB), lambda _i: self.search.refresh_marks(),
                  lambda _m: None)

    def _rebuild_index_now(self):
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

    def _refresh_qobuz_banner(self):
        message = qobuz_guard.guard.blocked_message()
        if message:
            self.qobuz_label.setText(f"<span style='color:{theme.ERROR}'><b>Qobuz paused</b></span>")
            self.qobuz_label.setToolTip(message)
        elif prefs.safe_mode():
            self.qobuz_label.setText(f"<span style='color:{theme.MUTED}'>Safe mode</span>")
            self.qobuz_label.setToolTip("Safe mode is on: no Discover, no background Qobuz requests, requests spaced "
                                        "3 seconds apart, album details on request, slower downloads. "
                                        "Switch it off in Settings → Qobuz contact.")
        else:
            self.qobuz_label.setText("")
            self.qobuz_label.setToolTip("")
        self.btn_resume.setVisible(bool(message) and qobuz_guard.guard.permanent())

    def _qobuz_blocked(self, text):
        self._refresh_qobuz_banner()
        self.show_status(qobuz_guard.guard.blocked_message() or text, error=True)
        self.notify("Qobuz account blocked", "All Qobuz requests from the app are stopped. Contact Qobuz support.", True)

    def _resume_qobuz(self):
        if QMessageBox.question(self, "Resume Qobuz requests",
                                "Only continue if Qobuz has told you the account is unblocked. Asking while it is "
                                "still blocked can make the block longer.\n\nResume?",
                                defaultButton=QMessageBox.No) == QMessageBox.Yes:
            qobuz_guard.guard.resume()
            self._refresh_qobuz_banner()
            self.show_status("Qobuz requests are allowed again.")

    def _apply_safe_mode(self):
        """Safe mode: slower Qobuz requests, no background Qobuz requests, and no Discover tab."""
        safe = prefs.safe_mode()
        qobuz_guard.guard.configure(budget=prefs.qobuz_budget(), gap=prefs.SAFE_GAP if safe else qobuz_guard.GAP)
        self.tabs.setTabVisible(self.tabs.indexOf(self.discover), not safe)
        if self.tabs.currentWidget() is self.discover and safe:
            self.tabs.setCurrentWidget(self.search)
        self._refresh_qobuz_banner()

    def _settings_saved(self):
        self._apply_safe_mode()
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
            self._refresh_history()
            if job.args[:1] == ["dl"]:
                self._downloaded_since_send = True
            if self.tabs.currentWidget() is self.library:
                self.library.refresh()
        if job.status in ("done", "failed") and not self.runner.pending() and not self.runner.busy():
            if job.status == "done":
                self.show_status("All jobs finished.")
                self.notify("Downloads finished", f"Last: {job.title}")
            if self._downloaded_since_send and prefs.get_bool(prefs.AUTO_SEND, False):
                self._downloaded_since_send = False
                self.library.auto_send()

    # ---- shortcuts, paste and drop -------------------------------------------
    def refresh_current(self):
        w = self.tabs.currentWidget()
        if w is self.search:
            self.search.rerun()
        elif w is self.library:
            self.library.refresh(force=True)
        elif w is self.health:
            self.health.scan()

    def _browse_artist(self, name):
        self.tabs.setCurrentWidget(self.search)
        self.search.search_artist(name)

    def queue_links(self, sources, how):
        if not sources:
            self.show_status(f"No Qobuz links {how}.")
            return
        added = self.download.enqueue(sources)
        if added:
            self.show_status(f"Queued {len(added)} link{'s' if len(added) != 1 else ''} {how}.")

    def paste_links(self):
        self.queue_links(shortcuts.links_in(QApplication.clipboard().text()), "from the clipboard")

    def dragEnterEvent(self, event):
        if shortcuts.sources_from_mime(event.mimeData()):
            event.acceptProposedAction()

    def dropEvent(self, event):
        sources = shortcuts.sources_from_mime(event.mimeData())
        event.acceptProposedAction()
        self.queue_links(sources, "dropped on the window")

    # ---- automation ------------------------------------------------------------
    def _auto_tick(self):
        hours = prefs.get_int(prefs.AUTO_HOURS, 0)
        last = prefs.get_str(prefs.AUTO_LAST_RUN, "")
        result = prefs.get_str(prefs.AUTO_LAST_RESULT, "")
        if not hours:
            self.auto_label.setText("")
            return
        if self._auto_running:
            self.auto_label.setText("Background check running…")
            return
        left = automation.seconds_left(last or None, hours)
        when = "due now" if not left else (f"in {left // 3600}h {left % 3600 // 60:02d}m" if left >= 3600
                                           else f"in {max(1, left // 60)} min")
        self.auto_label.setText(f"Next background check {when}" + (f" · last: {result}" if result else ""))
        if automation.due(last or None, hours):
            self.run_automation()

    def run_automation(self, manual=False):
        """The background check: scan the library (your server and your folders, plus Wikipedia's lists) and
        apply the one safe fix you can switch on. It never contacts Qobuz and never downloads."""
        if self._auto_running or self.health.busy() or self.library.busy():
            if manual:
                self.show_status("A scan or a send is already running — try again when it's done.")
            return
        prefs.set_value(prefs.AUTO_LAST_RUN, str(time.time()))
        self._auto_running = True
        self._auto_result = {}
        self._auto_parts = 0
        if self.health.scan(quiet=True):
            self._auto_parts += 1
        if not self._auto_parts:
            self._auto_running = False
            self.show_status("Background check: nothing to do — set up Navidrome first (Settings).")
        else:
            self.show_status("Background check started.")
        self._auto_tick()

    def _scan_finished(self, _previous, _issues, _completion, quiet):
        if not (quiet and self._auto_running):
            return
        kinds = [doctor.LOCATION] if prefs.get_bool(prefs.AUTO_FIX_LOCATION, False) else []
        if kinds:
            def fixed(n, failed):
                self._auto_result["fixed"], self._auto_result["failed"] = n, failed
                self._auto_part_done()
            self.health.apply_safe_fixes(kinds, fixed)
        else:
            self._auto_part_done()

    def _auto_part_done(self, error=""):
        if error:
            self._auto_result["error"] = error
        self._auto_parts -= 1
        if self._auto_parts > 0:
            return
        self._auto_running = False
        r = self._auto_result
        text = automation.summary(r.get("fixed", 0), r.get("failed", 0))
        if r.get("error"):
            text += f" ({r['error']})"
        prefs.set_value(prefs.AUTO_LAST_RESULT, text)
        self.show_status(f"Background check finished: {text}.", error=bool(r.get("error") or r.get("failed")))
        if text != "nothing to fix":
            self.notify("Background check", text[:1].upper() + text[1:] + ".")
        self._auto_tick()

    def _auto_sent(self, sent, failed, left):
        text = f"Sent {sent} album{'s' if sent != 1 else ''} to the library automatically"
        if failed:
            text += f", {failed} failed"
        if left:
            text += f"; {left} need{'s' if left == 1 else ''} a decision in the Library tab"
        self.show_status(text + ".", error=bool(failed))
        self.notify("Sent to library", text + ".", error=bool(failed))

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
        self.auto_timer.stop()
        if self.health.busy():
            self.health.worker.stop()
            self.health.worker.wait(15000)
        store = prefs.store()
        store.setValue("geometry", self.saveGeometry())
        store.setValue("split", self.split.saveState())
        if self.tray:
            self.tray.hide()


def main():
    if "--report" in sys.argv:
        # headless: measure the library and write a report folder, no window
        from . import report
        i = sys.argv.index("--report")
        return report.main(sys.argv[i + 1:i + 2])
    app = QApplication(sys.argv)
    app.setApplicationName("qobuz-dl-gui")
    app.setApplicationDisplayName(APP_NAME)
    app.setDesktopFileName("qobuz-dl-gui")
    app.setWindowIcon(QIcon(ICON))
    theme.init(app)
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
