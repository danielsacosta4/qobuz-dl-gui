"""Release type, edition and explicit handling for Qobuz album data, plus the library letter rule."""

import re
import unicodedata

KINDS = ["Studio album", "Live album", "Compilation", "EP", "Single", "Soundtrack", "Other"]


def filter_kind(kind):
    """Maps any release type to one of the filter boxes."""
    return kind if kind in KINDS else "Other"

_COMPILATION_WORDS = ("best of", "greatest hits", "anthology", "collection", "compilation",
                      "the essential", "essentials", "the hits", "hits collection")
_LIVE_RE = re.compile(r"(^|[\s(\[\-])live($|[\s)\]\-:,])", re.IGNORECASE)


def release_kind(item):
    """Studio album, Live album, Compilation, EP or Single.

    Follows the same reasoning as qobuz-dl's own release filter: words in the title or
    edition win over Qobuz's tag (which is often wrong), then the track count settles
    album / EP / single.
    """
    title = str(item.get("title") or "").lower()
    version = str(item.get("version") or "").lower()
    raw = str(item.get("release_type") or item.get("product_type") or "").lower()
    tracks = int(item.get("tracks_count") or 0)
    duration = int(item.get("duration") or 0)

    if _LIVE_RE.search(version) or "(live" in title or "- live" in title or "[live" in title or raw == "live":
        return "Live album"
    if raw == "compilation" or any(w in title or w in version for w in _COMPILATION_WORDS):
        return "Compilation"
    if " ep" in title.replace("(", " ").replace("[", " ") or version == "ep":
        return "EP"

    if raw in ("single",):
        return "EP" if tracks >= 4 else "Single"
    if raw in ("ep", "epmini"):
        return "Single" if 1 <= tracks <= 3 else "EP"
    if raw == "album":
        return "Single" if 1 <= tracks <= 3 and duration < 1200 else "Studio album"
    # No tag: decide on size
    if 1 <= tracks <= 3:
        return "Single"
    if 4 <= tracks <= 6 and (not duration or duration < 1740):
        return "EP"
    return "Studio album"


def edition(item):
    """The edition note (Deluxe, Remastered, Live at …), or '' for the standard release."""
    return str(item.get("version") or "").strip()


def is_explicit(item):
    return bool(item.get("parental_warning"))


def _norm(text):
    text = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode()
    text = re.sub(r"[\[(][^\])]*(explicit|clean|edited)[^\])]*[\])]", "", text, flags=re.IGNORECASE)
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _artist(item):
    return (item.get("artist") or {}).get("name") or (item.get("performer") or {}).get("name") or ""


def release_key(item):
    return (_norm(_artist(item)), _norm(item.get("title")), _norm(item.get("version")))


def prefer_explicit(items):
    """Drops a clean release when the explicit version of the same release is in the list.

    Releases that only exist in a clean version are kept.
    """
    explicit_keys = {release_key(i) for i in items if is_explicit(i)}
    return [i for i in items if is_explicit(i) or release_key(i) not in explicit_keys]


_SPECIAL = str.maketrans({"Ø": "O", "ø": "o", "Æ": "A", "æ": "a", "Œ": "O", "œ": "o", "ß": "s",
                          "Ł": "L", "ł": "l", "Đ": "D", "đ": "d", "Þ": "T", "þ": "t"})


def letter(name):
    """Library letter folder for an artist: first letter without accents, '#' otherwise.

    "The Beatles" goes under T, like the rest of the library.
    """
    stripped = unicodedata.normalize("NFKD", str(name or "").strip().translate(_SPECIAL))
    for ch in stripped:
        if unicodedata.combining(ch):
            continue
        up = ch.upper()
        if "A" <= up <= "Z":
            return up
        return "#"
    return "#"


def _quality_rank(item):
    return (1 if item.get("hires_streamable") or item.get("hires") else 0,
            float(item.get("maximum_bit_depth") or 0),
            float(item.get("maximum_sampling_rate") or 0),
            int(item.get("tracks_count") or 0))


def dedupe(items):
    """Keeps one copy of releases Qobuz lists more than once (same title, edition and
    explicit flag — e.g. a CD-quality and a Hi-Res upload), preferring the best quality.
    The original order is kept."""
    best = {}
    for i in items:
        key = release_key(i) + (is_explicit(i),)
        if key not in best or _quality_rank(i) > _quality_rank(best[key]):
            best[key] = i
    keep = {id(v) for v in best.values()}
    return [i for i in items if id(i) in keep]
