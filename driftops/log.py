"""One JSON log schema for every component (OPERATIONS § 6).

Keys: ts, level, component, event, plus whatever is bound: model_version, sim_day, request_id,
decision_id. Loki indexes only low-cardinality labels; IDs stay inside the line.
"""

from __future__ import annotations

import logging
import sys

import structlog


def setup(component: str, level: str = "INFO") -> structlog.stdlib.BoundLogger:
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level)
    for noisy in ("uvicorn.access", "httpx", "alembic", "mlflow"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", key="ts"),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(level)),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )
    structlog.contextvars.bind_contextvars(component=component)
    return structlog.get_logger()
