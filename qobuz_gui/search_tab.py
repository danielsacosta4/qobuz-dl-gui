"""Search tab: catalogue search, favourites, artist discographies and an album details panel."""

import html

from PySide6.QtCore import QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QBrush, QColor, QDesktopServices, QIcon, QPixmap
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkRequest
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMenu,
    QPushButton, QSpinBox, QSplitter, QTableWidget, QTableWidgetItem, QTextBrowser, QVBoxLayout,
    QWidget,
)

from . import classify, musicbrainz, prefs
from .client import run_async

WEB_URL = "https://play.qobuz.com/"
KINDS = [("albums", "Albums"), ("tracks", "Tracks"), ("artists", "Artists"), ("playlists", "Playlists")]
COLUMNS = ["", "Title", "Artist", "Release", "Official", "Edition", "Explicit", "Year", "Quality", "Have"]
(C_ICON, C_TITLE, C_ARTIST, C_RELEASE, C_OFFICIAL, C_EDITION, C_EXPLICIT, C_YEAR, C_QUALITY,
 C_HAVE) = range(len(COLUMNS))
AMBER = QColor("#c99a2e")
RED = QColor("#e5534b")
THUMB = 48
GREEN = QColor("#3fa65a")


def _first(*values):
    for v in values:
        if v:
            return v
    return ""


def _image(item, kind, size="thumbnail"):
    if kind == "albums":
        img = item.get("image") or {}
        return _first(img.get(size), img.get("small"), img.get("large"))
    if kind == "tracks":
        img = (item.get("album") or {}).get("image") or {}
        return _first(img.get(size), img.get("small"), img.get("large"))
    if kind == "artists":
        img = item.get("image") or {}
        if isinstance(img, dict):
            return _first(img.get("small"), img.get("medium"), img.get("large"), item.get("picture"))
        return _first(item.get("picture"))
    if kind == "playlists":
        for key in ("images300", "images150", "image_rectangle", "image_rectangle_mini", "images"):
            val = item.get(key)
            if isinstance(val, list) and val:
                return val[0]
    return ""


def _quality(item):
    if item.get("hires_streamable") or item.get("hires"):
        return f"Hi-Res {item.get('maximum_bit_depth', 24)}-bit / {item.get('maximum_sampling_rate', 96)} kHz"
    if "maximum_bit_depth" in item or "streamable" in item:
        return "CD 16-bit / 44.1 kHz"
    return ""


def _duration(seconds):
    seconds = int(seconds or 0)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def row_for(item, kind):
    """Returns a dict describing one result row."""
    if kind == "albums":
        artist = (item.get("artist") or {}).get("name", "")
        return dict(
            url=f"{WEB_URL}album/{item.get('id')}", id=item.get("id"), album_id=item.get("id"),
            cover=_image(item, kind), title=item.get("title", ""), artist=artist,
            release=classify.release_kind(item), edition=classify.edition(item),
            explicit="E" if classify.is_explicit(item) else "Clean",
            year=str(item.get("release_date_original") or item.get("released_at") or "")[:4],
            quality=_quality(item), browse_artist=None, have_title=item.get("title", ""),
        )
    if kind == "tracks":
        album = item.get("album") or {}
        artist = (item.get("performer") or {}).get("name") or (album.get("artist") or {}).get("name", "")
        return dict(
            url=f"{WEB_URL}track/{item.get('id')}", id=item.get("id"), album_id=album.get("id"),
            cover=_image(item, kind), title=item.get("title", ""), artist=artist,
            release="Track", edition=classify.edition(item),
            explicit="E" if classify.is_explicit(item) else "Clean",
            year=str(album.get("release_date_original") or item.get("release_date_original") or "")[:4],
            quality=_quality(item), browse_artist=None, have_title=album.get("title", ""),
            have_artist=(album.get("artist") or {}).get("name", artist), album_title=album.get("title", ""),
        )
    if kind == "artists":
        return dict(
            url=f"{WEB_URL}artist/{item.get('id')}", id=item.get("id"), album_id=None,
            cover=_image(item, kind), title=item.get("name", ""), artist="",
            release=f"{item.get('albums_count', '?')} releases", edition="", explicit="", year="",
            quality="", browse_artist=(item.get("id"), item.get("name", "")), have_title="",
        )
    owner = (item.get("owner") or {}).get("name", "")
    return dict(
        url=f"{WEB_URL}playlist/{item.get('id')}", id=item.get("id"), album_id=None,
        cover=_image(item, kind), title=item.get("name", ""), artist=owner,
        release=f"Playlist · {item.get('tracks_count', '?')} tracks", edition="", explicit="", year="",
        quality="", browse_artist=None, have_title="",
    )


class SearchTab(QWidget):
    def __init__(self, session, download_tab, status, index_provider, parent=None):
        super().__init__(parent)
        self.session = session
        self.download_tab = download_tab
        self.status = status
        self.index_provider = index_provider
        self.net = QNetworkAccessManager(self)
        self.covers = {}
        self.pending = {}
        self.history = []
        self.current = None          # (items, kind, heading, artist)
        self.rows = []
        self.album_cache = {}
        self._details_for = None
        self.mb_check = None          # musicbrainz.ArtistCheck for the artist being browsed
        self.mb_for = None

        # --- search bar
        self.query = QLineEdit()
        self.query.setPlaceholderText("Search Qobuz…")
        self.query.setClearButtonEnabled(True)
        self.kind = QComboBox()
        for key, label in KINDS:
            self.kind.addItem(label, key)
        self.limit = QSpinBox()
        self.limit.setRange(5, 500)
        self.limit.setValue(50)
        self.limit.setPrefix("max ")
        self.btn_search = QPushButton("Search")
        self.btn_favs = QPushButton("My favourites")
        self.btn_favs.setToolTip("Show your Qobuz favourites of the selected type")
        bar = QHBoxLayout()
        bar.addWidget(self.query, 1)
        bar.addWidget(self.kind)
        bar.addWidget(self.limit)
        bar.addWidget(self.btn_search)
        bar.addWidget(self.btn_favs)

        # --- heading + filters
        self.btn_back = QPushButton("← Back")
        self.btn_back.setEnabled(False)
        self.heading = QLabel("")
        self.btn_discog = QPushButton("Download discography")
        self.btn_discog.setToolTip("Queue every release shown below (the type filters and explicit preference apply)")
        self.btn_discog.setVisible(False)
        head = QHBoxLayout()
        head.addWidget(self.btn_back)
        head.addWidget(self.heading, 1)
        head.addWidget(self.btn_discog)

        self.type_boxes = {}
        filters = QHBoxLayout()
        filters.addWidget(QLabel("Show:"))
        for kind in classify.KINDS:
            cb = QCheckBox(kind)
            cb.setChecked(True)
            cb.toggled.connect(self._refilter)
            self.type_boxes[kind] = cb
            filters.addWidget(cb)
        filters.addSpacing(16)
        self.prefer_explicit = QCheckBox("Prefer explicit")
        self.prefer_explicit.setToolTip("Hide a clean release when its explicit version is listed. "
                                        "Releases that only exist clean stay.")
        self.prefer_explicit.setChecked(prefs.get_bool(prefs.PREFER_EXPLICIT, True))
        self.prefer_explicit.toggled.connect(self._refilter)
        filters.addWidget(self.prefer_explicit)
        self.official_only = QCheckBox("Official only")
        self.official_only.setToolTip("On artist pages: hide releases MusicBrainz lists as bootlegs or doesn't "
                                      "know at all (radio broadcasts, unofficial compilations…)")
        self.official_only.setChecked(prefs.get_bool(prefs.MB_OFFICIAL_ONLY, True))
        self.official_only.toggled.connect(self._refilter)
        filters.addWidget(self.official_only)
        self.show_hidden = QCheckBox("Show hidden")
        self.show_hidden.setToolTip("Show releases and labels you've hidden (right-click to unhide them)")
        self.show_hidden.toggled.connect(self._refilter)
        filters.addWidget(self.show_hidden)
        filters.addStretch(1)
        self.count_label = QLabel("")
        self.count_label.setEnabled(False)
        filters.addWidget(self.count_label)

        # --- results table
        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setIconSize(QSize(THUMB, THUMB))
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(THUMB + 8)
        self.table.setSortingEnabled(True)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(C_ICON, QHeaderView.Fixed)
        self.table.setColumnWidth(C_ICON, THUMB + 12)
        hh.setSectionResizeMode(C_TITLE, QHeaderView.Stretch)
        for c in range(C_ARTIST, len(COLUMNS)):
            hh.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        self.table.horizontalHeaderItem(C_HAVE).setToolTip(
            "✔ = found in your library folder · ↓ = in qobuz-dl's download history")

        # --- details panel
        self.details = QTextBrowser()
        self.details.setOpenExternalLinks(True)
        self.details.setMinimumWidth(300)
        self.details.setHtml(self._empty_details())
        self.btn_detail_dl = QPushButton("Download this album")
        self.btn_detail_dl.setEnabled(False)
        det_box = QVBoxLayout()
        det_box.setContentsMargins(0, 0, 0, 0)
        det_box.addWidget(self.details, 1)
        det_box.addWidget(self.btn_detail_dl)
        det_widget = QWidget()
        det_widget.setLayout(det_box)

        split = QSplitter(Qt.Horizontal)
        split.addWidget(self.table)
        split.addWidget(det_widget)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 1)
        split.setSizes([800, 340])

        # --- actions
        self.hint = QLabel("Double-click an artist to browse their releases, or anything else to download it. "
                           "Ctrl/Shift-click to pick several.")
        self.hint.setWordWrap(True)
        self.hint.setEnabled(False)
        self.btn_open = QPushButton("Open on Qobuz")
        self.btn_copy = QPushButton("Copy links")
        self.btn_queue = QPushButton("Download selected")
        self.btn_queue.setDefault(True)
        actions = QHBoxLayout()
        actions.addWidget(self.hint, 1)
        actions.addWidget(self.btn_open)
        actions.addWidget(self.btn_copy)
        actions.addWidget(self.btn_queue)

        lay = QVBoxLayout(self)
        lay.addLayout(bar)
        lay.addLayout(head)
        lay.addLayout(filters)
        self.mb_label = QLabel(musicbrainz.LIMITATION)
        self.mb_label.setWordWrap(True)
        self.mb_label.setEnabled(False)
        self.mb_label.setVisible(False)
        lay.addWidget(self.mb_label)
        lay.addWidget(split, 1)
        lay.addLayout(actions)

        self._detail_timer = QTimer(self)
        self._detail_timer.setSingleShot(True)
        self._detail_timer.setInterval(250)
        self._detail_timer.timeout.connect(self._load_details)

        self.query.returnPressed.connect(self.search)
        self.btn_search.clicked.connect(self.search)
        self.btn_favs.clicked.connect(self.favorites)
        self.btn_queue.clicked.connect(self.download_selected)
        self.btn_open.clicked.connect(self.open_selected)
        self.btn_copy.clicked.connect(self.copy_selected)
        self.btn_back.clicked.connect(self.back)
        self.btn_discog.clicked.connect(self.download_discography)
        self.btn_detail_dl.clicked.connect(self._download_detail_album)
        self.table.cellDoubleClicked.connect(self._double_clicked)
        self.table.customContextMenuRequested.connect(self._menu)
        self.table.itemSelectionChanged.connect(lambda: self._detail_timer.start())

    # ---- loading -------------------------------------------------------------
    def _busy(self, text):
        self.heading.setText(text)
        for b in (self.btn_search, self.btn_favs):
            b.setEnabled(False)

    def _ready(self):
        for b in (self.btn_search, self.btn_favs):
            b.setEnabled(True)

    def _fail(self, message):
        self._ready()
        self.heading.setText("")
        self.status(f"Search failed: {message}", error=True)

    def search(self):
        q = self.query.text().strip()
        if len(q) < 2:
            self.status("Type at least two characters to search.")
            return
        kind, limit, label = self.kind.currentData(), self.limit.value(), self.kind.currentText().lower()
        self._busy(f"Searching {label} for “{q}”…")
        run_async(lambda: self.session.search(kind, q, limit),
                  lambda items: self.show(items, kind, f"{label.capitalize()} for “{q}”"),
                  self._fail)

    def favorites(self):
        kind = self.kind.currentData()
        label = self.kind.currentText().lower()
        self._busy(f"Loading your favourite {label}…")
        run_async(lambda: self.session.favorites(kind),
                  lambda items: self.show(items, kind, f"Your favourite {label}"),
                  self._fail)

    def browse_artist(self, artist_id, name):
        self._busy(f"Loading releases by {name}…")

        def done(result):
            artist_name, items = result
            self.show(items, "albums", f"Releases by {artist_name or name}", push=True, artist=artist_name or name)
        run_async(lambda: self.session.artist_albums(artist_id), done, self._fail)

    def show(self, items, kind, heading, push=False, artist=None):
        self._ready()
        if push and self.current:
            self.history.append(self.current)
        elif not push:
            self.history.clear()
        self.current = (items, kind, heading, artist)
        self._render_current()
        if self.session.label:
            self.status(f"Logged in to Qobuz ({self.session.label})")

    def back(self):
        if self.history:
            self.current = self.history.pop()
            self._render_current()

    def refresh_marks(self):
        """Called when the library index changes."""
        if self.current:
            self._render_current(keep_scroll=True)

    def _start_mb(self, artist, items):
        self.mb_check = None
        self.mb_for = artist
        self.mb_label.setText(f"Checking {artist} on MusicBrainz… {musicbrainz.LIMITATION}")

        def done(check, a=artist):
            if self.mb_for != a:
                return
            self.mb_check = check
            if check.artist is None:
                self.mb_label.setText(f"{a} isn't on MusicBrainz, so releases can't be checked — "
                                      "the backup rules are used instead. " + musicbrainz.LIMITATION)
            else:
                self.mb_label.setText(musicbrainz.LIMITATION)
            if self.current and self.current[3] == a:
                self._render_current(keep_scroll=True)

        def fail(msg, a=artist):
            if self.mb_for == a:
                self.mb_label.setText(f"MusicBrainz check failed ({msg}) — releases are shown unverified. "
                                      + musicbrainz.LIMITATION)
        run_async(lambda: musicbrainz.ArtistCheck(artist).prepare(items), done, fail)

    def _kind(self, item):
        """Release type: MusicBrainz's when it confirmed the release, otherwise the guess."""
        if self.mb_check is not None:
            status, kind = self.mb_check.verdict(item)
            if status == musicbrainz.OFFICIAL and kind:
                return kind
        return classify.release_kind(item)

    def _official(self, item, artist):
        """(label, tooltip, colour) for the Official column."""
        reason = musicbrainz.suspicion(item, artist)
        if self.mb_check is not None and self.mb_check.artist is not None:
            status, _ = self.mb_check.verdict(item)
            if status == musicbrainz.OFFICIAL:
                return "Official", "Listed as an official release on MusicBrainz", GREEN
            if status == musicbrainz.BOOTLEG:
                return "Bootleg", "MusicBrainz lists this as a bootleg", RED
            tip = "MusicBrainz doesn't know this release" + (f"; {reason}" if reason else "")
            return "Unverified", tip, AMBER
        if reason:
            return "⚠ Likely unofficial", reason[:1].upper() + reason[1:], AMBER
        return "", "", None

    def _hidden(self, item):
        hidden_ids = set(prefs.get_json(prefs.HIDDEN_RELEASES, []) or [])
        hidden_labels = set(prefs.get_json(prefs.HIDDEN_LABELS, []) or [])
        label = str((item.get("label") or {}).get("name", "")).strip().lower()
        return str(item.get("id")) in hidden_ids or (label and label in hidden_labels)

    def _render_current(self, keep_scroll=False):
        items, kind, heading, artist = self.current
        self.btn_back.setEnabled(bool(self.history))
        self.btn_discog.setVisible(artist is not None)
        self.mb_label.setVisible(artist is not None)
        self.official_only.setEnabled(artist is not None)
        if artist is not None and self.mb_for != artist:
            self._start_mb(artist, items)
        elif artist is None:
            self.mb_for, self.mb_check = None, None
        heading_html = f"<b>{html.escape(heading)}</b>"
        if artist is not None:
            done, total = self._completion(items, artist)
            if total:
                heading_html += (f" &nbsp;·&nbsp; <span style='color:#3fa65a'>{done} of {total}</span> "
                                 f"{'official ' if self._mb_ready() else ''}studio albums in your library")
        self.heading.setText(heading_html)
        for cb in self.type_boxes.values():
            cb.setEnabled(kind == "albums")
        self.prefer_explicit.setEnabled(kind in ("albums", "tracks"))
        scroll = self.table.verticalScrollBar().value()
        self._fill(self._filtered(items, kind), kind, len(items))
        if keep_scroll:
            self.table.verticalScrollBar().setValue(scroll)

    def _mb_ready(self):
        return self.mb_check is not None and self.mb_check.artist is not None

    def _completion(self, items, artist):
        """(owned, total) studio albums for the browsed artist."""
        index = self.index_provider()
        if index is None:
            return 0, 0
        seen, total, owned = set(), 0, 0
        for i in classify.dedupe(items):
            if self._kind(i) != "Studio album":
                continue
            if self._mb_ready() and self.mb_check.verdict(i)[0] != musicbrainz.OFFICIAL:
                continue
            if not self._mb_ready() and musicbrainz.suspicion(i, artist):
                continue
            key = musicbrainz.norm_title(i.get("title"))
            if key in seen:
                continue
            seen.add(key)
            total += 1
            if index.status(i.get("id"), (i.get("artist") or {}).get("name", artist), i.get("title", "")) == "library":
                owned += 1
        return owned, total

    def _filtered(self, items, kind):
        artist = self.current[3] if self.current else None
        if not self.show_hidden.isChecked():
            items = [i for i in items if not self._hidden(i)]
        if kind == "albums":
            items = classify.dedupe(items)
        if kind in ("albums", "tracks") and self.prefer_explicit.isChecked():
            items = classify.prefer_explicit(items)
        if kind == "albums":
            allowed = {k for k, cb in self.type_boxes.items() if cb.isChecked()}
            items = [i for i in items if classify.filter_kind(self._kind(i)) in allowed]
        if kind == "albums" and artist is not None and self.official_only.isChecked():
            if self._mb_ready():
                items = [i for i in items if self.mb_check.verdict(i)[0] == musicbrainz.OFFICIAL]
            elif self.mb_check is not None:   # artist not on MusicBrainz: use the backup rules
                items = [i for i in items if not musicbrainz.suspicion(i, artist)]
        return items

    def _refilter(self):
        prefs.set_value(prefs.PREFER_EXPLICIT, self.prefer_explicit.isChecked())
        prefs.set_value(prefs.MB_OFFICIAL_ONLY, self.official_only.isChecked())
        if self.current:
            self._render_current()

    def _fill(self, items, kind, total):
        index = self.index_provider()
        self.table.setSortingEnabled(False)
        self.table.clearContents()
        self.table.setRowCount(len(items))
        self.rows = []
        for r, item in enumerate(items):
            row = row_for(item, kind)
            row["kind"] = kind
            row["item"] = item
            if kind == "albums":
                row["release"] = self._kind(item)
            self.rows.append(row)
            icon_item = QTableWidgetItem()
            icon_item.setData(Qt.UserRole, len(self.rows) - 1)
            self.table.setItem(r, C_ICON, icon_item)
            have = ""
            if index is not None and kind in ("albums", "tracks"):
                state = index.status(row["album_id"], row.get("have_artist", row["artist"]), row["have_title"])
                have = {"library": "✔", "history": "↓"}.get(state, "")
            official, official_tip, official_colour = ("", "", None)
            if kind == "albums":
                official, official_tip, official_colour = self._official(item, self.current[3] if self.current else None)
            values = {C_TITLE: row["title"], C_ARTIST: row["artist"], C_RELEASE: row["release"],
                      C_OFFICIAL: official, C_EDITION: row["edition"], C_EXPLICIT: row["explicit"],
                      C_YEAR: row["year"], C_QUALITY: row["quality"], C_HAVE: have}
            for c, text in values.items():
                cell = QTableWidgetItem(str(text))
                cell.setToolTip(str(text))
                if c in (C_EXPLICIT, C_HAVE, C_YEAR):
                    cell.setTextAlignment(Qt.AlignCenter)
                if c == C_HAVE and have:
                    cell.setForeground(QBrush(GREEN))
                    cell.setToolTip("In your library" if have == "✔" else "In the download history")
                if c == C_OFFICIAL and official:
                    cell.setToolTip(official_tip)
                    if official_colour is not None:
                        cell.setForeground(QBrush(official_colour))
                if self._hidden(item):
                    cell.setForeground(QBrush(QColor("#8a8a8a")))
                self.table.setItem(r, c, cell)
            if row["cover"]:
                self._cover(row["cover"], icon_item)
        self.table.setSortingEnabled(True)
        hidden = total - len(items)
        self.count_label.setText(f"{len(items)} shown" + (f" · {hidden} hidden by filters" if hidden else ""))

    def _cover(self, url, cell):
        if url in self.covers:
            cell.setIcon(self.covers[url])
            return
        waiting = self.pending.get(url)
        if waiting is not None:
            waiting.append(cell)
            return
        self.pending[url] = [cell]
        reply = self.net.get(QNetworkRequest(QUrl(url)))
        reply.finished.connect(lambda r=reply, u=url: self._cover_loaded(r, u))

    def _cover_loaded(self, reply, url):
        cells = self.pending.pop(url, [])
        pix = QPixmap()
        if pix.loadFromData(bytes(reply.readAll())):
            icon = QIcon(pix.scaled(THUMB, THUMB, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation))
            self.covers[url] = icon
            for cell in cells:
                try:
                    cell.setIcon(icon)
                except RuntimeError:
                    pass  # the row was replaced by a newer search
        reply.deleteLater()

    # ---- details panel -------------------------------------------------------
    @staticmethod
    def _empty_details():
        return "<p style='color:gray'>Select an album or track to see its details.</p>"

    def _row(self, table_row):
        item = self.table.item(table_row, C_ICON)
        return self.rows[item.data(Qt.UserRole)] if item is not None else None

    def _load_details(self):
        rows = self._selected_rows()
        if len(rows) != 1:
            self.details.setHtml(self._empty_details() if not rows
                                 else f"<p><b>{len(rows)} selected</b></p>")
            self.btn_detail_dl.setEnabled(False)
            self._details_for = None
            return
        row = self._row(rows[0])
        if not row:
            return
        if row["kind"] == "artists":
            self.details.setHtml(f"<h3>{html.escape(row['title'])}</h3><p>{html.escape(row['release'])}</p>"
                                 "<p style='color:gray'>Double-click to browse the releases.</p>")
            self.btn_detail_dl.setEnabled(False)
            return
        if row["kind"] == "playlists":
            self.details.setHtml(f"<h3>{html.escape(row['title'])}</h3><p>{html.escape(row['artist'])}</p>"
                                 f"<p>{html.escape(row['release'])}</p>")
            self.btn_detail_dl.setEnabled(False)
            return
        album_id = row.get("album_id")
        if not album_id:
            return
        self._details_for = album_id
        self.btn_detail_dl.setEnabled(True)
        if album_id in self.album_cache:
            self._render_details(self.album_cache[album_id])
            return
        self.details.setHtml(f"<h3>{html.escape(row.get('album_title') or row['title'])}</h3>"
                             "<p style='color:gray'>Loading…</p>")

        def done(meta, aid=album_id):
            self.album_cache[aid] = meta
            if self._details_for == aid:
                self._render_details(meta)

        def fail(msg, aid=album_id):
            if self._details_for == aid:
                self.details.setHtml(f"<p style='color:#e5534b'>Could not load the album: {html.escape(msg)}</p>")
        run_async(lambda: self.session.album(album_id), done, fail)

    def _render_details(self, a):
        e = html.escape
        title = a.get("title", "")
        if a.get("version"):
            title += f" ({a['version']})"
        artist = (a.get("artist") or {}).get("name", "")
        cover = (a.get("image") or {}).get("large") or (a.get("image") or {}).get("small") or ""
        facts = [
            ("Release", self._kind(a)),
            ("Explicit", "Yes" if classify.is_explicit(a) else "No (clean)"),
            ("Released", a.get("release_date_original") or ""),
            ("Label", (a.get("label") or {}).get("name", "")),
            ("Genre", (a.get("genre") or {}).get("name", "")),
            ("Quality", _quality(a)),
            ("Length", f"{a.get('tracks_count', '?')} tracks · {_duration(a.get('duration'))}"),
        ]
        rows = "".join(f"<tr><td style='color:gray;padding-right:8px'>{e(k)}</td><td>{e(str(v))}</td></tr>"
                       for k, v in facts if v)
        tracks = (a.get("tracks") or {}).get("items", [])
        multi_disc = len({t.get("media_number", 1) for t in tracks}) > 1
        lines = []
        for t in tracks:
            num = f"{t.get('media_number', 1)}.{t.get('track_number', '')}" if multi_disc else str(t.get("track_number", ""))
            name = t.get("title", "")
            if t.get("version"):
                name += f" ({t['version']})"
            mark = " <b>[E]</b>" if t.get("parental_warning") else ""
            lines.append(f"<tr><td style='color:gray;padding-right:6px' align='right'>{e(num)}</td>"
                         f"<td>{e(name)}{mark}</td><td style='color:gray' align='right'>{_duration(t.get('duration'))}</td></tr>")
        img = f"<img src='{e(cover)}' width='260'><br>" if cover else ""
        self.details.document().setDefaultStyleSheet("td { padding: 1px 2px; }")
        self.details.setHtml(
            f"{img}<h3 style='margin-bottom:0'>{e(title)}</h3><p style='margin-top:2px'>{e(artist)}</p>"
            f"<table>{rows}</table><h4>Tracks</h4><table width='100%'>{''.join(lines)}</table>")
        if cover:
            self._load_detail_image(cover)

    def _load_detail_image(self, url):
        from PySide6.QtGui import QTextDocument
        doc = self.details.document()
        if url in self.covers and isinstance(self.covers[url], QPixmap):
            doc.addResource(QTextDocument.ImageResource, QUrl(url), self.covers[url])
            self.details.setHtml(self.details.toHtml())
            return
        reply = self.net.get(QNetworkRequest(QUrl(url)))

        def loaded(r=reply, u=url):
            pix = QPixmap()
            if pix.loadFromData(bytes(r.readAll())):
                pix = pix.scaledToWidth(260, Qt.SmoothTransformation)
                self.covers[u] = pix
                self.details.document().addResource(QTextDocument.ImageResource, QUrl(u), pix)
                self.details.setHtml(self.details.toHtml())
            r.deleteLater()
        reply.finished.connect(loaded)

    def _download_detail_album(self):
        rows = self._selected_rows()
        if len(rows) != 1:
            return
        row = self._row(rows[0])
        if row and row.get("album_id"):
            name = row.get("album_title") or row["title"]
            artist = row.get("have_artist", row["artist"])
            self._queue([dict(src=f"{WEB_URL}album/{row['album_id']}", title=f"{artist} — {name}",
                              artist=artist, album=name, album_id=row["album_id"])])

    # ---- actions -------------------------------------------------------------
    def _selected_rows(self):
        return sorted({i.row() for i in self.table.selectedIndexes()})

    @staticmethod
    def _entry(row, title=None):
        if title is None:
            title = f"{row['artist']} — {row['title']}" if row["artist"] else row["title"]
        album = row.get("have_title", "") if row["kind"] in ("albums", "tracks") else ""
        return dict(src=row["url"], title=title, artist=row.get("have_artist", row["artist"]),
                    album=album, album_id=row.get("album_id"))

    def _queue(self, entries):
        entries = self.download_tab.filter_owned(entries)
        if entries is None:
            return 0
        added = 0
        for e in entries:
            added += len(self.download_tab.enqueue([e["src"]], title=e["title"]))
        if added:
            self.status(f"Added {added} item{'s' if added != 1 else ''} to the queue.")
        return added

    def download_selected(self):
        rows = [self._row(r) for r in self._selected_rows()]
        rows = [r for r in rows if r]
        if not rows:
            self.status("Select one or more rows first.")
            return
        self._queue([self._entry(r) for r in rows])

    def download_discography(self):
        if not self.current or self.current[3] is None:
            return
        items, kind, _heading, artist = self.current
        shown = self._filtered(items, kind)
        if not shown:
            self.status("Nothing to download with the current filters.")
            return
        from PySide6.QtWidgets import QMessageBox
        counts = {}
        for i in shown:
            k = classify.release_kind(i)
            counts[k] = counts.get(k, 0) + 1
        names = {"Studio album": ("studio album", "studio albums"), "Live album": ("live album", "live albums"),
                 "Compilation": ("compilation", "compilations"), "EP": ("EP", "EPs"), "Single": ("single", "singles")}
        summary = ", ".join(f"{n} {names[k][n != 1]}" for k, n in counts.items())
        if QMessageBox.question(self, "Download discography",
                                f"Queue {len(shown)} releases by {artist}?\n\n{summary}") != QMessageBox.Yes:
            return
        entries = []
        for i in shown:
            r = row_for(i, "albums")
            r["kind"] = "albums"
            title = r["title"] + (f" ({r['edition']})" if r["edition"] else "")
            entries.append(self._entry(r, f"{artist} — {title}"))
        self._queue(entries)

    def open_selected(self):
        for r in self._selected_rows()[:10]:
            row = self._row(r)
            if row:
                QDesktopServices.openUrl(QUrl(row["url"]))

    def copy_selected(self):
        from PySide6.QtWidgets import QApplication
        urls = [self._row(r)["url"] for r in self._selected_rows() if self._row(r)]
        if urls:
            QApplication.clipboard().setText("\n".join(urls))
            self.status(f"Copied {len(urls)} link{'s' if len(urls) != 1 else ''}.")

    def _double_clicked(self, table_row, _col):
        row = self._row(table_row)
        if not row:
            return
        if row["browse_artist"]:
            self.browse_artist(*row["browse_artist"])
        else:
            self.download_selected()

    def _menu(self, pos):
        table_row = self.table.rowAt(pos.y())
        if table_row < 0:
            return
        row = self._row(table_row)
        menu = QMenu(self)
        menu.addAction("Download", self.download_selected)
        if row and row["browse_artist"]:
            menu.addAction("Browse releases", lambda: self.browse_artist(*row["browse_artist"]))
        if row and row["kind"] == "albums" and row["artist"]:
            artist_id = None
            for item in (self.current[0] if self.current else []):
                if item.get("id") == row["id"]:
                    artist_id = (item.get("artist") or {}).get("id")
                    break
            if artist_id:
                menu.addAction(f"All releases by {row['artist']}",
                               lambda: self.browse_artist(artist_id, row["artist"]))
        if row and row["kind"] == "albums":
            item = row["item"]
            label = str((item.get("label") or {}).get("name", "")).strip()
            menu.addSeparator()
            if self._hidden(item):
                menu.addAction("Unhide", lambda: self._unhide(item))
            else:
                menu.addAction("Hide this release", lambda: self._hide_release(item))
                if label:
                    menu.addAction(f"Hide everything from the label “{label}”", lambda: self._hide_label(label))
        menu.addSeparator()
        menu.addAction("Open on Qobuz", self.open_selected)
        menu.addAction("Copy link", self.copy_selected)
        menu.exec(self.table.viewport().mapToGlobal(pos))

    def _hide_release(self, item):
        ids = list(prefs.get_json(prefs.HIDDEN_RELEASES, []) or [])
        ids.append(str(item.get("id")))
        prefs.set_json(prefs.HIDDEN_RELEASES, sorted(set(ids)))
        self.status("Hidden. Tick “Show hidden” to see it again.")
        self._refilter()

    def _hide_label(self, label):
        labels = list(prefs.get_json(prefs.HIDDEN_LABELS, []) or [])
        labels.append(label.lower())
        prefs.set_json(prefs.HIDDEN_LABELS, sorted(set(labels)))
        self.status(f"Releases from {label} are now hidden everywhere. Tick “Show hidden” to see them.")
        self._refilter()

    def _unhide(self, item):
        ids = set(prefs.get_json(prefs.HIDDEN_RELEASES, []) or [])
        ids.discard(str(item.get("id")))
        prefs.set_json(prefs.HIDDEN_RELEASES, sorted(ids))
        label = str((item.get("label") or {}).get("name", "")).strip().lower()
        labels = set(prefs.get_json(prefs.HIDDEN_LABELS, []) or [])
        if label in labels:
            labels.discard(label)
            prefs.set_json(prefs.HIDDEN_LABELS, sorted(labels))
        self._refilter()
