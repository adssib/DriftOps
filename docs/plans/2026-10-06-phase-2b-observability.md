# Phase 2b: Observability (ops side) — Implementation Plan

> Execute task by task, one commit per task on `phase-2-monitors`.

**Goal:** see the system: service health, the monitors' results, logs and uptime in Grafana,
with alerts as code. Everything installed by `make up`, nothing clicked together by hand.

**Architecture:** upstream charts in a `monitoring` namespace, pinned in helmfile
(kube-prometheus-stack 92.0.0, Loki 7.3.0 single binary, Alloy 1.13.0, blackbox exporter
11.19.1). The DriftOps chart ships what is DriftOps-specific: ServiceMonitors, the
`postgres_exporter` sidecar, the uptime Probe, PrometheusRules, Grafana datasources and
dashboards (sidecar-discovered by label), and a Grafana-managed DataQualityBreach alert.

**Spec:** OPERATIONS § 1–3, 6; STACK (observability); ADR-0018.

## Decisions

1. **Grafana reads ML results straight from Postgres** through a **read-only role** with a
   statement timeout: dashboards can't write, and can't hold the database with a slow query.
2. **Dashboards are generated** (`scripts/build_dashboards.py` → JSON in the chart), so five
   dashboards share one style and one set of queries instead of five hand-edited blobs.
3. **Simulated time vs. wall time.** ML panels plot simulated days (2020); service panels plot
   the last 30 minutes of wall time. Each panel says which.
4. **k3s control-plane scrapes are off** (etcd, scheduler, controller-manager, proxy run inside
   the k3s binary and aren't exposed): otherwise the default rules page on targets that can
   never be up.
5. **Grafana at `localhost:8080/grafana`**, anonymous read-only in dev; admin password generated.

## Tasks

1. Upstream values + helmfile releases (`needs:` so CRDs exist before our chart).
2. Read-only DB role; Grafana datasources (Postgres, Loki) as a labelled Secret.
3. ServiceMonitors, `postgres_exporter` sidecar, blackbox Probe, PrometheusRules.
4. Dashboards: generator + 5 dashboards.
5. Grafana-managed DataQualityBreach alert.
6. Bring-up; verify targets up, datasources healthy, dashboards return data; docs.
