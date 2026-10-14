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


def main():
    types = [t.strip().lower() for t in os.environ.get("QDLGUI_RELEASE_TYPES", "").split(",") if t.strip()]
    if types:
        _apply_release_type_filter(types)

    from qobuz_dl import cli

    sys.argv = ["qobuz-dl"] + sys.argv[1:]
    cli.main()


if __name__ == "__main__":
    main()
