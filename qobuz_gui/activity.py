"""The bottom panel: job queue, live log, progress bar, stop button and answer box."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QFontDatabase, QTextCursor
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
    QProgressBar, QPushButton, QSplitter, QTextEdit, QVBoxLayout, QWidget,
)

STATUS_ICON = {"queued": "⏳", "running": "▶", "done": "✔", "failed": "✖", "stopped": "■"}


class ActivityPanel(QWidget):
    def __init__(self, runner, parent=None):
        super().__init__(parent)
        self.runner = runner
        self.items = {}

        # Queue
        self.queue = QListWidget()
        self.queue.setMinimumWidth(240)
        self.queue.setSelectionMode(QListWidget.SingleSelection)
        q_title = QLabel("<b>Queue</b>")
        self.btn_remove = QPushButton("Remove")
        self.btn_remove.setToolTip("Remove the selected waiting job")
        self.btn_clear = QPushButton("Clear finished")
        q_buttons = QHBoxLayout()
        q_buttons.addWidget(self.btn_remove)
        q_buttons.addWidget(self.btn_clear)
        q_box = QVBoxLayout()
        q_box.setContentsMargins(0, 0, 0, 0)
        q_box.addWidget(q_title)
        q_box.addWidget(self.queue, 1)
        q_box.addLayout(q_buttons)
        q_widget = QWidget()
        q_widget.setLayout(q_box)

        # Log
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.document().setMaximumBlockCount(6000)
        mono = QFontDatabase.systemFont(QFontDatabase.FixedFont)
        self.log.setFont(mono)
        self.log.setLineWrapMode(QTextEdit.NoWrap)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setTextVisible(True)
        self.progress.setFormat("Idle")
        self.progress.setValue(0)

        self.btn_stop = QPushButton("Stop")
        self.btn_stop.setToolTip("Stop the running job (partial files are cleaned up)")
        self.btn_stop_all = QPushButton("Stop all")
        self.btn_stop.setEnabled(False)
        self.btn_stop_all.setEnabled(False)

        self.answer = QLineEdit()
        self.answer.setPlaceholderText("Reply to a question from qobuz-dl, then press Enter")
        self.answer.setEnabled(False)
        self.btn_copy = QPushButton("Copy log")
        self.btn_clear_log = QPushButton("Clear log")

        top = QHBoxLayout()
        top.addWidget(self.progress, 1)
        top.addWidget(self.btn_stop)
        top.addWidget(self.btn_stop_all)
        bottom = QHBoxLayout()
        bottom.addWidget(self.answer, 1)
        bottom.addWidget(self.btn_copy)
        bottom.addWidget(self.btn_clear_log)

        l_box = QVBoxLayout()
        l_box.setContentsMargins(0, 0, 0, 0)
        l_box.addLayout(top)
        l_box.addWidget(self.log, 1)
        l_box.addLayout(bottom)
        l_widget = QWidget()
        l_widget.setLayout(l_box)

        split = QSplitter(Qt.Horizontal)
        split.addWidget(q_widget)
        split.addWidget(l_widget)
        split.setStretchFactor(1, 1)
        split.setSizes([260, 900])

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(split)

        runner.jobAdded.connect(self._job_added)
        runner.jobChanged.connect(self._job_changed)
        runner.line.connect(self._line)
        runner.progress.connect(self._progress)
        runner.progressIdle.connect(self._idle)
        runner.prompt.connect(self._prompt)

        self.btn_stop.clicked.connect(runner.stop)
        self.btn_stop_all.clicked.connect(self._stop_all)
        self.btn_remove.clicked.connect(self._remove_selected)
        self.btn_clear.clicked.connect(self._clear_finished)
        self.answer.returnPressed.connect(self._send_answer)
        self.btn_copy.clicked.connect(self._copy_log)
        self.btn_clear_log.clicked.connect(self.log.clear)

    # ---- queue ---------------------------------------------------------------
    def _job_added(self, job):
        item = QListWidgetItem()
        item.setData(Qt.UserRole, job.id)
        item.setToolTip("qobuz-dl " + " ".join(job.args))
        self.items[job.id] = (item, job)
        self.queue.addItem(item)
        self._render(job)

    def _job_changed(self, job):
        self._render(job)
        running = self.runner.busy()
        self.btn_stop.setEnabled(running)
        self.btn_stop_all.setEnabled(running or bool(self.runner.pending()))
        self.answer.setEnabled(running)

    def _render(self, job):
        entry = self.items.get(job.id)
        if entry:
            entry[0].setText(f"{STATUS_ICON.get(job.status, '')}  {job.title}")

    def _remove_selected(self):
        item = self.queue.currentItem()
        if not item:
            return
        _, job = self.items[item.data(Qt.UserRole)]
        if job.status == "queued":
            self.runner.remove(job)
        if job.status != "running":
            self.queue.takeItem(self.queue.row(item))
            self.items.pop(job.id, None)

    def _clear_finished(self):
        for job_id, (item, job) in list(self.items.items()):
            if job.status in ("done", "failed", "stopped"):
                self.queue.takeItem(self.queue.row(item))
                self.items.pop(job_id)
        self.runner.clear_finished()

    def _stop_all(self):
        if QMessageBox.question(self, "Stop all", "Stop the running job and cancel everything waiting in the queue?") \
                == QMessageBox.Yes:
            self.runner.stop_all()

    # ---- log -----------------------------------------------------------------
    def _line(self, html_text, _plain):
        bar = self.log.verticalScrollBar()
        at_bottom = bar.value() >= bar.maximum() - 4
        self.log.append(html_text or "&nbsp;")
        if at_bottom:
            self.log.moveCursor(QTextCursor.End)
            bar.setValue(bar.maximum())

    def _progress(self, percent, label):
        self.progress.setValue(percent)
        self.progress.setFormat(f"{label}  —  %p%" if label else "%p%")

    def _idle(self):
        self.progress.setValue(0)
        self.progress.setFormat("Idle")

    def _copy_log(self):
        from PySide6.QtWidgets import QApplication
        QApplication.clipboard().setText(self.log.toPlainText())

    # ---- prompts -------------------------------------------------------------
    def _send_answer(self):
        text = self.answer.text()
        self.answer.clear()
        self.runner.send(text)

    def _prompt(self, job, text):
        self.window().activateWindow()
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Question)
        box.setWindowTitle(job.title if job else "qobuz-dl")
        box.setText(text.replace("[y/N]", "").replace("[y/n]", "").strip().rstrip(":"))
        box.setInformativeText("Details are in the log below.")
        yes = box.addButton(QMessageBox.Yes)
        no = box.addButton(QMessageBox.No)
        box.setDefaultButton(no)
        box.exec()
        self.runner.send("y" if box.clickedButton() == yes else "n")
