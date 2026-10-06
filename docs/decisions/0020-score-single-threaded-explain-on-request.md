# ADR-0020: Score single-threaded; explain only on request

- **Status:** Accepted
- **Date:** 2026-10-06
- **Phase:** 1

## Context

On the first bring-up, ~100 req/s from the simulator saturated the server pods (1 CPU each);
the event loop stalled, liveness probes timed out, and Kubernetes restarted healthy pods.
Measured per request with champion v1: predict 14.8 ms with LightGBM's default OpenMP pool vs.
3.2 ms with one thread; SHAP contributions 14.5 ms with one thread.

## Decision

LightGBM predicts with `num_threads=1` (and `OMP_NUM_THREADS=1` in the pod). SHAP reasons are
computed only when the caller asks (`?explain=false` for machine traffic; the simulator never
asks). Lookups and scoring run in the thread pool, never on the event loop.

## Alternatives considered

- **Bigger CPU limits**: pays for idle OpenMP threads; the pool sizes itself to the node, not the pod.
- **Always explain**: 4.5x the CPU of the prediction, for an answer nobody reads on machine traffic.
- **Async-only handler doing CPU work**: blocks `/healthz`, turning load into restarts.

## Consequences

- ✅ ~8 ms of CPU per machine request; p95 ≤ 100 ms at 88 req/s on two 1-CPU pods.
- ✅ Health checks answer under load.
- ⚠️ Explained requests still cost ~22 ms; a public form with heavy traffic needs its own limits (Phase 7).
- 🔭 Revisit if one request carries many rows: batch scoring benefits from threads.
