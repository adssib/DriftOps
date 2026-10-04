# CLAUDE.md: DriftOps

Guidance for Claude Code in this repo. For the documentation map, read `docs/README.md`.

## What this project is

A closed MLOps loop on Kubernetes: serve a flight-disruption model, monitor data quality, feature
drift and label drift, retrain when drift is sustained **but never past a data-quality breach**,
gate the new model on unseen days, promote it. Built for a founding-engineer / FDE interview at an
MLOps company: **system-design reasoning must hold up to a founder's questions**, and a working
end-to-end demo matters more than polish.

- Spec: `docs/SPEC.md` · Architecture: `docs/ARCHITECTURE.md` · Research: `docs/research/` ·
  Decisions: `docs/decisions/` · Plans: `docs/plans/`

## Working agreement (IMPORTANT, overrides default behavior)

**Claude implements; Adib owns the design and the story.** So every choice needs a reason he can
defend, written down.

- **Work from the phase's plan in `docs/plans/`, one task at a time.** A task ends when its
  verification has run and its output has been seen, not when its files exist. Commit per task.
- **Stop and show at the end of every phase**: what runs, the numbers, what changed in the docs.
- **If a task turns out bigger than planned, split it and say so.**
- Ask before adding a dependency not in the plan, changing a contract in `docs/SPEC.md`, or
  pulling work forward from a later phase.

## The invariants (breaking these breaks the project's claim)

1. **The research code is the production code.** `driftops/drift.py`, `policy.py` and
   `model.py` are what the backtest ran. The cluster imports them and never re-implements a rule
   or a threshold. Changing them means re-running `scripts/research/`.
2. **Thresholds are derived, not tuned:** PSI 0.10, abs(gap) 0.158, Brier 0.326 come from the rule
   in ADR-0004. Re-run the rule, don't re-argue the values.
3. **The gate never sees training data**, and **quarantined days are never training data**.
4. **Windows are event time; the simulated clock is the only "now"** (ADR-0010).
5. **Every number in a results table comes from a committed run.**

## Stack

| Piece | Choice |
|---|---|
| Model | LightGBM 4.x, categorical features natively, SHAP via `pred_contrib` |
| Serving | FastAPI + uvicorn, model in memory, pinned version (ADR-0007, 0013) |
| Storage | Postgres 17, one instance; psycopg 3 (`COPY` for bulk) |
| Registry | MLflow 3.x server from our image; Postgres backend, artifacts on a volume (ADR-0012) |
| Cluster | k3d locally, one Helm chart; AKS for sessions (ADR-0015) |
| Python | 3.12, `uv`; pandas 3, numpy 2 |

## Commands

| Task | Command |
|---|---|
| Fetch data | `uv run python -m driftops.data 2018-01 2020-06` |
| Unit tests | `uv run pytest` |
| Lint / format | `uv run ruff check . && uv run ruff format .` |
| Detection study | `uv run python scripts/research/detection_study.py` |
| Policy backtest | `./scripts/research/policy_backtest_all.sh && uv run python scripts/research/policy_report.py` |

## Conventions

- Flat `driftops/` package; every component in `docs/ARCHITECTURE.md` maps to a module or
  subcommand with the **same name**.
- Configuration from environment variables only; no secrets in the image or the repo.
- `data/` is gitignored; `reports/` and (Phase 5) `runs/` are committed: the results are the point.
- Tests: pure functions where failure is silent (drift math, policy, feature building, scenario
  transforms) plus the HTTP contract. Infrastructure is verified by running it.

## Docs discipline

- `docs/SPEC.md` is the contract; code that disagrees with it is a bug in one of them.
- Every non-trivial decision gets an ADR (template `docs/decisions/0000`). A changed decision gets
  a new ADR; the old one is marked Superseded.
- Present tense means it exists; anything else is marked with its phase.

## Commit attribution

One logical change per commit. Code Claude writes carries:
`Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`
