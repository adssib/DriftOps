# Phase 2a: The monitors (ML side) — Implementation Plan

> **For agentic workers:** execute task by task (superpowers:executing-plans), one commit per
> task on `phase-2-monitors`. Phase 2b (Prometheus, Grafana, Loki, alerts) is a separate plan.

**Goal:** the cluster measures itself the way the research did: late-arriving outcomes, and
data-quality, feature-drift and label-drift results per simulated day, matching the backtest.

**Architecture:** four CronJobs from the one image (`label-feed`, `monitor quality|drift|perf`),
every minute, each catching up **one simulated day at a time** from its watermark (ADR-0010)
and upserting one row per day into `monitor_results`. They call `driftops/drift.py` and the
thresholds in `driftops/policy.Limits`, never their own.

**Spec:** SPEC F5, F6; ARCHITECTURE § 2–3; research 01 (the numbers to agree with).

## Decisions this plan makes

1. **Runs.** A simulator restart rewinds the clock. Without a notion of a run, predictions
   duplicate and watermarks sit in the "future". Each simulator start registers a `runs` row;
   predictions, results and watermarks carry `run_id`. Outcomes are world truth, shared by all
   runs; every label query filters `known_at <= ` the moment it represents, so a rewound clock
   can never see the future.
2. **History traffic.** The backtest's windows were full from the first day. The seed scores
   the same 5% sample for 2020-01-01 → 03-09 with v1 and stores it as `run_id = NULL`
   ("history", part of every run), so the cluster's first window on March 10 is full too
   (ADR-0014 extended).
3. **Windows (same as the backtest).** For day D: features and quality over traffic of
   D−6 … D; labels over traffic of D−7 … D−1 with outcomes known by the end of D.
4. **Ordering between jobs.** `perf` processes day D only once the label feeder's watermark has
   passed the end of D. Each job waits on the data it needs, not on a schedule.
5. **Reference.** The reference of the model version that served most of the window, loaded
   from its bundle in MLflow (cached per version in the pod).

## Review focus

1. **Simulator restarts mid-scenario** → a new run; nothing duplicated; the old run's results
   stay as they were. Test: two runs over the same days in the DB test.
2. **A CronJob runs twice for the same day** (retry, overlap) → one row per (run, monitor, day,
   version). Test: run a monitor twice.
3. **Labels not delivered yet** → `perf` waits instead of computing on partial labels. Test.
4. **The clock jumps** (a gap between scenario segments) → every day in between is processed or
   skipped explicitly, never silently merged. Test on `days_to_process`.
5. **No predictions in a window** (e.g. empty early days) → a row with `rows = 0` and no alarm,
   not a crash. Test.

## Tasks

### Task 1: Runs and history traffic
- [ ] Schema (idempotent `ALTER … IF NOT EXISTS`): `runs`; `run_id` on `predictions`,
      `monitor_results`, `loop_events`, `sim_clock`; unique key `(run_id, monitor, window_end,
      model_version)`.
- [ ] Simulator registers a run and sends `X-DriftOps-Run`; the server honours it only for the
      simulator identity.
- [ ] Seed: history traffic (v1 scores for the sample, 2020-01-01 → history cutoff), once.
- [ ] Tests: run registration; header ignored for users; history inserted once.

### Task 2: Label feeder
- [ ] `driftops/feeder.py`, `python -m driftops label-feed`: moves `outcomes_feed` rows with
      `known_at` in (watermark, sim now] into `outcomes` (`ON CONFLICT DO NOTHING`), advances
      the watermark.
- [ ] Tests (DB): delivers only what's due; idempotent; a rewound clock delivers nothing new.

### Task 3: Monitor runner and the three monitors
- [ ] `driftops/monitors.py`: `days_to_process(watermark, now, first_day)`, window bounds,
      `quality(ref, window)`, `drift(ref, window)`, `perf(window)`, alarm flags from `Limits`,
      the runner (clock → days → windows → upsert → watermark), `python -m driftops monitor X`.
- [ ] Tests: pure window/day logic; each monitor on synthetic frames; DB integration covering
      review focus 1, 2, 3, 5.

### Task 4: CronJobs in the chart
- [ ] `label-feed`, `monitor-quality`, `monitor-drift`, `monitor-perf`: every minute,
      `concurrencyPolicy: Forbid`, `startingDeadlineSeconds`, `activeDeadlineSeconds`, history
      limits, the same security context as everything else.
- [ ] Verify: kubeconform both envs; jobs run and complete on k3d.

### Task 5: Agreement with the backtest
- [ ] `scripts/compare_with_backtest.py`: the cluster's per-day PSI, gap and Brier for the run vs.
      `reports/detection-study/` for the same days; first sustained alarm (K=3) per signal.
- [ ] Run the COVID scenario end to end; commit `runs/phase2/agreement.json`; docs.
