"""Regression test on a recorded real library (run locally; the data never goes in the repository).

Needs tests/private/ (exported by the Library report → testdata/):
    navidrome_albums.json  qobuz_discographies.json  library_layout.json
Run:  python3 tests/test_real_library.py
The thresholds are the numbers 0.9.5 reached; a change that makes them worse fails.
"""
import collections
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PRIVATE = os.path.join(HERE, "private")
sys.path.insert(0, os.path.dirname(HERE))
os.environ.setdefault("XDG_CACHE_HOME", tempfile.mkdtemp())


def load(name):
    with open(os.path.join(PRIVATE, name), encoding="utf-8") as f:
        return json.load(f)


def main():
    if not os.path.isfile(os.path.join(PRIVATE, "navidrome_albums.json")):
        print("SKIP: no recorded library in tests/private/")
        return 0
    from qobuz_gui import doctor, library, matching, rules

    albums, disc, layout = load("navidrome_albums.json"), load("qobuz_discographies.json"), load("library_layout.json")
    index = library.LibraryIndex.from_navidrome(albums, None)
    scanner = doctor.Scanner(None, None)
    by_artist = collections.defaultdict(list)
    for a in albums:
        by_artist[a.get("artist") or "Unknown"].append(a)
    matched = owned = 0
    for artist, items in by_artist.items():
        names = []
        for al in items:
            names += [n for n in matching.artist_names(artist, al) if n not in names]
        qobuz = matching.merge_discographies(disc.get(n) or [] for n in names)
        for al in items:
            cands = scanner._candidates(al.get("name", ""), qobuz, matching.artist_names(artist, al))
            if not cands:
                continue
            matched += 1
            if any(index.status(c.get("id"), (c.get("artist") or {}).get("name", artist), c.get("title", "")) == "library"
                   for c in cands):
                owned += 1
    n = len(albums)
    r1 = rules.duplicate_groups(albums)
    r4 = rules.location_problems(layout)
    r5 = rules.split_groups(albums)
    results = {
        "matched_pct": round(100 * matched / n, 1),
        "owned_of_matched_pct": round(100 * owned / matched, 1) if matched else 0,
        "R1_groups": len(r1), "R4": len(r4), "R5": len(r5),
    }
    print(json.dumps(results, indent=1))
    failures = []
    if results["matched_pct"] < 93.0:
        failures.append("fewer albums matched on Qobuz than 0.9.5 (93.4%)")
    if results["owned_of_matched_pct"] < 99.5:
        failures.append("fewer matched albums recognised as owned than 0.9.5 (99.9%)")
    # rule counts may only change when the library itself changes; a jump means a matching change
    for key, limit in (("R5", 15), ("R1_groups", 80)):
        if results[key] > limit:
            failures.append(f"{key} jumped to {results[key]} — check for false positives")
    for f in failures:
        print("FAIL", f)
    print("OK" if not failures else f"{len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
