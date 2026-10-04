"""Reproduces docs/findings/pulsar-metrics.md B1-B5. Runs in the Python 3.9 venv only:

.venv-pulsar/bin/python scripts/research/check_pulsar_metrics.py
"""

import numpy as np
import pandas as pd
from pulsar_metrics.metrics.drift import DriftMetric, DriftTestMetric
from pulsar_metrics.metrics.enums import PerformanceMetricsFuncs

rng = np.random.default_rng(0)
ref = pd.DataFrame({"x": rng.normal(0, 1, 50_000)})

print("B1/B2: drift_status for distance metric vs test metric")
for name, mu in [("no shift", 0.0), ("0.02 sigma", 0.02), ("1 sigma", 1.0)]:
    cur = pd.DataFrame({"x": rng.normal(mu, 1, 50_000)})
    w = DriftMetric("wasserstein", "x").evaluate(cur, ref, threshold=0.1)
    k = DriftTestMetric("ks_2samp", "x").evaluate(cur, ref)
    print(
        f"  {name:10s} wasserstein={w.metric_value:.3f} drift_status={w.drift_status!s:5s}"
        f" | ks p={k.metric_value:.1e} drift_status={k.drift_status}"
    )

print("B3/B4: chi2 on a categorical column")
cat_ref = pd.DataFrame({"c": rng.choice(list("ABC"), 1000)})
cat_cur = pd.DataFrame({"c": rng.choice(list("ABC"), 1000)})
print("  returned:", DriftTestMetric("chi2", "c").evaluate(cat_cur, cat_ref))

print("B5: mape is computed by", PerformanceMetricsFuncs.mape.value.func.__name__)
