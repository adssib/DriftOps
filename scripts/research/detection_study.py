"""Detection study: is drift on this data detectable, and quiet when it should be?

Trains champion v1 on 2018, then replays 2019-01 → 2020-06 day by day the way the drift
CronJob will see it (rolling 7-day windows of a 5% sample) and answers four questions:

  1. Does 2019 stay quiet?                     (control: a false alarm here is a failure)
  2. Does March 2020 fire, and how fast?       (real shock, known date)
  3. How fast does a gradual synthetic shift fire?   (sensitivity curve)
  4. Do pipeline corruptions trip data quality? (fake drift must not trigger a retrain)

Writes reports/detection-study/summary.json, timeline.csv and figures.

    uv run python scripts/research/detection_study.py
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import lightgbm as lgb
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.metrics import brier_score_loss, roc_auc_score

from driftops import drift
from driftops import features as F

PARQUET = Path("data/parquet")
OUT = Path("reports/detection-study")
SEED = 7
SAMPLE = 0.05  # the simulator replays a 5% sample: ~1,000 flights per simulated day
WINDOW_DAYS = 7
K_CONSECUTIVE = 3  # alarms in a row before the controller acts
TRAIN_THREADS = 2  # what one AKS node gives the retrain Job


def load_features() -> pd.DataFrame:
    parts = []
    for p in sorted(PARQUET.glob("*.parquet")):
        parts.append(F.build(F.load([p])))
    df = pd.concat(parts, ignore_index=True)
    for c in F.CATEGORICAL:
        df[c] = df[c].astype("category")
    return df


def row_counts() -> dict[str, int]:
    out = {}
    for p in sorted(PARQUET.glob("*.parquet")):
        out[p.stem] = int(pd.read_parquet(p, columns=["Cancelled"]).shape[0])
    return out


def train(train_df: pd.DataFrame, vocab: dict) -> tuple[lgb.Booster, dict]:
    X = F.to_model_frame(train_df, vocab)
    y = train_df[F.LABEL].to_numpy()
    rng = np.random.default_rng(SEED)
    valid = rng.random(len(X)) < 0.05
    params = {
        "objective": "binary",
        "learning_rate": 0.1,
        "num_leaves": 127,
        "min_data_in_leaf": 200,
        "feature_fraction": 0.9,
        "bagging_fraction": 0.8,
        "bagging_freq": 1,
        "max_cat_to_onehot": 8,
        "cat_smooth": 20,
        "num_threads": TRAIN_THREADS,
        "seed": SEED,
        "verbose": -1,
    }
    t0 = time.perf_counter()
    dtrain = lgb.Dataset(X[~valid], y[~valid], categorical_feature=F.CATEGORICAL)
    dvalid = lgb.Dataset(X[valid], y[valid], reference=dtrain)
    booster = lgb.train(
        params,
        dtrain,
        num_boost_round=400,
        valid_sets=[dvalid],
        callbacks=[lgb.early_stopping(30, verbose=False)],
    )
    seconds = time.perf_counter() - t0
    p = booster.predict(X[valid], num_iteration=booster.best_iteration)
    info = {
        "train_rows": int((~valid).sum()),
        "train_seconds": round(seconds, 1),
        "threads": TRAIN_THREADS,
        "best_iteration": booster.best_iteration,
        "holdout_auc_2018": round(roc_auc_score(y[valid], p), 4),
        "holdout_brier_2018": round(brier_score_loss(y[valid], p), 4),
        "base_rate_2018": round(float(y.mean()), 4),
    }
    return booster, info


def predict(booster: lgb.Booster, df: pd.DataFrame, vocab: dict) -> np.ndarray:
    return booster.predict(F.to_model_frame(df, vocab), num_iteration=booster.best_iteration)


def monthly_performance(df: pd.DataFrame, scores: np.ndarray) -> list[dict]:
    rows = []
    d = df[["flight_date", F.LABEL]].assign(score=scores)
    for m, g in d.groupby(d["flight_date"].dt.to_period("M")):
        rows.append(
            {
                "month": str(m),
                "flights": len(g),
                "disrupted_rate": round(float(g[F.LABEL].mean()), 4),
                "mean_score": round(float(g["score"].mean()), 4),
                "auc": round(roc_auc_score(g[F.LABEL], g["score"]), 4),
                "brier": round(brier_score_loss(g[F.LABEL], g["score"]), 4),
            }
        )
    return rows


def timeline(ref: drift.Reference, sample: pd.DataFrame, start: str, end: str) -> pd.DataFrame:
    """One row per simulated day: drift + data quality over the trailing 7-day window."""
    rows = []
    by_day = {d: g for d, g in sample.groupby("flight_date")}
    days = pd.date_range(start, end, freq="D")
    for day in days:
        win = [
            by_day[d]
            for d in pd.date_range(day - pd.Timedelta(days=WINDOW_DAYS - 1), day)
            if d in by_day
        ]
        if not win:
            continue
        w = pd.concat(win)
        fd = drift.feature_drift(ref, w)
        dq = drift.data_quality(ref, w)
        row = {"day": day, "rows": len(w), **{f"psi_{k}": v for k, v in fd.items()}, **dq}
        row["psi_score"] = drift.score_drift(ref, w["score"].to_numpy())
        row["psi_max_feature"] = max(fd.values())
        row["worst_feature"] = max(fd, key=fd.get)
        try:
            row["auc"] = roc_auc_score(w[F.LABEL], w["score"])
        except ValueError:
            row["auc"] = np.nan
        row["disrupted_rate"] = float(w[F.LABEL].mean())
        rows.append(row)
    return pd.DataFrame(rows)


def first_sustained(flags: pd.Series, k: int = K_CONSECUTIVE) -> int | None:
    """Index of the day the k-th consecutive alarm lands, or None."""
    run = 0
    for i, f in enumerate(flags.to_numpy()):
        run = run + 1 if f else 0
        if run >= k:
            return i
    return None


def hub_shift(sample: pd.DataFrame, start: str, rate: float, hubs: list[str], rng) -> pd.DataFrame:
    """Gradual synthetic drift: from `start`, flights out of `hubs` become (1 + rate*t) times
    as likely to be in the sample, t = days since start. Models an airline consolidating
    into its hubs; nothing about the pipeline is broken."""
    s = sample.copy()
    t = (s["flight_date"] - pd.Timestamp(start)).dt.days.clip(lower=0).to_numpy()
    w = np.where(s["origin"].isin(hubs).to_numpy(), 1 + rate * t, 1.0)
    keep = rng.random(len(s)) < w / w.max()
    # keep the per-day volume of the original sample so windows stay comparable
    out = []
    for d, g in s[keep].groupby("flight_date"):
        n = int((s["flight_date"] == d).sum())
        out.append(g.sample(n=min(n, len(g)), random_state=SEED))
    return pd.concat(out)


def corrupt(sample: pd.DataFrame, start: str, days: int, kind: str, rng) -> pd.DataFrame:
    """Pipeline bugs: the world is the same, the data feeding the model is wrong."""
    s = sample.copy()
    m = (s["flight_date"] >= start) & (
        s["flight_date"] < pd.Timestamp(start) + pd.Timedelta(days=days)
    )
    if kind == "distance_km":
        s.loc[m, "distance"] = s.loc[m, "distance"] * 1.609
    elif kind == "null_load":  # the schedule-feature lookup times out for 30% of requests
        hit = m & (rng.random(len(s)) < 0.3)
        s.loc[hit, ["origin_hour_load", "dest_hour_load"]] = np.nan
    elif kind == "unseen_carrier":  # a code change upstream: "AA" arrives as "AAL"
        s["carrier"] = s["carrier"].astype("string")
        s.loc[m & (s["carrier"] == "AA"), "carrier"] = "AAL"
    return s


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    summary: dict = {"row_counts": row_counts()}
    print("rows per month:", summary["row_counts"], flush=True)

    t0 = time.perf_counter()
    df = load_features()
    print(f"features built: {len(df):,} rows in {time.perf_counter() - t0:.0f}s", flush=True)

    is_2018 = df["flight_date"].dt.year == 2018
    train_df = df[is_2018]
    vocab = F.vocabulary(train_df)
    booster, info = train(train_df, vocab)
    summary["champion_v1"] = info
    print("champion v1:", info, flush=True)
    booster.save_model(str(OUT / "champion_v1.txt"), num_iteration=booster.best_iteration)

    rng = np.random.default_rng(SEED)
    ref_rows = train_df.sample(n=50_000, random_state=SEED)
    ref = drift.fit_reference(ref_rows, predict(booster, ref_rows, vocab), F.NUMERIC, F.CATEGORICAL)

    replay = df[~is_2018].reset_index(drop=True)
    replay_scores = predict(booster, replay, vocab)
    summary["monthly_performance"] = monthly_performance(replay, replay_scores)
    for r in summary["monthly_performance"]:
        print("  ", r, flush=True)

    sample_mask = rng.random(len(replay)) < SAMPLE
    sample = replay[sample_mask].assign(score=replay_scores[sample_mask])
    for c in F.CATEGORICAL:
        sample[c] = sample[c].astype("string")

    # 1+2. The real timeline.
    tl = timeline(ref, sample, "2019-01-07", "2020-06-30")
    tl.to_csv(OUT / "timeline.csv", index=False)

    control = tl[tl["day"] < "2020-01-01"]
    summary["control_2019"] = {
        "psi_max_feature_p50": round(float(control["psi_max_feature"].median()), 4),
        "psi_max_feature_p99": round(float(control["psi_max_feature"].quantile(0.99)), 4),
        "psi_max_feature_max": round(float(control["psi_max_feature"].max()), 4),
        "worst_feature_at_max": control.loc[control["psi_max_feature"].idxmax(), "worst_feature"],
        "psi_score_max": round(float(control["psi_score"].max()), 4),
    }
    # Threshold calibrated on the control year: 1.5x the worst quiet day, never below the
    # 0.1 "moderate shift" rule of thumb. Reported so it can be challenged.
    thr_feat = max(0.1, 1.5 * float(control["psi_max_feature"].max()))
    thr_score = max(0.1, 1.5 * float(control["psi_score"].max()))
    summary["thresholds"] = {"psi_feature": round(thr_feat, 4), "psi_score": round(thr_score, 4)}

    alarm = (tl["psi_max_feature"] > thr_feat) | (tl["psi_score"] > thr_score)
    tl["alarm"] = alarm
    false_alarm_days = int(alarm[tl["day"] < "2020-01-01"].sum())
    covid = tl[tl["day"] >= "2020-03-01"].reset_index(drop=True)
    i = first_sustained(covid["alarm"])
    summary["covid"] = {
        "false_alarm_days_2019": false_alarm_days,
        "sustained_alarm_on": None if i is None else str(covid.loc[i, "day"].date()),
        "days_after_march_1": i,
        "worst_feature_then": None if i is None else covid.loc[i, "worst_feature"],
    }
    print("control:", summary["control_2019"], summary["thresholds"], flush=True)
    print("covid:", summary["covid"], flush=True)

    # 3. Sensitivity: gradual hub consolidation starting 2019-07-01.
    hubs = ["ATL", "ORD", "DFW", "DEN", "CLT"]
    sens = []
    for rate in [0.005, 0.01, 0.02, 0.05, 0.1]:
        shifted = hub_shift(sample, "2019-07-01", rate, hubs, np.random.default_rng(SEED))
        stl = timeline(ref, shifted, "2019-07-01", "2019-12-31")
        a = (stl["psi_max_feature"] > thr_feat) | (stl["psi_score"] > thr_score)
        j = first_sustained(a)
        sens.append({"rate_per_day": rate, "days_to_detect": j})
        print("  hub shift", sens[-1], flush=True)
    summary["sensitivity_hub_shift"] = sens

    # 4. Corruptions must look like broken pipelines, not drift worth retraining on.
    corr = []
    for kind in ["distance_km", "null_load", "unseen_carrier"]:
        cs = corrupt(sample, "2019-09-01", 7, kind, np.random.default_rng(SEED))
        ctl = timeline(ref, cs, "2019-09-01", "2019-09-14")
        peak = ctl.loc[ctl["psi_max_feature"].idxmax()]
        corr.append(
            {
                "kind": kind,
                "psi_max_feature_peak": round(float(peak["psi_max_feature"]), 4),
                "worst_feature": peak["worst_feature"],
                "drift_alarm": bool((ctl["psi_max_feature"] > thr_feat).any()),
                "route_distance_mismatch_peak": round(
                    float(ctl["route_distance_mismatch"].max()), 4
                ),
                "null_increase_peak": round(float(ctl["null_increase"].max()), 4),
                "unseen_category_peak": round(float(ctl["unseen_category"].max()), 4),
                "out_of_range_peak": round(float(ctl["out_of_range"].max()), 4),
            }
        )
        print("  corruption", corr[-1], flush=True)
    summary["corruptions"] = corr

    (OUT / "summary.json").write_text(json.dumps(summary, indent=2, default=str))
    plot(tl, thr_feat, thr_score, summary)
    print("wrote", OUT, flush=True)


def plot(tl: pd.DataFrame, thr_feat: float, thr_score: float, summary: dict) -> None:
    fig, axes = plt.subplots(3, 1, figsize=(12, 9), sharex=True)
    ax = axes[0]
    ax.plot(tl["day"], tl["psi_max_feature"], label="max feature PSI", color="#2563eb")
    ax.plot(tl["day"], tl["psi_score"], label="prediction PSI", color="#d97706")
    ax.axhline(thr_feat, ls="--", color="#2563eb", lw=1, label=f"feature threshold {thr_feat:.2f}")
    ax.axhline(
        thr_score, ls=":", color="#d97706", lw=1, label=f"prediction threshold {thr_score:.2f}"
    )
    ax.set_yscale("log")
    ax.set_ylabel("PSI (log)")
    ax.legend(loc="upper left", fontsize=8)
    ax.set_title("Champion v1 (trained on 2018): drift over a rolling 7-day window")
    axes[1].plot(tl["day"], tl["auc"], color="#059669")
    axes[1].set_ylabel("AUC (7-day)")
    axes[2].plot(tl["day"], tl["disrupted_rate"], color="#dc2626")
    axes[2].set_ylabel("disrupted rate")
    for a in axes:
        a.axvline(pd.Timestamp("2020-03-01"), color="grey", lw=0.8)
        a.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(OUT / "timeline.png", dpi=130)

    sens = pd.DataFrame(summary["sensitivity_hub_shift"])
    fig, ax = plt.subplots(figsize=(6, 4))
    ax.plot(sens["rate_per_day"] * 100, sens["days_to_detect"], marker="o")
    ax.set_xscale("log")
    ax.set_xlabel("hub weight growth (% per day)")
    ax.set_ylabel("days until sustained alarm")
    ax.set_title("Detection curve: gradual hub consolidation")
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(OUT / "detection_curve.png", dpi=130)


if __name__ == "__main__":
    main()
