# DriftOps: How it's measured

> Every number in this repo comes from a committed run. This file defines the numbers.

## Model quality

| Metric | Definition | Why this one |
|---|---|---|
| **Brier** | mean of (p − y)² over every flight in a period | rewards calibration and sharpness; the COVID shock is mostly a calibration failure, which AUC can't see |
| **AUC** | mean of daily ROC AUC | ranking quality, reported alongside |
| **calibration gap** | observed disrupted rate − mean predicted probability, trailing 7 days | the label-drift signal |

Periods: 2019 (control), 2020 Jan–Feb, 2020 Mar–Apr (shock), 2020 May–Jun (empty skies).
Evaluated on **every** flight, never on a random split (ADR-0005; random splits leak same-day
weather).

## Detection

| Metric | Definition |
|---|---|
| **false-alarm days** | alarm days in the 2019 control year (target 0) |
| **days to detect** | days from the drift's known start to the K-th consecutive alarm window |
| **detection curve** | days to detect vs. gradual-drift rate |
| **corruption catch** | for each corruption: did data quality breach? did the drift alarm fire? |

## The loop

| Metric | Definition |
|---|---|
| retrains, promoted, rejected, blocked | counts from `loop_events` (backtest: from the run summary) |
| Brier by period vs. never retraining | the loop's payoff |
| **cluster–backtest agreement** (Phase 2–3) | same scenario in the cluster and in the backtest: same alarm days, same retrain decisions. Disagreement is a bug in one of them |
| alarm → promoted (Phase 3) | simulated days and wall-clock seconds from the K-th alarm to promoted pods |

## Service (Phase 1+)

| Metric | Source |
|---|---|
| requests/s, p50/p95/p99 latency by model version | Prometheus |
| predictions logged vs. dropped | server counters; dropped must be 0 at demo load |

## Results so far

- Detection: [research 01](research/01-detection-study.md)
- Policies: [research 02](research/02-retrain-policy-backtest.md)
- System scenario runs: Phase 5
