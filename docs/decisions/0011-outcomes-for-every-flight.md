# ADR-0011: The outcomes feed covers every flight, not only requested ones

- **Status:** Accepted
- **Date:** 2026-10-04
- **Phase:** 2

## Context

The backtest trained on labelled flights. In the cluster, the monitors only see the requests the simulator sends (a 5% sample).

## Decision

The label feeder delivers outcomes for every scheduled flight. Monitors use the traffic; retraining uses flights ⋈ outcomes.

## Alternatives considered

- **Train only on requested flights**: training data would depend on who asked; a selection bias that also shrinks data 20×.

## Consequences

- ✅ Matches the backtest exactly; mirrors reality (outcomes are published for all flights).
- ⚠️ Retraining reads ~1M rows from Postgres per run.
- 🔭 If outcomes were only observable for predictions we served.
