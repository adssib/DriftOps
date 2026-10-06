# Decisions

One record per non-trivial decision: context → decision → alternatives → consequences.
When a decision changes, write a new ADR and mark the old one **Superseded**; never rewrite it.
Template: [0000](0000-adr-template.md).

| ADR | Decision | Phase |
|---|---|---|
| [0001](0001-tabular-model-not-an-llm.md) | A LightGBM tabular model, not a fine-tuned LLM | Research |
| [0002](0002-historical-replay-with-known-drift-dates.md) | Replay 2018–2020 BTS data, because the drift dates are known | Research |
| [0003](0003-label-is-disrupted.md) | The label is "disrupted": late 15+ min, cancelled or diverted | Research |
| [0004](0004-effect-sizes-not-p-values.md) | Effect sizes with thresholds derived from the control year, not p-values | Research |
| [0005](0005-two-signals-and-a-data-quality-guard.md) | Retrain on feature OR label drift, never past a data-quality breach | Research |
| [0006](0006-postgres-for-predictions.md) | Postgres for per-request data, Prometheus for service metrics | 1 |
| [0007](0007-in-process-serving.md) | FastAPI with the model in memory, no serving framework | 1 |
| [0008](0008-own-drift-module.md) | Our own drift module instead of pulsar-metrics at runtime | Research |
| [0009](0009-one-image-one-cli.md) | One image, one CLI, every component a subcommand | 1 |
| [0010](0010-event-time-and-a-simulated-clock.md) | Event time, one simulated clock, day-by-day catch-up | 1 |
| [0011](0011-outcomes-for-every-flight.md) | The outcomes feed covers every flight, not only requested ones | 2 |
| [0012](0012-mlflow-artifacts-on-a-volume.md) | MLflow serves artifacts from a volume, no object store | 1 |
| [0013](0013-promotion-pins-a-version.md) | Promotion pins a model version in the pod spec | 1 |
| [0014](0014-seeded-history-for-demo-pacing.md) | Seed history, start the scenario at the shock | 1 |
| [0015](0015-k3d-first-aks-for-sessions.md) | Build and test on k3d; AKS only for 30-minute sessions | 1 |
| [0016](0016-no-service-mesh-yet.md) | No service mesh (yet) | 7 |
| [0017](0017-one-chart-values-per-environment.md) | One Helm chart, one values file per environment, helmfile for upstream charts | 1 |
| [0018](0018-loki-and-alloy-no-tracing-yet.md) | Logs in Loki via Alloy; no tracing yet | 2 |
| [0019](0019-identity-decides-source.md) | The caller's identity decides `source`, never the request body | 1 |
| [0020](0020-score-single-threaded-explain-on-request.md) | Score single-threaded; explain only on request | 1 |
