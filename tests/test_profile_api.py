"""The profile endpoint the panel reads.

Read-only by design: the runner never types their profile in, so there is no
PUT here and no form behind it. The conversation is the only way in.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.services import db_service

TODAY = datetime.now(timezone.utc).date()


def test_a_first_visit_returns_an_empty_profile_not_an_error(client):
    """A new runner has said nothing yet, and the panel must render that."""
    body = client.get("/api/profile/nueva").json()

    assert body["session_id"] == "nueva"
    assert body["goal"] is None
    assert body["weekly_km"] is None
    assert body["weeks_to_race"] is None


def test_what_the_coach_learned_is_readable_by_the_panel(client, gemini):
    gemini.extraction = {"goal": "10K", "experience_level": "intermedio", "weekly_km": 15.0}
    client.post("/api/chat", json={"message": "corro 15 km, quiero un 10K", "session_id": "s1"})

    body = client.get("/api/profile/s1").json()

    assert body["goal"] == "10K"
    assert body["experience_level"] == "intermedio"
    assert body["weekly_km"] == 15.0


def test_the_weeks_to_the_race_are_computed_for_the_browser(client, gemini):
    """The panel must not reimplement this: the coach reasons with the same number."""
    race = (TODAY + timedelta(weeks=6, days=2)).isoformat()
    gemini.extraction = {"race_date": race}
    client.post("/api/chat", json={"message": "la carrera es en octubre", "session_id": "s1"})

    body = client.get("/api/profile/s1").json()

    assert body["race_date"] == race
    assert body["weeks_to_race"] == 6


def test_reading_a_profile_does_not_invent_a_conversation(client):
    """Opening the panel must not leave a stray conversation behind."""
    client.get("/api/profile/curiosa")

    assert client.get("/api/history/curiosa").json()["messages"] == []


def test_profiles_do_not_leak_between_sessions(client, gemini):
    gemini.extraction = {"goal": "Maratón"}
    client.post("/api/chat", json={"message": "quiero un maraton", "session_id": "sA"})

    assert client.get("/api/profile/sB").json()["goal"] is None
