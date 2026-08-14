"""Profile extraction as the application actually runs it.

Driven through the HTTP endpoint rather than by calling the agent, because that
is the lesson F3 left: the question loop was "fixed" and verified by passing
history by hand, while the route went on not passing it.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.config import get_settings
from src.services import db_service

FUTURE_RACE = (datetime.now(timezone.utc).date() + timedelta(days=60)).isoformat()


def profile_of(session_id: str) -> dict:
    return db_service.get_or_create_user(web_session_id=session_id)


def test_what_the_runner_says_becomes_their_profile(client, gemini):
    gemini.extraction = {
        "goal": "10K",
        "experience_level": None,
        "weekly_km": 15.0,
        "race_date": FUTURE_RACE,
    }

    client.post(
        "/api/chat",
        json={"message": "Corro 15 km por semana y quiero un 10K", "session_id": "s1"},
    )

    stored = profile_of("s1")
    assert stored["goal"] == "10K"
    assert stored["weekly_km"] == 15.0
    assert stored["race_date"] == FUTURE_RACE


def test_the_profile_reaches_the_next_turns_prompt(client, gemini):
    """The whole point: what was said once shapes every answer after it."""
    gemini.extraction = {"goal": "21K", "weekly_km": 20.0}
    client.post("/api/chat", json={"message": "quiero un 21K, corro 20", "session_id": "s1"})

    gemini.extraction = {}
    client.post("/api/chat", json={"message": "¿y qué desayuno?", "session_id": "s1"})

    prompt = gemini.calls[-1]["system_prompt"]
    assert "Objetivo: 21K" in prompt
    assert "20.0 km por semana" in prompt
    # The stored goal must also pull in its coaching guidance, not just print.
    assert "medio maratón" in prompt


def test_a_turn_with_nothing_to_read_spends_no_request(client, gemini):
    """The daily budget is 500 requests; most turns look like this one."""
    client.post("/api/chat", json={"message": "¿y cómo respiro?", "session_id": "s1"})

    assert gemini.extractions == []
    assert len(gemini.calls) == 1  # the reply, and nothing else


def test_a_turn_that_mentions_a_number_is_read(client, gemini):
    client.post("/api/chat", json={"message": "corro 15 km", "session_id": "s1"})

    assert len(gemini.extractions) == 1


def test_the_extractor_sees_enough_history_to_read_a_bare_number(client, gemini):
    """"quince" alone means nothing without the question it answers."""
    client.post("/api/chat", json={"message": "quiero un 10K", "session_id": "s1"})
    client.post("/api/chat", json={"message": "quince", "session_id": "s1"})

    assert gemini.extractions[-1]["history"] != []


def test_an_implausible_reading_is_not_stored(client, gemini):
    gemini.extraction = {"weekly_km": 400.0, "goal": "5K"}

    client.post("/api/chat", json={"message": "corro 400 km", "session_id": "s1"})

    stored = profile_of("s1")
    assert stored["weekly_km"] is None
    assert stored["goal"] == "5K"  # the believable field still lands


def test_a_race_date_in_the_past_is_not_stored(client, gemini):
    gemini.extraction = {"race_date": "2020-10-01"}

    client.post("/api/chat", json={"message": "la carrera es en octubre", "session_id": "s1"})

    assert profile_of("s1")["race_date"] is None


def test_silence_on_a_field_does_not_erase_it(client, gemini):
    """A later turn that mentions no goal must not wipe the goal already known."""
    gemini.extraction = {"goal": "Maratón", "weekly_km": 30.0}
    client.post("/api/chat", json={"message": "quiero un maratón, corro 30", "session_id": "s1"})

    gemini.extraction = {"goal": None, "weekly_km": 33.0, "race_date": None}
    client.post("/api/chat", json={"message": "ya voy por 33 km", "session_id": "s1"})

    stored = profile_of("s1")
    assert stored["goal"] == "Maratón"
    assert stored["weekly_km"] == 33.0


def test_a_failed_extraction_still_answers_the_runner(client, gemini):
    """The reply is what was asked for; the profile is a side effect."""
    gemini.extract_fail_with = RuntimeError("the extractor fell over")

    response = client.post(
        "/api/chat", json={"message": "corro 15 km", "session_id": "s1"}
    )

    assert response.status_code == 200
    assert response.json()["reply"] == gemini.reply
    assert response.json()["degraded"] is False
    assert profile_of("s1")["weekly_km"] is None


def test_extraction_can_be_turned_off_to_buy_back_the_budget(
    client, gemini, monkeypatch
):
    monkeypatch.setenv("PROFILE_EXTRACTION_ENABLED", "false")
    get_settings.cache_clear()

    client.post("/api/chat", json={"message": "corro 15 km", "session_id": "s1"})

    assert gemini.extractions == []
    assert len(gemini.calls) == 1


@pytest.mark.parametrize("channel", ["web", "telegram"])
def test_a_profile_is_read_from_any_channel(client, coach, gemini, channel):
    """One runner, one profile, whichever way they reached the coach."""
    gemini.extraction = {"weekly_km": 25.0}

    import asyncio

    asyncio.run(
        coach.converse("corro 25 km por semana", web_session_id="s1", channel=channel)
    )

    assert profile_of("s1")["weekly_km"] == 25.0
