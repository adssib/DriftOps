# ADR-0003: The label is "disrupted": late 15+ min, cancelled or diverted

- **Status:** Accepted
- **Date:** 2026-10-04
- **Phase:** Research

## Context

BTS `ArrDel15` is empty for cancelled and diverted flights.

## Decision

`disrupted = ArrDel15 == 1 or Cancelled or Diverted`.

## Alternatives considered

- **`ArrDel15` only, dropping cancelled/diverted flights**: hides most of the March–April 2020 shock, which arrived as cancellations.
- **Separate late and cancelled models**: cleaner, but doubles the loop for no new lesson.

## Consequences

- ✅ Matches what a traveller asks: will my flight go wrong?
- ✅ Keeps the shock visible: disrupted rate 12% → 53%.
- ⚠️ The base rate (20.6% in 2018) mixes two mechanisms with different causes.
- 🔭 If the product question became cancellation-specific.
