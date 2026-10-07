"""Do the cluster's monitors agree with the research? (Phase 2 definition of done.)

Reads one run's `monitor_results` from the cluster's Postgres (via kubectl exec) and puts it next
to the detection study's daily timelines for the same days: feature PSI (max over features),
calibration gap and Brier. Reports per-day differences, correlation, and the first sustained
alarm (K = 3 consecutive days, the controller's rule) for each signal on each side.

The two sides use different 5% samples (the backtest drew with numpy, the cluster replays a
stable hash of the flight id), so values differ by sampling noise; the alarm days should match
within a day or two.

    uv run python scripts/compare_with_backtest.py [--run RUN_ID] > runs/phase2/agreement.json
"""

from __future__ import annotations

import argparse
import io
import json
import subprocess
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from driftops.policy import Limits

REPORTS = Path("reports/detection-study")
K = 3

SQL = """
COPY (
  SELECT monitor, window_end::date AS day, alarm,
         (metrics->>'psi_max_feature')::float AS psi_max_feature,
         metrics->>'worst_feature' AS worst_feature,
         (metrics->>'calibration_gap')::float AS calibration_gap,
         (metrics->>'brier')::float AS brier,
         (metrics->>'rows')::int AS rows
  FROM monitor_results WHERE run_id = {run}
  ORDER BY monitor, window_end
) TO STDOUT WITH CSV HEADER
"""


def psql(sql: str) -> str:
    cmd = [
        "kubectl",
        "-n",
        "driftops",
        "exec",
        "driftops-postgres-0",
        "--",
        "psql",
        "-U",
        "driftops",
        "-tAc",
        sql,
    ]
    return subprocess.run(cmd, capture_output=True, text=True, check=True).stdout


def first_sustained(days: pd.Series, flags: pd.Series, k: int = K) -> str | None:
    run = 0
    for d, f in zip(days, flags, strict=True):
        run = run + 1 if f else 0
        if run >= k:
            return str(pd.Timestamp(d).date())
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", type=int, help="run id (default: the clock's run)")
    ap.add_argument("--out", default="runs/phase2")
    a = ap.parse_args()
    run = a.run or int(psql("SELECT run_id FROM sim_clock").strip())
    cluster = pd.read_csv(io.StringIO(psql(SQL.format(run=run))), parse_dates=["day"])
    # Postgres CSV writes booleans as t/f, and the string "f" is truthy
    cluster["alarm"] = cluster["alarm"].map({"t": True, "f": False, True: True, False: False})
    lim = Limits()

    drift = cluster[cluster.monitor == "drift"].set_index("day")
    perf = cluster[cluster.monitor == "perf"].set_index("day")
    quality = cluster[cluster.monitor == "quality"].set_index("day")
    days = drift.index.intersection(perf.index)

    bt_f = pd.read_csv(REPORTS / "timeline.csv", parse_dates=["day"]).set_index("day")
    bt_l = pd.read_csv(REPORTS / "labels_timeline.csv", parse_dates=["day"]).set_index("day")
    days = days.intersection(bt_f.index).intersection(bt_l.index)

    both = pd.DataFrame(
        {
            "psi_cluster": drift.loc[days, "psi_max_feature"],
            "psi_backtest": bt_f.loc[days, "psi_max_feature"],
            "gap_cluster": perf.loc[days, "calibration_gap"],
            "gap_backtest": bt_l.loc[days, "calibration_gap"],
            "brier_cluster": perf.loc[days, "brier"],
            "brier_backtest": bt_l.loc[days, "brier"],
        }
    )

    def label_flag(gap, brier):
        return (gap.abs() > lim.calibration_gap) | (brier > lim.brier)

    report = {
        "run_id": run,
        "days_compared": len(both),
        "from": str(days.min().date()) if len(days) else None,
        "to": str(days.max().date()) if len(days) else None,
        "first_sustained_alarm": {
            "feature_drift": {
                "cluster": first_sustained(days, drift.loc[days, "alarm"]),
                "backtest": first_sustained(days, both["psi_backtest"] > lim.psi_feature),
            },
            "label_drift": {
                "cluster": first_sustained(days, perf.loc[days, "alarm"]),
                "backtest": first_sustained(
                    days, label_flag(both["gap_backtest"], both["brier_backtest"])
                ),
            },
        },
        "agreement": {
            name: {
                "correlation": round(
                    float(np.corrcoef(both[f"{name}_cluster"], both[f"{name}_backtest"])[0, 1]), 4
                ),
                "median_abs_diff": round(
                    float((both[f"{name}_cluster"] - both[f"{name}_backtest"]).abs().median()), 4
                ),
                "max_abs_diff": round(
                    float((both[f"{name}_cluster"] - both[f"{name}_backtest"]).abs().max()), 4
                ),
            }
            for name in ("psi", "gap", "brier")
        },
        "quality_alarm_days": int(quality["alarm"].sum()),
        "rows_per_window": {
            "drift_median": int(drift["rows"].median()),
            "perf_median": int(perf["rows"].median()),
        },
    }
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    both.to_csv(out / "agreement_daily.csv")
    print(json.dumps(report, indent=2))

    fig, axes = plt.subplots(2, 1, figsize=(11, 6.5), sharex=True)
    axes[0].plot(both.index, both["psi_backtest"], label="backtest", color="#94a3b8", lw=2.5)
    axes[0].plot(both.index, both["psi_cluster"], label="cluster", color="#2563eb", lw=1.4)
    axes[0].axhline(lim.psi_feature, ls="--", color="grey", lw=1)
    axes[0].set_ylabel("max feature PSI")
    axes[0].set_title(f"Cluster monitors vs. the backtest, COVID scenario (run {run})")
    axes[1].plot(both.index, both["gap_backtest"], label="backtest", color="#94a3b8", lw=2.5)
    axes[1].plot(both.index, both["gap_cluster"], label="cluster", color="#dc2626", lw=1.4)
    for y in (lim.calibration_gap, -lim.calibration_gap):
        axes[1].axhline(y, ls="--", color="grey", lw=1)
    axes[1].set_ylabel("calibration gap")
    for ax in axes:
        ax.legend(fontsize=8)
        ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(out / "agreement.png", dpi=130)


if __name__ == "__main__":
    main()
