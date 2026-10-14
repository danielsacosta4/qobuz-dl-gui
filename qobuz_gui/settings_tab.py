"""Settings tab: edits qobuz-dl's config.ini (shared with the command line)."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFormLayout, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QPushButton, QScrollArea, QSpinBox, QVBoxLayout, QWidget,
)

from . import config, navidrome, prefs
from .client import run_async
from .download_tab import FolderPicker, quality_combo, set_quality

# (config key, label, default) — boolean options
LIBRARY_OPTIONS = [
    ("fetch_lyrics", "Download lyrics", True),
    ("embed_lyrics", "Embed lyrics in the audio files", True),
    ("no_lrc_files", "Don't save separate .lrc files", True),
    ("embed_art", "Embed cover art in the audio files", True),
    ("no_cover", "Don't save cover.jpg", False),
    ("no_credits", "Don't create the booklet .txt (credits & review)", False),
    ("no_m3u", "Don't create .m3u files for playlists", False),
    ("albums_only", "Skip singles, EPs and VA releases", False),
    ("smart_discography", "Smart discography filter", False),
    ("no_fallback", "Skip releases not available in the chosen quality", False),
    ("no_database", "Don't keep a download history", False),
    ("multiple_disc_one_dir", "Put all discs of a release in one folder", False),
    ("multi_value_tags", "Multi-value tags (split artists/genres)", False),
    ("legacy_charmap", "Plain ASCII replacements in file names", False),
    ("playlist_as_albums", "Save playlist tracks in album folders", False),
]

TAG_OPTIONS = [
    ("no_album_artist_tag", "Album artist"), ("no_album_title_tag", "Album title"),
    ("no_track_artist_tag", "Track artist"), ("no_track_title_tag", "Track title"),
    ("no_release_date_tag", "Release date"), ("no_media_type_tag", "Media type"),
    ("no_genre_tag", "Genre"), ("no_track_number_tag", "Track number"),
    ("no_track_total_tag", "Track total"), ("no_disc_number_tag", "Disc number"),
    ("no_disc_total_tag", "Disc total"), ("no_composer_tag", "Composer"),
    ("no_conductor_tag", "Conductor"), ("no_ensemble_tag", "Ensemble"),
    ("no_work_tag", "Work (classical)"), ("no_explicit_tag", "Explicit flag"),
    ("no_copyright_tag", "Copyright"), ("no_label_tag", "Label"),
    ("no_upc_tag", "UPC / barcode"), ("no_isrc_tag", "ISRC"),
    ("no_replaygain_tag", "ReplayGain"), ("no_album_url_tag", "Qobuz album URL"),
]

FORMAT_FIELDS = [
    ("folder_format", "Folder", "{album_artist} - {album_title} ({year}) [{format} {bit_depth}]"),
    ("track_format", "Track", "{track_number} - {track_title_base}"),
    ("multiple_disc_track_format", "Track (multi-disc)", "{disc_number}.{track_number} - {track_title_base}"),
    ("fallback_folder_format", "Fallback folder", "{artist} - {album}"),
]

VARIABLES_HELP = (
    "Variables: {album_artist} {artist} {album_title} {album_title_base} {version_tag} {year} "
    "{release_date} {release_type} {album_genre} {label} {format} {bit_depth} {sampling_rate} "
    "{track_number} {disc_number} {track_title} {track_title_base} {track_composer} {isrc} "
    "{upc} {explicit}. Use / in the folder format to make subfolders."
)


class PasswordField(QWidget):
    def __init__(self, placeholder="", parent=None):
        super().__init__(parent)
        self.edit = QLineEdit()
        self.edit.setEchoMode(QLineEdit.Password)
        self.edit.setPlaceholderText(placeholder)
        self.show_btn = QPushButton("Show")
        self.show_btn.setCheckable(True)
        self.show_btn.toggled.connect(
            lambda on: (self.edit.setEchoMode(QLineEdit.Normal if on else QLineEdit.Password),
                        self.show_btn.setText("Hide" if on else "Show")))
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.edit, 1)
        lay.addWidget(self.show_btn)

    def text(self):
        return self.edit.text().strip()

    def setText(self, value):
        self.edit.setText(value)


class SettingsTab(QWidget):
    saved = Signal()

    def __init__(self, session, status, parent=None):
        super().__init__(parent)
        self.session = session
        self.status = status
        self._loaded_token = ""
        self._loaded_genius = ""

        # Account
        self.email = QLineEdit()
        self.token = PasswordField("Auth token from play.qobuz.com")
        self.genius = PasswordField("Optional — LRCLIB is used without it")
        self.no_keyring = QCheckBox("Store tokens in config.ini instead of the system keyring (KWallet)")
        self.btn_test = QPushButton("Test login")
        self.btn_appid = QPushButton("Refresh app ID")
        self.btn_appid.setToolTip("Fetch the current app ID and secrets from the Qobuz web player")
        self.login_state = QLabel("")
        self.login_state.setWordWrap(True)
        token_help = QLabel(
            "To get the token: log in at play.qobuz.com, press F12 → Application (Chrome) or Storage "
            "(Firefox) → Local Storage → https://play.qobuz.com → <i>localuser</i> → copy the "
            "<i>token</i> value.")
        token_help.setWordWrap(True)
        token_help.setTextFormat(Qt.RichText)
        acc_buttons = QHBoxLayout()
        acc_buttons.addWidget(self.btn_test)
        acc_buttons.addWidget(self.btn_appid)
        acc_buttons.addWidget(self.login_state, 1)
        acc = QFormLayout()
        acc.addRow("Email", self.email)
        acc.addRow("Auth token", self.token)
        acc.addRow("", token_help)
        acc.addRow("Genius token", self.genius)
        acc.addRow("", self.no_keyring)
        acc.addRow("", acc_buttons)
        acc_box = QGroupBox("Qobuz account")
        acc_box.setLayout(acc)

        # Downloads
        self.directory = FolderPicker("e.g. ~/Music/Qobuz")
        self.quality = quality_combo()
        self.workers = QSpinBox()
        self.workers.setRange(1, 16)
        self.blacklist = FolderPicker("blacklist.txt", file_mode=True, file_filter="Text files (*.txt);;All files (*)")
        self.embedded_size = QComboBox()
        self.saved_size = QComboBox()
        for c in (self.embedded_size, self.saved_size):
            c.addItems(config.ART_SIZES)
        self.disc_prefix = QLineEdit()
        dl = QFormLayout()
        dl.addRow("Download folder", self.directory)
        dl.addRow("Default quality", self.quality)
        dl.addRow("Parallel downloads", self.workers)
        dl.addRow("Blacklist file", self.blacklist)
        dl.addRow("Embedded cover size", self.embedded_size)
        dl.addRow("Saved cover size", self.saved_size)
        dl.addRow("Multi-disc folder prefix", self.disc_prefix)
        dl_box = QGroupBox("Downloads")
        dl_box.setLayout(dl)

        # Naming
        self.formats = {}
        self.previews = {}
        names = QFormLayout()
        for key, label, _ in FORMAT_FIELDS:
            edit = QLineEdit()
            prev = QLabel()
            prev.setEnabled(False)
            prev.setTextInteractionFlags(Qt.TextSelectableByMouse)
            edit.textChanged.connect(lambda text, p=prev: p.setText("→ " + config.preview_format(text)))
            self.formats[key] = edit
            self.previews[key] = prev
            col = QVBoxLayout()
            col.setSpacing(2)
            col.addWidget(edit)
            col.addWidget(prev)
            names.addRow(label, col)
        vars_help = QLabel(VARIABLES_HELP)
        vars_help.setWordWrap(True)
        vars_help.setEnabled(False)
        names.addRow("", vars_help)
        names_box = QGroupBox("File and folder names")
        names_box.setLayout(names)

        # Library options
        self.bools = {}
        grid = QGridLayout()
        for i, (key, label, _) in enumerate(LIBRARY_OPTIONS):
            cb = QCheckBox(label)
            self.bools[key] = cb
            grid.addWidget(cb, i // 2, i % 2)
        lib_box = QGroupBox("Library")
        lib_box.setLayout(grid)

        # Tags (checked = written)
        self.tags = {}
        tgrid = QGridLayout()
        for i, (key, label) in enumerate(TAG_OPTIONS):
            cb = QCheckBox(label)
            self.tags[key] = cb
            tgrid.addWidget(cb, i // 4, i % 4)
        tag_box = QGroupBox("Tags to write")
        tag_box.setLayout(tgrid)

        # App behaviour (stored by the GUI, not in config.ini)
        self.app_explicit = QCheckBox("Prefer explicit releases (hide clean versions that have an explicit twin)")
        self.app_tray = QCheckBox("Keep downloading in the system tray when the window is closed")
        app_lay = QVBoxLayout()
        app_lay.addWidget(self.app_explicit)
        app_lay.addWidget(self.app_tray)
        app_box = QGroupBox("App")
        app_box.setLayout(app_lay)

        # Navidrome
        self.nd_url = QLineEdit()
        self.nd_url.setPlaceholderText("https://navidrome.example.com")
        self.nd_user = QLineEdit()
        self.nd_password = PasswordField("Navidrome password")
        self.nd_rescan = QCheckBox("Ask Navidrome to rescan after albums are sent to the library")
        self.btn_nd_test = QPushButton("Test connection")
        self.nd_state = QLabel("")
        self.nd_state.setWordWrap(True)
        nd_buttons = QHBoxLayout()
        nd_buttons.addWidget(self.btn_nd_test)
        nd_buttons.addWidget(self.nd_state, 1)
        nd_help = QLabel("Used to know exactly which albums you have (the ✔ marks, duplicate checks, Library "
                         "doctor and artist completion) and to rescan after sending. Rescanning needs an admin "
                         "account. Leave empty to use the library folder names instead.")
        nd_help.setWordWrap(True)
        nd_help.setEnabled(False)
        nd = QFormLayout()
        nd.addRow("Address", self.nd_url)
        nd.addRow("Username", self.nd_user)
        nd.addRow("Password", self.nd_password)
        nd.addRow("", self.nd_rescan)
        nd.addRow("", nd_buttons)
        nd.addRow("", nd_help)
        nd_box = QGroupBox("Navidrome")
        nd_box.setLayout(nd)
        self.btn_nd_test.clicked.connect(self.test_navidrome)

        self.btn_save = QPushButton("Save settings")
        self.btn_save.setDefault(True)
        self.btn_revert = QPushButton("Revert")
        path_label = QLabel(f"Saved to {config.CONFIG_FILE} — the qobuz-dl command line uses the same file.")
        path_label.setEnabled(False)
        save_row = QHBoxLayout()
        save_row.addWidget(path_label, 1)
        save_row.addWidget(self.btn_revert)
        save_row.addWidget(self.btn_save)

        body = QWidget()
        bl = QVBoxLayout(body)
        for w in (acc_box, nd_box, dl_box, names_box, lib_box, tag_box, app_box):
            bl.addWidget(w)
        bl.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(body)

        lay = QVBoxLayout(self)
        lay.addWidget(scroll, 1)
        lay.addLayout(save_row)

        self.btn_save.clicked.connect(self.save)
        self.btn_revert.clicked.connect(self.load)
        self.btn_test.clicked.connect(self.test_login)
        self.btn_appid.clicked.connect(self.refresh_app_id)
        self.load()

    # ---- load / save ---------------------------------------------------------
    def load(self):
        cfg = config.load()
        self.email.setText(config.get(cfg, "email"))
        self._loaded_token = config.load_secret(cfg, "auth_token")
        self._loaded_genius = config.load_secret(cfg, "genius_token")
        self.token.setText(self._loaded_token)
        self.genius.setText(self._loaded_genius)
        self.no_keyring.setChecked(config.keyring_disabled(cfg))
        self.directory.setText(config.get(cfg, "directory") or config.get(cfg, "default_folder"))
        set_quality(self.quality, config.get(cfg, "default_quality", "27"))
        try:
            self.workers.setValue(int(config.get(cfg, "max_workers", "3") or 3))
        except ValueError:
            self.workers.setValue(3)
        self.blacklist.setText(config.get(cfg, "blacklist", "blacklist.txt"))
        self.embedded_size.setCurrentText(config.get(cfg, "embedded_art_size", "600") or "600")
        self.saved_size.setCurrentText(config.get(cfg, "saved_art_size", "org") or "org")
        self.disc_prefix.setText(config.get(cfg, "multiple_disc_prefix", "CD"))
        for key, _, default in FORMAT_FIELDS:
            self.formats[key].setText(config.get(cfg, key, default) or default)
        for key, _, default in LIBRARY_OPTIONS:
            self.bools[key].setChecked(config.get_bool(cfg, key, default))
        for key, _ in TAG_OPTIONS:
            self.tags[key].setChecked(not config.get_bool(cfg, key, False))
        self.app_explicit.setChecked(prefs.get_bool(prefs.PREFER_EXPLICIT, True))
        self.app_tray.setChecked(prefs.get_bool(prefs.MINIMIZE_TO_TRAY, True))
        nd = navidrome.settings()
        self.nd_url.setText(nd["url"])
        self.nd_user.setText(nd["user"])
        self._loaded_nd_password = navidrome.load_password()
        self.nd_password.setText(self._loaded_nd_password)
        self.nd_rescan.setChecked(prefs.get_bool(prefs.NAVIDROME_RESCAN, True))

    def save(self):
        formats = {key: self.formats[key].text().strip() for key, _, _ in FORMAT_FIELDS}
        problem = config.validate_formats(formats)
        if problem:
            QMessageBox.warning(self, "Check the name formats", problem)
            return

        cfg = config.load()
        config.set_value(cfg, "email", self.email.text().strip())
        keyring_was_disabled = config.keyring_disabled(cfg)
        config.set_value(cfg, "disable_keyring", self.no_keyring.isChecked())
        notes = []
        token, genius = self.token.text(), self.genius.text()
        storage_changed = keyring_was_disabled != self.no_keyring.isChecked()
        if token != self._loaded_token or storage_changed:
            notes.append(config.store_secret(cfg, "auth_token", token))
            if not self.no_keyring.isChecked():
                config.set_value(cfg, "password", "")
        if genius != self._loaded_genius or storage_changed:
            notes.append(config.store_secret(cfg, "genius_token", genius))

        config.set_value(cfg, "directory", self.directory.text())
        if cfg.has_option(config.SECTION, "default_folder"):
            cfg.remove_option(config.SECTION, "default_folder")
        config.set_value(cfg, "default_quality", self.quality.currentData())
        config.set_value(cfg, "max_workers", self.workers.value())
        config.set_value(cfg, "blacklist", self.blacklist.text())
        config.set_value(cfg, "embedded_art_size", self.embedded_size.currentText())
        config.set_value(cfg, "saved_art_size", self.saved_size.currentText())
        config.set_value(cfg, "multiple_disc_prefix", self.disc_prefix.text())
        for key, value in formats.items():
            config.set_value(cfg, key, value)
        for key, cb in self.bools.items():
            config.set_value(cfg, key, cb.isChecked())
        for key, cb in self.tags.items():
            config.set_value(cfg, key, not cb.isChecked())

        prefs.set_value(prefs.PREFER_EXPLICIT, self.app_explicit.isChecked())
        prefs.set_value(prefs.MINIMIZE_TO_TRAY, self.app_tray.isChecked())
        prefs.set_value(prefs.NAVIDROME_URL, self.nd_url.text().strip().rstrip("/"))
        prefs.set_value(prefs.NAVIDROME_USER, self.nd_user.text().strip())
        prefs.set_value(prefs.NAVIDROME_RESCAN, self.nd_rescan.isChecked())
        if self.nd_password.text() != getattr(self, "_loaded_nd_password", ""):
            notes.append(navidrome.store_password(self.nd_password.text()))
            self._loaded_nd_password = self.nd_password.text()
        try:
            config.save(cfg)
        except OSError as e:
            QMessageBox.warning(self, "Settings", f"Could not save config.ini: {e}")
            return
        self._loaded_token, self._loaded_genius = token, genius
        self.session.reset()
        self.login_state.setText("")
        self.status("Settings saved.")
        for note in filter(None, notes):
            QMessageBox.information(self, "Settings", note)
        self.saved.emit()

    # ---- account checks ------------------------------------------------------
    def _unsaved(self):
        cfg = config.load()
        return (self.token.text() != config.load_secret(cfg, "auth_token")
                or self.email.text().strip() != config.get(cfg, "email"))

    def test_navidrome(self):
        url = self.nd_url.text().strip()
        if not url:
            self.nd_state.setText("Enter the Navidrome address first.")
            return
        self.nd_url.setText(url)
        user, password = self.nd_user.text().strip(), self.nd_password.text()
        if not user:
            self.nd_state.setText("Enter the Navidrome username first.")
            return
        self.btn_nd_test.setEnabled(False)
        self.nd_state.setText("Connecting…")

        def work():
            nd = navidrome.Navidrome(url, user, password)
            version = nd.ping()
            albums = len(nd.all_albums())
            admin = True
            try:
                nd.scan_status()
            except navidrome.NavidromeError:
                admin = False
            return version, albums, admin

        def ok(result):
            version, albums, admin = result
            self.btn_nd_test.setEnabled(True)
            note = "" if admin else " — not an admin account, so rescans won't work"
            self.nd_state.setText(f"<span style='color:#3fa65a'>✔ Connected to Navidrome {version} · "
                                  f"{albums} albums</span>{note}")

        def fail(msg):
            self.btn_nd_test.setEnabled(True)
            self.nd_state.setText(f"<span style='color:#e5534b'>✖ {msg}</span>")
        run_async(work, ok, fail)

    def test_login(self):
        if self._unsaved():
            self.save()
        self.session.reset()
        self.btn_test.setEnabled(False)
        self.login_state.setText("Checking…")

        def ok(_client):
            self.btn_test.setEnabled(True)
            self.login_state.setText(f"<span style='color:#3fa65a'>✔ Logged in — {self.session.label or 'OK'}</span>")
            self.status(f"Logged in to Qobuz ({self.session.label})")

        def fail(msg):
            self.btn_test.setEnabled(True)
            self.login_state.setText(f"<span style='color:#e5534b'>✖ {msg}</span>")

        run_async(self.session.client, ok, fail)

    def refresh_app_id(self):
        self.btn_appid.setEnabled(False)
        self.login_state.setText("Fetching app ID from play.qobuz.com…")

        def work():
            cfg = config.load()
            config.refresh_app_credentials(cfg)
            config.save(cfg)
            return config.get(cfg, "app_id")

        def ok(app_id):
            self.btn_appid.setEnabled(True)
            self.session.reset()
            self.login_state.setText(f"App ID updated ({app_id}).")

        def fail(msg):
            self.btn_appid.setEnabled(True)
            self.login_state.setText(f"<span style='color:#e5534b'>✖ Could not fetch the app ID: {msg}</span>")

        run_async(work, ok, fail)
