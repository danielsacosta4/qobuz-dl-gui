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

# Automation (0.9.6)
AUTO_HOURS = "auto_hours"                     # 0 = off
AUTO_LAST_RUN = "auto_last_run"
AUTO_LAST_RESULT = "auto_last_result"
AUTO_SEND = "auto_send"                       # send finished downloads without asking (safe cases only)
AUTO_FIX_LOCATION = "auto_fix_location"


def get_int(key, default=0):
    try:
        return int(store().value(key, default) or 0)
    except (TypeError, ValueError):
        return default

# Discover (0.9.7)
DISCOVER_DISMISSED = "discover_dismissed"      # norm artist names you said no to

# Studio albums: Qobuz or Wikipedia (0.9.8)
STUDIO_DEFAULT = "studio_default"              # "qobuz" or "wikipedia" for artists without their own choice
STUDIO_SOURCES = "studio_sources"              # {norm artist: "qobuz" | "wikipedia"}
WIKI_LANG = "wiki_lang"
STUDIO_OVERRIDES = "studio_overrides"          # {norm artist: {page, lang, add: [{title, year}], remove: [title]}}

# Qobuz contact (0.10.0)
QOBUZ_BUDGET = "qobuz_budget"                  # background Qobuz requests allowed per day (0 = none)

# Safe mode (0.10.1): on unless you switch it off
SAFE_MODE = "safe_mode"
SAFE_GAP = 3.0                  # seconds between Qobuz requests in safe mode (1 otherwise)
SAFE_DELAY = 2                  # seconds between tracks in safe mode, one download at a time
SAFE_LOOKUPS = 5                # Qobuz link look-ups per click in safe mode


def safe_mode():
    """Safe mode keeps Qobuz traffic to the bare minimum: see the README. On by default."""
    return get_bool(SAFE_MODE, True)


def qobuz_budget():
    """Background Qobuz requests allowed per day: always 0 in safe mode."""
    return 0 if safe_mode() else get_int(QOBUZ_BUDGET, 0)
