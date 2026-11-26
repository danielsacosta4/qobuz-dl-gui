import pytest

from qobuz_gui import doctor, navidrome, studio, wikipedia
from qobuz_gui.doctor import Scanner
from qobuz_gui.qobuz_guard import QobuzBlocked


class NoQobuz:
    """A session that fails the test on any Qobuz request."""

    def __getattr__(self, name):
        raise AssertionError(f"Qobuz was contacted: session.{name}")


class FakeNavidrome:
    def __init__(self, albums):
        self.albums = albums

    def all_albums(self):
        return self.albums

    def album(self, album_id):
        return {"song": [{"suffix": "flac", "bitDepth": 16, "samplingRate": 44100, "path": f"A/Band/{album_id}/1.flac"}],
                "coverArt": ""}


ALBUMS = [{"id": "a1", "name": "First", "artist": "Band", "songCount": 1, "created": "x"},
          {"id": "a2", "name": "Third", "artist": "Band", "songCount": 1, "created": "x"}]
WIKI = {"albums": [{"title": "First", "year": 1990}, {"title": "Second", "year": 1993},
                   {"title": "Third", "year": 1997}], "page": "Band discography", "lang": "en"}


@pytest.fixture
def scanner(monkeypatch):
    monkeypatch.setattr(navidrome.Navidrome, "from_settings", classmethod(lambda cls: FakeNavidrome(ALBUMS)))
    monkeypatch.setattr(wikipedia, "studio_albums", lambda artist, lang="en", refresh=False: WIKI)

    def make(**kw):
        return Scanner(NoQobuz(), None, **kw)
    return make


def test_default_scan_never_contacts_qobuz_and_counts_from_wikipedia(scanner):
    issues, completion = scanner().run()
    assert len(completion) == 1
    c = completion[0]
    assert c.source == studio.WIKI and (c.owned, c.total) == (2, 3) and c.note == "Band discography"
    assert [(m["title"], m["url"]) for m in c.missing] == [("Second", "")]          # no link until you download
    assert {i.kind for i in issues} == {doctor.NO_COVER}                           # local checks still run


def test_wikipedia_outage_leaves_no_completion_and_a_note(scanner, monkeypatch):
    def down(*a, **k):
        raise wikipedia.WikipediaError("Couldn't reach Wikipedia (offline)")
    monkeypatch.setattr(wikipedia, "studio_albums", down)
    s = scanner()
    _issues, completion = s.run()
    assert completion == [] and "offline" in s.note


def test_qobuz_check_is_opt_in_only_for_chosen_artists_and_stops_when_blocked(monkeypatch):
    more = ALBUMS + [{"id": "b1", "name": "Other", "artist": "Else", "songCount": 1, "created": "x"}]
    monkeypatch.setattr(navidrome.Navidrome, "from_settings", classmethod(lambda cls: FakeNavidrome(more)))
    calls = []

    class Blocked:
        def search(self, *a, **k):
            calls.append(a)
            raise QobuzBlocked("Qobuz says this account is blocked. Contact Qobuz support.")
    s = Scanner(Blocked(), None, use_qobuz=True, only_artists=["Band"])
    issues, _completion = s.run()
    assert len(calls) == 1 and "blocked" in s.note                  # one refused request, then it stops
    assert not any(i.artist == "Else" for i in issues)               # only the chosen artist was looked at
    assert {i.kind for i in issues} <= {doctor.NO_COVER}             # no library-wide checks in a chosen-artists run


def test_the_library_report_makes_no_qobuz_request(monkeypatch, tmp_path, capsys):
    from qobuz_gui import report
    monkeypatch.setattr(navidrome, "configured", lambda: True)
    monkeypatch.setattr(navidrome.Navidrome, "from_settings", classmethod(lambda cls: FakeNavidrome(ALBUMS)))
    monkeypatch.setattr(report, "library_folder", lambda: str(tmp_path / "nolib"))
    out = tmp_path / "rep"
    assert report.run(str(out)) == 0                 # report._NoQobuz raises if Qobuz is touched
    assert "no Qobuz requests" in (out / "report.md").read_text(encoding="utf-8")
