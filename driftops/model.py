"""Train, score and evaluate. The retrain Job and the backtest share this code."""

from __future__ import annotations

from dataclasses import dataclass

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.metrics import brier_score_loss, roc_auc_score

from driftops import drift
from driftops import features as F

PARAMS = {
    "objective": "binary",
    "learning_rate": 0.1,
    "num_leaves": 127,
    "min_data_in_leaf": 200,
    "feature_fraction": 0.9,
    "bagging_fraction": 0.8,
    "bagging_freq": 1,
    "max_cat_to_onehot": 8,
    "cat_smooth": 20,
    "verbose": -1,
}


@dataclass
class Model:
    """A trained model plus everything needed to serve and monitor it."""

    name: str
    booster: lgb.Booster
    vocab: dict[str, list[str]]
    reference: drift.Reference
    trained_from: pd.Timestamp
    trained_to: pd.Timestamp

    def predict(self, df: pd.DataFrame) -> np.ndarray:
        return self.booster.predict(F.to_model_frame(df, self.vocab))


def train(
    df: pd.DataFrame,
    name: str,
    *,
    rounds: int = 300,
    threads: int = 4,
    half_life_days: float | None = None,
    max_rows: int = 2_000_000,
    seed: int = 7,
    booster: lgb.Booster | None = None,
) -> Model:
    """Fit on `df` (features + label). Fixed rounds: no early stopping, because the only
    data newer than the training window is the gate's, and the gate must stay unseen.

    half_life_days: weight rows by recency (0.5 ** (age / half_life)) so a long window keeps
    seasonality while the newest weeks dominate.
    """
    if len(df) > max_rows:
        df = df.sample(n=max_rows, random_state=seed)
    vocab = F.vocabulary(df)
    X = F.to_model_frame(df, vocab)
    y = df[F.LABEL].to_numpy()
    w = None
    if half_life_days:
        age = (df["flight_date"].max() - df["flight_date"]).dt.days.to_numpy()
        w = 0.5 ** (age / half_life_days)
    if booster is None:
        data = lgb.Dataset(X, y, weight=w, categorical_feature=F.CATEGORICAL)
        params = {**PARAMS, "num_threads": threads, "seed": seed}
        booster = lgb.train(params, data, num_boost_round=rounds)
    ref_rows = df.sample(n=min(50_000, len(df)), random_state=seed)
    ref_frame = ref_rows.copy()
    for c in F.CATEGORICAL:
        ref_frame[c] = ref_frame[c].astype("string")
    scores = booster.predict(F.to_model_frame(ref_rows, vocab))
    reference = drift.fit_reference(ref_frame, scores, F.NUMERIC, F.CATEGORICAL)
    return Model(
        name=name,
        booster=booster,
        vocab=vocab,
        reference=reference,
        trained_from=df["flight_date"].min(),
        trained_to=df["flight_date"].max(),
    )


def evaluate(y: np.ndarray, p: np.ndarray) -> dict[str, float]:
    out = {
        "rows": len(y),
        "brier": float(brier_score_loss(y, p)),
        "observed_rate": float(np.mean(y)),
        "predicted_rate": float(np.mean(p)),
    }
    out["calibration_gap"] = out["observed_rate"] - out["predicted_rate"]
    try:
        out["auc"] = float(roc_auc_score(y, p))
    except ValueError:
        out["auc"] = float("nan")
    return out
