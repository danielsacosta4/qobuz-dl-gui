from qobuz_gui import cache, classify, logparse
from qobuz_gui.shortcuts import links_in


def test_letter():
    assert classify.letter("Ágata") == "A"
    assert classify.letter("The Cure") == "T"
    assert classify.letter("2Pac") == "#"


def test_prefer_explicit_drops_clean_twin():
    clean = {"id": 1, "title": "Album", "artist": {"name": "X"}, "parental_warning": False}
    explicit = {"id": 2, "title": "Album", "artist": {"name": "X"}, "parental_warning": True}
    assert [i["id"] for i in classify.prefer_explicit([clean, explicit])] == [2]


def test_ansi_is_stripped_and_coloured():
    assert logparse.plain("\x1b[32mok\x1b[0m") == "ok"
    assert "ok" in logparse.to_html("\x1b[32mok\x1b[0m")


def test_parser_gives_lines():
    events = logparse.OutputParser().feed("first line\nsecond line\n")
    lines = [e[2] for e in events if e[0] == "line"]
    assert lines == ["first line", "second line"]


def test_links_in_text():
    text = ("look: https://play.qobuz.com/album/abc123, and https://open.qobuz.com/track/9.\n"
            "again https://play.qobuz.com/album/abc123 and https://example.com/no")
    assert links_in(text) == ["https://play.qobuz.com/album/abc123", "https://open.qobuz.com/track/9"]


def test_file_cache_round_trip():
    assert cache.read_file("covers", "http://x/1.jpg") is None
    cache.write_file("covers", "http://x/1.jpg", b"img")
    assert cache.read_file("covers", "http://x/1.jpg") == b"img"
    assert cache.read_file("covers", "http://x/1.jpg", max_age=-1) is None
    assert cache.prune_files("covers", -1) == 1
