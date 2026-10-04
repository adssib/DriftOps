# Findings: integrating `pulsar-metrics` 0.1.3

The plan was to use [pulsar-metrics](https://github.com/Rocket-Science-Development/pulsar_metrics)
(Rocket Science's open-source drift library, Apache-2.0) as the drift engine of the
drift-monitor CronJob. Every finding below was reproduced on 2026-10-04; the commands are in
the last section.

## Packaging

| # | Finding | Effect |
|---|---|---|
| P1 | Requires Python `~3.9` exactly, `pandas<2`, `pydantic<2`, `scikit-learn<2` | Can't share an image with the rest of the stack (Python 3.12, pandas 2+). Needs its own image or a fork. |
| P2 | numpy isn't pinned, so a fresh install pulls numpy 2, which pandas 1.x was compiled against the old ABI for | `import` fails: `ValueError: numpy.dtype size changed`. Works with `numpy<2`. |
| P3 | The PyPI 0.1.3 wheel and GitHub `master` (2023-05-01) are different code | PyPI's `drift.py` imports `InvalidInput` **from `black`** (the formatter), so `black` is a runtime dependency. |
| P4 | GitHub `master` doesn't import: `drift.py` does `import constant` (absolute) instead of a relative import | `ModuleNotFoundError: No module named 'constant'` when installed from source. |

## Behaviour

| # | Finding | Effect |
|---|---|---|
| B1 | `drift_status` means opposite things for the two metric families. `DriftMetric` (wasserstein, psi, kl…) sets `status = value < threshold`, which is **True when there is no drift**. `DriftTestMetric` (ks, chi2…) sets `status = pvalue < alpha`, which is **True when there is drift**. | A dashboard or alert that reads `drift_status` uniformly is wrong for one family. Measured: Wasserstein with no shift → `True`, with a 1σ shift → `False`. |
| B2 | Drift tests use p-values at a fixed alpha | At production window sizes everything is "significant": KS flags a 0.02σ mean shift on 50k rows (p = 0.004). Effect sizes (PSI, Wasserstein) with calibrated thresholds avoid this. |
| B3 | `chi2` is `scipy.stats.chisquare` (a goodness-of-fit test) applied to raw columns | On a categorical feature it fails (`could not convert string to float: 'C'`). |
| B4 | Exceptions inside `evaluate()` are caught, **printed**, and the method returns `None` | A monitoring job can't tell "no drift" from "the metric crashed". B3 is silent this way. |
| B5 | `mape` is mapped to `mean_absolute_error` | Reported MAPE is actually MAE. |

## What DriftOps does about it

- **Runtime:** `driftops/drift.py` implements the measures the monitor needs (PSI for
  numeric, categorical and prediction drift; data-quality rates) on the modern stack, with one
  meaning for a flag: `True` = alarm. It's about 150 lines and covered by tests.
- **Compatibility:** metric names follow pulsar-metrics (`psi`, `wasserstein`), so results
  could be cross-checked against it in its own Python 3.9 image.
- **Upstream:** B1, B3, B4, B5 and P2/P4 are small, independent fixes. They are candidates for
  a PR to the project (not opened; that is the repo owner's call).

## Reproduce

```bash
uv venv -p 3.9 .venv-pulsar
VIRTUAL_ENV=.venv-pulsar uv pip install pulsar-metrics==0.1.3
.venv-pulsar/bin/python -c "import pulsar_metrics.metrics.drift"     # P2: numpy ABI error
VIRTUAL_ENV=.venv-pulsar uv pip install 'numpy<2'
git clone --depth 1 https://github.com/Rocket-Science-Development/pulsar_metrics vendor/pulsar_metrics
PYTHONPATH=vendor/pulsar_metrics .venv-pulsar/bin/python -c "import pulsar_metrics.metrics.drift"  # P4
.venv-pulsar/bin/python scripts/research/check_pulsar_metrics.py                # B1-B5
```
