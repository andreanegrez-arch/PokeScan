from typing import Any

import psycopg

from app.config import settings

CONNECT_TIMEOUT_SECONDS = 10

# Shown when the database can't be reached, in place of a bare driver error.
CONNECTION_HINT = (
    "Can't connect to PostgreSQL. Is the server running? "
    "Check DATABASE_URL and DATABASE_PASSWORD in backend/.env."
)


def connect(**options: Any) -> psycopg.Connection:
    """Open a new connection to PostgreSQL.

    `options` override parts of DATABASE_URL (e.g. dbname="postgres") or set
    connection options (e.g. autocommit=True).
    """
    if settings.database_password:
        options.setdefault("password", settings.database_password)
    # Without a timeout, connecting to a server that is not running can hang
    # forever on Windows instead of failing.
    options.setdefault("connect_timeout", CONNECT_TIMEOUT_SECONDS)
    return psycopg.connect(settings.database_url, **options)
