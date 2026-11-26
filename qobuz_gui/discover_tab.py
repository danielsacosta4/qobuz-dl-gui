"""Discover tab: artists you don't have that your Navidrome server relates to the ones you own."""

import html
import time

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QHBoxLayout, QHeaderView, QLabel, QProgressBar, QPushButton, QSpinBox,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from . import cache, discover, matching, navidrome, prefs, studio, theme, wikipedia
from .client import run_async

COLUMNS = ["Artist", "Why", "Score"]


def _wiki_list(name, choice):
    """Wikipedia's studio albums for an artist set to use them (None otherwise, or when Wikipedia fails)."""
    if choice.source_for(name) != studio.WIKI:
        return None
    try:
        found = wikipedia.studio_albums(name, choice.lang)
    except wikipedia.WikipediaError:
        return None
    return found["albums"] if found else None


class FindWorker(QThread):
    progressed = Signal(int, int, str)
    finishedFind = Signal(object, str, str)     # rows, note, error

    def __init__(self, weigh, top_n, dismissed):
        super().__init__()
        self.weigh, self.top_n, self.dismissed = weigh, top_n, dismissed
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        try:
            rows, note = discover.find_suggestions(
                navidrome.Navidrome.from_settings(), self.weigh, self.top_n, self.dismissed,
                progress=lambda n, t, a: self.progressed.emit(n, t, a), should_stop=lambda: self._stop)
            self.finishedFind.emit(rows, note, "")
        except Exception as e:
            self.finishedFind.emit([], "", str(e) or e.__class__.__name__)


class DiscoverTab(QWidget):
    browseArtist = Signal(str)                  # artist name: searched on Qobuz when you press Browse

    def __init__(self, session, download_tab, status, parent=None):
        super().__init__(parent)
        self.session = session
        self.download_tab = download_tab
        self.status = status
        self.worker = None
        self.rows = []

        self.weigh = QCheckBox("Weigh by what I play")
        self.weigh.setChecked(True)
        self.weigh.setToolTip("Artists you play or star in Navidrome count more. Your players must send plays to "
                              "Navidrome (scrobble) for the counts to mean anything.")
        self.top_n = QSpinBox()
        self.top_n.setRange(5, 300)
        self.top_n.setValue(40)
        self.top_n.setPrefix("from my top ")
        self.top_n.setSuffix(" artists")
        self.show_dismissed = QCheckBox("Show dismissed")
        self.btn_find = QPushButton("Find")
        self.btn_find.setDefault(True)
        self.btn_stop = QPushButton("Stop")
        self.btn_stop.setVisible(False)
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        self.info = QLabel("")
        self.info.setWordWrap(True)
        self.info.setEnabled(False)

        bar = QHBoxLayout()
        for w in (self.weigh, self.top_n, self.show_dismissed):
            bar.addWidget(w)
        bar.addStretch(1)
        bar.addWidget(self.progress, 1)
        bar.addWidget(self.btn_stop)
        bar.addWidget(self.btn_find)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.Stretch)
        hh.setSectionResizeMode(2, QHeaderView.ResizeToContents)

        self.btn_browse = QPushButton("Search on Qobuz")
        self.btn_browse.setToolTip("Searches Qobuz for this artist (one request, only when you press it)")
        self.btn_download = QPushButton("Download latest album")
        self.btn_download.setToolTip("Finds the artist on Qobuz and queues their newest studio album "
                                     "(a few requests, only when you press it)")
        self.btn_dismiss = QPushButton("Dismiss / restore")
        self.btn_dismiss.setToolTip("Hide an artist you don't want to see again (Show dismissed brings them back)")
        self.hint = QLabel("This tab only talks to your Navidrome server. Qobuz is contacted only by the two "
                           "buttons on the right.")
        self.hint.setEnabled(False)
        self.hint.setWordWrap(True)
        actions = QHBoxLayout()
        actions.addWidget(self.btn_dismiss)
        actions.addWidget(self.hint, 1)
        actions.addWidget(self.btn_browse)
        actions.addWidget(self.btn_download)

        lay = QVBoxLayout(self)
        lay.addLayout(bar)
        lay.addWidget(self.info)
        lay.addWidget(self.table, 1)
        lay.addLayout(actions)

        self.btn_find.clicked.connect(self.find)
        self.btn_stop.clicked.connect(self._stop)
        self.btn_browse.clicked.connect(self._browse)
        self.btn_download.clicked.connect(self._download)
        self.btn_dismiss.clicked.connect(self._dismiss)
        self.show_dismissed.toggled.connect(self._fill)
        self.table.itemSelectionChanged.connect(self._selection)
        self.table.cellDoubleClicked.connect(lambda *_: self._browse())

        saved = cache.get("discover", "last")
        if saved and saved.get("rows"):
            self.rows = saved["rows"]
            self._fill()
            when = time.strftime("%d %b %Y %H:%M", time.localtime(saved.get("time", 0)))
            self.info.setText(f"Last search: {when}. Press Find to look again.")
        else:
            self.info.setText("Press Find. Your Navidrome server (through Last.fm) says which artists are related to "
                              "the ones in your library; the ones you don't have are listed here. Nothing is asked "
                              "of Qobuz.")
        self._selection()

    # ---- helpers -------------------------------------------------------------
    def _dismissed(self):
        return set(prefs.get_json(prefs.DISCOVER_DISMISSED, []) or [])

    def busy(self):
        return bool(self.worker and self.worker.isRunning())

    def _row(self):
        sel = sorted({i.row() for i in self.table.selectedIndexes()})
        if len(sel) != 1:
            return None
        item = self.table.item(sel[0], 0)
        return self.rows[item.data(Qt.UserRole)] if item is not None else None

    # ---- finding -------------------------------------------------------------
    def find(self):
        if self.busy():
            return
        if not navidrome.configured():
            self.info.setText("Set up the Navidrome connection in Settings first — this reads your artists from "
                              "your library.")
            return
        self.worker = FindWorker(self.weigh.isChecked(), self.top_n.value(), self._dismissed())
        self.worker.progressed.connect(self._progressed)
        self.worker.finishedFind.connect(self._finished)
        self.btn_find.setEnabled(False)
        self.btn_stop.setVisible(True)
        self.progress.setVisible(True)
        self.progress.setRange(0, 0)
        self.info.setText("Reading your library…")
        self.worker.start()

    def _progressed(self, n, total, artist):
        self.progress.setRange(0, total)
        self.progress.setValue(n)
        self.progress.setFormat(f"{n} of {total} — {artist}")

    def _stop(self):
        if self.worker:
            self.worker.stop()
            self.progress.setFormat("Stopping…")

    def _finished(self, rows, note, error):
        stopped = bool(self.worker and self.worker._stop)
        self.worker = None
        self.btn_find.setEnabled(True)
        self.btn_stop.setVisible(False)
        self.progress.setVisible(False)
        if error:
            self.info.setText(f"<span style='color:{theme.ERROR}'>{html.escape(error)}</span>")
            self.status(f"Discover failed: {error}", error=True)
            return
        self.rows = rows
        cache.put("discover", "last", {"time": time.time(), "rows": rows})
        text = f"{len(rows)} artist{'s' if len(rows) != 1 else ''} you might like" + (" (stopped early)" if stopped else "")
        self.info.setText(text + (f". {note}" if note else "."))
        self._fill()

    # ---- table ---------------------------------------------------------------
    def _fill(self):
        dismissed = self._dismissed()
        rows = [(i, r) for i, r in enumerate(self.rows) if self.show_dismissed.isChecked() or r.get("key") not in dismissed]
        self.table.setRowCount(len(rows))
        for t, (i, r) in enumerate(rows):
            because = r.get("because", [])
            why = "Similar to " + ", ".join(because[:3]) + (f" and {len(because) - 3} more" if len(because) > 3 else "")
            name = r["name"] + ("  (dismissed)" if r.get("key") in dismissed else "")
            for c, text in enumerate((name, why, f"{r.get('score', 0):g}")):
                cell = QTableWidgetItem(text)
                cell.setToolTip(text)
                if c == 0:
                    cell.setData(Qt.UserRole, i)
                if c == 2:
                    cell.setTextAlignment(Qt.AlignCenter)
                if r.get("key") in dismissed:
                    cell.setForeground(QBrush(QColor(theme.MUTED)))
                self.table.setItem(t, c, cell)
        self._selection()

    def _selection(self):
        have = self._row() is not None
        for b in (self.btn_browse, self.btn_download, self.btn_dismiss):
            b.setEnabled(have)

    # ---- actions ---------------------------------------------------------------
    def _browse(self):
        r = self._row()
        if r:
            self.browseArtist.emit(r["name"])

    def _dismiss(self):
        r = self._row()
        if not r or r.get("key") is None:
            return
        dismissed = self._dismissed()
        if r["key"] in dismissed:
            dismissed.discard(r["key"])
            self.status(f"{r['name']} is back in the list.")
        else:
            dismissed.add(r["key"])
            self.status(f"Dismissed {r['name']}. Tick “Show dismissed” to bring them back.")
        prefs.set_json(prefs.DISCOVER_DISMISSED, sorted(dismissed))
        self._fill()

    def _download(self):
        r = self._row()
        if not r:
            return
        name = r["name"]
        self.status(f"Looking for the latest studio album by {name} on Qobuz…")
        choice = studio.Choice.load()

        def work():
            key = matching.norm_artist(name)
            hit = next((a for a in self.session.search("artists", name, 10)
                        if matching.norm_artist(a.get("name")) == key), None)
            if hit is None:
                return None
            _n, items = self.session.artist_albums(hit["id"])
            return discover.latest_studio(items, _wiki_list(name, choice))

        def done(album):
            if album is None:
                self.status(f"Couldn't find a studio album by {name} on Qobuz — use Search on Qobuz.", error=True)
            else:
                self._queue_album(album)
        run_async(work, done, lambda m: self.status(f"Couldn't load {name} from Qobuz: {m}", error=True))

    def _queue_album(self, album):
        artist = (album.get("artist") or {}).get("name", "")
        title = album.get("title", "")
        entry = dict(src=f"https://play.qobuz.com/album/{album.get('id')}", title=f"{artist} — {title}",
                     artist=artist, album=title, album_id=album.get("id"))
        entries = self.download_tab.filter_owned([entry])
        if not entries:
            return
        added = sum(len(self.download_tab.enqueue([e["src"]], title=e["title"])) for e in entries)
        if added:
            self.status(f"Queued {artist} — {title}.")
