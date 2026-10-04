# ADR-0010: Event time, one simulated clock, day-by-day catch-up

- **Status:** Accepted
- **Date:** 2026-10-04
- **Phase:** 1

## Context

The demo replays months in minutes, CronJobs run at most once a minute, and the backtest decided once per simulated day.

## Decision

The simulator owns `sim_clock`. Every window is in event time (scheduled departure). Each monitor and controller run processes every simulated day completed since its watermark, one day at a time, and upserts one row per day. Decisions are anchored to the day evaluated.

## Alternatives considered

- **Wall-clock windows**: results would depend on replay speed and scheduler jitter.
- **Long-running monitor loops instead of CronJobs**: finer timing, but loses the scheduled-job model the design is meant to show.

## Consequences

- ✅ Same decisions as the backtest regardless of replay speed; a clock jump between scenario segments needs no special case.
- ⚠️ Promotion lands later in simulated time than in the backtest (a tick plus the Job's run time). Measured and reported.
- 🔭 If the demo needed sub-day decisions.
