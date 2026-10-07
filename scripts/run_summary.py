"""Summarise one run's monitor results: alarm days per monitor, as ranges, and what breached.

uv run python scripts/run_summary.py [--run RUN_ID] > runs/phase2/<scenario>.json
"""

from __future__ import annotations

import argparse
import datetime as dt
import io
import json
import subprocess
from collections import Counter

import pandas as pd

K = 3

SQL = """
COPY (
  SELECT monitor, window_end::date AS day, alarm,
         metrics->>'breaches' AS breaches, metrics->>'worst_feature' AS worst_feature,
         (metrics->>'rows')::int AS rows
  FROM monitor_results WHERE run_id = {run} ORDER BY monitor, window_end
) TO STDOUT WITH CSV HEADER
"""


def psql(sql: str) -> str:
    cmd = [
        "kubectl",
        "-n",
        "driftops",
        "exec",
        "driftops-postgres-0",
        "-c",
        "postgres",
        "--",
        "psql",
        "-U",
        "driftops",
        "-tAc",
        sql,
    ]
    return subprocess.run(cmd, capture_output=True, text=True, check=True).stdout


def ranges(days: list[dt.date]) -> list[str]:
    """[d1, d2, d3, d7] → ['d1..d3', 'd7']"""
    out, start, prev = [], None, None
    for d in sorted(days):
        if start is None:
            start = prev = d
        elif d == prev + dt.timedelta(days=1):
            prev = d
        else:
            out.append(str(start) if start == prev else f"{start}..{prev}")
            start = prev = d
    if start is not None:
        out.append(str(start) if start == prev else f"{start}..{prev}")
    return out


def first_sustained(frame: pd.DataFrame) -> str | None:
    run = 0
    for day, alarm in zip(frame["day"], frame["alarm"], strict=True):
        run = run + 1 if alarm else 0
        if run >= K:
            return str(day.date())
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", type=int)
    a = ap.parse_args()
    run = a.run or int(psql("SELECT run_id FROM sim_clock").strip())
    scenario, start = (
        psql(f"SELECT scenario, sim_start::date FROM runs WHERE run_id = {run}").strip().split("|")
    )
    df = pd.read_csv(io.StringIO(psql(SQL.format(run=run))), parse_dates=["day"])
    df["alarm"] = df["alarm"].map({"t": True, "f": False})
    out = {"run_id": run, "scenario": scenario, "sim_start": start, "monitors": {}}
    for monitor, g in df.groupby("monitor"):
        alarms = g[g["alarm"]]
        entry = {
            "days": len(g),
            "from": str(g["day"].min().date()),
            "to": str(g["day"].max().date()),
            "alarm_days": int(g["alarm"].sum()),
            "alarm_ranges": ranges([d.date() for d in alarms["day"]]),
            "first_sustained_alarm": first_sustained(g),
        }
        if monitor == "quality":
            kinds = Counter(k for b in alarms["breaches"].dropna() for k in json.loads(b))
            entry["breaches"] = dict(kinds)
        if monitor == "drift":
            entry["worst_feature_on_alarm"] = dict(Counter(alarms["worst_feature"].dropna()))
        out["monitors"][monitor] = entry
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
