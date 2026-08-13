from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src import database
from src.config import get_settings
from src.main import app


@pytest.fixture(autouse=True)
def isolated_database(tmp_path, monkeypatch):
    """Point every test at its own SQLite file.

    The engine, session factory and settings are all lru_cached, so the caches
    have to be cleared after the environment changes or the first test would
    pin the real database for the whole run.
    """
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    get_settings.cache_clear()
    database.get_engine.cache_clear()
    database.get_session_factory.cache_clear()
    yield
    get_settings.cache_clear()
    database.get_engine.cache_clear()
    database.get_session_factory.cache_clear()


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client
