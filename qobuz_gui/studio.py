"""Which list of studio albums counts for an artist: Qobuz's (release types + MusicBrainz) or Wikipedia's.

Qt-free. `compare` lines the two lists up (and your library) so the differences are visible; `counts`
turns the rows into owned / total / missing for whichever list is chosen.
"""

from . import library, matching, prefs

QOBUZ, WIKI = "qobuz", "wikipedia"
LABEL = {QOBUZ: "Qobuz", WIKI: "Wikipedia"}


# ---- the choice, stored in prefs ----------------------------------------------------------------
class Choice:
    """A snapshot of the settings, taken on the GUI thread so a scan thread never touches QSettings."""

    def __init__(self, default=QOBUZ, per_artist=None, lang="en"):
        self.default = default if default in LABEL else QOBUZ
        self.per_artist = dict(per_artist or {})
        self.lang = lang or "en"

    @classmethod
    def load(cls):
        return cls(prefs.get_str(prefs.STUDIO_DEFAULT, QOBUZ), prefs.get_json(prefs.STUDIO_SOURCES, {}) or {},
                   prefs.get_str(prefs.WIKI_LANG, "en"))

    def source_for(self, artist):
        return self.per_artist.get(matching.norm_artist(artist), self.default)


def set_source(artist, source):
    """Remembers the choice for one artist (choosing the default again forgets the override)."""
    choice = Choice.load()
    stored = dict(choice.per_artist)
    key = matching.norm_artist(artist)
    if source == choice.default:
        stored.pop(key, None)
    else:
        stored[key] = source
    prefs.set_json(prefs.STUDIO_SOURCES, stored)
    return stored


# ---- which artist a result list is about -------------------------------------------------------------------
def artist_of(item):
    return (item.get("artist") or {}).get("name", "")


def main_artists(items, cap=5):
    """The artists behind a list of Qobuz albums, most albums first, at most `cap` (a one-artist search
    gives one; a mixed list never asks Wikipedia about dozens of artists)."""
    count, names = {}, {}
    for item in items:
        name = artist_of(item)
        key = matching.norm_artist(name)
        if key:
            count[key] = count.get(key, 0) + 1
            names.setdefault(key, name)
    return [names[k] for k in sorted(count, key=lambda k: (-count[k], k))[:cap]]


# ---- matching titles ------------------------------------------------------------------------------------
def _keys(title):
    return matching.library_keys(title)


def titles_match(a_keys, b_keys):
    if a_keys & b_keys:
        return True
    return any(library._edition_only(x, y) or library._edition_only(y, x) for x in a_keys for y in b_keys)


def find_qobuz(title, items):
    """Qobuz albums that are this title (editions and versions count), best copy first: explicit, then Hi-Res."""
    want = _keys(title)
    hits = [q for q in items if titles_match(want, matching.qobuz_keys(q))]
    hits.sort(key=lambda q: (not q.get("parental_warning"), not q.get("hires_streamable"),
                             -int(q.get("tracks_count") or 0)))
    return hits


def quality_text(item):
    if item.get("hires_streamable") or item.get("hires"):
        return f"{int(item.get('maximum_bit_depth') or 24)}-bit / {float(item.get('maximum_sampling_rate') or 96):g} kHz"
    return "16-bit / 44.1 kHz"


def _row_from_qobuz(q):
    return {"title": q.get("title", "") + (f" ({q['version']})" if q.get("version") else ""),
            "year": _year(q.get("release_date_original")), "url": f"https://play.qobuz.com/album/{q.get('id')}",
            "explicit": bool(q.get("parental_warning")), "quality": quality_text(q), "id": q.get("id")}


def _year(value):
    text = str(value or "")[:4]
    return int(text) if text.isdigit() else None


def _owned(title, owned):
    """owned: a set of matching.library_keys of your albums, or a function title -> bool."""
    if callable(owned):
        return bool(owned(title))
    return titles_match(_keys(title), owned)


# ---- comparing -------------------------------------------------------------------------------------------
def compare(qobuz_studio, wiki, qobuz_all, owned_keys):
    """Rows for every album either list calls a studio album.

    qobuz_studio: Qobuz album dicts the Qobuz method counts as studio albums.
    wiki: [{title, year}] from Wikipedia.  qobuz_all: the artist's whole Qobuz catalogue (to find a
    download for a Wikipedia album Qobuz files differently).  owned_keys: matching.library_keys of your albums.
    Each row: title, year, on_qobuz, on_wikipedia, owned, url ("" when Qobuz doesn't have it), explicit,
    quality, qobuz_kind (True when the album is on Qobuz but not as a studio album).
    """
    rows, used = [], set()
    for w in wiki:
        hits = find_qobuz(w["title"], qobuz_studio)
        in_studio = bool(hits)
        if not hits:
            hits = find_qobuz(w["title"], qobuz_all)
        best = hits[0] if hits else None
        used.update(id(h) for h in find_qobuz(w["title"], qobuz_studio))
        row = {"title": w["title"], "year": w.get("year"), "on_wikipedia": True, "on_qobuz": in_studio,
               "owned": _owned(w["title"], owned_keys), "url": "", "explicit": False, "quality": "",
               "qobuz_kind": bool(best and not in_studio), "id": None}
        if best:
            row.update(url=f"https://play.qobuz.com/album/{best.get('id')}", explicit=bool(best.get("parental_warning")),
                       quality=quality_text(best), id=best.get("id"))
            if not row["year"]:
                row["year"] = _year(best.get("release_date_original"))
        rows.append(row)
    for q in qobuz_studio:
        if id(q) in used:
            continue
        base = _row_from_qobuz(q)
        rows.append({**base, "on_wikipedia": False, "on_qobuz": True, "owned": _owned(q.get("title", ""), owned_keys),
                     "qobuz_kind": False})
    rows.sort(key=lambda r: (r["year"] or 9999, r["title"].casefold()))
    return rows


def counts(rows, source):
    """(owned, total, missing) for the chosen list. missing: [{title, year, url, explicit, quality}] (url "" when
    the album isn't on Qobuz)."""
    chosen = [r for r in rows if (r["on_wikipedia"] if source == WIKI else r["on_qobuz"])]
    missing = [{"title": r["title"], "year": str(r["year"] or ""), "url": r["url"], "explicit": r["explicit"],
                "quality": r["quality"]} for r in chosen if not r["owned"]]
    return len(chosen) - len(missing), len(chosen), missing


def newest(wiki):
    """The Wikipedia studio album with the latest year (the last listed when years tie or are missing)."""
    best = None
    for w in wiki:
        if best is None or (w.get("year") or 0) >= (best.get("year") or 0):
            best = w
    return best
