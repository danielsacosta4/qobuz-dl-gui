"""Background work: a logged-in Qobuz API client for search, favourites and login checks."""

import contextlib
import io
import threading
import traceback

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from . import config, matching, studio
from .config import strip_ansi
from .qobuz_guard import QobuzBlocked, guard, looks_blocked


class _Signals(QObject):
    done = Signal(object)
    failed = Signal(str)


class Task(QRunnable):
    """Runs fn() on the thread pool; calls on_done(result) or on_fail(message) on the GUI thread."""

    def __init__(self, fn, on_done=None, on_fail=None):
        super().__init__()
        self.fn = fn
        self.signals = _Signals()
        if on_done:
            self.signals.done.connect(on_done)
        if on_fail:
            self.signals.failed.connect(on_fail)

    def run(self):
        try:
            result = self.fn()
        except SystemExit as e:
            self.signals.failed.emit(str(e.code) if isinstance(e.code, str) else "The operation was aborted.")
        except Exception as e:
            traceback.print_exc()
            self.signals.failed.emit(str(e) or e.__class__.__name__)
        else:
            self.signals.done.emit(result)


_running = set()  # keeps tasks (and their signal objects) alive until they report back


def run_async(fn, on_done=None, on_fail=None):
    task = Task(fn, on_done, on_fail)
    task.setAutoDelete(False)
    _running.add(task)
    task.signals.done.connect(lambda _r: _running.discard(task))
    task.signals.failed.connect(lambda _m: _running.discard(task))
    QThreadPool.globalInstance().start(task)
    return task


class AuthError(Exception):
    pass


class Session:
    """One shared qopy.Client, created on first use and reset when the settings change."""

    def __init__(self):
        self._client = None
        self._lock = threading.Lock()
        self.label = ""

    def reset(self):
        with self._lock:
            self._client = None
            self.label = ""

    def client(self):
        with self._lock:
            if self._client is None:
                guard.before(True)              # a block stops even the login
                self._client = self._login()
            return self._client

    def _login(self):
        from qobuz_dl.qopy import Client

        cfg = config.load()
        email, password, app_id, secrets, token = config.credentials(cfg)
        if not token and len(password) <= 60:
            raise AuthError("No auth token is set. Add it in Settings.")
        force_english = True
        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out):
                client = Client(email, password, app_id, secrets,
                                user_auth_token=token, force_english=force_english)
        except SystemExit:
            text = strip_ansi(out.getvalue()).strip()
            if looks_blocked(text):
                guard.trip("Qobuz says this account is blocked (USER_BLOCKED).")
                raise QobuzBlocked(guard.blocked_message())
            if "401" in text:
                raise AuthError("Qobuz rejected the auth token (401). It has probably expired — "
                                "paste a fresh one in Settings.")
            raise AuthError(text.splitlines()[0] if text else "Login failed.")
        self.label = getattr(client, "label", "") or ""
        return client

    # ---- API helpers (call from worker threads) ------------------------------
    # Every request goes through qobuz_guard: spaced out, counted against the budget when `interactive` is
    # False (use session.background() for anything that isn't a direct click), and stopped for good when Qobuz
    # says the account is blocked.
    def background(self):
        return _Background(self)

    def search(self, kind, query, limit, interactive=True):
        c = self.client()
        fn = {"albums": c.search_albums, "tracks": c.search_tracks,
              "artists": c.search_artists, "playlists": c.search_playlists}[kind]
        result = guard.call(lambda: fn(query, limit), interactive) or {}
        return (result.get(kind) or {}).get("items", [])

    def favorites(self, kind, limit=500, interactive=True):
        c = self.client()
        items, offset = [], 0
        while True:
            page = guard.call(lambda: c.get_favorites(fav_type=kind, limit=min(limit, 500), offset=offset),
                              interactive) or {}
            page = page.get(kind) or {}
            batch = page.get("items", [])
            items.extend(batch)
            total = page.get("total", len(items))
            offset += len(batch)
            if not batch or offset >= total or len(items) >= limit:
                return items

    def album(self, album_id, interactive=True):
        c = self.client()
        return guard.call(lambda: c.get_album_meta(album_id), interactive)

    def artist_albums(self, artist_id, limit=500, interactive=True):
        c = self.client()
        items, offset = [], 0
        name = ""
        while True:
            data = guard.call(lambda: c.api_call("artist/get", id=artist_id, extra="albums", limit=100,
                                                 offset=offset), interactive) or {}
            name = name or data.get("name", "")
            page = data.get("albums") or {}
            batch = page.get("items", [])
            items.extend(batch)
            offset += len(batch)
            if not batch or offset >= page.get("total", 0) or len(items) >= limit:
                return name, items

    def find_album(self, artist, title, interactive=True):
        """The Qobuz album matching an artist and a title (for a download), or None. One search request."""
        items = self.search("albums", f"{artist} {title}", 25, interactive)
        mine = [i for i in items if matching.artist_matches((i.get("artist") or {}).get("name", ""),
                                                             matching.artist_names(artist))]
        hits = studio.find_qobuz(title, mine)
        return hits[0] if hits else None


class _Background:
    """The same API, but every request counts against the daily budget for non-interactive use."""

    _WRAPPED = ("search", "favorites", "album", "artist_albums", "find_album")

    def __init__(self, session):
        self._s = session

    def __getattr__(self, name):
        attr = getattr(self._s, name)
        if name in self._WRAPPED:
            return lambda *a, **k: attr(*a, interactive=False, **k)
        return attr
