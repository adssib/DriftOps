"""Champion v1's bundle: the research model, packaged for the registry.

Uses the exact booster the research trained (reports/detection-study/champion_v1.txt) when it is
present, so the cluster starts from the model the numbers were measured on. Otherwise trains one
on 2018 with the research parameters.

    python -m driftops champion --out data/bundles/v1
"""

from __future__ import annotations

import time
from pathlib import Path

import lightgbm as lgb
import pandas as pd

from driftops import bundle, log
from driftops import features as F
from driftops import model as M

RESEARCH_BOOSTER = Path("reports/detection-study/champion_v1.txt")


def load_2018(parquet_dir: Path) -> pd.DataFrame:
    parts = [F.build(F.load([p])) for p in sorted(parquet_dir.glob("2018-*.parquet"))]
    if not parts:
        raise FileNotFoundError(
            f"no 2018 months in {parquet_dir}; run `python -m driftops.data 2018-01 2018-12`"
        )
    df = pd.concat(parts, ignore_index=True)
    for c in F.CATEGORICAL:
        df[c] = df[c].astype("category")
    return df


def run(out: str, parquet_dir: str = "data/parquet") -> int:
    lg = log.setup("champion")
    t0 = time.perf_counter()
    df = load_2018(Path(parquet_dir))
    if RESEARCH_BOOSTER.exists():
        booster = lgb.Booster(model_file=str(RESEARCH_BOOSTER))
        m = M.train(df, "v1", booster=booster, max_rows=10**9)
        origin = "research booster"
    else:
        m = M.train(df, "v1", rounds=400, threads=4, max_rows=10**9)
        origin = "trained here"
    bundle.save_bundle(out, m, {"version": 1, "origin": origin, "train_rows": len(df)})
    lg.info(
        "champion_built",
        out=out,
        origin=origin,
        rows=len(df),
        seconds=round(time.perf_counter() - t0, 1),
    )
    return 0
