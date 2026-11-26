"""Studio albums from Wikipedia's discography pages (MediaWiki API), as an alternative to Qobuz's release types.

Free of Qt. Polite by design: a descriptive User-Agent, one request per second, a handful of requests per
artist, answers cached for 30 days (7 for "not found"). Pages are read with the standard library's HTML parser,
from the table or list in the artist's *Studio albums* section.
"""

import re
import threading
import time
from html.parser import HTMLParser

from . import __version__, cache, matching

GAP = 1.0                                   # seconds between requests
MAX_REQUESTS = 5                            # per artist: find the page, list its sections, read one
FOUND_TTL = 30 * cache.DAY
MISSING_TTL = 7 * cache.DAY
USER_AGENT = f"qobuz-dl-gui/{__version__} (https://github.com/danielsacosta4/qobuz-dl-gui; personal music library tool)"
LANGUAGES = [("en", "English"), ("pt", "Português"), ("es", "Español"), ("fr", "Français"), ("de", "Deutsch")]

_STUDIO = {
    "en": r"studio albums?", "pt": r"[áa]lbuns? de est[úu]dio|[áa]lbuns? de carreira",
    "es": r"[áa]lbumes? de estudio", "fr": r"albums? studio", "de": r"studioalben|studioalbum",
}
_SUFFIXES = {
    "en": ["discography"], "pt": ["discografia"], "es": ["discografía"], "fr": ["discographie"],
    "de": ["diskografie", "diskographie"],
}
_QUALIFIERS = ["", " (band)", " (singer)", " (musician)", " (rapper)", " (group)", " (duo)", " (American band)",
               " (British band)", " (singer-songwriter)", " (banda)", " (cantor)", " (groupe)"]
_HEADER_WORDS = {"title", "titulo", "título", "album", "álbum", "year", "ano", "details", "album details",
                 "peak chart positions", "certifications", "sales", "released", "label", "formats"}

_lock = threading.Lock()
_last = 0.0


class WikipediaError(Exception):
    pass


def _get(lang, params):
    """One throttled MediaWiki API call (replaced in tests)."""
    import requests
    global _last
    with _lock:
        wait = GAP - (time.time() - _last)
        if wait > 0:
            time.sleep(wait)
        _last = time.time()
    try:
        r = requests.get(f"https://{lang}.wikipedia.org/w/api.php", params={"format": "json", "formatversion": 2,
                                                                          **params},
                         headers={"User-Agent": USER_AGENT}, timeout=25)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        raise WikipediaError(f"Couldn't reach Wikipedia ({str(e)[:100] or e.__class__.__name__})") from e


# ---- reading the section's HTML ---------------------------------------------------------------
_SKIP_CLASS = re.compile(r"reflist|references|navbox|mw-references|noprint|hatnote|reference")
_YEAR = re.compile(r"\b(19[5-9]\d|20[0-4]\d)\b")
_FOOTNOTE = re.compile(r"\s*\[[^\]]{1,12}\]\s*$")


def _clean(text):
    text = re.sub(r"\s+", " ", text or "").strip()
    text = _FOOTNOTE.sub("", text)
    return text.strip("  \"'“”‘’")


class _SectionParser(HTMLParser):
    """Collects (title, year) from the tables and lists of a discography section."""

    VOID = {"br", "img", "hr", "meta", "link", "input", "wbr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.table_entries = []
        self.list_entries = []
        self._skip = 0                 # >0 while inside a subtree we ignore (references, notes, sup)
        self._skip_tag = ""
        self._row = None               # {"cells": [...]} while inside a <tr>
        self._cell = None              # {"text": [], "italic": [], "in_i": 0, "i_buf": [], "head": bool}
        self._li = None
        self._in_table = 0

    # -- helpers
    @staticmethod
    def _holder(head=False):
        return {"text": [], "italic": [], "in_i": 0, "i_buf": [], "head": head}

    def _finish_row(self):
        row, self._row = self._row, None
        cells = row["cells"] if row else []
        if len(cells) < 2:
            return                                   # notes ("— did not chart") and spacer rows
        if all(c["head"] for c in cells) and not any(c["italic"] for c in cells):
            return                                   # header rows
        pick = next((c for c in cells[:2] if c["italic"]), None)
        if pick is not None:
            title = _clean(pick["italic"][0])
        elif cells[0]["head"]:
            title = _clean("".join(cells[0]["text"]))   # a title cell without italics
            pick = cells[0]
        else:
            return
        if not title or not re.search(r"[^\W\d_]", title) or title.casefold() in _HEADER_WORDS:
            return
        rest = " ".join(_clean("".join(c["text"])) for c in cells if c is not pick)
        m = re.search(r"(?:released|lançado|publicado|sortie|veröffentlicht)[^0-9]{0,24}(?:[0-9]{1,2}[^0-9]{1,12})?"
                      r"(19[5-9]\d|20[0-4]\d)", rest, re.I)
        year = int(m.group(1)) if m else None
        if year is None:
            m = _YEAR.search(rest)
            year = int(m.group(1)) if m else None
        self.table_entries.append((title, year))

    # -- parser events
    def handle_starttag(self, tag, attrs):
        if self._skip:
            if tag == self._skip_tag:
                self._skip += 1
            return
        a = dict(attrs)
        cls = a.get("class") or ""
        if tag in ("sup", "style", "script") or (_SKIP_CLASS.search(cls) and tag not in self.VOID):
            self._skip, self._skip_tag = 1, tag
            return
        if tag == "table":
            self._in_table += 1
        elif tag == "tr" and self._in_table:
            self._row = {"cells": []}
        elif tag in ("th", "td") and self._row is not None:
            self._cell = self._holder(head=tag == "th")
        elif tag == "li" and not self._in_table:
            self._li = self._holder()
        elif tag in ("i", "em"):
            holder = self._cell or self._li
            if holder is not None:
                holder["in_i"] += 1

    def handle_endtag(self, tag):
        if self._skip:
            if tag == self._skip_tag:
                self._skip -= 1
            return
        if tag in ("i", "em"):
            holder = self._cell or self._li
            if holder is not None and holder["in_i"]:
                holder["in_i"] -= 1
                if not holder["in_i"] and holder["i_buf"]:
                    holder["italic"].append("".join(holder["i_buf"]))
                    holder["i_buf"] = []
        elif tag in ("th", "td") and self._cell is not None:
            if self._row is not None:
                self._row["cells"].append(self._cell)
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._cell is not None:
                self._row["cells"].append(self._cell)
                self._cell = None
            self._finish_row()
        elif tag == "table" and self._in_table:
            self._in_table -= 1
        elif tag == "li" and self._li is not None:
            li, self._li = self._li, None
            if li["italic"]:
                title = _clean(li["italic"][0])
                tail = "".join(li["text"])
                m = re.search(r"\([^()]*?(19[5-9]\d|20[0-4]\d)\)", tail) or re.match(r"\s*(19[5-9]\d|20[0-4]\d)\b", tail) \
                    or _YEAR.search(tail)
                if title and re.search(r"[^\W\d_]", title):
                    self.list_entries.append((title, int(m.group(1)) if m else None))

    def handle_data(self, data):
        if self._skip:
            return
        holder = self._cell or self._li
        if holder is None:
            return
        holder["text"].append(data)
        if holder["in_i"]:
            holder["i_buf"].append(data)


def parse_section(html_text):
    """[{title, year}] from a discography section's HTML: its tables, or its lists when it has no table."""
    p = _SectionParser()
    p.feed(html_text or "")
    p.close()
    seen, out = set(), []
    for title, year in (p.table_entries or p.list_entries):
        key = matching.title_key(title)
        if key and key not in seen:
            seen.add(key)
            out.append({"title": title, "year": year})
    return out


# ---- finding the right page and section ---------------------------------------------------------------
def _candidates(artist, lang):
    base = artist.strip()
    suffixes = _SUFFIXES.get(lang, _SUFFIXES["en"])
    disco = [f"{base}{q} {s}" for s in suffixes for q in _QUALIFIERS]
    main = [f"{base}{q}" for q in _QUALIFIERS]
    return disco, main


def _existing(lang, titles):
    """The titles that are real, non-disambiguation pages, in the order given (one API call)."""
    data = _get(lang, {"action": "query", "titles": "|".join(titles[:50]), "redirects": 1, "prop": "pageprops",
                       "ppprop": "disambiguation"})
    q = data.get("query") or {}
    redirect = {r.get("from"): r.get("to") for r in q.get("redirects") or []}
    alive = {p.get("title") for p in q.get("pages") or []
             if not p.get("missing") and not p.get("invalid") and "disambiguation" not in (p.get("pageprops") or {})}
    out = []
    for t in titles:
        real = redirect.get(t, t)
        if real in alive and real not in out:
            out.append(real)
    return out


def _studio_section(lang, page):
    data = _get(lang, {"action": "parse", "page": page, "prop": "sections", "redirects": 1})
    pattern = re.compile(rf"^\s*(?:{_STUDIO.get(lang, _STUDIO['en'])})\s*$", re.I)
    for s in (data.get("parse") or {}).get("sections") or []:
        line = re.sub(r"<[^>]+>", "", str(s.get("line") or ""))
        if pattern.match(line):
            return s.get("index")
    return None


def _section_html(lang, page, index):
    data = _get(lang, {"action": "parse", "page": page, "prop": "text", "section": index, "redirects": 1,
                       "disablelimitreport": 1, "disableeditsection": 1})
    return ((data.get("parse") or {}).get("text")) or ""


def _find(artist, lang):
    disco, main = _candidates(artist, lang)
    pages = _existing(lang, disco + main)
    def is_disco(p):
        return any(w in p.casefold() for w in ("discograph", "discograf", "diskograph", "diskograf"))
    tried = 0
    for page in sorted(pages, key=lambda p: 0 if is_disco(p) else 1):
        if tried >= MAX_REQUESTS - 2:
            break
        tried += 1
        index = _studio_section(lang, page)
        if index is None:
            continue
        albums = parse_section(_section_html(lang, page, index))
        if albums:
            return {"albums": albums, "page": page, "lang": lang}
    return None


def studio_albums(artist, lang="en", refresh=False):
    """{"albums": [{title, year}], "page": title, "lang"} for an artist, or None when Wikipedia has no
    usable *Studio albums* section. Raises WikipediaError when Wikipedia can't be reached."""
    key = f"{lang}|{matching.norm_artist(artist)}"
    if not refresh:
        hit = cache.get("wiki_studio", key, FOUND_TTL)
        if hit is not None:
            if not hit.get("missing"):
                return hit
            if cache.get("wiki_studio", key, MISSING_TTL) is not None:
                return None
    found = _find(artist, lang)
    cache.put("wiki_studio", key, found or {"missing": True})
    return found


# ---- a page you name ----------------------------------------------------------------------------------------
_WIKI_URL = re.compile(r"https?://([a-z\-]+)\.(?:m\.)?wikipedia\.org/wiki/([^?#]+)", re.IGNORECASE)


def page_ref(text, lang="en"):
    """(language, page title) from a Wikipedia address or a plain page title."""
    from urllib.parse import unquote
    text = str(text or "").strip()
    m = _WIKI_URL.match(text)
    if m:
        return m.group(1).lower(), unquote(m.group(2)).replace("_", " ").strip()
    return lang, text.replace("_", " ").strip()


def studio_albums_from_page(title, lang="en", refresh=False):
    """Like studio_albums, but for the page you name. None when the page doesn't exist, is a disambiguation
    page or has no usable *Studio albums* section. Raises WikipediaError when Wikipedia can't be reached."""
    key = f"{lang}|page|{str(title).casefold()}"
    if not refresh:
        hit = cache.get("wiki_studio", key, FOUND_TTL)
        if hit is not None:
            if not hit.get("missing"):
                return hit
            if cache.get("wiki_studio", key, MISSING_TTL) is not None:
                return None
    found = None
    pages = _existing(lang, [title])
    if pages:
        index = _studio_section(lang, pages[0])
        albums = parse_section(_section_html(lang, pages[0], index)) if index is not None else []
        if albums:
            found = {"albums": albums, "page": pages[0], "lang": lang}
    cache.put("wiki_studio", key, found or {"missing": True})
    return found
