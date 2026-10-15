"""Moving finished downloads into the library (Letter/Artist/Album) and checking what is already there."""

import os
import re
import shutil
import sqlite3
from collections import Counter
from dataclasses import dataclass, field

from .classify import _norm, letter

AUDIO_EXT = {".flac", ".mp3", ".m4a", ".ogg", ".opus", ".wav", ".aiff", ".alac"}
DISC_RE = re.compile(r"^(cd|disc|disk)\s*\d+$", re.IGNORECASE)
MARKERS = ("[IN PROGRESS]", "[INCOMPLETE]")


@dataclass
class AlbumFolder:
    path: str
    name: str
    artist: str = ""
    tracks: int = 0
    size: int = 0
    kind: str = "album"          # album, playlist
    state: str = "ready"         # ready, incomplete, in_progress
    files: list = field(default_factory=list)
    dest: str = None
    others: list = field(default_factory=list)   # [(path, quality)] other copies in the library
    quality: tuple = ()
    action: str = "replace"                      # replace, keep, skip  (when others exist)


def _is_audio(name):
    return os.path.splitext(name)[1].lower() in AUDIO_EXT


def _read_artist(path):
    try:
        import mutagen
        audio = mutagen.File(path, easy=True)
        if not audio:
            return ""
        for key in ("albumartist", "album artist", "artist"):
            value = audio.get(key)
            if value:
                return str(value[0]).strip()
    except Exception:
        pass
    return ""


def clean_dirname(name):
    name = name.replace("/", "／").replace("\\", "＼").replace("\0", "")
    return name.strip().rstrip(".") or "Unknown Artist"


def scan_staging(staging, library=None, disc_prefix="CD"):
    """Finds album folders under the download folder and works out each one's artist."""
    staging = os.path.abspath(os.path.expanduser(staging))
    library = os.path.abspath(os.path.expanduser(library)) if library else None
    disc_re = re.compile(rf"^({re.escape(disc_prefix)}|cd|disc|disk)\s*\d+$", re.IGNORECASE) if disc_prefix else DISC_RE
    albums = {}
    for root, dirs, files in os.walk(staging):
        if library and (root == library or root.startswith(library + os.sep)):
            dirs[:] = []
            continue
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        audio = [f for f in files if _is_audio(f) and not f.startswith("~tmp_")]
        if not audio:
            continue
        album_dir = os.path.dirname(root) if disc_re.match(os.path.basename(root)) and root != staging else root
        if album_dir == staging:
            continue  # loose tracks in the top folder: not an album
        entry = albums.get(album_dir)
        if entry is None:
            entry = albums[album_dir] = AlbumFolder(path=album_dir, name=os.path.basename(album_dir))
        for f in audio:
            entry.files.append(os.path.join(root, f))

    out = []
    for path, album in sorted(albums.items(), key=lambda kv: kv[0].lower()):
        album.tracks = len(album.files)
        album.size = 0
        for root, _, files in os.walk(path):
            for f in files:
                try:
                    album.size += os.path.getsize(os.path.join(root, f))
                except OSError:
                    pass
        if "[IN PROGRESS]" in album.name:
            album.state = "in_progress"
        elif "[INCOMPLETE]" in album.name:
            album.state = "incomplete"

        sample = sorted(album.files)[:8]
        artists = Counter(a for a in (_read_artist(f) for f in sample) if a)
        has_m3u = any(f.lower().endswith((".m3u", ".m3u8")) for f in os.listdir(path))
        if has_m3u and len(artists) > 1:
            album.kind = "playlist"
        if artists:
            album.artist = artists.most_common(1)[0][0]
        else:
            rel = os.path.relpath(path, staging).split(os.sep)
            album.artist = rel[0] if len(rel) > 1 else album.name.split(" - ")[0].strip()
        out.append(album)
    return out


def clean_album_name(name):
    for marker in MARKERS:
        name = name.replace(marker, "")
    return re.sub(r"\s{2,}", " ", name).strip()


def find_artist_dir(library, artist):
    """Returns (letter_dir, artist_dir_name, exists). Reuses an existing folder whatever its case."""
    letter_dir = os.path.join(library, letter(artist))
    wanted = clean_dirname(artist)
    try:
        for name in os.listdir(letter_dir):
            if name.casefold() == wanted.casefold() and os.path.isdir(os.path.join(letter_dir, name)):
                return letter_dir, name, True
    except FileNotFoundError:
        pass
    return letter_dir, wanted, False


# Bracketed parts of a folder name that describe the file quality rather than the release:
# "[FLAC 24]", "[24B-96kHz]", "(Hi-Res)", "[MP3 320]", "[16-44.1]" …
_QUALITY_PART = re.compile(
    r"\s*[\[(][^\])]*\b(flac|mp3|alac|aac|wav|hi-?res|lossless|\d+\s*-?\s*bits?|\d+\s*b\b|"
    r"\d+(\.\d+)?\s*k\s*hz|\d+(\.\d+)?k|kbps|16|24|32)\b[^\])]*[\])]", re.IGNORECASE)


def album_key(folder_name):
    """Identifies a release independent of the quality written in its folder name."""
    from .classify import _norm
    name = _QUALITY_PART.sub("", clean_album_name(folder_name))
    name = _EXPLICIT_PART.sub("", name)
    return _norm(name)


_EXPLICIT_PART = re.compile(r"\s*[\[(]\s*(e|explicit|clean|edited)\s*[\])]", re.IGNORECASE)


def looks_explicit(folder_name):
    return bool(re.search(r"[\[(]\s*(e|explicit)\s*[\])]", folder_name, re.IGNORECASE))


def folder_quality(path):
    """(lossless, bit depth, sample rate kHz, bitrate) of the first audio file found, or ()."""
    try:
        import mutagen
    except ImportError:
        return ()
    for root, _, files in os.walk(path):
        for f in sorted(files):
            if not _is_audio(f) or f.startswith("~tmp_"):
                continue
            try:
                info = mutagen.File(os.path.join(root, f)).info
            except Exception:
                continue
            bits = int(getattr(info, "bits_per_sample", 0) or 0)
            rate = float(getattr(info, "sample_rate", 0) or 0) / 1000
            lossless = 1 if bits else 0
            return (lossless, bits, rate, int(getattr(info, "bitrate", 0) or 0) // 1000)
    return ()


def describe_quality(q):
    if not q:
        return "unknown quality"
    if q[0]:
        return f"{q[1]}-bit / {q[2]:g} kHz"
    return f"{q[3]} kbps" if q[3] else "lossy"


def other_copies(artist_path, album_folder_name):
    """Folders of the same release under the artist, whatever quality their names show."""
    key = album_key(album_folder_name)
    exact = clean_album_name(album_folder_name)
    found = []
    try:
        names = os.listdir(artist_path)
    except OSError:
        return found
    for name in names:
        p = os.path.join(artist_path, name)
        if name == exact or not os.path.isdir(p) or name.startswith("."):
            continue
        if key and album_key(name) == key:
            found.append(p)
    return found


def destination(library, album):
    letter_dir, artist_dir, artist_exists = find_artist_dir(library, album.artist)
    artist_path = os.path.join(letter_dir, artist_dir)
    dest = os.path.join(artist_path, clean_album_name(album.name))
    album.others = []
    if artist_exists:
        if not album.quality:
            album.quality = folder_quality(album.path)
        album.others = [(p, folder_quality(p)) for p in other_copies(artist_path, album.name)]
    if os.path.isdir(dest):
        status = "Already in library — will merge"
    elif album.others:
        old = album.others[0][1]
        new = album.quality
        if new and old and new > old:
            verdict = "better than"
        elif new and old and new == old:
            verdict = "same quality as"
        elif new and old:
            verdict = "lower quality than"
        else:
            verdict = "another copy of"
        status = f"{describe_quality(new)} — {verdict} the copy in the library ({describe_quality(old)})"
        if any(looks_explicit(os.path.basename(p)) for p, _ in album.others) and not looks_explicit(album.name):
            status = f"{describe_quality(new)} — the library already has the explicit version"
    elif artist_exists:
        status = "Adds to existing artist"
    else:
        status = "New artist"
    return dest, status


def default_action(album):
    """What to do when another copy exists: replace if the new one is at least as good."""
    if not album.others:
        return "replace"
    old = max((q for _, q in album.others), default=())
    if album.quality and old and album.quality < old:
        return "skip"
    # never swap an explicit copy for a clean one
    if any(looks_explicit(os.path.basename(p)) for p, _ in album.others) and not looks_explicit(album.name):
        return "skip"
    return "replace"


TRASH_DIR = ".replaced-by-qobuz-dl-gui"


def retire_copy(library, path):
    """Moves a replaced album into a hidden folder at the library root (Navidrome ignores
    folders starting with a dot). Same share, so it's a quick rename, and nothing is lost
    until the bin is emptied."""
    rel = os.path.relpath(path, library)
    target = os.path.join(library, TRASH_DIR, rel)
    if os.path.exists(target):
        n = 2
        while os.path.exists(f"{target} ({n})"):
            n += 1
        target = f"{target} ({n})"
    os.makedirs(os.path.dirname(target), exist_ok=True)
    os.rename(path, target)
    return target


def trash_size(library):
    """(number of albums, bytes) waiting in the replaced-albums bin."""
    root = os.path.join(library, TRASH_DIR)
    total, albums = 0, set()
    for dp, _, files in os.walk(root):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(dp, f))
            except OSError:
                pass
            parts = os.path.relpath(dp, root).split(os.sep)
            if len(parts) >= 3:
                albums.add(tuple(parts[:3]))
    return len(albums), total


def empty_trash(library):
    root = os.path.join(library, TRASH_DIR)
    if os.path.isdir(root):
        shutil.rmtree(root)


def copy_album(src, dest, progress=None, should_stop=None):
    """Copies a folder into dest (merging), verifying each file's size. Returns bytes copied."""
    plan = []
    for root, _, files in os.walk(src):
        for f in files:
            if f.startswith("~tmp_"):
                continue
            s = os.path.join(root, f)
            plan.append((s, os.path.join(dest, os.path.relpath(s, src)), os.path.getsize(s)))
    total = sum(p[2] for p in plan) or 1
    done = 0
    for s, d, size in plan:
        if should_stop and should_stop():
            raise InterruptedError("Stopped")
        os.makedirs(os.path.dirname(d), exist_ok=True)
        tmp = d + ".part"
        shutil.copyfile(s, tmp)
        os.replace(tmp, d)
        try:
            st = os.stat(s)
            os.utime(d, (st.st_atime, st.st_mtime))
        except OSError:
            pass
        if os.path.getsize(d) != size:
            raise OSError(f"Size check failed for {d}")
        done += size
        if progress:
            progress(done, total, os.path.basename(s))
    return done


def remove_staged(path, staging):
    """Deletes a sent album folder, then any parent folders it leaves empty (up to the staging root)."""
    shutil.rmtree(path)
    staging = os.path.abspath(staging)
    parent = os.path.dirname(os.path.abspath(path))
    while parent.startswith(staging + os.sep):
        try:
            os.rmdir(parent)
        except OSError:
            break
        parent = os.path.dirname(parent)


_BRACKETS = re.compile(r"\s*(\[[^\]]*\]|\([^)]*\))")


def _strip_brackets(text):
    """Drops [..] and (..) parts: tagging suffixes like "[2023, WEB]" and editions like "(Deluxe)"."""
    return _BRACKETS.sub("", str(text or "")).strip()


_EDITION_WORDS = {"deluxe", "edition", "remastered", "remaster", "anniversary", "expanded", "version",
                  "explicit", "clean", "special", "super", "bonus", "track", "tracks", "reissue", "hi", "res",
                  "the", "complete", "collector", "collectors", "mono", "stereo", "mix", "s"}


def _edition_only(longer, shorter):
    """True when `longer` is `shorter` plus only edition words, e.g. "arrival deluxe edition"."""
    if not longer.startswith(shorter + " "):
        return False
    extra = longer[len(shorter) + 1:].split()
    return all(w in _EDITION_WORDS or w.isdigit() or re.fullmatch(r"\d+(st|nd|rd|th)", w) for w in extra)


class LibraryIndex:
    """What the library and the download history already hold, for the ✔ marks in Search."""

    def __init__(self):
        self.artists = {}       # norm artist -> list of norm album folder names
        self.history = set()    # Qobuz album / track ids from qobuz-dl's database

    source = "folders"

    @classmethod
    def from_navidrome(cls, albums, db_path):
        """Index built from Navidrome's album list (exact album names instead of folder names)."""
        idx = cls()
        idx.source = "navidrome"
        for al in albums:
            names = idx.artists.setdefault(_norm(al.get("artist", "")), [])
            name = al.get("name", "")
            names.append(_norm(name))
            bare = _norm(_strip_brackets(name))
            if bare and bare != names[-1]:
                names.append(bare)   # "UK GRIM [2023, WEB]" -> "uk grim"
        idx._load_history(db_path)
        return idx

    def _load_history(self, db_path):
        if db_path and os.path.isfile(db_path):
            try:
                with sqlite3.connect(db_path) as conn:
                    self.history = {str(r[0]) for r in conn.execute("SELECT id FROM downloads")}
            except sqlite3.Error:
                pass

    @classmethod
    def build(cls, library, db_path):
        idx = cls()
        if library and os.path.isdir(library):
            for letter_name in os.listdir(library):
                letter_dir = os.path.join(library, letter_name)
                if letter_name.startswith(".") or not os.path.isdir(letter_dir):
                    continue
                try:
                    artists = os.listdir(letter_dir)
                except OSError:
                    continue
                for artist in artists:
                    adir = os.path.join(letter_dir, artist)
                    try:
                        albums = [_norm(a) for a in os.listdir(adir) if os.path.isdir(os.path.join(adir, a))]
                    except OSError:
                        continue
                    idx.artists.setdefault(_norm(artist), []).extend(albums)
        if db_path and os.path.isfile(db_path):
            try:
                with sqlite3.connect(db_path) as conn:
                    idx.history = {str(r[0]) for r in conn.execute("SELECT id FROM downloads")}
            except sqlite3.Error:
                pass
        return idx

    def status(self, item_id, artist, title):
        """'library', 'history' or '' for an album."""
        a_norm, t = _norm(artist), _norm(title)
        albums = self.artists.get(a_norm)
        if albums and t and self.source == "navidrome":
            titles = {t, _norm(_strip_brackets(title))} - {""}
            for name in albums:
                for tt in titles:
                    if name == tt or _edition_only(name, tt) or _edition_only(tt, name):
                        return "library"
            albums = None
        if albums and t:
            pattern = re.compile(rf"(^| ){re.escape(t)}( |$)")
            for folder in albums:
                # "abba arrival 1976 flac 24" -> "arrival 1976 flac 24": the album title
                # should then start the name, so a self-titled album doesn't match everything.
                if a_norm and folder.startswith(a_norm + " "):
                    if folder[len(a_norm) + 1:].startswith(t + " ") or folder[len(a_norm) + 1:] == t:
                        return "library"
                elif pattern.search(folder):
                    return "library"
        if item_id is not None and str(item_id) in self.history:
            return "history"
        return ""
