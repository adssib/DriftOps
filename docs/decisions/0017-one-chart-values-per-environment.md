# ADR-0017: One Helm chart, one values file per environment, helmfile for upstream charts

- **Status:** Accepted
- **Date:** 2026-10-06
- **Phase:** 1

## Context

The system runs on k3d (dev) and AKS (prod sessions), plus upstream charts (Prometheus, Loki, Argo Rollouts, KEDA).

## Decision

One chart, `deploy/helm/driftops`, with `values-dev.yaml` and `values-prod.yaml` and a `values.schema.json`. helmfile pins and installs our chart plus upstream charts per environment.

## Alternatives considered

- **A chart per environment**: two copies of every template drift apart; a fix lands in one and not the other.
- **Kustomize overlays**: fine for plain manifests; we already need Helm for the upstream charts.

## Consequences

- ✅ Dev and prod differ only in values; the same image runs in both.
- ✅ A typo in values fails at install (schema), not at runtime.
- ⚠️ helmfile is one more tool to install.
- 🔭 If environments diverged structurally (not just in values).
