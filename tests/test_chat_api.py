from __future__ import annotations

import pytest

from src.services.gemini_service import GeminiUnavailableError


def test_chat_returns_the_coach_reply(client, gemini):
    response = client.post("/api/chat", json={"message": "¿Cómo entreno para un 5K?"})

    assert response.status_code == 200
    body = response.json()
    assert body["reply"] == gemini.reply
    assert body["degraded"] is False


def test_chat_degrades_with_200_rather_than_failing(client, gemini):
    """A quota error is not the runner's problem; they still get an answer."""
    gemini.fail_with = GeminiUnavailableError("429")

    response = client.post("/api/chat", json={"message": "hola"})

    assert response.status_code == 200
    assert response.json()["degraded"] is True


@pytest.mark.parametrize(
    "payload",
    [
        {"message": ""},
        {},
        {"message": "x" * 4001},
    ],
)
def test_invalid_payloads_are_rejected_by_validation(client, payload):
    assert client.post("/api/chat", json=payload).status_code == 422


def test_welcome_greets_without_calling_the_model(client, gemini):
    response = client.get("/api/welcome", params={"username": "Juan"})

    assert response.status_code == 200
    assert "Juan" in response.json()["greeting"]
    assert gemini.calls == []


def test_health_reports_the_model_state(client):
    components = client.get("/health").json()["components"]

    assert components["gemini"] == "configured"
    assert components["gemini_model"] == "stub-model"


def test_health_reports_an_unconfigured_model(client, gemini):
    gemini.enabled = False

    assert client.get("/health").json()["components"]["gemini"] == "not_configured"
