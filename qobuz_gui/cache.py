"""A small on-disk JSON cache (SQLite) shared by the MusicBrainz check and the Library doctor."""

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
