# syntax=docker/dockerfile:1
# One image, one CLI (ADR-0009): server, simulator, seed, MLflow, migrations, every CronJob.

FROM python:3.12-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
# Dependencies only. The project itself is NOT installed as a wheel: uv caches built wheels by
# version, and with an unchanging version (0.1.0) it reused a stale one, so new images ran old
# code. The source is copied into the runtime image instead (see `make image` / CI: the image's
# source is checked against the commit after every build).
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-install-project

FROM python:3.12-slim
# libgomp: LightGBM's OpenMP runtime
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin driftops
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
COPY driftops ./driftops
COPY scenarios ./scenarios
# HOME=/tmp: MLflow and friends write caches there; the root filesystem is read-only in the pod
ENV PATH="/app/.venv/bin:$PATH" PYTHONPATH=/app PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    MLFLOW_DISABLE_AGENT_HINT=1 HOME=/tmp
# Last, so a new commit only rebuilds this layer: what code is running is always answerable
ARG GIT_SHA=unknown
ENV DRIFTOPS_GIT_SHA=${GIT_SHA}
USER 10001
EXPOSE 8000
ENTRYPOINT ["python", "-m", "driftops"]
CMD ["serve"]
