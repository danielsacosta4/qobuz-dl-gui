"""Safe writes to the library: a mount guard, previewable plans, checksummed copies and a
permanent journal with Undo.

Every change the app makes to the library (or to downloaded files) goes through a Plan:

    plan = Plan("Send ABBA — Voyage")
    plan.copy_tree(src, dest)          # each file copied with SHA-256 verification
    plan.retire(old_copy, library)     # moved to the hidden bin, not deleted
    plan.trash(src)                    # downloaded folder to the desktop Trash
    plan.describe()                    # → lines for the preview dialog
    plan.execute(journal)              # runs it, recording every step

and every executed plan can be undone from the journal (History in the Library tab).
"""

import datetime
import hashlib
import json
import os
import shutil
import sqlite3
import threading
import time
import urllib.parse
import uuid

MARKER = ".qobuz-dl-gui-library"
BIN_DIR = ".replaced-by-qobuz-dl-gui"
CHUNK = 1024 * 1024

_data_home = os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
JOURNAL_PATH = os.path.join(_data_home, "qobuz-dl-gui", "journal.sqlite")


class SafetyError(Exception):
    """A write was refused because doing it could damage the library."""


# ---- mount guard ---------------------------------------------------------------------
def marker_path(root):
    return os.path.join(root, MARKER)


def library_state(root):
    """'missing' (folder not there — share not mounted?), 'unmarked' (never confirmed as the
    library) or 'ok'."""
    if not root or not os.path.isdir(root):
        return "missing"
    return "ok" if os.path.isfile(marker_path(root)) else "unmarked"


def mark_library(root):
    """Confirms a folder as the library (done once, after the person agrees)."""
    if not os.path.isdir(root):
        raise SafetyError(f"{root} doesn't exist")
    with open(marker_path(root), "w", encoding="utf-8") as f:
        json.dump({"id": uuid.uuid4().hex, "created": datetime.datetime.now().isoformat(timespec="seconds"),
                   "note": "Marks this folder as the Qobuz-DL GUI library. If this file is missing the app "
                           "refuses to write here (protects against an unmounted share)."}, f, indent=1)


def require_library(root):
    """Raises SafetyError unless the library is mounted and confirmed."""
    state = library_state(root)
    if state == "missing":
        raise SafetyError(f"The library folder isn't available ({root}). Is the share mounted?")
    if state == "unmarked":
        raise SafetyError(f"{root} hasn't been confirmed as the library yet. Confirm it in the Library tab first.")


def _inside(path, root):
    path, root = os.path.abspath(path), os.path.abspath(root)
    return path == root or path.startswith(root + os.sep)


# ---- checksums -----------------------------------------------------------------------
def sha256_file(path, uncached=False, progress=None):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        if uncached:
            _drop_cache(f.fileno())
        for block in iter(lambda: f.read(CHUNK), b""):
            h.update(block)
            if progress:
                progress(len(block))
    return h.hexdigest()


def _drop_cache(fd):
    """Asks the kernel to forget cached pages so a read-back really reads from the share."""
    try:
        os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
    except (AttributeError, OSError):
        pass


def verified_copy(src, dst, progress=None, checking=None):
    """Copies src → dst through a .part file, hashing while writing, then re-reads dst from the
    share and compares. Returns the SHA-256. Raises OSError on a mismatch (dst is removed).

    progress(n) is called for bytes written; checking(n) for bytes read back (defaults to progress)."""
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    tmp = dst + ".part"
    h = hashlib.sha256()
    with open(src, "rb") as fi, open(tmp, "wb") as fo:
        for block in iter(lambda: fi.read(CHUNK), b""):
            h.update(block)
            fo.write(block)
            if progress:
                progress(len(block))
        fo.flush()
        os.fsync(fo.fileno())
        _drop_cache(fo.fileno())
    expected = h.hexdigest()
    got = sha256_file(tmp, uncached=True, progress=checking or progress)
    if got != expected:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise OSError(f"Checksum mismatch after copying {os.path.basename(src)} — the copy was discarded")
    os.replace(tmp, dst)
    try:
        st = os.stat(src)
        os.utime(dst, (st.st_atime, st.st_mtime))
    except OSError:
        pass
    return expected


# ---- desktop Trash (freedesktop.org spec, home trash) ---------------------------------
def _trash_dirs():
    base = os.path.join(_data_home, "Trash")
    files, info = os.path.join(base, "files"), os.path.join(base, "info")
    os.makedirs(files, exist_ok=True)
    os.makedirs(info, exist_ok=True)
    return files, info


def trash_put(path):
    """Moves a local file/folder to the desktop Trash (visible in Dolphin). Returns its trash path."""
    files, info = _trash_dirs()
    name = os.path.basename(path.rstrip(os.sep))
    target, n = name, 1
    while os.path.exists(os.path.join(files, target)) or os.path.exists(os.path.join(info, target + ".trashinfo")):
        n += 1
        target = f"{name}.{n}"
    with open(os.path.join(info, target + ".trashinfo"), "w", encoding="utf-8") as f:
        f.write("[Trash Info]\n"
                f"Path={urllib.parse.quote(os.path.abspath(path))}\n"
                f"DeletionDate={datetime.datetime.now().strftime('%Y-%m-%dT%H:%M:%S')}\n")
    dest = os.path.join(files, target)
    shutil.move(path, dest)
    return dest


def trash_restore(trash_path, original):
    if os.path.exists(original):
        raise SafetyError(f"Can't restore {original}: something is already there")
    os.makedirs(os.path.dirname(original), exist_ok=True)
    shutil.move(trash_path, original)
    info = os.path.join(os.path.dirname(os.path.dirname(trash_path)), "info",
                        os.path.basename(trash_path) + ".trashinfo")
    try:
        os.remove(info)
    except OSError:
        pass


def bin_path(library_root, path):
    """Where a library item goes in the hidden bin (same share, so moving is a rename)."""
    rel = os.path.relpath(path, library_root)
    target = os.path.join(library_root, BIN_DIR, rel)
    if os.path.exists(target):
        n = 2
        while os.path.exists(f"{target} ({n})"):
            n += 1
        target = f"{target} ({n})"
    return target


# ---- journal -----------------------------------------------------------------------
class Journal:
    """Permanent record of every executed plan and each of its steps (SQLite, safe across the
    app and its download subprocesses)."""

    def __init__(self, path=JOURNAL_PATH):
        self.path = path
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self._lock = threading.Lock()
        with self._conn() as c:
            c.execute("""CREATE TABLE IF NOT EXISTS batch (id INTEGER PRIMARY KEY, started REAL, title TEXT,
                         status TEXT, note TEXT DEFAULT '')""")
            c.execute("""CREATE TABLE IF NOT EXISTS op (id INTEGER PRIMARY KEY, batch INTEGER, seq INTEGER,
                         kind TEXT, src TEXT, dst TEXT, meta TEXT, status TEXT, at REAL)""")

    def _conn(self):
        c = sqlite3.connect(self.path, timeout=30)
        c.execute("PRAGMA journal_mode=WAL")
        return c

    def begin(self, title):
        with self._lock, self._conn() as c:
            return c.execute("INSERT INTO batch (started, title, status) VALUES (?, ?, 'running')",
                             (time.time(), title)).lastrowid

    def record(self, batch, seq, kind, src, dst, meta=None, status="done"):
        with self._lock, self._conn() as c:
            return c.execute("INSERT INTO op (batch, seq, kind, src, dst, meta, status, at) VALUES (?,?,?,?,?,?,?,?)",
                             (batch, seq, kind, src, dst, json.dumps(meta or {}), status, time.time())).lastrowid

    def set_op(self, op_id, status):
        with self._lock, self._conn() as c:
            c.execute("UPDATE op SET status=?, at=? WHERE id=?", (status, time.time(), op_id))

    def finish(self, batch, status, note=""):
        with self._lock, self._conn() as c:
            c.execute("UPDATE batch SET status=?, note=? WHERE id=?", (status, note, batch))

    def batches(self, limit=200):
        with self._conn() as c:
            rows = c.execute("""SELECT b.id, b.started, b.title, b.status, b.note,
                                (SELECT COUNT(*) FROM op o WHERE o.batch=b.id) FROM batch b
                                ORDER BY b.id DESC LIMIT ?""", (limit,)).fetchall()
        return [dict(id=r[0], started=r[1], title=r[2], status=r[3], note=r[4], ops=r[5]) for r in rows]

    def ops(self, batch):
        with self._conn() as c:
            rows = c.execute("SELECT id, seq, kind, src, dst, meta, status FROM op WHERE batch=? ORDER BY seq",
                             (batch,)).fetchall()
        return [dict(id=r[0], seq=r[1], kind=r[2], src=r[3], dst=r[4], meta=json.loads(r[5] or "{}"), status=r[6])
                for r in rows]

    # ---- undo
    def undo(self, batch):
        """Reverses a batch, newest step first. Steps that can't be reversed safely (the file
        changed since) are skipped and reported. Returns (undone, skipped_messages)."""
        undone, skipped = 0, []
        for op in reversed(self.ops(batch)):
            if op["status"] != "done":
                continue
            try:
                _undo_op(op)
                self.set_op(op["id"], "undone")
                undone += 1
            except Exception as e:
                skipped.append(f"{op['kind']} {os.path.basename(op['dst'] or op['src'] or '')}: {e}")
        self.finish(batch, "undone" if not skipped else "partly undone",
                    "; ".join(skipped)[:2000])
        return undone, skipped


def _undo_op(op):
    kind, src, dst, meta = op["kind"], op["src"], op["dst"], op["meta"]
    if kind == "copy":
        if not os.path.exists(dst):
            return
        if sha256_file(dst) != meta.get("sha256"):
            raise SafetyError("the file changed after it was copied, so it was left in place")
        os.remove(dst)
        _remove_empty_dirs(os.path.dirname(dst), meta.get("stop_at"))
    elif kind == "move":                       # library item moved into the bin, or over-written file moved aside
        if not os.path.exists(dst):
            raise SafetyError("it's no longer in the bin")
        if os.path.exists(src):
            raise SafetyError("something new is in its place")
        os.makedirs(os.path.dirname(src), exist_ok=True)
        shutil.move(dst, src)
        _remove_empty_dirs(os.path.dirname(dst), meta.get("stop_at"))
    elif kind == "rename":
        if not os.path.exists(dst):
            raise SafetyError("it's no longer where it was moved to")
        if os.path.lexists(src):
            raise SafetyError("something new is in its old place")
        os.makedirs(os.path.dirname(src), exist_ok=True)
        os.rename(dst, src)
    elif kind == "trash":
        trash_restore(dst, src)
    elif kind == "tags":
        _write_tags(src, meta.get("old", {}), replace=True)
    elif kind == "add_file":
        if os.path.exists(dst) and sha256_file(dst) != meta.get("sha256"):
            raise SafetyError("the file changed after it was added")
        if os.path.exists(dst):
            os.remove(dst)
    elif kind == "mkdir":
        try:
            os.rmdir(dst)
        except OSError:
            pass                               # not empty any more: leave it
    else:
        raise SafetyError(f"can't undo '{kind}'")


def _remove_empty_dirs(path, stop_at):
    while path and stop_at and _inside(path, stop_at) and os.path.abspath(path) != os.path.abspath(stop_at):
        try:
            os.rmdir(path)
        except OSError:
            return
        path = os.path.dirname(path)


# ---- tags (for album-artist fixes) ------------------------------------------------------
def _read_tags(path):
    import mutagen
    audio = mutagen.File(path, easy=True)
    if audio is None:
        return {}
    return {k: list(v) for k, v in audio.items()}


def _write_tags(path, tags, replace=False):
    import mutagen
    audio = mutagen.File(path, easy=True)
    if audio is None:
        raise SafetyError("not an audio file")
    if replace:
        for k in list(audio.keys()):
            if k not in tags:
                del audio[k]
    for k, v in tags.items():
        audio[k] = v
    audio.save()


# ---- plans -----------------------------------------------------------------------------
class Plan:
    """A list of steps that can be previewed, then executed and journaled."""

    def __init__(self, title, library_root=None):
        self.title = title
        self.library_root = library_root
        self.steps = []          # dicts: kind, src, dst, size, extra
        self.batch = None

    # -- building
    def copy_tree(self, src, dest, skip_prefix="~tmp_"):
        for root, dirs, files in os.walk(src):
            dirs.sort()
            for f in sorted(files):
                if f.startswith(skip_prefix) or f.endswith(".part"):
                    continue
                s = os.path.join(root, f)
                d = os.path.join(dest, os.path.relpath(s, src))
                if os.path.exists(d) and self.library_root and _inside(d, self.library_root):
                    # never overwrite in place: the existing file goes to the bin first
                    self.steps.append(dict(kind="move", src=d, dst=None, size=0, extra={"why": "replaced by a new copy"}))
                self.steps.append(dict(kind="copy", src=s, dst=d, size=os.path.getsize(s), extra={}))

    def retire(self, path):
        self.steps.append(dict(kind="move", src=path, dst=None, size=0, extra={"why": "replaced"}))

    def trash(self, path):
        self.steps.append(dict(kind="trash", src=path, dst=None, size=0, extra={}))

    def relocate(self, src, dst):
        """Renames a folder inside the library (an artist folder to its right letter). Refuses
        when something is already at the destination."""
        self.steps.append(dict(kind="rename", src=src, dst=dst, size=0, extra={}))

    def set_tags(self, path, tags):
        self.steps.append(dict(kind="tags", src=path, dst=None, size=0, extra={"tags": tags}))

    def add_file(self, path, data):
        self.steps.append(dict(kind="add_file", src=None, dst=path, size=len(data), extra={"data": data}))

    # -- preview
    def total_bytes(self):
        return sum(s["size"] for s in self.steps if s["kind"] == "copy")

    def describe(self):
        """Human-readable preview lines, grouped."""
        lines = []
        copies = [s for s in self.steps if s["kind"] == "copy"]
        if copies:
            dests = sorted({os.path.dirname(s["dst"]) for s in copies})
            top = os.path.commonpath(dests) if dests else ""
            lines.append(f"Copy {len(copies)} file{'s' if len(copies) != 1 else ''} "
                         f"({_human(self.total_bytes())}) to {top}, each checked with SHA-256")
        for s in self.steps:
            if s["kind"] == "move":
                lines.append(f"Move to the hidden bin ({s['extra'].get('why', '')}): {s['src']}")
            elif s["kind"] == "trash":
                lines.append(f"Move to the desktop Trash: {s['src']}")
            elif s["kind"] == "tags":
                pass
            elif s["kind"] == "add_file":
                lines.append(f"Add {s['dst']}")
            elif s["kind"] == "rename":
                lines.append(f"Move {s['src']}  →  {s['dst']}")
        tags = [s for s in self.steps if s["kind"] == "tags"]
        if tags:
            folders = sorted({os.path.dirname(s["src"]) for s in tags})
            lines.append(f"Rewrite tags in {len(tags)} file{'s' if len(tags) != 1 else ''} "
                         f"({', '.join(os.path.basename(f) for f in folders[:3])}"
                         f"{'…' if len(folders) > 3 else ''}) — the old tags are kept for Undo")
        return lines

    # -- execution
    def execute(self, journal, progress=None, should_stop=None):
        """Runs every step, journaling each. Stops at the first failure; what was done stays
        recorded (and undoable). Returns the batch id."""
        if self.library_root:
            require_library(self.library_root)
        for s in self.steps:
            # what each step is allowed to touch: library steps stay inside the confirmed
            # library; only "trash" (downloaded folders) works outside it
            if s["kind"] == "trash":
                continue
            if not self.library_root:
                raise SafetyError("No library set for this change")
            targets = ([s["dst"]] if s["kind"] in ("copy", "add_file") else
                       [s["src"], s["dst"]] if s["kind"] == "rename" else [s["src"]])
            for p in targets:
                if not p or not _inside(p, self.library_root) or _inside(p, os.path.join(self.library_root, BIN_DIR)):
                    raise SafetyError(f"Refusing to change something outside the library: {p}")
        self.batch = journal.begin(self.title)
        # every copied byte is written once and read back once to be checked
        total = 2 * self.total_bytes() or 1
        done_bytes = [0]
        try:
            for seq, s in enumerate(self.steps):
                if should_stop and should_stop():
                    raise InterruptedError("Stopped")
                kind = s["kind"]
                if kind == "copy":
                    def tick(n, name="Copying " + os.path.basename(s["src"])):
                        done_bytes[0] += n
                        if progress:
                            progress(done_bytes[0], total, name)

                    def check(n, name="Checking " + os.path.basename(s["src"])):
                        tick(n, name)
                    created = []
                    d = os.path.dirname(s["dst"])
                    while d and not os.path.isdir(d):
                        created.append(d)
                        d = os.path.dirname(d)
                    digest = verified_copy(s["src"], s["dst"], tick, check)
                    for folder in reversed(created):
                        journal.record(self.batch, seq, "mkdir", None, folder)
                    journal.record(self.batch, seq, "copy", s["src"], s["dst"],
                                   {"sha256": digest, "size": s["size"], "stop_at": self.library_root})
                elif kind == "move":
                    target = bin_path(self.library_root, s["src"])
                    os.makedirs(os.path.dirname(target), exist_ok=True)
                    os.rename(s["src"], target)
                    journal.record(self.batch, seq, "move", s["src"], target,
                                   {"stop_at": os.path.join(self.library_root, BIN_DIR)})
                elif kind == "rename":
                    if os.path.lexists(s["dst"]):
                        raise SafetyError(f"Something is already at {s['dst']} — nothing was moved")
                    created = []
                    d = os.path.dirname(s["dst"])
                    while d and not os.path.isdir(d):
                        created.append(d)
                        d = os.path.dirname(d)
                    for folder in reversed(created):
                        os.mkdir(folder)
                        journal.record(self.batch, seq, "mkdir", None, folder)
                    os.rename(s["src"], s["dst"])
                    journal.record(self.batch, seq, "rename", s["src"], s["dst"])
                elif kind == "trash":
                    dest = trash_put(s["src"])
                    journal.record(self.batch, seq, "trash", s["src"], dest)
                elif kind == "tags":
                    old = _read_tags(s["src"])
                    _write_tags(s["src"], s["extra"]["tags"])
                    journal.record(self.batch, seq, "tags", s["src"], None, {"old": old, "new": s["extra"]["tags"]})
                elif kind == "add_file":
                    if os.path.exists(s["dst"]):
                        self.retire_now(journal, seq, s["dst"])
                    data = s["extra"]["data"]
                    with open(s["dst"] + ".part", "wb") as f:
                        f.write(data)
                        f.flush()
                        os.fsync(f.fileno())
                    os.replace(s["dst"] + ".part", s["dst"])
                    digest = sha256_file(s["dst"], uncached=True)
                    if digest != hashlib.sha256(data).hexdigest():
                        os.remove(s["dst"])
                        raise OSError(f"Checksum mismatch writing {s['dst']}")
                    journal.record(self.batch, seq, "add_file", None, s["dst"], {"sha256": digest})
            journal.finish(self.batch, "done")
        except Exception as e:
            journal.finish(self.batch, "failed", str(e)[:2000])
            raise
        return self.batch

    def retire_now(self, journal, seq, path):
        target = bin_path(self.library_root, path)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        os.rename(path, target)
        journal.record(self.batch, seq, "move", path, target, {"stop_at": os.path.join(self.library_root, BIN_DIR)})


def _human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"
        n /= 1024
