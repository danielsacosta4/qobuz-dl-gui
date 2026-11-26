"""Search tab, part: which list decides what a studio album is (Qobuz or Wikipedia), per artist.

A mixin of SearchTab: it uses the tab's widgets and state (current, wikis, studio_box, status …).
"""

from PySide6.QtCore import QThread, Signal

from . import classify, matching, musicbrainz, prefs, studio, wikipedia
from .client import run_async
from .search_common import WEB_URL
from .studio_dialog import EMPTY_LIST, StudioCompare, no_list_found

MAX_WIKI_ARTISTS = 5          # Wikipedia lists looked up per result list (about a second or three each)


class WikiBatch(QThread):
    """Loads Wikipedia's studio albums for a few artists, one at a time (the module throttles requests)."""
    loaded = Signal(str, object, str)      # artist, found | None, error

    def __init__(self, artists, choice):
        super().__init__()
        self.artists, self.choice = list(artists), choice      # a studio.Choice snapshot from the GUI thread
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        for name in self.artists:
            if self._stop:
                return
            try:
                found = self.choice.albums_for(name)
            except wikipedia.WikipediaError as e:
                self.loaded.emit(name, None, str(e))
                return                      # Wikipedia is unreachable: don't try the rest
            self.loaded.emit(name, found, "")


class StudioListMixin:
    @staticmethod
    def _artist_of(item):
        return studio.artist_of(item)

    @staticmethod
    def _main_artists(items, cap=MAX_WIKI_ARTISTS):
        return studio.main_artists(items, cap)

    def _list_artist(self):
        """The artist the Studio box and Compare… are about: the page's artist, or the main one of a list."""
        if not self.current:
            return None
        items, kind, _heading, artist = self.current
        if artist is not None:
            return artist
        return (self._main_artists(items, 1) or [None])[0] if kind == "albums" else None

    def _wiki_entry(self, artist):
        """Wikipedia's list for an artist when it is chosen for them and loaded, else None."""
        entry = self.wikis.get(matching.norm_artist(artist))
        if entry and entry.get("found") and self._choice.source_for(artist) == studio.WIKI:
            return entry
        return None

    def _entry_for_item(self, item):
        page = self.current[3] if self.current else None
        return self._wiki_entry(page if page is not None else self._artist_of(item))

    def _in_wiki(self, item, entry):
        key = (id(entry), item.get("id"))
        if key not in self._wiki_kind:
            have = matching.qobuz_keys(item)
            self._wiki_kind[key] = any(studio.titles_match(wk, have) for wk in entry["keys"])
        return self._wiki_kind[key]

    def _ensure_wikis(self):
        """Starts loading the Wikipedia lists the current results need (artists set to Wikipedia)."""
        if not self.current:
            return
        items, kind, _heading, artist = self.current
        if kind != "albums":
            return
        artists = [artist] if artist is not None else self._main_artists(items)
        need = [a for a in artists if self._choice.source_for(a) == studio.WIKI
                and matching.norm_artist(a) not in self.wikis and matching.norm_artist(a) not in self._wiki_pending]
        if not need:
            return
        if self._wiki_batch is not None:
            self._wiki_batch.stop()
        self._wiki_pending |= {matching.norm_artist(a) for a in need}
        batch = WikiBatch(need, self._choice)
        batch.loaded.connect(lambda name, found, error, b=batch: self._wiki_loaded(b, name, found, error))
        batch.finished.connect(lambda b=batch: self._wiki_batch_done(b))
        self._wiki_batch = batch
        self.status("Reading studio albums from Wikipedia…")
        batch.start()

    def _wiki_loaded(self, batch, name, found, error):
        key = matching.norm_artist(name)
        self._wiki_pending.discard(key)
        if error:
            self.wikis[key] = {"found": None, "keys": [], "error": error}
            self.status(f"{error} — {name} keeps Qobuz's types.", error=True)
        elif found is None:
            self.wikis[key] = {"found": None, "keys": []}
            self.status(f"Wikipedia has no usable list of studio albums for {name} — Qobuz's types are used.",
                        error=True)
        else:
            self._set_wiki(name, found)
            self.status(f"Studio albums for {name} come from Wikipedia ({found['page']}).")
        if batch is self._wiki_batch and self.current:
            self._render_current(keep_scroll=True)

    def _wiki_batch_done(self, batch):
        if batch is self._wiki_batch:
            self._wiki_batch = None
            self._wiki_pending.clear()

    def _set_wiki(self, artist, found):
        self.wikis[matching.norm_artist(artist)] = {
            "found": found, "keys": [matching.library_keys(w["title"]) for w in found["albums"]]}

    def _forget_wiki_errors(self):
        for key in [k for k, e in self.wikis.items() if e.get("error")]:
            del self.wikis[key]

    def _studio_chosen(self, _index):
        artist = self._list_artist()
        if artist is None:
            return
        studio.set_source(artist, self.studio_box.currentData())
        self._choice = studio.Choice.load()
        self._render_current(keep_scroll=True)

    def _qobuz_studio(self, items, artist):
        """Albums the Qobuz method (release type + MusicBrainz) counts as studio albums."""
        out = []
        for i in classify.dedupe(items):
            if self._kind_qobuz(i) != "Studio album":
                continue
            if self._mb_ready() and self.mb_check.verdict(i)[0] != musicbrainz.OFFICIAL:
                continue
            if not self._mb_ready() and musicbrainz.suspicion(i, artist):
                continue
            out.append(i)
        return out

    def _compare(self):
        artist = self._list_artist()
        if artist is None:
            return
        items, _kind, _heading, page = self.current
        partial = page is None            # a flat search list: it may miss releases the search didn't return
        mine = [i for i in items if matching.artist_matches(self._artist_of(i), [artist])] if partial else items
        artist_id = next((i.get("artist", {}).get("id") for i in mine if (i.get("artist") or {}).get("id")), None)

        def show(found):
            self.status("")
            index = self.index_provider()
            owned = (lambda t: index.status(None, artist, t) == "library") if index is not None else (lambda t: False)
            rows = studio.compare(self._qobuz_studio(mine, artist), found["albums"], mine, owned)
            dlg = StudioCompare(self, artist, rows, self._choice.source_for(artist), found["page"], partial=partial,
                                override=self._choice.override_for(artist), removed=found.get("removed") or ())

            def corrected(action):
                studio.edit_override(artist, action)
                self._choice = studio.Choice.load()
                self.wikis.pop(matching.norm_artist(artist), None)     # read the list again, corrections included
                self._compare()
            dlg.overrideRequested.connect(corrected)

            def chosen(source):
                studio.set_source(artist, source)
                self._choice = studio.Choice.load()
                self.studio_box.setCurrentIndex(self.studio_box.findData(source))
                if source == studio.WIKI:
                    self._set_wiki(artist, found)
                self._render_current(keep_scroll=True)
            dlg.sourceChosen.connect(chosen)
            dlg.downloadRequested.connect(lambda picked: self._queue_picked(artist, picked))
            if artist_id is not None:
                dlg.openArtistPage.connect(lambda: (dlg.accept(), self.browse_artist(artist_id, artist)))
            dlg.exec()

        entry = self.wikis.get(matching.norm_artist(artist))
        if entry and entry.get("found"):
            show(entry["found"])
            return
        choice = self._choice
        self.status(f"Reading {artist}'s studio albums from Wikipedia…")

        def done(found):
            if found is None:
                if no_list_found(self, artist, choice):
                    self._choice = studio.Choice.load()
                    self._compare()
                    return
                show(EMPTY_LIST)                 # nothing on Wikipedia: the window still opens, so you can add albums
                return
            if choice.source_for(artist) == studio.WIKI:
                self._set_wiki(artist, found)    # the table follows the list as it is now
                self._render_current(keep_scroll=True)
            show(found)
        run_async(lambda: choice.albums_for(artist), done,
                  lambda m: self.status(f"Couldn't read Wikipedia: {m}", error=True))

    def _queue_picked(self, artist, picked):
        """Albums ticked in Compare: [(title, url)]. A Wikipedia-only album has no Qobuz link yet, so it is
        looked up now (one search each), because you asked to download it."""
        ready = [dict(src=u, title=f"{artist} — {t}", artist=artist, album=t, album_id=None) for t, u in picked if u]
        lookup = [t for t, u in picked if not u]
        if ready:
            self._queue(ready)
        if not lookup:
            return
        if prefs.safe_mode() and len(lookup) > prefs.SAFE_LOOKUPS:
            self.status(f"Safe mode looks up {prefs.SAFE_LOOKUPS} albums on Qobuz per click: press Download again "
                        f"for the other {len(lookup) - prefs.SAFE_LOOKUPS}.")
            lookup = lookup[:prefs.SAFE_LOOKUPS]
        self.status(f"Looking up {len(lookup)} album{'s' if len(lookup) != 1 else ''} on Qobuz…")

        def work():
            return [(t, self.session.find_album(artist, t)) for t in lookup]

        def done(found):
            entries = [dict(src=f"{WEB_URL}album/{al['id']}", title=f"{artist} — {t}", artist=artist, album=t,
                            album_id=al["id"]) for t, al in found if al]
            if entries:
                self._queue(entries)
            lost = [t for t, al in found if not al]
            if lost:
                self.status("Not found on Qobuz: " + "; ".join(lost[:4]) + (" …" if len(lost) > 4 else ""), error=True)
        run_async(work, done, lambda m: self.status(f"Couldn't look the albums up on Qobuz: {m}", error=True))

    def _kind(self, item):
        """Release type. For an artist set to Wikipedia, its list decides what a studio album is."""
        base = self._kind_qobuz(item)
        entry = self._entry_for_item(item)
        if entry is not None:
            if self._in_wiki(item, entry):
                return "Studio album"
            return "Other" if base == "Studio album" else base
        return base
