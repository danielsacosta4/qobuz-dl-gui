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

from . import cache, classify, matching, musicbrainz, prefs, studio, theme
from .client import run_async
from .search_common import COVER_TTL, THUMB, WEB_URL, _image, _quality
from .search_details import DetailsMixin
from .search_studio import StudioListMixin

KINDS = [("albums", "Albums"), ("tracks", "Tracks"), ("artists", "Artists"), ("playlists", "Playlists")]
COLUMNS = ["", "Title", "Artist", "Release", "Official", "Edition", "Explicit", "Year", "Quality", "Have"]
(C_ICON, C_TITLE, C_ARTIST, C_RELEASE, C_OFFICIAL, C_EDITION, C_EXPLICIT, C_YEAR, C_QUALITY,
 C_HAVE) = range(len(COLUMNS))


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


class SearchTab(StudioListMixin, DetailsMixin, QWidget):
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
        self.wikis = {}               # norm artist -> {"found": {...} | None, "keys": [...], "error": str}
        self._wiki_kind = {}          # (norm artist, album id) -> True when the album is on Wikipedia's list
        self._wiki_batch = None       # the WikiBatch thread that is loading lists right now
        self._wiki_pending = set()
        self._box_for = None          # the result list the Studio box was last set up for
        self._forced_details = None   # an album whose details you asked for in safe mode
        self._choice = studio.Choice()
        self._hidden_sets = None      # (release ids, labels), read once from prefs
        self._completion_cache = (None, (0, 0))
        self._details_html = ""

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
        self.studio_box = QComboBox()
        self.studio_box.addItem("Studio albums: Qobuz", studio.QOBUZ)
        self.studio_box.addItem("Studio albums: Wikipedia", studio.WIKI)
        self.studio_box.setToolTip("Which list decides what counts as a studio album for this artist — the one "
                                   "with the most results (remembered per artist)")
        self.studio_box.setVisible(False)
        self.btn_compare = QPushButton("Compare…")
        self.btn_compare.setToolTip("Qobuz's and Wikipedia's studio albums side by side")
        self.btn_compare.setVisible(False)
        head = QHBoxLayout()
        head.addWidget(self.btn_back)
        head.addWidget(self.heading, 1)
        head.addWidget(self.studio_box)
        head.addWidget(self.btn_compare)
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
        self.btn_detail_load = QPushButton("Load details (1 Qobuz request)")
        self.btn_detail_load.setVisible(False)
        det_box = QVBoxLayout()
        det_box.setContentsMargins(0, 0, 0, 0)
        det_box.addWidget(self.details, 1)
        det_box.addWidget(self.btn_detail_load)
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
        self._detail_timer.setInterval(500)     # a pause before asking Qobuz about the selected album
        self._detail_timer.timeout.connect(self._load_details)

        self.query.returnPressed.connect(self.search)
        self.btn_search.clicked.connect(self.search)
        self.btn_favs.clicked.connect(self.favorites)
        self.btn_queue.clicked.connect(self.download_selected)
        self.btn_open.clicked.connect(self.open_selected)
        self.btn_copy.clicked.connect(self.copy_selected)
        self.btn_back.clicked.connect(self.back)
        self.btn_discog.clicked.connect(self.download_discography)
        self.studio_box.activated.connect(self._studio_chosen)
        self.btn_compare.clicked.connect(self._compare)
        self.btn_detail_dl.clicked.connect(self._download_detail_album)
        self.btn_detail_load.clicked.connect(self._load_details_now)
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

    def search_artist(self, name):
        """Used by Discover: one Qobuz artist search for this name, started by your click."""
        self.kind.setCurrentIndex(self.kind.findData("artists"))
        self.query.setText(name)
        self.search()

    def browse_artist(self, artist_id, name):
        self._busy(f"Loading releases by {name}…")

        def done(result):
            artist_name, items = result
            self.show(items, "albums", f"Releases by {artist_name or name}", push=True, artist=artist_name or name)
        run_async(lambda: self.session.artist_albums(artist_id), done, self._fail)

    def show(self, items, kind, heading, push=False, artist=None):
        self._ready()
        self._forget_wiki_errors()
        if self._wiki_batch is not None:
            self._wiki_batch.stop()               # a new list: stop loading for the old one
            self._wiki_batch, self._wiki_pending = None, set()
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

    def _kind_qobuz(self, item):
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
                return "Official", "Listed as an official release on MusicBrainz", QColor(theme.OK)
            if status == musicbrainz.BOOTLEG:
                return "Bootleg", "MusicBrainz lists this as a bootleg", QColor(theme.ERROR)
            tip = "MusicBrainz doesn't know this release" + (f"; {reason}" if reason else "")
            return "Unverified", tip, QColor(theme.WARN)
        if reason:
            return "⚠ Likely unofficial", reason[:1].upper() + reason[1:], QColor(theme.WARN)
        return "", "", None

    def _hidden(self, item):
        if self._hidden_sets is None:
            self._hidden_sets = (set(prefs.get_json(prefs.HIDDEN_RELEASES, []) or []),
                                 set(prefs.get_json(prefs.HIDDEN_LABELS, []) or []))
        hidden_ids, hidden_labels = self._hidden_sets
        label = str((item.get("label") or {}).get("name", "")).strip().lower()
        return str(item.get("id")) in hidden_ids or (label and label in hidden_labels)

    def _render_current(self, keep_scroll=False):
        items, kind, heading, artist = self.current
        self.btn_back.setEnabled(bool(self.history))
        self.btn_discog.setVisible(artist is not None)
        self._choice = studio.Choice.load()
        main = self._list_artist()                   # the artist the Studio box is about (None: not albums)
        self.studio_box.setVisible(main is not None)
        self.btn_compare.setVisible(main is not None)
        if main is not None:
            self.studio_box.blockSignals(True)
            self.studio_box.setCurrentIndex(self.studio_box.findData(self._choice.source_for(main)))
            self.studio_box.blockSignals(False)
            self.studio_box.setToolTip(f"Which list decides what counts as a studio album for {main} "
                                       "(remembered for this artist)")
        self._ensure_wikis()
        self.mb_label.setVisible(artist is not None)
        self.official_only.setEnabled(artist is not None)
        if artist is not None and self.mb_for != artist:
            self._start_mb(artist, items)
        elif artist is None:
            self.mb_for, self.mb_check = None, None
        heading_html = f"<b>{html.escape(heading)}</b>"
        counted = artist if artist is not None else (main if main is not None and self._wiki_entry(main) else None)
        if counted is not None:
            done, total = self._completion(items, counted)
            wiki_on = self._wiki_entry(counted) is not None
            if total:
                heading_html += (f" &nbsp;·&nbsp; <span style='color:{theme.OK}'>{done} of {total}</span> "
                                 f"{'official ' if self._mb_ready() and not wiki_on else ''}"
                                 f"studio albums in your library"
                                 + (" (Wikipedia's list)" if wiki_on else ""))
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
        """(owned, total) studio albums for the browsed artist (cached until something it uses changes)."""
        index = self.index_provider()
        if index is None:
            return 0, 0
        key = (id(items), artist, id(self.mb_check), id(index), len(index.artists),
               id(self.wikis.get(matching.norm_artist(artist))), self._choice.source_for(artist))
        if self._completion_cache[0] == key:
            return self._completion_cache[1]
        result = self._count_completion(items, artist, index)
        self._completion_cache = (key, result)
        return result

    def _count_completion(self, items, artist, index):
        entry = self._wiki_entry(artist)
        if entry is not None:
            albums = entry["found"]["albums"]
            return sum(1 for w in albums if index.status(None, artist, w["title"]) == "library"), len(albums)
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
        if kind == "albums" and artist is not None and self.official_only.isChecked() \
                and not self._wiki_entry(artist):
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
                    cell.setForeground(QBrush(QColor(theme.OK)))
                    cell.setToolTip("In your library" if have == "✔" else "In the download history")
                if c == C_OFFICIAL and official:
                    cell.setToolTip(official_tip)
                    if official_colour is not None:
                        cell.setForeground(QBrush(official_colour))
                if self._hidden(item):
                    cell.setForeground(QBrush(QColor(theme.MUTED)))
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
        data = cache.read_file("covers", url, COVER_TTL)
        if data:
            pix = QPixmap()
            if pix.loadFromData(data):
                icon = QIcon(pix.scaled(THUMB, THUMB, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation))
                self.covers[url] = icon
                cell.setIcon(icon)
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
        data = bytes(reply.readAll())
        if pix.loadFromData(data):
            cache.write_file("covers", url, data)
            icon = QIcon(pix.scaled(THUMB, THUMB, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation))
            self.covers[url] = icon
            for cell in cells:
                try:
                    cell.setIcon(icon)
                except RuntimeError:
                    pass  # the row was replaced by a newer search
        reply.deleteLater()

    def _row(self, table_row):
        item = self.table.item(table_row, C_ICON)
        return self.rows[item.data(Qt.UserRole)] if item is not None else None

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
        self._hidden_sets = None
        self.status("Hidden. Tick “Show hidden” to see it again.")
        self._refilter()

    def _hide_label(self, label):
        labels = list(prefs.get_json(prefs.HIDDEN_LABELS, []) or [])
        labels.append(label.lower())
        prefs.set_json(prefs.HIDDEN_LABELS, sorted(set(labels)))
        self._hidden_sets = None
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
        self._hidden_sets = None
        self._refilter()

    def hide_selected(self):
        """Delete key: hides the selected releases, or shows them again when they're all hidden."""
        rows = [r for r in (self._row(t) for t in self._selected_rows()) if r and r["kind"] == "albums"]
        if not rows:
            return
        items = [r["item"] for r in rows]
        if all(self._hidden(i) for i in items):
            for i in items:
                self._unhide(i)
            self.status(f"Showing {len(items)} release{'s' if len(items) != 1 else ''} again.")
            return
        ids = set(prefs.get_json(prefs.HIDDEN_RELEASES, []) or [])
        ids |= {str(i.get("id")) for i in items}
        prefs.set_json(prefs.HIDDEN_RELEASES, sorted(ids))
        self._hidden_sets = None
        self.status(f"Hid {len(items)} release{'s' if len(items) != 1 else ''}. Tick “Show hidden” to see "
                    "them, then Delete again to bring them back.")
        self._refilter()

    def focus_search(self):
        self.query.setFocus()
        self.query.selectAll()

    def rerun(self):
        """F5: run the search in the box again."""
        if self.query.text().strip():
            self.search()
