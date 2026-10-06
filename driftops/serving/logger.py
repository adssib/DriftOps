"""Prediction logging that never blocks or fails a prediction (SPEC F2).

Requests put records on a bounded in-process queue and return immediately. A background thread
drains it in batches (by size or by time) into a sink, Postgres COPY in production. A full
queue drops the record; a failing sink drops the batch. Both are counted, never raised: losing
a log line is a monitoring problem, failing a prediction is an outage.
"""

from __future__ import annotations

import json
import queue
import threading
import time
from collections.abc import Callable

COLUMNS = [
    "request_id",
    "flight_id",
    "source",
    "event_time",
    "model_version",
    "features",
    "score",
    "latency_ms",
]

Sink = Callable[[list[dict]], None]


class PredictionLogger:
    def __init__(
        self,
        sink: Sink,
        *,
        maxsize: int = 20_000,
        batch_size: int = 500,
        flush_interval: float = 0.5,
        on_logged: Callable[[int], None] | None = None,
        on_dropped: Callable[[int, str], None] | None = None,
    ):
        self.sink = sink
        self.q: queue.Queue = queue.Queue(maxsize=maxsize)
        self.batch_size = batch_size
        self.flush_interval = flush_interval
        self.on_logged = on_logged or (lambda n: None)
        self.on_dropped = on_dropped or (lambda n, reason: None)
        self.logged = 0
        self.dropped = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # request path: must never block
    def log(self, record: dict) -> bool:
        try:
            self.q.put_nowait(record)
            return True
        except queue.Full:
            self._drop(1, "queue_full")
            return False

    def fill_ratio(self) -> float:
        return self.q.qsize() / self.q.maxsize if self.q.maxsize else 0.0

    # background
    def start(self) -> PredictionLogger:
        self._thread = threading.Thread(target=self._run, name="prediction-logger", daemon=True)
        self._thread.start()
        return self

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout)

    def _run(self) -> None:
        while not self._stop.is_set() or not self.q.empty():
            batch = self._collect()
            if batch:
                self._flush(batch)

    def _collect(self) -> list[dict]:
        batch: list[dict] = []
        deadline = time.monotonic() + self.flush_interval
        while len(batch) < self.batch_size:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                batch.append(self.q.get(timeout=min(remaining, 0.1)))
            except queue.Empty:
                if self._stop.is_set():
                    break
        return batch

    def _flush(self, batch: list[dict]) -> None:
        try:
            self.sink(batch)
            self.logged += len(batch)
            self.on_logged(len(batch))
        except Exception:  # noqa: BLE001 - a lost log batch must never reach the request path
            self._drop(len(batch), "sink_error")

    def _drop(self, n: int, reason: str) -> None:
        self.dropped += n
        self.on_dropped(n, reason)


def postgres_sink(pool) -> Sink:
    """COPY a batch into `predictions` (lands in the right daily partition by event_time)."""

    def sink(batch: list[dict]) -> None:
        sql = f"COPY predictions ({', '.join(COLUMNS)}) FROM STDIN"
        with pool.connection() as conn, conn.cursor() as cur, cur.copy(sql) as cp:
            for r in batch:
                cp.write_row(
                    (
                        r["request_id"],
                        r.get("flight_id"),
                        r["source"],
                        r["event_time"],
                        r["model_version"],
                        json.dumps(r["features"], default=_json_default),
                        r["score"],
                        r.get("latency_ms"),
                    )
                )

    return sink


def _json_default(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return str(x)
