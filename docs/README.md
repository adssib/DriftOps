# DriftOps: Documentation

Start here. The docs are split by **purpose**.

| Doc | Answers |
|---|---|
| [SPEC.md](SPEC.md) | **What** must be true: requirements, invariants, data, storage, interfaces, scope. |
| [ARCHITECTURE.md](ARCHITECTURE.md) | **How** it's shaped: the loop, components, jobs, dashboards, pacing, limits. |
| [ROADMAP.md](ROADMAP.md) | **In what order**: the phases, the cut line, the definition of done. |
| [EVAL.md](EVAL.md) | **How it's measured**: every metric the results tables use. |
| [STACK.md](STACK.md) | **With what**: every tool, layer by layer, and the phase it arrives in. |
| [OPERATIONS.md](OPERATIONS.md) | **How it's run**: health checks, SLOs, alerts, rollback, API security, logging, capacity and cost. |
| [research/](research/) | **Why this design**: the detection study and the retrain-policy backtest, with numbers. |
| [decisions/](decisions/) | **Why each choice**: Architecture Decision Records. |
| [findings/](findings/) | Things learned from other people's code (pulsar-metrics). |
| [plans/](plans/) | Implementation plans, one per phase. |

## Reading path

**ARCHITECTURE § 2 → research 01 → research 02 → SPEC.** If you read one ADR, read
[ADR-0005](decisions/0005-two-signals-and-a-data-quality-guard.md): the decision the loop is
arranged around.

## The one-paragraph version

A flight-disruption model is served on Kubernetes while a simulator replays 2020 through it. Three
monitors watch data quality, feature drift and label drift over event-time windows. A controller
retrains when drift is sustained, **but never on data that failed quality checks**, and a gate
promotes the new model only if it beats the old one on a week neither has seen. The research
showed why each piece is there: watching only features missed COVID for six weeks, and without
the quality guard the loop retrains on corrupted data and the gate lets it through.

## Conventions

- **SPEC is the contract.** If code and SPEC disagree, one is a bug: say which.
- **Every non-trivial decision gets an ADR.** A changed decision gets a new ADR; the old one is
  marked Superseded, never rewritten.
- **Present tense means it exists.** Anything not built yet is marked with its phase.
- **Diagrams are Mermaid blocks** that GitHub renders natively. **Each diagram lives in one place**:
  the system diagram in the README, the decision and sequence diagrams in ARCHITECTURE. Link to a
  diagram; never copy it.
