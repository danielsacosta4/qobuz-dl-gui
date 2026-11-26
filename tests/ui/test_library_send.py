import os

from qobuz_gui import history, safeio

from .conftest import wait_for


def stage(env, rel, tracks=2):
    folder = env.staging / rel
    folder.mkdir(parents=True)
    for n in range(1, tracks + 1):
        (folder / f"0{n}. Track.flac").write_bytes(os.urandom(2048))
    return folder


def test_auto_send_moves_a_new_album_and_leaves_one_that_needs_a_decision(make_window, env):
    stage(env, "New Band/Fresh Album")
    waiting = stage(env, "ABBA/Arrival")
    (env.lib / "A" / "ABBA" / "Arrival [FLAC 16]").mkdir(parents=True)       # another copy is already there
    safeio.mark_library(str(env.lib))
    win, q = make_window()
    assert wait_for(lambda: len(win.library.albums) == 2), win.library.albums

    done = []
    win.library.autoSendDone.connect(lambda *a: done.append(a))
    win.library.auto_send()
    assert wait_for(lambda: done, 20), "auto-send never finished"
    assert done[0] == (1, 0, 1)                                               # sent, failed, left for you
    sent = env.lib / "N" / "New Band" / "Fresh Album"
    assert sorted(p.name for p in sent.iterdir()) == ["01. Track.flac", "02. Track.flac"]
    assert waiting.is_dir() and not (env.lib / "A" / "ABBA" / "Arrival").exists()
    assert "automatically" in win.status_label.text()
    assert history.journal() is not None and q.calls == []                   # sending never asks Qobuz


def test_auto_send_refuses_an_unconfirmed_library(make_window, env):
    stage(env, "New Band/Fresh Album")
    win, _q = make_window()                                                   # no library marker
    wait_for(lambda: len(win.library.albums) == 1)
    win.library.auto_send()
    assert not win.library.busy() and "Auto-send skipped" in win.status_label.text()
    assert not (env.lib / "N").exists()
