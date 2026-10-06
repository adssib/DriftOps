"""Postgres access: one pool per process, the schema, bulk COPY. Parameterised SQL only."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from importlib import resources

import psycopg
from psycopg_pool import ConnectionPool


def pool(
    url: str, *, min_size: int = 1, max_size: int = 4, timeout_ms: int | None = None
) -> ConnectionPool:
    """A connection pool. `timeout_ms` sets statement_timeout on every connection, so a slow
    query fails fast instead of holding a request."""
    kwargs = {}
    if timeout_ms:
        kwargs["options"] = f"-c statement_timeout={int(timeout_ms)}"
    return ConnectionPool(url, min_size=min_size, max_size=max_size, kwargs=kwargs, open=True)


def connect(url: str) -> psycopg.Connection:
    return psycopg.connect(url, autocommit=False)


def schema_sql() -> str:
    return resources.files("driftops.sql").joinpath("schema.sql").read_text()


def apply_schema(conn: psycopg.Connection) -> None:
    conn.execute(schema_sql())
    conn.commit()


def copy_rows(
    conn: psycopg.Connection, table: str, columns: Sequence[str], rows: Iterable[Sequence]
) -> int:
    """COPY rows into a table. Table and column names are code constants, never user input."""
    n = 0
    cols = ", ".join(columns)
    with conn.cursor() as cur, cur.copy(f"COPY {table} ({cols}) FROM STDIN") as cp:
        for row in rows:
            cp.write_row(row)
            n += 1
    return n


def copy_frame(conn: psycopg.Connection, table: str, df) -> int:
    """COPY a DataFrame as CSV (NaN → NULL). Orders of magnitude faster than row-by-row."""
    cols = ", ".join(df.columns)
    with (
        conn.cursor() as cur,
        cur.copy(f"COPY {table} ({cols}) FROM STDIN WITH (FORMAT csv)") as cp,
    ):
        step = 200_000
        for start in range(0, len(df), step):
            cp.write(df.iloc[start : start + step].to_csv(index=False, header=False))
    return len(df)
