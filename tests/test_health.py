from __future__ import annotations

from src import database


def test_health_reports_healthy_when_the_database_answers(client):
    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "healthy"
    assert body["components"]["database"] == "connected"
    assert body["version"]


def test_health_degrades_when_the_database_is_unreachable(client, monkeypatch):
    """The check must reflect reality, not return a constant."""
    monkeypatch.setattr("src.routes.health.check_connection", lambda: False)

    body = client.get("/health").json()

    assert body["status"] == "degraded"
    assert body["components"]["database"] == "unavailable"


def test_health_timestamp_is_timezone_aware(client):
    from datetime import datetime

    timestamp = datetime.fromisoformat(client.get("/health").json()["timestamp"])
    assert timestamp.tzinfo is not None


def test_connection_check_returns_false_on_a_bad_url(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///Z:/nonexistent/path/x.db")
    from src.config import get_settings

    get_settings.cache_clear()
    database.get_engine.cache_clear()

    assert database.check_connection() is False
