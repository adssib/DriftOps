import datetime as dt
import json

import numpy as np
import pandas as pd
import pytest
import structlog

from driftops import db, drift, monitors, policy
from driftops import features as F

LG = structlog.get_logger()
D = dt.date


# days and windows ----------------------------------------------------------------------------


def test_first_run_starts_at_the_first_day():
    days = monitors.days_to_process(None, dt.datetime(2020, 3, 13), D(2020, 3, 10))
    assert days == [D(2020, 3, 10), D(2020, 3, 11), D(2020, 3, 12)]


def test_mid_day_clock_excludes_the_unfinished_day():
    assert monitors.days_to_process(
        D(2020, 3, 10), dt.datetime(2020, 3, 12, 15), D(2020, 3, 10)
    ) == [D(2020, 3, 11)]


def test_catch_up_is_capped():
    days = monitors.days_to_process(None, dt.datetime(2020, 6, 1), D(2020, 3, 10), max_days=5)
    assert days == [D(2020, 3, 10) + dt.timedelta(days=i) for i in range(5)]


def test_clock_jump_days_are_returned_not_merged():
    days = monitors.days_to_process(D(2020, 2, 2), dt.datetime(2020, 2, 6), D(2020, 1, 20))
    assert days == [D(2020, 2, 3), D(2020, 2, 4), D(2020, 2, 5)]


def test_windows_match_the_backtest():
    assert monitors.window("drift", D(2020, 3, 27)) == (
        dt.datetime(2020, 3, 21),
        dt.datetime(2020, 3, 28),
    )
    assert monitors.window("perf", D(2020, 3, 27)) == (
        dt.datetime(2020, 3, 20),
        dt.datetime(2020, 3, 27),
    )


# measures ------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def ref_and_frame():
    rng = np.random.default_rng(0)
    n = 6000
    routes = {("ORD", "LGA"): 733.0, ("ATL", "SEA"): 2182.0}
    keys = list(routes)
    idx = rng.integers(0, 2, n)
    frame = pd.DataFrame(
        {
            "carrier": rng.choice(["AA", "DL"], n),
            "origin": [keys[i][0] for i in idx],
            "dest": [keys[i][1] for i in idx],
            "distance": [routes[keys[i]] for i in idx],
            **{c: rng.integers(1, 24, n).astype(float) for c in ["dep_hour", "arr_hour"]},
            **{
                c: rng.uniform(1, 50, n)
                for c in ["sched_minutes", "origin_hour_load", "dest_hour_load"]
            },
        }
    )
    ref = drift.fit_reference(frame, rng.random(n), F.NUMERIC, F.CATEGORICAL)
    return ref, frame


def test_quality_clean_then_broken(ref_and_frame):
    ref, frame = ref_and_frame
    lim = policy.Limits()
    assert monitors.measure_quality(ref, frame.sample(2000, random_state=1), 1, lim).alarm is False
    broken = frame.sample(2000, random_state=2).assign(distance=lambda d: d["distance"] * 1.609)
    r = monitors.measure_quality(ref, broken, 1, lim)
    assert r.alarm and "route_distance_mismatch" in r.metrics["breaches"]


def test_drift_quiet_then_shifted(ref_and_frame):
    ref, frame = ref_and_frame
    lim = policy.Limits()
    same = frame.sample(2000, random_state=3)
    assert (
        monitors.measure_drift(ref, same, np.random.default_rng(4).random(2000), 1, lim).alarm
        is False
    )
    shifted = same.assign(dep_hour=6.0)
    r = monitors.measure_drift(ref, shifted, np.random.default_rng(4).random(2000), 1, lim)
    assert r.alarm and r.metrics["worst_feature"] == "dep_hour"


def test_perf_alarm_on_calibration_gap():
    rng = np.random.default_rng(5)
    n = 3000
    calm = pd.DataFrame(
        {
            "score": np.full(n, 0.2),
            "disrupted": (rng.random(n) < 0.2).astype(int),
            "carrier": rng.choice(["AA", "DL", "UA"], n),
            "model_version": 1,
        }
    )
    shock = calm.assign(disrupted=(rng.random(n) < 0.5).astype(int))
    lim = policy.Limits()
    assert monitors.measure_perf(calm, 1, lim).alarm is False
    r = monitors.measure_perf(shock, 1, lim)
    assert r.alarm and "label:calibration_gap" in r.metrics["alarms"]
    assert list(r.metrics["slices"]) == list(shock["carrier"].value_counts().index)


def test_empty_windows_are_explicit_not_errors():
    lim = policy.Limits()
    assert monitors.measure_quality(None, pd.DataFrame(), 0, lim).metrics == {"rows": 0}
    assert (
        monitors.measure_perf(pd.DataFrame(columns=["score", "disrupted", "carrier"]), 0, lim).alarm
        is False
    )


def test_json_has_no_nan():
    out = json.loads(
        monitors.to_json({"auc": float("nan"), "s": {"x": np.float64(0.5)}, "n": np.int64(3)})
    )
    assert out == {"auc": None, "s": {"x": 0.5}, "n": 3}


# against Postgres ----------------------------------------------------------------------------


def _world(conn, ref_frame):
    """Two days of traffic (history + run 1), outcomes, a clock, a delivered feeder watermark."""
    db.apply_schema(conn)
    conn.autocommit = True
    conn.execute("INSERT INTO runs (scenario, sim_start) VALUES ('t', '2020-03-10')")
    rows = []
    fid = 0
    for day, run_id in ((D(2020, 3, 9), None), (D(2020, 3, 10), 1), (D(2020, 3, 11), 1)):
        for i, rec in enumerate(ref_frame.head(300).to_dict(orient="records")):
            fid += 1
            et = dt.datetime.combine(day, dt.time(8)) + dt.timedelta(minutes=i)
            rows.append((fid, et, run_id, json.dumps(rec), 0.2))
            conn.execute(
                "INSERT INTO outcomes VALUES (%s, %s, %s)",
                (fid, int(i % 5 == 0), et + dt.timedelta(hours=3)),
            )
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO predictions (request_id, flight_id, source, run_id, event_time, model_version, features, score)"
            " VALUES (gen_random_uuid(), %s, 'sim', %s, %s, 1, %s, %s)",
            [(f, r, et, js, sc) for f, et, r, js, sc in rows],
        )
    conn.execute(
        "INSERT INTO sim_clock (id, now, scenario, segment, run_id) VALUES (1, '2020-03-12', 't', 'x', 1)"
    )


def test_runner_upserts_once_per_day_and_waits_for_labels(db_url, ref_and_frame):
    ref, frame = ref_and_frame
    with db.connect(db_url) as conn:
        _world(conn, frame)
        assert monitors.run_monitor(conn, "drift", LG, lambda v: ref) == 2  # Mar 10 and 11
        assert monitors.run_monitor(conn, "drift", LG, lambda v: ref) == 0  # caught up
        # a retried job without its watermark recomputes, but still one row per day
        conn.execute("DELETE FROM watermarks")
        assert monitors.run_monitor(conn, "drift", LG, lambda v: ref) == 2
        n = conn.execute("SELECT count(*) FROM monitor_results WHERE monitor = 'drift'").fetchone()[
            0
        ]
        assert n == 2
        # history (Mar 9, run_id NULL) is inside the Mar 10 window
        rows = conn.execute(
            "SELECT metrics->>'rows' FROM monitor_results WHERE monitor='drift' ORDER BY window_end"
        ).fetchall()
        assert [int(r[0]) for r in rows] == [600, 900]

        # perf waits for the label feeder
        assert monitors.run_monitor(conn, "perf", LG, lambda v: ref) == 0
        db.set_watermark(conn, "label-feed", dt.datetime(2020, 3, 12))
        assert monitors.run_monitor(conn, "perf", LG, lambda v: ref) == 2


def test_a_new_run_starts_its_own_results(db_url, ref_and_frame):
    ref, frame = ref_and_frame
    with db.connect(db_url) as conn:
        _world(conn, frame)
        monitors.run_monitor(conn, "quality", LG, lambda v: ref)
        conn.execute("INSERT INTO runs (scenario, sim_start) VALUES ('t', '2020-03-10')")
        conn.execute("UPDATE sim_clock SET run_id = 2, now = '2020-03-11'")
        assert monitors.run_monitor(conn, "quality", LG, lambda v: ref) == 1
        per_run = conn.execute(
            "SELECT run_id, count(*) FROM monitor_results GROUP BY run_id ORDER BY run_id"
        ).fetchall()
    assert per_run == [(1, 2), (2, 1)]
