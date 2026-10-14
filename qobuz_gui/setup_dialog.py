"""First-run setup: creates config.ini through qobuz-dl's own wizard."""

import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit, QMessageBox, QVBoxLayout,
)

from . import config
from .client import run_async
from .download_tab import FolderPicker, quality_combo, set_quality
from .settings_tab import PasswordField


class SetupDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Set up qobuz-dl")
        self.setMinimumWidth(560)

        intro = QLabel(
            "<b>Welcome.</b> qobuz-dl needs your Qobuz auth token (an active subscription is required). "
            "Log in at play.qobuz.com, press F12 → Application (Chrome) or Storage (Firefox) → "
            "Local Storage → https://play.qobuz.com → <i>localuser</i>, and copy the <i>token</i> value.")
        intro.setWordWrap(True)
        intro.setTextFormat(Qt.RichText)

        self.email = QLineEdit()
        self.token = PasswordField("Paste the token here")
        self.folder = FolderPicker()
        self.folder.setText(os.path.join(os.path.expanduser("~"), "Music", "Qobuz"))
        self.quality = quality_combo()
        set_quality(self.quality, 27)
        self.lyrics = QCheckBox("Download lyrics")
        self.lyrics.setChecked(True)
        self.genius = PasswordField("Optional")
        self.no_keyring = QCheckBox("Store tokens in config.ini instead of the system keyring")
        self.no_keyring.setToolTip("Tick this if KWallet / the Secret Service isn't running")

        form = QFormLayout()
        form.addRow("Qobuz email", self.email)
        form.addRow("Auth token", self.token)
        form.addRow("Download folder", self.folder)
        form.addRow("Quality", self.quality)
        form.addRow("", self.lyrics)
        form.addRow("Genius token", self.genius)
        form.addRow("", self.no_keyring)

        self.state = QLabel("")
        self.state.setWordWrap(True)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Ok).setText("Save")
        self.buttons.accepted.connect(self._save)
        self.buttons.rejected.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.addWidget(intro)
        lay.addLayout(form)
        lay.addWidget(self.state)
        lay.addWidget(self.buttons)

    def _save(self):
        token = self.token.text()
        if len(token) < 20:
            QMessageBox.information(self, "Auth token", "Paste your Qobuz auth token first.")
            return
        folder = os.path.expanduser(self.folder.text() or "~/Music/Qobuz")
        os.makedirs(folder, exist_ok=True)
        args = dict(
            email=self.email.text().strip(), token=token, directory=folder,
            quality=self.quality.currentData(), disable_keyring=self.no_keyring.isChecked(),
            fetch_lyrics=self.lyrics.isChecked(), genius_token=self.genius.text(),
        )
        self.buttons.setEnabled(False)
        self.state.setText("Saving and fetching the app ID from play.qobuz.com…")

        def ok(_out):
            cfg = config.load()
            # Make sure the token ended up somewhere the CLI can read it
            if not config.load_secret(cfg, "auth_token"):
                config.set_value(cfg, "disable_keyring", True)
                config.set_value(cfg, "auth_token", token)
                config.save(cfg)
            self.accept()

        def fail(msg):
            self.buttons.setEnabled(True)
            self.state.setText(f"<span style='color:#e5534b'>Setup failed: {msg}</span>")

        run_async(lambda: config.create_with_wizard(**args), ok, fail)
