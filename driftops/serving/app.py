"""The model server (SPEC F1, F2, F4; OPERATIONS § 1, 5, 6).

    POST /predict   probability, risk band, serving version, top SHAP reasons
    GET  /healthz   liveness: the process answers. Never checks dependencies.
    GET  /readyz    readiness: model loaded and the log queue has room
    GET  /metrics   Prometheus

The model loads in a background thread, so /healthz answers at once while /readyz stays 503
until the pinned version is in memory.
"""

from __future__ import annotations

import hmac
import threading
import time
import uuid
from collections.abc import Callable
from contextlib import asynccontextmanager

import numpy as np
import pandas as pd
import structlog
from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)
from pydantic import ValidationError

from driftops import features as F
from driftops.config import Settings
from driftops.model import Model
from driftops.serving.features import ScheduleLookup, build_row, event_time
from driftops.serving.logger import PredictionLogger
from driftops.serving.schemas import PredictRequest, PredictResponse, check_user_date, risk_band

MAX_BODY = 2048
TOP_REASONS = 3


class Metrics:
    def __init__(self) -> None:
        self.registry = CollectorRegistry()
        r = self.registry
        self.requests = Counter(
            "driftops_requests", "Requests", ["endpoint", "status", "model_version"], registry=r
        )
        self.latency = Histogram(
            "driftops_request_seconds",
            "Request latency",
            ["endpoint", "model_version"],
            buckets=(0.002, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0),
            registry=r,
        )
        self.logged = Counter(
            "driftops_predictions_logged", "Predictions written to Postgres", registry=r
        )
        self.dropped = Counter(
            "driftops_predictions_dropped", "Predictions not logged", ["reason"], registry=r
        )
        self.queue_fill = Gauge(
            "driftops_log_queue_fill_ratio", "Prediction log queue fill ratio", registry=r
        )
        self.model_info = Gauge(
            "driftops_model_info", "Serving model version", ["version"], registry=r
        )
        self.warnings = Counter(
            "driftops_request_warnings", "Request warnings", ["kind"], registry=r
        )


class State:
    def __init__(self) -> None:
        self.model: Model | None = None
        self.version: int | None = None
        self.load_error: str | None = None


def create_app(
    settings: Settings,
    *,
    load_model: Callable[[], Model],
    lookup: ScheduleLookup | None,
    logger: PredictionLogger,
    metrics: Metrics | None = None,
) -> FastAPI:
    m = metrics or Metrics()
    logger.on_logged = lambda n: m.logged.inc(n)
    logger.on_dropped = lambda n, reason: m.dropped.labels(reason).inc(n)
    state = State()
    log = structlog.get_logger()

    def _load() -> None:
        try:
            model = load_model()
            state.model, state.version = model, settings.model_version
            m.model_info.labels(str(settings.model_version)).set(1)
            log.info(
                "model_loaded",
                model_version=settings.model_version,
                trained_to=str(model.trained_to.date()),
            )
        except Exception as e:  # noqa: BLE001 - any load failure: stay unready, never crash-loop
            state.load_error = str(e)[:300]
            log.error(
                "model_load_failed", model_version=settings.model_version, error=state.load_error
            )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        logger.start()
        threading.Thread(target=_load, name="model-loader", daemon=True).start()
        yield
        logger.stop()

    app = FastAPI(title="DriftOps model server", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.driftops = state
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_methods=["GET", "POST"],
            allow_headers=["Content-Type", "X-Request-ID"],
            allow_credentials=False,
        )

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        rid = request.headers.get("x-request-id") or str(uuid.uuid4())
        if len(rid) > 64:
            rid = str(uuid.uuid4())
        structlog.contextvars.bind_contextvars(request_id=rid)
        t0 = time.perf_counter()
        try:
            response = await call_next(request)
        finally:
            structlog.contextvars.unbind_contextvars("request_id")
        response.headers["X-Request-ID"] = rid
        ms = (time.perf_counter() - t0) * 1000
        if request.url.path == "/predict" and response.status_code >= 400:
            log.info(
                "request",
                path=request.url.path,
                status=response.status_code,
                ms=round(ms, 2),
                request_id=rid,
            )
        return response

    def source_of(request: Request) -> str:
        """ADR-0019: the simulator proves who it is with a token; everyone else is a user."""
        auth = request.headers.get("authorization", "")
        token = auth.removeprefix("Bearer ").strip()
        if settings.sim_token and token and hmac.compare_digest(token, settings.sim_token):
            return "sim"
        return "user"

    def version_label() -> str:
        return str(state.version) if state.version is not None else "none"

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    @app.get("/readyz")
    def readyz():
        fill = logger.fill_ratio()
        m.queue_fill.set(fill)
        body = {
            "model_version": state.version,
            "queue_fill": round(fill, 3),
            "logged": logger.logged,
            "dropped": logger.dropped,
        }
        if state.model is None:
            body["status"] = "loading" if state.load_error is None else "load_failed"
            body["error"] = state.load_error
            return JSONResponse(body, status_code=503)
        if fill >= 0.9:
            body["status"] = "log_queue_full"
            return JSONResponse(body, status_code=503)
        body["status"] = "ready"
        return body

    @app.get("/metrics")
    def metrics_endpoint():
        m.queue_fill.set(logger.fill_ratio())
        return Response(generate_latest(m.registry), media_type=CONTENT_TYPE_LATEST)

    @app.post("/predict", response_model=PredictResponse)
    async def predict(request: Request):
        t0 = time.perf_counter()
        rid = structlog.contextvars.get_contextvars().get("request_id", str(uuid.uuid4()))

        def done(status: int, payload, version: str | None = None):
            v = version or version_label()
            m.requests.labels("predict", str(status), v).inc()
            m.latency.labels("predict", v).observe(time.perf_counter() - t0)
            return JSONResponse(payload, status_code=status)

        body = await request.body()
        if len(body) > MAX_BODY:
            return done(413, {"detail": f"body larger than {MAX_BODY} bytes", "request_id": rid})
        model = state.model
        if model is None:
            return done(503, {"detail": "model not loaded", "request_id": rid})
        source = source_of(request)
        run_header = request.headers.get("x-driftops-run", "")
        run_id = int(run_header) if source == "sim" and run_header.isdigit() else None
        try:
            req = PredictRequest.model_validate_json(body)
            if source == "user":
                check_user_date(req)
        except ValidationError as e:
            return done(
                422,
                {"detail": e.errors(include_url=False, include_context=False), "request_id": rid},
            )
        except ValueError as e:
            return done(422, {"detail": str(e), "request_id": rid})

        # Lookups (sync DB calls) and scoring (CPU) run in the thread pool, never on the event
        # loop: a blocked loop can't answer /healthz, and the liveness probe would restart a
        # perfectly healthy pod under load.
        explain = request.query_params.get("explain", "true").lower() != "false"
        row, warnings, p, reasons = await run_in_threadpool(
            score, model, lookup, req, source, explain
        )
        for w in warnings:
            m.warnings.labels(w.split(":")[0]).inc()

        latency_ms = (time.perf_counter() - t0) * 1000
        logger.log(
            {
                "request_id": rid,
                "flight_id": req.flight_id,
                "source": source,
                "run_id": run_id,
                "event_time": event_time(req),
                "model_version": state.version,
                "features": row,
                "score": p,
                "latency_ms": latency_ms,
            }
        )
        return done(
            200,
            PredictResponse(
                request_id=rid,
                p_disrupted=round(p, 4),
                risk=risk_band(p),
                model_version=state.version,
                top_reasons=reasons,
                warnings=warnings,
            ).model_dump(),
        )

    return app


def score(
    model: Model, lookup: ScheduleLookup | None, req: PredictRequest, source: str, explain: bool
):
    """Features → probability (+ SHAP reasons on request). Runs off the event loop.

    LightGBM is told to use one thread: its default OpenMP pool is sized to the node's cores,
    which for one row is pure overhead (3.2 ms vs 14.8 ms measured) and, inside a CPU-limited
    pod, causes throttling. SHAP costs ~4.5x the prediction, so machine callers skip it.
    """
    row, warnings = build_row(
        req, None if source == "sim" and req.distance is not None else lookup, model.vocab
    )
    X = F.to_model_frame(pd.DataFrame([row]), model.vocab)
    p = float(model.booster.predict(X, num_threads=1)[0])
    reasons: list[tuple[str, float]] = []
    if explain:
        contrib = model.booster.predict(X, pred_contrib=True, num_threads=1)[0][:-1]  # drop bias
        order = np.argsort(-np.abs(contrib))[:TOP_REASONS]
        reasons = [
            (f"{F.FEATURES[i]}={_fmt(row[F.FEATURES[i]])}", round(float(contrib[i]), 4))
            for i in order
        ]
    return row, warnings, p, reasons


def _fmt(v) -> str:
    if isinstance(v, float):
        return "null" if np.isnan(v) else f"{v:g}"
    return str(v)


def run() -> int:
    """Production wiring: Postgres lookup + logger, the pinned version from MLflow."""
    import uvicorn

    from driftops import bundle, db, log, registry
    from driftops.serving.features import PostgresLookup
    from driftops.serving.logger import postgres_sink

    s = Settings.from_env()
    lg = log.setup("server")
    lookup_pool = db.pool(s.db_url, max_size=4, timeout_ms=s.lookup_timeout_ms)
    log_pool = db.pool(s.db_url, max_size=2, timeout_ms=10_000)
    logger = PredictionLogger(postgres_sink(log_pool), maxsize=s.log_queue_size)

    def load_model() -> Model:
        registry.wait_for_version(s.model_name, s.model_version, uri=s.mlflow_uri, log=lg)
        path = registry.download(s.model_name, s.model_version, uri=s.mlflow_uri)
        return bundle.load_bundle(path)

    app = create_app(s, load_model=load_model, lookup=PostgresLookup(lookup_pool), logger=logger)
    lg.info("server_starting", port=s.port, model_version=s.model_version)
    uvicorn.run(app, host="0.0.0.0", port=s.port, access_log=False, log_config=None)
    return 0
