import json

import pytest

from qobuz_gui import qobuz_guard
from qobuz_gui.qobuz_guard import BudgetExceeded, Guard, QobuzBlocked


class Clock:
    def __init__(self, now=1_700_000_000.0):
        self.now, self.slept = now, []

    def __call__(self):
        return self.now

    def sleep(self, s):
        self.slept.append(s)
        self.now += s


class FakeResponse:
    def __init__(self, status, text=""):
        self.status_code, self.text = status, text


class HttpError(Exception):
    def __init__(self, status, text=""):
        super().__init__(f"{status} Client Error")
        self.response = FakeResponse(status, text)


def raiser(error):
    def fn():
        raise error
    return fn


@pytest.fixture
def g(tmp_path):
    clock = Clock()
    guard = Guard(path=str(tmp_path / "guard.json"), clock=clock, sleep=clock.sleep, gap=1.0)
    guard.clock = clock
    return guard


def test_requests_are_spaced(g):
    g.before(True)
    g.before(True)
    g.before(True)
    assert len(g.clock.slept) == 2 and all(0.99 <= s <= 1.0 for s in g.clock.slept)


def test_background_use_needs_a_budget_and_counts_it(g):
    with pytest.raises(BudgetExceeded):
        g.before(False)                       # default budget 0: nothing in the background
    g.configure(budget=2)
    g.before(False)
    g.before(False)
    assert g.used_today() == 2
    with pytest.raises(BudgetExceeded) as e:
        g.before(False)
    assert "daily limit of 2" in str(e.value)
    g.before(True)                            # a click is never counted against the budget
    g.clock.now += 86400 * 1.5
    assert g.used_today() == 0
    g.before(False)


def test_user_blocked_stops_everything_until_resumed_and_survives_a_restart(g):
    blocked = HttpError(403, '{"status":"error","code":403,"message":"Account is blocked","error_code":"USER_BLOCKED"}')
    with pytest.raises(QobuzBlocked) as e:
        g.call(raiser(blocked))
    assert "blocked" in str(e.value).lower() and "Contact Qobuz support" in str(e.value)
    calls = []
    with pytest.raises(QobuzBlocked):
        g.call(lambda: calls.append(1))        # nothing goes out any more
    assert calls == [] and g.permanent()
    again = Guard(path=g.path, clock=g.clock, sleep=g.clock.sleep)      # "restart"
    assert again.blocked_message()
    g.clock.now += 365 * 86400
    assert g.blocked_message()                 # a permanent block doesn't expire by itself
    g.resume()
    assert g.blocked_message() == "" and Guard(path=g.path).blocked_message() == ""
    assert g.call(lambda: "fine") == "fine"


def test_rate_limit_and_plain_403_pause_for_a_while(g):
    with pytest.raises(QobuzBlocked):
        g.call(raiser(HttpError(429, "slow down")))
    assert "too many requests" in g.blocked_message().lower() and not g.permanent()
    g.clock.now += qobuz_guard.RATE_LIMIT_PAUSE + 1
    assert g.blocked_message() == ""
    with pytest.raises(QobuzBlocked):
        g.call(raiser(HttpError(403, "<html>Forbidden</html>")))
    assert "403" in g.blocked_message()
    g.clock.now += qobuz_guard.FORBIDDEN_PAUSE + 1
    assert g.blocked_message() == ""


def test_other_errors_pass_through_and_dont_trip(g):
    with pytest.raises(HttpError):
        g.call(raiser(HttpError(404, "not found")))
    with pytest.raises(ValueError):
        g.call(raiser(ValueError("bad")))
    assert g.blocked_message() == ""


def test_a_damaged_state_file_is_ignored(tmp_path):
    p = tmp_path / "g.json"
    p.write_text("{not json")
    assert Guard(path=str(p)).blocked_message() == ""
    p.write_text(json.dumps({"blocked": {"permanent": True, "reason": "R"}}))
    assert "R" in Guard(path=str(p)).blocked_message()
