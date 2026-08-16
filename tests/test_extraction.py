"""The rules that decide when to spend a request and what is safe to store.

Pure domain, so none of this needs a key or a network. The two things worth
holding still are the gate, which protects a 500-request daily budget, and the
validator, which protects a profile that outlives the conversation it came from.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from src.coaching.extraction import (
    EXTRACTION_SCHEMA,
    GOALS,
    LEVELS,
    build_extraction_prompt,
    clean,
    mentions_profile_information,
)
from src.coaching.prompts import EXPERIENCE_GUIDANCE, GOAL_GUIDANCE

TODAY = date(2026, 8, 14)


# --- vocabulary --------------------------------------------------------------

def test_every_goal_the_model_may_return_selects_guidance():
    """Otherwise a stored goal quietly selects nothing and the coach turns generic."""
    assert set(GOALS) == set(GOAL_GUIDANCE)


def test_every_level_the_model_may_return_selects_guidance():
    assert set(LEVELS) == set(EXPERIENCE_GUIDANCE)


def test_the_schema_offers_the_model_exactly_that_vocabulary():
    properties = EXTRACTION_SCHEMA["properties"]
    assert properties["goal"]["enum"] == list(GOALS)
    assert properties["experience_level"]["enum"] == list(LEVELS)


# --- the gate ----------------------------------------------------------------

@pytest.mark.parametrize(
    "message",
    [
        "Corro 15 km por semana",
        "quiero preparar un maratón",
        "soy principiante",
        "la carrera es en diciembre",
        "corro quince kilómetros a la semana",  # a voice transcript spells numbers
        "hago veintidós por semana",
        "nunca he corrido",
    ],
)
def test_turns_that_carry_profile_data_are_sent(message):
    assert mentions_profile_information(message) is True


@pytest.mark.parametrize(
    "message",
    [
        "gracias entrenador",
        "¿y cómo debo respirar en las cuestas?",
        "me duele la rodilla desde ayer",
        "vale, lo intento",
        "¿qué desayuno antes de salir?",
        "",
    ],
)
def test_turns_that_carry_nothing_are_not_sent(message):
    """Most turns look like these. Each one skipped is a request left unspent."""
    assert mentions_profile_information(message) is False


def test_the_gate_reads_accents_and_case_the_same():
    assert mentions_profile_information("SOY PRINCIPIANTE") is True
    assert mentions_profile_information("un maratón") is True
    assert mentions_profile_information("un maraton") is True


def test_an_article_is_not_a_quantity():
    """"un" and "una" are far more often articles, and would trigger on anything."""
    assert mentions_profile_information("dame una respuesta corta") is False


# --- validation --------------------------------------------------------------

def test_a_clean_reading_passes_through():
    result = clean(
        {
            "goal": "10K",
            "experience_level": "principiante",
            "weekly_km": 15.0,
            "race_date": "2026-10-01",
        },
        TODAY,
    )
    assert result == {
        "goal": "10K",
        "experience_level": "principiante",
        "weekly_km": 15.0,
        "race_date": "2026-10-01",
    }


def test_fields_the_runner_did_not_mention_are_dropped_not_nulled():
    """A null must never erase what the runner said in an earlier session."""
    result = clean({"goal": "5K", "weekly_km": None, "race_date": None}, TODAY)

    assert result == {"goal": "5K"}
    assert "weekly_km" not in result


def test_a_goal_outside_the_vocabulary_is_refused():
    assert clean({"goal": "ultramaratón"}, TODAY) == {}


def test_an_unaccented_marathon_still_matches_the_guidance_key():
    assert clean({"goal": "maraton"}, TODAY) == {"goal": "Maratón"}


def test_a_half_marathon_by_name_becomes_the_distance():
    assert clean({"goal": "medio maraton"}, TODAY) == {"goal": "21K"}


@pytest.mark.parametrize("km", [0, -5, 400, "veinte", None, True])
def test_an_implausible_weekly_volume_is_refused(km):
    """A world-class marathoner runs about 250 a week; 400 is a misreading."""
    assert clean({"weekly_km": km}, TODAY) == {}


def test_a_race_date_already_past_is_refused():
    """The failure this guards: the model dates "octubre" in its cutoff year."""
    assert clean({"race_date": "2025-10-01"}, TODAY) == {}


def test_a_race_date_absurdly_far_ahead_is_refused():
    assert clean({"race_date": "2099-01-01"}, TODAY) == {}


def test_a_race_today_survives_a_clock_in_another_zone():
    assert clean({"race_date": TODAY.isoformat()}, TODAY) == {
        "race_date": TODAY.isoformat()
    }
    yesterday = (TODAY - timedelta(days=1)).isoformat()
    assert clean({"race_date": yesterday}, TODAY) == {"race_date": yesterday}


def test_a_date_that_is_not_a_date_is_refused():
    assert clean({"race_date": "en octubre"}, TODAY) == {}


def test_junk_in_place_of_an_object_yields_nothing():
    assert clean(None, TODAY) == {}
    assert clean(["10K"], TODAY) == {}


def test_unknown_keys_never_reach_the_caller():
    """update_profile filters too, but the domain should not hand it junk."""
    assert clean({"goal": "5K", "shoe_size": 42}, TODAY) == {"goal": "5K"}


# --- the prompt --------------------------------------------------------------

def test_the_prompt_states_todays_date():
    """Without it the model dates "octubre" in the past and every deadline breaks."""
    assert "2026-08-14" in build_extraction_prompt(TODAY)


def test_the_prompt_separates_weekly_volume_from_one_session():
    prompt = build_extraction_prompt(TODAY).lower()
    assert "semana completa" in prompt
    assert "una sola sesión" in prompt


def test_the_prompt_reads_the_last_message_not_the_whole_history():
    assert "ÚLTIMO mensaje" in build_extraction_prompt(TODAY)


# --- relative times ----------------------------------------------------------

def test_the_extractor_is_given_a_clock_when_there_is_one():
    """"recuérdame en dos minutos" is unanswerable without one: the model cannot
    turn a relative time into the wall-clock time the reminders table stores, so
    it returns nothing and the request silently does nothing."""
    prompt = build_extraction_prompt(date(2026, 8, 16), now_local="15:33")

    assert "15:33" in prompt
    assert "relativas" in prompt


def test_without_a_clock_it_says_nothing_about_the_time():
    """The date alone is still valid for everything except reminders."""
    prompt = build_extraction_prompt(date(2026, 8, 16))

    assert "Ahora son las" not in prompt
    assert "2026-08-16" in prompt
