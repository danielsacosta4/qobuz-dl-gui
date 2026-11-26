"""The in-app updater (Library tools → Update this app from GitHub), run for real against a fake download in a
throwaway install folder. It runs in a separate Python so the copy under test is the one being replaced."""
import os
import shutil
import subprocess
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SCRIPT = r'''
import io, os, sys, zipfile
app, mode = sys.argv[1], sys.argv[2]
sys.path.insert(0, app)
import requests
from qobuz_gui import updates

def archive():
    """What GitHub serves for the main branch: one top folder with the repository in it."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("qobuz-dl-gui-main/README.md", "readme")
        if mode != "broken":
            pkg = os.path.join(app, "qobuz_gui")
            for name in sorted(os.listdir(pkg)):
                path = os.path.join(pkg, name)
                if os.path.isfile(path) and name != "local_marker.txt":       # the marker is only in the old copy
                    data = open(path, "rb").read()
                    if name == "__init__.py":
                        data = b'"""Desktop GUI."""\n\n__version__ = "9.9.9"\n'
                    z.writestr("qobuz-dl-gui-main/qobuz_gui/" + name, data)
            z.writestr("qobuz-dl-gui-main/qobuz_gui/brand_new_module.py", "X = 1\n")
    return buf.getvalue()

class Response:
    def __init__(self, content):
        self.content = content
    def raise_for_status(self):
        pass

requests.get = lambda url, timeout=60: Response(archive())
try:
    print("RESULT", updates.update_gui())
except Exception as e:
    print("ERROR", type(e).__name__, e)
'''


def install(tmp_path, folder):
    """A copy of this checkout laid out like install.sh does it: <data>/qobuz-dl-gui/app/qobuz_gui."""
    app = tmp_path / folder
    shutil.copytree(os.path.join(REPO, "qobuz_gui"), app / "qobuz_gui", ignore=shutil.ignore_patterns("__pycache__"))
    (app / "qobuz_gui" / "local_marker.txt").write_text("the old copy")
    return app


def run(app, mode):
    out = subprocess.run([sys.executable, "-c", SCRIPT, str(app), mode], capture_output=True, text=True, timeout=120)
    return (out.stdout + out.stderr).strip()


def version_of(app):
    return (app / "qobuz_gui" / "__init__.py").read_text(encoding="utf-8")


def leftovers(app):
    return sorted(n for n in os.listdir(app) if n != "qobuz_gui")


def test_update_replaces_the_installed_copy(tmp_path):
    pytest.importorskip("requests")
    app = install(tmp_path, os.path.join("qobuz-dl-gui", "app"))
    assert run(app, "good") == "RESULT 9.9.9"
    assert '__version__ = "9.9.9"' in version_of(app)
    assert (app / "qobuz_gui" / "brand_new_module.py").is_file()          # files new in the update arrive
    assert (app / "qobuz_gui" / "app.py").is_file()
    assert not (app / "qobuz_gui" / "local_marker.txt").exists()           # the old copy is gone, not mixed in
    assert leftovers(app) == []                                            # no .old folder, no temp folder


def test_a_broken_download_leaves_the_working_copy_alone(tmp_path):
    pytest.importorskip("requests")
    app = install(tmp_path, os.path.join("qobuz-dl-gui", "app"))
    before = version_of(app)
    out = run(app, "broken")
    assert out.startswith("ERROR ValueError") and "doesn't contain the app" in out
    assert version_of(app) == before and (app / "qobuz_gui" / "local_marker.txt").is_file()
    assert leftovers(app) == []


def test_it_refuses_to_run_from_a_source_folder(tmp_path):
    pytest.importorskip("requests")
    app = install(tmp_path, "some-checkout")
    before = version_of(app)
    out = run(app, "good")
    assert out.startswith("ERROR RuntimeError") and "installed by install.sh" in out
    assert version_of(app) == before


def test_version_comparison():
    from qobuz_gui import updates
    assert updates.is_newer("0.11.0", "0.10.1") and updates.is_newer("0.10.1", "0.9.9")
    assert not updates.is_newer("0.10.1", "0.10.1") and not updates.is_newer("0.9.9", "0.10.0")
