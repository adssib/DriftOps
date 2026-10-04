# DriftOps: Spec

> **The contract.** What must be true. If the code and this file disagree, one of them is a bug;
> say which. Anything not built yet is marked with its phase.

## 1. Purpose

A flight-disruption model that runs on Kubernetes and **closes its own loop**: it notices when it
has gone stale, decides whether retraining is safe, retrains, proves the new model is better on
data neither model has seen, and rolls it out. A human is involved only when the *data* is
broken.

The question the project answers: **when should a model be retrained, and how do you know the new
one is better?** The research ([docs/research/](research/)) answered it offline with numbers;
the system makes the same decisions with the same code, live.

## 2. Requirements

| ID | Requirement | Phase |
|---|---|---|
| F1 | `POST /predict` returns the probability a flight is **disrupted** (arrives 15+ min late, is cancelled or diverted), the serving model version and the top SHAP reasons | 1 |
| F2 | Every prediction is logged (request, features, score, model version, event time). Logging **never blocks or fails** a prediction; overflow is dropped and counted | 1 |
| F3 | A simulator replays BTS flights against F1 on a **simulated clock**, following a scenario file; it can inject pipeline corruptions | 1 |
| F4 | The serving model is a **pinned version** from the MLflow registry; the version a pod serves is in its spec | 1 |
| F5 | Outcomes (labels) arrive late: a flight's outcome becomes visible once its simulated arrival time passes. Outcomes cover **every** flight, not only requested ones | 2 |
| F6 | Three monitors run on trailing **event-time** windows: data quality, feature drift, label drift (performance). Results are idempotent per window | 2 |
| F7 | Grafana shows service health, drift, data quality, model performance and the loop timeline | 2 |
| F8 | The controller retrains on sustained feature **or** label drift, **never past a data-quality breach**, with a cooldown; breached windows are quarantined | 3 |
| F9 | A retrain trains on labelled history, then the **gate** compares challenger vs. champion on the newest labelled week neither has seen; only a pass is promoted | 3 |
| F10 | Every decision (alarm, block, retrain, gate result, promotion, rollback) is a row in `loop_events` | 3 |
| F11 | Promotion goes through a canary that can roll back automatically on proxy metrics | 4 |
| F12 | Scenario runs export their events and metrics to `runs/`; results tables are generated from `runs/`, never typed | 5 |
| F13 | A 30-minute Azure session runs the whole system on AKS, created by a workflow button and destroyed on expiry | 6 |

User requests (`source=user`) are served and logged but **never** enter drift or performance
windows: a visitor clicking "Christmas Eve, ORD" twenty times must not cause a retrain.

## 3. The invariants (breaking these breaks the project's claim)

1. **The research code is the production code.** `driftops/drift.py`, `driftops/policy.py` and
   `driftops/model.py` are what the backtest ran. The cluster imports them; it never
   re-implements a threshold or a rule. A change to them re-runs the research.
2. **Thresholds are derived, not tuned on the test.** Feature PSI 0.10, abs(calibration gap)
   0.158, Brier 0.326: max(rule of thumb, 1.5 × the worst 2019 control-year value) (ADR-0004).
   Changing them means re-running the rule and the backtest.
3. **The gate never sees training data.** Training ends 8 days before the decision; the gate
   uses the 7 newest labelled days.
4. **A data-quality breach is never training data.** Quarantined days are excluded from training
   and gating (ADR-0005).
5. **Every number in a results table comes from a committed run.**

## 4. Data

**Source:** BTS Reporting Carrier On-Time Performance, monthly files, 2018-01 → 2020-06
(`python -m driftops.data 2018-01 2020-06`). ~7.2M flights in 2018, ~7.4M in 2019, ~2.5M in
2020 H1. `data/` is gitignored.

**Features** (`driftops/features.py`), all known before departure:

| Group | Features |
|---|---|
| categorical | `carrier`, `origin`, `dest` |
| numeric | `dep_hour`, `arr_hour`, `distance`, `sched_minutes`, `origin_hour_load`, `dest_hour_load` |
| calendar | `month`, `day_of_week`, `days_to_holiday` (no drift alarms on these) |

**Label:** `disrupted = ArrDel15 or Cancelled or Diverted` (ADR-0003).
**Event time:** the scheduled departure.

## 5. Storage (Postgres) — Phase 1 creates it, later phases fill it

| Table | Holds | Written by |
|---|---|---|
| `flights` | the 2020 H1 schedule with precomputed features, one row per flight | seed (1) |
| `outcomes_feed` | every flight's outcome and `known_at` (actual arrival; scheduled arrival if cancelled) | seed (1) |
| `outcomes` | outcomes that have "arrived" by simulated now | label feeder (2) |
| `predictions` | one row per request: `request_id`, `flight_id` (nullable), `source`, `event_time`, `model_version`, `features` jsonb, `score`, `latency_ms` | model server (1) |
| `monitor_results` | one row per (monitor, window end, model version): metrics jsonb, `alarm` | monitors (2) |
| `loop_events` | every controller decision and its outcome | controller, retrain Job (3) |
| `sim_clock` | the single simulated "now", scenario and segment | simulator (1) |
| `watermarks` | last processed event time per consumer | feeder, monitors (2) |

## 6. Interfaces

### CLI: one image, one entry point

```
python -m driftops <command>
  serve                 model server on :8000                      (1)
  simulate              replay a scenario against the server       (1)
  seed                  load flights + outcomes, register champion v1   (1)
  label-feed            move outcomes whose time has come           (2)
  monitor {quality,drift,perf}                                     (2)
  control               the retrain-controller tick                (3)
  retrain               train → register → gate → promote/reject   (3)
```

### HTTP

| Endpoint | Returns |
|---|---|
| `POST /predict` | `{"p_disrupted", "label", "model_version", "top_reasons": [[feature, contribution], …], "request_id"}` |
| `GET /healthz` | 200 once the process is up |
| `GET /readyz` | 200 once a model is loaded, else 503 |
| `GET /metrics` | Prometheus: request count and latency by `model_version`, `predictions_logged_total`, `predictions_dropped_total` |

`POST /predict` body: `carrier`, `origin`, `dest`, `flight_date`, `dep_time` ("HH:MM"), and
optionally `flight_id`, `source` (`sim` | `user`, default `user`) and any feature value. Missing
schedule features are filled from the `flights` table (the online feature lookup).

### Configuration

Environment variables only (12-factor): `DRIFTOPS_DB_URL`, `MLFLOW_TRACKING_URI`,
`DRIFTOPS_MODEL_VERSION`, `DRIFTOPS_SCENARIO`, `DRIFTOPS_SERVER_URL`. No secrets in the image or
the repo; the database password is a Kubernetes Secret.

## 7. Scope

**In:** one model, one region, one cluster; replayed data with known drift dates; synthetic
corruptions on real rows.

**Out:** live flight data, weather features, multi-model or multi-tenant serving, a feature
store product, GPU anything, high availability of the stateful pieces.

## 8. Ethics

BTS data is public and contains no personal data. The model predicts disruption risk for a
demo; it says so on the page and is not travel advice.
