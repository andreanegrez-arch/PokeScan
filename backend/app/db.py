from collections.abc import Iterator
from typing import Annotated, Any

import psycopg
from fastapi import Depends, Request
from psycopg_pool import ConnectionPool

from app.config import settings

CONNECT_TIMEOUT_SECONDS = 10

# Shown when the database can't be reached, in place of a bare driver error.
CONNECTION_HINT = (
    "Can't connect to PostgreSQL. Is the server running? "
    "Check DATABASE_URL and DATABASE_PASSWORD in backend/.env."
)


def _options(options: dict[str, Any]) -> dict[str, Any]:
    if settings.database_password:
        options.setdefault("password", settings.database_password)
    # Without a timeout, connecting to a server that is not running can hang
    # forever on Windows instead of failing.
    options.setdefault("connect_timeout", CONNECT_TIMEOUT_SECONDS)
    return options


def connect(**options: Any) -> psycopg.Connection:
    """Open a new connection to PostgreSQL (for jobs and scripts).

    `options` override parts of DATABASE_URL (e.g. dbname="postgres") or set
    connection options (e.g. autocommit=True).
    """
    return psycopg.connect(settings.database_url, **_options(options))


def create_pool() -> ConnectionPool:
    """Connections shared by the API's requests; open it before use.

    If PostgreSQL is down, requests fail after CONNECT_TIMEOUT_SECONDS with
    psycopg_pool.PoolTimeout (an OperationalError) instead of hanging.
    """
    return ConnectionPool(
        settings.database_url,
        kwargs=_options({}),
        min_size=1,
        max_size=10,
        timeout=CONNECT_TIMEOUT_SECONDS,
        check=ConnectionPool.check_connection,  # drop connections broken by a restart
        open=False,
    )


def get_conn(request: Request) -> Iterator[psycopg.Connection]:
    """FastAPI dependency: a connection from the app's pool for one request."""
    with request.app.state.pool.connection() as conn:
        yield conn


# Type of an endpoint parameter that receives a connection.
DbConnection = Annotated[psycopg.Connection, Depends(get_conn)]
