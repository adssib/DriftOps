"""Request → feature row: the online half of driftops/features.py.

The simulator plays an upstream pipeline that sends schedule features with the request; the
web form sends only what a traveller knows. Missing schedule features are filled from Postgres
(the online feature lookup). If the lookup fails or times out the prediction is still served,
with the missing features as nulls and a "degraded" warning: the data-quality monitor sees the
null rate rise, which is the "lookup times out" corruption the research tested.
"""

from __future__ import annotations

import datetime as dt
from functools import lru_cache
from typing import Protocol

import numpy as np

from driftops import features as F
from driftops.serving.schemas import PredictRequest


class ScheduleLookup(Protocol):
    def route(self, origin: str, dest: str) -> tuple[float, float] | None: ...
    def origin_load(self, day: dt.date, airport: str, hour: int) -> float | None: ...
    def dest_load(self, day: dt.date, airport: str, hour: int) -> float | None: ...


class PostgresLookup:
    """Schedule lookups over a pool whose connections carry a statement_timeout.

    The schedule is static, so answers are cached: after warm-up most requests never touch the
    database.
    """

    def __init__(self, pool):
        self.pool = pool
        self.route = lru_cache(maxsize=20_000)(self._route)
        self.origin_load = lru_cache(maxsize=200_000)(self._origin_load)
        self.dest_load = lru_cache(maxsize=200_000)(self._dest_load)

    def _one(self, sql: str, params: tuple):
        with self.pool.connection() as conn:
            return conn.execute(sql, params).fetchone()

    def _route(self, origin: str, dest: str):
        r = self._one(
            "SELECT distance, sched_minutes FROM routes WHERE origin = %s AND dest = %s",
            (origin, dest),
        )
        return (float(r[0]), float(r[1])) if r else None

    def _origin_load(self, day: dt.date, airport: str, hour: int):
        r = self._one(
            "SELECT count(*) FROM flights WHERE flight_date = %s AND origin = %s AND dep_hour = %s",
            (day, airport, hour),
        )
        return float(r[0])

    def _dest_load(self, day: dt.date, airport: str, hour: int):
        r = self._one(
            "SELECT count(*) FROM flights WHERE flight_date = %s AND dest = %s AND arr_hour = %s",
            (day, airport, hour),
        )
        return float(r[0])


def build_row(
    req: PredictRequest, lookup: ScheduleLookup | None, vocab: dict[str, list[str]]
) -> tuple[dict, list[str]]:
    """The feature row for one request, plus warnings (unseen codes, degraded lookup)."""
    warnings: list[str] = []
    h, m = map(int, req.dep_time.split(":"))
    distance, sched = req.distance, req.sched_minutes
    o_load, d_load = req.origin_hour_load, req.dest_hour_load

    degraded = False
    try:
        if (distance is None or sched is None) and lookup is not None:
            route = lookup.route(req.origin, req.dest)
            if route is None:
                warnings.append("unknown route")
            else:
                distance = distance if distance is not None else route[0]
                sched = sched if sched is not None else route[1]
        arr_hour = None if sched is None else int(((h * 60 + m + sched) // 60) % 24)
        if o_load is None and lookup is not None:
            o_load = lookup.origin_load(req.flight_date, req.origin, h)
        if d_load is None and lookup is not None and arr_hour is not None:
            d_load = lookup.dest_load(req.flight_date, req.dest, arr_hour)
    except Exception:  # noqa: BLE001 - any lookup failure degrades, never fails the request
        degraded = True
        arr_hour = None if sched is None else int(((h * 60 + m + sched) // 60) % 24)
    if degraded:
        warnings.append("degraded: schedule lookup failed")

    for name, value in (("carrier", req.carrier), ("origin", req.origin), ("dest", req.dest)):
        if value not in vocab.get(name, []):
            warnings.append(f"unseen {name}")

    row = {
        "carrier": req.carrier,
        "origin": req.origin,
        "dest": req.dest,
        "dep_hour": h,
        "arr_hour": np.nan if arr_hour is None else arr_hour,
        "distance": np.nan if distance is None else float(distance),
        "sched_minutes": np.nan if sched is None else float(sched),
        "origin_hour_load": np.nan if o_load is None else float(o_load),
        "dest_hour_load": np.nan if d_load is None else float(d_load),
        "month": req.flight_date.month,
        "day_of_week": req.flight_date.weekday(),
        "days_to_holiday": int(F.days_to_holiday([req.flight_date])[0]),
    }
    return row, warnings


def event_time(req: PredictRequest) -> dt.datetime:
    h, m = map(int, req.dep_time.split(":"))
    return dt.datetime.combine(req.flight_date, dt.time(h, m))
