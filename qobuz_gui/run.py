"""Subprocess entry point: runs the qobuz-dl CLI with a few GUI-friendly tweaks.

Usage: python -m qobuz_gui.run <qobuz-dl arguments...>

Environment:
  QDLGUI_RELEASE_TYPES  comma list (album,ep,single,live,compilation). When set, artist
                        discographies are filtered to those release types, using the
                        same filter the CLI's interactive menu offers.
"""

import os
import sys


def _apply_release_type_filter(types):
    import pick
    from qobuz_dl.core import QobuzDL

    labels = {"album": "Album", "ep": "EP", "single": "Single", "live": "Live", "compilation": "Compilation"}
    chosen = [labels[t] for t in types if t in labels]
    if not chosen:
        return

    # The CLI only offers the filter in its terminal menu (via pick). Answer that menu
    # with the GUI's choice instead of drawing it.
    def answer_menu(options, title=None, *args, **kwargs):
        if kwargs.get("multiselect"):
            return [(o, i) for i, o in enumerate(options) if o in chosen]
        return (options[0], 0)

    pick.pick = answer_menu
    QobuzDL._is_interactive_session = True


def _protect_sync_deletions(args):
    """qobuz-dl's playlist sync deletes files that left the playlist. Route those deletions to
    the library bin (inside the library) or the desktop Trash (elsewhere), and journal them."""
    import qobuz_dl.sync_playlist as sp
    from qobuz_gui import prefs, safeio

    url = next((a for a in args[1:] if "/playlist/" in a), "playlist")
    library_root = prefs.get_str(prefs.LIBRARY_DIR, "") or "/mnt/navidrome/Music"
    journal = safeio.Journal()
    state = {"batch": None, "seq": 0}

    def safe_remove(path):
        if state["batch"] is None:
            state["batch"] = journal.begin(f"Playlist sync — removed tracks ({url})")
        path = os.path.abspath(path)
        if os.path.isdir(library_root) and safeio.library_state(library_root) == "ok" and \
                safeio._inside(path, library_root):
            target = safeio.bin_path(library_root, path)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            os.rename(path, target)
            journal.record(state["batch"], state["seq"], "move", path, target,
                           {"stop_at": os.path.join(library_root, safeio.BIN_DIR)})
        else:
            dest = safeio.trash_put(path)
            journal.record(state["batch"], state["seq"], "trash", path, dest)
        state["seq"] += 1
        journal.finish(state["batch"], "done")

    class _OsProxy:
        def __getattr__(self, name):
            return getattr(os, name)
        remove = staticmethod(safe_remove)
        unlink = staticmethod(safe_remove)

    sp.os = _OsProxy()


def main():
    types = [t.strip().lower() for t in os.environ.get("QDLGUI_RELEASE_TYPES", "").split(",") if t.strip()]
    if types:
        _apply_release_type_filter(types)

    from qobuz_dl import cli

    args = sys.argv[1:]
    if args and args[0] in ("sp", "sync-playlist"):
        _protect_sync_deletions(args)

    sys.argv = ["qobuz-dl"] + sys.argv[1:]
    cli.main()


if __name__ == "__main__":
    main()
