# ADR-0014: Seed history, start the scenario at the shock

- **Status:** Accepted
- **Date:** 2026-10-04
- **Phase:** 1

## Context

COVID detection takes ~26 simulated days and retrains come weekly: 40 minutes at 20 s per simulated day, longer than a session.

## Decision

The seed loads January 1 – March 9, 2020 as already-known history; the COVID scenario replays from March 10 at 10 s per simulated day. The corruption scenario is separate.

## Alternatives considered

- **Replay from January**: the first 10 minutes show nothing happening.
- **Replay faster**: CronJobs tick once a minute; faster replay makes their lag dominate.

## Consequences

- ✅ Detection, retrains and the gate within ~8 minutes on screen.
- ⚠️ The first monitor windows include seeded, not simulated, traffic; this is stated on the dashboard.
- 🔭 If sessions got longer.
