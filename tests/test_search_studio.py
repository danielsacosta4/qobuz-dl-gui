from qobuz_gui import studio


def album(id_, artist):
    return {"id": id_, "title": f"T{id_}", "artist": {"name": artist}}


def test_main_artist_of_a_one_artist_search_comes_first():
    items = [album(1, "Radiohead"), album(2, "Radiohead"), album(3, "Radiohead"), album(4, "Thom Yorke"),
             album(5, "Radiohead & Friends"), album(6, "")]
    got = studio.main_artists(items)
    assert got[0] == "Radiohead" and set(got) == {"Radiohead", "Thom Yorke", "Radiohead & Friends"}
    assert studio.main_artists(items, 1) == ["Radiohead"]


def test_a_mixed_search_is_capped():
    items = [album(i, f"Artist {i}") for i in range(30)] + [album(100, "Artist 7"), album(101, "Artist 7")]
    got = studio.main_artists(items)
    assert len(got) == 5 and got[0] == "Artist 7"
    assert studio.main_artists([]) == []
    assert studio.main_artists([album(1, "")]) == []
