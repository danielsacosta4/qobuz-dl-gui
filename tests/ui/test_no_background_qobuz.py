"""Nothing the app does by itself may contact Qobuz: opening, scanning, the background check, the report."""
from qobuz_gui import doctor, navidrome, prefs, wikipedia

from .conftest import NoQobuz, settle, wait_for

ALBUMS = [{"id": "a1", "name": "First", "artist": "Band", "songCount": 1, "created": "x"},
          {"id": "a2", "name": "Third", "artist": "Band", "songCount": 1, "created": "x"}]
WIKI = {"albums": [{"title": "First", "year": 1990}, {"title": "Second", "year": 1993}, {"title": "Third", "year": 1997}],
        "page": "Band discography", "lang": "en"}


class FakeNavidrome:
    def all_albums(self):
        return ALBUMS

    def album(self, album_id):
        return {"song": [{"suffix": "flac", "bitDepth": 16, "samplingRate": 44100, "path": f"B/Band/{album_id}/1.flac"}],
                "coverArt": "x"}

    def album_list(self, kind, limit=200):
        return []

    def start_scan(self):
        return {}


def test_scan_and_background_check_never_contact_qobuz(make_window, env):
    env.monkeypatch.setattr(navidrome, "configured", lambda: True)
    env.monkeypatch.setattr(navidrome.Navidrome, "from_settings", classmethod(lambda cls: FakeNavidrome()))
    env.monkeypatch.setattr(wikipedia, "studio_albums", lambda artist, lang="en", refresh=False: WIKI)
    win, _q = make_window(session=NoQobuz())                 # any Qobuz request fails the test

    prefs.set_value(prefs.AUTO_HOURS, 6)
    win.run_automation(manual=True)
    assert wait_for(lambda: not win._auto_running and not win.health.busy(), 20), "the background check never finished"
    settle(0.2)
    assert "nothing to fix" in prefs.get_str(prefs.AUTO_LAST_RESULT)
    assert win.queued == []                                   # and it downloads nothing
    completion = win.health.completion
    assert len(completion) == 1 and (completion[0].owned, completion[0].total) == (2, 3)
    assert completion[0].missing[0]["url"] == ""              # no Qobuz link until you click Download

    win.health.scan()                                         # the manual Scan library button
    assert wait_for(lambda: not win.health.busy(), 20)
    assert doctor.load_results()[2][0].source == "wikipedia"


def test_browsing_the_tabs_asks_qobuz_nothing(make_window):
    win, _q = make_window(session=NoQobuz(), safe=False)
    for i in range(win.tabs.count()):
        win.tabs.setCurrentIndex(i)
        settle(0.15)
    win.refresh_current()
    settle(0.2)
