"""Search tab, part: the album details panel on the right.

A mixin of SearchTab: it uses the tab's widgets and state (details, table, album_cache, covers, net …).
"""

import html

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QPixmap
from PySide6.QtNetwork import QNetworkRequest

from . import cache, classify, prefs, theme
from .client import run_async
from .search_common import ALBUM_TTL, COVER_TTL, WEB_URL, _duration, _quality


class DetailsMixin:
    @staticmethod
    def _empty_details():
        return "<p style='color:gray'>Select an album or track to see its details.</p>"

    def _load_details(self):
        rows = self._selected_rows()
        if len(rows) != 1:
            self.details.setHtml(self._empty_details() if not rows else self._selection_summary(rows))
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
        self.btn_detail_load.setVisible(False)
        if prefs.safe_mode() and album_id not in self.album_cache and self._forced_details != album_id \
                and cache.get("qobuz_album", album_id, ALBUM_TTL) is None:
            # safe mode: no request just because a row was selected
            self.details.setHtml(f"<h3>{html.escape(row.get('album_title') or row['title'])}</h3>"
                                 f"<p style='color:{theme.MUTED}'>Safe mode: the track list isn't loaded until you "
                                 "ask for it.</p>")
            self.btn_detail_load.setVisible(True)
            return
        if album_id in self.album_cache:
            self._render_details(self.album_cache[album_id])
            return
        self.details.setHtml(f"<h3>{html.escape(row.get('album_title') or row['title'])}</h3>"
                             "<p style='color:gray'>Loading…</p>")

        def fetch(aid=album_id):
            meta = cache.get("qobuz_album", aid, ALBUM_TTL)
            if meta is None:
                meta = self.session.album(aid)
                cache.put("qobuz_album", aid, meta)
            return meta

        def done(meta, aid=album_id):
            self.album_cache[aid] = meta
            if self._details_for == aid:
                self._render_details(meta)

        def fail(msg, aid=album_id):
            if self._details_for == aid:
                self.details.setHtml(f"<p style='color:{theme.ERROR}'>Could not load the album: {html.escape(msg)}</p>")
        run_async(fetch, done, fail)

    def _load_details_now(self):
        self._forced_details = self._details_for
        self._load_details()

    def _selection_summary(self, rows):
        """Several rows selected: how many, how long, how many you already have."""
        rows = [r for r in (self._row(t) for t in rows) if r]
        index = self.index_provider()
        seconds = sum(int((r["item"].get("duration") or 0)) for r in rows)
        have = hist = 0
        if index is not None:
            for r in rows:
                if r["kind"] in ("albums", "tracks"):
                    state = index.status(r["album_id"], r.get("have_artist", r["artist"]), r["have_title"])
                    have += state == "library"
                    hist += state == "history"
        hires = sum(1 for r in rows if str(r.get("quality", "")).startswith("Hi-Res"))
        lines = [f"<h3>{len(rows)} selected</h3>"]
        if seconds:
            lines.append(f"<p>{_duration(seconds)} in total</p>")
        if hires:
            lines.append(f"<p>{hires} in Hi-Res</p>")
        if have:
            lines.append(f"<p style='color:{theme.OK}'>✔ {have} already in your library</p>")
        if hist:
            lines.append(f"<p style='color:{theme.MUTED}'>↓ {hist} downloaded before</p>")
        lines.append(f"<p style='color:{theme.MUTED}'>Ctrl+D downloads them all · Delete hides them</p>")
        return "".join(lines)

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
        badge = ""
        index = self.index_provider()
        if index is not None:
            state = index.status(a.get("id"), artist, a.get("title", ""))
            if state == "library":
                badge = f"<p style='color:{theme.OK};margin-top:0'><b>✔ In your library</b></p>"
            elif state == "history":
                badge = f"<p style='color:{theme.MUTED};margin-top:0'>↓ Downloaded before (not in the library)</p>"
        tracks = (a.get("tracks") or {}).get("items", [])
        multi_disc = len({t.get("media_number", 1) for t in tracks}) > 1
        lines = []
        for t in tracks:
            num = f"{t.get('media_number', 1)}.{t.get('track_number', '')}" if multi_disc else str(t.get("track_number", ""))
            name = t.get("title", "")
            if t.get("version"):
                name += f" ({t['version']})"
            mark = f" <b style='color:{theme.WARN}'>E</b>" if t.get("parental_warning") else ""
            tq = ""
            if t.get("hires_streamable") or t.get("hires"):
                tq = f"{t.get('maximum_bit_depth') or 24}/{float(t.get('maximum_sampling_rate') or 96):g}"
            lines.append(f"<tr><td style='color:gray;padding-right:6px' align='right'>{e(num)}</td>"
                         f"<td>{e(name)}{mark}</td><td style='color:gray' align='right'>{e(tq)}</td>"
                         f"<td style='color:gray' align='right'>{_duration(t.get('duration'))}</td></tr>")
        img = f"<img src='{e(cover)}' width='260'><br>" if cover else ""
        self.details.document().setDefaultStyleSheet("td { padding: 1px 2px; }")
        self._details_html = (
            f"{img}<h3 style='margin-bottom:0'>{e(title)}</h3><p style='margin-top:2px'>{e(artist)}</p>{badge}"
            f"<table>{rows}</table><h4>Tracks</h4><table width='100%'>{''.join(lines)}</table>")
        # put the cover in place first when it's already known, so the panel is drawn once
        ready = bool(cover) and self._detail_image_ready(cover)
        self.details.setHtml(self._details_html)
        if cover and not ready:
            self._load_detail_image(cover)

    def _detail_image_ready(self, url):
        from PySide6.QtGui import QTextDocument
        pix = self.covers.get(url) if isinstance(self.covers.get(url), QPixmap) else None
        if pix is None:
            data = cache.read_file("covers", url, COVER_TTL)
            loaded = QPixmap()
            if data and loaded.loadFromData(data):
                pix = self.covers[url] = loaded.scaledToWidth(260, Qt.SmoothTransformation)
        if pix is None:
            return False
        self.details.document().addResource(QTextDocument.ImageResource, QUrl(url), pix)
        return True

    def _load_detail_image(self, url):
        from PySide6.QtGui import QTextDocument
        html_now = self._details_html
        reply = self.net.get(QNetworkRequest(QUrl(url)))

        def loaded(r=reply, u=url):
            pix = QPixmap()
            data = bytes(r.readAll())
            if pix.loadFromData(data):
                cache.write_file("covers", u, data)
                pix = pix.scaledToWidth(260, Qt.SmoothTransformation)
                self.covers[u] = pix
                if self._details_html == html_now:     # still showing this album
                    self.details.document().addResource(QTextDocument.ImageResource, QUrl(u), pix)
                    self.details.setHtml(html_now)
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
