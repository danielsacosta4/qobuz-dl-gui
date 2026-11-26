"""Discover: artists you don't have yet that your own Navidrome server relates to the ones you do, optionally
weighted by what you play. Free of Qt, and it never contacts Qobuz: similar artists come from Navidrome
(getArtistInfo2, which needs Last.fm set up on the server). Qobuz is only asked later, when you press a button.
"""

import math
import time
from collections import Counter

from . import classify, matching

SKIP_ARTISTS = {"various artists", "various", "va", "unknown artist"}
NO_DATA = ("Navidrome returned no similar artists. It gets them from Last.fm, so the server needs a Last.fm API "
           "key (Navidrome setting LastFM.ApiKey) and your artists need to be known there.")


class DiscoverError(Exception):
    pass


# ---- your library -----------------------------------------------------------------------
def library_artists(albums):
    """(norm name -> display name, norm name -> number of albums) for Navidrome's album list.
    Joined names ("A • B") count for each artist, never as one."""
    names, counts = {}, Counter()
    for al in albums:
        for n in matching.artist_names(al.get("artist", ""), al):
            if matching.ARTIST_SEPARATORS.search(n):
                continue
            key = matching.norm_artist(n)
            if key and key not in SKIP_ARTISTS:
                names.setdefault(key, n)
                counts[key] += 1
    return names, counts


def artist_ids(albums):
    """norm name -> Navidrome artist id, for albums by a single artist."""
    out = {}
    for al in albums:
        name, aid = al.get("artist", ""), al.get("artistId")
        if aid and not matching.ARTIST_SEPARATORS.search(name):
            out.setdefault(matching.norm_artist(name), aid)
    return out


def artist_weights(frequent, starred):
    """norm artist -> how much you listen to them, from the most-played and starred album lists.
    Play counts are used when the server sends them, otherwise the position in the list."""
    w = Counter()
    n = len(frequent)
    for i, al in enumerate(frequent):
        plays = al.get("playCount") or 0
        value = math.log1p(float(plays)) if plays else (n - i) / max(n, 1) * 2
        for name in matching.artist_names(al.get("artist", ""), al):
            if not matching.ARTIST_SEPARATORS.search(name):
                w[matching.norm_artist(name)] += value
    for al in starred:
        for name in matching.artist_names(al.get("artist", ""), al):
            if not matching.ARTIST_SEPARATORS.search(name):
                w[matching.norm_artist(name)] += 2.0
    w.pop("", None)
    return dict(w)


def pick_sources(names, counts, weights, limit):
    """The library artists to ask about, most-played first (then those with the most albums)."""
    order = sorted(names, key=lambda k: (-weights.get(k, 0.0), -counts[k], names[k].casefold()))
    return [names[k] for k in order[:limit]]


def parse_similar(info):
    """[{id, name}] from a Navidrome getArtistInfo2 answer. Artists the server doesn't have come without an id."""
    out, seen = [], set()
    for a in ((info or {}).get("artistInfo2") or {}).get("similarArtist") or []:
        name = a.get("name") if isinstance(a, dict) else None
        key = matching.norm_artist(name)
        if key and key not in seen:
            seen.add(key)
            out.append({"id": a.get("id") or name, "name": str(name), "albums_count": a.get("albumCount") or 0,
                        "image": ""})
    return out


def merge(similar_by_source, library_keys, dismissed=(), weights=None):
    """Suggestions from {library artist: [similar artists]}: artists you don't have or dismissed,
    scored by how many of your artists point to them (more when you play those artists a lot)."""
    weights = weights or {}
    top = max(weights.values()) if weights else 0
    dismissed = set(dismissed)
    found = {}
    for source, sims in similar_by_source.items():
        bonus = weights.get(matching.norm_artist(source), 0.0) / top if top else 0.0
        for a in sims:
            key = matching.norm_artist(a["name"])
            if not key or key in library_keys or key in SKIP_ARTISTS:
                continue
            s = found.setdefault(key, {"key": key, "id": a["id"], "name": a["name"],
                                       "image": a.get("image", ""), "albums_count": a.get("albums_count", 0),
                                       "because": [], "score": 0.0, "dismissed": key in dismissed})
            if source not in s["because"]:
                s["because"].append(source)
                s["score"] += 1 + bonus
    out = sorted(found.values(), key=lambda s: (-s["score"], -len(s["because"]), s["name"].casefold()))
    for s in out:
        s["score"] = round(s["score"], 2)
    return out


def latest_studio(items, wiki=None):
    """The newest studio album among an artist's releases (Qobuz album dicts). With a Wikipedia list
    ([{title, year}]), the Qobuz copy of its newest studio album that Qobuz has."""
    if wiki:
        from . import studio as studio_mod
        for w in sorted(wiki, key=lambda w: w.get("year") or 0, reverse=True):
            hits = studio_mod.find_qobuz(w["title"], items)
            if hits:
                return hits[0]
    studio = [i for i in classify.prefer_explicit(classify.dedupe(items))
              if classify.release_kind(i) == "Studio album"]
    studio.sort(key=lambda i: str(i.get("release_date_original") or ""), reverse=True)
    return studio[0] if studio else None


def find_suggestions(nd, weigh=True, top_n=40, dismissed=(), progress=None, should_stop=None, gap=0.05):
    """The whole search, against Navidrome only. nd: a navidrome.Navidrome. Returns (suggestions, note)."""
    progress = progress or (lambda *_: None)
    should_stop = should_stop or (lambda: False)
    albums = nd.all_albums()
    names, counts = library_artists(albums)
    ids = artist_ids(albums)
    weights, note = {}, ""
    if weigh:
        try:
            weights = artist_weights(nd.album_list("frequent", 200), nd.album_list("starred", 200))
            if not weights:
                note = "Navidrome has no play counts or starred albums yet, so every artist counts the same."
        except Exception as e:
            note = f"Couldn't read your plays from Navidrome ({str(e)[:100]}); every artist counts the same."
    sources = pick_sources(names, counts, weights, top_n)
    similar, asked = {}, 0
    for n, name in enumerate(sources, 1):
        if should_stop():
            break
        progress(n, len(sources), name)
        aid = ids.get(matching.norm_artist(name))
        if not aid:
            continue
        asked += 1
        try:
            found = parse_similar(nd.artist_info(aid))
        except Exception:
            continue
        if found:
            similar[name] = found
        time.sleep(gap)
    if asked and not similar:
        raise DiscoverError(NO_DATA)
    return merge(similar, set(names), dismissed, weights), note
