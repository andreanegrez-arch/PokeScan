import pytest

from app import init_db
from app.config import settings


def test_database_name_comes_from_the_url(monkeypatch):
    monkeypatch.setattr(settings, "database_url", "postgresql://postgres@localhost:5432/pokescan")

    assert init_db.database_name() == "pokescan"


def test_database_name_is_required(monkeypatch):
    monkeypatch.setattr(settings, "database_url", "postgresql://postgres@localhost:5432")

    with pytest.raises(ValueError, match="database name"):
        init_db.database_name()


def test_schema_file_exists():
    assert init_db.SCHEMA_FILE.is_file()
