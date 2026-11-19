"""Create the PokeScan database and its tables.

Safe to run more than once: an existing database and existing tables are
left as they are.

Run from the backend folder:
    python -m app.init_db
"""

import logging
import sys
from pathlib import Path

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict

from app.config import settings
from app.db import CONNECTION_HINT, connect

log = logging.getLogger("init_db")

SCHEMA_FILE = Path(__file__).resolve().parents[2] / "db" / "schema.sql"


def database_name() -> str:
    """Name of the database in DATABASE_URL."""
    name = conninfo_to_dict(settings.database_url).get("dbname")
    if not name:
        raise ValueError("DATABASE_URL must end with the database name, e.g. /pokescan")
    return name


def create_database_if_missing(name: str) -> bool:
    """Create the database; return False if it already existed."""
    # The "postgres" database always exists, so we connect there to create ours.
    # CREATE DATABASE can't run inside a transaction, hence autocommit.
    with connect(dbname="postgres", autocommit=True) as conn:
        query = "SELECT 1 FROM pg_database WHERE datname = %s"
        if conn.execute(query, (name,)).fetchone():
            return False
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        return True


def apply_schema() -> None:
    """Run db/schema.sql, which only creates what is missing."""
    with connect() as conn:
        conn.execute(SCHEMA_FILE.read_text(encoding="utf-8"))


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    name = database_name()
    try:
        created = create_database_if_missing(name)
    except psycopg.OperationalError as error:
        log.error("%s\n%s", CONNECTION_HINT, error)
        return 1
    log.info("%s database %s", "Created" if created else "Found existing", name)

    apply_schema()
    log.info("Tables ready")
    return 0


if __name__ == "__main__":
    sys.exit(main())
