"""A small on-disk JSON cache (SQLite) shared by the MusicBrainz check and the Library doctor."""

import hashlib
import json
import os
import sqlite3
import threading
import time

_dir = os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache"),
                    "qobuz-dl-gui")
PATH = os.path.join(_dir, "cache.sqlite")
_lock = threading.Lock()
_conn = None

DAY = 24 * 3600


def _db():
    global _conn
    if _conn is None:
        os.makedirs(os.path.dirname(PATH), exist_ok=True)
        _conn = sqlite3.connect(PATH, check_same_thread=False)
        _conn.execute("CREATE TABLE IF NOT EXISTS kv (ns TEXT, k TEXT, v TEXT, t REAL, PRIMARY KEY (ns, k))")
        _conn.commit()
    return _conn


def get(ns, key, max_age=None):
    with _lock:
        row = _db().execute("SELECT v, t FROM kv WHERE ns=? AND k=?", (ns, str(key))).fetchone()
    if not row:
        return None
    if max_age is not None and time.time() - row[1] > max_age:
        return None
    try:
        return json.loads(row[0])
    except ValueError:
        return None


def put(ns, key, value):
    with _lock:
        _db().execute("INSERT OR REPLACE INTO kv VALUES (?, ?, ?, ?)",
                      (ns, str(key), json.dumps(value), time.time()))
        _db().commit()


def clear(ns=None):
    with _lock:
        if ns:
            _db().execute("DELETE FROM kv WHERE ns=?", (ns,))
        else:
            _db().execute("DELETE FROM kv")
        _db().commit()


# ---- files (cover images) -------------------------------------------------------------
def file_path(ns, key):
    return os.path.join(_dir, ns, hashlib.sha1(str(key).encode("utf-8")).hexdigest())


def read_file(ns, key, max_age=None):
    path = file_path(ns, key)
    try:
        if max_age is not None and time.time() - os.path.getmtime(path) > max_age:
            return None
        with open(path, "rb") as f:
            return f.read()
    except OSError:
        return None


def write_file(ns, key, data):
    path = file_path(ns, key)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".part"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except OSError:
        pass


def prune_files(ns, max_age):
    """Removes cached files older than max_age seconds. Returns how many went."""
    root = os.path.join(_dir, ns)
    gone = 0
    now = time.time()
    try:
        names = os.listdir(root)
    except OSError:
        return 0
    for name in names:
        p = os.path.join(root, name)
        try:
            if now - os.path.getmtime(p) > max_age:
                os.remove(p)
                gone += 1
        except OSError:
            pass
    return gone
