"""The label feeder: outcomes arrive late (SPEC F5).

Every minute, moves the outcomes whose `known_at` has passed on the simulated clock from
`outcomes_feed` (every flight's outcome, loaded by the seed) to `outcomes` (what the system has
been told so far). The move and the watermark commit together, so a retried run never loses or
duplicates a label. If the clock rewinds (a new run), nothing new is due; the monitors filter
labels by `known_at`, so they never see an outcome before its time either way.

    python -m driftops label-feed
"""

from __future__ import annotations

import datetime as dt

from driftops import db, log
from driftops.config import Settings

WATERMARK = "label-feed"
# before any outcome; psycopg can't load '-infinity' as a datetime. Naive like all BTS times.
EPOCH = dt.datetime(1970, 1, 1)  # noqa: DTZ001

MOVE_SQL = """
INSERT INTO outcomes (flight_id, disrupted, known_at)
SELECT flight_id, disrupted, known_at FROM outcomes_feed
WHERE known_at > %s AND known_at <= %s
ON CONFLICT (flight_id) DO NOTHING
"""


def feed(conn, lg) -> int:
    clock = db.sim_clock(conn)
    if clock is None:
        lg.info("feed_skipped", reason="no clock yet")
        return 0
    now = clock[0]
    with conn.transaction():
        since = db.get_watermark(conn, WATERMARK)
        if since is None:
            # first run: everything the seed already delivered is behind us
            since = conn.execute("SELECT max(known_at) FROM outcomes").fetchone()[0] or EPOCH
        if since >= now:
            lg.info("feed_idle", now=str(now), watermark=str(since))
            return 0
        moved = conn.execute(MOVE_SQL, (since, now)).rowcount
        db.set_watermark(conn, WATERMARK, now)
    lg.info("feed_done", moved=moved, since=str(since), until=str(now))
    return moved


def run() -> int:
    s = Settings.from_env()
    lg = log.setup("label-feed")
    with db.connect(s.db_url) as conn:
        conn.autocommit = True
        feed(conn, lg)
    return 0
