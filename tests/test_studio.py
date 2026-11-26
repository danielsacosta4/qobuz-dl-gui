from qobuz_gui import discover, matching, prefs, studio


def q(id_, title, year, version="", hires=False, explicit=False, kind="album", tracks=10):
    return {"id": id_, "title": title, "version": version, "release_date_original": f"{year}-01-01",
            "hires_streamable": hires, "maximum_bit_depth": 24 if hires else 16,
            "maximum_sampling_rate": 96 if hires else 44.1, "parental_warning": explicit, "release_type": kind,
            "tracks_count": tracks, "artist": {"name": "Band"}}


QOBUZ_STUDIO = [q("1", "First", 1990), q("2", "Second", 1993), q("4", "Qobuz Only Studio", 2001)]
QOBUZ_ALL = QOBUZ_STUDIO + [q("3", "Third (Deluxe Edition)", 1997, kind="single"), q("5", "Live in Tokyo", 1999, kind="live")]
WIKI = [{"title": "First", "year": 1990}, {"title": "Second", "year": 1993}, {"title": "Third", "year": 1997},
        {"title": "Lost Album", "year": 2005}]
OWNED = set().union(*(matching.library_keys(n) for n in ["First [FLAC 24]", "Third"]))


def rows():
    return studio.compare(QOBUZ_STUDIO, WIKI, QOBUZ_ALL, OWNED)


def test_compare_lines_the_lists_up():
    by = {r["title"]: r for r in rows()}
    assert set(by) == {"First", "Second", "Third", "Lost Album", "Qobuz Only Studio"}
    assert by["First"]["on_qobuz"] and by["First"]["on_wikipedia"] and by["First"]["owned"]
    assert by["Third"]["on_wikipedia"] and not by["Third"]["on_qobuz"] and by["Third"]["qobuz_kind"]
    assert by["Third"]["url"].endswith("/album/3") and by["Third"]["owned"]          # edition word ignored
    assert by["Lost Album"]["url"] == "" and not by["Lost Album"]["on_qobuz"]          # not on Qobuz at all
    assert by["Qobuz Only Studio"]["on_qobuz"] and not by["Qobuz Only Studio"]["on_wikipedia"]
    assert [r["year"] for r in rows()] == sorted(r["year"] for r in rows())


def test_counts_follow_the_chosen_source():
    owned, total, missing = studio.counts(rows(), studio.QOBUZ)
    assert (owned, total) == (1, 3) and [m["title"] for m in missing] == ["Second", "Qobuz Only Studio"]
    owned, total, missing = studio.counts(rows(), studio.WIKI)
    assert (owned, total) == (2, 4)
    assert [(m["title"], m["url"] != "") for m in missing] == [("Second", True), ("Lost Album", False)]


def test_best_copy_is_explicit_then_hires():
    items = [q("a", "Album", 2000), q("b", "Album", 2000, hires=True), q("c", "Album (Explicit)", 2000, explicit=True)]
    assert [x["id"] for x in studio.find_qobuz("Album", items)] == ["c", "b", "a"]


def test_newest_wikipedia_album_and_latest_pick():
    assert studio.newest(WIKI)["title"] == "Lost Album"
    assert studio.newest([]) is None
    wiki = [{"title": "Second", "year": 1993}, {"title": "Third", "year": 1997}]
    assert discover.latest_studio(QOBUZ_ALL)["id"] == "4"
    assert discover.latest_studio(QOBUZ_ALL, wiki)["id"] == "3"
    assert discover.latest_studio(QOBUZ_ALL, [{"title": "Not on Qobuz", "year": 2030}])["id"] == "4"      # falls back


def test_choice_is_stored_per_artist_and_the_default_wins(monkeypatch):
    store = {}
    monkeypatch.setattr(prefs, "get_str", lambda k, d="": store.get(k, d))
    monkeypatch.setattr(prefs, "get_json", lambda k, d=None: store.get(k, d))
    monkeypatch.setattr(prefs, "set_json", lambda k, v: store.__setitem__(k, v))
    assert studio.Choice.load().source_for("Radiohead") == studio.QOBUZ
    studio.set_source("Radiohead", studio.WIKI)
    assert studio.Choice.load().source_for("radiohead") == studio.WIKI
    assert studio.Choice.load().source_for("ABBA") == studio.QOBUZ
    store[prefs.STUDIO_DEFAULT] = studio.WIKI
    assert studio.Choice.load().source_for("ABBA") == studio.WIKI
    studio.set_source("Radiohead", studio.WIKI)                          # same as the default: no override kept
    assert studio.Choice.load().per_artist == {}


def test_completion_from_qobuz_or_wikipedia(monkeypatch):
    from qobuz_gui import doctor, wikipedia
    lib = [{"name": "First", "artist": "Band"}, {"name": "Third", "artist": "Band"}]
    qobuz = [q("1", "First", 1990), q("2", "Second", 1993), q("3", "Third (Deluxe Edition)", 1997, kind="single"),
             q("5", "Live in Tokyo", 1999, kind="live")]
    wiki = {"albums": WIKI, "page": "Band discography", "lang": "en"}
    monkeypatch.setattr(wikipedia, "studio_albums", lambda artist, lang="en", refresh=False: wiki)
    plain = doctor.Scanner(None, None)._completion("Band", lib, qobuz, None)
    assert plain.source == "qobuz" and (plain.owned, plain.total) == (1, 2)           # Qobuz calls "Third" a single
    assert [m["title"] for m in plain.missing] == ["Second"]
    assert plain.owned_titles == ["First", "Third"] and [a["title"] for a in plain.qobuz_studio] == ["First", "Second"]
    w = doctor.Scanner(None, None, studio=studio.Choice(default="wikipedia"))._completion("Band", lib, qobuz, None)
    assert w.source == "wikipedia" and (w.owned, w.total) == (2, 4) and w.note == "Band discography"
    assert [(m["title"], bool(m["url"])) for m in w.missing] == [("Second", True), ("Lost Album", False)]
    only = doctor.Scanner(None, None, studio=studio.Choice(per_artist={"band": "wikipedia"}))._completion("Band", lib, qobuz, None)
    assert only.source == "wikipedia"
    other = doctor.Scanner(None, None, studio=studio.Choice(per_artist={"someone": "wikipedia"}))._completion("Band", lib, qobuz, None)
    assert other.source == "qobuz"


def test_completion_falls_back_to_qobuz_when_wikipedia_fails(monkeypatch):
    from qobuz_gui import doctor, wikipedia
    lib = [{"name": "First", "artist": "Band"}]
    qobuz = [q("1", "First", 1990), q("2", "Second", 1993)]
    scanner = doctor.Scanner(None, None, studio=studio.Choice(default="wikipedia"))

    def down(*a, **k):
        raise wikipedia.WikipediaError("Couldn't reach Wikipedia (offline)")
    monkeypatch.setattr(wikipedia, "studio_albums", down)
    c = scanner._completion("Band", lib, qobuz, None)
    assert c.source == "qobuz" and (c.owned, c.total) == (1, 2) and "offline" in c.note
    monkeypatch.setattr(wikipedia, "studio_albums", lambda *a, **k: None)
    assert "Not found on Wikipedia" in scanner._completion("Band", lib, qobuz, None).note


def test_old_saved_results_still_load():
    from qobuz_gui import doctor
    old = {"artist": "A", "owned": 1, "total": 2, "missing": [], "verified": True}
    c = doctor.ArtistCompletion(**old)
    assert c.source == "qobuz" and c.qobuz_studio == [] and c.owned_titles == []


def test_missing_albums_export():
    from qobuz_gui import doctor
    comps = [
        doctor.ArtistCompletion("Zed", 1, 2, [{"title": "Late, \"Quoted\"", "year": "2001", "url": "https://play.qobuz.com/album/9"}], True),
        doctor.ArtistCompletion("abba", 2, 4, [{"title": "One", "year": "1976", "url": ""}, {"title": "Two", "year": "", "url": ""}],
                                True, source="wikipedia"),
        doctor.ArtistCompletion("Complete", 3, 3, [], True),
    ]
    rows = studio.missing_rows(comps)
    assert rows == [["abba", "One", "1976", "Wikipedia", ""], ["abba", "Two", "", "Wikipedia", ""],
                    ["Zed", 'Late, "Quoted"', "2001", "Qobuz", "https://play.qobuz.com/album/9"]]
    csv_text = studio.missing_csv(rows)
    assert csv_text.splitlines()[0] == "Artist,Album,Year,List,Qobuz link"
    assert '"Late, ""Quoted"""' in csv_text and csv_text.count("\n") == 4
    assert studio.missing_text(rows) == "abba\n  - One (1976)\n  - Two\n\nZed\n  - Late, \"Quoted\" (2001)\n"
    assert studio.missing_rows([]) == [] and studio.missing_text([]) == ""
