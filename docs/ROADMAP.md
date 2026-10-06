# DriftOps: Roadmap

> The build order. **Each phase ends with a working, demoable system**; the project is never in a
> big-bang-integration state.
>
> The research came first: the [detection study](research/01-detection-study.md) and the
> [retrain-policy backtest](research/02-retrain-policy-backtest.md) settled *what* the loop should
> decide, with numbers, before any infrastructure existed. The phases below build the system
> that makes those decisions live, with the same code.

## Build order

| # | Phase | Delivers | Measure |
|---|---|---|---|
| **1** | **Serving on k3d** | one image, one CLI; Postgres (partitions, indexes), MLflow; seed (flights, outcomes, champion v1); model server: strict validation, CORS, probes, JSON logs, async prediction logging; simulator on a simulated clock; one Helm chart with dev/prod values, helmfile; CI (ruff, pytest, helm lint, kubeconform, Trivy, GHCR) | requests/s, p95 latency, predictions logged vs. dropped (target: 0 dropped) |
| **2** | **Monitors + observability** | label feeder; data-quality, feature-drift and label-drift CronJobs (day-by-day catch-up); Prometheus, Alertmanager, Grafana (5 dashboards as code); `postgres_exporter` sidecar; Loki + Alloy; blackbox uptime probes; SLO burn-rate and dead-man's-switch alerts | the COVID scenario's alarm days **match the backtest's** |
| **3** | **The loop closes** | controller CronJob; retrain Job (train → gate → register); promotion by pinning the version; `loop_events` | decisions match the backtest; time from alarm to promoted pods |
| **4** | **Canary** | Argo Rollouts 20 → 50 → 100% with proxy-metric analysis and automatic rollback | a deliberately bad model is rolled back |
| **5** | **Scenario runs + write-up** | COVID and corruption scenarios end to end; `runs/` export; results tables generated from `runs/`; recorded demo | the system-level results table in [EVAL.md](EVAL.md) |
| **6** | **30-minute AKS sessions** | Terraform core + session layers, workflow button, sweeper, budget alert, cert-manager TLS | click → live time; cost per session |
| **7** | **Customer-ready hardening** | API keys and GitHub OIDC; roles; Traefik rate limits per route class; NetworkPolicies (default deny); operator API (`/admin/rollback`, `/admin/retrain`) | a security-review checklist, every item demonstrated |
| **8** | **Performance and capacity** | k6 in-cluster (smoke, load, stress, spike, soak); KEDA on req/s; Cluster Autoscaler | per-pod req/s at the SLO; the capacity and cost table in OPERATIONS § 7 **measured**, not estimated |

**Cut line:** Phases 1–3 and 5 are the project. Phases 4, 6, 7 and 8 make it customer-ready;
a closed loop on k3d with a recorded run is already the full story. The tools for every phase are
in [STACK.md](STACK.md); how it's run is in [OPERATIONS.md](OPERATIONS.md).

## Definition of done

- [x] Research: drift on this data is detectable and quiet when it should be (with numbers).
- [x] Research: the retrain policy beats never retraining and scheduled retraining where it
      matters, and the guard prevents the failure the gate can't (with numbers).
- [x] Docs: SPEC, ARCHITECTURE, ROADMAP, EVAL and ADRs for every decision made.
- [ ] `make up` on a clean laptop brings up the whole system on k3d.
- [ ] `curl /predict` returns a probability, reasons and the serving model version.
- [ ] The COVID scenario: alarm → retrain → gate → promotion, visible on the loop dashboard,
      with the same decisions as the backtest.
- [ ] The corruption scenario: alarm → **block**, no retrain, an alert.
- [ ] Results tables generated from committed runs.
- [ ] A recorded demo (≤ 5 minutes).
- [ ] (Phase 4) A bad model is rolled back by the canary.
- [ ] (Phase 6) A 30-minute AKS session from a button.
- [ ] (Phase 7) Anonymous, API-key and operator traffic each hit their own limits; denied requests are logged.
- [ ] (Phase 8) Measured req/s per pod at the SLO; the cost table recomputed from it.

## Current status

**Research done. Phase 1 is next**; its implementation plan is in
[`docs/plans/`](plans/).
