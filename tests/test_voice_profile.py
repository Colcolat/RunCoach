"""What the spoken coach knows about the runner.

Found in use, on a phone, against the deployed site: a runner said their goal,
their level and their weekly volume out loud, and two turns later the coach
asked for them again while the profile panel still read "sin registrar".

Voice was write-only for the profile. Transcripts were stored and nothing was
ever read back from them, and the session was opened with
build_system_prompt({}), so neither what had just been said nor what F4 had
already learned by text ever reached the model.
"""

from __future__ import annotations

import pytest

from src.services import db_service


@pytest.fixture(autouse=True)
def schema():
    db_service.create_schema()


def test_a_spoken_session_is_briefed_with_what_is_already_known(coach):
    """The session used to open with an empty profile no matter what was known."""
    user = db_service.get_or_create_user(web_session_id="hablante")
    db_service.update_profile(user["id"], goal="21K", weekly_km=18.0)

    profile = coach.profile_for("hablante")

    assert profile["goal"] == "21K"
    assert profile["weekly_km"] == 18.0


def test_a_session_with_no_id_is_briefed_with_nothing_rather_than_failing(coach):
    assert coach.profile_for(None) == {}


def test_an_unreadable_profile_costs_memory_not_the_call(coach, monkeypatch):
    """Losing the briefing is bad; dropping the runner mid-conversation is worse."""
    monkeypatch.setattr(
        db_service, "get_or_create_user",
        lambda **kw: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    assert coach.profile_for("hablante") == {}


@pytest.mark.asyncio
async def test_speaking_a_goal_fills_the_profile(coach, gemini):
    """The half that was missing: voice now teaches the profile, as text does."""
    gemini.extraction = {"goal": "maratón", "weekly_km": 3.0,
                         "experience_level": "principiante"}

    updates = await coach.read_spoken_profile("hablante", "quiero un maratón, corro 3 km")

    # Speech goes through the same cleaning as text, so what lands in the column
    # is the canonical distance rather than however it happened to be said.
    assert updates["goal"] == "Maratón"
    stored = db_service.get_or_create_user(web_session_id="hablante")
    assert stored["goal"] == "Maratón"
    assert stored["weekly_km"] == 3.0
    assert stored["experience_level"] == "principiante"


@pytest.mark.asyncio
async def test_a_spoken_reminder_time_becomes_a_reminder_not_a_column(coach, gemini):
    gemini.extraction = {"reminder_time": "06:30"}

    await coach.read_spoken_profile("hablante", "recuérdame a las seis y media")

    user = db_service.get_or_create_user(web_session_id="hablante")
    assert db_service.daily_reminder_time(user["id"]) == "06:30"


@pytest.mark.asyncio
async def test_a_turn_that_says_nothing_about_the_runner_costs_no_request(coach, gemini):
    """Same quota rule as the text path: most turns carry no profile at all."""
    await coach.read_spoken_profile("hablante", "vale, gracias")

    assert gemini.extractions == []


@pytest.mark.asyncio
async def test_a_failed_reading_never_reaches_the_runner(coach, gemini):
    """This runs mid-call. An exception here would end a conversation."""
    gemini.extract_fail_with = RuntimeError("boom")

    assert await coach.read_spoken_profile("hablante", "corro 30 km por semana") == {}
