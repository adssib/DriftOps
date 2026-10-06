"""Seed Postgres with the 2020 schedule and outcomes, and register champion v1.

Idempotent (a Job retry or `helm upgrade` re-runs it): a month already loaded is skipped, and v1
is registered only if the registry is empty.

- flights:        every scheduled flight with precomputed features and a stable sample bucket
- routes:         median distance and scheduled minutes per (origin, dest)
- outcomes_feed:  every flight's outcome and when it becomes known (the label feeder's source)
- outcomes:       outcomes known before --history-until, already arrived (ADR-0014)
"""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import pandas as pd

from driftops import db, log, registry
from driftops import features as F
from driftops.config import Settings
from driftops.data import months as month_range

FLIGHT_COLUMNS = [
    "flight_id",
    "flight_date",
    "carrier",
    "flight_number",
    "origin",
    "dest",
    "event_time",
    "dep_hour",
    "arr_hour",
    "distance",
    "sched_minutes",
    "origin_hour_load",
    "dest_hour_load",
    "month",
    "day_of_week",
    "days_to_holiday",
    "sample_bucket",
]


def sample_bucket(flight_id: np.ndarray) -> np.ndarray:
    """Stable hash → 0..99. The simulator replays bucket < N, so a 5% sample is the same 5%
    every run (Knuth's multiplicative hash; deterministic across processes, unlike hash())."""
    return (
        (flight_id.astype(np.uint64) * np.uint64(2654435761)) % np.uint64(2**32) % np.uint64(100)
    ).astype(np.int16)


def prepare_month(raw: pd.DataFrame, year: int, month: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Pure: raw BTS rows → (flights, outcomes) frames ready to COPY."""
    f = F.build(raw)
    flight_id = np.int64(year * 100 + month) * 1_000_000 + np.arange(len(f), dtype=np.int64)
    flights = pd.DataFrame(
        {
            "flight_id": flight_id,
            "flight_date": f["flight_date"].dt.date,
            "carrier": f["carrier"],
            "flight_number": raw["Flight_Number_Reporting_Airline"].to_numpy(),
            "origin": f["origin"],
            "dest": f["dest"],
            "event_time": f["event_time"],
            **{
                c: f[c]
                for c in [
                    "dep_hour",
                    "arr_hour",
                    "distance",
                    "sched_minutes",
                    "origin_hour_load",
                    "dest_hour_load",
                    "month",
                    "day_of_week",
                    "days_to_holiday",
                ]
            },
            "sample_bucket": sample_bucket(flight_id),
        }
    )
    sched_arrival = f["event_time"] + pd.to_timedelta(f["sched_minutes"].fillna(0), unit="m")
    on_time_or_late = (raw["Cancelled"] != 1) & (raw["Diverted"] != 1) & raw["ArrDelay"].notna()
    delay = pd.to_timedelta(raw["ArrDelay"].where(on_time_or_late, 0).clip(lower=0), unit="m")
    outcomes = pd.DataFrame(
        {
            "flight_id": flight_id,
            "disrupted": f[F.LABEL].astype("int16"),
            "known_at": sched_arrival + delay,
        }
    )
    return flights, outcomes


def month_loaded(conn, year: int, month: int) -> bool:
    start = pd.Timestamp(year=year, month=month, day=1)
    end = start + pd.offsets.MonthBegin(1)
    return conn.execute(
        "SELECT EXISTS (SELECT 1 FROM flights WHERE flight_date >= %s AND flight_date < %s)",
        (start.date(), end.date()),
    ).fetchone()[0]


def seed_month(conn, path: Path, history_until: pd.Timestamp, lg) -> int:
    year, month = map(int, path.stem.split("-"))
    if month_loaded(conn, year, month):
        lg.info("month_skipped", month=path.stem, reason="already loaded")
        return 0
    flights, outcomes = prepare_month(F.load([path]), year, month)
    with conn.transaction():
        db.copy_frame(conn, "flights", flights[FLIGHT_COLUMNS])
        db.copy_frame(conn, "outcomes_feed", outcomes)
        known = outcomes[outcomes["known_at"] < history_until]
        db.copy_frame(conn, "outcomes", known)
    lg.info("month_loaded", month=path.stem, flights=len(flights), known_outcomes=len(known))
    return len(flights)


def run(months: str, history_until: str, settings: Settings | None = None) -> int:
    s = settings or Settings.from_env()
    lg = log.setup("seed")
    t0 = time.perf_counter()
    first, last = months.split(":")
    paths = [
        Path(s.seed_dir) / "parquet" / f"{y}-{m:02d}.parquet" for y, m in month_range(first, last)
    ]
    missing = [str(p) for p in paths if not p.exists()]
    if missing:
        lg.error("seed_data_missing", missing=missing)
        return 1
    cutoff = pd.Timestamp(history_until)
    with db.connect(s.db_url) as conn:
        db.apply_schema(conn)
        conn.autocommit = True
        total = sum(seed_month(conn, p, cutoff, lg) for p in paths)
        conn.execute(
            """INSERT INTO routes (origin, dest, distance, sched_minutes)
               SELECT origin, dest,
                      percentile_cont(0.5) WITHIN GROUP (ORDER BY distance),
                      percentile_cont(0.5) WITHIN GROUP (ORDER BY sched_minutes)
               FROM flights WHERE distance IS NOT NULL AND sched_minutes IS NOT NULL
               GROUP BY origin, dest
               ON CONFLICT DO NOTHING"""
        )
        conn.execute(
            """INSERT INTO sim_clock (id, now, scenario, segment) VALUES (1, %s, 'seed', 'history')
               ON CONFLICT (id) DO NOTHING""",
            (cutoff.to_pydatetime(),),
        )
    register_champion(s, lg)
    lg.info("seed_done", flights_loaded=total, seconds=round(time.perf_counter() - t0, 1))
    return 0


def register_champion(s: Settings, lg) -> None:
    registry.wait_for_version(
        s.model_name,
        0,
        uri=s.mlflow_uri,
        get=lambda: registry.client(s.mlflow_uri).search_experiments(),
        log=lg,
    )
    if registry.versions(s.model_name, uri=s.mlflow_uri):
        lg.info("champion_skipped", reason="registry already has versions")
        return
    bundle_dir = Path(s.seed_dir) / "bundles" / "v1"
    v = registry.register(bundle_dir, s.model_name, tags={"origin": "seed"}, uri=s.mlflow_uri)
    registry.set_champion(s.model_name, v, uri=s.mlflow_uri)
    lg.info("champion_registered", version=v)
