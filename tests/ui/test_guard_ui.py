import pytest
from PySide6.QtWidgets import QMessageBox

from qobuz_gui import qobuz_guard
from qobuz_gui.client import Session
from qobuz_gui.download_tab import DownloadTab
from qobuz_gui.qobuz_guard import QobuzBlocked

from .conftest import settle


def test_a_block_shows_the_banner_and_stops_requests_until_you_resume(make_window, env):
    win, _q = make_window()
    guard = qobuz_guard.guard
    guard.trip("Qobuz says this account is blocked (USER_BLOCKED).")
    win._refresh_qobuz_banner()
    assert "Qobuz paused" in win.qobuz_label.text() and not win.btn_resume.isHidden()

    with pytest.raises(QobuzBlocked):                                   # the real session refuses before any request
        Session().search("albums", "abba", 5)

    asked = []
    env.monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: (asked.append(a[2]), QMessageBox.No)[1]))
    win.download.enqueue = DownloadTab.enqueue.__get__(win.download)    # the real one
    assert win.download.enqueue(["https://play.qobuz.com/album/1"]) == []
    assert asked and "Contact Qobuz support" in asked[0]

    win._resume_qobuz()                                                 # you answer No: still blocked
    assert guard.blocked_message()
    env.monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))
    win._resume_qobuz()
    assert guard.blocked_message() == "" and win.btn_resume.isHidden()


def test_a_download_log_that_says_blocked_stops_the_queue(make_window):
    win, _q = make_window()
    seen = []
    win.runner.blocked.connect(seen.append)
    win.runner._handle([("line", "x", '{"message":"Account is blocked","error_code":"USER_BLOCKED"}')])
    settle(0.1)
    assert qobuz_guard.guard.blocked_message() and qobuz_guard.guard.permanent()
    assert seen and win.runner.pending() == [] and "Qobuz paused" in win.qobuz_label.text()
