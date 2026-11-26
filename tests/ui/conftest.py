"""Window-level tests: the real MainWindow, headless (QT_QPA_PLATFORM=offscreen), with Qobuz, Navidrome,
Wikipedia, MusicBrainz and the update checks replaced by fakes. Nothing here touches the network."""
import os
import time

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QEventLoop                     # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from qobuz_gui import cache, config, musicbrainz, navidrome, prefs, qobuz_guard, theme, updates, wikipedia  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    theme.init(app)
    return app


def wait_for(cond, seconds=10):
    """Runs the event loop until cond() is true (or the time is up). Returns cond()."""
    app = QApplication.instance()
    end = time.time() + seconds
    while time.time() < end and not cond():
        app.processEvents(QEventLoop.AllEvents, 50)
        time.sleep(0.01)
    return bool(cond())


def settle(seconds=0.3):
    """Lets queued signals and short background tasks finish."""
    app = QApplication.instance()
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents(QEventLoop.AllEvents, 20)
        time.sleep(0.01)


class FakeQobuz:
    """Stands in for client.Session and records every Qobuz request the app would make."""
    label = "fake"

    def __init__(self):
        self.calls = []
        self.albums_by_artist = {}      # artist id -> [album dicts]
        self.search_results = {}        # kind -> [items]
        self.missing_titles = set()     # titles find_album doesn't find

    def background(self):
        return self

    def reset(self):
        pass

    def client(self):
        self.calls.append(("client",))
        return self

    def search(self, kind, query, limit, interactive=True):
        self.calls.append(("search", kind, query, interactive))
        if kind == "artists":
            return self.search_results.get(kind, [{"id": 7, "name": query}])
        return list(self.search_results.get(kind, []))

    def favorites(self, kind, limit=500, interactive=True):
        self.calls.append(("favorites", kind, interactive))
        return []

    def album(self, album_id, interactive=True):
        self.calls.append(("album", album_id, interactive))
        return {"id": album_id, "title": "Album", "artist": {"name": "Band"},
                "tracks": {"items": [{"track_number": 1, "title": "Track One", "duration": 60}]}}

    def artist_albums(self, artist_id, limit=500, interactive=True):
        self.calls.append(("artist_albums", artist_id, interactive))
        return "Band", list(self.albums_by_artist.get(artist_id, []))

    def find_album(self, artist, title, interactive=True):
        self.calls.append(("find_album", artist, title, interactive))
        return None if title in self.missing_titles else {"id": "found-" + title, "title": title}


class NoQobuz:
    """A session that fails the test on any Qobuz request."""
    label = ""

    def background(self):
        return self

    def reset(self):
        pass

    def __getattr__(self, name):
        raise AssertionError(f"Qobuz was contacted: session.{name}")


def album(id_, title, year, kind="album", artist="Band", artist_id=7, hires=False, explicit=False):
    return {"id": id_, "title": title, "version": "", "release_date_original": f"{year}-03-01",
            "release_type": kind, "parental_warning": explicit, "hires_streamable": hires,
            "maximum_bit_depth": 24 if hires else 16, "maximum_sampling_rate": 96 if hires else 44.1,
            "tracks_count": 10, "duration": 2400, "artist": {"name": artist, "id": artist_id},
            "label": {"name": "Label"}}


@pytest.fixture
def env(qapp, monkeypatch, tmp_path):
    """A clean slate for one test: empty settings and cache, an open Qobuz gate, no network."""
    prefs.store().clear()
    cache.clear()
    guard = qobuz_guard.guard
    guard.resume()
    guard._state.clear()
    guard.configure(budget=0, gap=0)
    monkeypatch.setattr(qobuz_guard, "GAP", 0)
    monkeypatch.setattr(prefs, "SAFE_GAP", 0)

    def no_network(*a, **k):
        raise RuntimeError("no network in tests")
    monkeypatch.setattr(updates, "check", no_network)
    monkeypatch.setattr(updates, "check_gui", no_network)
    monkeypatch.setattr(musicbrainz.ArtistCheck, "prepare", lambda self, items, **k: self)
    monkeypatch.setattr(wikipedia, "studio_albums", lambda artist, lang="en", refresh=False: None)
    monkeypatch.setattr(navidrome, "configured", lambda: False)
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: QMessageBox.Ok))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: QMessageBox.Ok))

    staging, lib = tmp_path / "Downloads", tmp_path / "Music"
    staging.mkdir()
    lib.mkdir()
    cfg = config.load()
    config.set_value(cfg, "directory", str(staging))
    config.save(cfg)
    prefs.set_value(prefs.LIBRARY_DIR, str(lib))
    yield type("Env", (), {"staging": staging, "lib": lib, "monkeypatch": monkeypatch})
    guard.resume()
    prefs.store().clear()
    if os.path.isfile(config.CONFIG_FILE):
        os.remove(config.CONFIG_FILE)


@pytest.fixture
def make_window(env):
    """make_window(session=None, safe=None) -> (MainWindow, FakeQobuz). The window is closed after the test."""
    from qobuz_gui import app as appmod
    made = []

    def make(session=None, safe=None):
        if safe is not None:
            prefs.set_value(prefs.SAFE_MODE, safe)
        win = appmod.MainWindow()
        q = session or FakeQobuz()
        win.session = q
        for tab in (win.search, win.health, win.download, win.discover, win.settings):
            tab.session = q
        win.show()
        wait_for(lambda: win.index is not None, 5)
        settle(0.2)
        win.queued = []
        win.download.enqueue = lambda srcs, title=None: win.queued.append((list(srcs), title)) or [object()]
        win.download.filter_owned = lambda entries: entries
        made.append(win)
        return win, q
    yield make
    for win in made:
        win._quitting = True
        win._shutdown()
        win.close()
    settle(0.2)
