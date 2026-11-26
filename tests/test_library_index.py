import os
import sqlite3

from qobuz_gui import library
from qobuz_gui.library import AlbumFolder, LibraryIndex


def make_db(path, ids):
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS downloads (id TEXT)")
        conn.execute("DELETE FROM downloads")
        conn.executemany("INSERT INTO downloads VALUES (?)", [(i,) for i in ids])


def test_refresh_history_reads_only_the_history(tmp_path):
    db = str(tmp_path / "qobuz_dl.db")
    make_db(db, ["1"])
    idx = LibraryIndex.from_navidrome([{"name": "Arrival", "artist": "ABBA"}], db)
    assert idx.status("1", "Someone", "Else") == "history"
    assert idx.status("2", "Someone", "Else") == ""
    make_db(db, ["1", "2"])
    assert idx.refresh_history(db) is idx
    assert idx.status("2", "Someone", "Else") == "history"
    assert idx.status(None, "ABBA", "Arrival (Deluxe Edition)") == "library"


def test_build_from_folders_uses_history(tmp_path):
    lib = tmp_path / "lib"
    (lib / "A" / "ABBA" / "Arrival").mkdir(parents=True)
    db = str(tmp_path / "h.db")
    make_db(db, ["9"])
    idx = LibraryIndex.build(str(lib), db)
    assert idx.status(None, "ABBA", "Arrival") == "library"
    assert idx.status("9", "X", "Y") == "history"


def test_tree_signature_changes_when_folders_change(tmp_path):
    a = library.tree_signature(str(tmp_path))
    assert a == library.tree_signature(str(tmp_path))
    (tmp_path / "Artist - Album").mkdir()
    assert library.tree_signature(str(tmp_path)) != a


def test_resolve_new_album_and_existing_copy(tmp_path):
    lib = tmp_path / "lib"
    old = lib / "A" / "ABBA" / "Arrival [FLAC 16]"
    old.mkdir(parents=True)
    new = AlbumFolder(str(tmp_path / "dl" / "Arrival"), "Arrival", artist="ABBA")
    library.resolve(str(lib), new)
    assert new.dest == str(lib / "A" / "ABBA" / "Arrival")
    assert new.dest_lib == str(lib)
    assert [p for p, _ in new.others] == [str(old)]
    fresh = AlbumFolder(str(tmp_path / "dl" / "Voulez-Vous"), "Voulez-Vous", artist="New Band")
    library.resolve(str(lib), fresh)
    assert fresh.others == [] and fresh.dest_status == "New artist"
    assert not os.path.exists(fresh.dest)


def test_default_action_never_swaps_explicit_for_clean():
    a = AlbumFolder("/d/x", "Album", others=[("/lib/Album (Explicit)", (1, 16, 44.1, 0))], quality=(1, 24, 96, 0))
    assert library.default_action(a) == "skip"
    b = AlbumFolder("/d/x", "Album", others=[("/lib/Album", (1, 16, 44.1, 0))], quality=(1, 24, 96, 0))
    assert library.default_action(b) == "replace"
    c = AlbumFolder("/d/x", "Album", others=[("/lib/Album", (1, 24, 96, 0))], quality=(1, 16, 44.1, 0))
    assert library.default_action(c) == "skip"
