"""Tests for the model client that need no network.

The conversion from our message shape to the API's is real logic: the API calls
the assistant "model", and a silent mismatch there would make the model read its
own past replies as if the runner had said them.
"""

from __future__ import annotations

import pytest

from src.config import Settings
from src.services.gemini_service import GeminiService, GeminiUnavailableError


def _service(**overrides) -> GeminiService:
    settings = Settings(google_api_key="", **overrides)
    return GeminiService(settings=settings)


def test_service_is_disabled_without_a_key():
    assert _service().enabled is False


def test_a_blank_key_does_not_count_as_configured():
    assert GeminiService(settings=Settings(google_api_key="   ")).enabled is False


def test_the_client_is_not_built_without_a_key():
    with pytest.raises(GeminiUnavailableError):
        _service()._get_client()


@pytest.mark.asyncio
async def test_generate_reports_the_missing_key_rather_than_crashing():
    with pytest.raises(GeminiUnavailableError):
        await _service().generate(message="hola", system_prompt="eres un coach")


def test_the_assistant_role_is_mapped_to_the_api_name():
    contents = GeminiService._to_contents(
        "¿y ahora?",
        [
            {"role": "user", "content": "corro 20 km"},
            {"role": "assistant", "content": "buen punto de partida"},
        ],
    )

    assert [c.role for c in contents] == ["user", "model", "user"]
    assert contents[-1].parts[0].text == "¿y ahora?"


def test_the_current_message_goes_last():
    contents = GeminiService._to_contents("actual", [{"role": "user", "content": "previo"}])

    assert contents[-1].parts[0].text == "actual"


def test_history_is_optional():
    contents = GeminiService._to_contents("solo esto", None)

    assert len(contents) == 1
    assert contents[0].role == "user"


@pytest.mark.parametrize(
    "turn",
    [
        {"role": "system", "content": "texto"},  # unmapped role
        {"role": "user", "content": ""},  # empty body
        {"content": "sin rol"},
        {"role": "user"},
    ],
)
def test_malformed_history_turns_are_dropped_not_forwarded(turn):
    contents = GeminiService._to_contents("actual", [turn])

    assert len(contents) == 1


def test_the_model_id_comes_from_configuration():
    """A retired model must be fixable with an env change, not a deploy."""
    service = GeminiService(settings=Settings(google_api_key="k", gemini_model="otro-modelo"))

    assert service.model == "otro-modelo"


# --- structured extraction ---------------------------------------------------

class _FakeResponse:
    def __init__(self, text):
        self.text = text
        self.candidates = []


class _FakeModels:
    def __init__(self, text):
        self._text = text
        self.config = None

    async def generate_content(self, model, contents, config):
        self.config = config
        return _FakeResponse(self._text)


class _FakeClient:
    """Enough of the SDK surface to exercise the parsing, with no network."""

    def __init__(self, text):
        self.aio = type("aio", (), {"models": _FakeModels(text)})()


def _extracting_service(text) -> GeminiService:
    service = GeminiService(settings=Settings(google_api_key="k"))
    service._client = _FakeClient(text)
    return service


@pytest.mark.asyncio
async def test_extract_reports_the_missing_key_rather_than_crashing():
    with pytest.raises(GeminiUnavailableError):
        await _service().extract(message="corro 15 km", system_prompt="lee", schema={})


@pytest.mark.asyncio
async def test_extract_returns_the_parsed_object():
    service = _extracting_service('{"goal": "10K", "weekly_km": 15}')

    assert await service.extract("corro 15", "lee", {}) == {"goal": "10K", "weekly_km": 15}


@pytest.mark.asyncio
async def test_a_truncated_body_is_a_failure_not_a_silent_empty_profile():
    """JSON mode makes this unlikely, not impossible: the token budget can cut it."""
    service = _extracting_service('{"goal": "10K", "weekly')

    with pytest.raises(GeminiUnavailableError):
        await service.extract("corro 15", "lee", {})


@pytest.mark.asyncio
async def test_a_json_value_that_is_not_an_object_is_refused():
    service = _extracting_service("[1, 2, 3]")

    with pytest.raises(GeminiUnavailableError):
        await service.extract("corro 15", "lee", {})


@pytest.mark.asyncio
async def test_extraction_asks_for_json_at_zero_temperature():
    """Inheriting the coaching temperature would make reading a number flaky."""
    service = _extracting_service("{}")
    schema = {"type": "OBJECT"}

    await service.extract("corro 15", "lee", schema)

    config = service._client.aio.models.config
    assert config.temperature == 0.0
    assert config.response_mime_type == "application/json"
    assert config.response_schema == schema
