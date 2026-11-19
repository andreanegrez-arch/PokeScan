import psycopg
import pytest
from psycopg import sql

from app.db import connect
from app.init_db import SCHEMA_FILE

TEST_DB = "pokescan_test"


@pytest.fixture
def db():
    """A connection to a throwaway database (pokescan_test) with the schema applied.

    The database is created before each test and dropped after it; the test is
    skipped when PostgreSQL can't be reached. `db.info.dbname` gives its name,
    for code that opens its own connections.
    """
    try:
        admin = connect(dbname="postgres", autocommit=True, connect_timeout=3)
    except psycopg.OperationalError:
        pytest.skip("PostgreSQL is not reachable")
    with admin:
        admin.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(TEST_DB)))
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(TEST_DB)))

    with connect(dbname=TEST_DB) as conn:
        conn.execute(SCHEMA_FILE.read_text(encoding="utf-8"))
        conn.commit()
        yield conn

    with connect(dbname="postgres", autocommit=True) as admin:
        admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(TEST_DB)))
