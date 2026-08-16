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

        # The extraction path (F4). `extraction` is what the model "reads" from
        # a turn; `extractions` records the calls, so a test can assert that a
        # turn was never sent at all.
        self.extraction: dict = {}
        self.extractions: list[dict] = []
        self.extract_fail_with: Exception | None = None

    async def generate(self, message, system_prompt, history=None):
        self.calls.append(
            {"message": message, "system_prompt": system_prompt, "history": history}
        )
        if self.fail_with is not None:
            raise self.fail_with
        return self.reply

    async def extract(self, message, system_prompt, schema, history=None):
        self.extractions.append(
            {
                "message": message,
                "system_prompt": system_prompt,
                "schema": schema,
                "history": history,
            }
        )
        if self.extract_fail_with is not None:
            raise self.extract_fail_with
        return dict(self.extraction)


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
    # Dispose before dropping the reference. Clearing the cache alone abandons
    # the connection pool for the garbage collector, which showed up as a pile
    # of "unclosed database" ResourceWarnings, one per test. Harmless in
    # production, where the engine lives as long as the process, but warnings
    # that are always there are warnings nobody reads.
    if database.get_engine.cache_info().currsize:
        database.get_engine().dispose()

    get_settings.cache_clear()
    database.get_engine.cache_clear()
    database.get_session_factory.cache_clear()
    dependencies.get_gemini.cache_clear()
    dependencies.get_coach.cache_clear()
    dependencies.get_telegram.cache_clear()


class StubTelegram:
    """Stands in for the bot. Records what would have been delivered.

    F6 is the only part of the system that acts without being asked, so what
    matters in tests is not that a message was formatted but that it was sent,
    to whom, and exactly once.
    """

    def __init__(self, username: str = "RunCoachTestBot") -> None:
        self.username = username
        self.sent: list[tuple[int, str]] = []
        self.fail_with: Exception | None = None

    @property
    def enabled(self) -> bool:
        return True

    def deep_link(self, session_id: str) -> str | None:
        if not self.username:
            return None
        return f"https://t.me/{self.username}?start={session_id}"

    async def send(self, chat_id: int, text: str) -> None:
        if self.fail_with is not None:
            raise self.fail_with
        self.sent.append((chat_id, text))


@pytest.fixture
def telegram() -> StubTelegram:
    return StubTelegram()


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
