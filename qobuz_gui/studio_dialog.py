"""Side-by-side view of an artist's studio albums on Qobuz and on Wikipedia, with what you own."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QMessageBox, QPushButton,
    QRadioButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from . import studio, theme, wikipedia

COLS = ["", "Album", "Year", "Qobuz", "Wikipedia", "You own"]


class StudioCompare(QDialog):
    sourceChosen = Signal(str)          # "qobuz" or "wikipedia"
    downloadRequested = Signal(list)    # [(title, url)] of the ticked albums
    openArtistPage = Signal()           # partial list: open the artist's full page in Search
    loadQobuz = Signal()                # ask Qobuz for this artist's catalogue (two or more requests)
    overrideRequested = Signal(dict)    # a correction to Wikipedia's list (see studio.edited_override)

    def __init__(self, parent, artist, rows, source, page="", partial=False, qobuz_loaded=True, can_load=True,
                 override=None, removed=()):
        super().__init__(parent)
        self.setWindowTitle(f"Studio albums — {artist}")
        self.resize(820, 560)
        self.artist, self.rows, self.source = artist, rows, source
        self.override, self.removed = dict(override or {}), list(removed)
        head = QLabel(f"<b>{artist}</b> — the two lists differ where a row has a ✘. "
                      "Pick the one that matches what you consider this artist's studio albums; the choice is "
                      "remembered for this artist." + (f"<br>Wikipedia page: {page}" if page else
                                                       "<br>Wikipedia has no usable list of studio albums for this "
                                                       "artist. Add albums yourself or choose another page below.")
                      + ("<br><i>You have corrected this list (marked “yours” below)."
                         + (f" Removed by you: {', '.join(self.removed)}." if self.removed else "") + "</i>"
                         if self.override else "")
                      + ("<br><i>From your search results, so releases the search didn't return are missing "
                         "from the Qobuz side.</i>" if partial else "")
                      + ("<br><i>No Qobuz data for this artist has been loaded (nothing was requested), so the Qobuz "
                         "column is empty." + (" Use “Load Qobuz data” if you want it." if can_load else
                                                " Safe mode keeps it that way.") + "</i>" if not qobuz_loaded else ""))
        head.setWordWrap(True)
        self.r_qobuz = QRadioButton("Count from Qobuz")
        self.r_wiki = QRadioButton("Count from Wikipedia")
        (self.r_wiki if source == studio.WIKI else self.r_qobuz).setChecked(True)
        self.summary = QLabel("")
        pick = QHBoxLayout()
        pick.addWidget(self.r_qobuz)
        pick.addWidget(self.r_wiki)
        pick.addStretch(1)
        pick.addWidget(self.summary)

        self.table = QTableWidget(len(rows), len(COLS))
        self.table.setHorizontalHeaderLabels(COLS)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.verticalHeader().setVisible(False)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(1, QHeaderView.Stretch)
        for c in (0, 2, 3, 4, 5):
            hh.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        self.btn_dl = QPushButton("Download ticked")
        self.btn_close = QPushButton("Close")
        self.btn_load = QPushButton("Load Qobuz data…")
        self.btn_load.setVisible(not qobuz_loaded and can_load)
        self.btn_load.setToolTip("Asks Qobuz for this artist's releases (a search and the artist's page, about "
                                 "2 to 5 requests). Nothing was requested to show this window.")
        self.btn_page = QPushButton("Open the artist's page")
        self.btn_page.setVisible(partial)
        self.btn_page.setToolTip("This list only has the albums your search returned; the artist's page has all "
                                 "their releases")
        self.btn_add = QPushButton("Add album…")
        self.btn_add.setToolTip("Add a studio album that Wikipedia's list is missing")
        self.btn_remove = QPushButton("Remove from list")
        self.btn_remove.setToolTip("Take the selected album off the Wikipedia list for this artist")
        self.btn_restore = QPushButton("Put back…")
        self.btn_restore.setEnabled(bool(self.removed))
        self.btn_other_page = QPushButton("Use another Wikipedia page…")
        self.btn_undo = QPushButton("Undo my changes")
        self.btn_undo.setEnabled(bool(self.override))
        fix = QHBoxLayout()
        fix.addWidget(QLabel("Correct Wikipedia's list:"))
        for b in (self.btn_add, self.btn_remove, self.btn_restore, self.btn_other_page, self.btn_undo):
            fix.addWidget(b)
        fix.addStretch(1)
        bar = QHBoxLayout()
        bar.addWidget(QLabel("A ✘ in Wikipedia means Qobuz calls it a studio album and Wikipedia doesn't (or Qobuz "
                             "files it as another type)."))
        bar.addStretch(1)
        bar.addWidget(self.btn_load)
        bar.addWidget(self.btn_page)
        bar.addWidget(self.btn_dl)
        bar.addWidget(self.btn_close)
        lay = QVBoxLayout(self)
        lay.addWidget(head)
        lay.addLayout(pick)
        lay.addWidget(self.table, 1)
        lay.addLayout(fix)
        lay.addLayout(bar)

        self._fill()
        self.r_qobuz.toggled.connect(self._changed)
        self.btn_dl.clicked.connect(self._download)
        self.btn_close.clicked.connect(self.accept)
        self.btn_page.clicked.connect(self.openArtistPage)
        self.btn_load.clicked.connect(self.loadQobuz)
        self.btn_add.clicked.connect(self._add)
        self.btn_remove.clicked.connect(self._remove)
        self.btn_restore.clicked.connect(self._restore)
        self.btn_other_page.clicked.connect(self._other_page)
        self.btn_undo.clicked.connect(lambda: self._request({"op": "reset"}))
        self.table.itemSelectionChanged.connect(self._selection)
        self._selection()

    def current(self):
        return studio.WIKI if self.r_wiki.isChecked() else studio.QOBUZ

    def _fill(self):
        source = self.current()
        for r, row in enumerate(self.rows):
            counted = row["on_wikipedia"] if source == studio.WIKI else row["on_qobuz"]
            check = QTableWidgetItem()
            can = not row["owned"]
            check.setFlags((Qt.ItemIsUserCheckable | Qt.ItemIsEnabled) if can else Qt.ItemIsEnabled)
            if can:
                check.setCheckState(Qt.Checked if counted else Qt.Unchecked)
            self.table.setItem(r, 0, check)
            q = "✔" if row["on_qobuz"] else ("other type" if row.get("qobuz_kind") else "✘" if row["url"] == "" else "✘")
            cells = [row["title"] + (" [E]" if row["explicit"] else ""), str(row["year"] or ""), q,
                     ("✔ yours" if row.get("added") else "✔") if row["on_wikipedia"] else "✘",
                     "✔" if row["owned"] else ""]
            if not row["url"]:
                cells[2] = "not on Qobuz"
            for c, text in enumerate(cells, 1):
                item = QTableWidgetItem(text)
                if c >= 2:
                    item.setTextAlignment(Qt.AlignCenter)
                if row["on_qobuz"] != row["on_wikipedia"]:
                    item.setForeground(QBrush(QColor(theme.WARN)))
                if not counted:
                    item.setForeground(QBrush(QColor(theme.MUTED)))
                if c == 5 and row["owned"]:
                    item.setForeground(QBrush(QColor(theme.OK)))
                self.table.setItem(r, c, item)
        qo, qt, _ = studio.counts(self.rows, studio.QOBUZ)
        wo, wt, _ = studio.counts(self.rows, studio.WIKI)
        self.summary.setText(f"Qobuz: you own {qo} of {qt} · Wikipedia: you own {wo} of {wt}")

    # ---- corrections: each one is saved by the window that opened this one, which then reopens it
    def _selected(self):
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        return self.rows[rows[0]] if len(rows) == 1 else None

    def _selection(self):
        row = self._selected()
        self.btn_remove.setEnabled(bool(row and row["on_wikipedia"]))

    def _request(self, action):
        self.overrideRequested.emit(action)
        self.accept()

    def _add(self):
        text, ok = QInputDialog.getText(self, "Add album", "Album title, with the year if you know it — for example "
                                        "“Title (1999)”:")
        title, year = studio.parse_title_year(text)
        if ok and title:
            self._request({"op": "add", "title": title, "year": year})

    def _remove(self):
        row = self._selected()
        if row and row["on_wikipedia"]:
            self._request({"op": "remove", "title": row["title"]})

    def _restore(self):
        if not self.removed:
            return
        title, ok = QInputDialog.getItem(self, "Put back", "Album to put back on the list:", self.removed, 0, False)
        if ok and title:
            self._request({"op": "restore", "title": title})

    def _other_page(self):
        text, ok = QInputDialog.getText(self, "Use another Wikipedia page", "Paste the page's address or type its "
                                        "title. Leave empty to go back to the page found automatically.",
                                        text=self.override.get("page", ""))
        if not ok:
            return
        lang, title = wikipedia.page_ref(text, self.override.get("lang") or "")
        self._request({"op": "page", "page": title, "lang": lang if title else ""})

    def _changed(self, _on):
        self._fill()
        self.sourceChosen.emit(self.current())

    def _download(self):
        picked = []
        for r, row in enumerate(self.rows):
            item = self.table.item(r, 0)
            if item is not None and item.checkState() == Qt.Checked:
                picked.append((row["title"], row["url"]))
        if picked:
            self.downloadRequested.emit(picked)


def no_list_found(parent, artist, choice):
    """Wikipedia gave no usable list. With corrections of yours in place (a page that doesn't work, say) offers
    to undo them; returns True when they were undone and the caller should look again."""
    if not choice.override_for(artist):
        return False
    if QMessageBox.question(parent, "Wikipedia", f"With your corrections there is no usable list of studio albums "
                            f"for {artist} (the page you chose may not have one).\n\nUndo your corrections for "
                            "this artist?") != QMessageBox.Yes:
        return False
    studio.edit_override(artist, {"op": "reset"})
    return True


EMPTY_LIST = {"albums": [], "page": "", "removed": []}
