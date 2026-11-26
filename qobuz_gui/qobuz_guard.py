"""One gate for every Qobuz request the app makes itself: a minimum gap between requests, a daily budget for
anything that isn't a direct click, and a hard stop when Qobuz says the account is blocked or rate-limited.

Qt-free and thread-safe. State lives in a small JSON file next to the cache, so a restart doesn't forget a block
(and doesn't go straight back to asking). Downloads run in a separate qobuz-dl process; the app refuses to
start them while this gate is closed (see `blocked_message`).
"""

import json
import os
import threading
import time

from . import cache

GAP = 1.0                      # seconds between two requests (user clicks included)
RATE_LIMIT_PAUSE = 30 * 60     # after HTTP 429
FORBIDDEN_PAUSE = 60 * 60      # after a 403 that doesn't say the account is blocked
DEFAULT_PATH = os.path.join(cache._dir, "qobuz-guard.json")


class QobuzBlocked(Exception):
    """Qobuz can't be asked right now (blocked, paused, or over the budget). The text is for the person."""


class BudgetExceeded(QobuzBlocked):
    pass


def looks_blocked(text):
    t = str(text or "").lower()
    return "user_blocked" in t or "account is blocked" in t


class Guard:
    def __init__(self, path=DEFAULT_PATH, clock=time.time, sleep=time.sleep, gap=GAP):
        self.path, self._clock, self._sleep, self.gap = path, clock, sleep, gap
        self._lock = threading.Lock()
        self._last = 0.0
        self.budget = 0                # background requests allowed per day (0 = none)
        self._state = self._load()

    # ---- persistence -----------------------------------------------------------------------
    def _load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save(self):
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = self.path + ".part"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._state, f)
            os.replace(tmp, self.path)
        except OSError:
            pass

    # ---- state ---------------------------------------------------------------------------------
    def configure(self, budget=None, gap=None):
        if budget is not None:
            self.budget = max(0, int(budget))
        if gap is not None:
            self.gap = max(0.0, float(gap))

    def blocked_message(self):
        """Why Qobuz can't be asked right now, or "" when it can."""
        with self._lock:
            return self._blocked_text()

    def _blocked_text(self):
        b = self._state.get("blocked")
        if not b:
            return ""
        if b.get("permanent"):
            return (b.get("reason") or "Qobuz says this account is blocked.") + \
                " Contact Qobuz support. The app won't ask Qobuz again until you press “I've been unblocked”."
        left = float(b.get("until") or 0) - self._clock()
        if left <= 0:
            self._state.pop("blocked", None)
            self._save()
            return ""
        return f"{b.get('reason') or 'Qobuz asked the app to slow down.'} Paused for another {max(1, int(left // 60))} min."

    def permanent(self):
        """True while the block lasts until you press “I've been unblocked”."""
        with self._lock:
            return bool(self._blocked_text() and (self._state.get("blocked") or {}).get("permanent"))

    def trip(self, reason, seconds=None):
        """Stop asking Qobuz: for `seconds`, or (None) until resume() is called."""
        with self._lock:
            self._state["blocked"] = ({"permanent": True, "reason": reason} if seconds is None
                                      else {"permanent": False, "until": self._clock() + seconds, "reason": reason})
            self._save()

    def resume(self):
        with self._lock:
            self._state.pop("blocked", None)
            self._save()

    def used_today(self):
        with self._lock:
            return self._used()

    def _used(self):
        day = time.strftime("%Y-%m-%d", time.localtime(self._clock()))
        d = self._state.get("day") or {}
        return d.get("n", 0) if d.get("date") == day else 0

    # ---- the gate -------------------------------------------------------------------------------
    def before(self, interactive=True):
        """Call before every Qobuz request. Raises QobuzBlocked when it must not go out; otherwise waits for the gap."""
        with self._lock:
            text = self._blocked_text()
            if text:
                raise QobuzBlocked(text)
            if not interactive:
                if self.budget <= 0:
                    raise BudgetExceeded("Background Qobuz checks are switched off (Settings → Qobuz contact).")
                if self._used() >= self.budget:
                    raise BudgetExceeded(f"The daily limit of {self.budget} background Qobuz requests is used up. "
                                         "It resets tomorrow, or raise it in Settings → Qobuz contact.")
                day = time.strftime("%Y-%m-%d", time.localtime(self._clock()))
                self._state["day"] = {"date": day, "n": self._used() + 1}
                self._save()
            wait = self.gap - (self._clock() - self._last)
            self._last = self._clock() + max(0.0, wait)
        if wait > 0:
            self._sleep(wait)

    def note_error(self, error):
        """Look at a failed request: a block or rate limit trips the gate. Returns True when it did."""
        response = getattr(error, "response", None)
        status = getattr(response, "status_code", None)
        text = getattr(response, "text", "") or str(error)
        if looks_blocked(text):
            self.trip("Qobuz says this account is blocked (USER_BLOCKED).")
            return True
        if status == 429 or "too many requests" in str(text).lower():
            self.trip("Qobuz says there were too many requests.", RATE_LIMIT_PAUSE)
            return True
        if status == 403:
            self.trip("Qobuz refused the request (HTTP 403).", FORBIDDEN_PAUSE)
            return True
        return False

    def call(self, fn, interactive=True):
        """Run fn() (one Qobuz request or a short run of them) through the gate."""
        self.before(interactive)
        try:
            return fn()
        except QobuzBlocked:
            raise
        except Exception as e:
            if self.note_error(e):
                raise QobuzBlocked(self.blocked_message() or "Qobuz refused the request.") from e
            raise


guard = Guard()
