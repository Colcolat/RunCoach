from __future__ import annotations

import pytest

from src.agents.coach_agent import (
    DEGRADED_MESSAGE,
    RATE_LIMITED_MESSAGE,
    CoachAgent,
    CoachReply,
)
from src.services.gemini_service import GeminiRateLimitedError
from src.coaching.prompts import GOAL_GUIDANCE
from src.services.gemini_service import GeminiUnavailableError


@pytest.mark.asyncio
async def test_the_model_reply_is_returned_untouched(coach, gemini):
    reply = await coach.handle_message("¿Cuánto corro mañana?")

    assert isinstance(reply, CoachReply)
    assert reply.text == gemini.reply
    assert reply.degraded is False


@pytest.mark.asyncio
async def test_the_persona_travels_as_system_prompt_not_as_a_user_turn(coach, gemini):
    """A long conversation must not be able to push the rules out of context."""
    await coach.handle_message("hola")

    call = gemini.calls[0]
    assert call["message"] == "hola"
    assert "diez por ciento" in call["system_prompt"]


@pytest.mark.asyncio
async def test_the_profile_shapes_the_prompt(coach, gemini):
    await coach.handle_message("¿qué hago?", profile={"goal": "21K"})

    assert GOAL_GUIDANCE["21K"] in gemini.calls[0]["system_prompt"]


@pytest.mark.asyncio
async def test_history_is_forwarded_to_the_model(coach, gemini):
    history = [
        {"role": "user", "content": "corro 20 km"},
        {"role": "assistant", "content": "buen punto de partida"},
    ]

    await coach.handle_message("¿y ahora?", history=history)

    assert gemini.calls[0]["history"] == history


@pytest.mark.asyncio
async def test_whitespace_is_stripped_before_reaching_the_model(coach, gemini):
    await coach.handle_message("  ¿cuánto corro?  ")

    assert gemini.calls[0]["message"] == "¿cuánto corro?"


@pytest.mark.parametrize("empty", ["", "   ", "\n\t"])
@pytest.mark.asyncio
async def test_an_empty_message_is_rejected(coach, empty):
    with pytest.raises(ValueError):
        await coach.handle_message(empty)


@pytest.mark.asyncio
async def test_a_model_failure_degrades_instead_of_raising(unavailable_gemini):
    agent = CoachAgent(gemini=unavailable_gemini)

    reply = await agent.handle_message("hola")

    assert reply.degraded is True
    assert reply.text == DEGRADED_MESSAGE


@pytest.mark.asyncio
async def test_degradation_covers_a_mid_conversation_failure(coach, gemini):
    """A runner already talking should get an answer, not a stack trace."""
    gemini.fail_with = GeminiUnavailableError("429 quota exceeded")

    reply = await coach.handle_message("¿cuánto corro?")

    assert reply.degraded is True


def test_welcome_costs_no_request(coach, gemini):
    reply = coach.welcome("Juan")

    assert "Juan" in reply.text
    assert gemini.calls == []
    assert reply.degraded is False


@pytest.mark.asyncio
async def test_a_rate_limit_says_so_instead_of_sounding_broken(coach, gemini):
    """A limit that clears in under a minute is not the coach being unreachable.

    Hit in real use while testing F4: the free tier allows 15 requests a minute
    and a conversation went over, and the runner was told the coach could not be
    consulted, which reads like the app is down.
    """
    gemini.fail_with = GeminiRateLimitedError("429")

    reply = await coach.handle_message("corro 20 km")

    assert reply.degraded is True
    assert reply.text == RATE_LIMITED_MESSAGE
    assert "segundos" in reply.text
    assert "no he perdido el hilo" in reply.text


@pytest.mark.asyncio
async def test_a_rate_limited_extraction_does_not_cost_the_reply(client, gemini):
    """The two run on separate models, but if the extractor is refused anyway."""
    gemini.extract_fail_with = GeminiRateLimitedError("429")

    response = client.post("/api/chat", json={"message": "corro 15 km", "session_id": "s1"})

    assert response.status_code == 200
    assert response.json()["reply"] == gemini.reply
    assert response.json()["degraded"] is False
