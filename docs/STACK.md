# DriftOps: Tech stack

> **What we use, layer by layer, and the one-line reason.** How it's operated (health checks,
> SLOs, alerts, rollback, security, capacity and cost) is in [OPERATIONS.md](OPERATIONS.md).
> Each row names the phase it arrives in.

## Application

| Piece | Choice | Why | Phase |
|---|---|---|---|
| Language | Python 3.12, `uv` | the research code is the production code (ADR-0009) | 1 |
| Model | LightGBM 4.x | seconds to retrain on CPU; native categoricals; SHAP via `pred_contrib` (ADR-0001) | done |
| API | FastAPI + uvicorn, **one process per pod** | Kubernetes scales pods; workers inside a pod hide load from the HPA | 1 |
| Validation | Pydantic v2, strict models, `extra="forbid"` | every field typed and bounded before it reaches the model or SQL | 1 |
| DB driver | psycopg 3 + `psycopg_pool`, `COPY` for batches | parameterised queries only; bulk writes without per-row round trips | 1 |
| Simulator client | httpx (async) | ~100 req/s from one pod, connection reuse | 1 |
| Logging | `structlog` → JSON on stdout | one schema across every component; Loki-ready (ADR-0018) | 1 |
| Metrics | `prometheus-client` | RED metrics by endpoint and model version | 1 |

## Data and ML platform

| Piece | Choice | Why | Phase |
|---|---|---|---|
| Database | PostgreSQL 17, one StatefulSet | predictions ⋈ outcomes joins, slices, partitions, BRIN (ADR-0006) | 1 |
| Registry | MLflow 3.x from our image; Postgres backend, artifacts on a volume | no object store to run for a few MB (ADR-0012) | 1 |
| Drift and policy | `driftops/drift.py`, `policy.py` | the code the research ran (ADR-0008) | done |

## Infrastructure

| Piece | Choice | Why | Phase |
|---|---|---|---|
| Local cluster | k3d (k3s in Docker) | free; ships Traefik and enforces NetworkPolicies | 1 |
| Cloud cluster | AKS, one node pool, Cluster Autoscaler for load tests | managed Kubernetes on the student credit (ADR-0015) | 6 |
| IaC | Terraform (azurerm): `core` once, `session` per run | InfraChat's split; sessions are disposable | 6 |
| Packaging | **one Helm chart**, `values-dev.yaml` / `values-prod.yaml`, `values.schema.json`, `helm test` | one source of truth per environment (ADR-0017) | 1 |
| Upstream charts | helmfile, versions pinned | kube-prometheus-stack, Loki, Alloy, Argo Rollouts, KEDA | 1–8 |
| Ingress / edge | Traefik (k3d default; installed on AKS too) | same middlewares in dev and prod: rate limits, headers, auth | 1, 7 |
| TLS | cert-manager + Let's Encrypt (prod), self-signed (dev) | | 6 |
| Images | GHCR, tagged by commit SHA, never `latest` in prod | the same image runs everywhere; only values differ | 1 |
| Secrets | Kubernetes Secrets (dev); Azure Key Vault + CSI driver (prod) | nothing in the repo or the image | 1, 6 |
| Autoscaling | HPA on CPU; **KEDA** on Prometheus req/s for the server | scale on load, not on a lagging CPU proxy | 8 |
| Progressive delivery | Argo Rollouts | canary with metric analysis and automatic rollback | 4 |

## Observability

| Signal | Choice | Covers | Phase |
|---|---|---|---|
| Metrics | Prometheus (kube-prometheus-stack): node-exporter, kube-state-metrics | RED for every API, USE for nodes, Job/CronJob health | 2 |
| Database metrics | **`postgres_exporter` as a sidecar** in the Postgres pod | connections, locks, slow queries, table and index sizes | 2 |
| Logs | **Loki** (single binary) + **Grafana Alloy** DaemonSet | Job and CronJob logs outlive their pods; searchable by component, model version, decision | 2 |
| ML results | Postgres, read by Grafana's Postgres datasource | drift, quality, performance, loop events | 2 |
| Uptime | blackbox exporter probing every endpoint through the ingress | availability as users see it | 2 |
| Alerting | Alertmanager → Discord/email webhook; rules as `PrometheusRule` code | paging vs. ticket alerts | 2 |
| Dashboards | Grafana, dashboards as JSON in the repo, provisioned | reviewed and versioned like code | 2 |
| Tracing | **not yet**: OpenTelemetry → Tempo when a request crosses more than two services | today a request is one hop; logs + `request_id` suffice (ADR-0018) | — |

## Security

| Concern | Choice | Phase |
|---|---|---|
| Input | Pydantic strict models, bounds, regexes, body size limit, `extra="forbid"` | 1 |
| SQL | parameterised queries only; app role has no DDL rights | 1 |
| CORS | allow only the demo site's origin; `POST`/`GET`; no credentials | 1 |
| Containers | non-root, read-only root filesystem, no privilege escalation, Pod Security `restricted` | 1 |
| AuthN | anonymous (web form) · API keys (machines) · GitHub OIDC (operators, Grafana) | 7 |
| AuthZ | roles `predictor` / `operator`; Kubernetes RBAC least privilege per ServiceAccount | 3, 7 |
| Rate limits | Traefik per IP and per key, different per route class | 7 |
| Network | NetworkPolicies, default deny, explicit allows | 7 |
| Supply chain | Trivy image scan in CI; Dependabot | 1 |
| Mesh / mTLS | **not yet** (ADR-0016) | — |

## Developer workflow

| Piece | Choice |
|---|---|
| Lint / format | ruff |
| Tests | pytest: pure functions where failure is silent, the HTTP contract, SQL against a throwaway Postgres |
| Manifests | `helm lint`, `helm template \| kubeconform` |
| Inner loop | `make up` / `make reload` (build, `k3d image import`, `helm upgrade`) |
| CI (GitHub Actions) | ruff → pytest → helm lint + kubeconform → image build → Trivy → push to GHCR by SHA |
| CD | `CD_session_up` workflow button (AKS, 30 min), sweeper, budget alert, as in InfraChat |
| Load testing | **k6**, run as a Job inside the cluster, results to Prometheus | 8 |
