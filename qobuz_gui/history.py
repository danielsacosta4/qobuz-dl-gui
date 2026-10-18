"""Shared UI for the safety layer: the library guard prompt, the preview dialog and the
History window with Undo."""

import datetime
import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QDialogButtonBox, QHBoxLayout, QHeaderView, QLabel, QMessageBox,
    QPlainTextEdit, QPushButton, QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from . import safeio
from .client import run_async

_journal = None


def journal():
    global _journal
    if _journal is None:
        _journal = safeio.Journal()
    return _journal


def ensure_library(parent, root):
    """Checks the guard; offers to confirm an unmarked library. True when writing is allowed."""
    state = safeio.library_state(root)
    if state == "ok":
        return True
    if state == "missing":
        QMessageBox.warning(parent, "Library not available",
                            f"The library folder isn't available:\n{root}\n\nIs the TrueNAS share mounted? "
                            "Nothing was changed.")
        return False
    try:
        letters = sorted(e for e in os.listdir(root) if not e.startswith("."))
    except OSError:
        letters = []
    sample = ", ".join(letters[:12]) + ("…" if len(letters) > 12 else "")
    answer = QMessageBox.question(
        parent, "Confirm your library",
        f"Is this your music library?\n\n{root}\n\nIt contains: {sample or '(nothing)'}\n\n"
        "The app will place a small hidden file here (.qobuz-dl-gui-library). From then on it only writes to "
        "this folder while that file is present — so if the share isn't mounted, nothing gets written by mistake.")
    if answer != QMessageBox.Yes:
        return False
    try:
        safeio.mark_library(root)
    except OSError as e:
        QMessageBox.warning(parent, "Confirm your library", f"Couldn't write the marker file: {e}")
        return False
    return True


def preview(parent, title, plans, footer=""):
    """Shows every step of the plans before anything happens. True to go ahead."""
    dlg = QDialog(parent)
    dlg.setWindowTitle(title)
    dlg.resize(860, 520)
    total = sum(p.total_bytes() for p in plans)
    head = QLabel(f"<b>{len(plans)} change{'s' if len(plans) != 1 else ''}</b> · {safeio._human(total)} to copy. "
                  "Nothing happens until you press <b>Go ahead</b>. Everything is recorded in History and can be undone.")
    head.setWordWrap(True)
    text = QPlainTextEdit()
    text.setReadOnly(True)
    text.setLineWrapMode(QPlainTextEdit.NoWrap)
    body = []
    for p in plans:
        body.append(f"■ {p.title}")
        body.extend(f"    {line}" for line in p.describe())
        body.append("")
    text.setPlainText("\n".join(body))
    buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
    go = buttons.addButton("Go ahead", QDialogButtonBox.AcceptRole)
    go.setDefault(True)
    buttons.accepted.connect(dlg.accept)
    buttons.rejected.connect(dlg.reject)
    v = QVBoxLayout(dlg)
    v.addWidget(head)
    v.addWidget(text, 1)
    if footer:
        f = QLabel(footer)
        f.setWordWrap(True)
        f.setEnabled(False)
        v.addWidget(f)
    v.addWidget(buttons)
    return bool(dlg.exec())


KIND_TEXT = {"copy": "Copied", "move": "Moved to bin", "trash": "Moved to Trash", "tags": "Tags rewritten",
             "add_file": "Added", "mkdir": "Created folder", "purge": "Deleted permanently"}


class HistoryDialog(QDialog):
    def __init__(self, parent=None, on_change=None):
        super().__init__(parent)
        self.setWindowTitle("History")
        self.resize(1000, 600)
        self.on_change = on_change

        intro = QLabel("Every change the app made to your library and downloads. Undo reverses a change step by "
                       "step; a file that was edited since is left alone and reported.")
        intro.setWordWrap(True)
        self.batches = QTableWidget(0, 4)
        self.batches.setHorizontalHeaderLabels(["When", "What", "Steps", "Status"])
        self.batches.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.batches.setSelectionMode(QAbstractItemView.SingleSelection)
        self.batches.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.batches.verticalHeader().setVisible(False)
        bh = self.batches.horizontalHeader()
        bh.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        bh.setSectionResizeMode(1, QHeaderView.Stretch)
        bh.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        bh.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.ops = QPlainTextEdit()
        self.ops.setReadOnly(True)
        self.ops.setLineWrapMode(QPlainTextEdit.NoWrap)
        split = QSplitter(Qt.Vertical)
        split.addWidget(self.batches)
        split.addWidget(self.ops)
        split.setSizes([320, 240])

        self.btn_undo = QPushButton("Undo selected")
        self.btn_undo.setEnabled(False)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(self.btn_undo)
        row.addWidget(close)
        v = QVBoxLayout(self)
        v.addWidget(intro)
        v.addWidget(split, 1)
        v.addLayout(row)

        self.batches.itemSelectionChanged.connect(self._show_ops)
        self.btn_undo.clicked.connect(self._undo)
        self.reload()

    def reload(self):
        self.rows = journal().batches()
        self.batches.setRowCount(len(self.rows))
        for r, b in enumerate(self.rows):
            when = datetime.datetime.fromtimestamp(b["started"]).strftime("%Y-%m-%d %H:%M")
            for c, text in enumerate([when, b["title"], str(b["ops"]), b["status"]]):
                cell = QTableWidgetItem(text)
                cell.setToolTip(b["note"] or text)
                self.batches.setItem(r, c, cell)

    def _selected(self):
        rows = {i.row() for i in self.batches.selectedIndexes()}
        return self.rows[rows.pop()] if rows else None

    def _show_ops(self):
        b = self._selected()
        if not b:
            self.ops.clear()
            self.btn_undo.setEnabled(False)
            return
        lines = []
        for o in journal().ops(b["id"]):
            what = KIND_TEXT.get(o["kind"], o["kind"])
            path = o["dst"] if o["kind"] in ("copy", "add_file", "mkdir") else o["src"]
            extra = f" → {o['dst']}" if o["kind"] in ("move", "trash") else ""
            lines.append(f"[{o['status']}] {what}: {path}{extra}")
        if b["note"]:
            lines.append("")
            lines.append(f"Note: {b['note']}")
        self.ops.setPlainText("\n".join(lines))
        self.btn_undo.setEnabled(b["status"] in ("done", "failed", "partly undone"))

    def _undo(self):
        b = self._selected()
        if not b:
            return
        if QMessageBox.question(self, "Undo", f"Undo “{b['title']}”?") != QMessageBox.Yes:
            return
        self.btn_undo.setEnabled(False)

        def done(result):
            n, skipped = result
            self.reload()
            msg = f"Undid {n} step{'s' if n != 1 else ''}."
            if skipped:
                msg += "\n\nLeft alone:\n" + "\n".join(f"• {s}" for s in skipped[:10])
            QMessageBox.information(self, "Undo", msg)
            if self.on_change:
                self.on_change()

        def fail(msg):
            self.reload()
            QMessageBox.warning(self, "Undo", f"Undo stopped: {msg}")
        run_async(lambda: journal().undo(b["id"]), done, fail)
