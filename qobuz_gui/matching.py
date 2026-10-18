"""Matching library albums to Qobuz releases.

Built from what your real library showed (Phase 0 report):
  * Navidrome joins several album artists with " • " (or ";") — each artist is looked up on its own.
  * Library titles carry qualifiers Qobuz leaves out ("(Original Mix)", "(Radio Edit)") — tried with and
    without them.
  * Qobuz keeps the version separately ("Arc" + version "Live") — tried as title and as title + version.
  * Artist pages on Qobuz include other artists' albums — a candidate is only accepted when its main
    artist is one of the library album's artists.
  * Language versions ("(Version Anglaise)") are different albums and are never folded together.
"""

import re
import unicodedata

from . import musicbrainz

ARTIST_SEPARATORS = re.compile(r"\s*(?:•|;)\s*")

# Qualifiers that describe a cut of a track rather than a different release. Remixes are NOT here:
# "(Kölsch Remix)" is a different release from the original.
_QUALIFIER = re.compile(
    r"\s*[\[(]\s*(original mix|radio edit|radio mix|extended mix|extended version|club mix|single version|"
    r"album version|single edit|edit|original version|main mix)\s*[\])]", re.IGNORECASE)
_TAG_SUFFIX = re.compile(r"\s*\[[^\]]*\]")       # "[2023, WEB]" — tagging notes, never part of the title


def norm_artist(name):
    """Unicode-aware: keeps Cyrillic, Japanese… (so non-Latin artists still compare), drops accents."""
    t = unicodedata.normalize("NFKD", str(name or ""))
    t = "".join(ch for ch in t if not unicodedata.combining(ch)).casefold()
    t = t.replace("&", " and ")
    return re.sub(r"[\W_]+", " ", t).strip()


def artist_names(artist, album=None):
    """All the names an album could be filed under: the full Navidrome artist, each part of a
    joined name, and the album's own artist list when Navidrome sends one."""
    names = []
    for n in [artist] + ARTIST_SEPARATORS.split(str(artist or "")):
        if n and n not in names:
            names.append(n)
    for a in (album or {}).get("artists") or []:
        n = a.get("name") if isinstance(a, dict) else str(a)
        if n and n not in names:
            names.append(n)
    return names


def artist_matches(candidate_artist, names):
    """True when a Qobuz album's main artist is one of the library album's artists."""
    c = norm_artist(candidate_artist)
    if not c:
        return False
    keys = {norm_artist(n) for n in names} - {""}
    if c in keys:
        return True
    # "Neil Young & Crazy Horse" vs "Neil Young • Crazy Horse": same words, joined differently
    joined = norm_artist(" ".join(names[:1]))
    if c.replace(" and ", " ") == joined.replace(" and ", " "):
        return True
    for k in keys:
        if len(k) >= 4 and (f" {k} " in f" {c} "):
            # the library artist is a named part of the Qobuz credit ("X & Y", "X feat. Y")
            return True
    return False


def title_key(title):
    """Comparable form of a title: tag suffixes and edition words removed, version words kept."""
    return musicbrainz.norm_title(_TAG_SUFFIX.sub("", str(title or "")))


def library_keys(name):
    base = _TAG_SUFFIX.sub("", str(name or ""))
    keys = {title_key(base), title_key(_QUALIFIER.sub("", base))}
    return keys - {""}


def qobuz_keys(album):
    title = album.get("title") or ""
    version = album.get("version") or ""
    keys = {title_key(title), title_key(_QUALIFIER.sub("", title))}
    if version:
        keys.add(title_key(f"{title} ({version})"))
    return keys - {""}


def candidates(album_name, qobuz, names=None):
    """Qobuz releases that are this library album. With `names`, only releases whose main artist
    is one of them are accepted."""
    want = library_keys(album_name)
    out = []
    for q in qobuz:
        if not (want & qobuz_keys(q)):
            continue
        if names is not None and not artist_matches((q.get("artist") or {}).get("name", ""), names):
            continue
        out.append(q)
    return out


def merge_discographies(lists):
    seen, out = set(), []
    for items in lists:
        for q in items:
            if q.get("id") in seen:
                continue
            seen.add(q.get("id"))
            out.append(q)
    return out
