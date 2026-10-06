# DriftOps: Operations

> **How it's run.** Health checks, SLOs, alerts, rollback, API security, logging, and how many
> machines it needs at what cost. The tools are in [STACK.md](STACK.md).
>
> ⚠️ **Capacity and cost numbers in § 7 are estimates until Phase 8's load tests replace them.**
> They are labelled. Every measured number will come from a committed run.

## 1. Health checks: every API, every component

**Rule: liveness never checks dependencies.** If `/healthz` checked Postgres, one database blip
would make Kubernetes restart every server pod at once: an outage caused by the health check.
Liveness asks "is this process stuck?"; readiness asks "should this pod get traffic?".

| Component | Startup probe | Liveness | Readiness | Phase |
|---|---|---|---|---|
| model server | `/readyz`, up to 120 s (model download) | `/healthz`: process responds | `/readyz`: model loaded **and** log queue < 90% full. Not the DB: predictions still work if logging degrades | 1 |
| simulator | — | `/healthz` | — (receives no traffic) | 1 |
| Postgres | `pg_isready` | `pg_isready` | `pg_isready` | 1 |
| MLflow | `/health` | `/health` | `/health` | 1 |
| Prometheus, Grafana, Loki, Alertmanager | chart defaults | chart defaults | chart defaults | 2 |
| CronJobs and Jobs | no probes; **dead man's switch**: alert if `kube_cronjob_status_last_successful_time` is older than 5 min, or any Job fails | | | 2 |

`/readyz` returns a body as well as a status: `{"model_version": 3, "queue_depth": 12, "db": "ok"}`.

**Uptime** is measured from the outside in: the blackbox exporter probes `/healthz` and a real
`POST /predict` through the ingress every 15 s (Phase 2).

## 2. SLOs

| SLI | SLO | Measured from |
|---|---|---|
| `/predict` availability: non-5xx at the ingress | 99.5% | Traefik metrics |
| `/predict` latency | p95 < 100 ms at design load | server histogram |
| prediction logging completeness: dropped / total | < 0.1% | server counters |
| monitor freshness: newest result within 2 simulated days of `sim_clock` | 99% of minutes | `monitor_results` |
| loop latency: K-th alarm → promoted pods | < 3 min wall clock | `loop_events` |

Availability alerts use **multi-window burn rates** (fast: 14.4× budget over 1 h *and* 5 min;
slow: 6× over 6 h *and* 30 min), so a short blip doesn't page and a slow leak doesn't go unseen.

## 3. Alerts

| Alert | Condition | Severity |
|---|---|---|
| PredictErrorBudgetBurn | burn-rate rule above | page |
| PredictLatencyHigh | p95 > 250 ms for 10 min | page |
| NoReadyServerPods | ready replicas = 0 for 1 min | page |
| **DataQualityBreach** | a quality monitor alarm: the guard's "alert a human" | page |
| CronJobStale | a monitor or the controller hasn't succeeded for 5 min | page |
| RetrainJobFailed | a retrain Job failed (not rejected: failed) | page |
| PostgresDown / PostgresConnectionsHigh | exporter down / > 80% of `max_connections` | page / ticket |
| PredictionsDropped | `predictions_dropped_total` increasing for 5 min | ticket |
| DriftAlarm | feature or label alarm (the controller handles it) | ticket |
| GateRejected | a challenger was rejected | ticket |
| VolumeFilling | a PVC > 80% full | ticket |

Page = Discord/email now (Alertmanager webhook). Ticket = shown on the loop dashboard.

## 4. Rollback

| What | How | Phase |
|---|---|---|
| **Model, automatic** | Argo Rollouts aborts the canary when analysis fails (error rate, p95, positive-prediction rate) | 4 |
| **Model, manual** | `driftops rollback --to <version>`: re-pins the previous version, moves the MLflow alias, writes a `loop_events` row | 3 |
| **Model, delayed** | perf-monitor's later verdict on the new model (labels arrive hours later) can trigger the same rollback | 3 |
| Application code | images are immutable by commit SHA; `helm rollback <revision>` or abort the rollout | 1 |
| Configuration | `helm rollback`: values are versioned with the release | 1 |
| Database schema | forward-only migrations as a pre-upgrade hook Job; expand → migrate → contract, never a destructive change in the release whose code still needs the old shape | 1 |
| Data | bad data is **quarantined, not deleted** (the guard); nightly `pg_dump` CronJob to Blob in prod | 3, 6 |
| Infrastructure | sessions are disposable: destroy and recreate from Terraform | 6 |

## 5. API security

### Input validation (Pydantic strict, Phase 1)

| Field | Rule |
|---|---|
| `carrier` | `^[A-Z0-9]{2}$` |
| `origin`, `dest` | `^[A-Z]{3}$`, origin ≠ dest |
| `flight_date` | ISO date; user requests: inside the replay range (2020-01-01 → 2020-06-30) |
| `dep_time` | `HH:MM`, 00:00–23:59 |
| `distance`, `sched_minutes`, loads (if sent) | 1–6,000 · 10–1,000 · 0–500 |
| anything else | rejected (`extra="forbid"`); body ≤ 2 KB |

**Unknown but well-formed values are served, not rejected**: a new carrier code gets a
prediction plus `"warnings": ["unseen carrier"]`, and the data-quality monitor counts it. That is
the guard's job, not the validator's.

**`source` comes from the caller's identity, never from the body.** Otherwise anyone could label
their requests `sim` and push them into the drift windows: a poisoning path straight into the
retrain decision.

**Degradation:** if the schedule-feature lookup times out (200 ms), the server still answers with
the missing features as nulls and logs the request as degraded. The quality monitor sees the null
rate rise, which is exactly the "lookup times out" corruption the research tested.

### CORS, headers, timeouts (Phase 1)

CORS allows only the demo site's origin, methods `GET` and `POST`, no credentials. Traefik adds
security headers (HSTS in prod, `X-Content-Type-Options`, `Referrer-Policy`) and a 2 s request
timeout.

### Authentication, authorization, rate limits (Phase 7)

| Route class | Who | AuthN | Rate limit |
|---|---|---|---|
| `POST /predict` from the web form | anyone | none | **30/min per IP**, burst 10 |
| `POST /predict` from integrations | API-key holders, role `predictor` | `X-API-Key` (hashed at rest) | **200/s per key**, burst 400, per-key tier |
| simulator | in-cluster only | NetworkPolicy, never via ingress | none |
| `/healthz`, `/readyz` | probes, uptime checks | none | 60/min per IP at the ingress |
| `POST /admin/rollback`, `/admin/retrain` | operators | GitHub OIDC, role `operator` | 5/min per user |
| Grafana | operators | Grafana's GitHub OAuth | — |
| `/metrics`, MLflow, Postgres | nobody outside the cluster | not exposed by the ingress | — |

Kubernetes RBAC (Phase 3): the controller's ServiceAccount may create Jobs and patch **one**
Deployment; the retrain Job's may patch that Deployment; nothing else may change the cluster.
NetworkPolicies (Phase 7): default deny; Postgres accepts the server, Jobs, MLflow and the
exporter only.

## 6. Logging

Every component logs JSON lines with the same keys: `ts`, `level`, `component`, `event`,
`model_version`, `sim_day`, `request_id`, `decision_id`. `request_id` comes from
`X-Request-ID` or is generated, and is returned in the response.

**Loki labels are low-cardinality only** (`namespace`, `component`, `level`). IDs stay inside
the line and are filtered at query time: `{component="retrain"} | json | decision_id="…"`.
Labelling by `request_id` is the classic way to make Loki slow and expensive.

At high load, successful access logs are sampled (1%); errors and decisions are never sampled.
Retention: Loki 7 days; `predictions` partitions dropped after 90 days in prod (dropping a
partition is instant, a `DELETE` is not).

## 7. Performance, capacity and cost

### Method (Phase 8)

k6 runs as a Job **inside** the cluster (a laptop's network would be the bottleneck), writing to
Prometheus. Borrowed from StressLab: **a level counts only if three 5-minute runs all pass** the
bar (p95 < 100 ms, errors < 0.1%), one change at a time, and invalid runs (the load generator was
the limit) are marked, not counted.

| Scenario | Shape | Answers |
|---|---|---|
| smoke | 1 VU, 1 min | does it work |
| load | ramp to target, hold 5 min | does one pod meet the SLO at its rated load |
| stress | ramp until p95 or errors break | the knee: max req/s per pod |
| spike | 10× in 10 s | does KEDA/HPA react before the SLO breaks |
| soak | target load, 30 min | leaks, queue growth, connection exhaustion |

Load tests run on **D-series** nodes, never B-series: burstable CPU credits run out mid-test and
make the results meaningless.

### Capacity model

```
pods(target) = ceil( target req/s ÷ (per-pod req/s at the SLO × 0.7 headroom) )
nodes        = ceil( (pods × pod CPU + platform CPU) ÷ allocatable CPU per node )
cost per h   = nodes × node $/h
```

The only input that must be measured is **per-pod req/s at the SLO**. Everything else is
arithmetic, so the table below re-computes itself from Phase 8's number.

### Worked example: ⚠️ estimates, to be replaced by measurements

Assumptions: a server pod = 1 vCPU / 1 GiB serving **~250 req/s at p95 < 100 ms** (to be
measured; single-row LightGBM scoring is well under 1 ms, so the Python request path dominates).
Node: `Standard_D4s_v5`, 4 vCPU / 16 GiB (~3.9 vCPU allocatable), **~$0.20/h** list price (to be confirmed for
canadacentral). Platform (Postgres, MLflow, monitoring) ≈ 4 vCPU.

| Load | Server pods | Nodes | ≈ $/hour | ≈ $/day if sustained |
|---|---|---|---|---|
| demo (~100 req/s) | 2 (min, for availability) | 2 | $0.40 | $9.60 |
| **1,000 req/s** | 6 | 3 | $0.60 | $14.40 |
| **10,000 req/s** (HPA/KEDA scaled) | 58 | 16 | $3.20 (≈ $0.05/min) | $77 |

**What breaks before 10,000 req/s is not the model server** (it scales horizontally): it is the
**prediction log**. 10,000 rows/s into one Postgres is 864M rows a day. The path at that scale:
batch `COPY` and daily partitions (already the design) → log a sample for monitoring and send the
full stream to a queue (Event Hubs/Kafka) landing in object storage → Postgres keeps only the
monitoring sample. That redesign, not more pods, is the real answer to "can it do 10k TPS?".

**Scaling reaction:** KEDA adds pods within ~30 s on existing nodes; a new AKS node takes 3–5
minutes. So spikes are absorbed by headroom and pod scaling; node autoscaling is for sustained
load. A minimum of 2 server pods keeps a single pod failure from being an outage.

**Student subscription limit:** the regional vCPU quota is low, so a 16-node test isn't possible
on this account. Phase 8 measures the per-pod number and the scale-out curve up to the quota, and
extrapolates, saying so.
