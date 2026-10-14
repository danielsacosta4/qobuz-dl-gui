"""Library tab: send finished downloads to the library as Letter/Artist/Album."""

import os

from PySide6.QtCore import QThread, Qt, QUrl, Signal
from PySide6.QtGui import QBrush, QColor, QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QFormLayout, QHBoxLayout, QHeaderView, QLabel, QMessageBox,
    QProgressBar, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from . import config, library, prefs
from .client import run_async
from .download_tab import FolderPicker

COLS = ["", "Album folder", "Artist", "Tracks", "Size", "Goes to", "Status", "Copy already in library"]
C_CHECK, C_NAME, C_ARTIST, C_TRACKS, C_SIZE, C_DEST, C_STATUS, C_ACTION = range(len(COLS))
ACTIONS = [("replace", "Replace it"), ("keep", "Keep both"), ("skip", "Don't send")]
DEFAULT_LIBRARY = "/mnt/navidrome/Music"


def human_size(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"
        n /= 1024


def staging_folder():
    cfg = config.load()
    return os.path.expanduser(config.get(cfg, "directory") or config.get(cfg, "default_folder") or "")


def library_folder():
    value = prefs.get_str(prefs.LIBRARY_DIR, "")
    if not value and os.path.isdir(DEFAULT_LIBRARY):
        value = DEFAULT_LIBRARY
    return os.path.expanduser(value)


class SendWorker(QThread):
    progress = Signal(int, str)            # percent of everything, current file
    albumDone = Signal(str, bool, str)     # album path, ok, message
    allDone = Signal(int, int)             # sent, failed

    def __init__(self, jobs, staging, delete_after, library_root):
        super().__init__()
        self.jobs = jobs                   # [(AlbumFolder, dest, [old copies to retire])]
        self.library_root = library_root
        self.staging = staging
        self.delete_after = delete_after
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        total = sum(j[0].size for j in self.jobs) or 1
        before = 0
        sent = failed = 0
        for album, dest, retire in self.jobs:
            if self._stop:
                break
            try:
                def cb(done, _tot, name, base=before):
                    self.progress.emit(int((base + done) * 100 / total), name)
                library.copy_album(album.path, dest, progress=cb, should_stop=lambda: self._stop)
                msg = "Sent"
                for old in retire:
                    library.retire_copy(self.library_root, old)
                if retire:
                    msg += f", replaced {len(retire)} old cop{'y' if len(retire) == 1 else 'ies'}"
                if self.delete_after:
                    library.remove_staged(album.path, self.staging)
                    msg += " and removed from downloads"
                sent += 1
                self.albumDone.emit(album.path, True, msg)
            except Exception as e:
                failed += 1
                self.albumDone.emit(album.path, False, str(e))
            before += album.size
        self.allDone.emit(sent, failed)


class LibraryTab(QWidget):
    librarySent = Signal()

    def __init__(self, status, parent=None):
        super().__init__(parent)
        self.status = status
        self.albums = []
        self.worker = None
        self._filling = False

        self.staging_label = QLabel()
        self.staging_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.library = FolderPicker("e.g. /mnt/navidrome/Music")
        self.library.setText(library_folder())
        self.delete_after = QCheckBox("Remove from the download folder after a verified copy")
        self.delete_after.setChecked(prefs.get_bool(prefs.DELETE_AFTER_SEND, True))
        form = QFormLayout()
        form.addRow("Downloads", self.staging_label)
        form.addRow("Library", self.library)
        form.addRow("", self.delete_after)

        intro = QLabel("Albums are placed as <i>Letter / Artist / Album</i> using the album artist tag. "
                       "An artist folder that already exists is reused, whatever its capitalisation. "
                       "Click an artist name to change it before sending.")
        intro.setWordWrap(True)

        self.table = QTableWidget(0, len(COLS))
        self.table.setHorizontalHeaderLabels(COLS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.SelectedClicked
                                   | QAbstractItemView.EditKeyPressed)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(C_CHECK, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(C_NAME, QHeaderView.Stretch)
        hh.setSectionResizeMode(C_DEST, QHeaderView.Stretch)
        for c in (C_ARTIST, C_TRACKS, C_SIZE, C_STATUS, C_ACTION):
            hh.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        self.table.horizontalHeaderItem(C_ACTION).setToolTip(
            "When the same album is already in the library in another quality or edition. "
            "Replaced copies go to a hidden bin in the library, not straight to deletion.")

        self.btn_refresh = QPushButton("Refresh")
        self.btn_all = QPushButton("Tick all ready")
        self.btn_none = QPushButton("Untick all")
        self.btn_open = QPushButton("Open download folder")
        self.btn_bin = QPushButton("Empty replaced bin")
        self.btn_bin.setToolTip("Old copies replaced by better ones wait in a hidden folder in the library "
                                "until you empty it")
        self.btn_send = QPushButton("Send to library")
        self.btn_send.setDefault(True)
        self.btn_stop = QPushButton("Stop")
        self.btn_stop.setVisible(False)
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        self.summary = QLabel("")
        self.summary.setEnabled(False)

        buttons = QHBoxLayout()
        buttons.addWidget(self.btn_refresh)
        buttons.addWidget(self.btn_all)
        buttons.addWidget(self.btn_none)
        buttons.addWidget(self.btn_open)
        buttons.addWidget(self.btn_bin)
        buttons.addWidget(self.summary, 1)
        buttons.addWidget(self.progress, 1)
        buttons.addWidget(self.btn_stop)
        buttons.addWidget(self.btn_send)

        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(intro)
        lay.addWidget(self.table, 1)
        lay.addLayout(buttons)

        self.btn_refresh.clicked.connect(self.refresh)
        self.btn_all.clicked.connect(lambda: self._tick_all(True))
        self.btn_none.clicked.connect(lambda: self._tick_all(False))
        self.btn_open.clicked.connect(self._open_staging)
        self.btn_bin.clicked.connect(self._empty_bin)
        self.btn_send.clicked.connect(self.send)
        self.btn_stop.clicked.connect(self._stop)
        self.table.itemChanged.connect(self._item_changed)
        self.library.changed.connect(self._library_changed)
        self.delete_after.toggled.connect(lambda on: prefs.set_value(prefs.DELETE_AFTER_SEND, on))

        self.reload_settings()

    # ---- settings ------------------------------------------------------------
    def reload_settings(self):
        self.staging_label.setText(staging_folder() or "(not set — choose a download folder in Settings)")
        self.refresh()

    def _library_changed(self, value):
        prefs.set_value(prefs.LIBRARY_DIR, value.strip())
        self._update_destinations()

    def library_path(self):
        return os.path.expanduser(self.library.text())

    # ---- scanning ------------------------------------------------------------
    def refresh(self):
        if self.worker and self.worker.isRunning():
            return
        staging = staging_folder()
        if not staging or not os.path.isdir(staging):
            self.albums = []
            self._fill()
            self.summary.setText("The download folder doesn't exist yet.")
            return
        self.btn_refresh.setEnabled(False)
        self.summary.setText("Scanning…")
        lib = self.library_path()
        disc_prefix = config.get(config.load(), "multiple_disc_prefix", "CD") or "CD"

        def done(albums):
            self.btn_refresh.setEnabled(True)
            self.albums = albums
            self._fill()

        def fail(msg):
            self.btn_refresh.setEnabled(True)
            self.summary.setText(f"Scan failed: {msg}")
        run_async(lambda: library.scan_staging(staging, lib, disc_prefix), done, fail)

    def _fill(self):
        self._filling = True
        self.table.setRowCount(len(self.albums))
        for r, album in enumerate(self.albums):
            check = QTableWidgetItem()
            check.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            sendable = album.kind == "album" and album.state != "in_progress"
            check.setCheckState(Qt.Checked if sendable and album.state == "ready" else Qt.Unchecked)
            if not sendable:
                check.setFlags(Qt.ItemIsEnabled)
            self.table.setItem(r, C_CHECK, check)
            cells = {C_NAME: album.name, C_ARTIST: album.artist, C_TRACKS: str(album.tracks),
                     C_SIZE: human_size(album.size)}
            for c, text in cells.items():
                cell = QTableWidgetItem(text)
                cell.setToolTip(album.path if c == C_NAME else text)
                if c != C_ARTIST or not sendable:
                    cell.setFlags(cell.flags() & ~Qt.ItemIsEditable)
                self.table.setItem(r, c, cell)
            for c in (C_DEST, C_STATUS, C_ACTION):
                cell = QTableWidgetItem("")
                cell.setFlags(cell.flags() & ~Qt.ItemIsEditable)
                self.table.setItem(r, c, cell)
        self._filling = False
        self._update_destinations()

    def _update_destinations(self):
        lib = self.library_path()
        lib_ok = bool(lib) and os.path.isdir(lib)
        self._filling = True
        ready = 0
        for r, album in enumerate(self.albums):
            dest_cell, status_cell = self.table.item(r, C_DEST), self.table.item(r, C_STATUS)
            if dest_cell is None:
                continue
            colour = None
            if album.kind == "playlist":
                dest, status, colour = "—", "Playlist (mixed artists) — not sorted", "#8a8a8a"
            elif album.state == "in_progress":
                dest, status, colour = "—", "Still downloading", "#c99a2e"
            else:
                if lib_ok:
                    full, status = library.destination(lib, album)
                    dest = os.path.relpath(full, lib)
                    album.dest = full
                else:
                    dest, status = "—", "Library folder not found"
                    album.dest = None
                if album.state == "incomplete":
                    status = "Incomplete download · " + status
                    colour = "#c99a2e"
                elif "merge" in status or album.others:
                    colour = "#c99a2e"
                elif lib_ok:
                    colour = "#3fa65a"
                    ready += 1
            dest_cell.setText(dest)
            dest_cell.setToolTip(getattr(album, "dest", None) or dest)
            status_cell.setText(status)
            self._set_action_cell(r, album)
            if colour:
                status_cell.setForeground(QBrush(QColor(colour)))
        self._filling = False
        n = len(self.albums)
        self.summary.setText(f"{n} folder{'s' if n != 1 else ''} in downloads" + (
            "" if lib_ok else " · library folder not found — is the share mounted?"))
        self.btn_send.setEnabled(lib_ok and n > 0)

    def _set_action_cell(self, row, album):
        old = self.table.cellWidget(row, C_ACTION)
        if not album.others or album.kind != "album" or album.state == "in_progress":
            if old is not None:
                self.table.removeCellWidget(row, C_ACTION)
            return
        if isinstance(old, QComboBox) and old.property("album_path") == album.path:
            return
        combo = QComboBox()
        combo.setProperty("album_path", album.path)
        for key, label in ACTIONS:
            combo.addItem(label, key)
        album.action = library.default_action(album)
        combo.setCurrentIndex([k for k, _ in ACTIONS].index(album.action))
        names = "\n".join(f"• {os.path.basename(p)} ({library.describe_quality(q)})" for p, q in album.others)
        combo.setToolTip(f"Already in the library:\n{names}")

        def changed(_i, a=album, c=combo):
            a.action = c.currentData()
        combo.currentIndexChanged.connect(changed)
        self.table.setCellWidget(row, C_ACTION, combo)

    def _empty_bin(self):
        lib = self.library_path()
        if not os.path.isdir(lib):
            return
        count, size = library.trash_size(lib)
        if not count:
            QMessageBox.information(self, "Replaced bin", "The bin is empty.")
            return
        if QMessageBox.question(
                self, "Empty replaced bin",
                f"Permanently delete {count} replaced album{'s' if count != 1 else ''} "
                f"({human_size(size)}) from {os.path.join(lib, library.TRASH_DIR)}?") != QMessageBox.Yes:
            return
        try:
            library.empty_trash(lib)
        except OSError as e:
            QMessageBox.warning(self, "Replaced bin", str(e))
            return
        self.status(f"Deleted {count} replaced album{'s' if count != 1 else ''}.")

    def _item_changed(self, item):
        if self._filling or item.column() != C_ARTIST:
            return
        album = self.albums[item.row()]
        album.artist = item.text().strip() or album.artist
        self._update_destinations()

    def _tick_all(self, on):
        for r, album in enumerate(self.albums):
            cell = self.table.item(r, C_CHECK)
            if cell and cell.flags() & Qt.ItemIsUserCheckable:
                cell.setCheckState(Qt.Checked if on and album.state == "ready" else Qt.Unchecked)

    def _open_staging(self):
        path = staging_folder()
        if path and os.path.isdir(path):
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    # ---- sending -------------------------------------------------------------
    def send(self):
        lib = self.library_path()
        if not os.path.isdir(lib):
            QMessageBox.warning(self, "Library", f"The library folder isn't available:\n{lib}\n\n"
                                "Check that the TrueNAS share is mounted.")
            return
        jobs = []
        for r, album in enumerate(self.albums):
            cell = self.table.item(r, C_CHECK)
            if not (cell and cell.checkState() == Qt.Checked and getattr(album, "dest", None)):
                continue
            if album.others and album.action == "skip":
                continue
            retire = [p for p, _ in album.others] if album.others and album.action == "replace" else []
            jobs.append((album, album.dest, retire))
        if not jobs:
            self.status("Tick at least one album to send (rows set to \"Don't send\" are left out).")
            return
        merges = sum(1 for a, d, _ in jobs if os.path.isdir(d))
        replaced = [p for _, _, ret in jobs for p in ret]
        size = sum(a.size for a, _, _ in jobs)
        text = f"Copy {len(jobs)} album{'s' if len(jobs) != 1 else ''} ({human_size(size)}) to {lib}?"
        if replaced:
            listing = "\n".join(f"• {os.path.relpath(p, lib)}" for p in replaced[:8])
            more = f"\n… and {len(replaced) - 8} more" if len(replaced) > 8 else ""
            text += (f"\n\nThese older copies will be moved to the hidden replaced bin "
                     f"(empty it later from this tab):\n{listing}{more}")
        if merges:
            text += f"\n\n{merges} already exist under the same name; files with the same name will be replaced."
        if self.delete_after.isChecked():
            text += "\n\nEach album is removed from the download folder once its copy is verified."
        if QMessageBox.question(self, "Send to library", text) != QMessageBox.Yes:
            return

        self.worker = SendWorker(jobs, staging_folder(), self.delete_after.isChecked(), lib)
        self.worker.progress.connect(self._progress)
        self.worker.albumDone.connect(self._album_done)
        self.worker.allDone.connect(self._all_done)
        self._sending(True)
        self.worker.start()

    def _sending(self, on):
        for w in (self.btn_send, self.btn_refresh, self.btn_all, self.btn_none, self.library, self.delete_after,
                  self.btn_bin):
            w.setEnabled(not on)
        self.btn_stop.setVisible(on)
        self.progress.setVisible(on)
        self.summary.setVisible(not on)
        self.progress.setValue(0)

    def _progress(self, percent, name):
        self.progress.setValue(percent)
        self.progress.setFormat(f"{name} — %p%")

    def _album_done(self, path, ok, message):
        for r, album in enumerate(self.albums):
            if album.path == path:
                cell = self.table.item(r, C_STATUS)
                cell.setText(("✔ " if ok else "✖ ") + message)
                cell.setForeground(QBrush(QColor("#3fa65a" if ok else "#e5534b")))

    def _stop(self):
        if self.worker:
            self.worker.stop()

    def _all_done(self, sent, failed):
        self._sending(False)
        self.worker = None
        msg = f"Sent {sent} album{'s' if sent != 1 else ''} to the library."
        if failed:
            msg += f" {failed} failed — see the Status column."
        self.status(msg, error=bool(failed))
        if sent:
            self._rescan_navidrome(msg, bool(failed))
            self.librarySent.emit()
        if not failed:
            self.refresh()

    def _rescan_navidrome(self, msg, error):
        from . import navidrome
        if not (navidrome.configured() and prefs.get_bool(prefs.NAVIDROME_RESCAN, True)):
            return

        def ok(_r):
            self.status(msg + " Navidrome is rescanning.", error=error)

        def fail(err):
            self.status(msg + f" Navidrome rescan didn't start: {err}", error=True)
        run_async(lambda: navidrome.Navidrome.from_settings().start_scan(), ok, fail)

    def busy(self):
        return bool(self.worker and self.worker.isRunning())
