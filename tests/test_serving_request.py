import datetime as dt

import numpy as np
import pytest
from pydantic import ValidationError

from driftops.serving.features import build_row, event_time
from driftops.serving.schemas import PredictRequest, check_user_date, risk_band

VOCAB = {"carrier": ["AA", "DL"], "origin": ["ORD", "ATL"], "dest": ["LGA", "SEA"]}
GOOD = {
    "carrier": "AA",
    "origin": "ORD",
    "dest": "LGA",
    "flight_date": "2020-03-27",
    "dep_time": "18:05",
}


class FakeLookup:
    def route(self, o, d):
        return (733.0, 130.0)

    def origin_load(self, day, a, h):
        return 42.0

    def dest_load(self, day, a, h):
        return 17.0


class BrokenLookup:
    def route(self, o, d):
        raise TimeoutError("statement timeout")

    origin_load = dest_load = route


@pytest.mark.parametrize(
    "patch",
    [
        {"carrier": "aa"},  # lowercase
        {"carrier": "AAL"},  # three characters
        {"origin": "OR"},
        {"dest": "ORD"},  # same as origin
        {"dep_time": "24:00"},
        {"dep_time": "7:5"},
        {"distance": 0},
        {"distance": 99999},
        {"flight_date": "not-a-date"},
        {"source": "sim"},  # identity decides source (ADR-0019)
        {"anything": 1},
    ],
)
def test_rejects_malformed(patch):
    with pytest.raises(ValidationError):
        PredictRequest(**{**GOOD, **patch})


def test_accepts_good_request():
    r = PredictRequest(**GOOD)
    assert r.carrier == "AA" and r.flight_date == dt.date(2020, 3, 27)
    assert event_time(r) == dt.datetime(2020, 3, 27, 18, 5)


def test_user_date_range():
    check_user_date(PredictRequest(**GOOD))
    with pytest.raises(ValueError):
        check_user_date(PredictRequest(**{**GOOD, "flight_date": "2021-01-01"}))


def test_fills_schedule_features_from_lookup():
    row, warnings = build_row(PredictRequest(**GOOD), FakeLookup(), VOCAB)
    assert row["distance"] == 733.0 and row["sched_minutes"] == 130.0
    assert row["arr_hour"] == 20  # 18:05 + 130 min
    assert row["origin_hour_load"] == 42.0 and row["dest_hour_load"] == 17.0
    assert row["month"] == 3 and row["day_of_week"] == 4  # a Friday
    assert warnings == []


def test_sent_features_win_over_lookup():
    row, _ = build_row(
        PredictRequest(**{**GOOD, "distance": 1000.0, "sched_minutes": 60.0}), FakeLookup(), VOCAB
    )
    assert row["distance"] == 1000.0 and row["arr_hour"] == 19


def test_flags_unseen_codes_without_rejecting():
    row, warnings = build_row(PredictRequest(**{**GOOD, "carrier": "ZZ"}), FakeLookup(), VOCAB)
    assert row["carrier"] == "ZZ"
    assert "unseen carrier" in warnings


def test_degrades_when_lookup_fails():
    row, warnings = build_row(PredictRequest(**GOOD), BrokenLookup(), VOCAB)
    assert np.isnan(row["distance"]) and np.isnan(row["origin_hour_load"])
    assert "degraded: schedule lookup failed" in warnings


def test_risk_bands():
    assert [risk_band(p) for p in (0.1, 0.25, 0.6)] == ["low", "elevated", "high"]
