from qobuz_gui import automation, doctor
from qobuz_gui.library import AlbumFolder


def issue(kind, **kw):
    base = dict(kind=kind, artist="A", album="B", detail="", folder="/lib/A/A/B")
    base.update(kw)
    return doctor.Issue(**base)


def test_only_the_location_fix_can_be_automatic():
    issues = [
        issue(doctor.LOCATION, album="D", extra={"to": "/lib/B/A"}),
        issue(doctor.LOCATION, album="E"),                                     # no target
        issue(doctor.NO_COVER, album="C", cover_url="http://x/c.jpg"),         # a download: never automatic
        issue(doctor.DUPLICATE, album="F"),                                    # never automatic
        issue(doctor.LOCATION, album="G", extra={"to": "/lib/C/A"}),           # an exception
    ]
    exc = [issues[4].key]
    got = automation.safe_fixes(issues, [doctor.NO_COVER, doctor.LOCATION, doctor.DUPLICATE], exc)
    assert [i.album for i in got] == ["D"]
    assert automation.safe_fixes(issues, [], exc) == []


def test_auto_sendable(tmp_path):
    existing = tmp_path / "A" / "Artist" / "Here"
    existing.mkdir(parents=True)
    albums = [
        AlbumFolder("/d/1", "New", dest=str(tmp_path / "A" / "Artist" / "New")),
        AlbumFolder("/d/2", "Here", dest=str(existing)),                         # would merge
        AlbumFolder("/d/3", "Other", dest=str(tmp_path / "x"), others=[("/lib/old", ())]),
        AlbumFolder("/d/4", "Half", dest=str(tmp_path / "y"), state="incomplete"),
        AlbumFolder("/d/5", "Mix", dest=str(tmp_path / "z"), kind="playlist"),
        AlbumFolder("/d/6", "Nowhere"),                                           # library not found
    ]
    assert [a.name for a in automation.auto_sendable(albums)] == ["New"]


def test_due_and_seconds_left():
    assert not automation.due(None, 0)
    assert automation.due(None, 6)
    assert not automation.due(1000, 6, now=1000 + 3600)
    assert automation.due(1000, 6, now=1000 + 6 * 3600)
    assert automation.seconds_left(1000, 1, now=1000 + 600) == 3000
    assert automation.seconds_left(None, 0) is None


def test_summary():
    assert automation.summary() == "nothing to fix"
    assert automation.summary(2, 1) == "2 problems fixed, 1 fix failed"
