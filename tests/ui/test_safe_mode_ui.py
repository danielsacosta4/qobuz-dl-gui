from qobuz_gui import prefs, qobuz_guard
from qobuz_gui.studio_dialog import StudioCompare

from .conftest import album, settle, wait_for


def test_selecting_an_album_asks_qobuz_nothing_until_you_press_load(make_window):
    win, q = make_window()
    win.tabs.setCurrentWidget(win.search)
    win.search.show([album("a1", "Arrival", 1976, artist="ABBA", artist_id=1)], "albums", "Albums for “abba”")
    win.search.table.selectRow(0)
    assert wait_for(lambda: win.search.btn_detail_load.isVisible(), 5)
    assert q.calls == [] and "Safe mode" in win.search.details.toPlainText()
    win.search.btn_detail_load.click()
    assert wait_for(lambda: "Track One" in win.search.details.toPlainText(), 5)
    assert q.calls == [("album", "a1", True)] and not win.search.btn_detail_load.isVisible()


def test_link_lookups_are_capped_per_click(make_window):
    win, q = make_window()
    win.health._queue_missing([("Band", {"title": f"Album {i}", "url": ""}) for i in range(8)])
    assert wait_for(lambda: len(win.queued) == 5, 10)
    settle(0.3)
    assert len([c for c in q.calls if c[0] == "find_album"]) == 5 and len(win.queued) == 5


def test_optional_qobuz_features_are_off(make_window):
    win, _q = make_window()
    assert not win.health.btn_qobuz_check.isEnabled()
    dlg = StudioCompare(win, "Band", [], "qobuz", qobuz_loaded=False, can_load=False)
    assert dlg.btn_load.isHidden()


def test_downloads_run_one_at_a_time_with_a_delay(make_window):
    win, _q = make_window()
    win.download.workers.setValue(8)
    win.download.delay.setValue(0)
    args = win.download.build_args()
    assert args[args.index("--max-workers") + 1] == "1" and args[args.index("--delay") + 1] == "2"
    win.download.delay.setValue(10)                                    # a longer delay of yours is kept
    args = win.download.build_args()
    assert args[args.index("--delay") + 1] == "10"


def test_switching_safe_mode_off_restores_the_settings(make_window):
    win, q = make_window()
    win.download.workers.setValue(8)
    prefs.set_value(prefs.SAFE_MODE, False)
    win._apply_safe_mode()
    settle(0.1)
    args = win.download.build_args()
    assert args[args.index("--max-workers") + 1] == "8" and "--delay" not in args
    assert qobuz_guard.guard.gap == qobuz_guard.GAP
    win.tabs.setCurrentWidget(win.search)
    win.search.show([album("a1", "Arrival", 1976)], "albums", "Albums")
    win.search.table.selectRow(0)
    assert wait_for(lambda: ("album", "a1", True) in q.calls, 5)       # details load on selection again
