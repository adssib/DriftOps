# ADR-0007: FastAPI with the model in memory, no serving framework

- **Status:** Accepted
- **Date:** 2026-10-04
- **Phase:** 1

## Context

The model is a few MB and scores in under a millisecond.

## Decision

The model server is FastAPI loading a pinned LightGBM bundle from MLflow. Rollout strategy belongs to Kubernetes (a rolling update in Phase 3, Argo Rollouts in Phase 4), not to the server.

## Alternatives considered

- **KServe / Seldon / Triton**: a control plane earns its place with many models, GPU models or request batching; here it adds moving parts for no gain.
- **Hot-swapping the model inside the pod**: no record of what a pod serves, and nothing for a canary to compare.

## Consequences

- ✅ One small image; latency is the network, not the model.
- ⚠️ Changing the model means new pods.
- 🔭 Many models per cluster, or a GPU model.
