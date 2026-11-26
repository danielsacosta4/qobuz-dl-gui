from qobuz_gui import prefs, qobuz_guard


def test_safe_mode_is_on_by_default_and_zeroes_the_budget(monkeypatch):
    store = {}
    monkeypatch.setattr(prefs, "get_bool", lambda k, d=False: store.get(k, d))
    monkeypatch.setattr(prefs, "get_int", lambda k, d=0: store.get(k, d))
    assert prefs.safe_mode() is True and prefs.qobuz_budget() == 0
    store[prefs.QOBUZ_BUDGET] = 50
    assert prefs.qobuz_budget() == 0                      # still safe: the stored 50 is ignored
    store[prefs.SAFE_MODE] = False
    assert prefs.safe_mode() is False and prefs.qobuz_budget() == 50


def test_safe_mode_numbers_are_conservative():
    assert prefs.SAFE_GAP > qobuz_guard.GAP and prefs.SAFE_DELAY >= 2 and prefs.SAFE_LOOKUPS <= 5


def test_guard_gap_can_be_reconfigured(tmp_path):
    g = qobuz_guard.Guard(path=str(tmp_path / "g.json"))
    g.configure(gap=3.0, budget=0)
    assert g.gap == 3.0 and g.budget == 0
    g.configure(gap=-5)
    assert g.gap == 0.0
