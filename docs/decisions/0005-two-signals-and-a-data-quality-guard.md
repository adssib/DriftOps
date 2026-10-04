# ADR-0005: Retrain on feature OR label drift, never past a data-quality breach

- **Status:** Accepted
- **Date:** 2026-10-04
- **Phase:** Research

## Context

Detection study: feature drift caught COVID on 2020-04-10, label drift on 2020-03-27; schedules didn't change in March, outcomes did. A miles→km bug (PSI 0.35) and a renamed carrier code (PSI 1.82) both trip the feature-drift alarm.

## Decision

The controller retrains when either signal is sustained for K=3 windows, data quality is clean, ≥50k labelled rows exist and a 7-day cooldown has passed. A breach blocks, quarantines the breached window (excluded from training and gating) and alerts a human. Retraining uses a sliding 8-week window.

## Alternatives considered

- **Feature drift only**: blind to concept drift: six weeks late on COVID.
- **Label drift only**: late for input shifts and needs labels to exist at all.
- **Rely on the gate to catch bad retrains**: the gate judges both models on the same corrupted days; in the backtest it passed the corrupted model.

## Consequences

- ✅ Backtest: best Brier of six policies (0.1617 vs. 0.1640 never retraining), 0 retrains in 2019, −5.8% in the shock, −20% in May–June.
- ✅ Guard on: a two-week unit bug costs nothing. Guard off: +8% Brier over the next 8 weeks.
- ⚠️ A sliding window learns temporary regimes (April 2020) and must retrain again on recovery; holding the old champion is sometimes right and stays a human call.
- 🔭 If labels arrived much later (weeks), label drift would stop being the early signal.
