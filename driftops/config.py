"""Settings from environment variables only (12-factor). No secrets in files."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _list(value: str) -> list[str]:
    return [v.strip() for v in value.split(",") if v.strip()]


@dataclass(frozen=True)
class Settings:
    db_url: str = "postgresql://driftops:driftops@localhost:5432/driftops"
    mlflow_uri: str = "http://localhost:5000"
    model_name: str = "driftops"
    model_version: int = 1
    server_url: str = "http://localhost:8000"
    scenario: str = "scenarios/covid.yaml"
    seed_dir: str = "data"
    sim_token: str = ""  # bearer token that identifies the simulator (ADR-0019)
    cors_origins: list[str] = field(default_factory=list)
    log_queue_size: int = 20_000
    lookup_timeout_ms: int = 200
    port: int = 8000

    @classmethod
    def from_env(cls) -> Settings:
        e = os.environ
        d = cls()
        return cls(
            db_url=e.get("DRIFTOPS_DB_URL", d.db_url),
            mlflow_uri=e.get("MLFLOW_TRACKING_URI", d.mlflow_uri),
            model_name=e.get("DRIFTOPS_MODEL_NAME", d.model_name),
            model_version=int(e.get("DRIFTOPS_MODEL_VERSION", d.model_version)),
            server_url=e.get("DRIFTOPS_SERVER_URL", d.server_url),
            scenario=e.get("DRIFTOPS_SCENARIO", d.scenario),
            seed_dir=e.get("DRIFTOPS_SEED_DIR", d.seed_dir),
            sim_token=e.get("DRIFTOPS_SIM_TOKEN", d.sim_token),
            cors_origins=_list(e.get("DRIFTOPS_CORS_ORIGINS", "")),
            log_queue_size=int(e.get("DRIFTOPS_LOG_QUEUE_SIZE", d.log_queue_size)),
            lookup_timeout_ms=int(e.get("DRIFTOPS_LOOKUP_TIMEOUT_MS", d.lookup_timeout_ms)),
            port=int(e.get("DRIFTOPS_PORT", d.port)),
        )
