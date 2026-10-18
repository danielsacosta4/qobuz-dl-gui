"""Library health tab: the Library doctor and Complete my artists."""

import datetime
import html
import os

from PySide6.QtCore import QThread, Qt, QUrl, Signal
from PySide6.QtGui import QBrush, QColor, QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QHBoxLayout, QHeaderView, QLabel, QMessageBox,
    QProgressBar, QPushButton, QSplitter, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
)

from . import doctor, history, musicbrainz, navidrome, prefs, rules, safeio
from .client import run_async
from .library_tab import library_folder

IGNORED = rules.EXCEPTIONS
GREEN, AMBER, RED = QColor("#3fa65a"), QColor("#c99a2e"), QColor("#e5534b")
FIX_TEXT = {
    doctor.DUPLICATE: "Move the extra copies to the replaced bin",
    doctor.LOCATION: "Move the artist folder to the right letter",
    doctor.UPGRADE: "Download better version",
    doctor.EXPLICIT: "Download explicit version",
    doctor.MISSING_TRACKS: "Download full album",
    doctor.NO_COVER: "Add cover.jpg from Qobuz",
    doctor.SPLIT: "Write one album artist tag",
    doctor.UNOFFICIAL: "Move to replaced bin",
}


class ScanWorker(QThread):
    progressed = Signal(int, int, str)
    finishedScan = Signal(object, object, str)     # issues, completion, error

    def __init__(self, session, use_mb):
        super().__init__()
        self.session = session
        self.use_mb = use_mb
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        try:
            scanner = doctor.Scanner(self.session, library_folder(), use_musicbrainz=self.use_mb,
                                     progress=lambda n, t, a: self.progressed.emit(n, t, a),
                                     should_stop=lambda: self._stop,
                                     prefer_explicit=prefs.get_bool(prefs.PREFER_EXPLICIT, True))
            issues, completion = scanner.run()
            self.finishedScan.emit(issues, completion, "")
        except Exception as e:
            self.finishedScan.emit([], [], str(e) or e.__class__.__name__)


def _cell(text, tip=None, colour=None, align=None):
    c = QTableWidgetItem(str(text))
    c.setToolTip(tip or str(text))
    if colour is not None:
        c.setForeground(QBrush(colour))
    if align is not None:
        c.setTextAlignment(align)
    return c


class HealthTab(QWidget):
    def __init__(self, session, download_tab, status, parent=None):
        super().__init__(parent)
        self.session = session
        self.download_tab = download_tab
        self.status = status
        self.worker = None
        self.issues = []
        self.completion = []

        # --- scan bar
        self.btn_scan = QPushButton("Scan library")
        self.btn_scan.setDefault(True)
        self.btn_stop = QPushButton("Stop")
        self.btn_stop.setVisible(False)
        self.use_mb = QCheckBox("Check official releases on MusicBrainz")
        self.use_mb.setChecked(prefs.get_bool("doctor_use_mb", True))
        self.use_mb.setToolTip(musicbrainz.LIMITATION)
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        self.last_scan = QLabel("")
        self.last_scan.setEnabled(False)
        bar = QHBoxLayout()
        self.btn_report = QPushButton("Library report")
        self.btn_report.setToolTip("Measure the library against the rules and write a report folder "
                                   "(numbers, CSV lists, a spot-check sample). Nothing is changed.")
        bar.addWidget(self.btn_scan)
        bar.addWidget(self.btn_report)
        bar.addWidget(self.btn_stop)
        bar.addWidget(self.use_mb)
        bar.addWidget(self.progress, 1)
        bar.addWidget(self.last_scan, 1)
        note = QLabel("The scan reads your library from Navidrome and compares it with Qobuz, pausing between "
                      "Qobuz requests to keep your account safe. MusicBrainz allows 1 request per second, so the "
                      "first full scan of a big library can take a while (it runs in the background and you can "
                      "keep using the app). Later scans reuse what was already checked and are much faster.")
        note.setWordWrap(True)
        note.setEnabled(False)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_doctor(), "Library doctor")
        self.tabs.addTab(self._build_artists(), "Complete my artists")

        lay = QVBoxLayout(self)
        lay.addLayout(bar)
        lay.addWidget(note)
        lay.addWidget(self.tabs, 1)

        self.btn_scan.clicked.connect(self.scan)
        self.btn_report.clicked.connect(self.run_report)
        self._report_dir = None
        self.download_tab.runner.jobChanged.connect(self._report_job_changed)
        self.btn_stop.clicked.connect(self._stop)
        self.use_mb.toggled.connect(lambda on: prefs.set_value("doctor_use_mb", on))

        when, issues, completion = doctor.load_results()
        if when:
            self._show_results(issues, completion, when)
        else:
            self.last_scan.setText("Not scanned yet.")

    # ================= Doctor ====================================================
    def _build_doctor(self):
        self.kind_filter = QComboBox()
        self.kind_filter.addItem("All problems", "")
        self.kind_filter.addItem("Rule breaks only (R1–R5)", "rules")
        for k, label in doctor.LABELS.items():
            self.kind_filter.addItem(label, k)
        self.show_ignored = QCheckBox("Show exceptions")
        self.show_ignored.setToolTip("Problems you marked as accepted exceptions")
        self.rules_line = QLabel("")
        self.rules_line.setTextFormat(Qt.RichText)
        self.rules_line.setWordWrap(True)
        self.rules_line.setToolTip("\n\n".join(f"{k} {n}: {t}" for k, (n, t) in rules.RULES.items()))
        self.summary = QLabel("")
        top = QHBoxLayout()
        top.addWidget(QLabel("Show:"))
        top.addWidget(self.kind_filter)
        top.addWidget(self.show_ignored)
        top.addStretch(1)
        top.addWidget(self.summary)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["", "Problem", "Artist", "Album", "Details"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setSortingEnabled(True)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        for c in (1, 2):
            hh.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(3, QHeaderView.Stretch)
        hh.setSectionResizeMode(4, QHeaderView.Stretch)

        self.btn_all = QPushButton("Tick all shown")
        self.btn_none = QPushButton("Untick all")
        self.btn_fix = QPushButton("Fix ticked")
        self.btn_ignore = QPushButton("Mark as exception")
        self.btn_ignore.setToolTip("Accept these as they are: they no longer count as rule breaks "
                                   "(Show exceptions brings them back)")
        self.btn_folder = QPushButton("Open folder")
        self.fix_hint = QLabel("")
        self.fix_hint.setEnabled(False)
        self.fix_hint.setWordWrap(True)
        actions = QHBoxLayout()
        actions.addWidget(self.btn_all)
        actions.addWidget(self.btn_none)
        actions.addWidget(self.btn_folder)
        actions.addWidget(self.fix_hint, 1)
        actions.addWidget(self.btn_ignore)
        actions.addWidget(self.btn_fix)

        w = QWidget()
        v = QVBoxLayout(w)
        v.addLayout(top)
        v.addWidget(self.rules_line)
        v.addWidget(self.table, 1)
        v.addLayout(actions)

        self.kind_filter.currentIndexChanged.connect(self._fill_issues)
        self.show_ignored.toggled.connect(self._fill_issues)
        self.btn_all.clicked.connect(lambda: self._tick(True))
        self.btn_none.clicked.connect(lambda: self._tick(False))
        self.btn_fix.clicked.connect(self._fix)
        self.btn_ignore.clicked.connect(self._ignore)
        self.btn_folder.clicked.connect(self._open_folder)
        self.table.itemSelectionChanged.connect(self._selection_hint)
        return w

    def _ignored(self):
        return set(prefs.get_json(IGNORED, []) or [])

    @staticmethod
    def _is_exception(issue, stored):
        return issue.key in stored or issue.legacy_key in stored

    def _fill_issues(self):
        kind = self.kind_filter.currentData()
        stored = self._ignored()
        ignored = {i.key for i in self.issues if self._is_exception(i, stored)}

        def wanted(i):
            if kind == "rules":
                return bool(i.rule)
            return not kind or i.kind == kind
        shown = [i for i in self.issues
                 if wanted(i) and (self.show_ignored.isChecked() or i.key not in ignored)]
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(shown))
        for r, issue in enumerate(shown):
            check = QTableWidgetItem()
            check.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            check.setCheckState(Qt.Unchecked)
            check.setData(Qt.UserRole, self.issues.index(issue))
            self.table.setItem(r, 0, check)
            colour = AMBER if issue.kind in (doctor.UNOFFICIAL, doctor.SPLIT, doctor.MISSING_TRACKS,
                                             doctor.DUPLICATE, doctor.LOCATION) else None
            label = issue.extra.get("label") or doctor.LABELS[issue.kind]
            if issue.key in ignored:
                label += " (exception)"
            self.table.setItem(r, 1, _cell(label, colour=colour))
            self.table.setItem(r, 2, _cell(issue.artist))
            self.table.setItem(r, 3, _cell(issue.album, tip=issue.folder or issue.album))
            self.table.setItem(r, 4, _cell(issue.detail))
        self.table.setSortingEnabled(True)
        counts = {}
        for i in self.issues:
            if i.key not in ignored:
                counts[i.kind] = counts.get(i.kind, 0) + 1
        self.summary.setText(" · ".join(f"{doctor.LABELS[k]}: {n}" for k, n in counts.items()
                                        if not doctor.RULE_OF.get(k)) or "")
        # one line per rule: how many breaks, how many accepted exceptions
        parts = []
        for code, (name, _text) in rules.RULES.items():
            kinds = [k for k, r in doctor.RULE_OF.items() if r == code]
            breaks = sum(1 for i in self.issues if i.kind in kinds and i.key not in ignored)
            exc = sum(1 for i in self.issues if i.kind in kinds and i.key in ignored)
            mark = "✔" if not breaks else "✘"
            colour = "#3fa65a" if not breaks else "#c99a2e"
            parts.append(f"<span style='color:{colour}'>{mark}</span> <b>{code}</b> {html.escape(name)}: "
                         f"{breaks}" + (f" <i>(+{exc} exception{'s' if exc != 1 else ''})</i>" if exc else ""))
        self.rules_line.setText("&nbsp;&nbsp;·&nbsp;&nbsp;".join(parts) if self.issues is not None else "")

    def _issue_rows(self, ticked=True):
        out = []
        for r in range(self.table.rowCount()):
            c = self.table.item(r, 0)
            if c is None:
                continue
            if (ticked and c.checkState() == Qt.Checked) or (not ticked and c.isSelected()):
                out.append(self.issues[c.data(Qt.UserRole)])
        return out

    def _tick(self, on):
        for r in range(self.table.rowCount()):
            self.table.item(r, 0).setCheckState(Qt.Checked if on else Qt.Unchecked)

    def _selection_hint(self):
        sel = self._issue_rows(ticked=False)
        if len(sel) == 1:
            self.fix_hint.setText(f"Fix: {FIX_TEXT[sel[0].kind]}")
        else:
            self.fix_hint.setText("")

    def _open_folder(self):
        for i in self._issue_rows(ticked=False)[:5]:
            if i.folder and os.path.isdir(i.folder):
                QDesktopServices.openUrl(QUrl.fromLocalFile(i.folder))

    def _ignore(self):
        ticked = self._issue_rows()
        if not ticked:
            return
        ignored = self._ignored() | {i.key for i in ticked}
        prefs.set_json(IGNORED, sorted(ignored))
        self._fill_issues()

    def _fix(self):
        ticked = self._issue_rows()
        if not ticked:
            self.status("Tick the problems you want fixed first.")
            return
        downloads = [i for i in ticked if i.kind in (doctor.UPGRADE, doctor.EXPLICIT, doctor.MISSING_TRACKS) and i.url]
        covers = [i for i in ticked if i.kind == doctor.NO_COVER]
        splits = [i for i in ticked if i.kind == doctor.SPLIT]
        bins = [i for i in ticked if i.kind == doctor.UNOFFICIAL]
        dupes = [i for i in ticked if i.kind == doctor.DUPLICATE]
        moves = [i for i in ticked if i.kind == doctor.LOCATION]
        lines = []
        if downloads:
            lines.append(f"• Queue {len(downloads)} download{'s' if len(downloads) != 1 else ''}. When they finish, "
                         "send them from the Library tab, which will offer to replace the old copies.")
        if covers:
            lines.append(f"• Save cover.jpg from Qobuz into {len(covers)} album folder{'s' if len(covers) != 1 else ''}.")
        if splits:
            lines.append(f"• Rewrite the album artist tag in {len(splits)} album folder{'s' if len(splits) != 1 else ''} "
                         "so Navidrome shows each as one album. This changes the files' tags.")
        if bins:
            lines.append(f"• Move {len(bins)} album{'s' if len(bins) != 1 else ''} to the hidden replaced bin "
                         "(nothing is deleted until you empty it in the Library tab).")
        if dupes:
            lines.append(f"• For {len(dupes)} album{'s' if len(dupes) != 1 else ''} in the library more than once, "
                         "keep the best copy and move the others to the hidden replaced bin.")
        if moves:
            lines.append(f"• Move {len(moves)} artist folder{'s' if len(moves) != 1 else ''} to the right letter.")
        if not lines:
            self.status("None of the ticked problems can be fixed automatically.")
            return
        if QMessageBox.question(self, "Fix problems", "\n\n".join(lines)) != QMessageBox.Yes:
            return

        # downloads go through the normal queue (explicit preference and options apply)
        for i in downloads:
            self.download_tab.enqueue([i.url], title=f"{i.artist} — {i.album} ({doctor.LABELS[i.kind].lower()})")

        file_fixes = covers + splits + bins + dupes + moves
        if not file_fixes:
            self.status(f"Queued {len(downloads)} download{'s' if len(downloads) != 1 else ''}.")
            return
        lib = library_folder()
        if not history.ensure_library(self, lib):
            return

        def build():
            """Builds one plan per fix (covers are downloaded first so the preview is exact)."""
            import requests
            from . import library as L
            plans, problems = [], []
            for i in file_fixes:
                try:
                    if not i.folder:
                        raise ValueError("couldn't find the album folder")
                    if i.kind == doctor.NO_COVER:
                        if not i.cover_url:
                            raise ValueError("Qobuz has no matching album to take a cover from")
                        r = requests.get(i.cover_url, timeout=30)
                        r.raise_for_status()
                        plan = safeio.Plan(f"Add cover — {i.artist} — {i.album}", lib)
                        plan.add_file(os.path.join(i.folder, "cover.jpg"), r.content)
                    elif i.kind == doctor.DUPLICATE:
                        retire = [f for f in i.extra.get("retire") or [] if os.path.isdir(f)]
                        if not retire:
                            raise ValueError("couldn't confirm which folders are the extra copies — "
                                             "open the folder and sort it by hand, or mark it as an exception")
                        plan = safeio.Plan(f"Keep one copy — {i.artist} — {i.album}", lib)
                        for f in retire:
                            plan.retire(f)
                    elif i.kind == doctor.LOCATION:
                        dst = i.extra.get("to") or ""
                        if not dst:
                            raise ValueError("this folder needs sorting by hand")
                        if os.path.lexists(dst):
                            raise ValueError(f"{dst} already exists — merge the two folders by hand")
                        plan = safeio.Plan(f"Move {i.artist} to {os.path.basename(os.path.dirname(dst))}", lib)
                        plan.relocate(i.folder, dst)
                    elif i.kind == doctor.SPLIT:
                        artist = i.extra.get("album_artist") or i.artist
                        plan = safeio.Plan(f"One album artist ({artist}) — {i.album}", lib)
                        for root, _, files in os.walk(i.folder):
                            for f in sorted(files):
                                if os.path.splitext(f)[1].lower() in L.AUDIO_EXT:
                                    plan.set_tags(os.path.join(root, f), {"albumartist": [artist]})
                    else:
                        plan = safeio.Plan(f"Move unofficial release to bin — {i.artist} — {i.album}", lib)
                        plan.retire(i.folder)
                    plans.append((i, plan))
                except Exception as e:
                    problems.append((i, str(e)))
            return plans, problems

        def built(result):
            plans, problems = result
            if not plans:
                self.status("Nothing could be fixed: " + "; ".join(f"{i.album} ({e})" for i, e in problems[:3]),
                            error=True)
                return
            if not history.preview(self, "Fix library problems", [p for _, p in plans]):
                return

            def work():
                done, failed = [], list(problems)
                for i, plan in plans:
                    try:
                        plan.execute(history.journal())
                        done.append(i)
                    except Exception as e:
                        failed.append((i, str(e)))
                return done, failed
            run_async(work, finished, lambda m: self.status(f"Fixing failed: {m}", error=True))

        def finished(result):
            done, failed = result
            fixed = {id(i) for i in done}
            self.issues = [i for i in self.issues if id(i) not in fixed]
            doctor.save_results(self.issues, self.completion)
            self._fill_issues()
            msg = f"Fixed {len(done)} problem{'s' if len(done) != 1 else ''} (undo from Library → History)"
            if downloads:
                msg += f", queued {len(downloads)} download{'s' if len(downloads) != 1 else ''}"
            if failed:
                msg += f". {len(failed)} couldn't be fixed: " + "; ".join(
                    f"{i.album} ({err})" for i, err in failed[:3])
            self.status(msg, error=bool(failed))
            if done:
                self._rescan()

        run_async(build, built, lambda m: self.status(f"Preparing fixes failed: {m}", error=True))

    def _rescan(self):
        if navidrome.configured() and prefs.get_bool(prefs.NAVIDROME_RESCAN, True):
            run_async(lambda: navidrome.Navidrome.from_settings().start_scan(), None, None)

    # ================= Complete my artists =========================================
    def _build_artists(self):
        self.hide_complete = QCheckBox("Hide complete artists")
        self.hide_complete.setChecked(True)
        self.sort_mode = QComboBox()
        self.sort_mode.addItem("Closest to complete first", "close")
        self.sort_mode.addItem("Most missing first", "missing")
        self.sort_mode.addItem("Artist name", "name")
        self.art_summary = QLabel("")
        top = QHBoxLayout()
        top.addWidget(self.sort_mode)
        top.addWidget(self.hide_complete)
        top.addStretch(1)
        top.addWidget(self.art_summary)

        self.art_table = QTableWidget(0, 4)
        self.art_table.setHorizontalHeaderLabels(["Artist", "Studio albums", "Complete", "Missing"])
        self.art_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.art_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.art_table.verticalHeader().setVisible(False)
        ah = self.art_table.horizontalHeader()
        ah.setSectionResizeMode(0, QHeaderView.Stretch)
        for c in (1, 2, 3):
            ah.setSectionResizeMode(c, QHeaderView.ResizeToContents)

        self.missing_title = QLabel("Select an artist to see the missing albums.")
        self.missing_title.setWordWrap(True)
        self.missing_table = QTableWidget(0, 4)
        self.missing_table.setHorizontalHeaderLabels(["", "Album", "Year", "Quality"])
        self.missing_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.missing_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.missing_table.verticalHeader().setVisible(False)
        mh = self.missing_table.horizontalHeader()
        mh.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        mh.setSectionResizeMode(1, QHeaderView.Stretch)
        for c in (2, 3):
            mh.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        self.btn_get_missing = QPushButton("Download ticked")
        self.btn_get_all = QPushButton("Download everything missing for selected artists")
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.addWidget(self.missing_title)
        rv.addWidget(self.missing_table, 1)
        rb = QHBoxLayout()
        rb.addWidget(self.btn_get_all)
        rb.addStretch(1)
        rb.addWidget(self.btn_get_missing)
        rv.addLayout(rb)

        split = QSplitter(Qt.Horizontal)
        split.addWidget(self.art_table)
        split.addWidget(right)
        split.setSizes([480, 520])

        note = QLabel("Counts official studio albums only (checked on MusicBrainz when it knows the artist, "
                      "otherwise by Qobuz's release type with broadcasts and similar releases left out). "
                      "Live albums, compilations, EPs and singles aren't counted.")
        note.setWordWrap(True)
        note.setEnabled(False)

        w = QWidget()
        v = QVBoxLayout(w)
        v.addLayout(top)
        v.addWidget(split, 1)
        v.addWidget(note)

        self.sort_mode.currentIndexChanged.connect(self._fill_artists)
        self.hide_complete.toggled.connect(self._fill_artists)
        self.art_table.itemSelectionChanged.connect(self._show_missing)
        self.btn_get_missing.clicked.connect(self._download_ticked_missing)
        self.btn_get_all.clicked.connect(self._download_all_missing)
        return w

    def _sorted_completion(self):
        rows = [c for c in self.completion if c.total and not (self.hide_complete.isChecked() and not c.missing)]
        mode = self.sort_mode.currentData()
        if mode == "close":
            rows.sort(key=lambda c: (-(c.owned / c.total), len(c.missing), c.artist.casefold()))
        elif mode == "missing":
            rows.sort(key=lambda c: (-len(c.missing), c.artist.casefold()))
        else:
            rows.sort(key=lambda c: c.artist.casefold())
        return rows

    def _fill_artists(self):
        rows = self._sorted_completion()
        self.art_table.setRowCount(len(rows))
        for r, c in enumerate(rows):
            name = _cell(c.artist + ("" if c.verified else "  ·  unverified"),
                         tip=("Checked against MusicBrainz's official list" if c.verified
                              else "Not checked on MusicBrainz — based on Qobuz's release types"))
            name.setData(Qt.UserRole, self.completion.index(c))
            self.art_table.setItem(r, 0, name)
            self.art_table.setItem(r, 1, _cell(f"{c.owned} of {c.total}", align=Qt.AlignCenter))
            pct = int(round(100 * c.owned / c.total))
            self.art_table.setItem(r, 2, _cell(f"{pct}%", colour=GREEN if pct == 100 else None, align=Qt.AlignCenter))
            self.art_table.setItem(r, 3, _cell(str(len(c.missing)), align=Qt.AlignCenter))
        done = sum(1 for c in self.completion if c.total and not c.missing)
        self.art_summary.setText(f"{done} of {sum(1 for c in self.completion if c.total)} artists complete")

    def _selected_artists(self):
        rows = sorted({i.row() for i in self.art_table.selectedIndexes()})
        return [self.completion[self.art_table.item(r, 0).data(Qt.UserRole)] for r in rows]

    def _show_missing(self):
        sel = self._selected_artists()
        if len(sel) != 1:
            self.missing_table.setRowCount(0)
            self.missing_title.setText(f"{len(sel)} artists selected." if sel else
                                       "Select an artist to see the missing albums.")
            return
        c = sel[0]
        self.missing_title.setText(f"<b>{html.escape(c.artist)}</b> — {c.owned} of {c.total} studio albums"
                                   + ("" if c.missing else " · complete ✔"))
        self.missing_table.setRowCount(len(c.missing))
        for r, m in enumerate(c.missing):
            chk = QTableWidgetItem()
            chk.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            chk.setCheckState(Qt.Checked)
            chk.setData(Qt.UserRole, r)
            self.missing_table.setItem(r, 0, chk)
            self.missing_table.setItem(r, 1, _cell(m["title"] + (" [E]" if m.get("explicit") else "")))
            self.missing_table.setItem(r, 2, _cell(m.get("year", ""), align=Qt.AlignCenter))
            self.missing_table.setItem(r, 3, _cell(m.get("quality", "")))

    def _queue_missing(self, entries):
        added = 0
        for artist, m in entries:
            added += len(self.download_tab.enqueue([m["url"]], title=f"{artist} — {m['title']}"))
        if added:
            self.status(f"Queued {added} missing album{'s' if added != 1 else ''}.")

    def _download_ticked_missing(self):
        sel = self._selected_artists()
        if len(sel) != 1:
            return
        c = sel[0]
        entries = [(c.artist, c.missing[self.missing_table.item(r, 0).data(Qt.UserRole)])
                   for r in range(self.missing_table.rowCount())
                   if self.missing_table.item(r, 0).checkState() == Qt.Checked]
        self._queue_missing(entries)

    def _download_all_missing(self):
        sel = self._selected_artists()
        entries = [(c.artist, m) for c in sel for m in c.missing]
        if not entries:
            return
        if QMessageBox.question(self, "Download missing albums",
                                f"Queue {len(entries)} missing studio albums from {len(sel)} "
                                f"artist{'s' if len(sel) != 1 else ''}?") != QMessageBox.Yes:
            return
        self._queue_missing(entries)

    # ================= report ====================================================
    def run_report(self):
        import datetime
        import sys
        from .jobs import Job
        if not navidrome.configured():
            QMessageBox.information(self, "Library report", "Set up the Navidrome connection in Settings first.")
            return
        stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H%M")
        self._report_dir = os.path.expanduser(f"~/qobuz-dl-gui-report-{stamp}")
        self.download_tab.runner.enqueue(Job("Library report", [], program=[
            sys.executable, "-u", "-m", "qobuz_gui.report", self._report_dir]))
        self.status(f"Library report queued — it will be written to {self._report_dir}")

    def _report_job_changed(self, job):
        if job.title != "Library report" or job.status not in ("done", "failed"):
            return
        if job.status == "done" and self._report_dir and os.path.isdir(self._report_dir):
            self.status(f"Library report ready: {self._report_dir}")
            QDesktopServices.openUrl(QUrl.fromLocalFile(self._report_dir))
        elif job.status == "failed":
            self.status("The library report failed — see the log.", error=True)

    # ================= scanning ==================================================
    def scan(self):
        if not navidrome.configured():
            QMessageBox.information(self, "Library doctor", "Set up the Navidrome connection in Settings first — "
                                    "the doctor reads your library from Navidrome.")
            return
        self.worker = ScanWorker(self.session, self.use_mb.isChecked())
        self.worker.progressed.connect(self._progress)
        self.worker.finishedScan.connect(self._finished)
        self.btn_scan.setEnabled(False)
        self.btn_stop.setVisible(True)
        self.progress.setVisible(True)
        self.progress.setValue(0)
        self.progress.setFormat("Reading the library from Navidrome…")
        self.last_scan.setVisible(False)
        self.worker.start()

    def _progress(self, n, total, artist):
        self.progress.setMaximum(total)
        self.progress.setValue(n)
        self.progress.setFormat(f"Artist {n} of {total} — {artist}")

    def _stop(self):
        if self.worker:
            self.worker.stop()
            self.progress.setFormat("Stopping after this artist…")

    def _finished(self, issues, completion, error):
        stopped = self.worker is not None and self.worker._stop
        self.worker = None
        self.btn_scan.setEnabled(True)
        self.btn_stop.setVisible(False)
        self.progress.setVisible(False)
        self.last_scan.setVisible(True)
        if error:
            self.status(f"Library scan failed: {error}", error=True)
            self.last_scan.setText(f"Scan failed: {error}")
            return
        doctor.save_results(issues, completion)
        self._show_results(issues, completion, None, partial=stopped)
        stored = self._ignored()
        n = sum(1 for i in issues if not self._is_exception(i, stored))
        self.status(f"Library scan {'stopped' if stopped else 'finished'}: {n} problem{'s' if n != 1 else ''} found.")

    def _show_results(self, issues, completion, when, partial=False):
        self.issues = list(issues)
        self.completion = list(completion)
        stamp = datetime.datetime.fromtimestamp(when).strftime("%d %b %Y %H:%M") if when else "just now"
        self.last_scan.setText(f"Last scan: {stamp}" + (" (stopped early — partial results)" if partial else ""))
        self._fill_issues()
        self._fill_artists()

    def busy(self):
        return bool(self.worker and self.worker.isRunning())
