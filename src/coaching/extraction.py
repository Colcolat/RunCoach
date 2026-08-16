"""Reading a runner's profile out of what they say.

Domain logic, like src.coaching.prompts: no SDK imports and no I/O, so the rules
that decide when to spend a request and what counts as a believable value can be
tested without a network or a key.

Three pieces live here:

  `mentions_profile_information` decides whether a turn is worth a second model
  call at all. The free tier allows 500 text requests a day; extracting on every
  turn would halve that to 250 conversations, and most turns carry nothing to
  extract ("gracias", "y como respiro en las cuestas").

  `EXTRACTION_SCHEMA` and `build_extraction_prompt` define what the model is
  asked for.

  `clean` decides what is believable enough to write to the database. The model
  is asked for JSON and returns JSON, but "it parsed" is not the same as "it is
  true", and this profile is durable: a wrong value follows the runner into
  every future session.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date, timedelta

# The vocabulary is not free-form: these values are looked up as keys in
# GOAL_GUIDANCE and EXPERIENCE_GUIDANCE. A value outside these lists would be
# stored happily and then silently fail to select any guidance, which is the
# kind of bug that looks like "the coach is a bit generic" rather than an error.
# tests/test_extraction.py asserts the two stay in step.
GOALS = ("5K", "10K", "21K", "Maratón")
LEVELS = ("principiante", "intermedio", "avanzado")

# A world-class marathoner runs about 250 km a week. Anything above this is a
# transcription artefact or a misread, not a runner.
MAX_WEEKLY_KM = 300.0

# How far ahead a race may plausibly be booked.
MAX_RACE_YEARS = 5

# How many past turns the extractor sees. Enough to read "quince" as an answer
# to "cuantos kilometros corres", short enough to keep the call cheap.
HISTORY_TURNS = 6


EXTRACTION_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "goal": {"type": "STRING", "enum": list(GOALS), "nullable": True},
        "experience_level": {"type": "STRING", "enum": list(LEVELS), "nullable": True},
        "weekly_km": {"type": "NUMBER", "nullable": True},
        "race_date": {"type": "STRING", "nullable": True},
        # F6. Not a profile column: it becomes a row in `reminders`, so the
        # agent takes it out of the result before writing the rest.
        "reminder_time": {"type": "STRING", "nullable": True},
    },
    "required": [
        "goal",
        "experience_level",
        "weekly_km",
        "race_date",
        "reminder_time",
    ],
}

# The key `clean` may return that does not belong on the user row.
REMINDER_FIELD = "reminder_time"


def _strip_accents(text: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn"
    )


# --- the gate ----------------------------------------------------------------

# Spelled-out numbers matter as much as digits: voice turns arrive as
# transcripts, and the Live API writes "quince kilometros", not "15 km".
# "un" and "una" are left out on purpose; they are articles far more often than
# they are quantities, and "un kilometro" is caught by the distance words below.
_NUMBER_WORDS = {
    "dos", "tres", "cuatro", "cinco", "seis", "siete", "ocho", "nueve", "diez",
    "once", "doce", "trece", "catorce", "quince", "dieciseis", "diecisiete",
    "dieciocho", "diecinueve", "veinte", "treinta", "cuarenta", "cincuenta",
    "sesenta", "setenta", "ochenta", "noventa", "cien", "ciento", "mil",
}

_DISTANCE_WORDS = {
    "km", "kms", "kilometro", "kilometros", "maraton", "maratones", "milla",
    "millas",
}

_LEVEL_WORDS = {
    "principiante", "principiantes", "novato", "intermedio", "avanzado",
    "experiencia", "empiezo", "empezando", "empece", "empezar", "nunca",
    "sedentario",
}

_MONTHS = {
    "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
    "septiembre", "setiembre", "octubre", "noviembre", "diciembre",
}

_TRIGGER_WORDS = _NUMBER_WORDS | _DISTANCE_WORDS | _LEVEL_WORDS | _MONTHS

_WORD = re.compile(r"[a-z]+")
_DIGIT = re.compile(r"\d")


def mentions_profile_information(message: str) -> bool:
    """Whether this turn is worth spending an extraction request on.

    Deliberately generous: a false positive costs one request out of 500, while
    a false negative loses a profile field until the runner happens to repeat
    it. What it filters out is the large class of turns that plainly carry no
    profile data at all, which in a real conversation is most of them.
    """
    text = _strip_accents((message or "").lower())
    if _DIGIT.search(text):
        return True
    words = set(_WORD.findall(text))
    if words & _TRIGGER_WORDS:
        return True
    # "veintidos", "veintitres" and friends, without listing all nine.
    return any(word.startswith("veinti") for word in words)


# --- the prompt --------------------------------------------------------------

def build_extraction_prompt(today: date, now_local: str | None = None) -> str:
    """The instruction for the extraction call.

    Today's date is injected rather than left to the model. Asked for "octubre"
    without it, the model dates the race in its training-cutoff year, which is
    in the past, and a race date in the past quietly disables every deadline the
    coach would otherwise reason about. `clean` rejects past dates as well; this
    is the fix and that is the guard.
    """
    # The clock, not just the calendar. Without it "recuérdame en dos minutos"
    # is unanswerable: the model has no way to turn a relative time into the
    # wall-clock time the reminders table stores, so it returns nothing and the
    # request silently does nothing. Local time, not UTC, because that is the
    # zone the reminder will be read back in.
    clock = (
        f"\nAhora son las {now_local} (hora local del corredor)."
        if now_local
        else ""
    )

    return f"""Extraes datos del perfil de un corredor a partir de lo que dice en una conversación.

Hoy es {today.isoformat()}.{clock}

Devuelve únicamente lo que la persona dijo de forma explícita. Si un dato hay que deducirlo o \
suponerlo, devuelve null. Devolver null es mucho mejor que arriesgar un valor equivocado: este \
perfil se guarda y acompaña al corredor en todas sus conversaciones siguientes.

El dato tiene que venir del ÚLTIMO mensaje del corredor. Los turnos anteriores están ahí solo para \
entenderlo, por ejemplo para saber a qué pregunta responde un "quince" suelto. No extraigas de \
nuevo algo que se dijo en un turno anterior.

weekly_km es el volumen que corre en una SEMANA COMPLETA. La distancia de una sola sesión o la \
distancia más larga que aguanta de un tirón NO son el volumen semanal: si dice que el domingo \
corrió doce kilómetros, o que aguanta cinco seguidos, weekly_km es null.

race_date en formato AAAA-MM-DD. Si solo dan el mes, usa el día uno de ese mes, y el próximo que \
ocurra contando desde hoy. Si no hay fecha, null.

goal solo si nombran la distancia objetivo. Un medio maratón son 21K.

experience_level solo si describen su experiencia corriendo.

reminder_time en formato HH:MM de 24 horas, y SOLO si piden explícitamente que se les avise o \
recuerde a una hora. "Suelo correr a las siete" es cuándo entrena, no una petición de aviso, y ahí \
va null. "Recuérdame a las siete" o "avísame por la mañana a las 6:30" sí lo son. Si dicen una hora \
sin precisar mañana o tarde, interpreta la más razonable para salir a correr."""


# --- validation --------------------------------------------------------------

_GOAL_ALIASES = {_strip_accents(goal).lower(): goal for goal in GOALS}
_GOAL_ALIASES.update(
    {
        "media maraton": "21K",
        "medio maraton": "21K",
        "half": "21K",
        "42k": "Maratón",
        "21.1k": "21K",
    }
)


def _clean_goal(value) -> str | None:
    if not isinstance(value, str):
        return None
    return _GOAL_ALIASES.get(_strip_accents(value).strip().lower())


def _clean_level(value) -> str | None:
    if not isinstance(value, str):
        return None
    level = _strip_accents(value).strip().lower()
    return level if level in LEVELS else None


def _clean_weekly_km(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    km = float(value)
    if not 0 < km <= MAX_WEEKLY_KM:
        return None
    return round(km, 1)


def _clean_race_date(value, today: date) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = date.fromisoformat(value.strip())
    except ValueError:
        return None
    # A day of slack so a race happening today survives a clock in another zone.
    if parsed < today - timedelta(days=1):
        return None
    if parsed > today.replace(year=today.year + MAX_RACE_YEARS):
        return None
    return parsed.isoformat()


def _clean_reminder_time(value) -> str | None:
    """Normalise to "HH:MM", refusing anything that is not a real clock time.

    Stored as text and later read back by the sweep, so a value like "25:00"
    would sit in the column silently doing nothing. Better to drop it and let
    the runner ask again.
    """
    if not isinstance(value, str):
        return None
    try:
        hour, minute = value.strip().split(":")
        hour, minute = int(hour), int(minute)
    except (ValueError, AttributeError):
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return f"{hour:02d}:{minute:02d}"


def clean(raw: dict | None, today: date) -> dict:
    """Keep the fields that survive validation; drop the rest silently.

    Returns only what should be written, so an empty result means "write
    nothing" rather than "write nulls over what we already knew". A field the
    runner did not mention this turn must never erase what they said last week.
    """
    if not isinstance(raw, dict):
        return {}

    candidates = {
        "goal": _clean_goal(raw.get("goal")),
        "experience_level": _clean_level(raw.get("experience_level")),
        "weekly_km": _clean_weekly_km(raw.get("weekly_km")),
        "race_date": _clean_race_date(raw.get("race_date"), today),
        REMINDER_FIELD: _clean_reminder_time(raw.get(REMINDER_FIELD)),
    }
    return {key: value for key, value in candidates.items() if value is not None}
