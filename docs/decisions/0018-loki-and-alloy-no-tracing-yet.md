# ADR-0018: Logs in Loki via Alloy; no tracing yet

- **Status:** Accepted
- **Date:** 2026-10-06
- **Phase:** 2

## Context

The most important code (monitors, controller, retrain) runs as Jobs whose pods, and logs, are deleted after they finish.

## Decision

Every component logs structured JSON to stdout; Grafana Alloy ships it to Loki (single binary). Labels are low-cardinality; IDs stay in the line. No distributed tracing.

## Alternatives considered

- **kubectl logs only**: logs vanish with the Job pods; "why was v5 rejected?" becomes unanswerable.
- **Promtail**: deprecated in favour of Alloy.
- **OpenTelemetry + Tempo now**: a request is one hop; `request_id` in logs answers the same questions.

## Consequences

- ✅ Job logs outlive their pods; the loop dashboard links a decision to its logs.
- ⚠️ Another stateful service (small: single binary, 7-day retention).
- 🔭 When a request crosses more than two services: add OpenTelemetry and Tempo.
