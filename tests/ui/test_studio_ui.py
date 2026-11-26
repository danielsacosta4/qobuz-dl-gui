from PySide6.QtCore import Qt

from qobuz_gui import doctor, library, studio, studio_dialog, wikipedia

from .conftest import album, settle, wait_for

BAND = [album("1", "First", 1990), album("2", "Second", 1993), album("3", "Third (Deluxe Edition)", 1997, "single"),
        album("4", "Qobuz Only Studio", 2001), album("5", "Live in Tokyo", 1999, "live")]
WIKI = {"albums": [{"title": "First", "year": 1990}, {"title": "Second", "year": 1993}, {"title": "Third", "year": 1997},
                   {"title": "Lost Album", "year": 2005}], "page": "Band discography", "lang": "en"}


def kinds(win):
    t = win.search.table
    return {t.item(r, 1).text(): t.item(r, 3).text() for r in range(t.rowCount())}


def setup(make_window, env, asked):
    def fake(artist, lang="en", refresh=False):
        asked.append(artist)
        return WIKI if artist == "Band" else None
    env.monkeypatch.setattr(wikipedia, "studio_albums", fake)
    win, q = make_window(safe=False)
    win.index = library.LibraryIndex.from_navidrome([{"name": "First", "artist": "Band"},
                                                     {"name": "Third", "artist": "Band"}], None)
    win.tabs.setCurrentWidget(win.search)
    return win, q


def test_album_search_switches_between_qobuz_and_wikipedia(make_window, env):
    asked = []
    win, _q = setup(make_window, env, asked)
    s = win.search
    s.show(BAND, "albums", "Albums for “band”")
    assert s.studio_box.isVisible() and s.btn_compare.isVisible() and s._list_artist() == "Band"
    assert kinds(win)["Third (Deluxe Edition)"] != "Studio album" and kinds(win)["Qobuz Only Studio"] == "Studio album"

    s.studio_box.setCurrentIndex(1)
    s._studio_chosen(1)
    assert wait_for(lambda: kinds(win)["Third (Deluxe Edition)"] == "Studio album", 5), kinds(win)
    assert kinds(win)["Qobuz Only Studio"] == "Other" and kinds(win)["Live in Tokyo"] == "Live album"
    assert "2 of 4" in s.heading.text() and "Wikipedia" in s.heading.text()
    assert asked == ["Band"] and studio.Choice.load().source_for("Band") == studio.WIKI

    s.show(BAND, "albums", "the same search again")                    # remembered, and no second request
    settle(0.2)
    assert kinds(win)["Third (Deluxe Edition)"] == "Studio album" and asked == ["Band"]

    s.studio_box.setCurrentIndex(0)
    s._studio_chosen(0)
    settle(0.2)
    assert kinds(win)["Qobuz Only Studio"] == "Studio album" and studio.Choice.load().source_for("Band") == studio.QOBUZ


def test_a_mixed_search_asks_wikipedia_about_at_most_five_artists(make_window, env):
    asked = []
    win, _q = setup(make_window, env, asked)
    env.monkeypatch.setattr("qobuz_gui.prefs.get_str", lambda k, d="": "wikipedia" if k == "studio_default" else d)
    mixed = BAND + [album(str(100 + i), f"Other {i}", 2000, artist=f"Other Artist {i}", artist_id=100 + i)
                    for i in range(12)]
    win.search.show(mixed, "albums", "Albums for “mixed”")
    wait_for(lambda: len(asked) >= 5, 10)
    settle(0.5)
    assert len(asked) == 5 and asked[0] == "Band"


def test_wikipedia_outage_keeps_qobuz_types(make_window, env):
    win, _q = setup(make_window, env, [])
    studio.set_source("Band", studio.WIKI)

    def down(artist, lang="en", refresh=False):
        raise wikipedia.WikipediaError("Couldn't reach Wikipedia (offline)")
    env.monkeypatch.setattr(wikipedia, "studio_albums", down)
    win.search.show(BAND, "albums", "offline")
    assert wait_for(lambda: "offline" in win.status_label.text(), 5), win.status_label.text()
    assert kinds(win)["Qobuz Only Studio"] == "Studio album"


def test_compare_in_complete_my_artists_makes_no_qobuz_request_and_remembers_the_choice(make_window, env):
    asked = []
    win, q = setup(make_window, env, asked)
    slim = [doctor._slim(a, "Band") for a in BAND]
    comp = doctor.Scanner(None, None)._completion("Band", [{"name": "First", "artist": "Band"},
                                                           {"name": "Third", "artist": "Band"}], slim, None)
    assert (comp.owned, comp.total) == (1, 3)
    h = win.health
    h.completion = [comp]
    h.hide_complete.setChecked(False)
    h._fill_artists()
    h.art_table.selectRow(0)
    settle(0.1)
    seen = {}

    def fake_exec(dialog):
        seen["rows"] = {r["title"]: (r["on_qobuz"], r["on_wikipedia"], r["owned"]) for r in dialog.rows}
        dialog.r_wiki.setChecked(True)                                  # you pick Wikipedia
        dialog.downloadRequested.emit([("Second", "")])                 # and download a Wikipedia-only album
    env.monkeypatch.setattr(studio_dialog.StudioCompare, "exec", fake_exec)
    q.calls.clear()
    h._compare_lists()
    assert wait_for(lambda: "rows" in seen, 5), "the Compare window never opened"
    assert seen["rows"]["Third"] == (False, True, True) and seen["rows"]["Lost Album"] == (False, True, False)
    c = h.completion[0]
    assert c.source == studio.WIKI and (c.owned, c.total) == (2, 4)
    assert studio.Choice.load().source_for("Band") == studio.WIKI
    assert doctor.load_results()[2][0].source == studio.WIKI           # saved: survives a restart
    assert wait_for(lambda: win.queued, 5)                              # the link was looked up because you asked
    assert win.queued == [(["https://play.qobuz.com/album/found-Second"], "Band — Second")]
    assert [c[0] for c in q.calls] == ["find_album"]                   # the only Qobuz request in the whole flow

    h._show_missing()
    table = h.missing_table
    assert table.item(1, 3).text() == "looked up on Qobuz when you download"
    assert table.item(1, 0).flags() & Qt.ItemIsUserCheckable


def test_correcting_a_list_from_search_updates_the_results(make_window, env):
    asked = []
    win, q = setup(make_window, env, asked)
    studio.set_source("Band", studio.WIKI)
    s = win.search
    s.show(BAND, "albums", "Albums for “band”")
    assert wait_for(lambda: kinds(win)["Third (Deluxe Edition)"] == "Studio album", 5)
    assert "2 of 4" in s.heading.text()

    steps = [{"op": "remove", "title": "Third"}, None]
    seen = []

    def fake_exec(dialog):
        seen.append(([r["title"] for r in dialog.rows if r["on_wikipedia"]], list(dialog.removed)))
        action = steps[len(seen) - 1]
        if action:
            dialog.overrideRequested.emit(action)
    env.monkeypatch.setattr(studio_dialog.StudioCompare, "exec", fake_exec)
    q.calls.clear()
    s._compare()
    assert wait_for(lambda: len(seen) == 2, 10), seen
    assert seen[0] == (["First", "Second", "Third", "Lost Album"], [])
    assert seen[1] == (["First", "Second", "Lost Album"], ["Third"])
    settle(0.2)
    assert kinds(win)["Third (Deluxe Edition)"] != "Studio album"      # the results follow your correction
    assert "1 of 3" in s.heading.text() and q.calls == []
    assert studio.Choice.load().override_for("band") == {"page": "", "lang": "", "add": [], "remove": ["Third"]}
