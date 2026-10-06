import os

import pytest


@pytest.fixture
def db_url():
    """A disposable Postgres, e.g. `docker run -p 55432:5432 postgres:17-alpine`. Tests that
    need it are skipped when DRIFTOPS_TEST_DB_URL isn't set."""
    url = os.environ.get("DRIFTOPS_TEST_DB_URL")
    if not url:
        pytest.skip("DRIFTOPS_TEST_DB_URL not set")
    import psycopg

    with psycopg.connect(url, autocommit=True) as c:
        c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    return url
