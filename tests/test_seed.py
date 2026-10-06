import numpy as np
import pandas as pd
import pytest

from driftops import seed


def raw_rows():
    return pd.DataFrame(
        {
            "FlightDate": pd.to_datetime(["2020-03-01", "2020-03-01", "2020-03-02"]),
            "Reporting_Airline": pd.Categorical(["AA", "DL", "AA"]),
            "Flight_Number_Reporting_Airline": [10, 20, 30],
            "Origin": pd.Categorical(["ORD", "ATL", "ORD"]),
            "Dest": pd.Categorical(["LGA", "SEA", "LGA"]),
            "OriginState": pd.Categorical(["IL", "GA", "IL"]),
            "DestState": pd.Categorical(["NY", "WA", "NY"]),
            "CRSDepTime": np.array([830, 1715, 2340], dtype="int16"),
            "CRSArrTime": np.array([1140, 1930, 255], dtype="int16"),
            "CRSElapsedTime": np.array([130.0, 315.0, 135.0], dtype="float32"),
            "Distance": np.array([733.0, 2182.0, 733.0], dtype="float32"),
            "DepDelay": [0.0, 45.0, np.nan],
            "ArrDelay": [-5.0, 50.0, np.nan],
            "ArrDel15": [0.0, 1.0, np.nan],
            "Cancelled": [0.0, 0.0, 1.0],
            "Diverted": [0.0, 0.0, 0.0],
        }
    )


def test_prepare_month_ids_labels_and_known_at():
    flights, outcomes = seed.prepare_month(raw_rows(), 2020, 3)
    assert flights["flight_id"].tolist() == [202003000000, 202003000001, 202003000002]
    assert outcomes["disrupted"].tolist() == [0, 1, 1]  # late, and cancelled
    # on time (early arrivals don't count as earlier knowledge): scheduled arrival
    assert outcomes["known_at"].iloc[0] == pd.Timestamp("2020-03-01 08:30") + pd.Timedelta(
        minutes=130
    )
    # 50 min late: known 50 min after the scheduled arrival
    assert outcomes["known_at"].iloc[1] == pd.Timestamp("2020-03-01 17:15") + pd.Timedelta(
        minutes=365
    )
    # cancelled: known at the scheduled arrival
    assert outcomes["known_at"].iloc[2] == pd.Timestamp("2020-03-02 23:40") + pd.Timedelta(
        minutes=135
    )


def test_sample_bucket_is_stable_and_spread():
    ids = np.arange(202001000000, 202001000000 + 100_000, dtype=np.int64)
    a, b = seed.sample_bucket(ids), seed.sample_bucket(ids)
    assert (a == b).all()
    share = (a < 5).mean()
    assert share == pytest.approx(0.05, abs=0.005)


def test_seed_is_idempotent(db_url, tmp_path, monkeypatch):
    from driftops import db

    path = tmp_path / "2020-03.parquet"
    raw_rows().to_parquet(path)
    lg = __import__("structlog").get_logger()
    with db.connect(db_url) as conn:
        db.apply_schema(conn)
        conn.autocommit = True
        cutoff = pd.Timestamp("2020-03-02")
        assert seed.seed_month(conn, path, cutoff, lg) == 3
        assert seed.seed_month(conn, path, cutoff, lg) == 0
        counts = [
            conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
            for t in ("flights", "outcomes_feed", "outcomes")
        ]
    assert counts == [3, 3, 2]  # two outcomes known before the cutoff
