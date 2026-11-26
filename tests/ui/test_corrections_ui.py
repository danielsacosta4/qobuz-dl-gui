from PySide6.QtWidgets import QFileDialog, QInputDialog, QMessageBox

from qobuz_gui import doctor, prefs, studio, studio_dialog, wikipedia

from .conftest import album, settle, wait_for

WIKI = {"albums": [{"title": "First", "year": 1990}, {"title": "Second", "year": 1993}, {"title": "Third", "year": 1997},
                   {"title": "Lost Album", "year": 2005}], "page": "Band discography", "lang": "en"}
LIBRARY = [{"name": "First", "artist": "Band"}, {"name": "Third", "artist": "Band"}]


def health_with_band(make_window, env):
    env.monkeypatch.setattr(wikipedia, "studio_albums", lambda artist, lang="en", refresh=False: WIKI)
    win, q = make_window()
    comp = doctor.Scanner(None, None)._completion_wikipedia("Band", LIBRARY, ["Band"])
    assert (comp.owned, comp.total) == (2, 4)
    h = win.health
    h.completion = [comp]
    h.hide_complete.setChecked(False)
    h._fill_artists()
    h.art_table.selectRow(0)
    settle(0.1)
    return win, q, h, comp


def test_corrections_change_the_counts_everywhere_and_survive_a_restart(make_window, env):
    win, q, h, comp = health_with_band(make_window, env)
    steps = [{"op": "remove", "title": "Lost Album"}, {"op": "add", "title": "Fifth", "year": 2010}, None]
    seen = []

    def fake_exec(dialog):
        seen.append({"titles": [r["title"] for r in dialog.rows], "removed": list(dialog.removed),
                     "yours": [r["title"] for r in dialog.rows if r.get("added")],
                     "undo": dialog.btn_undo.isEnabled()})
        action = steps[len(seen) - 1]
        if action:
            dialog.overrideRequested.emit(action)            # what pressing the button does
    env.monkeypatch.setattr(studio_dialog.StudioCompare, "exec", fake_exec)
    h._compare_lists()
    assert wait_for(lambda: len(seen) == 3, 10), seen        # the window reopened after each correction

    assert seen[0] == {"titles": ["First", "Second", "Third", "Lost Album"], "removed": [], "yours": [], "undo": False}
    assert seen[1]["titles"] == ["First", "Second", "Third"] and seen[1]["removed"] == ["Lost Album"] and seen[1]["undo"]
    assert seen[2]["titles"] == ["First", "Second", "Third", "Fifth"] and seen[2]["yours"] == ["Fifth"]
    assert (comp.owned, comp.total) == (2, 4) and [m["title"] for m in comp.missing] == ["Second", "Fifth"]
    assert h.art_table.item(0, 1).text() == "2 of 4"
    assert q.calls == []                                      # correcting a list never asks Qobuz

    saved = doctor.load_results()[2][0]                       # the results on disk
    assert [m["title"] for m in saved.missing] == ["Second", "Fifth"]
    again = studio.Choice.load()                              # a fresh start reads the same corrections
    assert [a["title"] for a in again.albums_for("Band")["albums"]] == ["First", "Second", "Third", "Fifth"]
    rescanned = doctor.Scanner(None, None, studio=again)._completion_wikipedia("Band", LIBRARY, ["Band"])
    assert (rescanned.owned, rescanned.total) == (2, 4)       # and a new scan counts from the corrected list

    studio.edit_override("Band", {"op": "reset"})             # Undo my changes
    assert len(studio.Choice.load().albums_for("Band")["albums"]) == 4 and not prefs.get_json(prefs.STUDIO_OVERRIDES)


def test_the_buttons_in_the_compare_window(make_window, env):
    win, _q = make_window()
    rows = studio.compare([album("9", "Qobuz Only", 2001)], WIKI["albums"], [], set())
    dlg = studio_dialog.StudioCompare(win, "Band", rows, studio.WIKI, "Band discography",
                                      override={"remove": ["Gone"]}, removed=["Gone"])
    asked = []
    dlg.overrideRequested.connect(asked.append)
    assert dlg.btn_undo.isEnabled() and dlg.btn_restore.isEnabled() and not dlg.btn_remove.isEnabled()

    by_title = {dlg.table.item(r, 1).text(): r for r in range(dlg.table.rowCount())}
    dlg.table.selectRow(by_title["Qobuz Only"])
    assert not dlg.btn_remove.isEnabled()                     # it isn't on Wikipedia's list: nothing to remove
    dlg.table.selectRow(by_title["Second"])
    assert dlg.btn_remove.isEnabled()
    dlg.btn_remove.click()

    env.monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("New One (2011)", True)))
    dlg.btn_add.click()
    env.monkeypatch.setattr(QInputDialog, "getItem", staticmethod(lambda *a, **k: ("Gone", True)))
    dlg.btn_restore.click()
    env.monkeypatch.setattr(QInputDialog, "getText", staticmethod(
        lambda *a, **k: ("https://pt.wikipedia.org/wiki/Banda_(grupo)", True)))
    dlg.btn_other_page.click()
    dlg.btn_undo.click()
    assert asked == [{"op": "remove", "title": "Second"}, {"op": "add", "title": "New One", "year": 2011},
                     {"op": "restore", "title": "Gone"}, {"op": "page", "page": "Banda (grupo)", "lang": "pt"},
                     {"op": "reset"}]
    env.monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("ignored", False)))
    dlg.btn_add.click()                                       # Cancel in the box: nothing happens
    assert len(asked) == 5


def test_a_page_that_does_not_work_can_be_undone(make_window, env):
    win, _q, h, _comp = health_with_band(make_window, env)
    studio.edit_override("Band", {"op": "page", "page": "Wrong page", "lang": "en"})
    env.monkeypatch.setattr(wikipedia, "studio_albums_from_page", lambda title, lang="en", refresh=False: None)
    questions = []
    env.monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: (questions.append(a[2]), QMessageBox.Yes)[1]))
    opened = []
    env.monkeypatch.setattr(studio_dialog.StudioCompare, "exec", lambda dialog: opened.append(len(dialog.rows)))
    h._compare_lists()
    assert wait_for(lambda: opened, 10)
    assert questions and "Undo your corrections" in questions[0]
    assert opened == [4] and not prefs.get_json(prefs.STUDIO_OVERRIDES)     # back to Wikipedia's own list


def test_export_missing_albums(make_window, env, tmp_path):
    win, _q, h, _comp = health_with_band(make_window, env)
    target = tmp_path / "missing.csv"
    env.monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(target), "")))
    h._export_missing()
    assert target.read_text(encoding="utf-8").splitlines() == [
        "Artist,Album,Year,List,Qobuz link", "Band,Second,1993,Wikipedia,", "Band,Lost Album,2005,Wikipedia,"]
    assert "Saved 2 missing albums from 1 artist" in win.status_label.text()

    text = tmp_path / "missing.txt"
    env.monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(text), "")))
    h._export_missing()
    assert text.read_text(encoding="utf-8") == "Band\n  - Second (1993)\n  - Lost Album (2005)\n"
    env.monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: ("", "")))
    h._export_missing()                                       # Cancel: nothing written, no error
