"""Reading and writing qobuz-dl's own config.ini, so the GUI and the CLI share one setup."""

import builtins
import configparser
import contextlib
import io
import os
import re

SECTION = "qobuz"
KEYRING_SERVICE = "qobuz-dl"

_config_dir = os.environ.get("CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".config")
CONFIG_PATH = os.path.join(_config_dir, "qobuz-dl")
CONFIG_FILE = os.path.join(CONFIG_PATH, "config.ini")
QOBUZ_DB = os.path.join(CONFIG_PATH, "qobuz_dl.db")

QUALITIES = [
    (5, "MP3 320 kbps"),
    (6, "CD — 16-bit / 44.1 kHz FLAC"),
    (7, "Hi-Res — 24-bit ≤ 96 kHz FLAC"),
    (27, "Hi-Res — 24-bit max FLAC"),
]

ART_SIZES = ["50", "100", "150", "300", "600", "max", "org"]

TRUE = {"true", "yes", "y", "1", "on"}

ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def strip_ansi(text):
    return ANSI_RE.sub("", text)


def exists():
    return os.path.isfile(CONFIG_FILE)


def load():
    cfg = configparser.ConfigParser(interpolation=None)
    if exists():
        try:
            from qobuz_dl.utils import read_config_file
            read_config_file(cfg, CONFIG_FILE)
        except Exception:
            cfg.read(CONFIG_FILE, encoding="utf-8")
    if not cfg.has_section(SECTION):
        cfg.add_section(SECTION)
    return cfg


def save(cfg):
    os.makedirs(CONFIG_PATH, exist_ok=True)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        cfg.write(f)


def get(cfg, key, default=""):
    return cfg.get(SECTION, key, fallback=default)


def get_bool(cfg, key, default=False):
    raw = cfg.get(SECTION, key, fallback=None)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in TRUE


def set_value(cfg, key, value):
    if isinstance(value, bool):
        value = "true" if value else "false"
    cfg.set(SECTION, key, str(value))


def keyring_disabled(cfg):
    return get_bool(cfg, "disable_keyring", False)


def _keyring():
    try:
        import keyring
        return keyring
    except Exception:
        return None


def load_secret(cfg, key):
    """Returns auth_token / genius_token the same way the CLI resolves them."""
    ini_value = get(cfg, key)
    if keyring_disabled(cfg):
        if key == "auth_token" and not ini_value:
            return get(cfg, "password")
        return ini_value
    kr = _keyring()
    if kr is not None:
        try:
            value = kr.get_password(KEYRING_SERVICE, key)
            if value:
                return value
        except Exception:
            pass
    return ini_value


def store_secret(cfg, key, value):
    """Saves a token to the OS keyring (KWallet / Secret Service) or to config.ini.

    Returns a short note when the keyring could not be used and the token went to
    config.ini instead, otherwise None.
    """
    if keyring_disabled(cfg):
        set_value(cfg, key, value)
        return None
    kr = _keyring()
    if kr is not None:
        try:
            if value:
                kr.set_password(KEYRING_SERVICE, key, value)
            else:
                try:
                    kr.delete_password(KEYRING_SERVICE, key)
                except Exception:
                    pass
            set_value(cfg, key, "")
            return None
        except Exception:
            pass
    set_value(cfg, key, value)
    return f"The system keyring is not available, so the {key.replace('_', ' ')} was saved in config.ini."


def credentials(cfg):
    """Everything qopy.Client needs: (email, password, app_id, secrets, token)."""
    token = load_secret(cfg, "auth_token")
    email = get(cfg, "email")
    password = token or get(cfg, "password")
    app_id = get(cfg, "app_id")
    secrets = [s for s in get(cfg, "secrets").split(",") if s]
    return email, password, app_id, secrets, token


def create_with_wizard(email, token, directory, quality, disable_keyring, fetch_lyrics, genius_token):
    """Runs qobuz-dl's own first-run wizard with the answers filled in.

    Using the CLI's wizard keeps every default key identical to what `qobuz-dl -r`
    writes (it also fetches the current app ID and secrets from play.qobuz.com).
    Prompts are matched by their wording, so a reordered wizard still works; an
    unknown prompt gets an empty answer, which the wizard treats as its default.
    """
    from qobuz_dl import cli

    def answer(prompt=""):
        p = strip_ansi(str(prompt)).lower()
        if "genius" in p:
            return genius_token or ""
        if "keyring" in p:  # mentions "tokens" too, so it is checked first
            return "yes" if disable_keyring else "no"
        if "email" in p:
            return email
        if "token" in p:
            return token
        if "lyrics" in p:
            return "yes" if fetch_lyrics else "no"
        if "download folder" in p:
            return directory
        if "quality" in p:
            return str(quality)
        return ""

    os.makedirs(CONFIG_PATH, exist_ok=True)
    original_input = builtins.input
    builtins.input = answer
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            cli._reset_config(CONFIG_FILE)
    finally:
        builtins.input = original_input
    return strip_ansi(out.getvalue())


def refresh_app_credentials(cfg):
    """Fetches a fresh app ID and secrets from the Qobuz web player bundle."""
    from qobuz_dl.bundle import Bundle
    bundle = Bundle()
    set_value(cfg, "app_id", str(bundle.get_app_id()))
    set_value(cfg, "secrets", ",".join(bundle.get_secrets().values()))


def validate_formats(formats):
    """Runs the CLI's pre-flight check on format strings. Returns error text or ''."""
    from qobuz_dl import cli
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            cli.validate_config_formats(formats)
    except SystemExit:
        text = strip_ansi(out.getvalue()).strip()
        return text or "Invalid format string."
    return ""


SAMPLE = {
    "album_artist": "Daft Punk", "albumartist": "Daft Punk", "artist": "Daft Punk",
    "track_artist": "Daft Punk", "album": "Random Access Memories (Deluxe)",
    "album_title": "Random Access Memories (Deluxe)", "album_title_base": "Random Access Memories",
    "album_version": "Deluxe", "version": "Deluxe", "version_tag": " - Deluxe",
    "album_genre": "Electronic", "album_composer": "Thomas Bangalter",
    "track_composer": "Guy-Manuel de Homem-Christo", "label": "Columbia",
    "copyright": "℗ 2013 Daft Life", "upc": "888837168618", "barcode": "888837168618",
    "release_date": "2013-05-17", "year": "2013", "media_type": "CD", "format": "FLAC",
    "bit_depth": "24", "sampling_rate": "88.2", "disc_count": "1", "track_count": "13",
    "disc_number": "01", "track_number": "08", "ExplicitFlag": "[E]", "explicit": "[E]",
    "release_type": "Album", "tracktitle": "Get Lucky", "track_title": "Get Lucky",
    "track_title_base": "Get Lucky", "track_id": "19656474", "isrc": "USQX91300108",
    "album_id": "0060253780968", "album_url": "https://play.qobuz.com/album/0060253780968",
    "quality_tag": "24B-88.2kHz",
}


def preview_format(pattern):
    try:
        from qobuz_dl.utils import PartialFormatter
        return PartialFormatter().format(pattern, **SAMPLE)
    except Exception as e:
        return f"(invalid: {e})"
