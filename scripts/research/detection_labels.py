"""Detection study, part 2: does label-based monitoring see March 2020 before feature drift does?

Feature drift fired on 2020-04-10 (detection_study.py): in March airlines kept their
schedules and cancelled flights on the day, so the *inputs* barely moved while the *outcomes*
did. This replays the same 5% sample and watches what perf-monitor will watch once labels
arrive: the calibration gap (observed disrupted rate minus mean predicted probability) and
the Brier score over the trailing 7 days.

    uv run python scripts/research/detection_labels.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import lightgbm as lgb
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from driftops import features as F

sys.path.insert(0, str(Path(__file__).resolve().parent))
from detection_study import (
    K_CONSECUTIVE,
    OUT,
    SAMPLE,
    SEED,
    WINDOW_DAYS,
    first_sustained,
    load_features,
)


def main() -> None:
    df = load_features()
    is_2018 = df["flight_date"].dt.year == 2018
    vocab = F.vocabulary(df[is_2018])
    booster = lgb.Booster(model_file=str(OUT / "champion_v1.txt"))
    replay = df[~is_2018].reset_index(drop=True)
    rng = np.random.default_rng(SEED)
    sample = replay[rng.random(len(replay)) < SAMPLE].copy()
    sample["score"] = booster.predict(F.to_model_frame(sample, vocab))

    daily = sample.groupby("flight_date").agg(
        n=("score", "size"),
        y=(F.LABEL, "sum"),
        p=("score", "sum"),
    )
    sample["sqerr"] = (sample["score"] - sample[F.LABEL]) ** 2
    daily["sq"] = sample.groupby("flight_date")["sqerr"].sum()
    roll = daily.rolling(WINDOW_DAYS).sum().dropna()
    tl = pd.DataFrame(
        {
            "observed_rate": roll["y"] / roll["n"],
            "predicted_rate": roll["p"] / roll["n"],
            "brier": roll["sq"] / roll["n"],
        }
    )
    tl["calibration_gap"] = tl["observed_rate"] - tl["predicted_rate"]
    tl = tl.reset_index().rename(columns={"flight_date": "day"})

    control = tl[tl["day"] < "2020-01-01"]
    gap_thr = 1.5 * float(control["calibration_gap"].abs().max())
    brier_thr = 1.5 * float(control["brier"].max())
    tl["alarm"] = (tl["calibration_gap"].abs() > gap_thr) | (tl["brier"] > brier_thr)

    covid = tl[tl["day"] >= "2020-03-01"].reset_index(drop=True)
    i = first_sustained(covid["alarm"])
    worst_2019 = control.loc[control["calibration_gap"].abs().idxmax()]
    out = {
        "control_2019": {
            "max_abs_calibration_gap": round(float(control["calibration_gap"].abs().max()), 4),
            "worst_gap_day": str(worst_2019["day"].date()),
            "max_brier": round(float(control["brier"].max()), 4),
        },
        "thresholds": {"abs_calibration_gap": round(gap_thr, 4), "brier": round(brier_thr, 4)},
        "false_alarm_days_2019": int(
            control.shape[0] and tl.loc[tl["day"] < "2020-01-01", "alarm"].sum()
        ),
        "covid": {
            "sustained_alarm_on": None if i is None else str(covid.loc[i, "day"].date()),
            "days_after_march_1": i,
            "first_single_alarm": (
                str(covid.loc[covid["alarm"].idxmax(), "day"].date())
                if covid["alarm"].any()
                else None
            ),
        },
        "k_consecutive": K_CONSECUTIVE,
    }
    print(json.dumps(out, indent=2))
    (OUT / "labels_summary.json").write_text(json.dumps(out, indent=2))
    tl.to_csv(OUT / "labels_timeline.csv", index=False)

    fig, axes = plt.subplots(2, 1, figsize=(12, 6), sharex=True)
    axes[0].plot(tl["day"], tl["observed_rate"], label="observed disrupted rate", color="#dc2626")
    axes[0].plot(
        tl["day"], tl["predicted_rate"], label="mean predicted probability", color="#2563eb"
    )
    axes[0].legend(fontsize=8)
    axes[0].set_title("Champion v1: what labels show (7-day window)")
    axes[1].plot(tl["day"], tl["calibration_gap"], color="#7c3aed")
    axes[1].axhline(gap_thr, ls="--", color="grey", lw=1)
    axes[1].axhline(-gap_thr, ls="--", color="grey", lw=1)
    axes[1].set_ylabel("calibration gap")
    for a in axes:
        a.axvline(pd.Timestamp("2020-03-01"), color="grey", lw=0.8)
        a.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(OUT / "labels_timeline.png", dpi=130)


if __name__ == "__main__":
    main()
