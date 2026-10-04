# ADR-0009: One image, one CLI, every component a subcommand

- **Status:** Accepted
- **Date:** 2026-10-04
- **Phase:** 1

## Context

The system has a server, a simulator, a seed Job, three monitors, a controller, a retrain Job and an MLflow server. They share the feature code, the drift code and the policy code.

## Decision

One Docker image; `python -m driftops <command>` selects the component. MLflow's server runs from the same image.

## Alternatives considered

- **One image per component**: seven builds and seven chances for the feature code to diverge between serving and training.
- **Upstream MLflow image**: lacks the Postgres driver; a second image to keep in step.

## Consequences

- ✅ Training and serving cannot drift apart: they import the same `features.py`.
- ✅ One build, one `k3d image import`.
- ⚠️ The image carries every dependency (MLflow is the heavy one); the server pays for code it doesn't use.
- 🔭 If image size started to dominate session start-up time on AKS.
