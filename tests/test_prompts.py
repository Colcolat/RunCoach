"""The coaching rules are the product, so they get their own tests.

These lock the training philosophy in place: if someone weakens a safety rule,
a test fails rather than the change passing unnoticed into a prompt.
"""

from __future__ import annotations

import pytest

from src.coaching.prompts import (
    EXPERIENCE_GUIDANCE,
    GOAL_GUIDANCE,
    build_system_prompt,
    welcome_message,
)


# --- safety rules, present regardless of profile -----------------------------


@pytest.mark.parametrize(
    "rule",
    [
        "diez por ciento",  # weekly volume cap
        "día de descanso",  # mandatory rest
        "valoración médica",  # acute pain referral
        "ochenta por ciento",  # 80/20 easy-hard distribution
    ],
)
def test_safety_rules_survive_an_empty_profile(rule):
    assert rule in build_system_prompt({})


@pytest.mark.parametrize(
    "profile",
    [
        {},
        {"goal": "5K"},
        {"experience_level": "avanzado"},
        {"goal": "Maratón", "experience_level": "avanzado", "weekly_km": 90},
    ],
)
def test_volume_cap_is_present_for_every_profile_shape(profile):
    """An advanced runner does not get a relaxed version of the 10% rule."""
    assert "diez por ciento" in build_system_prompt(profile)


def test_prompt_forbids_diagnosing_injuries():
    prompt = build_system_prompt({})
    assert "No diagnostiques lesiones" in prompt


def test_prompt_forbids_prescribing_medication():
    assert "Nunca prescribas medicamentos" in build_system_prompt({})


def test_cardiac_symptoms_route_to_a_doctor():
    prompt = build_system_prompt({})
    assert "dolor en el pecho" in prompt
    assert "consulte a un médico" in prompt


# --- voice readiness ---------------------------------------------------------


def test_persona_forbids_markdown_because_the_prompt_also_feeds_voice():
    """Bullet lists and asterisks read badly when spoken aloud."""
    prompt = build_system_prompt({})
    assert "listas con viñetas" in prompt
    assert "se leen en voz alta" in prompt


def test_persona_demands_concrete_numbers():
    assert "números concretos" in build_system_prompt({})


# --- profile handling --------------------------------------------------------


def test_unknown_fields_become_explicit_instructions_to_ask():
    prompt = build_system_prompt({})
    assert "Objetivo: aún sin definir, pregúntalo" in prompt
    assert "Nivel: aún sin definir, pregúntalo" in prompt


def test_known_profile_details_reach_the_prompt():
    prompt = build_system_prompt(
        {
            "username": "Juan",
            "goal": "21K",
            "experience_level": "intermedio",
            "weekly_km": 42,
            "race_date": "2026-12-06",
        }
    )
    assert "Juan" in prompt
    assert "42 km por semana" in prompt
    assert "2026-12-06" in prompt


def test_missing_mileage_explains_why_it_matters():
    """The model should know the 10% rule is uncomputable without a baseline."""
    assert "necesitas para calcular el diez por ciento" in build_system_prompt({})


# --- goal and level guidance -------------------------------------------------


def test_the_four_challenge_distances_are_all_covered():
    assert {"5K", "10K", "21K", "Maratón"} == set(GOAL_GUIDANCE)


@pytest.mark.parametrize("goal", sorted(GOAL_GUIDANCE))
def test_every_goal_injects_its_own_guidance(goal):
    assert GOAL_GUIDANCE[goal] in build_system_prompt({"goal": goal})


def test_goal_guidance_is_exclusive():
    """A 5K runner must not receive marathon guidance."""
    prompt = build_system_prompt({"goal": "5K"})
    assert GOAL_GUIDANCE["Maratón"] not in prompt


def test_beginner_guidance_defers_speed_work():
    prompt = build_system_prompt({"experience_level": "principiante"})
    assert "Nada de trabajo de velocidad todavía" in prompt


def test_advanced_guidance_does_not_leak_into_a_beginner_prompt():
    prompt = build_system_prompt({"experience_level": "principiante"})
    assert EXPERIENCE_GUIDANCE["avanzado"] not in prompt


def test_experience_level_lookup_is_case_insensitive():
    guidance = EXPERIENCE_GUIDANCE["intermedio"]
    assert guidance in build_system_prompt({"experience_level": "Intermedio"})
    assert guidance in build_system_prompt({"experience_level": "intermedio"})


def test_an_unsupported_goal_does_not_break_the_prompt():
    prompt = build_system_prompt({"goal": "ultramaratón"})
    assert "ultramaratón" in prompt
    assert "GUÍA PARA ESTE OBJETIVO" not in prompt


def test_build_system_prompt_accepts_none():
    assert build_system_prompt() == build_system_prompt({})


# --- welcome -----------------------------------------------------------------


def test_welcome_asks_for_the_two_facts_the_coach_needs():
    message = welcome_message()
    assert "5K" in message and "maratón" in message
    assert "por semana" in message


def test_welcome_uses_the_name_when_known():
    assert "Juan" in welcome_message("Juan")
