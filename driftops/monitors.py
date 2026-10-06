"""The three monitors: data quality, feature drift, label drift (SPEC F6; ARCHITECTURE § 3).

Each runs as its own CronJob every minute and catches up **one simulated day at a time** from
its watermark (ADR-0010): a 1-minute schedule against a 10-second simulated day still yields one
result per day, the same days the backtest evaluated. Windows are the backtest's:

    quality, drift   traffic of D-6 … D            (features are known at prediction time)
    perf             traffic of D-7 … D-1, with outcomes known by the end of D

Thresholds and alarm rules come from `driftops.policy`; the measures from `driftops.drift` and
`driftops.model`. Nothing here re-implements either (SPEC invariant 1).

    python -m driftops monitor quality|drift|perf
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
import pandas as pd

from driftops import db, drift, feeder, log, policy
from driftops import features as F
from driftops.model import evaluate

WINDOW_DAYS = 7
MAX_DAYS_PER_RUN = 30  # bounds one CronJob run; the next run continues from the watermark
TOP_SLICES = 10

TRAFFIC_SQL = """
SELECT features, score, model_version, flight_id
FROM predictions
WHERE source = 'sim'
  AND (run_id = %(run_id)s OR (run_id IS NULL AND event_time < %(run_start)s))
  AND event_time >= %(start)s AND event_time < %(end)s
"""

LABELLED_SQL = """
SELECT p.score, o.disrupted, p.features->>'carrier' AS carrier, p.model_version
FROM predictions p
JOIN outcomes o ON o.flight_id = p.flight_id
WHERE p.source = 'sim'
  AND (p.run_id = %(run_id)s OR (p.run_id IS NULL AND p.event_time < %(run_start)s))
  AND p.event_time >= %(start)s AND p.event_time < %(end)s
  AND o.known_at <= %(known_by)s
"""

UPSERT_SQL = """
INSERT INTO monitor_results (run_id, monitor, window_start, window_end, model_version, metrics, alarm)
VALUES (%s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (run_id, monitor, window_end, model_version)
DO UPDATE SET metrics = EXCLUDED.metrics, alarm = EXCLUDED.alarm, created_at = now()
"""


# Which days, which windows (pure) ------------------------------------------------------------


def days_to_process(
    last_done: dt.date | None,
    now: dt.datetime,
    first_day: dt.date,
    max_days: int = MAX_DAYS_PER_RUN,
) -> list[dt.date]:
    """Complete simulated days not yet processed, oldest first.

    Day D is complete once the clock reaches D+1 00:00 (the simulator sets exactly that at the
    end of each day). Days inside a clock jump are returned too: they get an explicit empty
    result rather than being silently merged into the next window.
    """
    start = first_day if last_done is None else max(last_done + dt.timedelta(days=1), first_day)
    last_complete = now.date() - dt.timedelta(days=1)
    days = []
    d = start
    while d <= last_complete and len(days) < max_days:
        days.append(d)
        d += dt.timedelta(days=1)
    return days


def window(monitor: str, day: dt.date) -> tuple[dt.datetime, dt.datetime]:
    """[start, end) of the traffic a monitor reads for day D."""
    d = dt.datetime.combine(day, dt.time())
    if monitor == "perf":
        return d - dt.timedelta(days=WINDOW_DAYS), d
    return d - dt.timedelta(days=WINDOW_DAYS - 1), d + dt.timedelta(days=1)


# The measures (pure) -------------------------------------------------------------------------


@dataclass
class Result:
    metrics: dict
    alarm: bool
    model_version: int


def measure_quality(
    ref: drift.Reference | None, frame: pd.DataFrame, version: int, limits: policy.Limits
) -> Result:
    if frame.empty or ref is None:
        return Result({"rows": 0}, False, version)
    dq = drift.data_quality(ref, frame)
    breaches = policy.quality_breaches(dq, limits)
    return Result({"rows": len(frame), **dq, "breaches": breaches}, bool(breaches), version)


def measure_drift(
    ref: drift.Reference | None,
    frame: pd.DataFrame,
    scores: np.ndarray,
    version: int,
    limits: policy.Limits,
) -> Result:
    if frame.empty or ref is None:
        return Result({"rows": 0}, False, version)
    fd = drift.feature_drift(ref, frame)
    worst = max(fd, key=fd.get)
    alarms = policy.feature_alarms(fd[worst], worst, limits)
    metrics = {
        "rows": len(frame),
        "psi": fd,
        "psi_max_feature": fd[worst],
        "worst_feature": worst,
        "psi_score": drift.score_drift(ref, scores),  # charted, never alarmed on (seasonal)
    }
    return Result(metrics, bool(alarms), version)


def measure_perf(labelled: pd.DataFrame, version: int, limits: policy.Limits) -> Result:
    if labelled.empty:
        return Result({"rows": 0}, False, version)
    y = labelled["disrupted"].to_numpy()
    p = labelled["score"].to_numpy()
    overall = evaluate(y, p)
    alarms = policy.label_alarms(overall["calibration_gap"], overall["brier"], limits)
    slices = {}
    for carrier in labelled["carrier"].value_counts().head(TOP_SLICES).index:
        g = labelled[labelled["carrier"] == carrier]
        ev = evaluate(g["disrupted"].to_numpy(), g["score"].to_numpy())
        slices[carrier] = {k: ev[k] for k in ("rows", "brier", "calibration_gap")}
    return Result({**overall, "alarms": alarms, "slices": slices}, bool(alarms), version)


# Reading windows ------------------------------------------------------------------------------


def _params(
    run_id: int, run_start: dt.datetime, start: dt.datetime, end: dt.datetime, **extra
) -> dict:
    return {"run_id": run_id, "run_start": run_start, "start": start, "end": end, **extra}


def read_traffic(
    conn, run_id, run_start, start, end
) -> tuple[pd.DataFrame, np.ndarray, int | None]:
    """Features frame, scores, and the model version that served most of the window."""
    rows = conn.execute(TRAFFIC_SQL, _params(run_id, run_start, start, end)).fetchall()
    if not rows:
        return pd.DataFrame(), np.array([]), None
    frame = pd.DataFrame.from_records([r[0] for r in rows])
    for c in F.CATEGORICAL:
        frame[c] = frame[c].astype("string")
    for c in F.NUMERIC:
        frame[c] = pd.to_numeric(frame[c], errors="coerce")
    scores = np.array([r[1] for r in rows], dtype=float)
    versions = pd.Series([r[2] for r in rows])
    return frame, scores, int(versions.mode().iloc[0])


def read_labelled(conn, run_id, run_start, start, end, known_by) -> pd.DataFrame:
    rows = conn.execute(
        LABELLED_SQL, _params(run_id, run_start, start, end, known_by=known_by)
    ).fetchall()
    return pd.DataFrame(rows, columns=["score", "disrupted", "carrier", "model_version"])


# The runner ----------------------------------------------------------------------------------


def watermark_name(monitor: str, run_id: int) -> str:
    return f"monitor:{monitor}:run:{run_id}"


def run_monitor(
    conn,
    monitor: str,
    lg,
    reference_for: Callable[[int], drift.Reference],
    limits: policy.Limits | None = None,
    max_days: int = MAX_DAYS_PER_RUN,
) -> int:
    """One catch-up run. Returns the number of days processed."""
    limits = limits or policy.Limits()
    clock = db.sim_clock(conn)
    if clock is None or clock[1] is None:
        lg.info("monitor_idle", monitor=monitor, reason="no run yet")
        return 0
    now, run_id, _ = clock
    run_start = conn.execute("SELECT sim_start FROM runs WHERE run_id = %s", (run_id,)).fetchone()[
        0
    ]
    wm = watermark_name(monitor, run_id)
    last = db.get_watermark(conn, wm)
    days = days_to_process(last.date() if last else None, now, run_start.date(), max_days)
    done = 0
    for day in days:
        start, end = window(monitor, day)
        day_end = dt.datetime.combine(day + dt.timedelta(days=1), dt.time())
        if monitor == "perf":
            delivered = db.get_watermark(conn, feeder.WATERMARK)
            if delivered is None or delivered < day_end:
                lg.info("waiting_for_labels", sim_day=str(day), delivered=str(delivered))
                break
            labelled = read_labelled(conn, run_id, run_start, start, end, known_by=day_end)
            version = int(labelled["model_version"].mode().iloc[0]) if len(labelled) else 0
            result = measure_perf(labelled, version, limits)
        else:
            frame, scores, version = read_traffic(conn, run_id, run_start, start, end)
            ref = reference_for(version) if version else None
            if monitor == "quality":
                result = measure_quality(ref, frame, version or 0, limits)
            else:
                result = measure_drift(ref, frame, scores, version or 0, limits)
        with conn.transaction():
            conn.execute(
                UPSERT_SQL,
                (
                    run_id,
                    monitor,
                    start,
                    dt.datetime.combine(day, dt.time()),
                    result.model_version,
                    to_json(result.metrics),
                    result.alarm,
                ),
            )
            db.set_watermark(conn, wm, dt.datetime.combine(day, dt.time()))
        lg.info(
            "monitor_result",
            monitor=monitor,
            run_id=run_id,
            sim_day=str(day),
            alarm=result.alarm,
            rows=result.metrics.get("rows"),
            model_version=result.model_version,
            **_headline(monitor, result.metrics),
        )
        done += 1
    return done


def to_json(metrics: dict) -> str:
    """jsonb rejects NaN (an AUC over one class is NaN): store null instead."""

    def clean(v):
        if isinstance(v, dict):
            return {str(k): clean(x) for k, x in v.items()}
        if isinstance(v, (list, tuple)):
            return [clean(x) for x in v]
        if isinstance(v, (float, np.floating)):
            return None if np.isnan(v) else float(v)
        if isinstance(v, np.integer):
            return int(v)
        return v

    return json.dumps(clean(metrics), allow_nan=False)


def _headline(monitor: str, m: dict) -> dict:
    if monitor == "drift" and "psi_max_feature" in m:
        return {
            "psi_max_feature": round(m["psi_max_feature"], 4),
            "worst_feature": m["worst_feature"],
        }
    if monitor == "perf" and "brier" in m:
        return {"calibration_gap": round(m["calibration_gap"], 4), "brier": round(m["brier"], 4)}
    if monitor == "quality" and "breaches" in m:
        return {"breaches": m["breaches"]}
    return {}


def run(monitor: str) -> int:
    """Production wiring: references come from each version's bundle in MLflow."""
    from driftops import bundle, registry
    from driftops.config import Settings

    s = Settings.from_env()
    lg = log.setup(f"monitor-{monitor}")
    cache: dict[int, drift.Reference] = {}

    def reference_for(version: int) -> drift.Reference:
        if version not in cache:
            path = registry.download(s.model_name, version, uri=s.mlflow_uri)
            cache[version] = bundle.load_bundle(path).reference
        return cache[version]

    with db.connect(s.db_url) as conn:
        conn.autocommit = True
        run_monitor(conn, monitor, lg, reference_for)
    return 0
