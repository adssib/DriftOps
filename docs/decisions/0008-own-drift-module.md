# ADR-0008: Our own drift module instead of pulsar-metrics at runtime

- **Status:** Accepted
- **Date:** 2026-10-04
- **Phase:** Research

## Context

pulsar-metrics 0.1.3 needs Python 3.9 and pandas<2, breaks on a fresh install (numpy 2 ABI), and `drift_status` means opposite things for distance metrics and tests; chi2 fails on categoricals; errors return `None` silently (docs/findings/pulsar-metrics.md).

## Decision

`driftops/drift.py` implements PSI (numeric, categorical, prediction) and the data-quality rates on Python 3.12, with one meaning for every flag: True = alarm. Metric names match pulsar-metrics.

## Alternatives considered

- **Use pulsar-metrics in its own Python 3.9 image**: keeps the silent-`None` and inverted-flag behaviour; a monitor that can't tell "no drift" from "crashed" is worse than none.
- **Evidently or similar**: another dependency for ~150 lines of arithmetic.

## Consequences

- ✅ Small, tested, same stack as everything else.
- ⚠️ ~150 lines to own.
- 🔭 If the monitors needed many more metrics than PSI and the quality rates.
