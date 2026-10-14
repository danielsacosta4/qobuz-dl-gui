"""Checks GitHub for a newer qobuz-dl (Sei969/qobuz-dl) and builds the job that installs it."""

import re
import sys
import time

REPO = "Sei969/qobuz-dl"
BRANCH = "master"
VERSION_URL = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}/qobuz_dl/__init__.py"
COMMITS_API = f"https://api.github.com/repos/{REPO}/commits/{BRANCH}"
COMMITS_PAGE = f"https://github.com/{REPO}/commits/{BRANCH}"
ARCHIVE_URL = f"https://github.com/{REPO}/archive/refs/heads/{BRANCH}.zip"
CHECK_EVERY = 24 * 3600

_VERSION_RE = re.compile(r'__version__\s*=\s*["\']([^"\']+)["\']')


def installed_version():
    """Reads the version from the installed package metadata, so it is right after an update
    even though this process imported the old code."""
    try:
        from importlib.metadata import version
        return version("qobuz-dl-ultimate")
    except Exception:
        pass
    try:
        import qobuz_dl
        return qobuz_dl.__version__
    except Exception:
        return "0"


def _as_tuple(v):
    parts = []
    for p in re.split(r"[.\-+]", str(v)):
        if p.isdigit():
            parts.append(int(p))
        else:
            m = re.match(r"(\d+)", p)
            parts.append(int(m.group(1)) if m else 0)
    return tuple(parts)


def is_newer(remote, local):
    return _as_tuple(remote) > _as_tuple(local)


def check():
    """Returns dict(installed, latest, newer, date, message). Network errors raise."""
    import requests
    r = requests.get(VERSION_URL, timeout=10)
    r.raise_for_status()
    m = _VERSION_RE.search(r.text)
    if not m:
        raise ValueError("Couldn't read the version on GitHub")
    latest = m.group(1)
    date = message = ""
    try:
        c = requests.get(COMMITS_API, timeout=10, headers={"Accept": "application/vnd.github+json"})
        if c.ok:
            info = c.json().get("commit", {})
            date = (info.get("committer") or {}).get("date", "")[:10]
            message = (info.get("message") or "").splitlines()[0][:120]
    except Exception:
        pass
    local = installed_version()
    return dict(installed=local, latest=latest, newer=is_newer(latest, local),
                date=date, message=message, checked=time.time())


def update_command():
    """pip command (run with this app's own Python) that installs the latest code from GitHub."""
    return [sys.executable, "-m", "pip", "install", "--upgrade", "--disable-pip-version-check",
            "--no-input", ARCHIVE_URL]


# ---- this app (github.com/danielsacosta4/qobuz-dl-gui) ---------------------------------
GUI_REPO = "danielsacosta4/qobuz-dl-gui"
GUI_BRANCH = "main"
GUI_VERSION_URL = f"https://raw.githubusercontent.com/{GUI_REPO}/{GUI_BRANCH}/qobuz_gui/__init__.py"
GUI_ARCHIVE_URL = f"https://github.com/{GUI_REPO}/archive/refs/heads/{GUI_BRANCH}.zip"
GUI_COMMITS_PAGE = f"https://github.com/{GUI_REPO}/commits/{GUI_BRANCH}"


def gui_installed_version():
    from . import __version__
    return __version__


def check_gui():
    import requests
    r = requests.get(GUI_VERSION_URL, timeout=10)
    r.raise_for_status()
    m = _VERSION_RE.search(r.text)
    if not m:
        raise ValueError("Couldn't read the app version on GitHub")
    latest = m.group(1)
    local = gui_installed_version()
    return dict(installed=local, latest=latest, newer=is_newer(latest, local), checked=time.time())


def update_gui():
    """Downloads the latest app from GitHub and swaps it in place of the running copy.
    Returns the new version. The app needs a restart afterwards."""
    import io
    import os
    import shutil
    import tempfile
    import zipfile

    import requests
    r = requests.get(GUI_ARCHIVE_URL, timeout=60)
    r.raise_for_status()
    package_dir = os.path.dirname(os.path.abspath(__file__))          # .../app/qobuz_gui
    parent = os.path.dirname(package_dir)
    if os.path.join("qobuz-dl-gui", "app") not in parent:
        raise RuntimeError("Self-update only works for the copy installed by install.sh "
                           "(this one runs from a source folder — use git pull there).")
    tmp = tempfile.mkdtemp(prefix="qobuz-gui-update-", dir=parent)
    try:
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            z.extractall(tmp)
        roots = [d for d in os.listdir(tmp) if os.path.isdir(os.path.join(tmp, d))]
        new_pkg = os.path.join(tmp, roots[0], "qobuz_gui") if roots else ""
        if not os.path.isfile(os.path.join(new_pkg, "__init__.py")):
            raise ValueError("The download doesn't contain the app")
        m = _VERSION_RE.search(open(os.path.join(new_pkg, "__init__.py"), encoding="utf-8").read())
        old = package_dir + ".old"
        shutil.rmtree(old, ignore_errors=True)
        os.replace(package_dir, old)
        try:
            shutil.copytree(new_pkg, package_dir)
        except Exception:
            os.replace(old, package_dir)       # put the working copy back
            raise
        shutil.rmtree(old, ignore_errors=True)
        return m.group(1) if m else "?"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
