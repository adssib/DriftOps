"""Model bundles in the MLflow registry.

A bundle is logged as run artifacts and registered as a generic model version pointing at them,
so the registry tracks versions and the `champion` alias without needing an MLflow model
flavour. Serving pins an explicit version (ADR-0013); the alias is for humans and dashboards.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import time
from pathlib import Path

import mlflow
from mlflow.tracking import MlflowClient

CHAMPION = "champion"


def client(uri: str | None = None) -> MlflowClient:
    if uri:
        mlflow.set_tracking_uri(uri)
    return MlflowClient()


def register(
    bundle_dir: str | Path, name: str, *, tags: dict | None = None, uri: str | None = None
) -> int:
    """Log the bundle as artifacts of a new run and register it. Returns the version number."""
    c = client(uri)
    mlflow.set_experiment(name)
    with mlflow.start_run(run_name=f"register-{name}") as run:
        mlflow.log_artifacts(str(bundle_dir), artifact_path="bundle")
        for k, v in (tags or {}).items():
            mlflow.set_tag(k, v)
        source = f"{run.info.artifact_uri}/bundle"
    try:
        c.get_registered_model(name)
    except mlflow.exceptions.MlflowException:
        c.create_registered_model(name)
    mv = c.create_model_version(name=name, source=source, run_id=run.info.run_id, tags=tags)
    return int(mv.version)


def versions(name: str, uri: str | None = None) -> list[int]:
    c = client(uri)
    try:
        return sorted(int(v.version) for v in c.search_model_versions(f"name='{name}'"))
    except mlflow.exceptions.MlflowException:
        return []


def set_champion(name: str, version: int, uri: str | None = None) -> None:
    client(uri).set_registered_model_alias(name, CHAMPION, str(version))


def download(
    name: str, version: int, dest: str | Path | None = None, uri: str | None = None
) -> Path:
    c = client(uri)
    mv = c.get_model_version(name, str(version))
    dest = Path(dest or tempfile.mkdtemp(prefix=f"{name}-v{version}-"))
    path = mlflow.artifacts.download_artifacts(artifact_uri=mv.source, dst_path=str(dest))
    return Path(path)


def wait_for_version(
    name: str,
    version: int,
    *,
    uri: str | None = None,
    timeout_s: float = 600,
    interval_s: float = 5,
    get=None,
    log=None,
) -> None:
    """Block until the registry is reachable and holds `version`.

    The server starts alongside MLflow and the seed Job; retrying here instead of crashing keeps
    the pod out of a crash loop while the rest of the system comes up.
    """
    get = get or (lambda: client(uri).get_model_version(name, str(version)))
    deadline = time.monotonic() + timeout_s
    attempt = 0
    while True:
        attempt += 1
        try:
            get()
            return
        except Exception as e:  # registry not up yet, or version not registered yet
            if time.monotonic() >= deadline:
                raise TimeoutError(f"{name} v{version} not available after {timeout_s:.0f}s") from e
            if log:
                log.info("waiting_for_model", version=version, attempt=attempt, error=str(e)[:200])
            time.sleep(interval_s)


def serve(port: int = 5000) -> int:
    """Run the MLflow server from this image (ADR-0009, ADR-0012).

    Backend store and artifact destination come from the environment; allowed hosts must list
    the in-cluster service names, or MLflow's host check rejects requests.
    """
    e = os.environ
    cmd = [
        "mlflow",
        "server",
        "--host",
        "0.0.0.0",
        "--port",
        str(port),
        "--backend-store-uri",
        e["MLFLOW_BACKEND_STORE_URI"],
        "--artifacts-destination",
        e.get("MLFLOW_ARTIFACTS_DESTINATION", "/mlartifacts"),
        "--serve-artifacts",
        "--allowed-hosts",
        e.get("MLFLOW_ALLOWED_HOSTS", "localhost:*,127.0.0.1:*"),
        "--workers",
        e.get("MLFLOW_WORKERS", "2"),
    ]
    return subprocess.call(cmd)
