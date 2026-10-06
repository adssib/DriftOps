import datetime as dt
import json
import uuid

from driftops import db


def test_schema_applies_twice(db_url):
    with db.connect(db_url) as conn:
        db.apply_schema(conn)
        db.apply_schema(conn)
        n = conn.execute(
            "SELECT count(*) FROM pg_inherits WHERE inhparent = 'predictions'::regclass"
        ).fetchone()[0]
    assert n == 182 + 1  # 2020-01-01 .. 2020-06-30 daily, plus default


def test_prediction_lands_in_its_daily_partition(db_url):
    with db.connect(db_url) as conn:
        db.apply_schema(conn)
        row = (uuid.uuid4(), 1, "sim", dt.datetime(2020, 3, 27, 9, 30), 1, json.dumps({}), 0.3, 1.2)
        db.copy_rows(
            conn,
            "predictions",
            [
                "request_id",
                "flight_id",
                "source",
                "event_time",
                "model_version",
                "features",
                "score",
                "latency_ms",
            ],
            [row],
        )
        conn.commit()
        part = conn.execute("SELECT tableoid::regclass::text FROM predictions").fetchone()[0]
    assert part == "predictions_20200327"


def test_window_query_prunes_partitions(db_url):
    with db.connect(db_url) as conn:
        db.apply_schema(conn)
        plan = "\n".join(
            r[0]
            for r in conn.execute(
                "EXPLAIN SELECT * FROM predictions WHERE source = 'sim' "
                "AND event_time >= '2020-03-20' AND event_time < '2020-03-27'"
            ).fetchall()
        )
    assert "predictions_20200320" in plan
    assert "predictions_20200401" not in plan


def test_sink_writes_run_id_and_nan_as_null(db_url):
    """The real COPY sink: columns line up, and a missing feature (NaN) is stored as null.
    json.dumps would write NaN, which jsonb rejects, dropping the whole batch."""
    import math

    from driftops.serving.logger import postgres_sink

    with db.connect(db_url) as conn:
        db.apply_schema(conn)
    pool = db.pool(db_url)
    record = {
        "request_id": str(uuid.uuid4()),
        "flight_id": 42,
        "source": "sim",
        "run_id": 7,
        "event_time": dt.datetime(2020, 3, 20, 8, 0),
        "model_version": 1,
        "features": {"carrier": "AA", "origin_hour_load": math.nan, "distance": 733.0},
        "score": 0.25,
        "latency_ms": 3.1,
    }
    postgres_sink(pool)([record])
    with db.connect(db_url) as conn:
        row = conn.execute(
            "SELECT run_id, source, features->'origin_hour_load', features->>'carrier' FROM predictions"
        ).fetchone()
    pool.close()
    assert row == (7, "sim", None, "AA")
