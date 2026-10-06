# syntax=docker/dockerfile:1
# One image, one CLI (ADR-0009): server, simulator, seed, MLflow and (later) every Job.

FROM python:3.12-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
# dependencies first: this layer is reused until uv.lock changes
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-install-project
COPY driftops ./driftops
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-editable

FROM python:3.12-slim
# libgomp: LightGBM's OpenMP runtime
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin driftops
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
COPY scenarios ./scenarios
# HOME=/tmp: MLflow and friends write caches there; the root filesystem is read-only in the pod
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    MLFLOW_DISABLE_AGENT_HINT=1 HOME=/tmp
USER 10001
EXPOSE 8000
ENTRYPOINT ["python", "-m", "driftops"]
CMD ["serve"]
