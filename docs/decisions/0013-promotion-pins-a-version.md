# ADR-0013: Promotion pins a model version in the pod spec

- **Status:** Accepted
- **Date:** 2026-10-04
- **Phase:** 1

## Context

Something has to decide which model a pod serves, and someone has to be able to tell afterwards.

## Decision

The server reads `DRIFTOPS_MODEL_VERSION` and loads exactly that registry version. Promotion changes that value (and moves the MLflow `champion` alias for humans), which rolls the pods.

## Alternatives considered

- **Server follows the `champion` alias and reloads**: no record in Kubernetes of what each pod serves; a canary can't run two versions side by side.

## Consequences

- ✅ `kubectl get` tells you what is serving; Phase 4's canary is a change of rollout strategy, not of the server.
- ⚠️ The controller's ServiceAccount needs permission to patch one Deployment.
- 🔭 If many models shared one server.
