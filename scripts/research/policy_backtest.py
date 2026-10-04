"""Retrain-policy backtest: does the whole loop pay off, not just the detection?

Replays every day from 2019-01-01 to 2020-06-30 and plays the cluster: the serving model
scores the day's flights, the monitors read the trailing windows, a policy decides, a
retrain trains on labelled history, the gate compares on the newest unseen week, and a
promotion swaps the serving model from the next day.

Labels come from the outcomes feed for *all* flights (not just the ones we were asked
about), so training data never depends on which requests happened to arrive. The monitors
see only the traffic: the same 5% sample as the detection study.

    uv run python scripts/research/policy_backtest.py --policy driftops --window 8w --name driftops_8w

Policies:
  never      champion v1 forever
  monthly    retrain on the 1st of every month (optionally gated)
  driftops  retrain when the controller says so (driftops/policy.py), always gated
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

from driftops import drift, model, policy
from driftops import features as F

PARQUET = Path("data/parquet")
FEATURES = Path("data/features.parquet")
OUT = Path("reports/policy-backtest")
SEED = 7
SAMPLE = 0.05
WINDOW = 7
START, END = pd.Timestamp("2019-01-01"), pd.Timestamp("2020-06-30")
CORRUPT_FROM, CORRUPT_DAYS = pd.Timestamp("2019-09-03"), 14
WINDOWS = {"8w": (56, None), "52w": (364, 28.0)}  # (days of history, recency half-life)
PERIODS = {
    "2019 control": ("2019-01-01", "2019-12-31"),
    "2020 Jan-Feb": ("2020-01-01", "2020-02-29"),
    "2020 Mar-Apr shock": ("2020-03-01", "2020-04-30"),
    "2020 May-Jun empty skies": ("2020-05-01", "2020-06-30"),
    "all": ("2019-01-01", "2020-06-30"),
}


def load() -> pd.DataFrame:
    if FEATURES.exists():
        df = pd.read_parquet(FEATURES)
    else:
        parts = [F.build(F.load([p])) for p in sorted(PARQUET.glob("*.parquet"))]
        df = pd.concat(parts, ignore_index=True)
        for c in F.CATEGORICAL:
            df[c] = df[c].astype("category")
        df.to_parquet(FEATURES, index=False)
    return df


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--policy", choices=["never", "monthly", "driftops"], required=True)
    ap.add_argument("--window", choices=list(WINDOWS), default="8w")
    ap.add_argument("--no-gate", action="store_true", help="monthly only: promote unconditionally")
    ap.add_argument("--no-guard", action="store_true", help="driftops: ignore data quality")
    ap.add_argument("--corrupt", choices=["none", "distance_km"], default="none")
    ap.add_argument("--threads", type=int, default=3)
    ap.add_argument("--name", required=True)
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)

    t0 = time.perf_counter()
    df = load()
    if a.corrupt == "distance_km":
        # The feature pipeline converts miles to km for two weeks. Every consumer (serving,
        # monitors, the retrain Job) reads the same broken features.
        m = (df["flight_date"] >= CORRUPT_FROM) & (
            df["flight_date"] < CORRUPT_FROM + pd.Timedelta(days=CORRUPT_DAYS)
        )
        df.loc[m, "distance"] = df.loc[m, "distance"] * 1.609
    rng = np.random.default_rng(SEED)
    traffic = rng.random(len(df)) < SAMPLE
    by_day = {pd.Timestamp(k): v for k, v in df.groupby("flight_date").indices.items()}
    print(f"[{a.name}] loaded {len(df):,} rows in {time.perf_counter() - t0:.0f}s", flush=True)

    v1_booster = lgb.Booster(model_file="reports/detection-study/champion_v1.txt")
    serving = model.train(
        df[df["flight_date"].dt.year == 2018], "v1", booster=v1_booster, max_rows=10**9
    )
    ctrl = policy.Controller(guard=not a.no_guard)
    hist_days, half_life = WINDOWS[a.window]

    # traffic rows the monitors have seen, with the score the serving model gave them
    seen: dict[pd.Timestamp, pd.DataFrame] = {}
    daily, events = [], []
    version = 1
    for day in pd.date_range(START, END):
        idx = by_day.get(day, np.array([], dtype=int))
        if len(idx) == 0:
            continue
        rows = df.iloc[idx]
        p = serving.predict(rows)
        y = rows[F.LABEL].to_numpy()
        daily.append(
            {
                "day": day,
                "model": serving.name,
                "rows": len(y),
                "sum_sq": float(np.sum((p - y) ** 2)),
                **{k: v for k, v in model.evaluate(y, p).items() if k in ("auc", "brier")},
            }
        )
        t = traffic[idx]
        tr = rows[t].copy()
        for c in F.CATEGORICAL:
            tr[c] = tr[c].astype("string")
        seen[day] = tr.assign(score=p[t])

        # End of day: monitors run. Features are known for today; labels through yesterday.
        feat_win = _concat(seen, day - pd.Timedelta(days=WINDOW - 1), day)
        lab_win = _concat(seen, day - pd.Timedelta(days=WINDOW), day - pd.Timedelta(days=1))
        fd = drift.feature_drift(serving.reference, feat_win)
        lab = (
            model.evaluate(lab_win[F.LABEL].to_numpy(), lab_win["score"].to_numpy())
            if len(lab_win)
            else None
        )
        train_to = day - pd.Timedelta(days=WINDOW + 1)
        train_from = train_to - pd.Timedelta(days=hist_days - 1)
        signals = policy.Signals(
            psi_max_feature=max(fd.values()),
            worst_feature=max(fd, key=fd.get),
            calibration_gap=lab["calibration_gap"] if lab else None,
            brier=lab["brier"] if lab else None,
            data_quality=drift.data_quality(serving.reference, feat_win),
            labelled_rows=int(
                ((df["flight_date"] >= train_from) & (df["flight_date"] <= train_to)).sum()
            )
            if a.policy == "driftops"
            else 0,
        )

        if a.policy == "never":
            continue
        if a.policy == "monthly":
            if day.day != 1:
                continue
            decision = policy.Decision("retrain", "schedule")
        else:
            decision = ctrl.decide(day, signals)
            if decision.action in ("block",) or (decision.action == "wait" and decision.alarms):
                events.append(
                    {
                        "day": str(day.date()),
                        "action": decision.action,
                        "reason": decision.reason,
                        "alarms": decision.alarms,
                    }
                )
            if decision.action != "retrain":
                continue

        # Retrain on [train_from, train_to]; gate on the newest WINDOW days, which neither
        # model has seen. Quarantined (data-quality-breached) days are left out of both.
        tmask = (df["flight_date"] >= train_from) & (df["flight_date"] <= train_to)
        gmask = (df["flight_date"] > train_to) & (df["flight_date"] < day)
        for qs, qe in ctrl.quarantine:
            q = (df["flight_date"] >= qs) & (df["flight_date"] <= qe)
            tmask &= ~q
            gmask &= ~q
        t1 = time.perf_counter()
        version += 1
        challenger = model.train(
            df[tmask], f"v{version}", threads=a.threads, half_life_days=half_life
        )
        secs = time.perf_counter() - t1
        g_rows = df[gmask]
        gy = g_rows[F.LABEL].to_numpy()
        result = policy.gate(
            model.evaluate(gy, serving.predict(g_rows)),
            model.evaluate(gy, challenger.predict(g_rows)),
        )
        promote = result.passed or (a.policy == "monthly" and a.no_gate)
        events.append(
            {
                "day": str(day.date()),
                "action": "retrain",
                "reason": decision.reason,
                "alarms": decision.alarms,
                "challenger": challenger.name,
                "train_rows": int(tmask.sum()),
                "train_seconds": round(secs, 1),
                "gate": result.reason,
                "gate_champion_brier": round(result.champion["brier"], 4),
                "gate_challenger_brier": round(result.challenger["brier"], 4),
                "promoted": bool(promote),
            }
        )
        print(
            f"[{a.name}] {day.date()} {events[-1]['alarms']} → {challenger.name}: {result.reason} promoted={promote} ({secs:.0f}s)",
            flush=True,
        )
        if promote:
            serving = challenger

    d = pd.DataFrame(daily)
    d.to_csv(OUT / f"{a.name}_daily.csv", index=False)
    summary = {
        "name": a.name,
        "args": vars(a),
        "retrains": sum(e["action"] == "retrain" for e in events),
        "promotions": sum(e.get("promoted", False) for e in events),
        "blocks": sum(e["action"] == "block" for e in events),
        "periods": {},
        "events": events,
    }
    for name, (s, e) in PERIODS.items():
        w = d[(d["day"] >= s) & (d["day"] <= e)]
        summary["periods"][name] = {
            "brier": round(float(w["sum_sq"].sum() / w["rows"].sum()), 5),
            "mean_daily_auc": round(float(w["auc"].mean()), 4),
        }
    if a.corrupt != "none":
        after = d[
            (d["day"] >= CORRUPT_FROM)
            & (d["day"] < CORRUPT_FROM + pd.Timedelta(days=CORRUPT_DAYS + 56))
        ]
        summary["periods"]["corruption + 8 weeks"] = {
            "brier": round(float(after["sum_sq"].sum() / after["rows"].sum()), 5),
            "mean_daily_auc": round(float(after["auc"].mean()), 4),
        }
    (OUT / f"{a.name}.json").write_text(json.dumps(summary, indent=2, default=str))
    print(
        f"[{a.name}] done in {time.perf_counter() - t0:.0f}s:",
        json.dumps(summary["periods"]),
        flush=True,
    )


def _concat(seen: dict, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    parts = [seen[d] for d in pd.date_range(start, end) if d in seen]
    return pd.concat(parts) if parts else pd.DataFrame(columns=F.FEATURES + [F.LABEL, "score"])


if __name__ == "__main__":
    main()
