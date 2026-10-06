import datetime as dt

import pytest

from driftops import simulator as S

BASE = {
    "flight_id": 202003000004,
    "carrier": "AA",
    "origin": "ORD",
    "dest": "LGA",
    "flight_date": "2020-03-27",
    "dep_time": "18:05",
    "distance": 733.0,
    "sched_minutes": 130.0,
    "origin_hour_load": 20.0,
    "dest_hour_load": 10.0,
}


def test_scenarios_in_repo_load():
    covid = S.load_scenario("scenarios/covid.yaml")
    days = list(covid.days())
    assert days[0][0] == dt.date(2020, 3, 10) and days[-1][0] == dt.date(2020, 5, 31)
    corrupt = S.load_scenario("scenarios/corruption.yaml")
    km = [d for d, seg in corrupt.days() if seg.transform == "distance_km"]
    assert len(km) == 14


def test_gap_between_segments_is_a_clock_jump(tmp_path):
    p = tmp_path / "s.yaml"
    p.write_text(
        "name: s\nsegments:\n  - {name: a, start: 2020-01-01, end: 2020-01-02}\n"
        "  - {name: b, start: 2020-03-01, end: 2020-03-01}\n"
    )
    assert [d.isoformat() for d, _ in S.load_scenario(p).days()] == [
        "2020-01-01",
        "2020-01-02",
        "2020-03-01",
    ]


@pytest.mark.parametrize(
    "body,error",
    [
        (
            "name: s\nsegments:\n  - {name: a, start: 2020-01-02, end: 2020-01-01}\n",
            "end before start",
        ),
        (
            (
                "name: s\nsegments:\n  - {name: a, start: 2020-01-01, end: 2020-01-05}\n"
                "  - {name: b, start: 2020-01-05, end: 2020-01-06}\n"
            ),
            "overlap",
        ),
        (
            "name: s\nsegments:\n  - {name: a, start: 2020-01-01, end: 2020-01-01, transform: nope}\n",
            "unknown transform",
        ),
        (
            "name: s\nsample_pct: 0\nsegments:\n  - {name: a, start: 2020-01-01, end: 2020-01-01}\n",
            "sample_pct",
        ),
    ],
)
def test_bad_scenarios_rejected(tmp_path, body, error):
    p = tmp_path / "s.yaml"
    p.write_text(body)
    with pytest.raises(ValueError, match=error):
        S.load_scenario(p)


def test_distance_km():
    assert S.distance_km(dict(BASE))["distance"] == pytest.approx(1179.4)


def test_null_load_hits_about_30_percent_deterministically():
    hit = [S.null_load({**BASE, "flight_id": i})["origin_hour_load"] is None for i in range(1000)]
    assert sum(hit) == 300
    assert S.null_load({**BASE, "flight_id": 3})["origin_hour_load"] == 20.0


def test_unseen_carrier_stays_well_formed():
    from driftops.serving.schemas import PredictRequest

    p = S.unseen_carrier(dict(BASE))
    assert p["carrier"] == "XA"
    PredictRequest(**p)  # passes validation: the guard, not the validator, must catch it
    assert S.unseen_carrier({**BASE, "carrier": "DL"})["carrier"] == "DL"


def test_offsets_spread_evenly():
    assert S.offsets(4, 10) == [0, 2.5, 5.0, 7.5]
    assert S.offsets(0, 10) == []


def test_payload_from_flights_row():
    row = {
        "flight_id": 1,
        "flight_date": dt.date(2020, 3, 27),
        "carrier": "AA",
        "origin": "ORD",
        "dest": "LGA",
        "event_time": dt.datetime(2020, 3, 27, 6, 5),
        "distance": 733.0,
        "sched_minutes": 130.0,
        "origin_hour_load": 5.0,
        "dest_hour_load": 7.0,
    }
    p = S.payload(row)
    assert p["dep_time"] == "06:05" and p["flight_date"] == "2020-03-27"


def test_replay_reconnects_every_simulated_day():
    """Load balancing in Kubernetes is per connection: a new pool per day spreads load."""
    import asyncio

    import structlog

    class FakeClient:
        made = 0

        def __init__(self):
            FakeClient.made += 1
            self.closed = False

        async def post(self, *a, **k):
            return type("R", (), {"status_code": 200})()

        async def aclose(self):
            self.closed = True

    class FakeConn:
        def execute(self, *a):
            pass

    rows = [
        {
            "flight_id": i,
            "flight_date": dt.date(2020, 3, 10),
            "carrier": "AA",
            "origin": "ORD",
            "dest": "LGA",
            "event_time": dt.datetime(2020, 3, 10, 8, 0),
            "distance": 733.0,
            "sched_minutes": 130.0,
            "origin_hour_load": 5.0,
            "dest_hour_load": 7.0,
        }
        for i in range(3)
    ]
    scenario = S.Scenario(
        "t", 0.01, 5, (S.Segment("a", dt.date(2020, 3, 10), dt.date(2020, 3, 12)),)
    )
    status = S.Status()
    orig = S._fetch_day
    S._fetch_day = lambda conn, day, pct: rows
    try:
        asyncio.run(S.replay(scenario, FakeConn(), FakeClient, status, structlog.get_logger()))
    finally:
        S._fetch_day = orig
    assert FakeClient.made == 3  # one pool per simulated day
    assert status.sent == 9 and status.codes[200] == 9 and status.done
