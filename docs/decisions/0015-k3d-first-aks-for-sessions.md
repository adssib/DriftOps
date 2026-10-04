# ADR-0015: Build and test on k3d; AKS only for 30-minute sessions

- **Status:** Accepted
- **Date:** 2026-10-04
- **Phase:** 1

## Context

$100 of student credit. A one-node AKS cluster costs ~$0.20–0.30 an hour plus disks and a load balancer while it exists.

## Decision

Everything runs on k3d locally from the same Helm chart. AKS is created per session by a workflow and destroyed at expiry, as in InfraChat (core layer once, disposable session layer, sweeper, budget alert).

## Alternatives considered

- **AKS always on**: ~$130–150 a month: the credit lasts three weeks.
- **A VM with k3s**: boots in ~2 minutes instead of ~8, but isn't managed Kubernetes.

## Consequences

- ✅ Development is free; each session costs cents.
- ⚠️ AKS creation (~6–8 min) eats into a 30-minute session.
- 🔭 If AKS creation proves too slow: fall back to a k3s VM per session.
