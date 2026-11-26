"""pytest setup: the package from this checkout, and a throwaway cache folder (cache.PATH is fixed on import)."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_tmp = tempfile.mkdtemp(prefix="qobuz-gui-test-")
os.environ["XDG_CACHE_HOME"] = os.path.join(_tmp, "cache")
os.environ["XDG_CONFIG_HOME"] = os.path.join(_tmp, "config")
os.environ["XDG_DATA_HOME"] = os.path.join(_tmp, "data")
os.environ["CONFIG_DIR"] = os.path.join(_tmp, "config")        # qobuz-dl's config.ini lives here in tests
os.environ["HOME"] = _tmp                                      # nothing a test does can touch the real home
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
