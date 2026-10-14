"""Download tab: URLs or a .txt list in, a qobuz-dl `dl` job out."""

import os
import re

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QFormLayout, QGridLayout, QGroupBox, QHBoxLayout,
    QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

from . import config, prefs
from .jobs import Job

URL_RE = re.compile(r"https?://\S+")
ALBUM_ID_RE = re.compile(r"/album/(?:[^/?#]+/)*([0-9A-Za-z]+)(?:[/?#]|$)")
RELEASE_TYPES = [("album", "Albums"), ("ep", "EPs"), ("single", "Singles"),
                 ("live", "Live"), ("compilation", "Compilations")]


def quality_combo():
    combo = QComboBox()
    for qid, label in config.QUALITIES:
        combo.addItem(label, qid)
    return combo


def set_quality(combo, value):
    try:
        idx = combo.findData(int(value))
    except (TypeError, ValueError):
        idx = -1
    combo.setCurrentIndex(idx if idx >= 0 else combo.count() - 1)


class FolderPicker(QWidget):
    changed = Signal(str)

    def __init__(self, placeholder="", file_mode=False, file_filter="", parent=None):
        super().__init__(parent)
        self.file_mode = file_mode
        self.file_filter = file_filter
        self.edit = QLineEdit()
        self.edit.setPlaceholderText(placeholder)
        btn = QPushButton("Browse…")
        btn.clicked.connect(self._browse)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.edit, 1)
        lay.addWidget(btn)
        self.edit.textChanged.connect(self.changed)

    def _browse(self):
        start = os.path.expanduser(self.edit.text() or "~")
        if self.file_mode:
            path, _ = QFileDialog.getOpenFileName(self, "Choose file", start, self.file_filter)
        else:
            path = QFileDialog.getExistingDirectory(self, "Choose folder", start)
        if path:
            self.edit.setText(path)

    def text(self):
        return self.edit.text().strip()

    def setText(self, value):
        self.edit.setText(value)


class DownloadTab(QWidget):
    def __init__(self, runner, parent=None):
        super().__init__(parent)
        self.runner = runner
        self.index_provider = lambda: None   # set by the main window
        self.session = None

        intro = QLabel("Paste Qobuz links (albums, tracks, artists, labels, playlists) or Last.fm "
                       "playlist links, one per line.")
        intro.setWordWrap(True)
        self.urls = QPlainTextEdit()
        self.urls.setPlaceholderText("https://play.qobuz.com/album/…\nhttps://open.qobuz.com/playlist/…")
        self.urls.setAcceptDrops(True)

        self.btn_txt = QPushButton("Add a .txt list…")
        self.btn_txt.setToolTip("A text file with one link per line. qobuz-dl marks finished links "
                                "with [DONE] in the file, so an interrupted list resumes where it stopped.")
        self.btn_paste = QPushButton("Paste")
        self.btn_go = QPushButton("Download")
        self.btn_go.setDefault(True)
        self.btn_go.setMinimumWidth(140)

        url_buttons = QHBoxLayout()
        url_buttons.addWidget(self.btn_paste)
        url_buttons.addWidget(self.btn_txt)
        url_buttons.addStretch(1)
        url_buttons.addWidget(self.btn_go)

        # Where and how
        self.quality = quality_combo()
        self.folder = FolderPicker("Download folder")
        basics = QFormLayout()
        basics.addRow("Quality", self.quality)
        basics.addRow("Save to", self.folder)

        # Options for this download (override config.ini for this run only)
        self.albums_only = QCheckBox("Skip singles, EPs and VA releases")
        self.smart = QCheckBox("Smart discography (filter duplicates, deluxe, live)")
        self.booklet = QCheckBox("Booklet, cover and PDFs only (no audio)")
        self.no_lyrics = QCheckBox("No lyrics")
        self.no_m3u = QCheckBox("No .m3u for playlists")
        self.multi_tags = QCheckBox("Multi-value tags")
        self.pl_albums = QCheckBox("Playlists as album folders")
        self.no_db = QCheckBox("Ignore download history")
        self.no_db.setToolTip("Download again even if the history says it was already downloaded")
        self.with_credits = QCheckBox("Force booklet .txt (credits)")

        self.delay = QSpinBox()
        self.delay.setRange(0, 120)
        self.delay.setSuffix(" s")
        self.delay.setToolTip("Wait between tracks and download one at a time (safer for big discographies)")
        self.workers = QSpinBox()
        self.workers.setRange(0, 16)
        self.workers.setSpecialValueText("from settings")
        self.workers.setToolTip("Parallel track downloads")
        self.blacklist = FolderPicker("Blacklist file (optional)", file_mode=True,
                                      file_filter="Text files (*.txt);;All files (*)")
        self.label_filter = QLineEdit()
        self.label_filter.setPlaceholderText("Only releases from this label (name or label URL)")

        opts = QGridLayout()
        boxes = [self.albums_only, self.smart, self.booklet, self.no_lyrics, self.no_m3u,
                 self.multi_tags, self.pl_albums, self.no_db, self.with_credits]
        for i, box in enumerate(boxes):
            opts.addWidget(box, i // 3, i % 3)
        row = (len(boxes) + 2) // 3
        small = QFormLayout()
        small.addRow("Delay between tracks", self.delay)
        small.addRow("Parallel downloads", self.workers)
        small.addRow("Blacklist", self.blacklist)
        small.addRow("Label filter", self.label_filter)
        opts.addLayout(small, row, 0, 1, 3)
        opt_box = QGroupBox("Options for this download")
        opt_box.setLayout(opts)

        # Artist discographies
        self.types = {}
        types_row = QHBoxLayout()
        for key, label in RELEASE_TYPES:
            cb = QCheckBox(label)
            cb.setChecked(True)
            self.types[key] = cb
            types_row.addWidget(cb)
        types_row.addStretch(1)
        types_box = QGroupBox("Artist links: release types to download")
        types_box.setLayout(types_row)

        lay = QVBoxLayout(self)
        lay.addWidget(intro)
        lay.addWidget(self.urls, 1)
        lay.addLayout(url_buttons)
        lay.addLayout(basics)
        lay.addWidget(opt_box)
        lay.addWidget(types_box)

        self.btn_go.clicked.connect(self._download)
        self.btn_txt.clicked.connect(self._add_txt)
        self.btn_paste.clicked.connect(self._paste)

        self.reload_settings()
        self._restore_options()
        for box in boxes + list(self.types.values()):
            box.toggled.connect(self._save_options)
        for spin in (self.delay, self.workers):
            spin.valueChanged.connect(self._save_options)
        self.blacklist.changed.connect(self._save_options)
        self.label_filter.textChanged.connect(self._save_options)

    def _option_boxes(self):
        return {"albums_only": self.albums_only, "smart": self.smart, "booklet": self.booklet,
                "no_lyrics": self.no_lyrics, "no_m3u": self.no_m3u, "multi_tags": self.multi_tags,
                "pl_albums": self.pl_albums, "no_db": self.no_db, "with_credits": self.with_credits}

    def _save_options(self, *_):
        prefs.set_json(prefs.DOWNLOAD_OPTIONS, {
            "boxes": {k: cb.isChecked() for k, cb in self._option_boxes().items()},
            "types": {k: cb.isChecked() for k, cb in self.types.items()},
            "delay": self.delay.value(), "workers": self.workers.value(),
            "blacklist": self.blacklist.text(), "label": self.label_filter.text(),
        })

    def _restore_options(self):
        saved = prefs.get_json(prefs.DOWNLOAD_OPTIONS, {}) or {}
        for k, cb in self._option_boxes().items():
            cb.setChecked(bool((saved.get("boxes") or {}).get(k, False)))
        for k, cb in self.types.items():
            cb.setChecked(bool((saved.get("types") or {}).get(k, True)))
        self.delay.setValue(int(saved.get("delay", 0) or 0))
        self.workers.setValue(int(saved.get("workers", 0) or 0))
        self.blacklist.setText(saved.get("blacklist", "") or "")
        self.label_filter.setText(saved.get("label", "") or "")

    def reload_settings(self):
        cfg = config.load()
        set_quality(self.quality, config.get(cfg, "default_quality", "27"))
        self.folder.setText(config.get(cfg, "directory") or config.get(cfg, "default_folder") or "")

    # ---- actions -------------------------------------------------------------
    def _paste(self):
        from PySide6.QtWidgets import QApplication
        text = QApplication.clipboard().text().strip()
        if text:
            current = self.urls.toPlainText().rstrip()
            self.urls.setPlainText((current + "\n" if current else "") + text)

    def _add_txt(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose a list of links", os.path.expanduser("~"),
                                              "Text files (*.txt);;All files (*)")
        if path:
            current = self.urls.toPlainText().rstrip()
            self.urls.setPlainText((current + "\n" if current else "") + path)

    def sources(self):
        out = []
        for raw in self.urls.toPlainText().splitlines():
            line = raw.strip()
            if not line:
                continue
            if os.path.isfile(os.path.expanduser(line)):
                out.append(os.path.expanduser(line))
            else:
                out.extend(URL_RE.findall(line))
        return out

    def _download(self):
        sources = self.sources()
        if not sources:
            QMessageBox.information(self, "Nothing to download", "Paste at least one Qobuz link or add a .txt list.")
            return
        album_ids = {src: m.group(1) for src in sources
                     for m in [ALBUM_ID_RE.search(src)] if m}
        if not album_ids or self.index_provider() is None or self.session is None:
            if self.enqueue(sources):
                self.urls.clear()
            return

        # Look the albums up first so the ones already in the library can be pointed out.
        from .client import run_async
        self.btn_go.setEnabled(False)
        self.btn_go.setText("Checking library…")

        def lookup():
            found = {}
            for src, aid in album_ids.items():
                try:
                    meta = self.session.album(aid)
                except Exception:
                    continue
                found[src] = ((meta.get("artist") or {}).get("name", ""), meta.get("title", ""), aid)
            return found

        def done(found):
            self._reset_go()
            entries = []
            for src in sources:
                artist, album, aid = found.get(src, ("", "", None))
                title = f"{artist} — {album}" if artist else None
                entries.append(dict(src=src, title=title, artist=artist, album=album, album_id=aid))
            entries = self.filter_owned(entries)
            if entries is None:
                return
            added = sum(len(self.enqueue([e["src"]], title=e["title"])) for e in entries)
            if added or not entries:
                self.urls.clear()

        def fail(_msg):
            self._reset_go()
            if self.enqueue(sources):
                self.urls.clear()
        run_async(lookup, done, fail)

    def _reset_go(self):
        self.btn_go.setEnabled(True)
        self.btn_go.setText("Download")

    def filter_owned(self, entries):
        """Warns about albums already in the library.

        entries: dicts with src, title, artist, album, album_id. Returns the entries to queue,
        or None when the person cancels.
        """
        index = self.index_provider()
        if index is None:
            return entries
        owned = [e for e in entries
                 if e.get("album") and index.status(e.get("album_id"), e.get("artist", ""), e["album"]) == "library"]
        if not owned:
            return entries
        names = "\n".join(f"• {e['artist']} — {e['album']}" for e in owned[:10])
        more = f"\n… and {len(owned) - 10} more" if len(owned) > 10 else ""
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Question)
        box.setWindowTitle("Already in your library")
        one = len(owned) == 1
        box.setText(f"{'This album is' if one else f'{len(owned)} of these albums are'} already in your library:"
                    f"\n\n{names}{more}")
        box.setInformativeText("Download anyway if you want a better quality or another edition — the Library "
                               "tab will then offer to replace the old copy.")
        anyway = box.addButton("Download anyway", QMessageBox.AcceptRole)
        skip = None
        if len(owned) < len(entries):
            skip = box.addButton("Skip those", QMessageBox.ActionRole)
        box.addButton(QMessageBox.Cancel)
        box.setDefaultButton(skip or anyway)
        box.exec()
        clicked = box.clickedButton()
        if clicked == anyway:
            return entries
        if skip is not None and clicked == skip:
            owned_ids = {id(e) for e in owned}
            return [e for e in entries if id(e) not in owned_ids]
        return None

    def build_args(self):
        args = ["-q", str(self.quality.currentData())]
        folder = self.folder.text()
        if folder:
            args += ["-d", os.path.expanduser(folder)]
        flags = [
            (self.albums_only, "--albums-only"), (self.smart, "--smart-discography"),
            (self.booklet, "--booklet-only"), (self.no_lyrics, "--no-lyrics"),
            (self.no_m3u, "--no-m3u"), (self.multi_tags, "--multi-tags"),
            (self.pl_albums, "--playlist-as-albums"), (self.no_db, "--no-db"),
            (self.with_credits, "--with-credits"),
        ]
        args += [flag for box, flag in flags if box.isChecked()]
        if self.delay.value():
            args += ["--delay", str(self.delay.value())]
        if self.workers.value():
            args += ["--max-workers", str(self.workers.value())]
        if self.blacklist.text():
            args += ["-b", os.path.expanduser(self.blacklist.text())]
        if self.label_filter.text().strip():
            args += ["--filter-label", self.label_filter.text().strip()]
        return args

    def release_type_env(self, sources):
        chosen = [k for k, cb in self.types.items() if cb.isChecked()]
        if len(chosen) == len(self.types) or not any("/artist/" in s for s in sources):
            return {}
        return {"QDLGUI_RELEASE_TYPES": ",".join(chosen)}

    def enqueue(self, sources, title=None):
        """Queues one `dl` job per source so each shows up (and can fail) on its own."""
        if any("/artist/" in s for s in sources) and not any(cb.isChecked() for cb in self.types.values()):
            QMessageBox.information(self, "No release types", "Tick at least one release type for artist links.")
            return []
        args = self.build_args()
        jobs = []
        for src in sources:
            name = title if (title and len(sources) == 1) else describe(src)
            jobs.append(self.runner.enqueue(Job(name, ["dl", src, *args], env=self.release_type_env([src]))))
        return jobs


def describe(source):
    if os.path.isfile(source):
        return f"List: {os.path.basename(source)}"
    m = re.search(r"/(album|track|artist|playlist|label|interpreter|user)/(?:[^/]+/)*([^/?#]+)", source)
    if "last.fm" in source:
        return "Last.fm playlist"
    if m:
        return f"{m.group(1).title()} {m.group(2)}"
    return source
