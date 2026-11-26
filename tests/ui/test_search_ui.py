"""The Search tab's everyday behaviour: results, filters, hiding, the details panel, artist pages."""
from qobuz_gui import library, prefs

from .conftest import album, settle, wait_for

BAND = [album("1", "First", 1990, hires=True), album("2", "Second", 1993), album("3", "Third EP", 1997, "ep"),
        album("5", "Live in Tokyo", 1999, "live"), album("6", "Second", 1993, explicit=True)]


def shown(win):
    t = win.search.table
    return [t.item(r, 1).text() for r in range(t.rowCount())]


def search_window(make_window, safe=False):
    win, q = make_window(safe=safe)
    win.index = library.LibraryIndex.from_navidrome([{"name": "First", "artist": "Band"}], None)
    win.tabs.setCurrentWidget(win.search)
    return win, q


def test_results_columns_and_have_mark(make_window):
    win, _q = search_window(make_window)
    win.search.show(BAND, "albums", "Albums for “band”")
    t = win.search.table
    rows = {t.item(r, 1).text() + "|" + t.item(r, 6).text(): r for r in range(t.rowCount())}
    assert "Second|Clean" not in rows and "Second|E" in rows        # Prefer explicit hides the clean twin
    first = rows["First|Clean"]
    assert t.item(first, 9).text() == "✔" and t.item(first, 8).text().startswith("Hi-Res")
    assert t.item(first, 7).text() == "1990" and t.item(first, 2).text() == "Band"
    assert win.search.count_label.text().startswith("4 shown") and "1 hidden by filters" in win.search.count_label.text()

    win.search.prefer_explicit.setChecked(False)
    assert shown(win).count("Second") == 2


def test_type_filters(make_window):
    win, _q = search_window(make_window)
    win.search.show(BAND, "albums", "Albums")
    assert "Live in Tokyo" in shown(win)
    win.search.type_boxes["Live album"].setChecked(False)
    assert "Live in Tokyo" not in shown(win) and "First" in shown(win)
    win.search.type_boxes["Live album"].setChecked(True)
    assert "Live in Tokyo" in shown(win)


def test_delete_hides_and_brings_back(make_window):
    win, _q = search_window(make_window)
    s = win.search
    s.show(BAND, "albums", "Albums")
    row = shown(win).index("Live in Tokyo")
    s.table.selectRow(row)
    s.hide_selected()
    assert "Live in Tokyo" not in shown(win) and prefs.get_json(prefs.HIDDEN_RELEASES) == ["5"]
    s.show_hidden.setChecked(True)
    assert "Live in Tokyo" in shown(win)
    s.table.selectRow(shown(win).index("Live in Tokyo"))
    s.hide_selected()                                                # all selected are hidden: Delete shows them again
    assert not prefs.get_json(prefs.HIDDEN_RELEASES)


def test_details_panel_and_selection_summary(make_window):
    win, q = search_window(make_window)                             # Safe mode off: details load on selection
    s = win.search
    s.show(BAND, "albums", "Albums")
    s.table.selectRow(shown(win).index("First"))
    assert wait_for(lambda: "Track One" in s.details.toPlainText(), 5)
    assert ("album", "1", True) in q.calls and s.btn_detail_dl.isEnabled()
    n = len(q.calls)
    s.table.selectRow(shown(win).index("Live in Tokyo"))
    assert wait_for(lambda: ("album", "5", True) in q.calls, 5)
    s.table.selectRow(shown(win).index("First"))                    # seen before: no second request
    settle(0.8)
    assert len([c for c in q.calls if c == ("album", "1", True)]) == 1 and len(q.calls) == n + 1

    s.table.selectAll()
    assert wait_for(lambda: "4 selected" in s.details.toPlainText(), 5)
    text = s.details.toPlainText()
    assert "1 in Hi-Res" in text and "1 already in your library" in text and not s.btn_detail_dl.isEnabled()

    s._download_detail_album()                                       # several selected: nothing happens
    assert win.queued == []
    s.table.selectRow(shown(win).index("First"))
    settle(0.7)
    s._download_detail_album()
    assert win.queued == [(["https://play.qobuz.com/album/1"], "Band — First")]


def test_download_copy_and_double_click(make_window, qapp):
    win, _q = search_window(make_window)
    s = win.search
    s.show(BAND, "albums", "Albums")
    s.table.selectAll()
    s.copy_selected()
    assert qapp.clipboard().text().count("https://play.qobuz.com/album/") == 4
    s.download_selected()
    assert len(win.queued) == 4 and win.queued[0][0][0].startswith("https://play.qobuz.com/album/")
    win.queued.clear()
    s.table.clearSelection()
    s.download_selected()
    assert win.queued == [] and "Select one or more rows" in win.status_label.text()


def test_artist_page_counts_studio_albums_and_goes_back(make_window):
    win, q = search_window(make_window)
    s = win.search
    s.show([{"id": 7, "name": "Band", "albums_count": 5}], "artists", "Artists for “band”")
    assert not s.studio_box.isVisible() and not s.btn_discog.isVisible()
    q.albums_by_artist[7] = BAND
    s._double_clicked(0, 0)                                          # double-click an artist: browse their releases
    assert wait_for(lambda: s.btn_discog.isVisible(), 5)
    assert ("artist_albums", 7, True) in q.calls
    assert "Releases by Band" in s.heading.text() and "1 of 2" in s.heading.text()   # First owned, Second not
    assert s.btn_back.isEnabled() and s.studio_box.isVisible()
    s.back()
    assert shown(win) == ["Band"] and not s.btn_discog.isVisible()
