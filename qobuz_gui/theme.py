"""Status colours, picked once at start-up so they stay readable on light and dark themes.

Read them as theme.OK etc. at the moment you need them (not copied at import time):
the values change when init() sees a dark palette.
"""

_LIGHT = {"OK": "#2e8b4a", "WARN": "#a87b14", "ERROR": "#c9372c", "MUTED": "#707070", "ACCENT": "#2f6fc4"}
_DARK = {"OK": "#5cc47a", "WARN": "#e0b04a", "ERROR": "#f07068", "MUTED": "#9a9a9a", "ACCENT": "#6aa6f0"}

OK, WARN, ERROR, MUTED, ACCENT = (_LIGHT[k] for k in ("OK", "WARN", "ERROR", "MUTED", "ACCENT"))
dark = False


def init(app):
    """Chooses the light or dark set from the application palette."""
    global OK, WARN, ERROR, MUTED, ACCENT, dark
    dark = app.palette().window().color().lightness() < 128
    c = _DARK if dark else _LIGHT
    OK, WARN, ERROR, MUTED, ACCENT = c["OK"], c["WARN"], c["ERROR"], c["MUTED"], c["ACCENT"]


def qcolor(hex_value):
    from PySide6.QtGui import QColor
    return QColor(hex_value)


def span(text, hex_value, bold=False):
    tag = "b" if bold else "span"
    return f"<{tag} style='color:{hex_value}'>{text}</{tag}>"
