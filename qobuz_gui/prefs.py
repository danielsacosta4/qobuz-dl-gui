"""GUI-only settings (not part of qobuz-dl's config.ini), stored with QSettings."""

import json

from PySide6.QtCore import QSettings

_store = None


def store():
    global _store
    if _store is None:
        _store = QSettings("qobuz-dl-gui", "qobuz-dl-gui")
    return _store


def get_bool(key, default=False):
    value = store().value(key, None)
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).lower() in ("true", "1", "yes")


def get_str(key, default=""):
    value = store().value(key, None)
    return default if value is None else str(value)


def set_value(key, value):
    store().setValue(key, value)


def get_json(key, default=None):
    raw = store().value(key, None)
    if not raw:
        return default
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return default


def set_json(key, value):
    store().setValue(key, json.dumps(value))


# Keys
PREFER_EXPLICIT = "prefer_explicit"
LIBRARY_DIR = "library_dir"
DELETE_AFTER_SEND = "delete_after_send"
MINIMIZE_TO_TRAY = "minimize_to_tray"
DOWNLOAD_OPTIONS = "download_options"
SAVED_QUEUE = "saved_queue"
UPDATE_INFO = "update_info"
NAVIDROME_URL = "navidrome_url"
NAVIDROME_USER = "navidrome_user"
NAVIDROME_PASSWORD = "navidrome_password"   # only used when the keyring isn't available
NAVIDROME_RESCAN = "navidrome_rescan"
MB_OFFICIAL_ONLY = "mb_official_only"
HIDDEN_RELEASES = "hidden_releases"
HIDDEN_LABELS = "hidden_labels"
GUI_UPDATE_INFO = "gui_update_info"
