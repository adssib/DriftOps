"""A model bundle: everything needed to serve and monitor one model version.

    <dir>/model.txt        LightGBM booster
    <dir>/vocab.json       category vocabulary the booster was trained with
    <dir>/reference.json   drift reference (a sample of its training data)
    <dir>/meta.json        name, training window, rows, feature list

The MLflow registry stores bundles as artifacts (ADR-0012); serving and the monitors load them.
"""

from __future__ import annotations

import json
from pathlib import Path

import lightgbm as lgb
import pandas as pd

from driftops import drift
from driftops import features as F
from driftops.model import Model


def save_bundle(path: str | Path, model: Model, extra_meta: dict | None = None) -> Path:
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    model.booster.save_model(str(p / "model.txt"))
    (p / "vocab.json").write_text(json.dumps(model.vocab))
    (p / "reference.json").write_text(json.dumps(drift.reference_to_dict(model.reference)))
    meta = {
        "name": model.name,
        "trained_from": str(model.trained_from.date()),
        "trained_to": str(model.trained_to.date()),
        "features": F.FEATURES,
        **(extra_meta or {}),
    }
    (p / "meta.json").write_text(json.dumps(meta, indent=2))
    return p


def load_bundle(path: str | Path) -> Model:
    p = Path(path)
    meta = json.loads((p / "meta.json").read_text())
    return Model(
        name=meta["name"],
        booster=lgb.Booster(model_file=str(p / "model.txt")),
        vocab=json.loads((p / "vocab.json").read_text()),
        reference=drift.reference_from_dict(json.loads((p / "reference.json").read_text())),
        trained_from=pd.Timestamp(meta["trained_from"]),
        trained_to=pd.Timestamp(meta["trained_to"]),
    )
