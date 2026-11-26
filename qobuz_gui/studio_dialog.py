"""Side-by-side view of an artist's studio albums on Qobuz and on Wikipedia, with what you own."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QHBoxLayout, QHeaderView, QLabel, QPushButton, QRadioButton, QTableWidget,
    QTableWidgetItem, QVBoxLayout,
)

from . import studio, theme

COLS = ["", "Album", "Year", "Qobuz", "Wikipedia", "You own"]


class StudioCompare(QDialog):
    sourceChosen = Signal(str)          # "qobuz" or "wikipedia"
    downloadRequested = Signal(list)    # [(title, url)] of the ticked albums
    openArtistPage = Signal()           # partial list: open the artist's full page in Search
    loadQobuz = Signal()                # ask Qobuz for this artist's catalogue (two or more requests)

    def __init__(self, parent, artist, rows, source, page="", partial=False, qobuz_loaded=True, can_load=True):
        super().__init__(parent)
        self.setWindowTitle(f"Studio albums — {artist}")
        self.resize(820, 560)
        self.artist, self.rows, self.source = artist, rows, source
        head = QLabel(f"<b>{artist}</b> — the two lists differ where a row has a ✘. "
                      "Pick the one that matches what you consider this artist's studio albums; the choice is "
                      "remembered for this artist." + (f"<br>Wikipedia page: {page}" if page else "")
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
        lay.addLayout(bar)

        self._fill()
        self.r_qobuz.toggled.connect(self._changed)
        self.btn_dl.clicked.connect(self._download)
        self.btn_close.clicked.connect(self.accept)
        self.btn_page.clicked.connect(self.openArtistPage)
        self.btn_load.clicked.connect(self.loadQobuz)

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
                     "✔" if row["on_wikipedia"] else "✘", "✔" if row["owned"] else ""]
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
