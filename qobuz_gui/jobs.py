"""A queue of qobuz-dl runs, one subprocess at a time."""

import codecs
import html
import itertools
import os
import signal
import sys
from dataclasses import dataclass, field

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QTimer, Signal

from .logparse import OutputParser

PACKAGE_PARENT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_ids = itertools.count(1)


@dataclass
class Job:
    title: str
    args: list
    env: dict = field(default_factory=dict)
    # Some CLI commands report success through sys.exit("message"), which exits with 1.
    success_text: str = ""
    # Run this command instead of qobuz-dl (e.g. the pip update); first item is the program.
    program: list = field(default_factory=list)
    id: int = field(default_factory=lambda: next(_ids))
    status: str = "queued"  # queued, running, done, failed, stopped
    saw_success: bool = False


class JobRunner(QObject):
    jobAdded = Signal(object)
    jobChanged = Signal(object)
    line = Signal(str, str)            # html, plain
    progress = Signal(int, str)        # percent, label
    progressIdle = Signal()
    prompt = Signal(object, str)       # job, prompt text
    authProblem = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.jobs = []
        self.current = None
        self.proc = None
        self.parser = None
        self.decoder = None
        self._kill_timer = QTimer(self)
        self._kill_timer.setSingleShot(True)
        self._kill_timer.setInterval(5000)
        self._kill_timer.timeout.connect(self._force_kill)

    # ---- queue ---------------------------------------------------------------
    def enqueue(self, job):
        self.jobs.append(job)
        self.jobAdded.emit(job)
        self._start_next()
        return job

    def remove(self, job):
        if job.status == "queued":
            job.status = "stopped"
            self.jobChanged.emit(job)

    def clear_finished(self):
        self.jobs = [j for j in self.jobs if j.status in ("queued", "running")]

    def busy(self):
        return self.current is not None

    def unfinished(self):
        """Queued and running jobs, as plain dicts (for remembering the queue between launches)."""
        return [dict(title=j.title, args=j.args, env=j.env, success_text=j.success_text)
                for j in self.jobs if j.status in ("queued", "running") and not j.program]

    def pending(self):
        return [j for j in self.jobs if j.status == "queued"]

    def _start_next(self):
        if self.current is not None:
            return
        nxt = next((j for j in self.jobs if j.status == "queued"), None)
        if nxt is None:
            return
        self.current = nxt
        nxt.status = "running"
        self.jobChanged.emit(nxt)

        self.parser = OutputParser()
        self.decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self.proc = QProcess(self)
        self.proc.setProcessChannelMode(QProcess.MergedChannels)

        env = QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONUNBUFFERED", "1")
        env.insert("PYTHONIOENCODING", "utf-8")
        env.insert("COLUMNS", "100")
        path = env.value("PYTHONPATH", "")
        env.insert("PYTHONPATH", PACKAGE_PARENT + (os.pathsep + path if path else ""))
        for k, v in nxt.env.items():
            env.insert(k, str(v))
        self.proc.setProcessEnvironment(env)
        self.proc.setWorkingDirectory(os.path.expanduser("~"))

        self.proc.readyReadStandardOutput.connect(self._read)
        self.proc.finished.connect(self._finished)
        self.proc.errorOccurred.connect(self._error)

        self.line.emit(f'<b>▶ {html.escape(nxt.title)}</b>', f"▶ {nxt.title}")
        if nxt.program:
            self.proc.start(nxt.program[0], nxt.program[1:])
        else:
            self.proc.start(sys.executable, ["-u", "-m", "qobuz_gui.run", *nxt.args])

    # ---- process I/O ---------------------------------------------------------
    def _read(self):
        data = self.decoder.decode(bytes(self.proc.readAllStandardOutput()))
        self._handle(self.parser.feed(data))

    def _handle(self, events):
        for ev in events:
            if ev[0] == "line":
                _, html_text, text = ev
                if self.current and self.current.success_text and self.current.success_text in text:
                    self.current.saw_success = True
                if "401 Unauthorized" in text or "Auth Token missing" in text:
                    self.authProblem.emit(text.strip())
                self.line.emit(html_text, text)
            elif ev[0] == "progress":
                self.progress.emit(ev[1], ev[2])
            elif ev[0] == "prompt":
                self.prompt.emit(self.current, ev[1])

    def send(self, text):
        """Writes an answer to the running process (it is waiting on input())."""
        if self.proc and self.proc.state() == QProcess.Running:
            self._handle(self.parser.answered())
            self.line.emit(f'<i>&gt; {html.escape(text)}</i>', f"> {text}")
            self.proc.write((text + "\n").encode("utf-8"))

    def stop(self):
        if not self.proc or self.proc.state() != QProcess.Running:
            return
        self.current.status = "stopped"
        try:
            # SIGINT runs qobuz-dl's own handler, which removes partial files.
            os.kill(int(self.proc.processId()), signal.SIGINT)
        except (OSError, ValueError):
            self.proc.terminate()
        self._kill_timer.start()

    def stop_all(self):
        for j in self.jobs:
            if j.status == "queued":
                j.status = "stopped"
                self.jobChanged.emit(j)
        self.stop()

    def _force_kill(self):
        if self.proc and self.proc.state() == QProcess.Running:
            self.proc.kill()

    def _error(self, err):
        if err == QProcess.FailedToStart and self.current:
            self.line.emit('<span style="color:#e5534b">Could not start Python to run qobuz-dl.</span>',
                           "Could not start Python to run qobuz-dl.")

    def _finished(self, code, _status):
        self._kill_timer.stop()
        self._read()
        self._handle(self.parser.flush())
        job = self.current
        if job.status != "stopped":
            ok = code == 0 or (job.success_text and job.saw_success)
            job.status = "done" if ok else "failed"
        mark = {"done": "✔ Finished", "failed": f"✖ Failed (exit code {code})", "stopped": "■ Stopped"}[job.status]
        colour = {"done": "#3fa65a", "failed": "#e5534b", "stopped": "#c99a2e"}[job.status]
        self.line.emit(f'<b style="color:{colour}">{mark}: {html.escape(job.title)}</b>', f"{mark}: {job.title}")
        self.line.emit("", "")
        self.progressIdle.emit()
        self.proc.deleteLater()
        self.proc = None
        self.current = None
        self.jobChanged.emit(job)
        self._start_next()
