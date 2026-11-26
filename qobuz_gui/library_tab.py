"""Library tab: send finished downloads to the library as Letter/Artist/Album."""

import os

from PySide6.QtCore import QThread, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QBrush, QColor, QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QFormLayout, QHBoxLayout, QHeaderView, QLabel, QMessageBox,
    QProgressBar, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from . import automation, config, history, library, prefs, rules, safeio, theme
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
        self.jobs = jobs                   # [(AlbumFolder, safeio.Plan)]
        self.library_root = library_root
        self.staging = staging
        self.delete_after = delete_after
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        # bytes are counted twice: written, then read back from the share and checked
        total = 2 * sum(plan.total_bytes() for _, plan in self.jobs) or 1
        before = 0
        sent = failed = 0
        journal = history.journal()
        for album, plan in self.jobs:
            if self._stop:
                break
            try:
                def cb(done, _tot, name, base=before):
                    self.progress.emit(int((base + done) * 100 / total), name)
                plan.execute(journal, progress=cb, should_stop=lambda: self._stop)
                retired = sum(1 for s in plan.steps if s["kind"] == "move" and s["extra"].get("why") == "replaced")
                msg = "Sent (checksums verified)"
                if retired:
                    msg += f", replaced {retired} old cop{'y' if retired == 1 else 'ies'}"
                if any(s["kind"] == "trash" for s in plan.steps):
                    msg += ", download moved to Trash"
                    library.remove_empty_parents(album.path, self.staging)
                sent += 1
                self.albumDone.emit(album.path, True, msg)
            except Exception as e:
                failed += 1
                self.albumDone.emit(album.path, False, str(e))
            before += 2 * plan.total_bytes()
        self.allDone.emit(sent, failed)


class LibraryTab(QWidget):
    librarySent = Signal()
    autoSendDone = Signal(int, int, int)    # sent, failed, left for a decision

    def __init__(self, status, parent=None):
        super().__init__(parent)
        self.status = status
        self.albums = []
        self.worker = None
        self._filling = False
        self._scanning = False
        self._rescan = False
        self._after_scan = []
        self._last_scan = None            # (tree signature, library) of the albums shown
        self._auto = False
        self._auto_left = 0
        self._lib_timer = QTimer(self)        # typing a library path: rescan once, when it stops
        self._lib_timer.setSingleShot(True)
        self._lib_timer.setInterval(700)
        self._lib_timer.timeout.connect(lambda: self.refresh(force=True))

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
        self.btn_history = QPushButton("History…")
        self.btn_history.setToolTip("Every change the app made, with Undo")
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
        buttons.addWidget(self.btn_history)
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

        self.btn_refresh.clicked.connect(lambda: self.refresh(force=True))
        self.btn_all.clicked.connect(lambda: self._tick_all(True))
        self.btn_none.clicked.connect(lambda: self._tick_all(False))
        self.btn_open.clicked.connect(self._open_staging)
        self.btn_bin.clicked.connect(self._empty_bin)
        self.btn_history.clicked.connect(self.show_history)
        self.btn_send.clicked.connect(self.send)
        self.btn_stop.clicked.connect(self._stop)
        self.table.itemChanged.connect(self._item_changed)
        self.library.changed.connect(self._library_changed)
        self.delete_after.toggled.connect(lambda on: prefs.set_value(prefs.DELETE_AFTER_SEND, on))

        self.reload_settings()

    # ---- settings ------------------------------------------------------------
    def reload_settings(self):
        self.staging_label.setText(staging_folder() or "(not set — choose a download folder in Settings)")
        self.refresh(force=True)

    def _library_changed(self, value):
        prefs.set_value(prefs.LIBRARY_DIR, value.strip())
        self._lib_timer.start()

    def library_path(self):
        return os.path.expanduser(self.library.text())

    # ---- scanning ------------------------------------------------------------
    def refresh(self, then=None, force=False):
        """Re-reads the download folder in the background. Unless forced, nothing is re-read when
        the folder tree hasn't changed since the last scan. `then` runs once the table is up to date."""
        if then is not None:
            self._after_scan.append(then)
        if self.worker and self.worker.isRunning():
            self._after_scan.clear()
            return
        if self._scanning:
            self._rescan = self._rescan or force
            return
        staging = staging_folder()
        if not staging or not os.path.isdir(staging):
            self.albums = []
            self._last_scan = None
            self._fill()
            self.summary.setText("The download folder doesn't exist yet.")
            self._run_after_scan()
            return
        self._scanning = True
        self.btn_refresh.setEnabled(False)
        lib = self.library_path()
        disc_prefix = config.get(config.load(), "multiple_disc_prefix", "CD") or "CD"
        last = None if force else self._last_scan
        if last is None:
            self.summary.setText("Scanning…")

        def work():
            sig = library.tree_signature(staging)
            if last is not None and last == (sig, lib):
                return None
            albums = library.scan_staging(staging, lib, disc_prefix)
            return sig, library.resolve_all(lib, albums)

        def done(result):
            self._scanning = False
            self.btn_refresh.setEnabled(True)
            if self._rescan or lib != self.library_path():
                self._rescan = False      # asked again while this scan ran: scan once more
                self.refresh(force=True)
                return
            if result is not None:
                sig, albums = result
                self._last_scan = (sig, lib)
                self.albums = albums
                self._fill()
            self._run_after_scan()

        def fail(msg):
            self._scanning = False
            self.btn_refresh.setEnabled(True)
            self._last_scan = None
            self.summary.setText(f"Scan failed: {msg}")
            self._after_scan.clear()
        run_async(work, done, fail)

    def _run_after_scan(self):
        pending, self._after_scan = self._after_scan, []
        for fn in pending:
            fn()

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
                dest, status, colour = "—", "Playlist (mixed artists) — not sorted", theme.MUTED
            elif album.state == "in_progress":
                dest, status, colour = "—", "Still downloading", theme.WARN
            else:
                if lib_ok:
                    if album.dest_status is None or album.dest_lib != lib:
                        library.resolve(lib, album)   # one album (artist edited) or a library change
                    full, status = album.dest, album.dest_status
                    dest = os.path.relpath(full, lib)
                else:
                    dest, status = "—", "Library folder not found"
                    album.dest = None
                if album.state == "incomplete":
                    status = "Incomplete download · " + status
                    colour = theme.WARN
                elif "merge" in status or album.others:
                    colour = theme.WARN
                elif lib_ok:
                    colour = theme.OK
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
        combo.setCurrentIndex([k for k, _ in ACTIONS].index(album.action))
        names = "\n".join(f"• {os.path.basename(p)} ({library.describe_quality(q)})" for p, q in album.others)
        combo.setToolTip(f"Already in the library:\n{names}")

        def changed(_i, a=album, c=combo):
            a.action = c.currentData()
        combo.currentIndexChanged.connect(changed)
        self.table.setCellWidget(row, C_ACTION, combo)

    def show_history(self):
        history.HistoryDialog(self, on_change=lambda *_: self.refresh(force=True)).exec()

    def _empty_bin(self):
        lib = self.library_path()
        if safeio.library_state(lib) != "ok":
            history.ensure_library(self, lib)
            return
        count, size = library.trash_size(lib)
        if not count:
            QMessageBox.information(self, "Replaced bin", "The bin is empty.")
            return
        if QMessageBox.question(
                self, "Empty replaced bin",
                f"Permanently delete {count} replaced album{'s' if count != 1 else ''} "
                f"({human_size(size)}) from {os.path.join(lib, library.TRASH_DIR)}?\n\n"
                "This is the one step that can't be undone.") != QMessageBox.Yes:
            return
        j = history.journal()
        batch = j.begin(f"Empty replaced bin ({count} albums)")
        try:
            j.record(batch, 0, "purge", os.path.join(lib, library.TRASH_DIR), None,
                     {"albums": count, "bytes": size})
            library.empty_trash(lib)
            j.finish(batch, "done", "permanent — can't be undone")
        except OSError as e:
            j.finish(batch, "failed", str(e))
            QMessageBox.warning(self, "Replaced bin", str(e))
            return
        self.status(f"Deleted {count} replaced album{'s' if count != 1 else ''}.")

    def _item_changed(self, item):
        if self._filling or item.column() != C_ARTIST:
            return
        album = self.albums[item.row()]
        album.artist = item.text().strip() or album.artist
        album.dest_status = None
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
        if not history.ensure_library(self, lib):
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
        # "Keep both" is a deliberate choice: remember it so R1 doesn't report the pair later
        kept = [rules.exception_key("duplicate", a.artist, library.clean_album_name(a.name))
                for a, _, _ in jobs if a.others and a.action == "keep"]
        if kept:
            prefs.set_json(rules.EXCEPTIONS, sorted(set(prefs.get_json(rules.EXCEPTIONS, []) or []) | set(kept)))
        plans = self._plans(jobs, lib)
        if not history.preview(self, "Send to library", [p for _, p in plans],
                               footer="Each file is read back from the share and compared (SHA-256) before the "
                                      "next step. Replaced files go to the hidden bin, downloads to the desktop "
                                      "Trash — both can be undone from History."):
            return
        self._start(plans, lib)

    def _plans(self, jobs, lib):
        plans = []
        for album, dest, retire in jobs:
            plan = safeio.Plan(f"Send {album.artist} — {library.clean_album_name(album.name)}", lib)
            plan.copy_tree(album.path, dest)
            for old in retire:
                plan.retire(old)
            if self.delete_after.isChecked():
                plan.trash(album.path)
            plans.append((album, plan))
        return plans

    def _start(self, plans, lib, auto=False):
        self._auto = auto
        self.worker = SendWorker(plans, staging_folder(), self.delete_after.isChecked(), lib)
        self.worker.progress.connect(self._progress)
        self.worker.albumDone.connect(self._album_done)
        self.worker.allDone.connect(self._all_done)
        self._sending(True)
        self.worker.start()

    def auto_send(self):
        """Sends the finished downloads that need no decision (new to the library, nothing to merge
        or replace), without the preview. Same checksums and History as a manual send."""
        if self.busy():
            return
        lib = self.library_path()
        if safeio.library_state(lib) != "ok":
            self.status("Auto-send skipped: the library isn't available or hasn't been confirmed yet "
                        "(send once by hand to confirm it).", error=True)
            return

        def go():
            if self.busy():
                return
            ready = [a for a in self.albums if a.kind == "album" and a.state == "ready"]
            albums = automation.auto_sendable(self.albums)
            if not albums:
                return
            self._auto_left = len(ready) - len(albums)
            self._start(self._plans([(a, a.dest, []) for a in albums], lib), lib, auto=True)
        self.refresh(then=go, force=True)

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
                cell.setForeground(QBrush(QColor(theme.OK if ok else theme.ERROR)))

    def _stop(self):
        if self.worker:
            self.worker.stop()

    def _all_done(self, sent, failed):
        self._sending(False)
        self.worker = None
        auto, self._auto = self._auto, False
        msg = f"Sent {sent} album{'s' if sent != 1 else ''} to the library."
        if failed:
            msg += f" {failed} failed — see the Status column."
        self.status(msg, error=bool(failed))
        if sent:
            self._rescan_navidrome(msg, bool(failed))
            self.librarySent.emit()
        if not failed:
            self.refresh(force=True)
        if auto:
            self.autoSendDone.emit(sent, failed, self._auto_left)

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
