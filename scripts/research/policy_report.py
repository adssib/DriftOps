"""Compare the backtested retrain policies: one table, one chart.

uv run python scripts/research/policy_report.py
"""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

OUT = Path("reports/policy-backtest")
MAIN = [
    "never",
    "monthly_8w_nogate",
    "monthly_8w_gated",
    "monthly_52w_gated",
    "driftops_8w",
    "driftops_52w",
]
CORRUPT = ["corrupt_never", "corrupt_driftops_guard", "corrupt_driftops_noguard"]
COLORS = {
    "never": "#6b7280",
    "monthly_8w_nogate": "#d97706",
    "monthly_8w_gated": "#f59e0b",
    "monthly_52w_gated": "#a16207",
    "driftops_8w": "#2563eb",
    "driftops_52w": "#059669",
    "corrupt_never": "#6b7280",
    "corrupt_driftops_guard": "#2563eb",
    "corrupt_driftops_noguard": "#dc2626",
}


def table(names: list[str], periods: list[str]) -> str:
    rows = []
    for n in names:
        p = OUT / f"{n}.json"
        if not p.exists():
            continue
        s = json.loads(p.read_text())
        cells = [f"{s['periods'][k]['brier']:.4f}" for k in periods]
        rows.append(
            f"| {n} | {s['retrains']} | {s['promotions']} | {s['blocks']} | "
            + " | ".join(cells)
            + " |"
        )
    head = "| policy | retrains | promoted | blocked | " + " | ".join(periods) + " |"
    sep = "|---" * (4 + len(periods)) + "|"
    return "\n".join([head, sep, *rows])


def chart(names: list[str], path: Path, title: str, xlim=None) -> None:
    fig, ax = plt.subplots(figsize=(12, 5))
    for n in names:
        p = OUT / f"{n}_daily.csv"
        if not p.exists():
            continue
        d = pd.read_csv(p, parse_dates=["day"]).set_index("day")
        roll = d["sum_sq"].rolling(7).sum() / d["rows"].rolling(7).sum()
        ax.plot(roll.index, roll, label=n, color=COLORS.get(n), lw=1.4)
        s = json.loads((OUT / f"{n}.json").read_text())
        promoted = [pd.Timestamp(e["day"]) for e in s["events"] if e.get("promoted")]
        ax.scatter(promoted, roll.reindex(promoted), color=COLORS.get(n), s=18, zorder=3)
    ax.axvline(pd.Timestamp("2020-03-01"), color="grey", lw=0.8)
    if xlim:
        ax.set_xlim(*map(pd.Timestamp, xlim))
    ax.set_ylabel("Brier, 7-day (lower is better)")
    ax.set_title(title)
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=130)


def main() -> None:
    periods = [
        "2019 control",
        "2020 Jan-Feb",
        "2020 Mar-Apr shock",
        "2020 May-Jun empty skies",
        "all",
    ]
    md = ["## Policies (Brier, lower is better)", "", table(MAIN, periods), ""]
    md += [
        "## Corruption: distance in km for 2 weeks from 2019-09-03",
        "",
        table(CORRUPT, ["corruption + 8 weeks", "2019 control", "all"]),
        "",
    ]
    (OUT / "results.md").write_text("\n".join(md))
    print("\n".join(md))
    chart(MAIN, OUT / "policies.png", "Serving model quality by retrain policy (dots = promotions)")
    chart(
        CORRUPT,
        OUT / "corruption.png",
        "Two weeks of km-instead-of-miles: guard on vs off",
        xlim=("2019-08-15", "2019-12-31"),
    )


if __name__ == "__main__":
    main()
