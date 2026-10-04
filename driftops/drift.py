"""Drift and data-quality measures.

Effect sizes, not p-values. With thousands of rows per window, a KS test calls a 0.02-sigma
shift "significant" (docs/findings/pulsar-metrics.md shows it), so every threshold here is on
how *big* a change is: PSI for features and predictions, rates for data quality.

Reference = a fixed sample of the current champion's training data. When a new champion is
promoted its reference replaces this one, which is how drift "resolves" after a retrain.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

EPS = 1e-4
RARE = "__other__"


@dataclass
class Reference:
    """Everything drift and data-quality checks need, frozen at training time."""

    numeric_edges: dict[str, np.ndarray]
    numeric_props: dict[str, np.ndarray]
    numeric_range: dict[str, tuple[float, float]]
    categorical_props: dict[str, pd.Series]
    vocab: dict[str, set[str]]
    route_distance: dict[tuple[str, str], float]
    score_edges: np.ndarray
    score_props: np.ndarray
    null_rate: dict[str, float] = field(default_factory=dict)


def fit_reference(
    df: pd.DataFrame,
    scores: np.ndarray,
    numeric: list[str],
    categorical: list[str],
    bins: int = 10,
    top_k: int = 30,
) -> Reference:
    edges, props, ranges = {}, {}, {}
    for c in numeric:
        x = df[c].dropna().to_numpy(dtype=float)
        e = np.unique(np.quantile(x, np.linspace(0, 1, bins + 1)))
        e[0], e[-1] = -np.inf, np.inf
        edges[c] = e
        props[c] = _hist(x, e)
        ranges[c] = (float(np.quantile(x, 0.0005)), float(np.quantile(x, 0.9995)))
    cats = {}
    for c in categorical:
        cats[c] = _cat_props(df[c], _top(df[c], top_k))
    s_edges = np.unique(np.quantile(scores, np.linspace(0, 1, bins + 1)))
    s_edges[0], s_edges[-1] = -np.inf, np.inf
    routes = df.groupby(["origin", "dest"], observed=True)["distance"].median().to_dict()
    return Reference(
        numeric_edges=edges,
        numeric_props=props,
        numeric_range=ranges,
        categorical_props=cats,
        vocab={c: set(df[c].dropna().unique()) for c in categorical},
        route_distance=routes,
        score_edges=s_edges,
        score_props=_hist(scores, s_edges),
        null_rate={c: float(df[c].isna().mean()) for c in numeric + categorical},
    )


def psi(expected: np.ndarray, actual: np.ndarray) -> float:
    """Population Stability Index. ~0.1 = moderate shift, ~0.25 = large (industry rule of thumb)."""
    e = np.clip(expected, EPS, None)
    a = np.clip(actual, EPS, None)
    return float(np.sum((a - e) * np.log(a / e)))


def feature_drift(ref: Reference, cur: pd.DataFrame) -> dict[str, float]:
    """PSI per feature for one window."""
    out = {}
    for c, e in ref.numeric_edges.items():
        out[c] = psi(ref.numeric_props[c], _hist(cur[c].dropna().to_numpy(dtype=float), e))
    for c, p in ref.categorical_props.items():
        out[c] = psi(p.to_numpy(), _cat_props(cur[c], [k for k in p.index if k != RARE]).to_numpy())
    return out


def score_drift(ref: Reference, scores: np.ndarray) -> float:
    return psi(ref.score_props, _hist(scores, ref.score_edges))


def data_quality(ref: Reference, cur: pd.DataFrame) -> dict[str, float]:
    """Rates of things that mean "the pipeline broke", not "the world changed".

    - null_increase: worst rise in a column's null rate vs. training
    - out_of_range: share of numeric values outside the training 0.05%-99.95% range
    - unseen_category: share of rows with a carrier/airport the model never saw
    - route_distance_mismatch: share of rows whose distance disagrees with the known route
      distance by more than 2%. A route's distance doesn't change when the world does,
      so this catches unit bugs (miles -> km) that PSI alone would read as drift.
    """
    n = max(len(cur), 1)
    null_increase = max(
        (float(cur[c].isna().mean()) - ref.null_rate.get(c, 0.0) for c in ref.null_rate),
        default=0.0,
    )
    oor = 0
    for c, (lo, hi) in ref.numeric_range.items():
        x = cur[c]
        oor += int(((x < lo) | (x > hi)).sum())
    unseen = np.zeros(len(cur), dtype=bool)
    for c, v in ref.vocab.items():
        unseen |= ~cur[c].isin(v).to_numpy() & cur[c].notna().to_numpy()
    known = pd.Series(list(zip(cur["origin"], cur["dest"]))).map(ref.route_distance)
    rel = (cur["distance"].to_numpy() - known.to_numpy()) / known.to_numpy()
    mismatch = np.nan_to_num(np.abs(rel) > 0.02, nan=False)
    return {
        "null_increase": null_increase,
        "out_of_range": oor / (n * max(len(ref.numeric_range), 1)),
        "unseen_category": float(unseen.mean()) if len(cur) else 0.0,
        "route_distance_mismatch": float(mismatch.mean()) if len(cur) else 0.0,
    }


def _hist(x: np.ndarray, edges: np.ndarray) -> np.ndarray:
    counts, _ = np.histogram(x, bins=edges)
    total = counts.sum()
    return counts / total if total else np.zeros_like(counts, dtype=float)


def _top(s: pd.Series, k: int) -> list[str]:
    return s.value_counts().head(k).index.tolist()


def _cat_props(s: pd.Series, keep: list[str]) -> pd.Series:
    """Category shares over a fixed vocabulary; everything else (incl. unseen) is RARE."""
    v = s.astype("string").where(s.isin(keep), RARE)
    p = v.value_counts(normalize=True)
    return p.reindex(keep + [RARE], fill_value=0.0)
