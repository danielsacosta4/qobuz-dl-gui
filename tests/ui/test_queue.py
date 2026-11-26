import sys

from qobuz_gui import prefs
from qobuz_gui.jobs import Job

from .conftest import settle, wait_for


def titles(win):
    q = win.activity.queue
    return [q.item(i).text().split("  ", 1)[1].split("   ·")[0] for i in range(q.count())]


def quick(title):
    """A job that runs a do-nothing command instead of qobuz-dl."""
    return Job(title, [], program=[sys.executable, "-c", "pass"])


def download(title):
    return Job(title, ["dl", f"https://play.qobuz.com/album/{title}"])


def test_pause_holds_the_queue_and_resume_runs_it(make_window):
    win, _q = make_window()
    panel, runner = win.activity, win.runner
    panel.btn_pause.setChecked(True)
    assert runner.paused and panel.btn_pause.text() == "Resume the queue"
    for name in ("one", "two"):
        runner.enqueue(quick(name))
    settle(0.3)
    assert not runner.busy() and len(runner.pending()) == 2          # nothing started
    assert "Paused" in panel.progress.format() and "2 jobs waiting" in panel.progress.format()
    panel.btn_pause.setChecked(False)
    assert wait_for(lambda: not runner.busy() and not runner.pending(), 15)
    assert [j.status for j in runner.jobs] == ["done", "done"] and panel.btn_pause.text() == "Pause after this job"


def test_waiting_jobs_can_be_reordered_and_the_saved_queue_follows(make_window):
    win, _q = make_window()
    panel, runner = win.activity, win.runner
    runner.set_paused(True)
    jobs = [runner.enqueue(download(n)) for n in ("a", "b", "c")]
    assert titles(win) == ["a", "b", "c"]

    panel.queue.setCurrentRow(2)
    assert panel.btn_up.isEnabled() and not panel.btn_down.isEnabled()
    panel.btn_up.click()
    assert titles(win) == ["a", "c", "b"] and [j.title for j in runner.pending()] == ["a", "c", "b"]
    assert panel.queue.currentItem().text().endswith("c")            # the selection follows the job
    panel.btn_up.click()
    assert titles(win) == ["c", "a", "b"] and not panel.btn_up.isEnabled()
    assert [j["title"] for j in prefs.get_json(prefs.SAVED_QUEUE)] == ["c", "a", "b"]   # survives a restart
    assert runner.move(jobs[2], -1) is False and runner.move(jobs[1], 1) is False        # already at the ends


def test_retry_queues_a_failed_download_again(make_window):
    win, _q = make_window()
    panel, runner = win.activity, win.runner
    runner.set_paused(True)
    failed = runner.enqueue(download("broken"))
    failed.status = "failed"
    runner.jobChanged.emit(failed)
    panel.queue.setCurrentRow(0)
    assert panel.btn_retry.isEnabled() and not panel.btn_up.isEnabled()
    panel.btn_retry.click()
    assert [j.status for j in runner.jobs] == ["failed", "queued"]
    again = runner.jobs[1]
    assert again is not failed and again.title == "broken" and again.args == failed.args
    assert panel.queue.currentItem().text().endswith("broken") and panel.queue.currentRow() == 1

    assert runner.retry(again) is None                                # only failed or stopped jobs
    update = runner.enqueue(quick("an update"))
    update.status = "failed"
    assert runner.retry(update) is None                               # not downloads: never retried from here
