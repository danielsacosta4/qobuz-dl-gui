"""Library doctor and artist completion: compares the Navidrome library with Qobuz (and,
optionally, MusicBrainz). Pure Python, run from a worker thread.

Network use is throttled: Qobuz calls are spaced out (to stay clear of the account limits
qobuz-dl warns about) and MusicBrainz goes through its own 1-request-per-second limiter.
Everything is cached, so a second scan only fetches what changed.
"""

import os
import time
from dataclasses import asdict, dataclass, field

from . import cache, classify, library, musicbrainz, navidrome

QOBUZ_TTL = 7 * cache.DAY
QOBUZ_GAP = 0.6          # seconds between Qobuz API calls
WEB_URL = "https://play.qobuz.com/"

# Issue types
UPGRADE = "upgrade"
EXPLICIT = "explicit"
MISSING_TRACKS = "missing_tracks"
NO_COVER = "no_cover"
SPLIT = "split"
UNOFFICIAL = "unofficial"

LABELS = {
    UPGRADE: "Hi-Res available",
    EXPLICIT: "Explicit version available",
    MISSING_TRACKS: "Missing tracks",
    NO_COVER: "No cover",
    SPLIT: "Album split in Navidrome",
    UNOFFICIAL: "Unofficial release",
}


@dataclass
class Issue:
    kind: str
    artist: str
    album: str
    detail: str
    album_id: str = ""            # Navidrome album id
    url: str = ""                 # Qobuz link to download for the fix
    cover_url: str = ""
    folder: str = ""              # album folder on the share, when found
    extra: dict = field(default_factory=dict)

    @property
    def key(self):
        return f"{self.kind}|{self.artist.lower()}|{self.album.lower()}"


@dataclass
class ArtistCompletion:
    artist: str
    owned: int
    total: int
    missing: list                 # [{title, year, url, explicit, quality}]
    verified: bool                # counted against MusicBrainz's official list


# ---- helpers ---------------------------------------------------------------------
def _title_key(t):
    return musicbrainz.norm_title(t)


def _qobuz_quality(a):
    if a.get("hires_streamable") or a.get("hires"):
        return (1, int(a.get("maximum_bit_depth") or 24), float(a.get("maximum_sampling_rate") or 96), 0)
    return (1, 16, 44.1, 0)


def _slim(a, artist_name):
    img = a.get("image") or {}
    return {
        "id": a.get("id"), "title": a.get("title", ""), "version": a.get("version") or "",
        "parental_warning": bool(a.get("parental_warning")),
        "hires_streamable": bool(a.get("hires_streamable")),
        "maximum_bit_depth": a.get("maximum_bit_depth"), "maximum_sampling_rate": a.get("maximum_sampling_rate"),
        "tracks_count": a.get("tracks_count") or 0, "duration": a.get("duration") or 0,
        "upc": a.get("upc") or "", "label": {"name": (a.get("label") or {}).get("name", "")},
        "release_type": a.get("release_type") or a.get("product_type") or "",
        "release_date_original": a.get("release_date_original") or "",
        "artist": {"name": (a.get("artist") or {}).get("name", artist_name)},
        "image": {"large": img.get("large") or img.get("small") or ""},
    }


def locate_folder(library_root, artist, album_name, extra_artists=()):
    """Finds an album's folder on the share (Letter/Artist/Album) from its artist and title."""
    if not library_root or not os.path.isdir(library_root):
        return ""
    want = _title_key(album_name)
    for name in (artist, *extra_artists):
        letter_dir, artist_dir, exists = library.find_artist_dir(library_root, name)
        if not exists:
            continue
        base = os.path.join(letter_dir, artist_dir)
        try:
            entries = os.listdir(base)
        except OSError:
            continue
        best = ""
        for e in entries:
            if e.startswith(".") or not os.path.isdir(os.path.join(base, e)):
                continue
            key = library.album_key(e)
            # folder names look like "Artist - Album (Year) [...]"
            if f" {want} " in f" {key} " or key.endswith(" " + want) or key == want:
                if not best or len(e) < len(best):
                    best = e
        if best:
            return os.path.join(base, best)
    return ""


class Scanner:
    def __init__(self, session, library_root, use_musicbrainz=False, progress=None, should_stop=None,
                 prefer_explicit=True):
        self.session = session            # client.Session (Qobuz)
        self.library_root = library_root
        self.use_mb = use_musicbrainz
        self.progress = progress or (lambda *_: None)
        self.should_stop = should_stop or (lambda: False)
        self.prefer_explicit = prefer_explicit
        self._last_qobuz = 0.0

    # ---- Qobuz (throttled + cached)
    def _qobuz_wait(self):
        gap = QOBUZ_GAP - (time.time() - self._last_qobuz)
        if gap > 0:
            time.sleep(gap)
        self._last_qobuz = time.time()

    def qobuz_discography(self, artist_name):
        key = _title_key(artist_name)
        cached = cache.get("qobuz_artist", key, QOBUZ_TTL)
        if cached is not None:
            return cached
        self._qobuz_wait()
        found = None
        for a in self.session.search("artists", artist_name, 10):
            if _title_key(a.get("name")) == key:
                found = a
                break
        albums = []
        if found:
            self._qobuz_wait()
            _name, items = self.session.artist_albums(found.get("id"))
            albums = [_slim(a, artist_name) for a in items]
        cache.put("qobuz_artist", key, albums)
        return albums

    # ---- Navidrome details (cached by album id + change stamp)
    @staticmethod
    def album_details(nd, album):
        stamp = f"{album.get('id')}|{album.get('songCount')}|{album.get('created')}|{album.get('changed', '')}"
        cached = cache.get("nd_album", stamp)
        if cached is not None:
            return cached
        d = nd.album(album["id"])
        songs = d.get("song") or []
        info = {
            "quality": list(navidrome.album_quality(d)),
            "songs": len(songs),
            "cover": bool(d.get("coverArt")),
            "artists": sorted({s.get("artist", "") for s in songs if s.get("artist")}),
        }
        cache.put("nd_album", stamp, info)
        return info

    # ---- main
    def run(self):
        nd = navidrome.Navidrome.from_settings()
        albums = nd.all_albums()
        by_artist = {}
        for a in albums:
            by_artist.setdefault(a.get("artist") or "Unknown", []).append(a)

        issues, completion = [], []
        self._split_check(albums, issues)
        artists = sorted(by_artist, key=str.casefold)
        for n, artist in enumerate(artists, 1):
            if self.should_stop():
                break
            self.progress(n, len(artists), artist)
            lib_albums = by_artist[artist]
            try:
                qobuz = self.qobuz_discography(artist)
            except Exception:
                qobuz = []
            check = None
            if self.use_mb and qobuz:
                try:
                    check = musicbrainz.ArtistCheck(artist).prepare(qobuz, barcode_budget=0)
                except Exception:
                    check = None
            for al in lib_albums:
                if self.should_stop():
                    break
                try:
                    details = self.album_details(nd, al)
                except Exception:
                    continue
                self._album_issues(artist, al, details, qobuz, check, issues)
            if qobuz:
                completion.append(self._completion(artist, lib_albums, qobuz, check))
        return issues, completion

    # ---- checks
    def _candidates(self, album_name, qobuz):
        key = _title_key(album_name)
        return [q for q in qobuz if _title_key(q["title"]) == key]

    def _album_issues(self, artist, al, details, qobuz, check, issues):
        name = al.get("name", "")
        cands = self._candidates(name, qobuz)
        quality = tuple(details.get("quality") or ())
        lib_explicit = navidrome.is_explicit(al)

        def folder():
            return locate_folder(self.library_root, artist, name)

        if not details.get("cover"):
            best = max(cands, key=_qobuz_quality) if cands else None
            issues.append(Issue(NO_COVER, artist, name, "No cover art in the files or the folder",
                                album_id=al.get("id", ""), cover_url=(best or {}).get("image", {}).get("large", ""),
                                folder=folder()))
        if not cands:
            return

        # Hi-Res available for a CD-quality-or-lower album
        if quality and (quality[0] == 0 or quality[1] <= 16):
            hires = [c for c in cands if c["hires_streamable"] and int(c.get("maximum_bit_depth") or 0) > 16]
            if not hires and quality[0] == 0:
                # lossy file (MP3, AAC…): any lossless edition is an upgrade
                hires = list(cands)
            if hires:
                pool = [c for c in hires if c["parental_warning"]] if (lib_explicit or self.prefer_explicit) else []
                best = max(pool or hires, key=_qobuz_quality)
                q = _qobuz_quality(best)
                label = LABELS[UPGRADE] if q[1] > 16 else "Lossless available"
                issues.append(Issue(UPGRADE, artist, name,
                                    f"{library.describe_quality(quality)} → {q[1]}-bit / {q[2]:g} kHz on Qobuz",
                                    extra={"label": label},
                                    album_id=al.get("id", ""), url=f"{WEB_URL}album/{best['id']}"))

        # Explicit version exists (only when Qobuz has both versions, so an untagged
        # explicit copy from elsewhere isn't mistaken for a clean one)
        if not lib_explicit:
            exp = [c for c in cands if c["parental_warning"]]
            clean = [c for c in cands if not c["parental_warning"]]
            if exp and clean:
                best = max(exp, key=_qobuz_quality)
                issues.append(Issue(EXPLICIT, artist, name,
                                    "Your copy isn't tagged explicit; Qobuz has an explicit version",
                                    album_id=al.get("id", ""), url=f"{WEB_URL}album/{best['id']}"))

        # Missing tracks (compared with the same edition when it can be told apart)
        same_edition = [c for c in cands if classify._norm(f"{c['title']} {c['version']}") == classify._norm(name)]
        ref = same_edition or cands
        expected = min(int(c.get("tracks_count") or 0) for c in ref)
        if expected and details.get("songs", 0) < expected:
            best = min(ref, key=lambda c: int(c.get("tracks_count") or 0))
            issues.append(Issue(MISSING_TRACKS, artist, name,
                                f"{details.get('songs', 0)} of {expected} tracks",
                                album_id=al.get("id", ""), url=f"{WEB_URL}album/{best['id']}"))

        # Unofficial: MusicBrainz when it knows the artist, the backup rules otherwise
        if check is None or check.artist is None:
            reason = musicbrainz.suspicion(cands[0], artist)
            if reason:
                issues.append(Issue(UNOFFICIAL, artist, name, f"Likely unofficial: {reason}",
                                    album_id=al.get("id", ""), folder=folder()))
        else:
            status, _ = check.verdict(cands[0])
            reason = musicbrainz.suspicion(cands[0], artist)
            if status == musicbrainz.BOOTLEG or (status == musicbrainz.UNKNOWN and reason):
                why = "MusicBrainz lists it as a bootleg" if status == musicbrainz.BOOTLEG else \
                    f"not on MusicBrainz; {reason}"
                issues.append(Issue(UNOFFICIAL, artist, name, why, album_id=al.get("id", ""), folder=folder()))

    def _split_check(self, albums, issues):
        """Same album name and year under several album artists usually means the album artist
        tag is missing or inconsistent, so Navidrome shows it as several albums."""
        groups = {}
        for a in albums:
            key = (_title_key(a.get("name")), str(a.get("year") or ""))
            if key[0]:
                groups.setdefault(key, []).append(a)
        for (_t, _y), group in groups.items():
            artists = sorted({g.get("artist", "") for g in group})
            if len(group) < 2 or len(artists) < 2:
                continue
            main = max(group, key=lambda g: int(g.get("songCount") or 0))
            name = main.get("name", "")
            others = [x for x in artists if x != main.get("artist")]
            issues.append(Issue(SPLIT, main.get("artist", ""), name,
                                f"Shown as {len(group)} albums: {', '.join(artists)}",
                                album_id=main.get("id", ""),
                                folder=locate_folder(self.library_root, main.get("artist", ""), name, others),
                                extra={"album_artist": main.get("artist", ""), "artists": artists}))

    def _completion(self, artist, lib_albums, qobuz, check):
        verified = check is not None and check.artist is not None
        owned_keys = {_title_key(a.get("name")) for a in lib_albums}
        studio = {}
        for q in classify.prefer_explicit(classify.dedupe(qobuz)) if self.prefer_explicit else classify.dedupe(qobuz):
            if verified:
                status, kind = check.verdict(q)
                if status != musicbrainz.OFFICIAL or (kind or classify.release_kind(q)) != "Studio album":
                    continue
            else:
                if classify.release_kind(q) != "Studio album" or musicbrainz.suspicion(q, artist):
                    continue
            key = _title_key(q["title"])
            if key and key not in studio:
                studio[key] = q
        missing = []
        for key, q in sorted(studio.items(), key=lambda kv: kv[1].get("release_date_original", "")):
            if key not in owned_keys:
                qq = _qobuz_quality(q)
                missing.append({"title": q["title"] + (f" ({q['version']})" if q["version"] else ""),
                                "year": str(q.get("release_date_original") or "")[:4],
                                "url": f"{WEB_URL}album/{q['id']}", "explicit": q["parental_warning"],
                                "quality": f"{qq[1]}-bit / {qq[2]:g} kHz"})
        total = len(studio)
        return ArtistCompletion(artist, total - len(missing), total, missing, verified)


# ---- fixes (run from worker threads) ----------------------------------------------
def fix_cover(issue):
    import requests
    if not issue.folder or not issue.cover_url:
        raise ValueError("No folder or cover found for this album")
    r = requests.get(issue.cover_url, timeout=30)
    r.raise_for_status()
    path = os.path.join(issue.folder, "cover.jpg")
    with open(path + ".part", "wb") as f:
        f.write(r.content)
    os.replace(path + ".part", path)
    return path


def fix_album_artist(folder, album_artist):
    """Writes the same album artist tag to every audio file in the folder."""
    import mutagen
    changed = 0
    for root, _, files in os.walk(folder):
        for f in files:
            if os.path.splitext(f)[1].lower() not in library.AUDIO_EXT:
                continue
            audio = mutagen.File(os.path.join(root, f), easy=True)
            if audio is None:
                continue
            audio["albumartist"] = [album_artist]
            audio.save()
            changed += 1
    return changed


def save_results(issues, completion):
    cache.put("doctor", "last", {"time": time.time(), "issues": [asdict(i) for i in issues],
                                 "completion": [asdict(c) for c in completion]})


def load_results():
    data = cache.get("doctor", "last")
    if not data:
        return None, [], []
    return data.get("time"), [Issue(**i) for i in data.get("issues", [])], \
        [ArtistCompletion(**c) for c in data.get("completion", [])]
