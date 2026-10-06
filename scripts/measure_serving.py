"""Phase 1 measurement: requests/s, latency percentiles, logged vs dropped, over a window.

Scrapes every server pod's /metrics (via kubectl exec) twice, `--seconds` apart, and reports
the difference, plus Postgres row counts, pod restarts and the simulator's progress.

    uv run python scripts/measure_serving.py --seconds 300 > runs/phase1/serving.json
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import time

NS = "driftops"
SCRAPE = (
    "import urllib.request as u;print(u.urlopen('http://localhost:8000/{path}').read().decode())"
)


def sh(*args: str) -> str:
    return subprocess.run(args, capture_output=True, text=True, check=True).stdout


def pods(component: str) -> list[str]:
    out = sh(
        "kubectl",
        "-n",
        NS,
        "get",
        "pods",
        "-l",
        f"app.kubernetes.io/component={component}",
        "-o",
        "jsonpath={.items[*].metadata.name}",
    )
    return out.split()


def scrape(pod: str, path: str = "metrics") -> str:
    return sh("kubectl", "-n", NS, "exec", pod, "--", "python", "-c", SCRAPE.format(path=path))


def parse(text: str) -> dict:
    """Sum the server's predict counters and latency buckets across label sets."""
    out = {
        "requests": 0.0,
        "ok": 0.0,
        "logged": 0.0,
        "dropped": 0.0,
        "buckets": {},
        "lat_sum": 0.0,
        "lat_count": 0.0,
    }
    for line in text.splitlines():
        if line.startswith("#") or not line.strip():
            continue
        name, value = line.rsplit(" ", 1)
        v = float(value)
        if name.startswith("driftops_requests_total") and 'endpoint="predict"' in name:
            out["requests"] += v
            if 'status="200"' in name:
                out["ok"] += v
        elif name.startswith("driftops_predictions_logged_total"):
            out["logged"] += v
        elif name.startswith("driftops_predictions_dropped_total"):
            out["dropped"] += v
        elif name.startswith("driftops_request_seconds_bucket") and 'endpoint="predict"' in name:
            le = re.search(r'le="([^"]+)"', name).group(1)
            out["buckets"][le] = out["buckets"].get(le, 0.0) + v
        elif name.startswith("driftops_request_seconds_sum") and 'endpoint="predict"' in name:
            out["lat_sum"] += v
        elif name.startswith("driftops_request_seconds_count") and 'endpoint="predict"' in name:
            out["lat_count"] += v
    return out


def total(component: str = "server") -> dict:
    acc: dict = {}
    for p in pods(component):
        m = parse(scrape(p))
        for k, v in m.items():
            if k == "buckets":
                b = acc.setdefault("buckets", {})
                for le, c in v.items():
                    b[le] = b.get(le, 0.0) + c
            else:
                acc[k] = acc.get(k, 0.0) + v
    return acc


def quantile(buckets: dict, q: float) -> float | str:
    """Upper bound of the histogram bucket holding the q-quantile (Prometheus-style, coarse)."""
    items = sorted(((float("inf") if k == "+Inf" else float(k)), c) for k, c in buckets.items())
    n = items[-1][1] if items else 0
    if not n:
        return "n/a"
    for le, c in items:
        if c >= q * n:
            return "> 1 s" if le == float("inf") else f"<= {le * 1000:g} ms"
    return "n/a"


def restarts() -> dict:
    out = json.loads(sh("kubectl", "-n", NS, "get", "pods", "-o", "json"))
    return {
        p["metadata"]["name"]: sum(
            c.get("restartCount", 0) for c in p["status"].get("containerStatuses", [])
        )
        for p in out["items"]
    }


def db_counts() -> dict:
    sql = "SELECT (SELECT count(*) FROM predictions), (SELECT count(*) FROM predictions WHERE source='sim'), (SELECT now FROM sim_clock)"
    out = sh(
        "kubectl",
        "-n",
        NS,
        "exec",
        "driftops-postgres-0",
        "--",
        "psql",
        "-U",
        "driftops",
        "-tAc",
        sql,
    )
    total_rows, sim_rows, clock = out.strip().split("|")
    return {"predictions": int(total_rows), "sim_predictions": int(sim_rows), "sim_clock": clock}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=int, default=300)
    a = ap.parse_args()
    t0, m0, db0, r0 = time.time(), total(), db_counts(), restarts()
    time.sleep(a.seconds)
    t1, m1, db1, r1 = time.time(), total(), db_counts(), restarts()
    dt = t1 - t0
    d = {
        k: m1[k] - m0.get(k, 0.0)
        for k in ("requests", "ok", "logged", "dropped", "lat_sum", "lat_count")
    }
    buckets = {k: m1["buckets"][k] - m0["buckets"].get(k, 0.0) for k in m1["buckets"]}
    sim = json.loads(scrape(pods("simulator")[0], "status"))
    print(
        json.dumps(
            {
                "window_s": round(dt),
                "server_pods": len(pods("server")),
                "requests_per_s": round(d["requests"] / dt, 1),
                "ok_ratio": round(d["ok"] / d["requests"], 4) if d["requests"] else None,
                "latency_mean_ms": round(d["lat_sum"] / d["lat_count"] * 1000, 2)
                if d["lat_count"]
                else None,
                "latency_p50": quantile(buckets, 0.50),
                "latency_p95": quantile(buckets, 0.95),
                "latency_p99": quantile(buckets, 0.99),
                "logged": int(d["logged"]),
                "dropped": int(d["dropped"]),
                "rows_written": db1["predictions"] - db0["predictions"],
                "sim_clock": {"from": db0["sim_clock"], "to": db1["sim_clock"]},
                "restarts_during_window": {
                    k: r1.get(k, 0) - r0.get(k, 0) for k in r1 if r1.get(k, 0) != r0.get(k, 0)
                },
                "simulator": sim,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
