"""Official-release check against MusicBrainz.

MusicBrainz allows one request per second per client, so every request here goes through
a shared limiter, and results are cached on disk (artists for 14 days, barcodes for 30).
"""

import difflib
import re
import threading
import time
import unicodedata

import requests

from . import __version__, cache

API = "https://musicbrainz.org/ws/2"
USER_AGENT = f"qobuz-dl-gui/{__version__} ( https://github.com/danielsacosta4/qobuz-dl-gui )"
LIMITATION = ("Release types come from MusicBrainz, which allows 1 request per second. The first look at a "
              "large artist can take a few seconds; after that it's instant. Albums MusicBrainz doesn't "
              "know are marked “unverified”, not hidden.")

ARTIST_TTL = 14 * cache.DAY
BARCODE_TTL = 30 * cache.DAY

# Verdicts
OFFICIAL = "official"
BOOTLEG = "bootleg"
UNKNOWN = "unverified"

_lock = threading.Lock()
_last = [0.0]
_session = requests.Session()
_session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})


def _get(path, **params):
    params["fmt"] = "json"
    for attempt in range(4):
        with _lock:
            wait = 1.05 - (time.time() - _last[0])
            if wait > 0:
                time.sleep(wait)
            _last[0] = time.time()
        r = _session.get(f"{API}/{path}", params=params, timeout=20)
        if r.status_code == 503:          # rate limited: back off and retry
            time.sleep(2 + attempt * 2)
            continue
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()
    raise RuntimeError("MusicBrainz is busy, try again later")


# ---- matching helpers ----------------------------------------------------------
# a bracket naming a live recording ("(Live At The Palace / 1999)") is a different release, not an edition
_EDITION = re.compile(
    r"[\[(](?![^\])]*\blive\b)[^\])]*(deluxe|remaster|anniversary|expanded|edition|bonus|explicit|clean|"
    r"special|super|collector|reissue|mono|stereo|hi-?res|\d{4})[^\])]*[\])]", re.IGNORECASE)


def norm_title(title):
    t = unicodedata.normalize("NFKD", str(title or "")).encode("ascii", "ignore").decode()
    t = _EDITION.sub("", t)
    t = re.sub(r"\s+-\s+(deluxe|remaster\w*|\d+(th)? anniversary|expanded|special)\b.*$", "", t, flags=re.I)
    t = t.replace("&", " and ")
    return re.sub(r"[^a-z0-9]+", " ", t.lower()).strip()


def _similar(a, b):
    return a == b or (len(a) > 3 and difflib.SequenceMatcher(None, a, b).ratio() >= 0.92)


def kind_of(primary, secondary):
    """Release-group types -> the app's labels."""
    secondary = [s.lower() for s in (secondary or [])]
    primary = (primary or "").lower()
    if "live" in secondary:
        return "Live album"
    if "compilation" in secondary:
        return "Compilation"
    if "soundtrack" in secondary:
        return "Soundtrack"
    if "remix" in secondary or "dj-mix" in secondary:
        return "Remix"
    if secondary:
        return secondary[0].title()
    return {"album": "Studio album", "ep": "EP", "single": "Single"}.get(primary, primary.title() or "Other")


# ---- lookups -------------------------------------------------------------------
def find_artist(name):
    """Best MusicBrainz artist for a name, or None."""
    key = norm_title(name)
    cached = cache.get("mb_artist", key, ARTIST_TTL)
    if cached is not None:
        return cached or None
    data = _get("artist", query=f'artist:"{name}"', limit=5) or {}
    best = None
    for a in data.get("artists", []):
        names = [a.get("name", "")] + [al.get("name", "") for al in a.get("aliases", []) or []]
        if any(norm_title(n) == key for n in names) and int(a.get("score", 0)) >= 80:
            best = {"id": a["id"], "name": a.get("name", "")}
            break
    cache.put("mb_artist", key, best or {})
    return best


def release_groups(artist_id):
    """Official release groups of an artist (MusicBrainz's default view leaves out groups that
    only have bootleg releases)."""
    cached = cache.get("mb_groups", artist_id, ARTIST_TTL)
    if cached is not None:
        return cached
    out, offset = [], 0
    while True:
        data = _get("release-group", artist=artist_id, limit=100, offset=offset,
                    **{"release-group-status": "website-default"}) or {}
        batch = data.get("release-groups", [])
        out.extend({"title": g.get("title", ""), "primary": g.get("primary-type"),
                    "secondary": g.get("secondary-types") or [], "date": g.get("first-release-date", "")}
                   for g in batch)
        offset += len(batch)
        if not batch or offset >= data.get("release-group-count", 0):
            break
    cache.put("mb_groups", artist_id, out)
    return out


def by_barcode(barcode):
    """Release found by barcode: dict(status, kind, artist) or {} when MusicBrainz doesn't know it."""
    barcode = str(barcode or "").strip()
    if not barcode:
        return {}
    cached = cache.get("mb_barcode", barcode, BARCODE_TTL)
    if cached is not None:
        return cached
    data = _get("release", query=f"barcode:{barcode}", limit=3) or {}
    found = {}
    for rel in data.get("releases", []):
        if int(rel.get("score", 0)) < 90:
            continue
        rg = rel.get("release-group") or {}
        credit = rel.get("artist-credit") or []
        found = {"status": (rel.get("status") or "").lower(),
                 "kind": kind_of(rg.get("primary-type"), rg.get("secondary-types")),
                 "artist": credit[0].get("name", "") if credit else ""}
        break
    cache.put("mb_barcode", barcode, found)
    return found


# ---- verdicts ------------------------------------------------------------------
class ArtistCheck:
    """Classifies one artist's Qobuz albums. Network work happens in prepare() (call it from
    a worker thread); verdict() is then instant."""

    def __init__(self, artist_name):
        self.artist_name = artist_name
        self.artist = None
        self.groups = []
        self.ready = False

    def prepare(self, albums=(), progress=None, barcode_budget=40):
        self.artist = find_artist(self.artist_name)
        if self.artist:
            self.groups = release_groups(self.artist["id"])
        # Barcode lookups only for albums the title match can't place (1 request/s each)
        todo = [a for a in albums if self._match(a) is None and (a.get("upc") or a.get("barcode"))]
        for i, a in enumerate(todo[:barcode_budget]):
            if progress:
                progress(i + 1, min(len(todo), barcode_budget))
            by_barcode(a.get("upc") or a.get("barcode"))
        self.ready = True
        return self

    def _match(self, album):
        t = norm_title(album.get("title"))
        if not t:
            return None
        for g in self.groups:
            if _similar(t, norm_title(g["title"])):
                return g
        return None

    def verdict(self, album):
        """(status, kind) — status is official / bootleg / unverified; kind may be '' if unknown."""
        g = self._match(album)
        if g is not None:
            return OFFICIAL, kind_of(g["primary"], g["secondary"])
        code = album.get("upc") or album.get("barcode")
        if code:
            found = cache.get("mb_barcode", str(code).strip(), BARCODE_TTL)
            if found:
                if found.get("status") in ("bootleg", "pseudo-release"):
                    return BOOTLEG, found.get("kind", "")
                if found.get("status") in ("official", "promotion"):
                    return OFFICIAL, found.get("kind", "")
        return UNKNOWN, ""


# ---- backup rules when MusicBrainz can't decide -----------------------------------
_SUSPECT_WORDS = re.compile(r"\b(broadcast|radio|fm|transmission|tribute|karaoke|in the style of|"
                            r"performed by|interview|unauthori[sz]ed|bootleg)\b", re.IGNORECASE)
# Labels known for reissuing radio broadcasts. Kept short on purpose: hiding a label from
# the Search tab adds to your own list, which is where most of this should come from.
SUSPECT_LABELS = {"leftfield media", "laser media", "good ship funke", "zip city", "radio loyalty",
                  "hi hat", "chrome dreams"}


def suspicion(album, browsed_artist_name=None):
    """A short reason when an album looks unofficial by itself, else ''."""
    text = f"{album.get('title', '')} {album.get('version') or ''}"
    if _SUSPECT_WORDS.search(text):
        return "title suggests a broadcast, tribute or karaoke release"
    label = str((album.get("label") or {}).get("name", "")).lower().strip()
    if label and label in SUSPECT_LABELS:
        return f"label ({label}) mostly issues unofficial broadcasts"
    if browsed_artist_name:
        main = str((album.get("artist") or {}).get("name", ""))
        if main and norm_title(main) != norm_title(browsed_artist_name) and \
                norm_title(browsed_artist_name) not in norm_title(main):
            return f"main artist is {main}"
    return ""
