import os

import pytest

from qobuz_gui import discover


def art(id_, name, **kw):
    return dict(id=id_, name=name, **kw)


ALBUMS = [
    {"name": "Arrival", "artist": "ABBA", "artistId": "n1", "genre": "Pop"},
    {"name": "Voulez-Vous", "artist": "ABBA", "artistId": "n1", "genre": "Pop"},
    {"name": "Harvest", "artist": "Neil Young • Crazy Horse", "artistId": "n2", "genre": "Rock"},
    {"name": "Kid A", "artist": "Radiohead", "artistId": "n3"},
    {"name": "Compilation", "artist": "Various Artists", "artistId": "n9"},
]


def test_library_artists_split_joined_names_and_skip_various():
    names, counts = discover.library_artists(ALBUMS)
    assert set(names) == {"abba", "neil young", "crazy horse", "radiohead"}
    assert counts["abba"] == 2
    assert discover.artist_ids(ALBUMS) == {"abba": "n1", "radiohead": "n3", "various artists": "n9"}


def test_parse_similar_handles_missing_ids_and_junk():
    info = {"artistInfo2": {"similarArtist": [
        {"id": "n5", "name": "Thom Yorke", "albumCount": 3}, {"name": "Not In Library"},
        {"id": "n5", "name": "thom yorke"}, {"id": "x"}, "junk"]}}
    got = discover.parse_similar(info)
    assert [(a["id"], a["name"]) for a in got] == [("n5", "Thom Yorke"), ("Not In Library", "Not In Library")]
    assert discover.parse_similar({}) == [] and discover.parse_similar(None) == []


def test_weights_use_play_counts_then_stars():
    freq = [{"artist": "ABBA", "playCount": 50}, {"artist": "Neil Young • Crazy Horse", "playCount": 3}]
    w = discover.artist_weights(freq, [{"artist": "Neil Young"}])
    assert w["abba"] > w["neil young"] > w["crazy horse"]
    ranked = discover.artist_weights([{"artist": "X"}, {"artist": "Y"}], [])
    assert ranked["x"] > ranked["y"]


def test_pick_sources_most_played_first():
    names, counts = discover.library_artists(ALBUMS)
    assert discover.pick_sources(names, counts, {"neil young": 5.0}, 2) == ["Neil Young", "ABBA"]
    assert discover.pick_sources(names, counts, {}, 1) == ["ABBA"]


def test_merge_scores_drops_owned_and_dismissed():
    similar = {
        "ABBA": [art(1, "Roxette"), art(2, "Neil Young"), art(3, "Ace of Base")],
        "Crazy Horse": [art(1, "Roxette"), art(4, "Dismissed Band")],
    }
    out = discover.merge(similar, {"abba", "neil young", "crazy horse"}, dismissed={"dismissed band"})
    by = {s["name"]: s for s in out}
    assert [s["name"] for s in out][:2] == ["Roxette", "Ace of Base"]
    assert "Neil Young" not in by
    assert by["Roxette"]["because"] == ["ABBA", "Crazy Horse"] and by["Roxette"]["score"] == 2
    assert by["Dismissed Band"]["dismissed"] is True


def test_merge_weights_boost_artists_you_play():
    similar = {"ABBA": [art(1, "A-ish")], "Neil Young": [art(2, "N-ish")]}
    out = discover.merge(similar, {"abba", "neil young"}, weights={"abba": 1.0, "neil young": 10.0})
    assert [s["name"] for s in out] == ["N-ish", "A-ish"]


class FakeNavidrome:
    def __init__(self, info=None, fail_plays=False):
        self.info, self.fail_plays, self.asked = info or {}, fail_plays, []

    def all_albums(self):
        return ALBUMS

    def album_list(self, kind, limit=200):
        if self.fail_plays:
            raise RuntimeError("HTTP 500")
        return [{"artist": "Radiohead", "playCount": 90}] if kind == "frequent" else []

    def artist_info(self, aid, count=20):
        self.asked.append(aid)
        return self.info.get(aid, {})


def similar_to(*names):
    return {"artistInfo2": {"similarArtist": [{"id": f"s{i}", "name": n} for i, n in enumerate(names)]}}


def test_find_suggestions_reads_navidrome_only_and_asks_played_artists_first():
    nd = FakeNavidrome({"n3": similar_to("Thom Yorke", "ABBA"), "n1": similar_to("Roxette", "Thom Yorke")})
    rows, note = discover.find_suggestions(nd, gap=0)
    assert [r["name"] for r in rows] == ["Thom Yorke", "Roxette"]            # ABBA is already in the library
    assert nd.asked[0] == "n3" and set(nd.asked) == {"n1", "n3"}              # n2 is a joined name: no single id
    assert rows[0]["because"] == ["Radiohead", "ABBA"] and note == ""


def test_find_suggestions_explains_missing_lastfm_and_survives_missing_plays():
    nd = FakeNavidrome({}, fail_plays=True)
    with pytest.raises(discover.DiscoverError) as e:
        discover.find_suggestions(nd, gap=0)
    assert "Last.fm" in str(e.value)
    rows, note = discover.find_suggestions(FakeNavidrome({"n1": similar_to("Roxette")}, fail_plays=True), gap=0)
    assert [r["name"] for r in rows] == ["Roxette"] and "Couldn't read your plays" in note
    stopped, _ = discover.find_suggestions(FakeNavidrome({"n1": similar_to("Roxette")}), gap=0, should_stop=lambda: True)
    assert stopped == []


def test_the_package_has_no_unusual_qobuz_calls():
    """The Qobuz endpoints qobuz-dl doesn't use, and the raw-call helper, must never come back."""
    root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "qobuz_gui")
    banned = ("getSimilarArtists", "getFeatured", "similarArtists", ".raw(", "def raw(", "album/getFeatured")
    for name in os.listdir(root):
        if name.endswith(".py"):
            text = open(os.path.join(root, name), encoding="utf-8").read()
            for word in banned:
                assert word not in text, f"{word} found in {name}"
