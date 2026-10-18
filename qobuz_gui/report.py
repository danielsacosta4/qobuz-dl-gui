"""Library report: measures the library against the rules, using Navidrome as the source of truth.

Run it from the app (Library health → Library report) or from a terminal:

    qobuz-dl-gui --report [OUTPUT_FOLDER]

It writes a folder with:
  report.md / report.json   the numbers (what's measured and how is written in report.md)
  unmatched.csv             your albums the app couldn't find on Qobuz (or couldn't match)
  not_recognised.csv        albums matched on Qobuz but NOT recognised as owned (the ✔ would be missing)
  duplicates.csv            R1: albums present more than once (editions = same album, languages = different)
  upgrades.csv              R2: CD quality or lower with a Hi-Res version on Qobuz
  explicit.csv              R3: clean copies where Qobuz has an explicit version
  location.csv              R4: folders not in Letter/Artist/Album
  split.csv                 R5: albums Navidrome shows as several
  spot_check.csv            30 random "owned" matches to check by eye (precision)
  testdata/                 the raw data used, for building tests — stays on your computer

Nothing in the library is changed.
"""

import csv
import datetime
import json
import os
import random
import sys
import time

from . import __version__, classify, doctor, library, matching, navidrome, prefs, rules

DEFAULT_LIBRARY = "/mnt/navidrome/Music"


def library_folder():
    value = prefs.get_str(prefs.LIBRARY_DIR, "")
    if not value and os.path.isdir(DEFAULT_LIBRARY):
        value = DEFAULT_LIBRARY
    return os.path.expanduser(value)


def _say(msg):
    print(msg, flush=True)


def _write_csv(path, header, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def _pct(n, d):
    return round(100.0 * n / d, 1) if d else 0.0


# ---- R4: folder layout on the share -----------------------------------------------
def check_location(root):
    """Walks Letter/Artist/Album (dirs only, plus audio presence) and lists what doesn't fit."""
    problems = []
    if not root or not os.path.isdir(root):
        return None, problems
    album_count = 0
    for letter_name in sorted(os.listdir(root)):
        lp = os.path.join(root, letter_name)
        if letter_name.startswith(".") or not os.path.isdir(lp):
            if os.path.isfile(lp) and os.path.splitext(letter_name)[1].lower() in library.AUDIO_EXT:
                problems.append((letter_name, "", "", "audio file loose in the library root"))
            continue
        if not (len(letter_name) == 1 and (letter_name.isalpha() and letter_name.isupper() or letter_name == "#")):
            problems.append((letter_name, "", "", "top-level folder isn't a letter or #"))
            continue
        for artist in sorted(os.listdir(lp)):
            ap = os.path.join(lp, artist)
            if artist.startswith("."):
                continue
            if os.path.isfile(ap):
                if os.path.splitext(artist)[1].lower() in library.AUDIO_EXT:
                    problems.append((letter_name, "", artist, "audio file loose in a letter folder"))
                continue
            expected = classify.letter(artist)
            if expected != letter_name:
                problems.append((letter_name, artist, "", f"artist belongs under {expected}"))
            try:
                entries = os.listdir(ap)
            except OSError:
                continue
            for album in entries:
                alp = os.path.join(ap, album)
                if album.startswith("."):
                    continue
                if os.path.isfile(alp):
                    if os.path.splitext(album)[1].lower() in library.AUDIO_EXT:
                        problems.append((letter_name, artist, album, "audio file loose in an artist folder"))
                    continue
                album_count += 1
    return album_count, problems


# ---- main --------------------------------------------------------------------------
def run(out_dir=None):
    started = time.time()
    stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H%M")
    out_dir = os.path.expanduser(out_dir or f"~/qobuz-dl-gui-report-{stamp}")
    os.makedirs(os.path.join(out_dir, "testdata"), exist_ok=True)
    _say(f"Qobuz-DL GUI {__version__} — library report")
    _say(f"Writing to {out_dir}")

    if not navidrome.configured():
        _say("Navidrome isn't set up (Settings → Navidrome). The report needs it.")
        return 2

    from .client import Session
    from .library import LibraryIndex
    from . import config

    nd = navidrome.Navidrome.from_settings()
    _say("Reading the album list from Navidrome…")
    albums = nd.all_albums()
    _say(f"  {len(albums)} albums")
    index = LibraryIndex.from_navidrome(albums, config.QOBUZ_DB)

    session = Session()
    scanner = doctor.Scanner(session, library_folder(), use_musicbrainz=False,
                             prefer_explicit=prefs.get_bool(prefs.PREFER_EXPLICIT, True))

    by_artist = {}
    for a in albums:
        by_artist.setdefault(a.get("artist") or "Unknown", []).append(a)

    rows_unmatched, rows_not_recog, recognised_pairs = [], [], []
    issues = []
    on_qobuz = recognised = artist_missing = 0
    discographies = {}
    artists = sorted(by_artist, key=str.casefold)
    for n, artist in enumerate(artists, 1):
        if n == 1 or n % 25 == 0 or n == len(artists):
            _say(f"  artist {n} of {len(artists)} — {artist}")
        names = []
        for al in by_artist[artist]:
            for nm in matching.artist_names(artist, al):
                if nm not in names:
                    names.append(nm)
        qobuz_lists = []
        for nm in names:
            try:
                got = scanner.qobuz_discography(nm)
            except Exception as e:
                _say(f"    Qobuz lookup failed for {nm}: {e}")
                got = []
            discographies[nm] = got
            qobuz_lists.append(got)
        qobuz = matching.merge_discographies(qobuz_lists)
        for al in by_artist[artist]:
            name = al.get("name", "")
            if not qobuz:
                artist_missing += 1
                rows_unmatched.append((artist, name, al.get("year", ""), "artist not found on Qobuz"))
                continue
            cands = scanner._candidates(name, qobuz, matching.artist_names(artist, al))
            if not cands:
                rows_unmatched.append((artist, name, al.get("year", ""), "artist found, album not matched"))
                continue
            on_qobuz += 1
            # the way the Search tab decides the ✔: the Qobuz title and artist against the index
            hit = None
            for c in cands:
                if index.status(c.get("id"), (c.get("artist") or {}).get("name", artist), c.get("title", "")) == "library":
                    hit = c
                    break
            if hit:
                recognised += 1
                recognised_pairs.append((artist, name, hit))
            else:
                c = cands[0]
                rows_not_recog.append((artist, name, c.get("title", ""), c.get("version", ""),
                                       f"{doctor.WEB_URL}album/{c.get('id')}"))
            try:
                details = scanner.album_details(nd, al)
                scanner._album_issues(artist, al, details, qobuz, None, issues, names)
            except Exception as e:
                _say(f"    couldn't read {artist} — {name} from Navidrome: {e}")

    # R1 duplicates (editions count as the same album)
    dup_groups = rules.duplicate_groups(albums)
    rows_dup = [(g[0].get("artist", ""), " | ".join(x.get("name", "") for x in g), len(g)) for g in dup_groups]

    # R5 split
    split_issues = []
    scanner._split_check(albums, split_issues)

    # R4 location
    _say("Checking the folder layout on the share…")
    lib_albums, loc_problems = check_location(library_folder())

    upgrades = [i for i in issues if i.kind == doctor.UPGRADE]
    explicit = [i for i in issues if i.kind == doctor.EXPLICIT]

    # spot check sample (precision of "owned")
    rnd = random.Random(int(started))
    sample = rnd.sample(recognised_pairs, min(30, len(recognised_pairs)))

    numbers = {
        "app_version": __version__,
        "date": datetime.datetime.now().isoformat(timespec="minutes"),
        "albums_in_navidrome": len(albums),
        "artists_in_navidrome": len(by_artist),
        "albums_matched_on_qobuz": on_qobuz,
        "albums_artist_not_on_qobuz": artist_missing,
        "albums_artist_on_qobuz_album_unmatched": len(rows_unmatched) - artist_missing,
        "recognised_as_owned": recognised,
        "recognised_as_owned_pct": _pct(recognised, on_qobuz),
        "R1_duplicate_groups": len(dup_groups),
        "R1_extra_copies": sum(len(g) - 1 for g in dup_groups),
        "R2_cd_or_lower_with_hires": len(upgrades),
        "R3_clean_with_explicit": len(explicit),
        "R4_location_problems": len(loc_problems),
        "R4_album_folders_seen": lib_albums,
        "R5_split_albums": len(split_issues),
        "minutes": round((time.time() - started) / 60, 1),
    }

    _write_csv(os.path.join(out_dir, "unmatched.csv"), ["artist", "album", "year", "why"], rows_unmatched)
    _write_csv(os.path.join(out_dir, "not_recognised.csv"),
               ["artist", "album (Navidrome)", "Qobuz title", "Qobuz edition", "Qobuz link"], rows_not_recog)
    _write_csv(os.path.join(out_dir, "duplicates.csv"), ["artist", "albums", "copies"], rows_dup)
    _write_csv(os.path.join(out_dir, "upgrades.csv"), ["artist", "album", "detail", "Qobuz link"],
               [(i.artist, i.album, i.detail, i.url) for i in upgrades])
    _write_csv(os.path.join(out_dir, "explicit.csv"), ["artist", "album", "detail", "Qobuz link"],
               [(i.artist, i.album, i.detail, i.url) for i in explicit])
    _write_csv(os.path.join(out_dir, "location.csv"), ["letter", "artist", "album", "problem"], loc_problems)
    _write_csv(os.path.join(out_dir, "split.csv"), ["album", "detail"], [(i.album, i.detail) for i in split_issues])
    _write_csv(os.path.join(out_dir, "spot_check.csv"),
               ["artist", "album (Navidrome)", "matched Qobuz title", "Qobuz edition", "Qobuz year", "Qobuz link",
                "same album? (y/n)"],
               [(a, n, h.get("title", ""), h.get("version", ""), str(h.get("release_date_original", ""))[:4],
                 f"{doctor.WEB_URL}album/{h.get('id')}", "") for a, n, h in sample])

    with open(os.path.join(out_dir, "report.json"), "w", encoding="utf-8") as f:
        json.dump(numbers, f, indent=2)
    with open(os.path.join(out_dir, "testdata", "navidrome_albums.json"), "w", encoding="utf-8") as f:
        json.dump(albums, f)
    with open(os.path.join(out_dir, "testdata", "qobuz_discographies.json"), "w", encoding="utf-8") as f:
        json.dump(discographies, f)
    with open(os.path.join(out_dir, "testdata", "library_layout.json"), "w", encoding="utf-8") as f:
        json.dump(_layout_snapshot(library_folder()), f)

    _write_markdown(out_dir, numbers)
    _say("")
    _say(open(os.path.join(out_dir, "report.md"), encoding="utf-8").read())
    return 0


def _layout_snapshot(root):
    """Letter/Artist/Album folder names only (no files), for tests of the location rule."""
    snap = {}
    if not root or not os.path.isdir(root):
        return snap
    for letter_name in sorted(os.listdir(root)):
        lp = os.path.join(root, letter_name)
        if letter_name.startswith(".") or not os.path.isdir(lp):
            continue
        snap[letter_name] = {}
        for artist in sorted(os.listdir(lp)):
            ap = os.path.join(lp, artist)
            if artist.startswith(".") or not os.path.isdir(ap):
                continue
            try:
                snap[letter_name][artist] = sorted(x for x in os.listdir(ap)
                                                   if os.path.isdir(os.path.join(ap, x)) and not x.startswith("."))
            except OSError:
                snap[letter_name][artist] = []
    return snap


def _write_markdown(out_dir, n):
    lines = [
        f"# Library report — {n['date']}",
        "",
        f"App version {n['app_version']} · {n['albums_in_navidrome']} albums by {n['artists_in_navidrome']} "
        f"artists in Navidrome · took {n['minutes']} min",
        "",
        "## Recognised as owned",
        "",
        f"**{n['recognised_as_owned_pct']}%** — {n['recognised_as_owned']} of the {n['albums_matched_on_qobuz']} "
        "albums the app found on Qobuz would get the ✔ in Search.",
        "",
        f"Not counted: {n['albums_artist_not_on_qobuz']} albums whose artist isn't on Qobuz, and "
        f"{n['albums_artist_on_qobuz_album_unmatched']} whose artist is but the album wasn't matched "
        "(see unmatched.csv — some may be matching mistakes).",
        "",
        "How it's measured: every album in Navidrome is owned by definition. For each one, the app finds the same "
        "album in the artist's Qobuz discography and asks the same question the Search tab asks: is this Qobuz "
        "album in the library? Misses are listed in not_recognised.csv.",
        "",
        "## Rules",
        "",
        "| Rule | Count | File |",
        "|---|---|---|",
        f"| R1 One copy — extra copies (editions count as the same album) | {n['R1_extra_copies']} "
        f"in {n['R1_duplicate_groups']} albums | duplicates.csv |",
        f"| R2 Best quality — CD quality or lower with Hi-Res on Qobuz | {n['R2_cd_or_lower_with_hires']} | upgrades.csv |",
        f"| R3 Explicit — clean copies with an explicit version on Qobuz | {n['R3_clean_with_explicit']} | explicit.csv |",
        f"| R4 Location — folders not in Letter/Artist/Album | {n['R4_location_problems']} | location.csv |",
        f"| R5 One album in Navidrome — split albums | {n['R5_split_albums']} | split.csv |",
        "",
        "## Checks the numbers can't do",
        "",
        "- **Are the ✔ matches really the same album?** Open spot_check.csv and fill the last column (y/n) "
        "for the 30 random matches. That's the precision of the number above.",
        "- **R0, are the files intact?** Run `flac -t` over the library (it reads every file once).",
        "- **Tags and release identity:** check a sample of 20 albums in MusicBrainz Picard.",
        "",
        "The testdata/ folder holds the raw data used here. It stays on your computer unless you choose to send it.",
    ]
    with open(os.path.join(out_dir, "report.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    out = argv[0] if argv else None
    try:
        return run(out)
    except KeyboardInterrupt:
        _say("Stopped.")
        return 1
    except Exception as e:
        _say(f"The report failed: {e}")
        raise


if __name__ == "__main__":
    sys.exit(main())
