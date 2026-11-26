"""Small helpers and constants shared by the Search tab's parts."""

from . import cache

WEB_URL = "https://play.qobuz.com/"
THUMB = 48
ALBUM_TTL = 7 * cache.DAY
COVER_TTL = 60 * cache.DAY


def _first(*values):
    for v in values:
        if v:
            return v
    return ""


def _image(item, kind, size="thumbnail"):
    if kind == "albums":
        img = item.get("image") or {}
        return _first(img.get(size), img.get("small"), img.get("large"))
    if kind == "tracks":
        img = (item.get("album") or {}).get("image") or {}
        return _first(img.get(size), img.get("small"), img.get("large"))
    if kind == "artists":
        img = item.get("image") or {}
        if isinstance(img, dict):
            return _first(img.get("small"), img.get("medium"), img.get("large"), item.get("picture"))
        return _first(item.get("picture"))
    if kind == "playlists":
        for key in ("images300", "images150", "image_rectangle", "image_rectangle_mini", "images"):
            val = item.get(key)
            if isinstance(val, list) and val:
                return val[0]
    return ""


def _quality(item):
    if item.get("hires_streamable") or item.get("hires"):
        return f"Hi-Res {item.get('maximum_bit_depth', 24)}-bit / {item.get('maximum_sampling_rate', 96)} kHz"
    if "maximum_bit_depth" in item or "streamable" in item:
        return "CD 16-bit / 44.1 kHz"
    return ""


def _duration(seconds):
    seconds = int(seconds or 0)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
