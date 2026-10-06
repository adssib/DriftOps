"""Replays 2020 against the model server on a simulated clock (SPEC F3, ADR-0010, ADR-0014).

For each simulated day of a scenario: read that day's sampled flights in departure order
(`sample_bucket < sample_pct`, the same flights every run), spread them over `seconds_per_day`
of wall time, send each as `POST /predict` with the simulator's token, and move `sim_clock`
forward with the flights. A segment's transform rewrites the payload the way a broken upstream
pipeline would; the world (the flights, their outcomes) is unchanged.

    python -m driftops simulate --scenario scenarios/covid.yaml
"""

from __future__ import annotations

import asyncio
import datetime as dt
import time
from collections import Counter as Tally
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from itertools import pairwise
from pathlib import Path

import yaml

# Transforms: a broken pipeline, not a changed world ---------------------------------------


def distance_km(p: dict) -> dict:
    """A units bug upstream: miles sent as kilometres."""
    if p.get("distance") is not None:
        p["distance"] = round(p["distance"] * 1.609, 1)
    return p


def null_load(p: dict) -> dict:
    """The schedule-feature service times out for ~30% of requests (deterministic by flight)."""
    if p.get("flight_id", 0) % 10 < 3:
        p["origin_hour_load"] = None
        p["dest_hour_load"] = None
    return p


def unseen_carrier(p: dict) -> dict:
    """A code change upstream: American arrives as "XA". Well-formed, so validation passes and the
    data-quality guard has to catch it. (A three-letter "AAL" would be stopped by validation as
    a 422 before it ever reached the model.)"""
    if p.get("carrier") == "AA":
        p["carrier"] = "XA"
    return p


TRANSFORMS: dict[str, Callable[[dict], dict]] = {
    "distance_km": distance_km,
    "null_load": null_load,
    "unseen_carrier": unseen_carrier,
}

# Scenarios ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class Segment:
    name: str
    start: dt.date
    end: dt.date  # inclusive
    transform: str | None = None


@dataclass(frozen=True)
class Scenario:
    name: str
    seconds_per_day: float
    sample_pct: int
    segments: tuple[Segment, ...]
    concurrency: int = 32

    def days(self) -> Iterator[tuple[dt.date, Segment]]:
        """Every simulated day in order. A gap between segments is a clock jump."""
        for seg in self.segments:
            d = seg.start
            while d <= seg.end:
                yield d, seg
                d += dt.timedelta(days=1)


def load_scenario(path: str | Path) -> Scenario:
    raw = yaml.safe_load(Path(path).read_text())
    segs = []
    for s in raw["segments"]:
        t = s.get("transform")
        if t is not None and t not in TRANSFORMS:
            raise ValueError(f"unknown transform {t!r}; known: {sorted(TRANSFORMS)}")
        start, end = _date(s["start"]), _date(s["end"])
        if end < start:
            raise ValueError(f"segment {s['name']}: end before start")
        segs.append(Segment(s["name"], start, end, t))
    for a, b in pairwise(segs):
        if b.start <= a.end:
            raise ValueError(f"segments {a.name} and {b.name} overlap")
    sample = int(raw.get("sample_pct", 5))
    if not 1 <= sample <= 100:
        raise ValueError("sample_pct must be 1..100")
    return Scenario(
        name=raw["name"],
        seconds_per_day=float(raw.get("seconds_per_day", 10)),
        sample_pct=sample,
        segments=tuple(segs),
        concurrency=int(raw.get("concurrency", 32)),
    )


def _date(v) -> dt.date:
    return v if isinstance(v, dt.date) else dt.date.fromisoformat(str(v))


def offsets(n: int, seconds_per_day: float) -> list[float]:
    """When, in seconds after the day starts, each of n requests is sent: evenly spread."""
    return [i * seconds_per_day / n for i in range(n)] if n else []


def payload(row: dict) -> dict:
    """A flights row as the upstream pipeline would send it."""
    et: dt.datetime = row["event_time"]
    return {
        "flight_id": row["flight_id"],
        "carrier": row["carrier"],
        "origin": row["origin"],
        "dest": row["dest"],
        "flight_date": row["flight_date"].isoformat(),
        "dep_time": et.strftime("%H:%M"),
        "distance": row["distance"],
        "sched_minutes": row["sched_minutes"],
        "origin_hour_load": row["origin_hour_load"],
        "dest_hour_load": row["dest_hour_load"],
    }


# Runner ------------------------------------------------------------------------------------


@dataclass
class Status:
    scenario: str = ""
    day: str | None = None
    segment: str | None = None
    sent: int = 0
    codes: Tally = field(default_factory=Tally)
    errors: int = 0
    done: bool = False
    started: float = field(default_factory=time.time)

    def as_dict(self) -> dict:
        return {
            "scenario": self.scenario,
            "day": self.day,
            "segment": self.segment,
            "sent": self.sent,
            "status_codes": dict(self.codes),
            "transport_errors": self.errors,
            "done": self.done,
            "uptime_s": round(time.time() - self.started),
        }


DAY_SQL = """
SELECT flight_id, flight_date, carrier, origin, dest, event_time, distance, sched_minutes,
       origin_hour_load, dest_hour_load
FROM flights
WHERE flight_date = %s AND sample_bucket < %s
ORDER BY event_time, flight_id
"""

CLOCK_SQL = """
INSERT INTO sim_clock (id, now, scenario, segment, updated_at) VALUES (1, %s, %s, %s, now())
ON CONFLICT (id) DO UPDATE SET now = EXCLUDED.now, scenario = EXCLUDED.scenario,
    segment = EXCLUDED.segment, updated_at = now()
"""


async def replay(scenario: Scenario, conn, make_client, status: Status, log) -> None:
    """Replay every day of the scenario.

    A fresh HTTP client (connection pool) per simulated day: a Kubernetes Service balances per
    TCP connection, not per request, so keep-alive connections opened while only one server pod
    was ready would pin all traffic to it forever (measured: 822m vs 9m CPU across two pods).
    Reconnecting every ~10 s spreads load over whichever pods are ready now.
    """
    sem = asyncio.Semaphore(scenario.concurrency)
    client = None

    async def send(p: dict) -> None:
        async with sem:
            try:
                r = await client.post("/predict", params={"explain": "false"}, json=p)
                status.codes[r.status_code] += 1
            except Exception as e:  # noqa: BLE001 - server restarting, network blip: count, move on
                status.errors += 1
                if status.errors % 100 == 1:
                    log.warning("send_failed", error=str(e)[:200], errors=status.errors)

    status.scenario = scenario.name
    for day, seg in scenario.days():
        status.day, status.segment = day.isoformat(), seg.name
        if client is not None:
            await client.aclose()
        client = make_client()
        rows = await asyncio.to_thread(_fetch_day, conn, day, scenario.sample_pct)
        transform = TRANSFORMS.get(seg.transform) if seg.transform else None
        t0 = time.monotonic()
        tasks, last_clock = [], 0.0
        for row, off in zip(rows, offsets(len(rows), scenario.seconds_per_day)):
            delay = t0 + off - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            p = payload(row)
            tasks.append(asyncio.create_task(send(transform(p) if transform else p)))
            status.sent += 1
            if time.monotonic() - last_clock >= 1.0:
                await asyncio.to_thread(
                    _set_clock, conn, row["event_time"], scenario.name, seg.name
                )
                last_clock = time.monotonic()
        await asyncio.gather(*tasks)
        end_of_day = dt.datetime.combine(day + dt.timedelta(days=1), dt.time())
        await asyncio.to_thread(_set_clock, conn, end_of_day, scenario.name, seg.name)
        log.info(
            "day_replayed",
            sim_day=day.isoformat(),
            segment=seg.name,
            requests=len(rows),
            codes=dict(status.codes),
            seconds=round(time.monotonic() - t0, 2),
        )
    if client is not None:
        await client.aclose()
    status.done = True
    log.info("scenario_done", scenario=scenario.name, sent=status.sent, codes=dict(status.codes))


def _fetch_day(conn, day: dt.date, sample_pct: int) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(DAY_SQL, (day, sample_pct))
        cols = [c.name for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def _set_clock(conn, now: dt.datetime, scenario: str, segment: str) -> None:
    conn.execute(CLOCK_SQL, (now, scenario, segment))


def run(scenario_path: str | None = None) -> int:
    """Production wiring: a tiny status app (/healthz, /status) with the replay as a task."""
    from contextlib import asynccontextmanager

    import httpx
    import psycopg
    import uvicorn
    from fastapi import FastAPI

    from driftops import log as logmod
    from driftops.config import Settings

    s = Settings.from_env()
    lg = logmod.setup("simulator")
    scenario = load_scenario(scenario_path or s.scenario)
    status = Status(scenario=scenario.name)

    async def main_task():
        while True:
            try:
                conn = await asyncio.to_thread(psycopg.connect, s.db_url, autocommit=True)
                break
            except Exception as e:  # noqa: BLE001 - keep waiting for the database
                lg.info("waiting_for_db", error=str(e)[:200])
                await asyncio.sleep(5)
        headers = {"Authorization": f"Bearer {s.sim_token}"} if s.sim_token else {}
        limits = httpx.Limits(
            max_connections=scenario.concurrency, max_keepalive_connections=scenario.concurrency
        )

        def make_client() -> httpx.AsyncClient:
            return httpx.AsyncClient(
                base_url=s.server_url, headers=headers, timeout=5.0, limits=limits
            )

        async with make_client() as probe:
            await _wait_ready(probe, lg)
        await replay(scenario, conn, make_client, status, lg)

    @asynccontextmanager
    async def lifespan(app):
        task = asyncio.create_task(main_task())
        yield
        task.cancel()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None)

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    @app.get("/status")
    def status_endpoint():
        return status.as_dict()

    lg.info("simulator_starting", scenario=scenario.name, days=sum(1 for _ in scenario.days()))
    uvicorn.run(app, host="0.0.0.0", port=s.port, access_log=False, log_config=None)
    return 0


async def _wait_ready(client, lg) -> None:
    while True:
        try:
            r = await client.get("/readyz")
            if r.status_code == 200:
                lg.info("server_ready", **r.json())
                return
            lg.info("waiting_for_server", status=r.status_code)
        except Exception as e:  # noqa: BLE001 - keep waiting for the server
            lg.info("waiting_for_server", error=str(e)[:200])
        await asyncio.sleep(3)
