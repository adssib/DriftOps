"""Generate the Grafana dashboards (dashboards as code).

    uv run python scripts/build_dashboards.py   → deploy/helm/driftops/dashboards/*.json

Five dashboards, one per audience (ARCHITECTURE § 4). Threshold lines come from
`driftops.policy.Limits`, so a chart can never show a different line from the one that alarms.
ML panels plot simulated days (2020) from Postgres; service panels plot wall time from
Prometheus. Each panel's description says which.
"""

from __future__ import annotations

import json
from pathlib import Path

from driftops.policy import Limits

OUT = Path("deploy/helm/driftops/dashboards")
PG = {"type": "grafana-postgresql-datasource", "uid": "driftops-postgres"}
PROM = {"type": "prometheus", "uid": "prometheus"}
LOKI = {"type": "loki", "uid": "loki"}
SIM_TIME = {"from": "2020-03-01T00:00:00.000Z", "to": "2020-06-01T00:00:00.000Z"}
NS = "driftops"
LIM = Limits()

RUN_FILTER = "run_id = $run"
RUN_VAR = {
    "name": "run",
    "label": "Run",
    "type": "query",
    "datasource": PG,
    "query": "SELECT run_id AS __value, 'run ' || run_id || ' · ' || scenario AS __text FROM runs ORDER BY run_id DESC",
    "refresh": 1,
    "sort": 0,
    "current": {},
}


# panel builders ----------------------------------------------------------------------------


def sql(raw: str, fmt: str = "time_series", ref: str = "A") -> dict:
    return {
        "refId": ref,
        "datasource": PG,
        "rawQuery": True,
        "editorMode": "code",
        "format": fmt,
        "rawSql": raw,
    }


def promql(expr: str, legend: str = "", ref: str = "A") -> dict:
    return {"refId": ref, "datasource": PROM, "expr": expr, "legendFormat": legend}


def thresholds(*lines: float, color: str = "red") -> dict:
    steps = [{"color": "green", "value": None}] + [{"color": color, "value": v} for v in lines]
    return {"mode": "absolute", "steps": steps}


def timeseries(title, targets, desc, unit="short", lines=(), ds=PG, relative=None, stack=False):
    custom = {"lineWidth": 2, "fillOpacity": 8, "showPoints": "never", "spanNulls": True}
    if lines:
        custom["thresholdsStyle"] = {"mode": "dashed"}
    if stack:
        custom["stacking"] = {"mode": "normal"}
    p = {
        "type": "timeseries",
        "title": title,
        "description": desc,
        "datasource": ds,
        "targets": targets,
        "fieldConfig": {
            "defaults": {"unit": unit, "custom": custom, "thresholds": thresholds(*lines)},
            "overrides": [],
        },
        "options": {
            "legend": {"displayMode": "list", "placement": "bottom"},
            "tooltip": {"mode": "multi"},
        },
    }
    if relative:
        p["timeFrom"] = relative
    return p


def stat(title, targets, desc, unit="short", ds=PROM, relative=None, lines=(), text_mode="value"):
    p = {
        "type": "stat",
        "title": title,
        "description": desc,
        "datasource": ds,
        "targets": targets,
        "fieldConfig": {
            "defaults": {"unit": unit, "thresholds": thresholds(*lines)},
            "overrides": [],
        },
        "options": {
            "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
            "colorMode": "background" if lines else "none",
            "textMode": text_mode,
            "graphMode": "none",
        },
    }
    if relative:
        p["timeFrom"] = relative
    return p


def table(title, targets, desc, ds=PG):
    return {
        "type": "table",
        "title": title,
        "description": desc,
        "datasource": ds,
        "targets": targets,
        "fieldConfig": {"defaults": {}, "overrides": []},
        "options": {"showHeader": True},
    }


def alarm_timeline(title, targets, desc):
    mappings = [
        {
            "type": "value",
            "options": {
                "0": {"text": "ok", "color": "green", "index": 0},
                "1": {"text": "ALARM", "color": "red", "index": 1},
            },
        }
    ]
    return {
        "type": "state-timeline",
        "title": title,
        "description": desc,
        "datasource": PG,
        "targets": targets,
        "fieldConfig": {
            "defaults": {
                "mappings": mappings,
                "color": {"mode": "thresholds"},
                "thresholds": thresholds(1),
            },
            "overrides": [],
        },
        "options": {"showValue": "never", "mergeValues": True, "rowHeight": 0.8},
    }


def logs(title, expr, desc, relative="30m"):
    return {
        "type": "logs",
        "title": title,
        "description": desc,
        "datasource": LOKI,
        "targets": [{"refId": "A", "datasource": LOKI, "expr": expr}],
        "options": {
            "showTime": True,
            "wrapLogMessage": True,
            "sortOrder": "Descending",
            "enableLogDetails": True,
        },
        "timeFrom": relative,
    }


def text(content: str) -> dict:
    return {
        "type": "text",
        "title": "",
        "options": {"mode": "markdown", "content": content},
        "transparent": True,
    }


def monitor_series(monitor: str, expr: str, name: str) -> str:
    return (
        f'SELECT window_end AS time, {expr} AS "{name}" FROM monitor_results '
        f"WHERE {RUN_FILTER} AND monitor = '{monitor}' AND $__timeFilter(window_end) ORDER BY 1"
    )


def layout(rows: list[list[tuple[dict, int, int]]]) -> list[dict]:
    """rows of (panel, width, height) → panels with gridPos, ids."""
    panels, y, pid = [], 0, 1
    for row in rows:
        x, h_max = 0, 0
        for panel, w, h in row:
            panel = dict(panel)
            panel["gridPos"] = {"x": x, "y": y, "w": w, "h": h}
            panel["id"] = pid
            pid += 1
            panels.append(panel)
            x += w
            h_max = max(h_max, h)
        y += h_max
    return panels


def dashboard(
    uid, title, desc, rows, time=None, refresh="", variables=(), annotations=True, tags=()
):
    ann = [
        {
            "builtIn": 1,
            "datasource": {"type": "grafana", "uid": "-- Grafana --"},
            "enable": True,
            "hide": True,
            "name": "Annotations & Alerts",
            "type": "dashboard",
        }
    ]
    if annotations:
        ann.append(
            {
                "name": "Loop decisions",
                "datasource": PG,
                "enable": True,
                "iconColor": "purple",
                "target": sql(
                    f"SELECT sim_time AS time, kind || coalesce(' v' || model_version, '') AS text, kind AS tags "
                    f"FROM loop_events WHERE {RUN_FILTER} AND $__timeFilter(sim_time) ORDER BY 1",
                    fmt="table",
                ),
            }
        )
    return {
        "uid": uid,
        "title": title,
        "description": desc,
        "tags": ["driftops", *tags],
        "timezone": "utc",
        "editable": False,
        "schemaVersion": 39,
        "time": time or {"from": "now-30m", "to": "now"},
        "refresh": refresh,
        "templating": {"list": list(variables)},
        "annotations": {"list": ann},
        "panels": layout(rows),
    }


# the five dashboards ------------------------------------------------------------------------

SERVER = f'namespace="{NS}"'
PREDICT = 'endpoint="predict"'


def service_health() -> dict:
    def quantile(q: float) -> str:
        return f"histogram_quantile({q}, sum by (le) (rate(driftops_request_seconds_bucket{{{PREDICT}}}[5m])))"

    rows = [
        [
            (
                stat(
                    "Requests / s",
                    [promql(f"sum(rate(driftops_requests_total{{{PREDICT}}}[1m]))")],
                    "All /predict traffic, wall time.",
                    "reqps",
                ),
                4,
                4,
            ),
            (
                stat(
                    "Error ratio (5m)",
                    [promql("driftops:predict_error_ratio:rate5m")],
                    "5xx / all. SLO: 99.5% available (OPERATIONS § 2).",
                    "percentunit",
                    lines=(0.005,),
                ),
                4,
                4,
            ),
            (
                stat(
                    "p95 latency",
                    [promql(quantile(0.95))],
                    "SLO: p95 < 100 ms at design load.",
                    "s",
                    lines=(0.1,),
                ),
                4,
                4,
            ),
            (
                stat(
                    "Dropped predictions (5m)",
                    [promql("sum(increase(driftops_predictions_dropped_total[5m])) or vector(0)")],
                    "Predictions that never reached Postgres: monitoring is blind to them.",
                    lines=(1,),
                ),
                4,
                4,
            ),
            (
                stat(
                    "Ready server pods",
                    [
                        promql(
                            f'kube_deployment_status_replicas_available{{{SERVER},deployment="driftops-server"}}'
                        )
                    ],
                    "Below 1 is an outage.",
                ),
                4,
                4,
            ),
            (
                stat(
                    "Serving",
                    [
                        promql("max by (version) (driftops_model_info)", "model v{{version}}"),
                        promql("max by (git_sha) (driftops_build_info)", "build {{git_sha}}", "B"),
                    ],
                    "Pinned model version and the commit the pods run.",
                    text_mode="name",
                ),
                4,
                4,
            ),
        ],
        [
            (
                timeseries(
                    "Requests / s by model version",
                    [
                        promql(
                            f"sum by (model_version) (rate(driftops_requests_total{{{PREDICT}}}[1m]))",
                            "v{{model_version}}",
                        )
                    ],
                    "During a canary both versions appear.",
                    "reqps",
                    ds=PROM,
                ),
                8,
                8,
            ),
            (
                timeseries(
                    "Latency",
                    [
                        promql(quantile(0.5), "p50"),
                        promql(quantile(0.95), "p95", "B"),
                        promql(quantile(0.99), "p99", "C"),
                    ],
                    "Histogram quantiles over 5 minutes.",
                    "s",
                    lines=(0.1,),
                    ds=PROM,
                ),
                8,
                8,
            ),
            (
                timeseries(
                    "Requests / s per pod",
                    [
                        promql(
                            f"sum by (pod) (rate(driftops_requests_total{{{PREDICT}}}[1m]))",
                            "{{pod}}",
                        )
                    ],
                    "A Service balances per connection, not per request: uneven lines here mean pinned keep-alive connections.",
                    "reqps",
                    ds=PROM,
                ),
                8,
                8,
            ),
        ],
        [
            (
                timeseries(
                    "CPU per server pod",
                    [
                        promql(
                            f'sum by (pod) (rate(container_cpu_usage_seconds_total{{{SERVER},container="server"}}[1m]))',
                            "{{pod}}",
                        )
                    ],
                    "Limit: 1 CPU per pod.",
                    "short",
                    lines=(1,),
                    ds=PROM,
                ),
                8,
                8,
            ),
            (
                timeseries(
                    "CPU throttling",
                    [
                        promql(
                            f'sum by (pod) (rate(container_cpu_cfs_throttled_periods_total{{{SERVER},container="server"}}[1m])) / sum by (pod) (rate(container_cpu_cfs_periods_total{{{SERVER},container="server"}}[1m]))',
                            "{{pod}}",
                        )
                    ],
                    "Share of CPU periods throttled by the limit. Throttling shows up as latency.",
                    "percentunit",
                    ds=PROM,
                ),
                8,
                8,
            ),
            (
                timeseries(
                    "Prediction log",
                    [
                        promql("sum(rate(driftops_predictions_logged_total[1m]))", "logged / s"),
                        promql(
                            "sum(rate(driftops_predictions_dropped_total[1m])) or vector(0)",
                            "dropped / s",
                            "B",
                        ),
                        promql("max(driftops_log_queue_fill_ratio)", "queue fill", "C"),
                    ],
                    "Batches flushed to Postgres. Dropped must stay 0.",
                    "short",
                    ds=PROM,
                ),
                8,
                8,
            ),
        ],
        [
            (
                timeseries(
                    "Uptime probes",
                    [promql('probe_success{service="driftops"}', "{{instance}}")],
                    "Blackbox exporter, through the ingress and at the service. 1 = up.",
                    "short",
                    ds=PROM,
                ),
                8,
                7,
            ),
            (
                timeseries(
                    "Postgres",
                    [
                        promql(f"sum(pg_stat_activity_count{{{SERVER}}})", "connections"),
                        promql(
                            f'pg_database_size_bytes{{{SERVER},datname="driftops"}} / 1e6',
                            "database MB",
                            "B",
                        ),
                    ],
                    "From the postgres_exporter sidecar.",
                    "short",
                    ds=PROM,
                ),
                8,
                7,
            ),
            (
                logs(
                    "Server warnings and errors",
                    f'{{namespace="{NS}", component="server"}} | json | level=~"warning|error"',
                    "Loki. Request IDs are in the line, not in labels.",
                ),
                8,
                7,
            ),
        ],
    ]
    return dashboard(
        "driftops-service",
        "DriftOps · Service health",
        "SRE view: traffic, errors, latency, saturation, logging, uptime.",
        rows,
        refresh="10s",
        annotations=False,
        tags=("sre",),
    )


def data_drift() -> dict:
    per_feature = (
        "SELECT window_end AS time, f.key AS metric, f.value::float AS value FROM monitor_results, "
        "jsonb_each_text(metrics->'psi') AS f WHERE " + RUN_FILTER + " AND monitor = 'drift' "
        "AND $__timeFilter(window_end) ORDER BY 1"
    )
    rows = [
        [
            (
                text(
                    f"**Feature drift**: PSI of each feature over the trailing 7 simulated days against the serving model's reference. Alarm above **{LIM.psi_feature}** (ADR-0004). Calendar features are never alarmed on; prediction PSI is charted, never alarmed on (seasonal by construction). X axis: simulated time."
                ),
                24,
                2,
            )
        ],
        [
            (
                timeseries(
                    "Max feature PSI",
                    [
                        sql(
                            monitor_series(
                                "drift", "(metrics->>'psi_max_feature')::float", "max feature PSI"
                            )
                        )
                    ],
                    "The alarm signal. Simulated days.",
                    lines=(LIM.psi_feature,),
                ),
                12,
                9,
            ),
            (
                timeseries(
                    "PSI per feature",
                    [sql(per_feature)],
                    "Which feature moves. In March 2020 nothing does; in April the schedule cut shows in the hour loads.",
                    lines=(LIM.psi_feature,),
                ),
                12,
                9,
            ),
        ],
        [
            (
                timeseries(
                    "Prediction PSI (charted, not alarmed)",
                    [
                        sql(
                            monitor_series(
                                "drift", "(metrics->>'psi_score')::float", "prediction PSI"
                            )
                        )
                    ],
                    "Score distribution vs. reference. Swings with the seasons even when nothing is wrong.",
                ),
                12,
                7,
            ),
            (
                alarm_timeline(
                    "Drift alarm",
                    [sql(monitor_series("drift", "alarm::int", "drift"))],
                    "One cell per simulated day.",
                ),
                12,
                7,
            ),
        ],
    ]
    return dashboard(
        "driftops-drift",
        "DriftOps · Data drift",
        "ML engineer view: are the inputs changing?",
        rows,
        time=SIM_TIME,
        variables=[RUN_VAR],
        tags=("ml",),
    )


def data_quality() -> dict:
    def rate(key, limit, desc):
        return (
            timeseries(
                key.replace("_", " "),
                [sql(monitor_series("quality", f"(metrics->>'{key}')::float", key))],
                desc,
                "percentunit",
                lines=(limit,),
            ),
            6,
            8,
        )

    rows = [
        [
            (
                text(
                    "**Data quality**: things that mean *the pipeline broke*, not *the world changed*. Any breach **blocks retraining** and pages a human (ADR-0005). Simulated time."
                ),
                24,
                2,
            )
        ],
        [
            rate(
                "null_increase",
                LIM.null_increase,
                "Worst rise in a column's null rate vs. training. The 'lookup times out' corruption.",
            ),
            rate(
                "out_of_range",
                LIM.out_of_range,
                "Share of numeric values outside the training 0.05–99.95% range.",
            ),
            rate(
                "unseen_category",
                LIM.unseen_category,
                "Rows with a carrier or airport the model never saw. The 'renamed code' corruption.",
            ),
            rate(
                "route_distance_mismatch",
                LIM.route_distance_mismatch,
                "Distance disagrees with the known route by > 2%. The miles→km bug.",
            ),
        ],
        [
            (
                alarm_timeline(
                    "Quality breach",
                    [sql(monitor_series("quality", "alarm::int", "quality"))],
                    "Red = retraining is blocked for this window.",
                ),
                24,
                5,
            )
        ],
    ]
    return dashboard(
        "driftops-quality",
        "DriftOps · Data quality",
        "Data engineer view: is the data broken?",
        rows,
        time=SIM_TIME,
        variables=[RUN_VAR],
        tags=("ml",),
    )


def model_performance() -> dict:
    slices = (
        "SELECT s.key AS carrier, (s.value->>'rows')::int AS flights, "
        "round((s.value->>'brier')::numeric, 4) AS brier, round((s.value->>'calibration_gap')::numeric, 4) AS calibration_gap "
        "FROM monitor_results, jsonb_each(metrics->'slices') AS s WHERE "
        + RUN_FILTER
        + " AND monitor = 'perf' "
        "AND window_end = (SELECT max(window_end) FROM monitor_results WHERE "
        + RUN_FILTER
        + " AND monitor = 'perf') "
        "ORDER BY flights DESC"
    )
    lag = (
        'SELECT (SELECT now FROM sim_clock) AS "simulated now", '
        "(SELECT value FROM watermarks WHERE name = 'label-feed') AS \"labels delivered up to\""
    )
    rows = [
        [
            (
                text(
                    f"**Label drift**: what outcomes say about the model, over the 7 simulated days before each day, with only the outcomes known by then. Alarm when |calibration gap| > **{LIM.calibration_gap}** or Brier > **{LIM.brier}**. This is the signal that caught COVID two weeks before feature drift did."
                ),
                24,
                2,
            )
        ],
        [
            (
                timeseries(
                    "Observed vs predicted disruption rate",
                    [
                        sql(
                            monitor_series("perf", "(metrics->>'observed_rate')::float", "observed")
                        ),
                        sql(
                            monitor_series(
                                "perf", "(metrics->>'predicted_rate')::float", "predicted"
                            ),
                            ref="B",
                        ),
                    ],
                    "When these separate, the model's world has changed.",
                    "percentunit",
                ),
                12,
                8,
            ),
            (
                timeseries(
                    "Calibration gap",
                    [
                        sql(
                            monitor_series(
                                "perf", "(metrics->>'calibration_gap')::float", "calibration gap"
                            )
                        )
                    ],
                    "Observed minus predicted. Alarm beyond ±threshold.",
                    lines=(-LIM.calibration_gap, LIM.calibration_gap),
                ),
                12,
                8,
            ),
        ],
        [
            (
                timeseries(
                    "Brier score",
                    [sql(monitor_series("perf", "(metrics->>'brier')::float", "Brier"))],
                    "Lower is better.",
                    lines=(LIM.brier,),
                ),
                8,
                8,
            ),
            (
                timeseries(
                    "AUC",
                    [sql(monitor_series("perf", "(metrics->>'auc')::float", "AUC"))],
                    "Ranking quality; can't see calibration failures.",
                    "short",
                ),
                8,
                8,
            ),
            (
                table(
                    "Label lag",
                    [sql(lag, fmt="table")],
                    "How far behind the simulated clock the outcomes are.",
                ),
                8,
                8,
            ),
        ],
        [
            (
                table(
                    "Per-carrier slices, latest window",
                    [sql(slices, fmt="table")],
                    "The ten busiest carriers. A slice can fail while the average looks fine.",
                ),
                24,
                8,
            )
        ],
    ]
    return dashboard(
        "driftops-performance",
        "DriftOps · Model performance",
        "Data scientist view: is the model still right?",
        rows,
        time=SIM_TIME,
        variables=[RUN_VAR],
        tags=("ml",),
    )


def the_loop() -> dict:
    def thr(metric_sql, limit):
        return f"abs({metric_sql}) / {limit}"

    normalised = [
        sql(
            monitor_series(
                "drift",
                thr("(metrics->>'psi_max_feature')::float", LIM.psi_feature),
                "feature drift",
            )
        ),
        sql(
            monitor_series(
                "perf",
                thr("(metrics->>'calibration_gap')::float", LIM.calibration_gap),
                "label drift (gap)",
            ),
            ref="B",
        ),
    ]
    alarms = (
        "SELECT window_end AS time, monitor AS metric, alarm::int AS value FROM monitor_results "
        "WHERE " + RUN_FILTER + " AND $__timeFilter(window_end) ORDER BY 1"
    )
    clock = (
        'SELECT c.now AS "simulated now", r.scenario, c.segment, c.run_id AS run FROM sim_clock c '
        "LEFT JOIN runs r USING (run_id)"
    )
    decisions = (
        'SELECT sim_time AS "simulated time", kind, model_version, decision_id, details::text AS details '
        "FROM loop_events WHERE " + RUN_FILTER + " ORDER BY id DESC LIMIT 50"
    )
    rows = [
        [
            (table("Simulated clock", [sql(clock, fmt="table")], "Where the replay is."), 12, 4),
            (
                stat(
                    "Serving model",
                    [promql("max by (version) (driftops_model_info)", "v{{version}}")],
                    "The pinned version the server pods run.",
                    text_mode="name",
                ),
                4,
                4,
            ),
            (
                stat(
                    "Retrains",
                    [
                        sql(
                            f"SELECT count(*) FROM loop_events WHERE {RUN_FILTER} AND kind = 'retrain'",
                            fmt="table",
                        )
                    ],
                    "Phase 3.",
                    ds=PG,
                ),
                4,
                4,
            ),
            (
                stat(
                    "Blocked by data quality",
                    [
                        sql(
                            f"SELECT count(*) FROM monitor_results WHERE {RUN_FILTER} AND monitor = 'quality' AND alarm",
                            fmt="table",
                        )
                    ],
                    "Windows where retraining was not allowed.",
                    ds=PG,
                    lines=(1,),
                ),
                4,
                4,
            ),
        ],
        [
            (
                timeseries(
                    "Both signals, as a fraction of their threshold",
                    normalised,
                    "1.0 = the alarm line for each signal. Label drift crosses first on COVID; feature drift follows when schedules are cut.",
                    "short",
                    lines=(1,),
                ),
                24,
                9,
            )
        ],
        [
            (
                alarm_timeline(
                    "Alarms per monitor",
                    [sql(alarms)],
                    "quality / drift / perf, one cell per simulated day. K = 3 in a row (and clean quality) triggers a retrain (Phase 3).",
                ),
                24,
                6,
            )
        ],
        [
            (
                table(
                    "Decisions (loop_events)",
                    [sql(decisions, fmt="table")],
                    "Every alarm, block, retrain, gate result and promotion (Phase 3).",
                ),
                12,
                8,
            ),
            (
                timeseries(
                    "CronJob freshness",
                    [
                        promql(
                            f"time() - kube_cronjob_status_last_successful_time{{{SERVER}}}",
                            "{{cronjob}}",
                        )
                    ],
                    "Seconds since each CronJob last succeeded. Above 300 = the dead man's switch fires.",
                    "s",
                    lines=(300,),
                    ds=PROM,
                    relative="30m",
                ),
                12,
                8,
            ),
        ],
        [
            (
                logs(
                    "Monitor and feeder logs",
                    f'{{namespace="{NS}", component=~"monitor-.*|label-feed"}} | json | event=~"monitor_result|feed_done|waiting_for_labels"',
                    "What each CronJob decided, from Loki.",
                ),
                24,
                8,
            )
        ],
    ]
    return dashboard(
        "driftops-loop",
        "DriftOps · The loop",
        "The demo view: signals, alarms, decisions.",
        rows,
        time=SIM_TIME,
        refresh="30s",
        variables=[RUN_VAR],
        tags=("demo",),
    )


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, build in {
        "service-health": service_health,
        "data-drift": data_drift,
        "data-quality": data_quality,
        "model-performance": model_performance,
        "the-loop": the_loop,
    }.items():
        path = OUT / f"{name}.json"
        path.write_text(json.dumps(build(), indent=1) + "\n")
        print(path)


if __name__ == "__main__":
    main()
