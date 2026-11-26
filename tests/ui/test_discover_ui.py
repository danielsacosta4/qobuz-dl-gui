from qobuz_gui import navidrome, prefs

from .conftest import album, settle, wait_for

ALBUMS = [{"name": "Arrival", "artist": "ABBA", "artistId": "n1"},
          {"name": "Harvest", "artist": "Neil Young", "artistId": "n2"},
          {"name": "Kid A", "artist": "Radiohead", "artistId": "n3"}]
SIMILAR = {"n1": ["Roxette", "Ace of Base"], "n2": ["Crosby, Stills & Nash", "Roxette"],
           "n3": ["Thom Yorke", "Atoms for Peace", "Roxette"]}


class FakeNavidrome:
    asked = []

    def all_albums(self):
        return ALBUMS

    def album_list(self, kind, limit=200):
        return [{"artist": "Radiohead", "playCount": 90}, {"artist": "ABBA", "playCount": 5}] if kind == "frequent" else []

    def artist_info(self, aid, count=20):
        FakeNavidrome.asked.append(aid)
        return {"artistInfo2": {"similarArtist": [{"name": n} for n in SIMILAR[aid]]}}


def discover_window(make_window, env):
    FakeNavidrome.asked = []
    env.monkeypatch.setattr(navidrome, "configured", lambda: True)
    env.monkeypatch.setattr(navidrome.Navidrome, "from_settings", classmethod(lambda cls: FakeNavidrome()))
    win, q = make_window(safe=False)
    q.calls.clear()
    win.tabs.setCurrentWidget(win.discover)
    win.discover.find()
    assert wait_for(lambda: not win.discover.busy() and win.discover.rows, 10), win.discover.info.text()
    return win, q


def test_finding_suggestions_uses_navidrome_only(make_window, env):
    win, q = discover_window(make_window, env)
    rows = win.discover.rows
    assert q.calls == []                                               # zero Qobuz contact
    assert rows[0]["name"] == "Roxette" and rows[0]["because"][0] == "Radiohead"
    assert {r["name"] for r in rows} == {"Roxette", "Ace of Base", "Crosby, Stills & Nash", "Thom Yorke", "Atoms for Peace"}
    assert FakeNavidrome.asked[0] == "n3"                              # your most played artist is asked first


def test_only_the_buttons_contact_qobuz(make_window, env):
    win, q = discover_window(make_window, env)
    d = win.discover
    q.albums_by_artist[7] = [album("alb1", "Latest", 2025), album("old1", "Old", 1990)]
    d.table.selectRow(0)
    settle(0.1)
    searched = []
    win.search.search = lambda: searched.append(win.search.query.text())
    d._browse()
    settle(0.1)
    assert searched == ["Roxette"] and q.calls == []                   # the search itself is one request you started
    d._download()
    assert wait_for(lambda: win.queued, 5)
    assert win.queued == [(["https://play.qobuz.com/album/alb1"], "Band — Latest")]
    assert [c[0] for c in q.calls] == ["search", "artist_albums"] and all(c[-1] is True for c in q.calls)


def test_dismiss_and_restore(make_window, env):
    win, _q = discover_window(make_window, env)
    d = win.discover
    d.table.selectRow(0)
    d._dismiss()
    assert prefs.get_json(prefs.DISCOVER_DISMISSED) == ["roxette"] and d.table.rowCount() == 4
    d.show_dismissed.setChecked(True)
    d.table.selectRow(0)
    d._dismiss()
    assert not prefs.get_json(prefs.DISCOVER_DISMISSED)
