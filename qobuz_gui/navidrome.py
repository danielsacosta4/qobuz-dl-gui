"""Navidrome connection through the Subsonic / OpenSubsonic API (the same one Maré uses)."""

import hashlib
import secrets as _secrets

import requests

from . import prefs

CLIENT = "qobuz-dl-gui"
API_VERSION = "1.16.1"
KEYRING_KEY = "navidrome_password"
DEFAULT_URL = ""   # each person enters their own server in Settings


class NavidromeError(Exception):
    pass


def settings():
    return dict(url=prefs.get_str(prefs.NAVIDROME_URL, ""), user=prefs.get_str(prefs.NAVIDROME_USER, ""))


def load_password():
    try:
        import keyring
        return keyring.get_password("qobuz-dl-gui", KEYRING_KEY) or prefs.get_str(prefs.NAVIDROME_PASSWORD, "")
    except Exception:
        return prefs.get_str(prefs.NAVIDROME_PASSWORD, "")


def store_password(value):
    """Keeps the password in KWallet / Secret Service; falls back to the app settings file."""
    try:
        import keyring
        if value:
            keyring.set_password("qobuz-dl-gui", KEYRING_KEY, value)
        else:
            try:
                keyring.delete_password("qobuz-dl-gui", KEYRING_KEY)
            except Exception:
                pass
        prefs.set_value(prefs.NAVIDROME_PASSWORD, "")
        return None
    except Exception:
        prefs.set_value(prefs.NAVIDROME_PASSWORD, value)
        return "The system keyring isn't available, so the Navidrome password was saved in the app settings."


def configured():
    s = settings()
    return bool(s["url"] and s["user"])


class Navidrome:
    def __init__(self, url, user, password, timeout=20):
        self.url = url.rstrip("/")
        self.user = user
        self.password = password
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers["User-Agent"] = CLIENT

    @classmethod
    def from_settings(cls):
        s = settings()
        if not (s["url"] and s["user"]):
            raise NavidromeError("Navidrome isn't set up. Add it in Settings.")
        return cls(s["url"], s["user"], load_password())

    def _auth(self):
        salt = _secrets.token_hex(6)
        token = hashlib.md5((self.password + salt).encode("utf-8")).hexdigest()
        return dict(u=self.user, t=token, s=salt, v=API_VERSION, c=CLIENT, f="json")

    def call(self, method, **params):
        try:
            r = self.session.get(f"{self.url}/rest/{method}", params={**self._auth(), **params},
                                 timeout=self.timeout)
        except requests.RequestException as e:
            raise NavidromeError(f"Can't reach Navidrome: {e}") from e
        if r.status_code != 200:
            raise NavidromeError(f"Navidrome answered HTTP {r.status_code}")
        try:
            body = r.json()["subsonic-response"]
        except (ValueError, KeyError) as e:
            raise NavidromeError("That address doesn't look like a Navidrome server.") from e
        if body.get("status") != "ok":
            err = body.get("error") or {}
            code = err.get("code")
            if code in (40, 41):
                raise NavidromeError("Navidrome rejected the username or password.")
            if code == 50:
                raise NavidromeError("This Navidrome user isn't allowed to do that (an admin account is needed).")
            raise NavidromeError(err.get("message") or "Navidrome returned an error.")
        return body

    # ---- API -------------------------------------------------------------------
    def ping(self):
        body = self.call("ping")
        return body.get("serverVersion") or body.get("version", "")

    def all_albums(self, page=500):
        """Every album: dicts with id, name, artist, artistId, year, songCount, created,
        coverArt, musicBrainzId and explicitStatus (when the server reports them)."""
        out, offset = [], 0
        while True:
            body = self.call("getAlbumList2", type="alphabeticalByName", size=page, offset=offset)
            batch = (body.get("albumList2") or {}).get("album") or []
            out.extend(batch)
            if len(batch) < page:
                return out
            offset += page

    def album(self, album_id):
        return self.call("getAlbum", id=album_id).get("album") or {}

    def start_scan(self):
        return self.call("startScan").get("scanStatus") or {}

    def scan_status(self):
        return self.call("getScanStatus").get("scanStatus") or {}


def album_quality(album_details):
    """(lossless, bit depth, sample rate kHz, bitrate) of the weakest track, like library.folder_quality."""
    songs = album_details.get("song") or []
    if not songs:
        return ()
    worst = None
    for s in songs:
        suffix = str(s.get("suffix") or "").lower()
        lossless = 1 if suffix in ("flac", "alac", "wav", "aiff", "ape", "wv") else 0
        bits = int(s.get("bitDepth") or (16 if lossless else 0))
        rate = float(s.get("samplingRate") or 0) / 1000 or (44.1 if lossless else 0)
        q = (lossless, bits, rate, int(s.get("bitRate") or 0))
        worst = q if worst is None or q < worst else worst
    return worst


def is_explicit(album):
    return str(album.get("explicitStatus") or "").lower() in ("explicit", "e")
