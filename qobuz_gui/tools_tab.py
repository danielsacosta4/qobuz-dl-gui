"""Playlist sync and library tools (lyrics, history database, stats)."""

import html
import os
import time

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QMessageBox, QPushButton, QVBoxLayout, QWidget,
)

from . import config, prefs, updates
from .download_tab import FolderPicker, describe, quality_combo, set_quality
from .client import run_async
from .jobs import Job


def _default_folder():
    cfg = config.load()
    return config.get(cfg, "directory") or config.get(cfg, "default_folder") or ""


def _note(text):
    label = QLabel(text)
    label.setWordWrap(True)
    return label


class SyncTab(QWidget):
    def __init__(self, runner, parent=None):
        super().__init__(parent)
        self.runner = runner

        self.url = QLineEdit()
        self.url.setPlaceholderText("https://play.qobuz.com/playlist/…")
        self.folder = FolderPicker("Folder that holds (or will hold) the playlist")
        self.quality = quality_combo()
        self.no_confirm = QCheckBox("Don't ask before deleting or downloading")
        self.btn = QPushButton("Sync playlist")
        self.btn.setDefault(True)

        form = QFormLayout()
        form.addRow("Playlist link", self.url)
        form.addRow("Folder", self.folder)
        form.addRow("Quality", self.quality)
        form.addRow("", self.no_confirm)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(self.btn)

        box = QGroupBox("Mirror a Qobuz playlist into a local folder")
        inner = QVBoxLayout(box)
        inner.addWidget(_note(
            "Downloads tracks that were added to the playlist and deletes local tracks that were "
            "removed from it, then rewrites the .m3u in the playlist's order. A subfolder named after "
            "the playlist is created inside the folder you pick. Before anything is deleted you get a "
            "summary in the log and a Yes/No question, unless you tick the box below."))
        inner.addLayout(form)
        inner.addLayout(buttons)

        lay = QVBoxLayout(self)
        lay.addWidget(box)
        lay.addStretch(1)

        self.btn.clicked.connect(self._sync)
        self.reload_settings()

    # ---- updates -------------------------------------------------------------
    def show_update_info(self, info, error=""):
        local = updates.installed_version()
        if error:
            text = f"Installed: <b>{local}</b>. <span style='color:#e5534b'>Couldn't check GitHub: {error}</span>"
        elif not info:
            text = f"Installed: <b>{local}</b>. Not checked yet."
        else:
            newer = updates.is_newer(info.get("latest", "0"), local)
            when = f" · last change on GitHub {info['date']}" if info.get("date") else ""
            change = f"<br><span style='color:gray'>{html.escape(info['message'])}</span>" if info.get("message") else ""
            link = f" · <a href='{updates.COMMITS_PAGE}'>see changes</a>"
            if newer:
                text = (f"<b style='color:#3fa65a'>qobuz-dl {info['latest']} is available</b> "
                        f"(installed: {local}){when}{link}{change}")
            else:
                text = f"Installed: <b>{local}</b> — up to date with GitHub ({info.get('latest')}){when}{link}"
        self.update_label.setText(text)

    def show_gui_info(self, info, error=""):
        local = updates.gui_installed_version()
        link = f"<a href='https://github.com/{updates.GUI_REPO}'>github.com/{updates.GUI_REPO}</a>"
        if error:
            text = f"Qobuz-DL GUI <b>{local}</b>. <span style='color:#e5534b'>Couldn't check GitHub: {html.escape(error)}</span>"
        elif info and updates.is_newer(info.get("latest", "0"), local):
            text = (f"<b style='color:#3fa65a'>Qobuz-DL GUI {info['latest']} is available</b> (installed: {local}) · "
                    f"<a href='{updates.GUI_COMMITS_PAGE}'>see changes</a>")
        elif info:
            text = f"Qobuz-DL GUI <b>{local}</b> — up to date · {link}"
        else:
            text = f"Qobuz-DL GUI <b>{local}</b> · {link}"
        self.gui_label.setText(text)
        self.btn_gui_update.setVisible(bool(info) and updates.is_newer((info or {}).get("latest", "0"), local))

    def check_gui_updates(self, manual=False, on_newer=None):
        cached = prefs.get_json(prefs.GUI_UPDATE_INFO, None)
        if not manual and cached and time.time() - cached.get("checked", 0) < updates.CHECK_EVERY:
            self.show_gui_info(cached)
            if on_newer and updates.is_newer(cached.get("latest", "0"), updates.gui_installed_version()):
                on_newer(cached)
            return

        def done(info):
            prefs.set_json(prefs.GUI_UPDATE_INFO, info)
            self.show_gui_info(info)
            if info["newer"] and on_newer:
                on_newer(info)

        def fail(msg):
            if manual:
                self.show_gui_info(cached, error=msg)
        run_async(updates.check_gui, done, fail)

    def run_gui_update(self):
        if QMessageBox.question(self, "Update Qobuz-DL GUI",
                                "Download the latest version of this app from GitHub and install it?\n\n"
                                "Restart the app afterwards to use it.") != QMessageBox.Yes:
            return
        self.btn_gui_update.setEnabled(False)
        self.btn_gui_update.setText("Updating…")

        def done(version):
            self.btn_gui_update.setText("Update this app from GitHub")
            self.btn_gui_update.setEnabled(True)
            self.btn_gui_update.setVisible(False)
            self.gui_label.setText(f"<b style='color:#3fa65a'>Qobuz-DL GUI {html.escape(version)} installed.</b> "
                                   "Restart the app to use it.")
            QMessageBox.information(self, "Qobuz-DL GUI updated",
                                    f"Version {version} is installed. Quit (tray icon → Quit) and open the app again.")

        def fail(msg):
            self.btn_gui_update.setText("Update this app from GitHub")
            self.btn_gui_update.setEnabled(True)
            QMessageBox.warning(self, "Update failed", msg)
        run_async(updates.update_gui, done, fail)

    def check_updates(self, manual=False, on_newer=None):
        """Checks GitHub (at most once a day unless manual). Calls on_newer(info) when an update exists."""
        cached = prefs.get_json(prefs.UPDATE_INFO, None)
        if not manual and cached and time.time() - cached.get("checked", 0) < updates.CHECK_EVERY:
            self.show_update_info(cached)
            if on_newer and updates.is_newer(cached.get("latest", "0"), updates.installed_version()):
                on_newer(cached)
            return
        self.btn_check.setEnabled(False)
        self.btn_check.setText("Checking…")
        if manual:
            self.check_gui_updates(manual=True)

        def done(info):
            self.btn_check.setEnabled(True)
            self.btn_check.setText("Check now")
            prefs.set_json(prefs.UPDATE_INFO, info)
            self.show_update_info(info)
            if info["newer"] and on_newer:
                on_newer(info)

        def fail(msg):
            self.btn_check.setEnabled(True)
            self.btn_check.setText("Check now")
            if manual:
                self.show_update_info(cached, error=msg)
        run_async(updates.check, done, fail)

    def run_update(self):
        if any(j.program for j in self.runner.jobs if j.status in ("queued", "running")):
            return
        if QMessageBox.question(
                self, "Update qobuz-dl",
                "Install the latest qobuz-dl from github.com/Sei969/qobuz-dl?\n\n"
                "Downloads use the new version as soon as it's installed. Restart Qobuz-DL afterwards so "
                "search and settings use it too.") != QMessageBox.Yes:
            return
        self.runner.enqueue(Job("Update qobuz-dl from GitHub", [], program=updates.update_command(),
                                success_text="Successfully installed"))

    def reload_settings(self):
        cfg = config.load()
        self.folder.setText(_default_folder())
        set_quality(self.quality, config.get(cfg, "default_quality", "27"))

    def _sync(self):
        url = self.url.text().strip()
        if "/playlist/" not in url:
            QMessageBox.information(self, "Playlist link needed", "Paste a Qobuz playlist link.")
            return
        args = ["sp", url, "-q", str(self.quality.currentData())]
        if self.folder.text():
            args += ["-d", os.path.expanduser(self.folder.text())]
        if self.no_confirm.isChecked():
            args.append("-y")
        self.runner.enqueue(Job(f"Sync {describe(url)}", args))


class ToolsTab(QWidget):
    def __init__(self, runner, parent=None):
        super().__init__(parent)
        self.runner = runner

        # Lyrics
        self.lyrics_dir = FolderPicker("Music folder to scan")
        btn_lyrics = QPushButton("Add missing lyrics")
        lyr = QGroupBox("Add lyrics to music you already have")
        l1 = QVBoxLayout(lyr)
        l1.addWidget(_note("Scans the folder (and subfolders) for FLAC/MP3 files without lyrics and adds "
                           "them from Qobuz, LRCLIB or Genius. No audio is downloaded."))
        row = QHBoxLayout()
        row.addWidget(self.lyrics_dir, 1)
        row.addWidget(btn_lyrics)
        l1.addLayout(row)

        # History database
        self.db_dir = FolderPicker("Library folder to scan")
        btn_syncdb = QPushButton("Rebuild history from folder")
        btn_stats = QPushButton("Downloaded artists…")
        btn_purge = QPushButton("Clear download history…")
        db = QGroupBox("Download history")
        l2 = QVBoxLayout(db)
        l2.addWidget(_note("qobuz-dl remembers what it downloaded and skips it next time. Rebuilding reads "
                           "the tags of files you already have so they are skipped too."))
        row2 = QHBoxLayout()
        row2.addWidget(self.db_dir, 1)
        row2.addWidget(btn_syncdb)
        l2.addLayout(row2)
        row3 = QHBoxLayout()
        row3.addWidget(btn_stats)
        row3.addWidget(btn_purge)
        row3.addStretch(1)
        l2.addLayout(row3)

        # Files
        btn_cfg = QPushButton("Open config folder")
        btn_dl = QPushButton("Open download folder")
        files = QGroupBox("Files")
        l3 = QHBoxLayout(files)
        l3.addWidget(btn_dl)
        l3.addWidget(btn_cfg)
        l3.addStretch(1)

        # Updates
        self.update_label = QLabel("")
        self.update_label.setWordWrap(True)
        self.update_label.setTextFormat(Qt.RichText)
        self.update_label.setOpenExternalLinks(True)
        self.btn_check = QPushButton("Check now")
        self.btn_update = QPushButton("Update qobuz-dl from GitHub")
        self.btn_update.setToolTip("Installs the latest code from github.com/Sei969/qobuz-dl into this app. "
                                   "It runs in the queue, after any downloads already waiting.")
        self.gui_label = QLabel("")
        self.gui_label.setWordWrap(True)
        self.gui_label.setTextFormat(Qt.RichText)
        self.gui_label.setOpenExternalLinks(True)
        self.btn_gui_update = QPushButton("Update this app from GitHub")
        self.btn_gui_update.setVisible(False)
        upd = QGroupBox("Updates")
        l4 = QVBoxLayout(upd)
        l4.addWidget(self.update_label)
        row4 = QHBoxLayout()
        row4.addWidget(self.btn_check)
        row4.addWidget(self.btn_update)
        row4.addStretch(1)
        l4.addLayout(row4)
        l4.addWidget(self.gui_label)
        row5 = QHBoxLayout()
        row5.addWidget(self.btn_gui_update)
        row5.addStretch(1)
        l4.addLayout(row5)

        lay = QVBoxLayout(self)
        lay.addWidget(lyr)
        lay.addWidget(db)
        lay.addWidget(upd)
        lay.addWidget(files)
        lay.addStretch(1)

        self.btn_check.clicked.connect(lambda: self.check_updates(manual=True))
        self.btn_gui_update.clicked.connect(self.run_gui_update)
        self.show_gui_info(prefs.get_json(prefs.GUI_UPDATE_INFO, None))
        self.btn_update.clicked.connect(self.run_update)
        self.show_update_info(prefs.get_json(prefs.UPDATE_INFO, None))
        btn_lyrics.clicked.connect(self._lyrics)
        btn_syncdb.clicked.connect(self._sync_db)
        btn_stats.clicked.connect(self._stats)
        btn_purge.clicked.connect(self._purge)
        btn_cfg.clicked.connect(lambda: self._open(config.CONFIG_PATH))
        btn_dl.clicked.connect(lambda: self._open(os.path.expanduser(_default_folder() or "~")))
        self.reload_settings()

    def reload_settings(self):
        folder = _default_folder()
        self.lyrics_dir.setText(folder)
        self.db_dir.setText(folder)

    def _folder(self, picker):
        path = os.path.expanduser(picker.text())
        if not path or not os.path.isdir(path):
            QMessageBox.information(self, "Folder not found", f"This folder doesn't exist:\n{path or '(empty)'}")
            return None
        return path

    def _lyrics(self):
        path = self._folder(self.lyrics_dir)
        if path:
            self.runner.enqueue(Job(f"Lyrics for {os.path.basename(path) or path}", ["lyrics", path]))

    def _sync_db(self):
        path = self._folder(self.db_dir)
        if path:
            self.runner.enqueue(Job(f"Rebuild history from {os.path.basename(path) or path}",
                                    ["--sync-db", path],
                                    success_text="Database synchronization finished"))

    def _stats(self):
        try:
            from qobuz_dl.db import get_stats
            artists = get_stats(config.QOBUZ_DB) if os.path.isfile(config.QOBUZ_DB) else []
        except Exception as e:
            QMessageBox.warning(self, "Download history", f"Could not read the history: {e}")
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("Downloaded artists")
        dlg.resize(420, 520)
        lst = QListWidget()
        lst.addItems(artists)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(dlg.reject)
        v = QVBoxLayout(dlg)
        v.addWidget(QLabel(f"<b>{len(artists)}</b> unique artists" if artists
                           else "Nothing in the history yet."))
        v.addWidget(lst, 1)
        v.addWidget(buttons)
        dlg.exec()

    def _purge(self):
        if self.runner.busy():
            QMessageBox.information(self, "Busy", "Wait until the current job finishes.")
            return
        if QMessageBox.question(
                self, "Clear download history",
                "Forget everything qobuz-dl has downloaded? Files on disk are not touched, but releases "
                "you download again will no longer be skipped.") != QMessageBox.Yes:
            return
        try:
            os.remove(config.QOBUZ_DB)
        except FileNotFoundError:
            pass
        except OSError as e:
            QMessageBox.warning(self, "Clear download history", str(e))
            return
        QMessageBox.information(self, "Clear download history", "The download history was cleared.")

    def _open(self, path):
        os.makedirs(path, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))
