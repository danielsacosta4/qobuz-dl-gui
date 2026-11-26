"""Which list of studio albums counts for an artist: Qobuz's (release types + MusicBrainz) or Wikipedia's.

Qt-free. `compare` lines the two lists up (and your library) so the differences are visible; `counts`
turns the rows into owned / total / missing for whichever list is chosen.
"""

from . import library, matching, prefs, wikipedia

QOBUZ, WIKI = "qobuz", "wikipedia"
LABEL = {QOBUZ: "Qobuz", WIKI: "Wikipedia"}


# ---- the choice, stored in prefs ----------------------------------------------------------------
class Choice:
    """A snapshot of the settings, taken on the GUI thread so a scan thread never touches QSettings."""

    def __init__(self, default=QOBUZ, per_artist=None, lang="en", overrides=None):
        self.default = default if default in LABEL else QOBUZ
        self.per_artist = dict(per_artist or {})
        self.lang = lang or "en"
        self.overrides = dict(overrides or {})      # your corrections to Wikipedia's lists, per artist

    @classmethod
    def load(cls):
        return cls(prefs.get_str(prefs.STUDIO_DEFAULT, QOBUZ), prefs.get_json(prefs.STUDIO_SOURCES, {}) or {},
                   prefs.get_str(prefs.WIKI_LANG, "en"), prefs.get_json(prefs.STUDIO_OVERRIDES, {}) or {})

    def source_for(self, artist):
        return self.per_artist.get(matching.norm_artist(artist), self.default)

    def override_for(self, artist):
        return self.overrides.get(matching.norm_artist(artist)) or {}

    def albums_for(self, artist, lookup_name=None, refresh=False):
        """Wikipedia's studio albums for an artist with your corrections applied: {"albums", "page", "lang",
        "edited", "removed"}, or None when there is no list. `lookup_name` is the name to search Wikipedia
        for when it differs from the artist's library name. Raises wikipedia.WikipediaError when Wikipedia
        can't be reached. Makes network requests: call it from a worker thread."""
        o = self.override_for(artist)
        if o.get("page"):
            found = wikipedia.studio_albums_from_page(o["page"], o.get("lang") or self.lang, refresh)
        else:
            found = wikipedia.studio_albums(lookup_name or artist, self.lang, refresh)
        return apply_overrides(found, o, self.lang)


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


# ---- your corrections to a Wikipedia list ---------------------------------------------------------------------
def apply_overrides(found, o, lang="en"):
    """Wikipedia's answer with your added and removed albums applied. `found` may be None (no list on
    Wikipedia): albums you added then make a list of their own."""
    if not o or not (o.get("add") or o.get("remove")):
        if found is not None and o and o.get("page"):
            return {**found, "edited": True, "removed": []}
        return found
    albums = [dict(a) for a in (found or {}).get("albums") or []]
    removed = []
    for title in o.get("remove") or []:
        keys = _keys(title)
        hit = [a for a in albums if titles_match(_keys(a["title"]), keys)]
        if hit:
            removed.append(hit[0]["title"])
            albums = [a for a in albums if a not in hit]
    for extra in o.get("add") or []:
        keys = _keys(extra.get("title", ""))
        if keys and not any(titles_match(_keys(a["title"]), keys) for a in albums):
            albums.append({"title": extra["title"], "year": extra.get("year"), "added": True})
    if not albums and found is None:
        return None
    if any(a.get("added") for a in albums):
        albums.sort(key=lambda a: a.get("year") or 9999)        # stable: Wikipedia's order is kept within a year
    return {"albums": albums, "page": (found or {}).get("page") or "your own list",
            "lang": (found or {}).get("lang") or lang, "edited": True, "removed": removed}


def edited_override(o, action):
    """A copy of one artist's corrections after an action: {"op": "add", "title", "year"} · {"op": "remove",
    "title"} · {"op": "restore", "title"} · {"op": "page", "page", "lang"} · {"op": "reset"}."""
    o = {"page": o.get("page", ""), "lang": o.get("lang", ""), "add": [dict(a) for a in o.get("add") or []],
         "remove": list(o.get("remove") or [])}
    op, title = action.get("op"), str(action.get("title") or "").strip()
    keys = _keys(title) if title else set()

    def same(other):
        return titles_match(_keys(other), keys)
    if op == "reset":
        return {}
    if op == "page":
        o["page"], o["lang"] = str(action.get("page") or "").strip(), str(action.get("lang") or "")
    elif op == "add" and keys:
        o["remove"] = [t for t in o["remove"] if not same(t)]
        if not any(same(a["title"]) for a in o["add"]):
            o["add"].append({"title": title, "year": action.get("year")})
    elif op == "remove" and keys:
        if any(same(a["title"]) for a in o["add"]):
            o["add"] = [a for a in o["add"] if not same(a["title"])]       # it was yours: just take it back out
        elif not any(same(t) for t in o["remove"]):
            o["remove"].append(title)
    elif op == "restore" and keys:
        o["remove"] = [t for t in o["remove"] if not same(t)]
    if not (o["page"] or o["add"] or o["remove"]):
        return {}
    return o


def edit_override(artist, action):
    """Applies an action to one artist's corrections and saves them. Returns the artist's corrections."""
    stored = dict(prefs.get_json(prefs.STUDIO_OVERRIDES, {}) or {})
    key = matching.norm_artist(artist)
    new = edited_override(stored.get(key) or {}, action)
    if new:
        stored[key] = new
    else:
        stored.pop(key, None)
    prefs.set_json(prefs.STUDIO_OVERRIDES, stored)
    return new


def parse_title_year(text):
    """("Title", 1999) from "Title (1999)", "Title, 1999" or "Title 1999"; the year is None when there is none."""
    import re
    text = str(text or "").strip()
    m = re.match(r"^(.*?)[\s,]*[\[(]?\b(19[5-9]\d|20[0-4]\d)\b[\])]?\s*$", text)
    if m and m.group(1).strip():
        return m.group(1).strip(), int(m.group(2))
    return text, None


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
               "added": bool(w.get("added")),
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


# ---- a list of what is missing, to save --------------------------------------------------------------------
EXPORT_COLUMNS = ["Artist", "Album", "Year", "List", "Qobuz link"]


def missing_rows(completions):
    """One row per missing album, for doctor.ArtistCompletion-like objects: [artist, album, year, list, link]."""
    rows = []
    for c in sorted(completions, key=lambda c: c.artist.casefold()):
        for m in c.missing:
            rows.append([c.artist, m.get("title", ""), str(m.get("year") or ""),
                         LABEL.get(getattr(c, "source", QOBUZ), "Qobuz"), m.get("url") or ""])
    return rows


def missing_csv(rows):
    import csv
    import io
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(EXPORT_COLUMNS)
    writer.writerows(rows)
    return out.getvalue()


def missing_text(rows):
    """Plain text, grouped by artist: easy to read or print."""
    lines, last = [], None
    for artist, album, year, _source, _link in rows:
        if artist != last:
            if lines:
                lines.append("")
            lines.append(artist)
            last = artist
        lines.append(f"  - {album}" + (f" ({year})" if year else ""))
    return "\n".join(lines) + ("\n" if lines else "")
