# Phase 1: Serving on k3d — Implementation Plan

> **For agentic workers:** execute task by task (superpowers:executing-plans). Steps use checkbox
> (`- [ ]`) syntax. Each task ends when its verification has run and its output has been seen.

**Goal:** the baseline model is live on local Kubernetes: a simulator replays 2020 through it on a
simulated clock and every prediction lands in Postgres, with `make up` building the whole thing.

**Architecture:** one image, one CLI (`python -m driftops <command>`, ADR-0009). Postgres holds
the schedule, outcomes and predictions; MLflow (from our image, Postgres-backed, artifacts on a
volume, ADR-0012) holds model bundles; the server loads a **pinned** version (ADR-0013). One Helm
chart with dev/prod values installed by helmfile (ADR-0017) on k3d (ADR-0015).

**Tech stack:** Python 3.12, FastAPI, Pydantic v2, psycopg 3, MLflow 3, LightGBM, structlog,
prometheus-client, httpx; Postgres 17; k3d, Helm, helmfile, kubeconform.

**Spec:** [SPEC.md](../SPEC.md) (F1–F4, § 5 storage and indexes, § 6 interfaces),
[OPERATIONS.md](../OPERATIONS.md) (§ 1 probes, § 5 validation, § 6 logging).

## Global constraints

- The research modules (`drift.py`, `policy.py`, `model.py`, `features.py`) are imported, never
  re-implemented. Changes to them are additive only (new helpers, no behaviour change).
- Configuration from environment variables only; no secrets in the image or the repo.
- Liveness never checks dependencies; logging never blocks or fails a prediction.
- `source` is derived from the caller's identity, never from the body (ADR-0019).
- Containers run non-root with a read-only root filesystem.
- **No commits in this phase until Adib has reviewed.**

## Review focus (failure modes no happy-path test exercises)

1. **Postgres down while serving** → `/predict` still answers 200; predictions are dropped and
   counted. Test: logger with a failing sink.
2. **Log queue full** → requests don't slow down; `predictions_dropped_total` rises. Test:
   bounded-queue test.
3. **MLflow not up when the server starts** → the server retries, doesn't crash-loop. Test:
   registry loader with retries against a failing client.
4. **A client sends `"source": "sim"`** → 422, and without the simulator token the request is
   `user`. Test: app test.
5. **Seed runs twice** (helm upgrade, Job retry) → no duplicate rows, no second v1. Test:
   idempotency check in the seed's integration test.

## File map

| File | Responsibility |
|---|---|
| `driftops/__main__.py` | CLI: subcommands `serve`, `simulate`, `seed`, `champion`, `mlflow` |
| `driftops/config.py` | `Settings` from environment variables |
| `driftops/log.py` | structlog JSON setup, shared keys |
| `driftops/sql/schema.sql` | tables, daily partitions, indexes (SPEC § 5) |
| `driftops/db.py` | connection pool, `apply_schema`, `copy_rows` |
| `driftops/bundle.py` | model bundle on disk: booster, vocabulary, drift reference, metadata |
| `driftops/registry.py` | register / download bundles in MLflow, set the `champion` alias |
| `driftops/champion.py` | build champion v1's bundle from 2018 (reuses the research booster) |
| `driftops/seed.py` | load flights, routes, outcomes; register v1; idempotent |
| `driftops/serving/schemas.py` | Pydantic request/response with OPERATIONS § 5 rules |
| `driftops/serving/features.py` | request → feature row; schedule lookup with a 200 ms timeout |
| `driftops/serving/logger.py` | `PredictionLogger`: bounded queue, batch `COPY`, drop counting |
| `driftops/serving/app.py` | FastAPI app: `/predict`, `/healthz`, `/readyz`, `/metrics` |
| `driftops/simulator.py` | scenarios, sampling, transforms, pacing, `sim_clock` |
| `scenarios/*.yaml` | `covid`, `corruption` |
| `Dockerfile`, `.dockerignore`, `Makefile` | one image; `make up/down/test/lint` |
| `deploy/k3d.yaml`, `deploy/helmfile.yaml` | cluster and releases |
| `deploy/helm/driftops/` | the chart: Postgres, MLflow, seed Job, server, simulator, `helm test` |
| `.github/workflows/ci.yml` | ruff, pytest (with Postgres), helm lint + kubeconform, build, Trivy |

## Tasks

### Task 1: CLI, settings, JSON logging
- [ ] `driftops/__main__.py` dispatches subcommands; `config.Settings.from_env()`; `log.setup(component)`.
- [ ] Test: `python -m driftops --help` lists every subcommand; settings read env with defaults.

### Task 2: Schema and DB helpers
- [ ] `schema.sql`: every SPEC § 5 table; `predictions` range-partitioned by day (2020-01-01 →
      2020-07-01 plus a default partition), BRIN + partial + b-tree indexes; idempotent (`IF NOT EXISTS`).
- [ ] `db.py`: `pool(url)`, `apply_schema(conn)`, `copy_rows(conn, table, columns, rows)`.
- [ ] Test (needs `DRIFTOPS_TEST_DB_URL`, else skipped): schema applies twice; a prediction row
      lands in the right daily partition; `EXPLAIN` of a window query uses the partition pruning.

### Task 3: Model bundle and registry
- [ ] `bundle.py`: `save_bundle(dir, booster, vocab, reference, meta)`, `load_bundle(dir) -> Model`;
      `drift.reference_to_dict` / `reference_from_dict` (additive).
- [ ] `registry.py`: `register(bundle_dir, name) -> int`, `download(name, version, dest) -> Path`,
      `set_champion(name, version)`, `wait_for_version(name, version, timeout)` with retries.
- [ ] Tests: bundle round-trip gives identical predictions; register → download → load against a
      local SQLite MLflow store; `wait_for_version` retries a failing client then succeeds (review focus 3).

### Task 4: Champion v1 and seed
- [ ] `champion.py`: bundle from `reports/detection-study/champion_v1.txt` if present (the
      research model), else train on 2018 with the research parameters.
- [ ] `seed.py`: 2020-01 → 06 → `flights` (+ `sample_bucket` = stable hash % 100), `routes`,
      `outcomes_feed` (`known_at` = actual arrival; scheduled arrival if cancelled or diverted);
      outcomes before `--history-until` go straight to `outcomes` (ADR-0014); register v1 and set
      `champion`. Skips whatever already exists (review focus 5).
- [ ] Test (DB): seed a one-week fixture twice → same row counts, one model version.

### Task 5: Request schema and feature building
- [ ] `schemas.py`: `PredictRequest` (strict, `extra="forbid"`, OPERATIONS § 5 rules),
      `PredictResponse`.
- [ ] `features.py`: `ScheduleLookup` protocol (`route`, `hour_load`), `PostgresLookup` with a
      200 ms statement timeout, `build_row(req, lookup) -> (row, warnings)`; lookup failure →
      nulls + `"degraded"`.
- [ ] Tests: each validation rule; `source` in body → error; `build_row` fills from a fake lookup,
      flags unseen codes, degrades on a raising lookup.

### Task 6: Prediction logger
- [ ] `PredictionLogger(sink, maxsize, batch_size, flush_interval)`: `log(record)` never blocks;
      a thread flushes batches; sink errors drop the batch and count it.
- [ ] Tests: full queue drops and counts (review focus 2); failing sink doesn't raise and counts
      (review focus 1); batches flush by size and by time.

### Task 7: The FastAPI app
- [ ] `create_app(settings, model_loader, lookup, logger)`: lifespan loads the pinned version;
      request-ID + JSON access-log middleware; 2 KB body limit; CORS from settings; SHAP top-3;
      risk bands; Prometheus metrics (requests, latency by `model_version`, logged, dropped,
      queue depth); simulator identity via bearer token → `source="sim"`.
- [ ] Tests (TestClient, fakes): `/readyz` 503 before load, 200 after; `/predict` contract;
      422 on bad input; 413 on a big body; `source` spoof (review focus 4); CORS header.

### Task 8: Simulator
- [ ] Scenario YAML (start, end, seconds per day, sample %, segments with optional transform);
      transforms `distance_km`, `null_load`, `unseen_carrier` as pure functions; day loop reads
      the sampled flights in departure order, paces requests, updates `sim_clock`;
      `/healthz` and `/status`.
- [ ] Tests: scenario parsing and segment lookup; each transform; pacing schedule.

### Task 9: Image and local cluster
- [ ] `Dockerfile` (python:3.12-slim, libgomp1, `uv sync --frozen --no-dev`, uid 10001);
      `deploy/k3d.yaml` (port 8080 → ingress, `./data` mounted at `/data`); `Makefile`.
- [ ] Verify: image builds; `docker run driftops --help`; `k3d cluster create` works.

### Task 10: Helm chart and helmfile
- [ ] Templates: secrets (DB password, simulator token, stable across upgrades), Postgres
      StatefulSet (+ init script creating the `mlflow` database), MLflow Deployment + PVC, seed
      Job, server Deployment (2 replicas, probes, security context) + Service + Ingress,
      simulator Deployment + scenario ConfigMap, `helm test` pod.
- [ ] `values.yaml`, `values-dev.yaml`, `values-prod.yaml`, `values.schema.json`; `helmfile.yaml`.
- [ ] Verify: `helm lint`; `helm template | kubeconform -strict`.

### Task 11: Bring-up and measurement
- [ ] `make up` on a fresh cluster; `helm test`; `curl` through the ingress; simulator running.
- [ ] Measure: req/s, p95 latency, logged vs. dropped after 5 minutes of the COVID scenario.
- [ ] Docs: ROADMAP status, README "Run it", any ADR the build forced.

### Task 12: CI
- [ ] `.github/workflows/ci.yml`: ruff; pytest with a Postgres service; helm lint + kubeconform;
      docker build; Trivy (fail on critical); push to GHCR on `main` only.
