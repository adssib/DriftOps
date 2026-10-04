# ADR-0002: Replay 2018–2020 BTS data, because the drift dates are known

- **Status:** Accepted
- **Date:** 2026-10-04
- **Phase:** Research

## Context

Nothing in this project is live, so "new data" has to come from somewhere. A drift detector can only be *measured* against drift whose start you know.

## Decision

Champion v1 trains on 2018. 2019 is the control year (no retrain should happen); January–June 2020 contains COVID (drift from mid-March). Synthetic gradual shifts and pipeline corruptions are injected on top of real rows.

## Alternatives considered

- **Recent data (2023+)**: no known shock, so detection can't be timed, only observed.
- **Fully synthetic drift**: proves the detector finds what you planted, nothing more.

## Consequences

- ✅ Days to detect, false-alarm days and blocked retrains become numbers.
- ✅ The gradual hub-shift sweep gives a detection curve for subtle drift, since COVID alone is too easy.
- ⚠️ The data is six years old.
- ⚠️ Labels already exist; the label feeder holds them back until each flight's simulated arrival.
- 🔭 If a newer period with a well-dated shift became available.
