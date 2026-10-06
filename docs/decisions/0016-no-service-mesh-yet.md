# ADR-0016: No service mesh (yet)

- **Status:** Accepted
- **Date:** 2026-10-06
- **Phase:** 7

## Context

A mesh (Istio, Linkerd) gives mTLS between pods, traffic splitting and per-request telemetry. The cluster is one node, a request crosses one hop, and sessions last 30 minutes.

## Decision

No mesh. Traffic splitting comes from Argo Rollouts, edge policy from Traefik, pod-to-pod restriction from NetworkPolicies, telemetry from the server's own metrics and logs.

## Alternatives considered

- **Istio**: a sidecar in every pod plus a control plane: more memory than the ML system itself on one node, slower sessions, and Jobs that never finish because the sidecar keeps running.
- **Linkerd**: lighter, same structural cost for a single-hop system.

## Consequences

- ✅ Every resource goes to the system being demonstrated.
- ✅ Canary, rate limits and isolation still exist, each from the simplest tool that does it.
- ⚠️ No mTLS between pods: traffic inside the cluster is plaintext, limited by NetworkPolicies.
- 🔭 Many services calling each other, or a customer requiring mTLS / zero-trust inside the cluster.
