import numpy as np
import pandas as pd
import pytest

from driftops import drift


@pytest.fixture
def frame():
    rng = np.random.default_rng(0)
    n = 20_000
    routes = {("ORD", "LGA"): 733.0, ("ATL", "DFW"): 731.0, ("SEA", "SFO"): 679.0}
    keys = list(routes)
    idx = rng.integers(0, len(keys), n)
    return pd.DataFrame(
        {
            "origin": [keys[i][0] for i in idx],
            "dest": [keys[i][1] for i in idx],
            "carrier": rng.choice(["AA", "DL", "UA"], n),
            "distance": [routes[keys[i]] for i in idx],
            "dep_hour": rng.integers(5, 23, n).astype(float),
        }
    )


@pytest.fixture
def ref(frame):
    return drift.fit_reference(
        frame,
        np.random.default_rng(1).random(len(frame)),
        ["distance", "dep_hour"],
        ["carrier", "origin"],
    )


def test_psi_is_zero_for_identical_distributions():
    p = np.array([0.2, 0.3, 0.5])
    assert drift.psi(p, p) == pytest.approx(0.0)


def test_psi_grows_with_shift():
    e = np.array([0.25, 0.25, 0.25, 0.25])
    small = np.array([0.27, 0.25, 0.25, 0.23])
    big = np.array([0.55, 0.25, 0.15, 0.05])
    assert 0 < drift.psi(e, small) < drift.psi(e, big)


def test_same_distribution_stays_under_threshold(frame, ref):
    fd = drift.feature_drift(ref, frame.sample(5_000, random_state=2))
    assert max(fd.values()) < 0.02


def test_shifted_feature_is_the_worst(frame, ref):
    cur = frame.sample(5_000, random_state=3).copy()
    cur["dep_hour"] = cur["dep_hour"].clip(upper=10)
    fd = drift.feature_drift(ref, cur)
    assert max(fd, key=fd.get) == "dep_hour"
    assert fd["dep_hour"] > 0.1


def test_clean_window_has_clean_quality(frame, ref):
    dq = drift.data_quality(ref, frame.sample(2_000, random_state=4))
    assert dq["route_distance_mismatch"] == 0
    assert dq["unseen_category"] == 0
    assert dq["null_increase"] == 0


def test_unit_bug_is_a_route_mismatch(frame, ref):
    cur = frame.sample(2_000, random_state=5).copy()
    cur["distance"] *= 1.609
    assert drift.data_quality(ref, cur)["route_distance_mismatch"] == pytest.approx(1.0)


def test_renamed_code_is_unseen(frame, ref):
    cur = frame.sample(2_000, random_state=6).copy()
    cur.loc[cur["carrier"] == "AA", "carrier"] = "AAL"
    share = (frame["carrier"] == "AA").mean()
    assert drift.data_quality(ref, cur)["unseen_category"] == pytest.approx(share, abs=0.05)


def test_nulls_are_counted(frame, ref):
    cur = frame.sample(2_000, random_state=7).copy()
    cur.loc[cur.index[:600], "dep_hour"] = np.nan
    assert drift.data_quality(ref, cur)["null_increase"] == pytest.approx(0.3)
