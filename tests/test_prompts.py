"""The coaching rules are the product, so they get their own tests.

These lock the training philosophy in place: if someone weakens a safety rule,
a test fails rather than the change passing unnoticed into a prompt.
"""

from __future__ import annotations

from datetime import date

import pytest

from src.coaching.prompts import (
    EXPERIENCE_GUIDANCE,
    GOAL_GUIDANCE,
    build_system_prompt,
    weeks_until,
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


def test_unknown_fields_are_named_without_ordering_a_question():
    """Regression: the coach asked for the same goal four times in a real session.

    The system instruction is constant for the whole session, so a profile line
    reading "ask for the goal" re-issued that order on every turn, including
    turns after the runner had already answered. Missing fields are now
    described, never commanded.
    """
    prompt = build_system_prompt({})

    assert "Objetivo: sin registrar" in prompt
    # The persona may still tell it how to ask; the profile must not tell it to.
    assert "sin definir, pregúntalo" not in prompt
    assert "sin saber, y lo necesitas" not in prompt


def test_the_profile_is_framed_as_superseded_by_the_conversation():
    prompt = build_system_prompt({})

    assert "la conversación manda" in prompt


def test_the_persona_forbids_repeating_an_answered_question():
    prompt = build_system_prompt({})

    assert "Nunca vuelvas a preguntar algo que la persona ya te dijo" in prompt


def test_the_persona_says_when_to_stop_asking_and_deliver():
    """Without this the model optimises for gathering and never commits."""
    prompt = build_system_prompt({})

    assert "deja de preguntar y da el plan" in prompt


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


def test_a_missing_field_is_stated_rather_than_omitted():
    """The model should see the gap, so it can ask once or assume out loud.

    Omitting the line entirely would leave the model unaware anything is
    missing; ordering it to ask produced the repeated-question loop instead.
    """
    prompt = build_system_prompt({})

    assert "Volumen actual: sin registrar" in prompt


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


# --- the race date, and how far away it is -----------------------------------

def test_a_race_date_is_rendered_with_the_weeks_left():
    """A bare ISO date tells the model nothing: it has no reliable sense of today."""
    prompt = build_system_prompt(
        {"goal": "10K", "race_date": "2026-10-01"}, today=date(2026, 8, 14)
    )

    assert "faltan 6 semanas" in prompt


def test_a_race_this_week_says_so():
    prompt = build_system_prompt({"race_date": "2026-08-16"}, today=date(2026, 8, 14))

    assert "esta misma semana" in prompt


def test_a_single_week_is_not_pluralised():
    prompt = build_system_prompt({"race_date": "2026-08-22"}, today=date(2026, 8, 14))

    assert "falta 1 semana" in prompt


def test_a_race_already_run_prompts_for_the_next_one():
    prompt = build_system_prompt({"race_date": "2026-07-01"}, today=date(2026, 8, 14))

    assert "ya pasó" in prompt


def test_without_a_date_of_reference_the_race_date_still_renders():
    """Callers that have no clock, such as tests and the voice path, still work."""
    prompt = build_system_prompt({"race_date": "2026-10-01"})

    assert "Fecha de la carrera: 2026-10-01" in prompt
    assert "semanas" not in prompt.split("PERFIL REGISTRADO")[1]


def test_a_malformed_stored_date_does_not_break_the_prompt():
    prompt = build_system_prompt({"race_date": "en octubre"}, today=date(2026, 8, 14))

    assert "Fecha de la carrera: en octubre" in prompt


def test_the_coach_is_told_its_own_plan_is_not_kilometres_run():
    """Otherwise it computes the next ten percent on its own last proposal."""
    prompt = build_system_prompt()

    assert "no es kilometraje que el corredor haya corrido" in prompt
    assert "Nunca calcules el diez por ciento sobre tu propia propuesta" in prompt


# --- weeks_until, shared by the prompt and the profile endpoint --------------

def test_weeks_until_counts_whole_weeks():
    assert weeks_until("2026-10-01", date(2026, 8, 14)) == 6


def test_weeks_until_is_negative_once_the_race_has_passed():
    assert weeks_until("2026-07-01", date(2026, 8, 14)) < 0


def test_weeks_until_is_zero_inside_the_race_week():
    assert weeks_until("2026-08-16", date(2026, 8, 14)) == 0


@pytest.mark.parametrize("stored", [None, "", "en octubre", "2026-13-45"])
def test_weeks_until_refuses_what_it_cannot_parse(stored):
    """The column is text, so it can hold anything, and neither caller may crash."""
    assert weeks_until(stored, date(2026, 8, 14)) is None


# --- the coach knows what it can do ------------------------------------------

def test_the_coach_knows_it_can_set_reminders():
    """Found in a real conversation. Asked to be reminded, the coach answered
    "no puedo hacer eso, mi trabajo es solo ayudarte con el entrenamiento" - and
    it was right to, because nothing in its briefing said otherwise. F6 was
    built, deployed and working, and completely invisible to the one part of the
    system a runner actually talks to."""
    prompt = build_system_prompt({})

    assert "recordarle" in prompt
    assert "Telegram" in prompt


def test_it_is_told_not_to_deny_the_capability():
    """A model asked for something outside its brief will decline politely, which
    is exactly the failure this prevents."""
    prompt = build_system_prompt({})

    assert "Nunca digas que no puedes recordar" in prompt
