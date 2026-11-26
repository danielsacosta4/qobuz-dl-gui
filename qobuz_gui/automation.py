"""Decisions for the background check, kept free of Qt so they can be tested.

The background check never contacts Qobuz and never downloads anything. It scans your own library and, when you
switch it on, moves an artist folder to the right letter (undoable) without a preview. The rest waits for you.
"""

import time

from . import doctor

SAFE_FIX_KINDS = (doctor.LOCATION,)      # a cover fix needs a Qobuz download, so it is never automatic
INTERVALS = [(0, "Off"), (6, "Every 6 hours"), (12, "Every 12 hours"), (24, "Once a day")]


def due(last_run, hours, now=None):
    """True when the background check should run now."""
    if not hours:
        return False
    now = time.time() if now is None else now
    return not last_run or now - float(last_run) >= hours * 3600


def seconds_left(last_run, hours, now=None):
    if not hours:
        return None
    now = time.time() if now is None else now
    if not last_run:
        return 0
    return max(0, int(float(last_run) + hours * 3600 - now))


def safe_fixes(issues, kinds, exceptions=()):
    """The issues the background check may fix on its own."""
    allowed = set(kinds) & set(SAFE_FIX_KINDS)
    exceptions = set(exceptions)
    out = []
    for i in issues:
        if i.kind not in allowed or not i.folder:
            continue
        if i.key in exceptions or i.legacy_key in exceptions:
            continue
        if i.kind == doctor.LOCATION and not i.extra.get("to"):
            continue
        out.append(i)
    return out


def auto_sendable(albums):
    """Downloaded albums that can go to the library without a decision: complete, a real album,
    a destination worked out, no other copy of it there and nothing to merge into."""
    import os
    out = []
    for a in albums:
        if a.kind != "album" or a.state != "ready" or not a.dest or a.others:
            continue
        if os.path.exists(a.dest):
            continue
        out.append(a)
    return out


def summary(fixed=0, failed=0):
    parts = []
    if fixed:
        parts.append(f"{fixed} problem{'s' if fixed != 1 else ''} fixed")
    if failed:
        parts.append(f"{failed} fix{'es' if failed != 1 else ''} failed")
    return ", ".join(parts) or "nothing to fix"
