import pytest

from qobuz_gui import cache, wikipedia

TABLE = """
<div class="mw-parser-output">
<div class="hatnote navigation-not-searchable">Main article: <a>Band albums</a></div>
<table class="wikitable plainrowheaders" style="text-align:center;">
<caption>List of studio albums, with selected chart positions</caption>
<tbody>
<tr><th scope="col" rowspan="2">Title</th><th scope="col" rowspan="2">Album details</th><th scope="col" colspan="2">Peak chart positions</th></tr>
<tr><th scope="col"><a href="/wiki/UK">UK</a><sup id="cite_ref-1" class="reference"><a href="#cite_note-1">[1]</a></sup></th><th scope="col">US</th></tr>
<tr><th scope="row"><i><a href="/wiki/First_Light">First Light</a></i></th>
<td style="text-align:left;"><ul><li>Released: 22 February 1993</li><li>Label: Example</li><li>Formats: CD, LP</li></ul></td><td>52</td><td>32</td></tr>
<tr><th scope="row"><i><a href="/wiki/Second_Wind">Second Wind</a></i></th>
<td style="text-align:left;"><ul><li>Released: 13 March 1995</li></ul></td><td>6</td><td>88</td></tr>
<tr><th scope="row"><i><a href="/wiki/Third_Rail">Third Rail</a></i><sup class="reference"><a>[A]</a></sup></th>
<td><ul><li>Released: 2 October 2000</li></ul></td><td>1</td><td>1</td></tr>
<tr><td colspan="4">"—" denotes a recording that did not chart or was not released in that territory.</td></tr>
</tbody></table>
<div class="reflist"><ol class="references"><li id="cite_note-1"><i>Some Magazine</i>. Retrieved 2020.</li></ol></div>
</div>
"""

YEAR_FIRST = """
<table class="wikitable"><tr><th>Year</th><th>Album</th><th>Label</th></tr>
<tr><td>1976</td><td><i>Alpha</i></td><td>Example</td></tr>
<tr><td>1977</td><td><i>Beta</i></td><td>Example</td></tr>
<tr><td>1979</td><td><i>Gamma</i></td><td>Example</td></tr></table>
"""

LIST = """
<ul><li><i><a>Honest Mistake</a></i> (1995)</li><li><i>Second Try</i> – 1998</li><li>2003 – <i>Third</i></li>
<li><i>No Year Here</i></li></ul>
<div class="reflist"><ul><li><i>Some Site</i> review</li></ul></div>
"""


def test_table_with_titles_years_and_notes():
    got = wikipedia.parse_section(TABLE)
    assert [(a["title"], a["year"]) for a in got] == [("First Light", 1993), ("Second Wind", 1995), ("Third Rail", 2000)]


def test_table_that_starts_with_a_year_column():
    got = wikipedia.parse_section(YEAR_FIRST)
    assert [(a["title"], a["year"]) for a in got] == [("Alpha", 1976), ("Beta", 1977), ("Gamma", 1979)]


def test_list_when_there_is_no_table_and_references_are_ignored():
    got = wikipedia.parse_section(LIST)
    assert [(a["title"], a["year"]) for a in got] == [("Honest Mistake", 1995), ("Second Try", 1998),
                                                      ("Third", 2003), ("No Year Here", None)]


def test_duplicates_and_junk():
    html = "<table><tr><th>Title</th><th>Year</th></tr>" \
           "<tr><th scope='row'><i>Same</i></th><td>2001</td></tr>" \
           "<tr><th scope='row'><i>Same (Deluxe Edition)</i></th><td>2011</td></tr>" \
           "<tr><th scope='row'>—</th><td>2002</td></tr></table>"
    got = wikipedia.parse_section(html)
    assert [a["title"] for a in got] == ["Same"]
    assert wikipedia.parse_section("") == [] and wikipedia.parse_section("<p>No albums.</p>") == []


class FakeWiki:
    """A stand-in for the MediaWiki API."""

    def __init__(self, pages, sections, texts, down=False, redirects=None, disambig=()):
        self.pages, self.sections, self.texts, self.down = pages, sections, texts, down
        self.redirects, self.disambig, self.calls = redirects or {}, set(disambig), []

    def __call__(self, lang, params):
        self.calls.append(params["action"])
        if self.down:
            raise wikipedia.WikipediaError("Couldn't reach Wikipedia (offline)")
        if params["action"] == "query":
            pages = []
            for t in params["titles"].split("|"):
                real = self.redirects.get(t, t)
                page = {"title": real}
                if real not in self.pages:
                    page["missing"] = True
                if real in self.disambig:
                    page["pageprops"] = {"disambiguation": ""}
                pages.append(page)
            return {"query": {"redirects": [{"from": a, "to": b} for a, b in self.redirects.items()], "pages": pages}}
        if params["prop"] == "sections":
            return {"parse": {"sections": self.sections.get(params["page"], [])}}
        return {"parse": {"text": self.texts[(params["page"], params["section"])]}}


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    cache.clear("wiki_studio")
    monkeypatch.setattr(wikipedia, "GAP", 0)


def test_finds_the_discography_page_and_caches_the_answer(monkeypatch):
    fake = FakeWiki({"Band discography", "Band"},
                    {"Band discography": [{"line": "Albums", "index": "1"}, {"line": "Studio albums", "index": "2"}]},
                    {("Band discography", "2"): TABLE})
    monkeypatch.setattr(wikipedia, "_get", fake)
    got = wikipedia.studio_albums("Band")
    assert got["page"] == "Band discography" and [a["title"] for a in got["albums"]] == ["First Light", "Second Wind", "Third Rail"]
    n = len(fake.calls)
    assert wikipedia.studio_albums("Band") == got and len(fake.calls) == n        # cached
    assert n <= wikipedia.MAX_REQUESTS


def test_redirect_and_the_main_article_as_a_fallback(monkeypatch):
    fake = FakeWiki({"Singer albums discography", "Singer"},
                    {"Singer": [{"line": "<span>Studio albums</span>", "index": "7"}]},
                    {("Singer", "7"): LIST},
                    redirects={"Singer discography": "Singer albums discography"})
    monkeypatch.setattr(wikipedia, "_get", fake)
    got = wikipedia.studio_albums("Singer")
    assert got["page"] == "Singer" and len(got["albums"]) == 4        # the discography page had no such section


def test_not_found_is_remembered_but_an_outage_is_not(monkeypatch):
    fake = FakeWiki(set(), {}, {})
    monkeypatch.setattr(wikipedia, "_get", fake)
    assert wikipedia.studio_albums("Nobody Known") is None
    n = len(fake.calls)
    assert wikipedia.studio_albums("Nobody Known") is None and len(fake.calls) == n
    monkeypatch.setattr(wikipedia, "_get", FakeWiki(set(), {}, {}, down=True))
    with pytest.raises(wikipedia.WikipediaError):
        wikipedia.studio_albums("Someone Else")
    monkeypatch.setattr(wikipedia, "_get", FakeWiki({"Someone Else"}, {"Someone Else": [{"line": "Studio albums", "index": "3"}]},
                                                    {("Someone Else", "3"): LIST}))
    assert wikipedia.studio_albums("Someone Else") is not None          # the outage wasn't cached as "missing"


def test_disambiguation_pages_are_skipped(monkeypatch):
    fake = FakeWiki({"Common Name"}, {"Common Name": [{"line": "Studio albums", "index": "1"}]},
                    {("Common Name", "1"): LIST}, disambig={"Common Name"})
    monkeypatch.setattr(wikipedia, "_get", fake)
    assert wikipedia.studio_albums("Common Name") is None


def test_portuguese_section_name(monkeypatch):
    fake = FakeWiki({"Banda", "Banda discografia"},
                    {"Banda discografia": [{"line": "Álbuns de estúdio", "index": "4"}]},
                    {("Banda discografia", "4"): YEAR_FIRST})
    monkeypatch.setattr(wikipedia, "_get", fake)
    got = wikipedia.studio_albums("Banda", lang="pt")
    assert got["lang"] == "pt" and len(got["albums"]) == 3


def test_a_page_you_name(monkeypatch):
    fake = FakeWiki({"Band (group)", "Empty page"},
                    {"Band (group)": [{"line": "Studio albums", "index": "3"}], "Empty page": [{"line": "History", "index": "1"}]},
                    {("Band (group)", "3"): TABLE}, redirects={"Band group": "Band (group)"})
    monkeypatch.setattr(wikipedia, "_get", fake)
    got = wikipedia.studio_albums_from_page("Band group")
    assert got["page"] == "Band (group)" and len(got["albums"]) == 3
    n = len(fake.calls)
    assert wikipedia.studio_albums_from_page("Band group") == got and len(fake.calls) == n      # cached
    assert wikipedia.studio_albums_from_page("Empty page") is None                             # no such section
    assert wikipedia.studio_albums_from_page("No such page") is None
