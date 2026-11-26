import pytest

from qobuz_gui import prefs, studio, wikipedia

WIKI = {"albums": [{"title": "First", "year": 1990}, {"title": "Second", "year": 1993}, {"title": "Third", "year": 1997}],
        "page": "Band discography", "lang": "en"}


def titles(found):
    return [a["title"] for a in found["albums"]]


def test_no_corrections_leaves_the_answer_alone():
    assert studio.apply_overrides(WIKI, {}) is WIKI
    assert studio.apply_overrides(None, {}) is None


def test_add_and_remove_are_applied_and_ordered_by_year():
    o = {"add": [{"title": "Middle", "year": 1995}, {"title": "second (Remastered)", "year": 1993}],
         "remove": ["Third (Deluxe Edition)", "Never Was There"]}
    got = studio.apply_overrides(WIKI, o)
    assert titles(got) == ["First", "Second", "Middle"]          # "second (Remastered)" is already there
    assert got["edited"] and got["removed"] == ["Third"]         # only what was really taken off
    assert [a.get("added", False) for a in got["albums"]] == [False, False, True]
    assert titles(WIKI) == ["First", "Second", "Third"]          # the cached answer is not changed


def test_a_list_of_your_own_when_wikipedia_has_none():
    got = studio.apply_overrides(None, {"add": [{"title": "Only One", "year": 2001}]})
    assert titles(got) == ["Only One"] and got["page"] == "your own list"
    assert studio.apply_overrides(None, {"remove": ["X"]}) is None


def test_editing_the_corrections():
    o = studio.edited_override({}, {"op": "add", "title": "Extra", "year": 2000})
    assert o["add"] == [{"title": "Extra", "year": 2000}]
    o = studio.edited_override(o, {"op": "add", "title": "extra"})               # the same album again: no duplicate
    assert len(o["add"]) == 1
    o = studio.edited_override(o, {"op": "remove", "title": "Third"})
    assert o["remove"] == ["Third"]
    o = studio.edited_override(o, {"op": "add", "title": "Third"})               # adding it back un-removes it
    assert o["remove"] == [] and len(o["add"]) == 2
    o = studio.edited_override(o, {"op": "remove", "title": "Extra"})            # removing your own addition
    assert [a["title"] for a in o["add"]] == ["Third"]
    o = studio.edited_override(o, {"op": "page", "page": "Band (group) discography", "lang": "en"})
    assert o["page"] == "Band (group) discography"
    assert studio.edited_override(o, {"op": "reset"}) == {}
    only_remove = studio.edited_override({}, {"op": "remove", "title": "Third"})
    assert studio.edited_override(only_remove, {"op": "restore", "title": "third"}) == {}   # nothing left: forgotten


def test_parse_title_year():
    assert studio.parse_title_year("Title (1999)") == ("Title", 1999)
    assert studio.parse_title_year("Title, 2004") == ("Title", 2004)
    assert studio.parse_title_year("Title") == ("Title", None)
    assert studio.parse_title_year("1999") == ("1999", None)                     # an album called "1999"
    assert studio.parse_title_year("  ") == ("", None)


def test_page_ref():
    assert wikipedia.page_ref("https://pt.wikipedia.org/wiki/Banda_(grupo)#Discografia") == ("pt", "Banda (grupo)")
    assert wikipedia.page_ref("https://en.m.wikipedia.org/wiki/Band%27s_discography?x=1") == ("en", "Band's discography")
    assert wikipedia.page_ref("Band discography", "fr") == ("fr", "Band discography")
    assert wikipedia.page_ref("", "en") == ("en", "")


@pytest.fixture
def store(monkeypatch):
    data = {}
    monkeypatch.setattr(prefs, "get_str", lambda k, d="": data.get(k, d))
    monkeypatch.setattr(prefs, "get_json", lambda k, d=None: data.get(k, d))
    monkeypatch.setattr(prefs, "set_json", lambda k, v: data.__setitem__(k, v))
    return data


def test_albums_for_uses_your_page_and_your_corrections(store, monkeypatch):
    asked = []

    def by_name(artist, lang="en", refresh=False):
        asked.append(("name", artist, lang))
        return WIKI

    def by_page(title, lang="en", refresh=False):
        asked.append(("page", title, lang))
        return {"albums": [{"title": "Other", "year": 2010}], "page": title, "lang": lang}
    monkeypatch.setattr(wikipedia, "studio_albums", by_name)
    monkeypatch.setattr(wikipedia, "studio_albums_from_page", by_page)

    assert studio.Choice.load().albums_for("The Band", "Band") is WIKI and asked == [("name", "Band", "en")]
    studio.edit_override("The Band", {"op": "remove", "title": "Second"})
    studio.edit_override("The Band", {"op": "add", "title": "Fourth", "year": 2001})
    got = studio.Choice.load().albums_for("the band", "Band")                   # stored per artist, any spelling
    assert titles(got) == ["First", "Third", "Fourth"] and got["removed"] == ["Second"]
    assert studio.Choice.load().albums_for("Someone Else") is WIKI              # other artists are untouched

    studio.edit_override("The Band", {"op": "page", "page": "Band (group)", "lang": "pt"})
    got = studio.Choice.load().albums_for("The Band", "Band")
    assert asked[-1] == ("page", "Band (group)", "pt") and titles(got) == ["Fourth", "Other"]      # by year
    assert studio.edit_override("The Band", {"op": "reset"}) == {} and store[prefs.STUDIO_OVERRIDES] == {}


def test_compare_marks_albums_you_added():
    found = studio.apply_overrides(WIKI, {"add": [{"title": "Fourth", "year": 2001}]})
    rows = studio.compare([], found["albums"], [], set())
    assert {r["title"]: r["added"] for r in rows} == {"First": False, "Second": False, "Third": False, "Fourth": True}
