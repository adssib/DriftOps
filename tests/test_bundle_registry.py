import numpy as np
import pandas as pd
import pytest

from driftops import bundle, drift, registry
from driftops import features as F
from driftops import model as M


@pytest.fixture(scope="module")
def tiny_model():
    rng = np.random.default_rng(0)
    n = 4000
    df = pd.DataFrame(
        {
            "flight_date": pd.Timestamp("2018-01-01")
            + pd.to_timedelta(rng.integers(0, 60, n), unit="D"),
            "carrier": rng.choice(["AA", "DL", "UA"], n),
            "origin": rng.choice(["ORD", "ATL", "DFW"], n),
            "dest": rng.choice(["LGA", "SEA", "SFO"], n),
            "dep_hour": rng.integers(5, 23, n),
            "arr_hour": rng.integers(5, 23, n),
            "distance": rng.uniform(200, 2500, n).astype("float32"),
            "sched_minutes": rng.uniform(50, 300, n).astype("float32"),
            "origin_hour_load": rng.integers(1, 40, n).astype("float32"),
            "dest_hour_load": rng.integers(1, 40, n).astype("float32"),
            "month": rng.integers(1, 3, n),
            "day_of_week": rng.integers(0, 7, n),
            "days_to_holiday": rng.integers(0, 30, n),
        }
    )
    df[F.LABEL] = (rng.random(n) < 0.2 + 0.3 * (df["dep_hour"] > 17)).astype("int8")
    for c in F.CATEGORICAL:
        df[c] = df[c].astype("category")
    return M.train(df, "v1", rounds=20, threads=1), df


def test_reference_round_trips(tiny_model):
    m, _ = tiny_model
    back = drift.reference_from_dict(drift.reference_to_dict(m.reference))
    assert np.isinf(back.numeric_edges["distance"][0])
    assert back.route_distance == pytest.approx(m.reference.route_distance)
    assert back.vocab == m.reference.vocab


def test_bundle_round_trip_predicts_identically(tiny_model, tmp_path):
    m, df = tiny_model
    bundle.save_bundle(tmp_path / "b", m, {"version": 1})
    back = bundle.load_bundle(tmp_path / "b")
    np.testing.assert_allclose(back.predict(df.head(200)), m.predict(df.head(200)))


def test_register_download_load(tiny_model, tmp_path, monkeypatch):
    m, df = tiny_model
    monkeypatch.setenv("MLFLOW_DISABLE_AGENT_HINT", "1")
    uri = f"sqlite:///{tmp_path}/mlflow.db"
    monkeypatch.chdir(tmp_path)  # local artifact root lands in tmp
    bundle.save_bundle(tmp_path / "b", m)
    v1 = registry.register(tmp_path / "b", "driftops-test", uri=uri)
    v2 = registry.register(tmp_path / "b", "driftops-test", uri=uri)
    assert (v1, v2) == (1, 2)
    registry.set_champion("driftops-test", 1, uri=uri)
    path = registry.download("driftops-test", 1, tmp_path / "dl", uri=uri)
    back = bundle.load_bundle(path)
    np.testing.assert_allclose(back.predict(df.head(50)), m.predict(df.head(50)))
    assert registry.versions("driftops-test", uri=uri) == [1, 2]


def test_wait_for_version_retries_then_succeeds():
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise ConnectionError("registry not up")

    registry.wait_for_version("x", 1, get=flaky, interval_s=0.01, timeout_s=5)
    assert calls["n"] == 3


def test_wait_for_version_gives_up():
    def down():
        raise ConnectionError("down")

    with pytest.raises(TimeoutError):
        registry.wait_for_version("x", 1, get=down, interval_s=0.01, timeout_s=0.05)
