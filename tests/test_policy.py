import pandas as pd

from driftops.policy import Controller, Signals, gate

CLEAN = {
    "null_increase": 0.0,
    "out_of_range": 0.0,
    "unseen_category": 0.0,
    "route_distance_mismatch": 0.0,
}
BROKEN = {**CLEAN, "route_distance_mismatch": 0.97}


def sig(psi=0.01, gap=0.0, dq=CLEAN, rows=1_000_000):
    return Signals(psi, "origin", gap, 0.15, dq, rows)


def day(n):
    return pd.Timestamp("2020-03-01") + pd.Timedelta(days=n)


def test_quiet_does_nothing():
    c = Controller()
    assert c.decide(day(0), sig()).action == "none"


def test_needs_k_consecutive_alarms():
    c = Controller()
    actions = [c.decide(day(i), sig(psi=0.5)).action for i in range(3)]
    assert actions == ["wait", "wait", "retrain"]


def test_a_quiet_day_resets_the_run():
    c = Controller()
    c.decide(day(0), sig(psi=0.5))
    c.decide(day(1), sig(psi=0.5))
    c.decide(day(2), sig())
    assert c.decide(day(3), sig(psi=0.5)).action == "wait"


def test_label_drift_alone_triggers():
    c = Controller()
    actions = [c.decide(day(i), sig(gap=0.3)).action for i in range(3)]
    assert actions[-1] == "retrain"


def test_cooldown_after_retrain():
    c = Controller()
    for i in range(3):
        c.decide(day(i), sig(psi=0.5))
    actions = [c.decide(day(3 + i), sig(psi=0.5)).action for i in range(3)]
    assert "retrain" not in actions


def test_broken_data_blocks_and_quarantines():
    c = Controller()
    d = c.decide(day(0), sig(psi=0.5, dq=BROKEN))
    assert d.action == "block"
    assert c.quarantine == [(day(-6), day(0))]


def test_no_retrain_while_window_holds_breached_days():
    c = Controller()
    c.decide(day(0), sig(psi=0.5, dq=BROKEN))
    actions = [c.decide(day(1 + i), sig(psi=0.5)).action for i in range(6)]
    assert "retrain" not in actions
    later = [c.decide(day(7 + i), sig(psi=0.5)).action for i in range(3)]
    assert later[-1] == "retrain"


def test_without_guard_broken_data_retrains():
    c = Controller(guard=False)
    actions = [c.decide(day(i), sig(psi=0.5, dq=BROKEN)).action for i in range(3)]
    assert actions[-1] == "retrain"


def test_gate_requires_better_brier():
    champ = {"brier": 0.20, "auc": 0.60}
    assert gate(champ, {"brier": 0.19, "auc": 0.60}).passed
    assert not gate(champ, {"brier": 0.21, "auc": 0.70}).passed


def test_gate_rejects_auc_collapse():
    champ = {"brier": 0.20, "auc": 0.60}
    assert not gate(champ, {"brier": 0.19, "auc": 0.55}).passed
