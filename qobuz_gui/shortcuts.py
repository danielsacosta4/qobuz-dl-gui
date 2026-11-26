"""Keyboard shortcuts and link pasting/dropping for the main window."""

import os
import re

# (keys, what they do) — also listed in Library tools
SHORTCUTS = [
    ("Ctrl+F", "Go to the search box"),
    ("Ctrl+1 … Ctrl+8", "Switch tab"),
    ("Ctrl+D", "Download the selected search results"),
    ("Ctrl+Shift+C", "Copy the links of the selected search results"),
    ("Delete", "Hide the selected releases in Search (again to show them)"),
    ("Ctrl+V", "Queue the Qobuz links on the clipboard (when you're not typing in a box)"),
    ("Ctrl+Return", "Send to library (Library tab)"),
    ("F5", "Search again · rescan downloads · scan the library — depending on the tab"),
    ("Esc", "Stop the running download (with the queue or log focused)"),
    ("Ctrl+Q", "Quit"),
    ("Drag and drop", "Drop Qobuz links or a .txt list anywhere on the window to queue them"),
]

LINK_RE = re.compile(r"https?://(?:www\.|open\.|play\.)?qobuz\.com/[^\s\"'<>]+", re.IGNORECASE)


def links_in(text):
    """Qobuz links in a piece of text, in order, without repeats."""
    out = []
    for m in LINK_RE.finditer(text or ""):
        url = m.group(0).rstrip(".,;)")
        if url not in out:
            out.append(url)
    return out


def sources_from_mime(mime):
    """Qobuz links and local .txt lists from a drag-and-drop."""
    out = []
    if mime.hasUrls():
        for url in mime.urls():
            if url.isLocalFile():
                path = url.toLocalFile()
                if path.lower().endswith(".txt") and os.path.isfile(path):
                    out.append(path)
            else:
                out.extend(links_in(url.toString()))
    if not out and mime.hasText():
        out.extend(links_in(mime.text()))
    return list(dict.fromkeys(out))


def _key(win, seq, slot, widget=None, context=None):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QKeySequence, QShortcut
    sc = QShortcut(QKeySequence(seq), widget or win)
    sc.setContext(Qt.WindowShortcut if context is None else context)
    sc.activated.connect(slot)
    return sc


def install(win):
    """win: the MainWindow (tabs, search, library, health, activity …)."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QKeySequence
    _key(win, "Ctrl+F", lambda: (win.tabs.setCurrentWidget(win.search), win.search.focus_search()))
    for n in range(1, min(9, win.tabs.count() + 1)):
        _key(win, f"Ctrl+{n}", lambda i=n - 1: win.tabs.setCurrentIndex(i))
    _key(win, "Ctrl+D", win.search.download_selected, win.search, Qt.WidgetWithChildrenShortcut)
    _key(win, "Ctrl+Shift+C", win.search.copy_selected, win.search, Qt.WidgetWithChildrenShortcut)
    _key(win, QKeySequence.Delete, win.search.hide_selected, win.search.table, Qt.WidgetShortcut)
    _key(win, "Ctrl+Return", win.library.send, win.library, Qt.WidgetWithChildrenShortcut)
    _key(win, "Ctrl+Enter", win.library.send, win.library, Qt.WidgetWithChildrenShortcut)
    _key(win, "F5", win.refresh_current)
    _key(win, QKeySequence.Paste, win.paste_links)
    _key(win, "Ctrl+Q", win.quit_app)
