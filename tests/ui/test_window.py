from PySide6.QtGui import QShortcut

from qobuz_gui import prefs, qobuz_guard

from .conftest import settle


def tab_names(win):
    return [win.tabs.tabText(i) for i in range(win.tabs.count()) if win.tabs.isTabVisible(i)]


def test_window_opens_in_safe_mode_by_default(make_window):
    win, q = make_window()
    assert prefs.safe_mode() is True
    assert tab_names(win) == ["Search", "Download", "Library", "Library health", "Playlist sync", "Library tools",
                              "Settings"]                                   # Discover is hidden
    assert "Safe mode" in win.qobuz_label.text()
    assert qobuz_guard.guard.budget == 0
    assert q.calls == []                                                    # opening the app asks Qobuz nothing


def test_safe_mode_off_shows_discover_and_back_again(make_window):
    win, _q = make_window(safe=False)
    assert "Discover" in tab_names(win) and "Safe mode" not in win.qobuz_label.text()
    prefs.set_value(prefs.SAFE_MODE, True)
    win._apply_safe_mode()
    settle(0.1)
    assert "Discover" not in tab_names(win)


def test_keyboard_shortcuts_are_registered(make_window):
    win, _q = make_window()
    keys = {s.key().toString() for s in win.findChildren(QShortcut)}
    assert {"Ctrl+F", "Ctrl+D", "Del", "F5", "Ctrl+Q", "Ctrl+V", "Esc", "Ctrl+Return", "Ctrl+1", "Ctrl+8"} <= keys


def test_pasting_links_queues_them(make_window, qapp):
    win, _q = make_window()
    qapp.clipboard().setText("try https://play.qobuz.com/album/zz9 and https://open.qobuz.com/track/7")
    win.paste_links()
    assert win.queued == [(["https://play.qobuz.com/album/zz9", "https://open.qobuz.com/track/7"], None)]
