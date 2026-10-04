# ADR-0004: Effect sizes with thresholds derived from the control year, not p-values

- **Status:** Accepted
- **Date:** 2026-10-04
- **Phase:** Research

## Context

A 7-day window holds ~7,000 sampled rows. At that size a KS test calls a 0.02-sigma shift significant (pulsar-metrics findings, B2).

## Decision

Alarms are on PSI (features) and on the calibration gap and Brier score (labels). Each threshold is max(rule of thumb, 1.5 × the worst 2019 value): feature PSI 0.10, abs(gap) 0.158, Brier 0.326.

## Alternatives considered

- **p-values at alpha 0.05**: answer "is there any change", and at this size there always is.
- **Thresholds tuned to catch COVID fastest**: fits the test; would false-alarm on the next storm season.

## Consequences

- ✅ Thresholds calibrated on January–June 2019 alone give 0 alarms in July–December and still catch COVID (2020-03-26): the rule holds out of sample.
- ⚠️ 2019's summer storms set the label thresholds, part of why COVID takes 14 days to cross them.
- 🔭 If the window length or sample rate changes: re-run the rule, don't re-argue the values.
