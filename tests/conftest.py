from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src import database, dependencies
from src.agents.coach_agent import CoachAgent
from src.config import get_settings
from src.main import app
from src.services.gemini_service import GeminiUnavailableError


class StubGemini:
    """Stands in for the model so the suite never spends quota or touches the network.

    Records what it was asked, so tests can assert on the prompt the coach built
    rather than only on the reply it returned.
    """

    def __init__(self, reply: str = "Sube de veinte a veintidós kilómetros.") -> None:
        self.reply = reply
        self.enabled = True
        self.model = "stub-model"
        self.calls: list[dict] = []
        self.fail_with: Exception | None = None

    async def generate(self, message, system_prompt, history=None):
        self.calls.append(
            {"message": message, "system_prompt": system_prompt, "history": history}
        )
        if self.fail_with is not None:
            raise self.fail_with
        return self.reply


@pytest.fixture(autouse=True)
def isolated_database(tmp_path, monkeypatch):
    """Point every test at its own SQLite file.

    The engine, session factory and settings are all lru_cached, so the caches
    have to be cleared after the environment changes or the first test would
    pin the real database for the whole run.
    """
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    _clear_caches()
    yield
    _clear_caches()


def _clear_caches() -> None:
    get_settings.cache_clear()
    database.get_engine.cache_clear()
    database.get_session_factory.cache_clear()
    dependencies.get_gemini.cache_clear()
    dependencies.get_coach.cache_clear()


@pytest.fixture
def gemini() -> StubGemini:
    return StubGemini()


@pytest.fixture
def unavailable_gemini() -> StubGemini:
    stub = StubGemini()
    stub.enabled = False
    stub.fail_with = GeminiUnavailableError("not configured")
    return stub


@pytest.fixture
def coach(gemini) -> CoachAgent:
    return CoachAgent(gemini=gemini)


@pytest.fixture
def client(gemini, coach):
    app.dependency_overrides[dependencies.get_gemini] = lambda: gemini
    app.dependency_overrides[dependencies.get_coach] = lambda: coach
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
