-- DriftOps storage (docs/SPEC.md § 5). Idempotent: safe to apply on every seed run.
-- Times are the airport-local scheduled times BTS publishes; event time = scheduled departure.

CREATE TABLE IF NOT EXISTS flights (
    flight_id        bigint PRIMARY KEY,
    flight_date      date      NOT NULL,
    carrier          text      NOT NULL,
    flight_number    integer,
    origin           text      NOT NULL,
    dest             text      NOT NULL,
    event_time       timestamp NOT NULL,
    dep_hour         smallint  NOT NULL,
    arr_hour         smallint  NOT NULL,
    distance         real,
    sched_minutes    real,
    origin_hour_load real,
    dest_hour_load   real,
    month            smallint  NOT NULL,
    day_of_week      smallint  NOT NULL,
    days_to_holiday  smallint  NOT NULL,
    sample_bucket    smallint  NOT NULL   -- stable hash % 100: the simulator's traffic sample
);
-- schedule-feature lookups for user requests; the simulator's per-day scan uses the prefix
CREATE INDEX IF NOT EXISTS flights_origin_lookup ON flights (flight_date, origin, dep_hour);
CREATE INDEX IF NOT EXISTS flights_dest_lookup   ON flights (flight_date, dest, arr_hour);

CREATE TABLE IF NOT EXISTS routes (
    origin        text NOT NULL,
    dest          text NOT NULL,
    distance      real NOT NULL,
    sched_minutes real NOT NULL,
    PRIMARY KEY (origin, dest)
);

CREATE TABLE IF NOT EXISTS outcomes_feed (
    flight_id bigint    PRIMARY KEY,
    disrupted smallint  NOT NULL,
    known_at  timestamp NOT NULL           -- actual arrival; scheduled arrival if cancelled
);
CREATE INDEX IF NOT EXISTS outcomes_feed_known_at ON outcomes_feed (known_at);

CREATE TABLE IF NOT EXISTS outcomes (
    flight_id   bigint      PRIMARY KEY,
    disrupted   smallint    NOT NULL,
    known_at    timestamp   NOT NULL,
    ingested_at timestamptz NOT NULL DEFAULT now()
);

-- Append-only, time-ordered, read in time windows: daily partitions + BRIN (SPEC § 5).
CREATE TABLE IF NOT EXISTS predictions (
    request_id    uuid        NOT NULL,
    flight_id     bigint,
    source        text        NOT NULL CHECK (source IN ('sim', 'user')),
    event_time    timestamp   NOT NULL,
    model_version integer     NOT NULL,
    features      jsonb       NOT NULL,
    score         real        NOT NULL,
    latency_ms    real,
    created_at    timestamptz NOT NULL DEFAULT now()
) PARTITION BY RANGE (event_time);

DO $$
DECLARE d date;
BEGIN
    FOR d IN SELECT generate_series('2020-01-01'::date, '2020-06-30'::date, '1 day')::date LOOP
        EXECUTE format(
            'CREATE TABLE IF NOT EXISTS %I PARTITION OF predictions FOR VALUES FROM (%L) TO (%L)',
            'predictions_' || to_char(d, 'YYYYMMDD'), d, d + 1);
    END LOOP;
END $$;
CREATE TABLE IF NOT EXISTS predictions_default PARTITION OF predictions DEFAULT;

CREATE INDEX IF NOT EXISTS predictions_event_brin ON predictions USING brin (event_time);
CREATE INDEX IF NOT EXISTS predictions_sim_window ON predictions (event_time) WHERE source = 'sim';
CREATE INDEX IF NOT EXISTS predictions_flight     ON predictions (flight_id);
CREATE INDEX IF NOT EXISTS predictions_request    ON predictions (request_id);

CREATE TABLE IF NOT EXISTS monitor_results (
    id            bigserial   PRIMARY KEY,
    monitor       text        NOT NULL CHECK (monitor IN ('quality', 'drift', 'perf')),
    window_start  timestamp   NOT NULL,
    window_end    timestamp   NOT NULL,
    model_version integer     NOT NULL,
    metrics       jsonb       NOT NULL,
    alarm         boolean     NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (monitor, window_end, model_version)   -- idempotent upserts from retried CronJobs
);

CREATE TABLE IF NOT EXISTS loop_events (
    id            bigserial   PRIMARY KEY,
    sim_time      timestamp,
    kind          text        NOT NULL,
    model_version integer,
    decision_id   text,
    details       jsonb       NOT NULL DEFAULT '{}',
    created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS loop_events_created ON loop_events (created_at);
CREATE INDEX IF NOT EXISTS loop_events_kind    ON loop_events (kind, created_at);

CREATE TABLE IF NOT EXISTS sim_clock (
    id         smallint    PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    now        timestamp   NOT NULL,
    scenario   text        NOT NULL,
    segment    text        NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS watermarks (
    name       text        PRIMARY KEY,
    value      timestamp   NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);
