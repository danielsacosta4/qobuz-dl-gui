"""The library rules (see RULES.md) and the checks that don't need Qobuz.

R0 Intact            every file decodes without errors            (independent check: flac -t)
R1 One copy          each album exactly once (editions = one album; language versions = different albums)
R2 Best quality      the best quality Qobuz offers (Hi-Res when it exists, lossless at least)
R3 Explicit          the explicit version whenever one exists
R4 Location          Letter/Artist/Album, "The" under T, accents dropped, digits/symbols under #
R5 One album         Navidrome shows each album once, under one album artist
"""

import os

from . import classify, library, matching

RULES = {
    "R1": ("One copy", "Each album is in the library exactly once. Editions (deluxe, remaster, anniversary) "
                       "count as the same album; language versions count as different albums."),
    "R2": ("Best quality", "Each album is in the best quality Qobuz offers: Hi-Res when it exists, lossless at least."),
    "R3": ("Explicit", "Each album is the explicit version whenever an explicit version exists."),
    "R4": ("Location", "Every album is filed as Letter/Artist/Album. “The” goes under T, accents are "
                       "dropped, names starting with a digit or symbol go under #."),
    "R5": ("One album in Navidrome", "Navidrome shows each album once, under a single album artist."),
}


# accepted exceptions are kept under the same setting the old "Ignore" button used
EXCEPTIONS = "doctor_ignored"


def legacy_key(kind, artist, album):
    """How 0.9.4 and earlier stored an ignored problem."""
    return f"{kind}|{str(artist).lower()}|{str(album).lower()}"


def exception_key(kind, artist, album):
    """Key under which a problem is remembered as an accepted exception."""
    return f"{kind}|{matching.norm_artist(artist)}|{matching.title_key(album)}"


# ---- R1 -------------------------------------------------------------------------------
def duplicate_groups(albums):
    """Albums (Navidrome entries) present more than once for the same artist."""
    groups = {}
    for a in albums:
        key = (matching.norm_artist(a.get("artist", "")), matching.title_key(a.get("name", "")))
        if key[1]:
            groups.setdefault(key, []).append(a)
    return [g for g in groups.values() if len(g) > 1]


# ---- R4 -------------------------------------------------------------------------------
def location_problems(layout):
    """layout: {letter: {artist_folder: [album folders]}}. Returns (letter, artist, problem, right_letter)."""
    out = []
    for letter_name, artists in sorted(layout.items()):
        if letter_name.startswith("."):
            continue
        if not (len(letter_name) == 1 and (letter_name.isalpha() and letter_name.isupper() or letter_name == "#")):
            out.append((letter_name, "", "top-level folder isn't a letter or #", ""))
            continue
        for artist in sorted(artists):
            right = classify.letter(artist)
            if right != letter_name:
                out.append((letter_name, artist, f"belongs under {right}", right))
    return out


def read_layout(root):
    """Letter/Artist/Album folder names (no files) from the share."""
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


# ---- R5 -------------------------------------------------------------------------------
def split_groups(albums):
    """Albums Navidrome shows as several entries.

    Same title and year under different album artists is only a split when the entries share an
    artist ("Gilberto Gil" and "Gilberto Gil • Jorge Ben Jor") or when there are many artists
    (an untagged compilation shown once per track artist, all on one label). Unrelated artists with a
    song of the same name ("Everybody Hurts", cover versions of "Losing My Religion") are not a split.
    """
    groups = {}
    for a in albums:
        key = (matching.title_key(a.get("name")), str(a.get("year") or ""))
        if key[0]:
            groups.setdefault(key, []).append(a)
    out = []
    for group in groups.values():
        artists = sorted({g.get("artist", "") for g in group})
        if len(group) < 2 or len(artists) < 2:
            continue
        name_sets = [{matching.norm_artist(n) for n in matching.artist_names(g.get("artist", ""), g)} - {""}
                     for g in group]
        overlap = any(name_sets[i] & name_sets[j]
                      for i in range(len(name_sets)) for j in range(i + 1, len(name_sets)))
        # many artists: a split compilation shares one label (or none); cover versions of the same
        # song each come from a different label
        labels = {matching.norm_artist(l.get("name", "")) for g in group for l in (g.get("recordLabels") or [])
                  if isinstance(l, dict)} - {""}
        if overlap or (len(artists) >= 3 and len(labels) <= 1):
            out.append(group)
    return out


def album_folder_title(folder_name, artist):
    """"ABBA - Voyage (2021) [FLAC 24]" -> "Voyage" (for matching folders to albums)."""
    name = library.clean_album_name(folder_name)
    prefix = f"{artist} - "
    if name.lower().startswith(prefix.lower()):
        name = name[len(prefix):]
    return name
