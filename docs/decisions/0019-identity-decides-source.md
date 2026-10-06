# ADR-0019: The caller's identity decides `source`, never the request body

- **Status:** Accepted
- **Date:** 2026-10-06
- **Phase:** 1

## Context

Requests labelled `sim` enter the drift and performance windows that drive retraining; `user` requests never do.

## Decision

The server derives `source` from who is calling (in-cluster simulator identity → `sim`; everyone else → `user`) and rejects a `source` field in the body.

## Alternatives considered

- **Trust a `source` field in the body**: any client could label its requests `sim` and steer the retrain decision: a poisoning path.

## Consequences

- ✅ The retrain decision can only be influenced through the traffic it is meant to watch.
- ⚠️ The simulator needs an identity (in-cluster address now; an API key in Phase 7).
- 🔭 Never, while user traffic is excluded from monitoring.
