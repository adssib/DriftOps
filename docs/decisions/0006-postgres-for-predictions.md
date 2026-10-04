# ADR-0006: Postgres for per-request data, Prometheus for service metrics

- **Status:** Accepted
- **Date:** 2026-10-04
- **Phase:** 1

## Context

Pulsar stores collected predictions in InfluxDB. DriftOps joins each prediction with an outcome that arrives hours later and slices by carrier and airport.

## Decision

Flights, predictions, outcomes and all monitor results live in Postgres. Prometheus holds only aggregate service metrics (latency, errors, request rate by model version), which a canary queries.

## Alternatives considered

- **InfluxDB / a time-series DB**: good at aggregates over time, awkward at the prediction ⋈ outcome join and per-slice queries.
- **Prometheus for everything**: per-request rows are high-cardinality: the wrong shape.

## Consequences

- ✅ The join and slice queries are plain SQL; one store for the monitors and the retrain Job.
- ⚠️ Two stores for Grafana to read.
- 🔭 At much higher volume: predictions move to object storage plus a query engine. The monitors only read windows, so that move is local.
