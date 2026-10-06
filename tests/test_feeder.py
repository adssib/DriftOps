import datetime as dt

import structlog

from driftops import db, feeder

LG = structlog.get_logger()


def setup(conn):
    db.apply_schema(conn)
    conn.autocommit = True
    rows = [
        (1, 0, dt.datetime(2020, 3, 10, 9)),
        (2, 1, dt.datetime(2020, 3, 10, 18)),
        (3, 1, dt.datetime(2020, 3, 11, 9)),
    ]
    with conn.cursor() as cur:
        cur.executemany("INSERT INTO outcomes_feed VALUES (%s, %s, %s)", rows)


def clock(conn, now):
    conn.execute(
        "INSERT INTO sim_clock (id, now, scenario, segment) VALUES (1, %s, 's', 'x') "
        "ON CONFLICT (id) DO UPDATE SET now = EXCLUDED.now",
        (now,),
    )


def test_delivers_only_what_is_due_and_is_idempotent(db_url):
    with db.connect(db_url) as conn:
        setup(conn)
        assert feeder.feed(conn, LG) == 0  # no clock yet
        clock(conn, dt.datetime(2020, 3, 10, 12))
        assert feeder.feed(conn, LG) == 1
        assert feeder.feed(conn, LG) == 0  # nothing new: same clock
        clock(conn, dt.datetime(2020, 3, 12))
        assert feeder.feed(conn, LG) == 2
        ids = [r[0] for r in conn.execute("SELECT flight_id FROM outcomes ORDER BY 1")]
    assert ids == [1, 2, 3]


def test_rewound_clock_delivers_nothing(db_url):
    with db.connect(db_url) as conn:
        setup(conn)
        clock(conn, dt.datetime(2020, 3, 12))
        assert feeder.feed(conn, LG) == 3
        clock(conn, dt.datetime(2020, 3, 10, 12))  # a new run starts earlier
        assert feeder.feed(conn, LG) == 0
